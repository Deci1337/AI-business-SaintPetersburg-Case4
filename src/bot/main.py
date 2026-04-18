import asyncio
import csv
import html
import os
import logging
import httpx
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, ReplyKeyboardRemove
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from src.bot.logger import log_dialog
from src.bot.ticket_flow import start_ticket_flow, handle_ticket_message, handle_ticket_callback

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8001")
API_KEY = os.getenv("API_KEY")
RATINGS_FILE = "data/ratings.csv"

HISTORY_MAX_TURNS = 3
HISTORY_TTL_MINUTES = 30
DIALOG_CLOSE_TIMEOUT_MINUTES = 10  # таймаут до авто-закрытия и показа оценки

# analysis_id -> {"question": str, "answer": str, "chat_id": int}
_pending_ratings: dict = {}

# chat_id -> {"turns": [...], "last_active": datetime, "last_aid": str|None}
_dialog_history: dict[int, dict] = {}

# chat_id -> asyncio.TimerHandle — таймер авто-закрытия диалога
_close_timers: dict[int, asyncio.TimerHandle] = {}

# chat_id -> bool — ждёт ли сотрудник ответа оператора (live-chat режим)
_waiting_operator: dict[int, str] = {}  # chat_id -> analysis_id


def _get_history(chat_id: int) -> list[dict]:
    entry = _dialog_history.get(chat_id)
    if not entry:
        return []
    if datetime.now() - entry["last_active"] > timedelta(minutes=HISTORY_TTL_MINUTES):
        _dialog_history.pop(chat_id, None)
        return []
    return entry["turns"]


def _push_history(chat_id: int, user_msg: str, assistant_msg: str, aid: str | None = None) -> None:
    entry = _dialog_history.setdefault(chat_id, {"turns": [], "last_active": datetime.now(), "last_aid": None})
    entry["turns"].append({"user": user_msg, "assistant": assistant_msg})
    entry["turns"] = entry["turns"][-HISTORY_MAX_TURNS:]
    entry["last_active"] = datetime.now()
    if aid:
        entry["last_aid"] = aid


def _clear_history(chat_id: int) -> None:
    _dialog_history.pop(chat_id, None)


def _get_last_aid(chat_id: int) -> str | None:
    return _dialog_history.get(chat_id, {}).get("last_aid")


def _save_rating(analysis_id: str, score: int, question: str, answer: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(RATINGS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().isoformat(), analysis_id, score,
            question[:200], answer[:200],
        ])


def _rating_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(str(i), callback_data=f"rate_{aid}_{i}")
        for i in range(1, 6)
    ]])


def _waiting_operator_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Отмена, уже решил", callback_data=f"usercanceled_{aid}"),
    ]])


def _close_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Закрыть обращение", callback_data=f"closedialog_{aid}"),
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
    "/start — начать новый диалог\n"
    "/new — сбросить контекст и начать заново\n"
    "/help — помощь"
)


# --- Таймер закрытия диалога ---

def _cancel_close_timer(chat_id: int) -> None:
    handle = _close_timers.pop(chat_id, None)
    if handle:
        handle.cancel()


async def _report_dialog_closed(chat_id: int, escalated: bool = False) -> None:
    """Отправляет summary закрытого диалога в API для статистики."""
    turns = _dialog_history.get(chat_id, {}).get("turns", [])
    if not turns:
        return
    try:
        headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{API_BASE}/dialog-closed",
                json={
                    "history": turns,
                    "escalated": escalated,
                    "service": "Другое",
                },
                headers=headers,
            )
    except Exception as e:
        logging.warning(f"_report_dialog_closed error: {e}")


async def _auto_close_dialog(chat_id: int, bot: Bot) -> None:
    """Вызывается по таймауту — закрывает диалог и просит оценку."""
    _close_timers.pop(chat_id, None)
    aid = _get_last_aid(chat_id)

    await _report_dialog_closed(chat_id, escalated=False)
    _clear_history(chat_id)

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "💬 <i>Диалог закрыт автоматически по истечении времени неактивности.</i>\n\n"
                "Пожалуйста, оцените консультацию:"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_rating_keyboard(aid) if aid else None,
        )
    except Exception as e:
        logging.warning(f"auto_close_dialog error: {e}")


def _schedule_close_timer(chat_id: int, bot: Bot) -> None:
    """Перезапускает таймер авто-закрытия диалога."""
    _cancel_close_timer(chat_id)
    loop = asyncio.get_event_loop()
    handle = loop.call_later(
        DIALOG_CLOSE_TIMEOUT_MINUTES * 60,
        lambda: asyncio.create_task(_auto_close_dialog(chat_id, bot)),
    )
    _close_timers[chat_id] = handle


async def _close_dialog_and_ask_rating(
    chat_id: int,
    bot: Bot,
    aid: str | None,
    close_text: str,
) -> None:
    """Закрывает диалог и отправляет запрос на оценку."""
    _cancel_close_timer(chat_id)
    _clear_history(chat_id)
    p = _pending_ratings.get(aid, {}) if aid else {}
    await bot.send_message(
        chat_id=chat_id,
        text=close_text,
        parse_mode=ParseMode.HTML,
        reply_markup=_rating_keyboard(aid) if aid and p else None,
    )


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _cancel_close_timer(chat_id)
    _clear_history(chat_id)
    _waiting_operator.pop(chat_id, None)
    await update.message.reply_text(
        "Здравствуйте! Я AI-помощник сервис-деска «Балтийский Берег».\n"
        "Опишите вашу проблему — найду решение в базе знаний.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    _cancel_close_timer(chat_id)
    _clear_history(chat_id)
    _waiting_operator.pop(chat_id, None)
    await update.message.reply_text(
        "Контекст диалога сброшен. Опишите новую проблему.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Если активен флоу создания заявки — передаём управление ему
    if await handle_ticket_message(update, context):
        return

    chat_id = update.effective_chat.id
    query = update.message.text
    user_id = str(update.effective_user.id)

    # Если ждёт оператора — сообщаем что запрос уже передан
    if chat_id in _waiting_operator:
        await update.message.reply_text(
            "⏳ Ваш запрос передан оператору. Ожидайте ответа.\n\n"
            "Если вопрос уже решён — нажмите «Отмена, уже решил» выше."
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    history = _get_history(chat_id)

    try:
        headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{API_BASE}/ask",
                json={
                    "question": query,
                    "source": "telegram",
                    "history": history,
                },
                headers=headers,
            )
            data = r.json()
        answer = data["answer"]
        escalated = data["escalated"]
        irrelevant = data.get("irrelevant", False)
        wants_operator = data.get("wants_operator", False)
        classification = data.get("classification", {})
        top_source = data.get("top_source")
    except Exception as e:
        logging.error(f"Error for user {user_id}: {e}")
        answer = "Произошла ошибка. Попробуйте позже или обратитесь к специалисту поддержки."
        escalated = True
        irrelevant = False
        wants_operator = False
        classification = {}
        top_source = None
        data = {}

    # Нерелевантный запрос — просим уточнить, ничего не логируем
    if irrelevant:
        await update.message.reply_text(
            "Я помогаю только с вопросами IT-поддержки: компьютеры, программы, сеть, доступ, оргтехника.\n\n"
            "Пожалуйста, опишите вашу техническую проблему."
        )
        return

    # Слишком общий запрос — просим уточнение, не эскалируем и не создаём заявку
    if data.get("clarify"):
        await update.message.reply_text(answer)
        return

    analysis_id = data.get("analysis_id") if isinstance(data, dict) else None

    if escalated:
        # Сохраняем для последующей оценки
        if analysis_id:
            _pending_ratings[analysis_id] = {"question": query, "answer": answer, "chat_id": chat_id}

        _cancel_close_timer(chat_id)
        _clear_history(chat_id)

        if wants_operator:
            await update.message.reply_text(
                "👨‍💼 Переключаю на специалиста поддержки.\n\n"
                "<i>💬 Создаю заявку...</i>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "К сожалению, точного ответа в базе знаний не нашлось.\n\n"
                "<i>💬 Создаю заявку в поддержку...</i>",
                parse_mode=ParseMode.HTML,
            )
        await start_ticket_flow(update, context, query, classification)

    else:
        # Сохраняем пару в историю
        _push_history(chat_id, query, answer, aid=analysis_id)

        # Сохраняем для последующей оценки (после закрытия)
        if analysis_id:
            _pending_ratings[analysis_id] = {"question": query, "answer": answer, "chat_id": chat_id}

        parts = [html.escape(answer)]
        if top_source and top_source.get("title"):
            src_label = "KB" if top_source.get("source") == "kb" else ("Решение" if top_source.get("source") == "expense" else "Тикет")
            parts.append(f"\n<i>📎 {src_label}: {html.escape(top_source['title'][:60])}</i>")
        text = "\n".join(parts)

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_close_keyboard(analysis_id) if analysis_id else None,
        )

        # Запускаем таймер авто-закрытия
        _schedule_close_timer(chat_id, context.bot)

    log_dialog(user_id, query, answer, escalated)

    if escalated:
        try:
            from src.bot.admin_bot import notify_admins
            asyncio.create_task(notify_admins(
                analysis_id=data.get("analysis_id", ""),
                question=query,
                answer=answer,
                escalated=escalated,
                user_chat_id=chat_id,
                chunks=data.get("chunks", []),
                dialog_history=history,
            ))
        except Exception as e:
            logging.warning(f"notify_admins error: {e}")


async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cb = update.callback_query

    # Ticket flow callbacks
    if cb.data.startswith("ticket_"):
        await handle_ticket_callback(update, context)
        return

    # Закрыть обращение вручную (кнопка под ответом бота)
    if cb.data.startswith("closedialog_"):
        await cb.answer()
        _, aid = cb.data.split("_", 1)
        chat_id = cb.message.chat_id
        _cancel_close_timer(chat_id)
        await _report_dialog_closed(chat_id, escalated=False)
        _clear_history(chat_id)
        await cb.edit_message_reply_markup(reply_markup=None)
        p = _pending_ratings.get(aid, {})
        await cb.message.reply_text(
            "✅ Обращение закрыто.\n\nПожалуйста, оцените консультацию:",
            reply_markup=_rating_keyboard(aid) if p else None,
        )
        return

    # Пользователь отменил ожидание оператора
    if cb.data.startswith("usercanceled_"):
        await cb.answer()
        _, aid = cb.data.split("_", 1)
        chat_id = cb.message.chat_id
        _waiting_operator.pop(chat_id, None)
        await _report_dialog_closed(chat_id, escalated=True)
        _clear_history(chat_id)
        await cb.edit_message_reply_markup(reply_markup=None)

        # Уведомляем admin_bot об отмене
        try:
            from src.bot.admin_bot import user_canceled_request
            asyncio.create_task(user_canceled_request(aid))
        except Exception as e:
            logging.warning(f"user_canceled_request error: {e}")

        # Показываем оценку
        p = _pending_ratings.get(aid, {})
        await cb.message.reply_text(
            "✅ Запрос закрыт как решённый.\n\n"
            "Пожалуйста, оцените консультацию:",
            reply_markup=_rating_keyboard(aid) if p else None,
        )
        return

    # Оценка
    if cb.data.startswith("rate_"):
        await cb.answer()
        parts = cb.data.split("_", 2)
        if len(parts) != 3:
            return
        _, aid, score_str = parts
        score = int(score_str)
        p = _pending_ratings.get(aid, {})
        _save_rating(aid, score, p.get("question", ""), p.get("answer", ""))
        _pending_ratings.pop(aid, None)

        thanks = {
            5: "Отлично! Рад помочь.",
            4: "Хорошо, учтём.",
            3: "Понятно, постараемся лучше.",
            2: "Спасибо, исправим.",
            1: "Учтём, передадим в анализ.",
        }
        await cb.edit_message_reply_markup(reply_markup=None)
        await cb.message.reply_text(
            f"Оценка: {score}/5 — {thanks.get(score, 'Спасибо!')}\n\n"
            "<i>Для нового обращения просто напишите сообщение.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    await cb.answer()


def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(rating_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logging.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
