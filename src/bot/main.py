import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from src.rag.llm import ask

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помощник сервис-деска «Балтийский Берег».\n"
        "Опишите вашу проблему или задайте вопрос."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    await update.message.reply_text("Ищу ответ...")
    try:
        answer = ask(query)
    except Exception as e:
        logging.error(f"Error: {e}")
        answer = "Произошла ошибка. Попробуйте позже или обратитесь к специалисту."
    await update.message.reply_text(answer)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
