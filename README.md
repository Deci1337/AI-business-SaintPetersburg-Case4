# Baltiyskiy Bereg — AI Service Desk Bot

LLM-чатбот сервис-деска «ТД Балтийский Берег»: RAG поверх исторических тикетов и KB-статей IntraService.

---

## Структура кода

```
src/
  rag/
    db.py            — подключение к MSSQL, SQL-запросы
    indexer.py       — парсинг HTML → чанки → ChromaDB
    update_index.py  — инкрементальное обновление индекса
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
    index.html       — дашборд (Alpine.js + Chart.js)
    analysis.html    — детальная страница запроса
data/
  cleaned.bak        — дамп MSSQL (скачивается отдельно)
  chroma/            — векторный индекс (генерируется)
  dialogs.csv        — лог диалогов
scripts/             — вспомогательные shell-скрипты
tests/               — pytest
Dockerfile, docker-compose.yml, requirements.txt, restore-db.sh
```

---

## Зависимости

**Системные:** Docker + Docker Compose, Python 3.11+, ODBC Driver 18 for SQL Server.

**Python (`requirements.txt`):**

| Группа | Пакеты |
|---|---|
| LLM / RAG | `openai`, `langchain`, `langchain-community`, `langchain-openai`, `tiktoken` |
| Vector DB | `chromadb`, `sentence-transformers` (`intfloat/multilingual-e5-small`) |
| MSSQL | `pyodbc`, `sqlalchemy` |
| API / Bot | `fastapi`, `uvicorn`, `python-telegram-bot`, `httpx`, `aiofiles`, `pydantic` |
| Прочее | `python-dotenv`, `beautifulsoup4`, `apscheduler` |

**Внешние сервисы:** YandexGPT API, Telegram Bot API.

---

## Деплой

### 1. Клонирование и конфигурация

```bash
git clone https://github.com/Deci1337/AI-business-SaintPetersburg-Case4
cd AI-business-SaintPetersburg-Case4
cp .env.example .env
```

Переменные `.env`:

```env
YANDEX_GPT_API_KEY=
YANDEX_GPT_FOLDER_ID=
YANDEX_GPT_MODEL=yandexgpt/latest

MSSQL_SA_PASSWORD=
MSSQL_HOST=localhost
MSSQL_PORT=1433
MSSQL_DATABASE=service_desk_tdbb
MSSQL_USER=SA

TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_BOT_TOKEN=
TELEGRAM_ADMIN_CHAT_IDS=
API_BASE_URL=http://localhost:8001
API_PUBLIC_URL=http://localhost:8001
```

### 2. Запуск через Docker Compose

```bash
# Скачать дамп БД
curl -H "X-API-Key: YOUR_API_KEY" \
     https://data.ai-business-spb.ru/data/baltiyskiy-bereg/cleaned.bak \
     -o data/cleaned.bak

# Поднять все сервисы (api, user-bot, admin-bot, mssql)
docker compose up -d --build

# Построить векторный индекс (один раз, ~15–20 мин)
docker compose exec api python -m src.rag.indexer
```

Дашборд доступен по адресу `http://<IP_сервера>:8001` — подставьте IP вашей VM (или домен из `API_PUBLIC_URL` в `.env`).

### 3. Деплой на Yandex Cloud VM

```bash
# Локально — залить данные
scp -r data/chroma ubuntu@<VM_IP>:~/app/data/
scp data/cleaned.bak ubuntu@<VM_IP>:~/app/data/

# На сервере
curl -fsSL https://get.docker.com | sh
git clone <repo_url> ~/app && cd ~/app
cp .env.example .env && nano .env
docker compose up -d --build
```

Обновление: `git pull && docker compose up -d --build`.
В Security Group Yandex Cloud открыть TCP/8001.
