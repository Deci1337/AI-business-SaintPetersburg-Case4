# Контекст проекта — Baltiyskiy Bereg Service Desk Bot

## Что это

Хакатон AI Business SPB 2026. Задача — LLM-чатбот для сервис-деска компании «Балтийский Берег» (производитель рыбной продукции, ~1000 сотрудников).

**Дедлайн: очень скоро. МВП нужен в течение первых 12 часов работы.**

---

## Проблема заказчика

Нет структурированной базы знаний. Вся экспертиза — в 104 000 исторических тикетов и ~1000 KB-статей в системе IntraService (MSSQL). Сотрудники не могут быстро найти решение — идут к живому оператору.

**Цель:** бот отвечает на вопросы сотрудников, опираясь на историю тикетов и KB. ≤50% запросов должны уходить к живому оператору.

---

## Архитектура (RAG)

```
Пользователь (Telegram)
        ↓
    Telegram Bot (python-telegram-bot)
        ↓
    Retriever (ChromaDB + multilingual-e5-small)
        ↓ top-6 чанков
    YandexGPT (OpenAI-совместимый API)
        ↓
    Ответ пользователю
```

**Почему RAG, а не fine-tuning:**
- 12 часов на МВП — обучение модели нереально
- RAG решает задачу: поиск по документам, не генерация новых знаний
- YandexGPT уже предоставлен организаторами

---

## Данные

- **Источник:** дамп MSSQL `cleaned.bak` (~104 000 тикетов, ~1000 KB-статей)
- **Ключевые таблицы:**
  - `Task` — тикеты. Главное поле: `Comment` (HTML Q&A диалоги между пользователем и поддержкой)
  - `KBDocument` — статьи базы знаний (HTML)
  - `Service`, `TaskType`, `Status`, `Priority` — lookup-таблицы
- **ПДн:** удалены из дампа организаторами

---

## Стек

| Компонент | Технология |
|---|---|
| LLM | YandexGPT (yandexgpt/latest) через OpenAI-совместимый API |
| Векторная БД | ChromaDB (persistent, локально) |
| Embeddings | intfloat/multilingual-e5-small (русский, легковесная) |
| База данных | MSSQL 2022 в Docker |
| Бот | python-telegram-bot 21.x |
| HTML-парсинг | BeautifulSoup4 |

---

## Структура проекта

```
KaspiskyCase/
  .env                  ← секреты (не коммитить)
  .env.example          ← шаблон переменных
  docker-compose.yml    ← MSSQL + app
  Dockerfile
  requirements.txt
  README.md             ← инструкция по запуску
  CONTEXT.md            ← этот файл
  src/
    rag/
      db.py             ← подключение к MSSQL, SQL-запросы тикетов и KB
      indexer.py        ← парсинг HTML → чанки → ChromaDB (запускать 1 раз)
      retriever.py      ← векторный поиск по ChromaDB
      llm.py            ← вызов YandexGPT, сборка промпта
      restore_db.sh     ← скрипт восстановления .bak внутри контейнера
    bot/
      main.py           ← Telegram-бот (polling)
  data/
    cleaned.bak         ← дамп MSSQL (положить вручную, не в git)
    chroma/             ← векторный индекс (генерируется автоматически)
```

---

## Переменные окружения (.env)

```env
YANDEX_GPT_API_KEY=     # API-ключ Yandex Cloud (есть у команды)
YANDEX_GPT_FOLDER_ID=   # folder_id каталога Yandex Cloud
YANDEX_GPT_MODEL=yandexgpt/latest
YANDEX_GPT_BASE_URL=https://llm.api.cloud.yandex.net/foundationModels/v1

MSSQL_SA_PASSWORD=      # придумать сложный пароль
MSSQL_DB=service_desk_tdbb

TELEGRAM_BOT_TOKEN=     # токен от @BotFather
```

---

## Следующие шаги (актуально)

### Шаг 1 — Подготовка окружения
```bash
cd C:/Dev/AI-Agency/KaspiskyCase
cp .env.example .env
# заполнить .env своими ключами
```

### Шаг 2 — Положить данные
```
data/cleaned.bak  ← скопировать файл дампа сюда
```

### Шаг 3 — Поднять MSSQL
```bash
docker compose up -d mssql
# подождать ~2 минуты
```

### Шаг 4 — Восстановить БД из бэкапа
```bash
docker exec mssql-baltbereg /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U SA -P "ВАШ_ПАРОЛЬ" -No \
  -Q "RESTORE DATABASE [service_desk_tdbb] FROM DISK='/var/opt/mssql/backup/cleaned.bak' WITH MOVE 'service_desk_tdbb' TO '/var/opt/mssql/data/service_desk_tdbb.mdf', MOVE 'service_desk_tdbb_log' TO '/var/opt/mssql/data/service_desk_tdbb_log.ldf', REPLACE"
```

### Шаг 5 — Установить зависимости
```bash
pip install -r requirements.txt
```

### Шаг 6 — Построить RAG-индекс (один раз, ~10-20 мин)
```bash
python -m src.rag.indexer
```
Индекс сохраняется в `data/chroma/`. Повторно запускать не нужно.

### Шаг 7 — Запустить бота
```bash
python -m src.bot.main
```

---

## Что ещё нужно сделать (бэклог МВП)

- [ ] Проверить подключение к MSSQL после восстановления БД
- [ ] Проверить вызов YandexGPT (тестовый запрос)
- [ ] Прогнать индексацию, убедиться что чанки корректные
- [ ] Протестировать бота на 5-10 реальных вопросах из тикетов
- [ ] Добавить fallback — если уверенность низкая, направлять к оператору
- [ ] (Опционально) Простой веб-интерфейс или FastAPI endpoint для демо жюри

---

## Критерии оценки жюри (держать в голове)

1. Бот работает без ошибок, отвечает ≤30 сек
2. Решает реальные проблемы (показать живые диалоги из БД)
3. Код запускается по инструкции, есть README
4. Инновационность — извлечение знаний из 104к неструктурированных тикетов

---

## Контакты команды

Проект ведёт: mike.dvortsov@gmail.com
