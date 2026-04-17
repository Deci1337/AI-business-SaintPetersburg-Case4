import time
import uuid
from collections import OrderedDict
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from src.rag.llm import ask
from src.rag.retriever import search

app = FastAPI(title="BaltBereg Service Desk API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory хранилище (LRU, макс 1000 записей)
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
    source: str = "api"  # "api" | "telegram"


@app.post("/ask")
def ask_endpoint(q: Query):
    t0 = time.time()
    chunks = search(q.question, n_results=6)
    search_ms = round((time.time() - t0) * 1000)

    t1 = time.time()
    answer, escalated = ask(q.question)
    llm_ms = round((time.time() - t1) * 1000)
    total_ms = round((time.time() - t0) * 1000)

    data = {
        "question": q.question,
        "answer": answer,
        "escalated": escalated,
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
    data["analysis_url"] = f"http://localhost:8000/analysis/{aid}"
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
        }
        for aid, d in reversed(list(analyses.items()))
    ]


@app.get("/analyses/{aid}")
def get_analysis(aid: str):
    if aid not in analyses:
        raise HTTPException(404, "Анализ не найден")
    return analyses[aid]


@app.get("/analysis/{aid}", response_class=HTMLResponse)
def analysis_page(aid: str):
    return FileResponse("src/web/analysis.html")


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory="src/web"), name="static")


@app.get("/")
def dashboard():
    return FileResponse("src/web/index.html")
