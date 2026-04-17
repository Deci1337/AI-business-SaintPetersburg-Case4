import os
import logging
import httpx
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from src.bot.logger import log_dialog

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

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
        async with httpx.AsyncClient(timeout=35) as client:
            r = await client.post(
                f"{API_BASE}/ask",
                json={"question": query, "source": "telegram"},
            )
            data = r.json()
        answer = data["answer"]
        escalated = data["escalated"]
        analysis_url = data.get("analysis_url", "")
    except Exception as e:
        logging.error(f"Error for user {user_id}: {e}")
        answer = "Произошла ошибка. Попробуйте позже или обратитесь к специалисту поддержки."
        escalated = True
        analysis_url = ""

    if escalated:
        answer += "\n\n⚠️ Если ответ не помог — создайте заявку в сервис-деске."

    if analysis_url:
        answer += f"\n\n🔍 [Подробный анализ]({analysis_url})"

    await update.message.reply_text(answer, parse_mode="Markdown")
    log_dialog(user_id, query, answer, escalated)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
