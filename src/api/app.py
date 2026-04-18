import csv
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.rag.llm import ask_full
from src.rag.retriever import search
from src.rag.db import get_connection
from src.rag.update_index import run_incremental_update, get_last_index_time

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("baltbereg.api")
PUBLIC_URL = os.getenv("API_PUBLIC_URL", "http://localhost:8001")
API_KEY = os.getenv("API_KEY")


def require_api_key(authorization: str | None = Header(default=None)):
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        run_incremental_update,
        CronTrigger(hour=3, minute=0),
        id="incremental_index",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    log.info("APScheduler запущен — обновление индекса каждые сутки в 03:00")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="BaltBereg Service Desk API", version="1.0.0", lifespan=lifespan)

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
QUESTIONS_LOG = "data/questions_log.jsonl"


def _append_question_log(entry: dict) -> None:
    """Персистентный лог каждого вопроса — не сбрасывается при перезапуске."""
    os.makedirs("data", exist_ok=True)
    with open(QUESTIONS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _normalize_question(q: str) -> str:
    """Приводим вопрос к общему виду для группировки похожих."""
    q = q.lower().strip()
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q)
    # Убираем стоп-слова
    stop = {"у", "в", "на", "с", "по", "из", "к", "за", "и", "или", "не", "как",
            "что", "мне", "я", "он", "она", "они", "это", "мой", "наш", "при",
            "от", "до", "об", "для", "то", "а", "но", "же", "уже", "ещё", "нет"}
    words = [w for w in q.split() if w not in stop and len(w) > 2]
    return " ".join(words[:8])  # первые 8 значимых слов — ключ группировки


def save_analysis(data: dict) -> str:
    aid = str(uuid.uuid4())
    if len(analyses) >= MAX_ANALYSES:
        analyses.popitem(last=False)
    analyses[aid] = data
    return aid


class DialogTurn(BaseModel):
    user: str
    assistant: str


class Query(BaseModel):
    question: str
    source: str = "api"
    history: list[DialogTurn] = []


@app.post("/ask")
def ask_endpoint(q: Query, _=Depends(require_api_key)):
    t0 = time.time()
    chunks = search(q.question, n_results=6)
    search_ms = round((time.time() - t0) * 1000)

    t1 = time.time()
    history = [{"user": t.user, "assistant": t.assistant} for t in q.history]
    result = ask_full(q.question, history=history)
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

    _append_question_log({
        "ts": data["created_at"],
        "question": q.question,
        "service": result["classification"].get("service", "Другое"),
        "escalated": result["escalated"],
    })
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


@app.get("/stats/weekly")
def get_weekly_stats():
    """За последние 7 дней: всего запросов боту и сколько эскалировано."""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=7)
    week = [d for d in analyses.values() if datetime.fromisoformat(d["created_at"]) >= cutoff]
    total = len(week)
    escalated = sum(1 for d in week if d["escalated"])
    resolved = total - escalated
    return {
        "total": total,
        "escalated": escalated,
        "resolved": resolved,
        "escalation_rate": round(escalated / total * 100, 1) if total else 0,
        "automation_rate": round(resolved / total * 100, 1) if total else 0,
    }


@app.get("/stats/knowledge-gaps")
def knowledge_gaps(period: str = "month", top_n: int = 7):
    """Топ категорий и конкретных вопросов из персистентного лога.
    Читает data/questions_log.jsonl — не сбрасывается при перезапуске."""
    from datetime import timedelta
    from collections import defaultdict

    period_days = {"day": 1, "week": 7, "month": 30, "all": None}
    days = period_days.get(period, 30)
    cutoff = (datetime.now() - timedelta(days=days)) if days else None

    # Читаем персистентный лог
    rows = []
    if os.path.exists(QUESTIONS_LOG):
        with open(QUESTIONS_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if cutoff:
                        ts = datetime.fromisoformat(entry["ts"])
                        if ts < cutoff:
                            continue
                    rows.append(entry)
                except Exception:
                    pass

    # Дополняем RAM-сессией (вдруг лог пустой — первый запуск)
    if not rows:
        for d in analyses.values():
            if cutoff and datetime.fromisoformat(d["created_at"]) < cutoff:
                continue
            rows.append({
                "ts": d["created_at"],
                "question": d.get("question", ""),
                "service": d.get("classification", {}).get("service", "Другое"),
                "escalated": d.get("escalated", False),
            })

    if not rows:
        return {"period": period, "total": 0, "categories": []}

    total = len(rows)

    # Группировка по категории
    cat_counts: dict[str, int] = defaultdict(int)
    # Внутри категории — счётчик нормализованных вопросов + оригинал
    cat_qcounts: dict[str, dict[str, dict]] = defaultdict(dict)

    for entry in rows:
        svc = entry.get("service", "Другое")
        q_orig = entry.get("question", "").strip()
        cat_counts[svc] += 1

        if q_orig:
            key = _normalize_question(q_orig)
            if key not in cat_qcounts[svc]:
                cat_qcounts[svc][key] = {"question": q_orig, "count": 0}
            cat_qcounts[svc][key]["count"] += 1

    sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    categories = []
    for svc, cnt in sorted_cats:
        # Топ-5 конкретных вопросов по частоте
        top_qs = sorted(cat_qcounts[svc].values(), key=lambda x: x["count"], reverse=True)[:5]
        categories.append({
            "service": svc,
            "count": cnt,
            "share_pct": round(cnt / total * 100, 1),
            "top_questions": [
                {"question": q["question"], "count": q["count"]}
                for q in top_qs
            ],
        })

    return {"period": period, "total": total, "categories": categories}


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


@app.get("/ratings/timeline")
def ratings_timeline(period: str = "day"):
    """Динамика оценок: всего оценено и положительно (4-5★) по периодам (hour/day/week)."""
    path = "data/ratings.csv"
    if not os.path.exists(path):
        return {"labels": [], "total": [], "positive": []}

    fmt_map = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "week": "%Y-W%W"}
    fmt = fmt_map.get(period, "%Y-%m-%d")

    from collections import defaultdict
    buckets_total = defaultdict(int)
    buckets_pos = defaultdict(int)

    with open(path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            try:
                ts = datetime.fromisoformat(row[0])
                score = int(row[2])
                key = ts.strftime(fmt)
                buckets_total[key] += 1
                if score >= 4:
                    buckets_pos[key] += 1
            except (ValueError, IndexError):
                pass

    labels = sorted(buckets_total.keys())
    return {
        "labels": labels,
        "total": [buckets_total[l] for l in labels],
        "positive": [buckets_pos[l] for l in labels],
    }


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


@app.get("/index-status")
def index_status():
    """Дата последнего обновления ChromaDB-индекса."""
    return {"last_updated": get_last_index_time()}


@app.post("/index-update", dependencies=[Depends(require_api_key)])
async def trigger_index_update():
    """Ручной запуск инкрементального обновления индекса (для отладки)."""
    import asyncio
    asyncio.create_task(asyncio.to_thread(run_incremental_update))
    return {"status": "started"}


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
