import asyncio
import html
import os
import logging
import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from src.bot.logger import log_dialog

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8001")

HELP_TEXT = (
    "Я AI-помощник сервис-деска «Балтийский Берег».\n\n"
    "Опишите проблему своими словами, и я найду решение в базе знаний.\n\n"
    "Примеры:\n"
    "• Не подключается удалённый доступ (VPN)\n"
    "• Ошибка в 1С при формировании отчёта\n"
    "• Не работает принтер\n"
    "• Нет доступа к папке на сервере\n\n"
    "Команды:\n"
    "/start — начать\n"
    "/new — новый вопрос\n"
    "/help — помощь"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! Я AI-помощник сервис-деска «Балтийский Берег».\n"
        "Опишите вашу проблему — найду решение в базе знаний."
    )


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Хорошо, начнём сначала. Опишите вашу проблему.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    user_id = str(update.effective_user.id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{API_BASE}/ask",
                json={"question": query, "source": "telegram"},
            )
            data = r.json()
        answer = data["answer"]
        escalated = data["escalated"]
        classification = data.get("classification", {})
        top_source = data.get("top_source")
    except Exception as e:
        logging.error(f"Error for user {user_id}: {e}")
        answer = "Произошла ошибка. Попробуйте позже или обратитесь к специалисту поддержки."
        escalated = True
        classification = {}
        top_source = None
        data = {}

    parts = [html.escape(answer)]

    if top_source and top_source.get("title"):
        src_label = "KB" if top_source.get("source") == "kb" else ("Решение" if top_source.get("source") == "expense" else "Тикет")
        parts.append(f"\n<i>📎 {src_label}: {html.escape(top_source['title'][:60])}</i>")

    if escalated:
        parts.append("\n⚠️ <i>Не нашёл точного решения. Рекомендую обратиться к специалисту поддержки или создать заявку.</i>")

    analysis_id = data.get("analysis_id") if isinstance(data, dict) else None
    if analysis_id:
        parts.append(f'\n🔍 <code>localhost:8001/analysis/{analysis_id}</code>')

    text = "\n".join(parts)

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    log_dialog(user_id, query, answer, escalated)


def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
