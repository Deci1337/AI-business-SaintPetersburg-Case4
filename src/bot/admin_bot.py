import asyncio
import csv
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(level=logging.INFO)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_CHAT_IDS = [int(x) for x in os.getenv("ADMIN_CHAT_ID", "").split(",") if x.strip()]
MAIN_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RATINGS_FILE = "data/ratings.csv"

# claimed[analysis_id] = admin_user_id
claimed: dict = {}
# pending[analysis_id] = {"user_chat_id": int, "question": str, "answer": str}
pending: dict = {}

_app = None


def _ratings_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(str(i), callback_data=f"rate_{aid}_{i}") for i in range(1, 6)
    ]])


def _escalation_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(str(i), callback_data=f"rate_{aid}_{i}") for i in range(1, 6)],
        [InlineKeyboardButton("Взять вопрос", callback_data=f"claim_{aid}")],
    ])


def _format_card(analysis_id: str, question: str, answer: str, escalated: bool) -> str:
    status = "Эскалация — требует ответа оператора" if escalated else "Автоответ"
    lines = [
        f"Запрос #{analysis_id[:8]}",
        "",
        f"Вопрос: {question}",
        "",
        f"Ответ модели: {answer[:600]}{'...' if len(answer) > 600 else ''}",
        "",
        f"Статус: {status}",
        "",
        "Оцените ответ модели (1 — плохо, 5 — отлично):",
    ]
    return "\n".join(lines)


async def notify_admins(
    analysis_id: str,
    question: str,
    answer: str,
    escalated: bool,
    user_chat_id: int,
) -> None:
    if not ADMIN_CHAT_IDS or not ADMIN_BOT_TOKEN:
        return

    pending[analysis_id] = {
        "user_chat_id": user_chat_id,
        "question": question,
        "answer": answer,
    }

    text = _format_card(analysis_id, question, answer, escalated)
    keyboard = _escalation_keyboard(analysis_id) if escalated else _ratings_keyboard(analysis_id)

    bot = Bot(token=ADMIN_BOT_TOKEN)
    for chat_id in ADMIN_CHAT_IDS:
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        except Exception as e:
            logging.error(f"notify_admins: chat_id={chat_id} error={e}")


def _save_rating(analysis_id: str, score: int, question: str, answer: str) -> None:
    with open(RATINGS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().isoformat(),
            analysis_id,
            score,
            question[:200],
            answer[:200],
        ])


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("rate_"):
        _, aid, score_str = data.split("_", 2)
        score = int(score_str)
        p = pending.get(aid, {})
        _save_rating(aid, score, p.get("question", ""), p.get("answer", ""))
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"Оценка {score}/5 сохранена. Спасибо!")

    elif data.startswith("claim_"):
        _, aid = data.split("_", 1)
        admin_id = query.from_user.id
        admin_name = query.from_user.username or query.from_user.first_name

        if aid in claimed:
            await query.answer("Этот вопрос уже взят другим оператором.", show_alert=True)
            return

        claimed[aid] = admin_id
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(str(i), callback_data=f"rate_{aid}_{i}") for i in range(1, 6)
            ], [
                InlineKeyboardButton(f"Взято: @{admin_name}", callback_data="noop"),
            ]])
        )
        await query.message.reply_text(
            f"Вы взяли вопрос #{aid[:8]}.\n"
            f"Напишите ответ следующим сообщением — он будет переслан пользователю."
        )
        context.user_data["pending_reply"] = aid

    elif data == "noop":
        await query.answer("Вопрос уже взят.", show_alert=True)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    aid = context.user_data.get("pending_reply")

    if not aid:
        return

    p = pending.get(aid)
    if not p:
        await update.message.reply_text("Не найден исходный запрос. Возможно, устарел.")
        return

    if claimed.get(aid) != admin_id:
        await update.message.reply_text("Этот вопрос взят другим оператором.")
        return

    reply_text = update.message.text
    try:
        main_bot = Bot(token=MAIN_BOT_TOKEN)
        await main_bot.send_message(
            chat_id=p["user_chat_id"],
            text=f"Ответ оператора поддержки:\n\n{reply_text}",
        )
        await update.message.reply_text("Ответ отправлен пользователю.")
    except Exception as e:
        logging.error(f"forward reply error: {e}")
        await update.message.reply_text(f"Ошибка при отправке: {e}")

    context.user_data.pop("pending_reply", None)
    claimed.pop(aid, None)


def main():
    if not ADMIN_BOT_TOKEN:
        logging.error("ADMIN_BOT_TOKEN не задан в .env")
        return
    if not ADMIN_CHAT_IDS:
        logging.error("ADMIN_CHAT_ID не задан в .env")
        return

    global _app
    _app = ApplicationBuilder().token(ADMIN_BOT_TOKEN).build()
    _app.add_handler(CallbackQueryHandler(callback_handler))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logging.info("Admin bot started")
    _app.run_polling()


if __name__ == "__main__":
    main()
