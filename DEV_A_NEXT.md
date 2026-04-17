# Dev A — Plan Next (8 часов до сдачи)

## Статус сейчас
- База поднята, индекс построен ✅
- RAG-пайплайн работает (retriever → YandexGPT → ответ) ✅
- Telegram-бот запускается ✅

Ниже — задачи по приоритету. Делай сверху вниз.

---

## 🔴 КРИТИЧНО (сделать первым)

### 1. Проверить качество индекса — 5 тестовых запросов

Запусти каждый и запиши score топ-1 результата:

```bash
python -c "
from src.rag.retriever import search
queries = [
    'не подключается VPN удалёнка',
    '1С не открывается ошибка базы',
    'принтер не печатает',
    'забыл пароль от учётной записи',
    'не приходят письма на почту',
]
for q in queries:
    r = search(q)
    print(f'[{r[0][\"score\"]:.3f}] {r[0][\"meta\"][\"title\"][:60]}')
    print(f'  Запрос: {q}')
    print()
"
```

**Норма:** score > 0.5 на релевантных запросах.  
Если score < 0.45 — смотри задачу 3 (улучшение индексации).

---

### 2. Проверить полный RAG-цикл с YandexGPT

```bash
python -c "
from src.rag.llm import ask_full
import time

q = 'Не могу подключиться к VPN из дома, что делать?'
t0 = time.time()
r = ask_full(q)
elapsed = time.time() - t0

print('=== ОТВЕТ ===')
print(r['answer'])
print()
print(f'Эскалация: {r[\"escalated\"]}')
print(f'Сервис: {r[\"classification\"][\"service\"]}')
print(f'Приоритет: {r[\"classification\"][\"priority\"]}')
print(f'Топ источник: {r[\"top_source\"]}')
print(f'Время: {elapsed:.1f}с')
"
```

**Норма:** ответ за ≤30 сек, по делу, на русском, escalated=False.

---

## 🟡 ВАЖНО (улучшения качества)

### 3. Добавить `TaskExpenses.Comments` как источник

Сейчас индексируем только `Task.Comment` (Q&A диалог).  
`TaskExpenses` — комментарии инженеров при выполнении работ. Это ценно: там конкретные шаги решения.

В `src/rag/db.py` добавь новую функцию:

```python
def fetch_task_expenses(limit: int = None) -> list[dict]:
    sql = """
        SELECT TOP {limit}
            te.TaskId,
            te.Comments,
            t.Name AS TaskName,
            s.NameXml.value('(/Language/Ru)[1]', 'nvarchar(500)') AS ServiceName
        FROM TaskExpenses te
        JOIN Task t ON te.TaskId = t.Id
        LEFT JOIN Service s ON t.ServiceId = s.Id
        WHERE te.Comments IS NOT NULL AND LEN(te.Comments) > 30
        ORDER BY te.TaskId
    """.format(limit=limit if limit else 500000)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
```

Затем в `src/rag/indexer.py` в функцию `build_index()` добавь блок после тикетов:

```python
# TaskExpenses — шаги выполнения (комментарии инженеров)
from .db import fetch_task_expenses
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
        ids.append(f"expense_{e['TaskId']}_{i}")
        metas.append({
            "source": "expense",
            "title": title,
            "service": e["ServiceName"] or "",
        })
if docs:
    embeddings = model.encode(docs, batch_size=64, show_progress_bar=True).tolist()
    _upsert_batched(expenses_col, docs, ids, metas, embeddings)
print(f"Expenses: {len(docs)} чанков")
```

В `src/rag/retriever.py` добавь `expenses_col` в поиск:

```python
def get_collections():
    global _client, _kb_col, _ticket_col
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        _kb_col = _client.get_or_create_collection("kb_articles")
        _ticket_col = _client.get_or_create_collection("tickets")
        # добавить:
        global _expenses_col
        _expenses_col = _client.get_or_create_collection("expenses")
    return _kb_col, _ticket_col

# и обновить search() чтобы итерировался по трём коллекциям
```

После — переиндексировать: `python -m src.rag.indexer`

---

### 4. Улучшить чанкинг — добавить `Description` тикета

Сейчас берём только `Comment`. Но `Task.Description` — формулировка проблемы пользователем.  
Добавить её в мета при индексации тикетов в `indexer.py`:

```python
# в блоке индексации тикетов, строка ~88
desc = clean_html(t["Description"] or "")
comment_text = clean_html(t["Comment"])
text = f"{desc}\n{comment_text}".strip() if desc else comment_text
```

Это увеличит точность поиска по формулировкам пользователей.

---

### 5. Проверить порог эскалации

Текущий порог: `score < 0.4` → эскалация, `score < 0.45` → мягкая эскалация.

После добавления expenses-коллекции оценить реальное распределение score:

```bash
python -c "
from src.rag.retriever import search

test_queries = [
    'не работает принтер HP',
    'ошибка при запуске 1С',
    'заблокировали учётную запись',
    'нет интернета на рабочем месте',
    'как подключить новый монитор',
    'не могу войти в Outlook',
    'компьютер долго загружается',
]
scores = []
for q in test_queries:
    r = search(q)
    s = r[0]['score'] if r else 0
    scores.append(s)
    flag = '✅' if s > 0.5 else ('⚠️' if s > 0.4 else '❌')
    print(f'{flag} {s:.3f} | {q}')

avg = sum(scores)/len(scores)
print(f'\\nСредний score: {avg:.3f}')
print(f'Эскалируется: {sum(1 for s in scores if s < 0.4)}/{len(scores)}')
"
```

Если средний score < 0.45 — рассмотри снижение порога до 0.35.

---

## 🟢 ЕСЛИ ОСТАЛОСЬ ВРЕМЯ

### 6. Добавить поле `Priority` тикета в метаданные индекса

В `fetch_tickets()` уже есть `PriorityName`. Передать в `metas` при индексации:

```python
metas.append({
    ...
    "priority": t["PriorityName"] or "",
})
```

Это позволит в будущем фильтровать поиск по приоритету.

---

### 7. Логировать slow queries

В `src/rag/retriever.py` в функции `search()`:

```python
import time
t0 = time.time()
# ... поиск ...
elapsed = time.time() - t0
if elapsed > 2.0:
    print(f"[SLOW SEARCH] {elapsed:.2f}s | query={query[:60]}")
```

Поможет найти деградацию скорости при большом индексе.

---

## Сигнал Dev B

После завершения задач 1-2 (обязательно) и 3-4 (желательно):

> **"Индекс обновлён, RAG проверен, средний score: X.XX, эскалация на тестах: N/7"**

---

## Быстрая сводка по файлам

| Файл | Что трогаем |
|------|-------------|
| `src/rag/db.py` | Добавить `fetch_task_expenses()` |
| `src/rag/indexer.py` | Добавить блок expenses + Description в тикеты |
| `src/rag/retriever.py` | Добавить третью коллекцию в поиск |
| Остальное | Не трогать |

