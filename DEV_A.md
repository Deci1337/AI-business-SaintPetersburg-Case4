# Разработчик A — Data & RAG Pipeline

## Цель
Поднять MSSQL, восстановить БД, построить векторный индекс, проверить качество поиска.

## Контекст
Читай CONTEXT.md — там полная архитектура и описание данных.
Рабочая папка: `C:/Dev/AI-Agency/KaspiskyCase`

---

## Задачи

- [ ] **1. Подготовить окружение**
  ```bash
  cd C:/Dev/AI-Agency/KaspiskyCase
  cp .env.example .env
  # заполнить YANDEX_GPT_API_KEY, YANDEX_GPT_FOLDER_ID, MSSQL_SA_PASSWORD
  pip install -r requirements.txt
  ```
  → Verify: `python -c "import pyodbc, chromadb, sentence_transformers"` без ошибок

- [ ] **2. Положить данные и поднять MSSQL**
  ```bash
  # скопировать cleaned.bak в data/
  docker compose up -d mssql
  # подождать 2 минуты
  ```
  → Verify: `docker ps` показывает mssql-baltbereg (healthy)

- [ ] **3. Восстановить БД из бэкапа**
  ```bash
  docker exec mssql-baltbereg /opt/mssql-tools18/bin/sqlcmd \
    -S localhost -U SA -P "ВАШ_ПАРОЛЬ" -No \
    -Q "RESTORE DATABASE [service_desk_tdbb] FROM DISK='/var/opt/mssql/backup/cleaned.bak' \
    WITH MOVE 'service_desk_tdbb' TO '/var/opt/mssql/data/service_desk_tdbb.mdf', \
    MOVE 'service_desk_tdbb_log' TO '/var/opt/mssql/data/service_desk_tdbb_log.ldf', REPLACE"
  ```
  → Verify:
  ```bash
  docker exec mssql-baltbereg /opt/mssql-tools18/bin/sqlcmd \
    -S localhost -U SA -P "ВАШ_ПАРОЛЬ" -No \
    -Q "SELECT COUNT(*) FROM service_desk_tdbb.dbo.Task"
  ```
  Должно вернуть ~104000

- [ ] **4. Проверить данные вручную**
  ```bash
  python -c "
  from src.rag.db import fetch_tickets, fetch_kb_articles
  t = fetch_tickets(limit=5)
  print(t[0])
  kb = fetch_kb_articles()
  print(f'KB: {len(kb)} статей')
  "
  ```
  → Verify: видим тикеты с полем Comment (HTML), KB-статьи с Description

- [ ] **5. Запустить индексацию (~10-20 мин)**
  ```bash
  python -m src.rag.indexer
  ```
  → Verify: в конце выводит "Индекс построен", папка `data/chroma/` не пустая

- [ ] **6. Проверить качество поиска**
  ```bash
  python -c "
  from src.rag.retriever import search
  results = search('не подключается удаленка VPN')
  for r in results:
      print(r['score'], r['meta']['title'])
      print(r['text'][:200])
      print('---')
  "
  ```
  → Verify: топ результаты релевантны запросу, score > 0.5

- [ ] **7. Проверить полный RAG-цикл (LLM)**
  ```bash
  python -c "
  from src.rag.llm import ask
  print(ask('Не подключается удаленка, что делать?'))
  "
  ```
  → Verify: ответ на русском, по делу, ≤30 сек

- [ ] **8. (Если время есть) Улучшить индексацию**
  - Проверить, что HTML из `Task.Comment` парсится корректно
  - Если чанки слишком короткие/длинные — скорректировать `max_chars` в `indexer.py`
  - Добавить поле `TaskExpenses.Comments` как дополнительный источник

---

## Done When
- [ ] MSSQL работает, БД восстановлена
- [ ] Индекс построен (KB + тикеты)
- [ ] Поиск возвращает релевантные результаты
- [ ] YandexGPT отвечает на тестовый вопрос

---

## Передать разработчику B
Когда индекс готов — сообщить: **"Индекс готов, RAG работает"**.
Разработчик B может запустить бота.
