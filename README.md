# Baltiyskiy Bereg — AI Service Desk Bot

LLM-чатбот для сервис-деска «ТД Балтийский Берег». Отвечает на вопросы сотрудников, опираясь на 104 000 исторических тикетов и 1 000 KB-статей из реальной системы IntraService.

**Хакатон:** AI Business SPB 2026, Кейс 4  
**Репозиторий:** https://github.com/Deci1337/AI-business-SaintPetersburg-Case4

---

## Архитектура

```
Сотрудник (Telegram)
        ↓
  Telegram Bot  ──────────────────────────────────────────┐
        ↓                                                  │
  FastAPI /ask                                             │
        ↓                                                  │
  YandexGPT (extract_query) ← очистка запроса             │
        ↓                                                  │
  ChromaDB (3 коллекции)                                   │
    • kb_articles   — 1 680 чанков KB-статей               │
    • tickets       — 82 000+ чанков Q&A тикетов           │
    • expenses      — 9 864 чанков решений инженеров       │
        ↓                                                  │
  YandexGPT (генерация ответа)                             │
        ↓                                                  │
  Ответ пользователю + аналитика на веб-дашборде ──────────┘
```

---

## Быстрый старт

### 1. Клонировать и настроить окружение

```bash
git clone https://github.com/Deci1337/AI-business-SaintPetersburg-Case4
cd AI-business-SaintPetersburg-Case4
cp .env.example .env
# Заполнить .env (см. раздел ниже)
pip install -r requirements.txt
```

### 2. Переменные окружения (.env)

```env
YANDEX_GPT_API_KEY=       # API-ключ Yandex Cloud
YANDEX_GPT_FOLDER_ID=     # folder_id каталога Yandex Cloud
YANDEX_GPT_MODEL=yandexgpt/latest

MSSQL_SA_PASSWORD=        # пароль SA для Docker-контейнера
MSSQL_HOST=localhost
MSSQL_PORT=1433
MSSQL_DATABASE=service_desk_tdbb
MSSQL_USER=SA

TELEGRAM_BOT_TOKEN=       # токен от @BotFather
API_BASE_URL=http://localhost:8001
API_PUBLIC_URL=http://localhost:8001
```

### 3. Поднять MSSQL и восстановить БД

Скачать дамп `cleaned.bak` и положить в `data/`:

```bash
curl -H "X-API-Key: YOUR_API_KEY" \
     https://data.ai-business-spb.ru/data/baltiyskiy-bereg/cleaned.bak \
     -o data/cleaned.bak
```

Запустить контейнер:

```bash
docker compose up -d mssql
# Подождать ~2 минуты
```

Восстановить БД:

```bash
docker exec mssql-baltbereg /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U SA -P "ВАШ_ПАРОЛЬ" -No \
  -Q "RESTORE DATABASE [service_desk_tdbb] FROM DISK='/var/opt/mssql/backup/cleaned.bak' \
  WITH MOVE 'service_desk_tdbb' TO '/var/opt/mssql/data/service_desk_tdbb.mdf', \
  MOVE 'service_desk_tdbb_log' TO '/var/opt/mssql/data/service_desk_tdbb_log.ldf', REPLACE"
```

Проверить:

```bash
docker exec mssql-baltbereg /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U SA -P "ВАШ_ПАРОЛЬ" -No \
  -Q "SELECT COUNT(*) FROM service_desk_tdbb.dbo.Task"
# Ожидается ~104 000
```

### 4. Построить векторный индекс (один раз, ~15-20 мин)

```bash
python -m src.rag.indexer
```

Индекс сохраняется в `data/chroma/`. Повторный запуск не нужен — только при обновлении БД.

### 5. Запустить API-сервер

```bash
uvicorn src.api.app:app --port 8001
```

Дашборд: http://localhost:8001

### 6. Запустить Telegram-бот (отдельный терминал)

```bash
python -m src.bot.main
```

---

## Проверка работы

```bash
# Тест поиска
python -c "
from src.rag.retriever import search
r = search('не подключается VPN')
for x in r[:3]:
    print(f'{x[\"score\"]:.3f} {x[\"meta\"][\"title\"][:50]}')
"

# Тест полного RAG-цикла
python -c "
from src.rag.llm import ask_full
r = ask_full('Не могу подключиться к VPN из дома, что делать?')
print(r['answer'])
print('Эскалация:', r['escalated'])
"
```

---

## API эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/ask` | Задать вопрос (RAG + YandexGPT) |
| GET | `/stats` | Статистика сессии |
| GET | `/db-stats` | Реальная статистика из MSSQL |
| GET | `/analyses` | Список запросов сессии |
| GET | `/analyses/{id}` | Детальный анализ запроса |
| GET | `/health` | Статус API |
| GET | `/` | Веб-дашборд |
| GET | `/analysis/{id}` | Страница анализа запроса |

---

## Структура проекта

```
src/
  rag/
    db.py         — подключение к MSSQL, SQL-запросы
    indexer.py    — парсинг HTML → чанки → ChromaDB
    retriever.py  — векторный поиск по 3 коллекциям
    llm.py        — YandexGPT: очистка запроса + генерация ответа
  bot/
    main.py       — Telegram-бот (polling)
    logger.py     — логирование диалогов в CSV
  api/
    app.py        — FastAPI сервер
  web/
    index.html    — дашборд аналитики
    analysis.html — страница анализа запроса
data/
  cleaned.bak     — дамп MSSQL (не в git, скачивать отдельно)
  chroma/         — векторный индекс (генерируется локально)
  dialogs.csv     — лог диалогов Telegram-бота
```

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| LLM | YandexGPT (yandexgpt/latest) |
| Векторная БД | ChromaDB (persistent) |
| Embeddings | intfloat/multilingual-e5-small |
| База данных | MSSQL Server 2022 (Docker) |
| Бот | python-telegram-bot 21.x |
| API | FastAPI + Uvicorn |
| Фронтенд | Tailwind CSS + Alpine.js + Chart.js |
