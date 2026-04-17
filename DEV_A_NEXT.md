# Dev A — Backend: ratings endpoint + .env + переиндексация

## Файлы
- `src/api/app.py` — новый эндпоинт `/ratings`
- `.env.example` — добавить ADMIN_BOT_TOKEN, ADMIN_CHAT_ID
- `data/chroma/` — синхронизировать индекс (expenses-коллекция)

Dev B не трогает эти файлы → конфликтов нет.

---

## БЛОК 1 — Эндпоинт рейтингов `/ratings`

Dev B пишет оценки в `data/ratings.csv` из admin bot.
Тебе нужно сделать API-эндпоинт чтобы дашборд мог читать эту статистику.

### Задачи

- [ ] **1. Добавить `POST /ratings`** в `src/api/app.py`

  Принимает оценку напрямую через API (дублирует CSV-запись для надёжности):
  ```python
  class Rating(BaseModel):
      analysis_id: str
      score: int  # 1-5
      question: str = ""
      answer: str = ""

  @app.post("/ratings")
  def save_rating(r: Rating):
      if not 1 <= r.score <= 5:
          raise HTTPException(400, "score must be 1-5")
      with open("data/ratings.csv", "a", newline="", encoding="utf-8") as f:
          csv.writer(f).writerow([
              datetime.now().isoformat(), r.analysis_id, r.score,
              r.question[:200], r.answer[:200]
          ])
      return {"ok": True}
  ```

- [ ] **2. Добавить `GET /ratings/stats`** — агрегация для дашборда:
  ```python
  @app.get("/ratings/stats")
  def ratings_stats():
      path = "data/ratings.csv"
      if not os.path.exists(path):
          return {"total": 0, "avg_score": None, "distribution": {}}
      rows = []
      with open(path, encoding="utf-8") as f:
          for row in csv.reader(f):
              if len(row) >= 3:
                  rows.append(int(row[2]))
      if not rows:
          return {"total": 0, "avg_score": None, "distribution": {}}
      dist = {str(i): rows.count(i) for i in range(1, 6)}
      return {
          "total": len(rows),
          "avg_score": round(sum(rows) / len(rows), 2),
          "distribution": dist,
      }
  ```
  Verify: `curl localhost:8001/ratings/stats` возвращает JSON

- [ ] **3. Добавить `import csv` в app.py** (если ещё нет)
  Verify: сервер стартует без ImportError

---

## БЛОК 2 — Обновить .env.example

Добавить новые переменные для admin bot:

```env
# Admin Bot (для уведомлений операторам и оценок ответов)
ADMIN_BOT_TOKEN=       # отдельный токен от @BotFather для admin-бота
ADMIN_CHAT_ID=         # Telegram ID группы или через запятую несколько ID
```

Verify: `.env.example` содержит эти поля

---

## БЛОК 3 — Синхронизировать индекс

У тебя переиндексировано с expenses (9 864 чанков). На машине Dev B только 2 коллекции.

- [ ] **4. Передать `data/chroma/`** Dev B (через общий диск / архив / git-lfs)
  Verify: у Dev B `python -c "import chromadb; c=chromadb.PersistentClient('./data/chroma'); [print(x.name, x.count()) for x in c.list_collections()]"` показывает 3 коллекции

---

## БЛОК 4 — Проверить extract_query в связке с retriever

Dev B добавил препроцессинг запроса через LLM (`extract_query` в `llm.py`).
Проверь что поиск улучшился:

```bash
python -c "
from src.rag.llm import extract_query
from src.rag.retriever import search

queries = [
    'привет. Очень плохо работает интернет. можете уже это исправить?',
    'не работает 1С опять, помогите пожалуйста срочно',
    'добрый день, у меня проблема с впн уже второй день',
]
for q in queries:
    cleaned = extract_query(q)
    r = search(cleaned, n_results=3)
    print(f'Оригинал: {q[:50]}')
    print(f'Cleaned:  {cleaned}')
    print(f'Топ-1: [{r[0][\"score\"]:.3f}] {r[0][\"meta\"][\"title\"][:50]}')
    print()
"
```

Verify: топ результаты релевантны очищенному запросу, score > 0.6

---

## Done When
- [ ] `POST /ratings` и `GET /ratings/stats` работают
- [ ] `.env.example` обновлён
- [ ] Индекс с 3 коллекциями передан Dev B
- [ ] extract_query проверен на 3 запросах
