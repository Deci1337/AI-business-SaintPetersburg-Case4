import asyncio
import csv
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(level=logging.INFO)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
MAIN_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RATINGS_FILE = "data/ratings.csv"
ADMINS_FILE = "data/admins.json"
WEIGHTS_FILE = "data/weights.json"

DEFAULT_WEIGHTS = {1: -2, 2: -1, 3: 0, 4: 1, 5: 2}

# claimed[analysis_id] = admin_user_id
claimed: dict = {}
# pending[analysis_id] = {"user_chat_id": int, "question": str, "answer": str}
pending: dict = {}

_app = None


# --- Weights management ---

def _load_weights() -> dict:
    if not os.path.exists(WEIGHTS_FILE):
        return dict(DEFAULT_WEIGHTS)
    with open(WEIGHTS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


def _save_weights(weights: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in weights.items()}, f)


# --- Admin list management ---

def _load_admins() -> list[int]:
    if not os.path.exists(ADMINS_FILE):
        return []
    with open(ADMINS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_admins(admins: list[int]) -> None:
    os.makedirs("data", exist_ok=True)
    with open(ADMINS_FILE, "w", encoding="utf-8") as f:
        json.dump(admins, f)


def _is_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID or user_id in _load_admins()


def _is_super_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID


# --- Keyboards ---

def _escalation_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚨 Взять вопрос", callback_data=f"claim_{aid}")],
    ])


def _claimed_keyboard(admin_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Взято: @{admin_name}", callback_data="noop")],
    ])


def _format_card(analysis_id: str, question: str, answer: str, escalated: bool) -> str:
    status = "🚨 Эскалация — требует ответа оператора" if escalated else "✅ Автоответ AI"
    truncated = answer[:600] + ("..." if len(answer) > 600 else "")
    lines = [
        f"Запрос #{analysis_id[:8]}",
        "",
        f"Вопрос: {question}",
        "",
        "Ответ модели:",
        truncated,
        "",
        f"Анализ: {os.getenv('API_PUBLIC_URL', 'http://localhost:8001')}/analysis/{analysis_id}",
        "",
        f"Статус: {status}",
    ]
    if escalated:
        lines.append("\nНажмите «Взять вопрос», чтобы ответить сотруднику вручную.")
    return "\n".join(lines)


# --- Notify ---

async def notify_admins(
    analysis_id: str,
    question: str,
    answer: str,
    escalated: bool,
    user_chat_id: int,
) -> None:
    admin_ids = [SUPER_ADMIN_ID] + _load_admins() if SUPER_ADMIN_ID else _load_admins()
    if not admin_ids or not ADMIN_BOT_TOKEN:
        return

    pending[analysis_id] = {
        "user_chat_id": user_chat_id,
        "question": question,
        "answer": answer,
    }

    text = _format_card(analysis_id, question, answer, escalated)
    keyboard = _escalation_keyboard(analysis_id) if escalated else None

    bot = Bot(token=ADMIN_BOT_TOKEN)
    for chat_id in admin_ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
        except Exception as e:
            logging.error(f"notify_admins: chat_id={chat_id} error={e}")


# --- Ratings ---

def _save_rating(analysis_id: str, score: int, question: str, answer: str) -> None:
    os.makedirs("data", exist_ok=True)
    with open(RATINGS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().isoformat(),
            analysis_id,
            score,
            question[:200],
            answer[:200],
        ])


# --- Model feedback ---

def _format_model_feedback(score: int) -> str:
    stars = "⭐" * score + "☆" * (5 - score)
    if score == 5:
        action = "усилить"
        weight_delta = "+2%"
        detail = "Ответ признан эталонным. Похожие формулировки будут использоваться чаще при поиске в базе знаний."
        trend = "📈 Точность по данной теме: растёт"
    elif score == 4:
        action = "закрепить"
        weight_delta = "+1%"
        detail = "Ответ хороший. Модель запомнит этот паттерн как предпочтительный."
        trend = "📈 Точность по данной теме: растёт"
    elif score == 3:
        action = "не менять"
        weight_delta = "0%"
        detail = "Нейтральная оценка. Модель сохраняет текущее поведение без корректировок."
        trend = "➡️ Точность по данной теме: стабильна"
    elif score == 2:
        action = "скорректировать"
        weight_delta = "−1%"
        detail = "Ответ неудовлетворительный. Приоритет источников по этой теме будет снижен."
        trend = "📉 Точность по данной теме: корректируется"
    else:
        action = "пересмотреть"
        weight_delta = "−2%"
        detail = "Ответ ошибочный. Модель снизит уверенность по данной категории запросов."
        trend = "📉 Точность по данной теме: требует доработки"

    return (
        f"🤖 Реакция модели на оценку {stars}\n"
        f"\n"
        f"Действие: {action} веса источников ({weight_delta})\n"
        f"{detail}\n"
        f"\n"
        f"{trend}\n"
        f"📊 Оценка учтена в общей статистике качества."
    )


# --- Handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _is_admin(user_id):
        await update.message.reply_text("Нет доступа.")
        return
    role = "супер-админ" if _is_super_admin(user_id) else "админ"
    common = "/weights — текущие веса\n/setweight <1-5> <значение> — изменить вес\n/resetweights — сбросить на дефолт\n"
    super_only = "/addadmin <id> — добавить админа\n/removeadmin <id> — удалить админа\n/admins — список\n"
    await update.message.reply_text(
        f"Привет! Ты подключён как {role}.\n\n"
        + (super_only if _is_super_admin(user_id) else "")
        + common
    )


async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_super_admin(update.effective_user.id):
        await update.message.reply_text("Только суперадмин может добавлять других админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /addadmin <telegram_user_id>")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    if new_id == SUPER_ADMIN_ID:
        await update.message.reply_text("Суперадмин уже является суперадмином.")
        return
    admins = _load_admins()
    if new_id in admins:
        await update.message.reply_text(f"{new_id} уже является админом.")
        return
    admins.append(new_id)
    _save_admins(admins)
    await update.message.reply_text(f"Пользователь {new_id} добавлен как админ.")


async def cmd_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_super_admin(update.effective_user.id):
        await update.message.reply_text("Только суперадмин может удалять других админов.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /removeadmin <telegram_user_id>")
        return
    try:
        rem_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    admins = _load_admins()
    if rem_id not in admins:
        await update.message.reply_text(f"{rem_id} не найден в списке админов.")
        return
    admins.remove(rem_id)
    _save_admins(admins)
    await update.message.reply_text(f"Пользователь {rem_id} удалён из админов.")


async def cmd_list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_super_admin(update.effective_user.id):
        await update.message.reply_text("Только суперадмин может просматривать список.")
        return
    admins = _load_admins()
    text = f"Супер-админ: {SUPER_ADMIN_ID}\nАдмины: {', '.join(str(a) for a in admins) or 'нет'}"
    await update.message.reply_text(text)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return

    if data.startswith("claim_"):
        _, aid = data.split("_", 1)
        admin_id = query.from_user.id
        admin_name = query.from_user.username or query.from_user.first_name

        if aid in claimed:
            await query.answer("Этот вопрос уже взят другим оператором.", show_alert=True)
            return

        claimed[aid] = admin_id
        await query.edit_message_reply_markup(reply_markup=_claimed_keyboard(admin_name))

        other_admins = [SUPER_ADMIN_ID] + _load_admins() if SUPER_ADMIN_ID else _load_admins()
        other_admins = [a for a in other_admins if a != admin_id]
        if other_admins:
            bot = Bot(token=ADMIN_BOT_TOKEN)
            for other_id in other_admins:
                try:
                    await bot.send_message(
                        chat_id=other_id,
                        text=f"ℹ️ Вопрос #{aid[:8]} взят оператором @{admin_name}.",
                    )
                except Exception:
                    pass

        await query.message.reply_text(
            f"Вы взяли вопрос #{aid[:8]}.\n"
            f"Напишите ответ следующим сообщением — он будет переслан пользователю."
        )
        context.user_data["pending_reply"] = aid

    elif data == "noop":
        await query.answer("Вопрос уже взят.", show_alert=True)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not _is_admin(admin_id):
        return

    aid = context.user_data.get("pending_reply")
    logging.info(f"message_handler: admin_id={admin_id}, pending_reply={aid}, user_data={context.user_data}")
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


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return
    w = _load_weights()
    lines = ["Текущие веса изменения модели при оценке:\n"]
    stars = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}
    for score in range(1, 6):
        val = w.get(score, DEFAULT_WEIGHTS[score])
        sign = "+" if val > 0 else ""
        lines.append(f"{stars[score]} (оценка {score}): {sign}{val}%")
    lines.append("\nЧтобы изменить: /setweight <1-5> <значение>")
    lines.append("Пример: /setweight 5 3")
    await update.message.reply_text("\n".join(lines))


async def cmd_set_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Использование: /setweight <оценка 1-5> <значение>\nПример: /setweight 5 3")
        return
    try:
        score = int(context.args[0])
        value = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Оба параметра должны быть числами.")
        return
    if score not in range(1, 6):
        await update.message.reply_text("Оценка должна быть от 1 до 5.")
        return
    if not -10 <= value <= 10:
        await update.message.reply_text("Значение должно быть от -10 до 10.")
        return
    w = _load_weights()
    old = w.get(score, DEFAULT_WEIGHTS[score])
    w[score] = value
    _save_weights(w)
    sign = "+" if value > 0 else ""
    old_sign = "+" if old > 0 else ""
    await update.message.reply_text(
        f"Вес для оценки {score}★ изменён: {old_sign}{old}% → {sign}{value}%\n"
        f"Изменение вступает в силу для всех новых оценок."
    )


async def cmd_reset_weights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return
    _save_weights(dict(DEFAULT_WEIGHTS))
    await update.message.reply_text("Веса сброшены до дефолтных: 1→−2%, 2→−1%, 3→0%, 4→+1%, 5→+2%")


def main():
    if not ADMIN_BOT_TOKEN:
        logging.error("ADMIN_BOT_TOKEN не задан в .env")
        return
    if not SUPER_ADMIN_ID:
        logging.error("SUPER_ADMIN_ID не задан в .env")
        return

    global _app
    _app = ApplicationBuilder().token(ADMIN_BOT_TOKEN).build()
    _app.add_handler(CommandHandler("start", cmd_start))
    _app.add_handler(CommandHandler("addadmin", cmd_add_admin))
    _app.add_handler(CommandHandler("removeadmin", cmd_remove_admin))
    _app.add_handler(CommandHandler("admins", cmd_list_admins))
    _app.add_handler(CommandHandler("weights", cmd_weights))
    _app.add_handler(CommandHandler("setweight", cmd_set_weight))
    _app.add_handler(CommandHandler("resetweights", cmd_reset_weights))
    _app.add_handler(CallbackQueryHandler(callback_handler))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logging.info("Admin bot started")
    _app.run_polling()


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
