import chromadb
from sentence_transformers import SentenceTransformer
from .indexer import CHROMA_PATH, EMBED_MODEL, get_model

_client = None
_kb_col = None
_ticket_col = None


def get_collections():
    global _client, _kb_col, _ticket_col
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        _kb_col = _client.get_or_create_collection("kb_articles")
        _ticket_col = _client.get_or_create_collection("tickets")
    return _kb_col, _ticket_col


def search(query: str, n_results: int = 5) -> list[dict]:
    model = get_model()
    embedding = model.encode([query]).tolist()[0]
    kb_col, ticket_col = get_collections()

    results = []
    for col in [kb_col, ticket_col]:
        try:
            r = col.query(query_embeddings=[embedding], n_results=n_results)
            for doc, meta, dist in zip(r["documents"][0], r["metadatas"][0], r["distances"][0]):
                results.append({"text": doc, "meta": meta, "score": 1 - dist})
        except Exception:
            pass

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:n_results]


def format_context(results: list[dict]) -> str:
    parts = []
    for r in results:
        src = r["meta"].get("source", "")
        title = r["meta"].get("title", "")
        label = f"[{'KB' if src == 'kb' else 'Тикет'}] {title}"
        parts.append(f"{label}\n{r['text']}")
    return "\n\n---\n\n".join(parts)
