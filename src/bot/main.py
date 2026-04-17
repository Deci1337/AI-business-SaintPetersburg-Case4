import asyncio
import html
import os
import logging
import httpx
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from src.bot.logger import log_dialog

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8001")

HELP_TEXT = (
    "Я помогу найти решение по базе знаний сервис-деска «Балтийский Берег».\n\n"
    "Просто опишите проблему своими словами. Примеры:\n"
    "• Не подключается удалённый доступ (VPN)\n"
    "• Ошибка в 1С при формировании отчёта\n"
    "• Не работает принтер\n"
    "• Нет доступа к папке на сервере\n\n"
    "Команды:\n"
    "/start — начать\n"
    "/help — помощь"
)

QUICK_REPLIES = ReplyKeyboardMarkup(
    [
        ["Не работает VPN", "Проблема с 1С"],
        ["Нет доступа к файлам", "Не работает почта"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

SERVICE_EMOJI = {
    "IT-инфраструктура": "🖥",
    "1С и ERP": "📊",
    "Сеть и VPN": "🌐",
    "Оргтехника": "🖨",
    "Почта": "📧",
    "Доступ и права": "🔑",
    "Другое": "❓",
}

PRIORITY_EMOJI = {
    "Критичный": "🔴",
    "Высокий": "🟠",
    "Средний": "🟡",
    "Низкий": "🟢",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я ИИ-помощник сервис-деска «Балтийский Берег».\n"
        "Опишите проблему — найду решение в базе знаний.\n\n"
        "Или выберите частый вопрос 👇",
        reply_markup=QUICK_REPLIES,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=QUICK_REPLIES)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    user_id = str(update.effective_user.id)

    await update.message.reply_text("🔍 Ищу ответ в базе знаний...")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{API_BASE}/ask",
                json={"question": query, "source": "telegram"},
            )
            data = r.json()
        answer = data["answer"]
        escalated = data["escalated"]
        analysis_url = data.get("analysis_url", "")
        classification = data.get("classification", {})
        top_source = data.get("top_source")
    except Exception as e:
        logging.error(f"Error for user {user_id}: {e}")
        answer = "Произошла ошибка. Попробуйте позже или обратитесь к специалисту поддержки."
        escalated = True
        analysis_url = ""
        classification = {}
        top_source = None

    # Собираем HTML-сообщение
    parts = [html.escape(answer)]

    # Метрики классификации
    svc = classification.get("service", "")
    pri = classification.get("priority", "")
    svc_icon = SERVICE_EMOJI.get(svc, "❓")
    pri_icon = PRIORITY_EMOJI.get(pri, "⬜")

    # Confidence score
    score_str = ""
    if top_source and top_source.get("score"):
        score = top_source["score"]
        bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
        score_str = f" · {bar} {score:.0%}"

    if svc or pri:
        parts.append(
            f"\n<i>{svc_icon} {html.escape(svc)}  ·  {pri_icon} {html.escape(pri)}{score_str}</i>"
        )

    # Источник
    if top_source and top_source.get("title"):
        src_label = "KB" if top_source.get("source") == "kb" else "Тикет"
        parts.append(f"<i>📎 {src_label}: {html.escape(top_source['title'][:60])}</i>")

    if escalated:
        parts.append("\n⚠️ <i>Рекомендую создать заявку в сервис-деске, если ответ не помог.</i>")

    if analysis_url:
        aid = analysis_url.split("/")[-1]
        parts.append(f'\n🔍 Анализ: <code>localhost:8001/analysis/{aid}</code>')

    text = "\n".join(parts)

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=QUICK_REPLIES)
    log_dialog(user_id, query, answer, escalated)


def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
