import json
import os
import time
import chromadb
from .indexer import CHROMA_PATH, get_model

_client = None
_kb_col = None
_ticket_col = None
_expenses_col = None

ADJUSTMENTS_FILE = "data/chunk_adjustments.json"


def get_collections():
    global _client, _kb_col, _ticket_col, _expenses_col
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        _kb_col = _client.get_or_create_collection("kb_articles")
        _ticket_col = _client.get_or_create_collection("tickets")
        _expenses_col = _client.get_or_create_collection("expenses")
    return _kb_col, _ticket_col, _expenses_col


def load_adjustments() -> dict[str, float]:
    if not os.path.exists(ADJUSTMENTS_FILE):
        return {}
    try:
        with open(ADJUSTMENTS_FILE, encoding="utf-8") as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def save_adjustments(adj: dict[str, float]) -> None:
    os.makedirs("data", exist_ok=True)
    tmp = ADJUSTMENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(adj, f, ensure_ascii=False)
    os.replace(tmp, ADJUSTMENTS_FILE)


def apply_feedback(chunk_ids: list[str], delta: float) -> None:
    """Применяет обучающую коррекцию к чанкам. delta в диапазоне примерно [-0.1, +0.1]."""
    if not chunk_ids or delta == 0:
        return
    adj = load_adjustments()
    for cid in chunk_ids:
        cur = adj.get(cid, 0.0)
        new = max(-0.3, min(0.3, cur + delta))  # cap чтобы не ушло в космос
        adj[cid] = round(new, 4)
    save_adjustments(adj)


def search(query: str, n_results: int = 5) -> list[dict]:
    t0 = time.time()
    model = get_model()
    embedding = model.encode([query]).tolist()[0]
    kb_col, ticket_col, expenses_col = get_collections()
    adj = load_adjustments()

    results = []
    for col in [kb_col, ticket_col, expenses_col]:
        try:
            r = col.query(query_embeddings=[embedding], n_results=n_results)
            for cid, doc, meta, dist in zip(
                r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]
            ):
                base = 1 - dist
                boost = adj.get(cid, 0.0)
                results.append({
                    "id": cid,
                    "text": doc,
                    "meta": meta,
                    "score": base + boost,
                    "base_score": base,
                    "boost": boost,
                })
        except Exception:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    elapsed = time.time() - t0
    if elapsed > 2.0:
        print(f"[SLOW SEARCH] {elapsed:.2f}s | query={query[:60]}")
    return results[:n_results]


def format_context(results: list[dict]) -> str:
    parts = []
    for r in results:
        src = r["meta"].get("source", "")
        title = r["meta"].get("title", "")
        if src == "kb":
            label = f"[KB] {title}"
        elif src == "expense":
            label = f"[Решение] {title}"
        else:
            label = f"[Тикет] {title}"
        parts.append(f"{label}\n{r['text']}")
    return "\n\n---\n\n".join(parts)
