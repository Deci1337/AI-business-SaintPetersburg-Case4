import logging
import os
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from src.rag.llm import ask_full
from src.rag.retriever import search
from src.rag.db import get_connection

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("baltbereg.api")
PUBLIC_URL = os.getenv("API_PUBLIC_URL", "http://localhost:8001")

app = FastAPI(title="BaltBereg Service Desk API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled %s on %s: %s\n%s", type(exc).__name__, request.url.path, exc, traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

analyses: OrderedDict = OrderedDict()
MAX_ANALYSES = 1000


def save_analysis(data: dict) -> str:
    aid = str(uuid.uuid4())
    if len(analyses) >= MAX_ANALYSES:
        analyses.popitem(last=False)
    analyses[aid] = data
    return aid


class Query(BaseModel):
    question: str
    source: str = "api"


@app.post("/ask")
def ask_endpoint(q: Query):
    t0 = time.time()
    chunks = search(q.question, n_results=6)
    search_ms = round((time.time() - t0) * 1000)

    t1 = time.time()
    result = ask_full(q.question)
    llm_ms = round((time.time() - t1) * 1000)
    total_ms = round((time.time() - t0) * 1000)

    data = {
        "question": q.question,
        "answer": result["answer"],
        "escalated": result["escalated"],
        "classification": result["classification"],
        "top_source": result["top_source"],
        "source": q.source,
        "created_at": datetime.now().isoformat(),
        "timing": {
            "search_ms": search_ms,
            "llm_ms": llm_ms,
            "total_ms": total_ms,
        },
        "chunks": [
            {
                "rank": i + 1,
                "score": round(c["score"], 3),
                "source": c["meta"].get("source", ""),
                "title": c["meta"].get("title", "")[:80],
                "service": c["meta"].get("service", ""),
                "text": c["text"][:600],
            }
            for i, c in enumerate(chunks)
        ],
    }

    aid = save_analysis(data)
    data["analysis_id"] = aid
    data["analysis_url"] = f"{PUBLIC_URL}/analysis/{aid}"
    return data


@app.get("/analyses")
def list_analyses():
    return [
        {
            "id": aid,
            "question": d["question"][:80],
            "escalated": d["escalated"],
            "total_ms": d["timing"]["total_ms"],
            "created_at": d["created_at"],
            "source": d.get("source", "api"),
            "classification": d.get("classification", {}),
        }
        for aid, d in reversed(list(analyses.items()))
    ]


@app.get("/analyses/{aid}")
def get_analysis(aid: str):
    if aid not in analyses:
        raise HTTPException(404, "Анализ не найден")
    return analyses[aid]


@app.get("/stats")
def get_stats():
    total = len(analyses)
    if total == 0:
        return {"total": 0}

    escalated = sum(1 for d in analyses.values() if d["escalated"])
    timings = [d["timing"]["total_ms"] for d in analyses.values()]
    sources = {}
    priorities = {}
    for d in analyses.values():
        cl = d.get("classification", {})
        svc = cl.get("service", "Другое")
        pri = cl.get("priority", "Средний")
        sources[svc] = sources.get(svc, 0) + 1
        priorities[pri] = priorities.get(pri, 0) + 1

    return {
        "total": total,
        "escalated": escalated,
        "resolved": total - escalated,
        "escalation_rate": round(escalated / total * 100, 1),
        "automation_rate": round((total - escalated) / total * 100, 1),
        "avg_ms": round(sum(timings) / len(timings)),
        "min_ms": min(timings),
        "max_ms": max(timings),
        "by_service": sources,
        "by_priority": priorities,
        "sources": {
            "telegram": sum(1 for d in analyses.values() if d.get("source") == "telegram"),
            "api": sum(1 for d in analyses.values() if d.get("source") != "telegram"),
        },
    }


@app.get("/db-stats")
def db_stats():
    """Реальная статистика из БД: топ сервисы, просрочки, объём за месяцы."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT TOP 10
                    MAX(s.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)')) AS ServiceName,
                    COUNT(*) AS cnt
                FROM Task t
                LEFT JOIN Service s ON t.ServiceId = s.Id
                WHERE t.Created >= DATEADD(YEAR, -1, GETDATE())
                GROUP BY t.ServiceId
                ORDER BY cnt DESC
            """)
            top_services = [{"service": row[0] or "—", "count": row[1]} for row in cursor.fetchall()]

            cursor.execute("""
                SELECT
                    SUM(CASE WHEN ReactionOverdue = 1 THEN 1 ELSE 0 END) AS reaction_overdue,
                    SUM(CASE WHEN ResolutionOverdue = 1 THEN 1 ELSE 0 END) AS resolution_overdue,
                    COUNT(*) AS total,
                    SUM(CASE WHEN Closed IS NOT NULL THEN 1 ELSE 0 END) AS closed,
                    AVG(CAST(ResolutionTimeFact AS float)) AS avg_resolution_min
                FROM Task
                WHERE Created >= DATEADD(YEAR, -1, GETDATE())
            """)
            row = cursor.fetchone()
            overdue_stats = {
                "reaction_overdue": row[0] or 0,
                "resolution_overdue": row[1] or 0,
                "total": row[2] or 0,
                "closed": row[3] or 0,
                "avg_resolution_min": round(row[4], 1) if row[4] else None,
            }

            cursor.execute("""
                SELECT
                    YEAR(Created) AS yr,
                    MONTH(Created) AS mo,
                    COUNT(*) AS cnt
                FROM Task
                WHERE Created >= DATEADD(MONTH, -12, GETDATE())
                GROUP BY YEAR(Created), MONTH(Created)
                ORDER BY yr, mo
            """)
            monthly = [{"year": r[0], "month": r[1], "count": r[2]} for r in cursor.fetchall()]

        return {
            "top_services": top_services,
            "overdue": overdue_stats,
            "monthly": monthly,
        }
    except Exception as e:
        log.error("db-stats error: %s", e)
        raise HTTPException(500, f"DB error: {e}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/analysis/{aid}")
def analysis_page(aid: str):
    return FileResponse("src/web/analysis.html")


app.mount("/static", StaticFiles(directory="src/web"), name="static")


@app.get("/")
def dashboard():
    return FileResponse("src/web/index.html")
