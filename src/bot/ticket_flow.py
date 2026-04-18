"""
Флоу создания заявки — state machine через user_data.

Состояния (хранятся в context.user_data["ticket_state"]):
  "clarify"  — ждём уточнение от пользователя
  "confirm"  — ждём подтверждение (callback кнопки)

Запускается из handle_message при эскалации.
"""
import csv
import logging
import os
import uuid
from datetime import datetime

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

log = logging.getLogger("baltbereg.ticket_flow")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8001")
API_KEY = os.getenv("API_KEY")
TICKETS_FILE = "data/pending_tickets.csv"

STATE_CLARIFY = "clarify"
STATE_CONFIRM = "confirm"


# ---------- storage ----------

def _save_ticket(ticket: dict) -> None:
    os.makedirs("data", exist_ok=True)
    write_header = not os.path.exists(TICKETS_FILE)
    with open(TICKETS_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "id", "created_at", "chat_id", "service", "priority",
            "subject", "description",
        ])
        if write_header:
            w.writeheader()
        w.writerow(ticket)


# ---------- helpers ----------

async def _make_subject(question: str, clarification: str) -> str:
    combined = f"{question}. {clarification}".strip(" .")
    try:
        headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{API_BASE}/ask",
                json={
                    "question": (
                        "Сформулируй тему IT-заявки одним коротким предложением "
                        f"(до 10 слов) на основе проблемы: {combined}"
                    ),
                    "source": "internal",
                    "history": [],
                },
                headers=headers,
            )
            answer = r.json().get("answer", "").strip()
            subject = answer.split(".")[0].strip()[:120]
            # LLM иногда отвечает сигнальными токенами — это не тема
            bad = {"нужен_специалист", "не знаю", ""}
            if not subject or subject.lower() in bad or len(subject) < 5:
                return combined[:80]
            if "нужен_специалист" in subject.lower():
                return combined[:80]
            return subject
    except Exception as e:
        log.warning(f"_make_subject failed: {e}")
        return combined[:80]


def _priority_emoji(priority: str) -> str:
    return {"Критичный": "🔴", "Высокий": "🟠", "Средний": "🟡", "Низкий": "🟢"}.get(priority, "⚪")


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📨 Подтвердить", callback_data="ticket_confirm"),
        InlineKeyboardButton("✏️ Изменить", callback_data="ticket_edit"),
        InlineKeyboardButton("❌ Отмена", callback_data="ticket_cancel"),
    ]])


# ---------- public API ----------

def is_in_ticket_flow(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.user_data.get("ticket_state") in (STATE_CLARIFY, STATE_CONFIRM)


async def start_ticket_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    classification: dict,
) -> None:
    service = classification.get("service", "Другое")
    priority = classification.get("priority", "Средний")

    context.user_data["ticket"] = {
        "question": question,
        "service": service,
        "priority": priority,
    }
    context.user_data["ticket_state"] = STATE_CLARIFY

    text = (
        "📋 <b>Создаю заявку.</b> Я уже определил:\n\n"
        f"📂 Сервис: <b>{service}</b>\n"
        f"{_priority_emoji(priority)} Приоритет: <b>{priority}</b>\n\n"
        "Уточните проблему подробнее или отправьте /skip, "
        "чтобы использовать исходный запрос.\n"
        "Для отмены — /cancel"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def handle_ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Вызывается из handle_message ПЕРЕД основной логикой.
    Возвращает True если сообщение обработано в рамках флоу (нужно прервать дальнейшую обработку).
    """
    state = context.user_data.get("ticket_state")
    if not state:
        return False

    text = update.message.text.strip()

    if state == STATE_CLARIFY:
        if text.startswith("/cancel"):
            _clear_ticket(context)
            await update.message.reply_text("Создание заявки отменено.")
            return True

        clarification = text if not text.startswith("/skip") else context.user_data["ticket"]["question"]
        context.user_data["ticket"]["clarification"] = clarification
        await _show_preview(update, context)
        return True

    return False


async def handle_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Вызывается из callback_handler для обработки ticket_* кнопок.
    Возвращает True если callback обработан.
    """
    q = update.callback_query
    data = q.data

    if not data.startswith("ticket_"):
        return False

    await q.answer()

    if data == "ticket_confirm":
        ticket = context.user_data.get("ticket", {})
        question = ticket.get("question", "")
        clarification = ticket.get("clarification", question)
        full_desc = (
            f"{question}\n\nУточнение: {clarification}"
            if clarification and clarification != question
            else question
        )
        subject = ticket.get("subject") or await _make_subject(question, clarification)

        record = {
            "id": str(uuid.uuid4())[:8],
            "created_at": datetime.now().isoformat(),
            "chat_id": update.effective_chat.id,
            "service": ticket.get("service", ""),
            "priority": ticket.get("priority", ""),
            "subject": subject,
            "description": full_desc[:500],
        }
        _save_ticket(record)

        try:
            from src.bot.admin_bot import notify_admins
            import asyncio
            asyncio.create_task(notify_admins(
                analysis_id=record["id"],
                question=f"[ЗАЯВКА #{record['id']}] {subject}",
                answer=(
                    f"Сервис: {record['service']}\n"
                    f"Приоритет: {record['priority']}\n\n"
                    f"{record['description']}"
                ),
                escalated=True,
                user_chat_id=update.effective_chat.id,
                chunks=[],
            ))
        except Exception as e:
            log.warning(f"ticket notify_admins: {e}")

        _clear_ticket(context)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(
            f"✅ Заявка <b>#{record['id']}</b> создана и передана оператору.\n"
            "Вам ответят в ближайшее время. Для нового вопроса просто напишите сообщение.",
            parse_mode=ParseMode.HTML,
        )

    elif data == "ticket_edit":
        await q.edit_message_reply_markup(reply_markup=None)
        ticket = context.user_data.get("ticket", {})
        context.user_data["ticket_state"] = STATE_CLARIFY
        await q.message.reply_text(
            f"✏️ Опишите проблему заново.\n"
            f"Сервис: <b>{ticket.get('service', '—')}</b> | "
            f"Приоритет: <b>{ticket.get('priority', '—')}</b>\n"
            "Или /cancel для отмены.",
            parse_mode=ParseMode.HTML,
        )

    elif data == "ticket_cancel":
        _clear_ticket(context)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("Создание заявки отменено.")

    return True


# ---------- internals ----------

async def _show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ticket = context.user_data["ticket"]
    question = ticket.get("question", "")
    clarification = ticket.get("clarification", question)
    service = ticket.get("service", "Другое")
    priority = ticket.get("priority", "Средний")

    subject = await _make_subject(question, clarification)
    ticket["subject"] = subject
    context.user_data["ticket"] = ticket
    context.user_data["ticket_state"] = STATE_CONFIRM

    full_desc = (
        f"{question}\n\nУточнение: {clarification}"
        if clarification and clarification != question
        else question
    )

    text = (
        "✅ <b>Заявка готова — проверьте:</b>\n\n"
        f"📋 Тема: <i>{subject}</i>\n"
        f"📂 Сервис: <b>{service}</b>\n"
        f"{_priority_emoji(priority)} Приоритет: <b>{priority}</b>\n"
        f"📝 Описание:\n{full_desc[:400]}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    reply_markup=_confirm_keyboard())


def _clear_ticket(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("ticket", None)
    context.user_data.pop("ticket_state", None)
