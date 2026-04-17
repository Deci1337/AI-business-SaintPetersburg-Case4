import csv
import json
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
            top_services = {(row[0] or "—"): row[1] for row in cursor.fetchall()}

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
            total = row[2] or 0
            closed = row[3] or 0
            overdue = row[1] or 0
            avg_min = row[4]

            months_ru = ['', 'Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек']
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
            monthly = {f"{months_ru[r[1]]} {r[0]}": r[2] for r in cursor.fetchall()}

        return {
            "top_services": top_services,
            "monthly": monthly,
            "total_tasks": total,
            "closed_tasks": closed,
            "overdue_tasks": overdue,
            "avg_resolution_hours": round(avg_min / 60, 1) if avg_min else None,
        }
    except Exception as e:
        log.error("db-stats error: %s", e)
        raise HTTPException(500, f"DB error: {e}")


class Rating(BaseModel):
    analysis_id: str
    score: int
    question: str = ""
    answer: str = ""


@app.post("/ratings")
def save_rating(r: Rating):
    if not 1 <= r.score <= 5:
        raise HTTPException(400, "score must be 1-5")
    os.makedirs("data", exist_ok=True)
    with open("data/ratings.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().isoformat(), r.analysis_id, r.score,
            r.question[:200], r.answer[:200],
        ])
    return {"ok": True}


@app.get("/weights")
def get_weights():
    path = "data/weights.json"
    defaults = {"1": -2, "2": -1, "3": 0, "4": 1, "5": 2}
    if not os.path.exists(path):
        return defaults
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/ratings/stats")
def ratings_stats():
    path = "data/ratings.csv"
    if not os.path.exists(path):
        return {"total": 0, "avg_score": None, "distribution": {}}
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 3:
                try:
                    rows.append(int(row[2]))
                except ValueError:
                    pass
    if not rows:
        return {"total": 0, "avg_score": None, "distribution": {}}
    return {
        "total": len(rows),
        "avg_score": round(sum(rows) / len(rows), 2),
        "distribution": {str(i): rows.count(i) for i in range(1, 6)},
    }


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
