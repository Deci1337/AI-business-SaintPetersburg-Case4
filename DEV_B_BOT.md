# Dev B — Веб-дашборд + Admin Bot (твои задачи)

## Файлы
- `src/web/index.html` — дашборд
- `src/web/analysis.html` — страница анализа
- `src/bot/admin_bot.py` — **новый файл** (admin bot)

Dev A не трогает эти файлы → конфликтов нет.

---

## БЛОК 1 — Редизайн веб-дашборда

### Принципы
- Никаких эмодзи в интерфейсе (убрать все 🔍📊💬🗂🎯📋📈 и т.д.)
- Чистый профессиональный вид: белый фон, серые границы, синие акценты
- Графики — только там где данных достаточно, иначе таблица/список
- Все тексты читаются без обрезания

### Задачи

- [ ] **1. Убрать все эмодзи** из `index.html` и `analysis.html`
  Заменить на иконки SVG или просто текст.
  Verify: ctrl+F по "emoji" и символам типа 🔍💬 — не найдёт ничего

- [ ] **2. Починить "Распределение времени"** в `analysis.html`
  Сейчас текст выезжает за пределы полосы при коротких значениях.
  Решение: убрать текст внутри полосы, показать значения снаружи как подписи под баром.
  ```html
  <!-- Вместо текста внутри div — подписи снаружи -->
  <div class="flex gap-4 mt-3 text-sm text-gray-600">
    <span>Поиск: <strong x-text="data?.timing?.search_ms + ' мс'"></strong></span>
    <span>YandexGPT: <strong x-text="data?.timing?.llm_ms + ' мс'"></strong></span>
    <span>Итого: <strong x-text="data?.timing?.total_ms + ' мс'"></strong></span>
  </div>
  ```
  Verify: при любых значениях текст не выезжает

- [ ] **3. Редизайн шапки** — убрать синий квадрат с иконкой, сделать минималистично:
  просто логотип текстом "Балтийский Берег" + подзаголовок "Service Desk AI Dashboard"
  Verify: шапка выглядит как у корпоративного инструмента

- [ ] **4. Карточки метрик** — убрать крупные цифры с `text-2xl/3xl`, сделать компактнее:
  метрика + подпись в одну строку, горизонтальный ряд
  Verify: метрики занимают меньше места, читаются с первого взгляда

- [ ] **5. Графики статистики БД** — добавить заголовки осей, убрать лишние gridlines
  Для `dbServicesChart` (горизонтальный бар): убрать легенду, добавить значения на баре (`datalabels` или просто `tooltip`)
  Для `dbMonthlyChart` (линейный): добавить подпись оси Y "Заявок"
  Verify: графики читаются без пояснений

- [ ] **6. Блок "Найденные фрагменты"** в `analysis.html` — убрать score-бар, оставить только число %
  Бар дублирует число и занимает место. Достаточно цветного бейджа с процентом.
  Verify: список чанков компактнее, бара нет

---

## БЛОК 2 — Admin Bot (`src/bot/admin_bot.py`)

### Логика
1. Бот отвечает пользователю автоматически
2. **Всегда** — в admin-чат падает карточка: вопрос + ответ нейросети + оценка 1-5
3. **При эскалации** — дополнительно кнопка "Взять вопрос" (первый нажавший берёт)
4. Взявший админ отвечает текстом — бот пересылает ответ пользователю
5. Оценки собираются в CSV для последующего дообучения

### Переменные окружения (добавить в .env)
```
ADMIN_BOT_TOKEN=       # отдельный токен от @BotFather
ADMIN_CHAT_ID=         # ID группы/канала или список через запятую
```

### Задачи

- [ ] **7. Создать `src/bot/admin_bot.py`**

  Скелет:
  ```python
  import os, csv, asyncio, logging
  from datetime import datetime
  from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
  from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters, ContextTypes
  from dotenv import load_dotenv

  load_dotenv()
  ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
  ADMIN_CHAT_IDS = [int(x) for x in os.getenv("ADMIN_CHAT_ID", "").split(",") if x.strip()]
  RATINGS_FILE = "data/ratings.csv"

  # claimed[analysis_id] = admin_user_id — кто взял вопрос
  claimed = {}
  # pending[analysis_id] = {"user_chat_id": ..., "question": ..., "answer": ...}
  pending = {}
  ```

- [ ] **8. Функция `notify_admins(analysis_id, question, answer, escalated, user_chat_id)`**

  Отправляет во все ADMIN_CHAT_IDS карточку:
  ```
  Новый запрос #abc123

  Вопрос: не подключается VPN
  Ответ бота: Проверьте клиент UniVPN...
  Статус: Автоответ / Эскалация
  ```
  Кнопки оценки: [1] [2] [3] [4] [5]
  Если escalated=True — добавить кнопку [Взять вопрос]

  Verify: при запросе через Telegram в admin-чат приходит карточка

- [ ] **9. Обработчик кнопок `callback_query_handler`**

  Парсить `callback_data`:
  - `rate_{aid}_{score}` → записать в `data/ratings.csv`: timestamp, aid, question, answer, score
  - `claim_{aid}` → если `aid` не в `claimed`: записать admin_id, изменить кнопку на "Взято @username", остальным убрать кнопку
  - `claim_{aid}` если уже занят → ответить `answer_callback_query("Уже взято другим оператором")`

  Verify: оценка записывается в CSV, повторное нажатие "Взять" блокируется

- [ ] **10. Обработчик ответа админа**

  Если admin_id есть в `claimed.values()` и пишет текст → найти `pending` по admin_id → переслать текст пользователю через основной бот токен (`BOT_TOKEN` из env).

  Verify: пользователь получает ответ от имени основного бота

- [ ] **11. Подключить `notify_admins` к основному боту**

  В `src/bot/main.py` после `log_dialog(...)` добавить:
  ```python
  from src.bot.admin_bot import notify_admins
  asyncio.create_task(notify_admins(
      analysis_id=data.get("analysis_id"),
      question=query,
      answer=answer,
      escalated=escalated,
      user_chat_id=update.effective_chat.id,
  ))
  ```
  Verify: admin bot запущен параллельно, карточки приходят

- [ ] **12. Запуск admin bot**

  Добавить в `src/bot/admin_bot.py` блок `if __name__ == "__main__"` по аналогии с `main.py`.
  Verify: `python -m src.bot.admin_bot` запускается без ошибок

---

## Done When
- [ ] Дашборд без эмодзи, графики читаются, тайминги не выезжают
- [ ] Admin bot получает карточку на каждый запрос
- [ ] Оценки записываются в `data/ratings.csv`
- [ ] Эскалированные запросы можно "взять" одному из админов
- [ ] Пользователь получает ответ от взявшего админа
