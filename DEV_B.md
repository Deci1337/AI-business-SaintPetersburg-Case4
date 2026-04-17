# Разработчик B — Bot, API & Demo

## Цель
Запустить Telegram-бота, добавить fallback-логику, подготовить демо для жюри.

## Контекст
Читай CONTEXT.md — там полная архитектура и описание данных.
Рабочая папка: `C:/Dev/AI-Agency/KaspiskyCase`
RAG-индекс строит Разработчик A — дождись его сигнала или работай с моками (шаг 1).

---

## Задачи

- [x] **1. Подготовить окружение**
  ```bash
  cd C:/Dev/AI-Agency/KaspiskyCase
  cp .env.example .env
  # заполнить TELEGRAM_BOT_TOKEN (получить у @BotFather)
  # заполнить YANDEX_GPT_API_KEY, YANDEX_GPT_FOLDER_ID, MSSQL_SA_PASSWORD
  pip install -r requirements.txt
  ```
  → Verify: `python -c "from telegram.ext import ApplicationBuilder"` без ошибок

- [x] **2. Создать Telegram-бота**
  - Открыть @BotFather в Telegram
  - `/newbot` → имя: `BaltBereg Support` → username: `baltbereg_support_bot`
  - Скопировать токен в `.env` → `TELEGRAM_BOT_TOKEN=...`
  → Verify: токен в .env заполнен

- [x] **3. Добавить fallback-логику в llm.py**
  Открыть `src/rag/llm.py`, добавить оценку уверенности:
  ```python
  # После получения results в функции ask():
  if not results or results[0]["score"] < 0.4:
      return (
          "Не нашёл точного ответа в базе знаний. "
          "Рекомендую обратиться к специалисту поддержки: создайте заявку в системе."
      )
  ```
  → Verify: запрос "абракадабра xyz" → бот предлагает обратиться к оператору

- [x] **4. Улучшить UX бота — добавить команды**
  Открыть `src/bot/main.py`, добавить:
  ```python
  async def help_cmd(update, context):
      await update.message.reply_text(
          "Я помогу найти решение по базе знаний сервис-деска.\n\n"
          "Просто опишите проблему своими словами.\n"
          "Примеры:\n"
          "• «Не подключается удалённый доступ»\n"
          "• «Ошибка в 1С при формировании отчёта»\n"
          "• «Не работает принтер»"
      )

  # Зарегистрировать в main():
  app.add_handler(CommandHandler("help", help_cmd))
  ```
  → Verify: `/help` в боте возвращает подсказку

- [x] **5. Добавить логирование диалогов**
  Создать `src/bot/logger.py`:
  ```python
  import csv, os
  from datetime import datetime

  LOG_FILE = "data/dialogs.csv"

  def log_dialog(user_id: str, query: str, answer: str, escalated: bool):
      is_new = not os.path.exists(LOG_FILE)
      with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
          w = csv.writer(f)
          if is_new:
              w.writerow(["timestamp", "user_id", "query", "answer", "escalated"])
          w.writerow([datetime.now().isoformat(), user_id, query, answer[:200], escalated])
  ```
  Вызывать из `handle_message` в `main.py`:
  ```python
  from src.bot.logger import log_dialog
  escalated = "специалист" in answer or "обратитесь" in answer
  log_dialog(str(update.effective_user.id), query, answer, escalated)
  ```
  → Verify: после диалога с ботом появляется `data/dialogs.csv`

- [x] **6. Создать FastAPI endpoint для демо жюри**
  Создать `src/api/app.py`:
  ```python
  from fastapi import FastAPI
  from pydantic import BaseModel
  from src.rag.llm import ask

  app = FastAPI(title="BaltBereg Service Desk API")

  class Query(BaseModel):
      question: str

  @app.post("/ask")
  def ask_endpoint(q: Query):
      answer = ask(q.question)
      escalated = "специалист" in answer or "обратитесь" in answer
      return {"answer": answer, "escalated": escalated}

  @app.get("/health")
  def health():
      return {"status": "ok"}
  ```
  Запуск:
  ```bash
  uvicorn src.api.app:app --reload --port 8000
  ```
  → Verify: `curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"не работает VPN"}'` возвращает ответ

- [ ] **7. Подготовить 5 демо-вопросов для жюри**
  Создать `DEMO.md` — 5 реальных вопросов из базы тикетов с ожидаемыми ответами.
  Запустить каждый через бота и зафиксировать ответы.
  → Verify: все 5 вопросов дают релевантный ответ ≤30 сек

- [ ] **8. Финальная проверка перед демо**
  ```bash
  # Запустить бота
  python -m src.bot.main

  # В другом терминале — API
  uvicorn src.api.app:app --port 8000
  ```
  → Verify: бот отвечает в Telegram, API доступен по localhost:8000/docs

---

## Done When
- [ ] Telegram-бот запущен и отвечает
- [ ] Fallback работает (низкая уверенность → эскалация)
- [ ] Логирование диалогов включено
- [ ] FastAPI endpoint работает
- [ ] 5 демо-вопросов протестированы

---

## Параллельная работа пока A строит индекс
Пока Разработчик A строит индекс (~20 мин), ты можешь:
- Создать бота в @BotFather
- Написать fallback-логику
- Написать FastAPI endpoint
- Подготовить DEMO.md с вопросами
- Улучшить UX бота
