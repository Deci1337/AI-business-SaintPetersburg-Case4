import time
import chromadb
from .indexer import CHROMA_PATH, get_model

_client = None
_kb_col = None
_ticket_col = None
_expenses_col = None


def get_collections():
    global _client, _kb_col, _ticket_col, _expenses_col
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        _kb_col = _client.get_or_create_collection("kb_articles")
        _ticket_col = _client.get_or_create_collection("tickets")
        _expenses_col = _client.get_or_create_collection("expenses")
    return _kb_col, _ticket_col, _expenses_col


def search(query: str, n_results: int = 5) -> list[dict]:
    t0 = time.time()
    model = get_model()
    embedding = model.encode([query]).tolist()[0]
    kb_col, ticket_col, expenses_col = get_collections()

    results = []
    for col in [kb_col, ticket_col, expenses_col]:
        try:
            r = col.query(query_embeddings=[embedding], n_results=n_results)
            for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
                results.append({"text": doc, "meta": meta, "score": 1 - dist})
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
