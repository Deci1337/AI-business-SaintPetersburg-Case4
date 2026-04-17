# Baltiyskiy Bereg — Service Desk AI Bot

RAG-чатбот для сервис-деска на базе YandexGPT + ChromaDB.

## Быстрый старт

```bash
cp .env.example .env
# заполните .env

# 1. Положите cleaned.bak в data/
# 2. Поднимите MSSQL
docker compose up -d mssql

# 3. Восстановите БД (подождите ~2 мин после запуска контейнера)
docker exec mssql-baltbereg bash /var/opt/mssql/backup/../../../app/src/rag/restore_db.sh

# 4. Постройте RAG-индекс (первый раз ~10-20 мин)
pip install -r requirements.txt
python -m src.rag.indexer

# 5. Запустите бота
python -m src.bot.main
```

## Структура

```
src/
  rag/
    db.py        — запросы к MSSQL
    indexer.py   — парсинг HTML, построение ChromaDB-индекса
    retriever.py — векторный поиск
    llm.py       — вызов YandexGPT
  bot/
    main.py      — Telegram-бот
data/
  cleaned.bak    — дамп MSSQL (положить вручную)
  chroma/        — векторный индекс (генерируется)
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| YANDEX_GPT_API_KEY | API-ключ Yandex Cloud |
| YANDEX_GPT_FOLDER_ID | folder_id каталога |
| YANDEX_GPT_MODEL | Модель (yandexgpt/latest) |
| MSSQL_SA_PASSWORD | Пароль SA для MSSQL |
| TELEGRAM_BOT_TOKEN | Токен Telegram-бота |
