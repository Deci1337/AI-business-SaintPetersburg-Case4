import asyncio
import csv
import html
import os
import logging
import httpx
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from src.bot.logger import log_dialog

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8001")
API_KEY = os.getenv("API_KEY")
RATINGS_FILE = "data/ratings.csv"

# analysis_id -> {"question": str, "answer": str}
_pending_ratings: dict = {}


def _save_rating(analysis_id: str, score: int, question: str, answer: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(RATINGS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().isoformat(), analysis_id, score,
            question[:200], answer[:200],
        ])


def _rating_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{'⭐' * i}", callback_data=f"rate_{aid}_{i}")
        for i in range(1, 6)
    ]])

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
        headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{API_BASE}/ask",
                json={"question": query, "source": "telegram"},
                headers=headers,
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

    analysis_id = data.get("analysis_id") if isinstance(data, dict) else None

    if escalated:
        text = "Ваш запрос передан специалисту поддержки. Ожидайте ответа."
    else:
        parts = [html.escape(answer)]
        if top_source and top_source.get("title"):
            src_label = "KB" if top_source.get("source") == "kb" else ("Решение" if top_source.get("source") == "expense" else "Тикет")
            parts.append(f"\n<i>📎 {src_label}: {html.escape(top_source['title'][:60])}</i>")
        text = "\n".join(parts)

    if escalated:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        # Показываем кнопки оценки только для автоответов
        _pending_ratings[analysis_id] = {"question": query, "answer": answer}
        await update.message.reply_text(
            text + "\n\n<i>Оцените ответ — это помогает улучшить систему:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=_rating_keyboard(analysis_id) if analysis_id else None,
        )
    log_dialog(user_id, query, answer, escalated)

    try:
        from src.bot.admin_bot import notify_admins
        asyncio.create_task(notify_admins(
            analysis_id=data.get("analysis_id", ""),
            question=query,
            answer=answer,
            escalated=escalated,
            user_chat_id=update.effective_chat.id,
        ))
    except Exception as e:
        logging.warning(f"notify_admins error: {e}")


async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_", 2)
    if len(parts) != 3:
        return
    _, aid, score_str = parts
    score = int(score_str)
    p = _pending_ratings.get(aid, {})
    _save_rating(aid, score, p.get("question", ""), p.get("answer", ""))
    _pending_ratings.pop(aid, None)

    stars = "⭐" * score + "☆" * (5 - score)
    thanks = {5: "Отлично! Спасибо.", 4: "Хорошо, учтём.", 3: "Понятно, постараемся лучше.", 2: "Спасибо, исправим.", 1: "Учтём, передадим в анализ."}
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text(f"{stars} {thanks.get(score, 'Спасибо!')}")


def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern=r"^rate_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
