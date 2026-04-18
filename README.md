# Baltiyskiy Bereg — AI Service Desk Bot

LLM-чатбот для сервис-деска «ТД Балтийский Берег». Отвечает на вопросы сотрудников, опираясь на 104 000 исторических тикетов и 1 000 KB-статей из реальной системы IntraService.

**Хакатон:** AI Business SPB 2026, Кейс 4
**Репозиторий:** https://github.com/Deci1337/AI-business-SaintPetersburg-Case4

---

## Архитектура

```
Сотрудник (Telegram user-bot)
        ↓
  FastAPI /ask
        ↓
  YandexGPT (extract_query + фильтр релевантности)
        ↓
  ChromaDB (3 коллекции)
    • kb_articles   — 1 680 чанков KB-статей
    • tickets       — 82 000+ чанков Q&A тикетов
    • expenses      — 9 864 чанков решений инженеров
        ↓
  YandexGPT (генерация ответа)
        ↓
  Ответ пользователю  →  оценка 1–5  →  эскалация в admin-bot
        ↓
  Веб-дашборд аналитики (/, /analysis/{id})
```

Компоненты запускаются в трёх Docker-контейнерах: `api`, `user-bot`, `admin-bot` плюс `mssql`.

---

## Структура проекта

```
src/
  rag/
    db.py            — подключение к MSSQL, SQL-запросы
    indexer.py       — парсинг HTML → чанки → ChromaDB
    retriever.py     — векторный поиск по 3 коллекциям
    llm.py           — YandexGPT: очистка запроса, фильтр, генерация
  api/
    app.py           — FastAPI: /ask, /stats, /analyses, дашборд
  bot/
    main.py          — user-bot (Telegram, polling)
    admin_bot.py     — admin-bot: уведомления об эскалациях
    ticket_flow.py   — сценарий создания тикета
    logger.py        — лог диалогов в CSV
  web/
    index.html       — дашборд аналитики (Alpine.js + Chart.js)
    analysis.html    — детальная страница запроса
data/
  cleaned.bak        — дамп MSSQL (скачивается отдельно, не в git)
  chroma/            — векторный индекс (генерируется локально)
  dialogs.csv        — лог диалогов
scripts/
  auto-update-bots.sh — cron-скрипт обновления ботов
Dockerfile, docker-compose.yml, requirements.txt, restore-db.sh
```

---

## Зависимости

**Системные:** Docker + Docker Compose, Python 3.11+ (для локального запуска без Docker), ODBC Driver 18 for SQL Server.

**Python (`requirements.txt`):**

| Группа | Пакеты |
|---|---|
| LLM / RAG | `openai` (клиент для YandexGPT-совместимого API), `langchain`, `langchain-community`, `langchain-openai`, `tiktoken` |
| Vector DB | `chromadb`, `sentence-transformers` (embeddings: `intfloat/multilingual-e5-small`) |
| MSSQL | `pyodbc`, `sqlalchemy` |
| API / Bot | `fastapi`, `uvicorn`, `python-telegram-bot`, `httpx`, `aiofiles`, `pydantic` |
| Прочее | `python-dotenv`, `beautifulsoup4`, `apscheduler` (планировщик уведомлений) |

**Внешние сервисы:** Yandex Cloud (YandexGPT API), Telegram Bot API.

---

## Быстрый старт (локально)

```bash
git clone https://github.com/Deci1337/AI-business-SaintPetersburg-Case4
cd AI-business-SaintPetersburg-Case4
cp .env.example .env    # заполнить переменные (см. ниже)
```

### Переменные окружения (.env)

```env
YANDEX_GPT_API_KEY=
YANDEX_GPT_FOLDER_ID=
YANDEX_GPT_MODEL=yandexgpt/latest

MSSQL_SA_PASSWORD=
MSSQL_HOST=localhost
MSSQL_PORT=1433
MSSQL_DATABASE=service_desk_tdbb
MSSQL_USER=SA

TELEGRAM_BOT_TOKEN=          # user-bot
TELEGRAM_ADMIN_BOT_TOKEN=    # admin-bot
TELEGRAM_ADMIN_CHAT_IDS=     # id чатов админов через запятую
API_BASE_URL=http://localhost:8001
API_PUBLIC_URL=http://localhost:8001
```

### Поднять всё через Docker Compose

```bash
# 1. Скачать дамп БД
curl -H "X-API-Key: YOUR_API_KEY" \
     https://data.ai-business-spb.ru/data/baltiyskiy-bereg/cleaned.bak \
     -o data/cleaned.bak

# 2. Запуск (mssql автоматически восстановит БД из cleaned.bak)
docker compose up -d --build

# 3. Построить векторный индекс (один раз, ~15–20 мин)
docker compose exec api python -m src.rag.indexer
```

Дашборд: http://localhost:8001

### Альтернатива: запуск без Docker

```bash
pip install -r requirements.txt
# поднять mssql через docker compose up -d mssql
python -m src.rag.indexer                       # индексация
uvicorn src.api.app:app --port 8001             # API
python -m src.bot.main                          # user-bot (отдельный терминал)
python -m src.bot.admin_bot                     # admin-bot (отдельный терминал)
```

---

## Деплой на Yandex Cloud VM

Подробная инструкция — [DEPLOY.md](DEPLOY.md).

Короткая версия:

```bash
# На локальной машине — залить данные
scp -r data/chroma ubuntu@<VM_IP>:~/app/data/
scp data/cleaned.bak ubuntu@<VM_IP>:~/app/data/

# На сервере
curl -fsSL https://get.docker.com | sh
git clone <repo_url> ~/app && cd ~/app
cp .env.example .env && nano .env
docker compose up -d --build
```

Обновление: `git pull && docker compose up -d --build`.
Открыть TCP/8001 в Security Group Yandex Cloud для доступа к дашборду.

---

## API эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/ask` | Задать вопрос (RAG + YandexGPT) |
| GET  | `/stats` | Статистика сессии |
| GET  | `/db-stats` | Реальная статистика из MSSQL |
| GET  | `/analyses` | Список запросов сессии |
| GET  | `/analyses/{id}` | Детальный анализ запроса |
| GET  | `/health` | Статус API |
| GET  | `/` | Веб-дашборд |
| GET  | `/analysis/{id}` | Страница анализа запроса |

---

## Стек

| Компонент | Технология |
|---|---|
| LLM | YandexGPT (`yandexgpt/latest`) |
| Векторная БД | ChromaDB (persistent) |
| Embeddings | `intfloat/multilingual-e5-small` |
| База данных | MSSQL Server 2022 (Docker) |
| Бот | python-telegram-bot 21.x (user-bot + admin-bot) |
| API | FastAPI + Uvicorn |
| Фронтенд | Tailwind CSS + Alpine.js + Chart.js |
| Оркестрация | Docker Compose |
