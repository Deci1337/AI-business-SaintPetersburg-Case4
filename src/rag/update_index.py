"""
Инкрементальное обновление ChromaDB-индекса.

Находит новые записи в MSSQL (Task.Comment + TaskExpenses.Comments)
с CreatedDate > last_index_update, добавляет в ChromaDB через upsert.

Запуск вручную: python -m src.rag.update_index
Автозапуск: APScheduler внутри FastAPI каждые 24 часа в 03:00
"""
import json
import logging
import os
from datetime import datetime, timezone

import chromadb

from .db import get_connection
from .indexer import clean_html, chunk_text, get_model, CHROMA_PATH

log = logging.getLogger("baltbereg.update_index")

LAST_UPDATE_FILE = "data/last_index_update.json"
_DEFAULT_CUTOFF = "2000-01-01T00:00:00"


def _load_last_update() -> datetime:
    if os.path.exists(LAST_UPDATE_FILE):
        with open(LAST_UPDATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("last_update", _DEFAULT_CUTOFF)
    else:
        ts = _DEFAULT_CUTOFF
    return datetime.fromisoformat(ts)


def _save_last_update(dt: datetime) -> None:
    os.makedirs("data", exist_ok=True)
    with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_update": dt.isoformat(), "updated_at": datetime.now().isoformat()}, f)


def get_last_index_time() -> str:
    """Для дашборда — возвращает строку с датой последнего обновления."""
    if not os.path.exists(LAST_UPDATE_FILE):
        return "Никогда"
    with open(LAST_UPDATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    updated_at = data.get("updated_at", data.get("last_update", ""))
    try:
        dt = datetime.fromisoformat(updated_at)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return updated_at


def run_incremental_update() -> dict:
    """
    Основная функция обновления индекса.
    Возвращает статистику: {"tickets": int, "expenses": int, "errors": list}
    """
    cutoff = _load_last_update()
    log.info(f"Инкрементальное обновление индекса. Новее: {cutoff.isoformat()}")

    stats = {"tickets": 0, "expenses": 0, "errors": []}
    now = datetime.now()

    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        model = get_model()

        with get_connection() as conn:
            cursor = conn.cursor()

            # --- Новые тикеты ---
            cursor.execute("""
                SELECT
                    t.Id,
                    t.Name,
                    t.Description,
                    t.Created,
                    MAX(s.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)')) AS ServiceName,
                    MAX(tt.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)')) AS TaskTypeName,
                    MAX(st.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)')) AS StatusName,
                    MAX(p.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)')) AS PriorityName,
                    STRING_AGG(CAST(tc.Text AS NVARCHAR(MAX)), ' | ') AS Comment
                FROM Task t
                LEFT JOIN Service s ON t.ServiceId = s.Id
                LEFT JOIN TaskType tt ON t.TypeId = tt.Id
                LEFT JOIN Status st ON t.StatusId = st.Id
                LEFT JOIN Priority p ON t.PriorityId = p.Id
                LEFT JOIN TaskComment tc ON t.Id = tc.TaskId AND LEN(ISNULL(tc.Text, '')) > 50
                WHERE t.Created > ?
                GROUP BY t.Id, t.Name, t.Description, t.Created
                HAVING STRING_AGG(CAST(tc.Text AS NVARCHAR(MAX)), ' | ') IS NOT NULL
            """, cutoff)

            ticket_col = client.get_or_create_collection("tickets")
            rows = cursor.fetchall()
            docs, ids, metas = [], [], []
            for row in rows:
                task_id, name, desc, created, svc, ttype, status, priority, comment = row
                desc_text = clean_html(desc or "")
                comment_text = clean_html(comment or "")
                text = f"{desc_text}\n{comment_text}".strip() if desc_text else comment_text
                if not text:
                    continue
                title = name or ""
                for i, chunk in enumerate(chunk_text(text)):
                    full = f"Заявка: {title}\n{chunk}"
                    docs.append(full)
                    ids.append(f"ticket_{task_id}_{i}")
                    metas.append({
                        "source": "ticket",
                        "title": title,
                        "service": svc or "",
                        "type": ttype or "",
                        "status": status or "",
                        "priority": priority or "",
                    })

            if docs:
                embeddings = model.encode(docs, batch_size=64).tolist()
                ticket_col.upsert(documents=docs, ids=ids, metadatas=metas, embeddings=embeddings)
                stats["tickets"] = len(docs)
                log.info(f"Тикеты: добавлено {len(docs)} чанков")

            # --- Новые TaskExpenses ---
            cursor.execute("""
                SELECT
                    te.Id,
                    t.Name AS TaskName,
                    te.Comments,
                    te.Created,
                    MAX(s.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)')) AS ServiceName
                FROM TaskExpenses te
                LEFT JOIN Task t ON te.TaskId = t.Id
                LEFT JOIN Service s ON t.ServiceId = s.Id
                WHERE te.Created > ?
                  AND LEN(ISNULL(te.Comments, '')) > 20
                GROUP BY te.Id, t.Name, te.Comments, te.Created
            """, cutoff)

            expenses_col = client.get_or_create_collection("expenses")
            rows = cursor.fetchall()
            docs, ids, metas = [], [], []
            for row in rows:
                exp_id, task_name, comments, created, svc = row
                if not comments:
                    continue
                title = task_name or ""
                for i, chunk in enumerate(chunk_text(comments)):
                    full = f"Решение по заявке: {title}\n{chunk}"
                    docs.append(full)
                    ids.append(f"expense_{exp_id}_{i}")
                    metas.append({
                        "source": "expense",
                        "title": title,
                        "service": svc or "",
                    })

            if docs:
                embeddings = model.encode(docs, batch_size=64).tolist()
                expenses_col.upsert(documents=docs, ids=ids, metadatas=metas, embeddings=embeddings)
                stats["expenses"] = len(docs)
                log.info(f"Expenses: добавлено {len(docs)} чанков")

        _save_last_update(now)
        log.info(f"Обновление завершено. Тикеты: {stats['tickets']}, Expenses: {stats['expenses']}")

    except Exception as e:
        msg = f"Ошибка обновления индекса: {e}"
        log.error(msg)
        stats["errors"].append(msg)

    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_incremental_update()
    print(f"Готово: {result}")
