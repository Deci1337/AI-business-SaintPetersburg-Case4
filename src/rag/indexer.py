"""
Парсит HTML из тикетов и KB, строит ChromaDB-индекс.
Запускать один раз: python -m src.rag.indexer
"""
import re
import chromadb
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
from .db import fetch_tickets, fetch_kb_articles, fetch_task_expenses

CHROMA_PATH = "./data/chroma"
EMBED_MODEL = "intfloat/multilingual-e5-small"  # легковесная, русский ок

_model = None


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def clean_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def chunk_text(text: str, max_chars: int = 800) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) > max_chars:
            if current:
                chunks.append(current.strip())
            current = s
        else:
            current += " " + s
    if current:
        chunks.append(current.strip())
    return chunks or [text[:max_chars]]


def _upsert_batched(col, docs, ids, metas, embeddings, batch_size=5000):
    for i in range(0, len(docs), batch_size):
        col.upsert(
            documents=docs[i:i+batch_size],
            ids=ids[i:i+batch_size],
            metadatas=metas[i:i+batch_size],
            embeddings=embeddings[i:i+batch_size],
        )


def build_index():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    model = get_model()

    # KB-статьи
    kb_col = client.get_or_create_collection("kb_articles")
    print("Загружаю KB-статьи...")
    articles = fetch_kb_articles()
    docs, ids, metas = [], [], []
    for a in articles:
        text = clean_html(a["Description"])
        for i, chunk in enumerate(chunk_text(text)):
            docs.append(chunk)
            ids.append(f"kb_{a['Id']}_{i}")
            metas.append({"source": "kb", "title": a["Name"] or "", "folder": a["FolderName"] or ""})

    if docs:
        embeddings = model.encode(docs, show_progress_bar=True).tolist()
        _upsert_batched(kb_col, docs, ids, metas, embeddings)
    print(f"KB: {len(docs)} чанков")

    # Тикеты (Comment + Description)
    ticket_col = client.get_or_create_collection("tickets")
    print("Загружаю тикеты...")
    tickets = fetch_tickets()
    docs, ids, metas = [], [], []
    for t in tickets:
        desc = clean_html(t["Description"] or "")
        comment_text = clean_html(t["Comment"])
        text = f"{desc}\n{comment_text}".strip() if desc else comment_text
        if not text:
            continue
        title = (t["Name"] or "")
        for i, chunk in enumerate(chunk_text(text)):
            full = f"Заявка: {title}\n{chunk}"
            docs.append(full)
            ids.append(f"ticket_{t['Id']}_{i}")
            metas.append({
                "source": "ticket",
                "title": title,
                "service": t["ServiceName"] or "",
                "type": t["TaskTypeName"] or "",
                "status": t["StatusName"] or "",
                "priority": t["PriorityName"] or "",
            })

    if docs:
        embeddings = model.encode(docs, batch_size=64, show_progress_bar=True).tolist()
        _upsert_batched(ticket_col, docs, ids, metas, embeddings)
    print(f"Тикеты: {len(docs)} чанков")

    # TaskExpenses — комментарии инженеров при выполнении работ
    expenses_col = client.get_or_create_collection("expenses")
    print("Загружаю TaskExpenses...")
    expenses = fetch_task_expenses()
    docs, ids, metas = [], [], []
    for e in expenses:
        text = e["Comments"]
        if not text or len(text.strip()) < 20:
            continue
        title = e["TaskName"] or ""
        for i, chunk in enumerate(chunk_text(text)):
            full = f"Решение по заявке: {title}\n{chunk}"
            docs.append(full)
            ids.append(f"expense_{e['Id']}_{i}")
            metas.append({
                "source": "expense",
                "title": title,
                "service": e["ServiceName"] or "",
            })
    if docs:
        embeddings = model.encode(docs, batch_size=64, show_progress_bar=True).tolist()
        _upsert_batched(expenses_col, docs, ids, metas, embeddings)
    print(f"Expenses: {len(docs)} чанков")
    print("Индекс построен.")


if __name__ == "__main__":
    build_index()
