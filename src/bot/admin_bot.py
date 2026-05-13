import asyncio
import json
import logging
import os
from datetime import datetime
import httpx

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(level=logging.INFO)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
MAIN_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8001")
API_KEY = os.getenv("API_KEY")
ADMINS_FILE = "data/admins.json"
WEIGHTS_FILE = "data/weights.json"
ESCALATIONS_FILE = "data/escalations.json"

DEFAULT_WEIGHTS = {1: -2, 2: -1, 3: 0, 4: 1, 5: 2}

# Shared state через JSON-файл (user-bot и admin-bot — разные процессы)
# {
#   "pending": {aid: {"user_chat_id", "question", "answer", "history", "idea"}},
#   "claimed": {aid: admin_id},
#   "messages": {aid: {admin_chat_id: message_id}}
# }


def _load_state() -> dict:
    if not os.path.exists(ESCALATIONS_FILE):
        return {"pending": {}, "claimed": {}, "messages": {}}
    try:
        with open(ESCALATIONS_FILE, encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("pending", {})
        s.setdefault("claimed", {})
        s.setdefault("messages", {})
        return s
    except Exception:
        return {"pending": {}, "claimed": {}, "messages": {}}


def _save_state(state: dict) -> None:
    os.makedirs("data", exist_ok=True)
    tmp = ESCALATIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, ESCALATIONS_FILE)


def _get_pending(aid: str) -> dict | None:
    return _load_state()["pending"].get(aid)


def _set_pending(aid: str, data: dict) -> None:
    s = _load_state()
    s["pending"][aid] = data
    _save_state(s)


def _pop_pending(aid: str) -> None:
    s = _load_state()
    s["pending"].pop(aid, None)
    s["claimed"].pop(aid, None)
    s["messages"].pop(aid, None)
    _save_state(s)


def _get_claimed(aid: str) -> int | None:
    v = _load_state()["claimed"].get(aid)
    return int(v) if v is not None else None


def _set_claimed(aid: str, admin_id: int) -> None:
    s = _load_state()
    s["claimed"][aid] = admin_id
    _save_state(s)


def _get_messages(aid: str) -> dict[int, int]:
    m = _load_state()["messages"].get(aid, {})
    return {int(k): int(v) for k, v in m.items()}


def _set_message(aid: str, admin_chat_id: int, message_id: int) -> None:
    s = _load_state()
    s["messages"].setdefault(aid, {})[str(admin_chat_id)] = message_id
    _save_state(s)

_app = None


# --- Weights management ---

def _load_weights() -> dict:
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    try:
        r = httpx.get(f"{API_BASE}/weights", headers=headers, timeout=10)
        if r.is_success:
            return {int(k): v for k, v in r.json().items()}
    except Exception:
        pass
    if not os.path.exists(WEIGHTS_FILE):
        return dict(DEFAULT_WEIGHTS)
    with open(WEIGHTS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v for k, v in raw.items()}


def _save_weights(weights: dict) -> None:
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    try:
        r = httpx.put(
            f"{API_BASE}/weights",
            json={"weights": {str(k): v for k, v in weights.items()}},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        return
    except Exception:
        pass
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


def _all_admin_ids() -> list[int]:
    admins = _load_admins()
    if SUPER_ADMIN_ID and SUPER_ADMIN_ID not in admins:
        return [SUPER_ADMIN_ID] + admins
    return admins


# --- Keyboards ---

def _escalation_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 История диалога", callback_data=f"history_{aid}")],
        [InlineKeyboardButton("🚨 Взять диалог", callback_data=f"claim_{aid}")],
    ])


def _claimed_keyboard(aid: str, admin_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Взято: @{admin_name}", callback_data="noop")],
    ])


def _active_chat_keyboard(aid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Закрыть запрос", callback_data=f"closeticket_{aid}")],
    ])


def _canceled_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отменён сотрудником", callback_data="noop")],
    ])


# --- Format helpers ---

def _format_similar_solutions(chunks: list) -> str:
    expense_chunks = [c for c in chunks if c.get("source") == "expense"][:3]
    if not expense_chunks:
        return ""
    lines = ["", "💡 Похожие решения из базы:"]
    for i, c in enumerate(expense_chunks, 1):
        title = c.get("title", "—")[:50]
        text = c.get("text", "")[:150].replace("\n", " ").strip()
        score = c.get("score", 0)
        lines.append(f"  {i}. [{round(score*100)}%] {title}")
        if text:
            lines.append(f"     {text}...")
    return "\n".join(lines)


def _format_card(
    analysis_id: str,
    question: str,
    answer: str,
    chunks: list | None = None,
    idea: str | None = None,
    dialog_history: list[dict] | None = None,
) -> str:
    truncated = answer[:600] + ("..." if len(answer) > 600 else "")
    lines = [
        f"🚨 Эскалация — запрос #{analysis_id[:8]}",
        "",
        f"Вопрос: {question}",
    ]
    if idea:
        lines += ["", f"💡 Суть проблемы (AI): {idea}"]
    lines += [
        "",
        "Последний ответ AI:",
        truncated,
        "",
        f"Анализ: {os.getenv('API_PUBLIC_URL', 'http://localhost:8001')}/analysis/{analysis_id}",
    ]
    if dialog_history:
        lines.append("")
        lines.append(f"📋 История диалога ({len(dialog_history)} сообщ.) — кнопка ниже")
    if chunks:
        similar = _format_similar_solutions(chunks)
        if similar:
            lines.append(similar)
    lines.append("\nНажмите «Взять диалог», чтобы ответить сотруднику.")
    return "\n".join(lines)


async def _make_idea(question: str, history: list[dict] | None = None) -> str:
    """Генерирует краткую суть проблемы через API /ask (internal режим)."""
    import httpx
    api_base = os.getenv("API_BASE_URL", "http://api:8001")
    api_key = os.getenv("API_KEY")
    dialog = ""
    if history:
        dialog = "\n".join(f"Сотрудник: {t.get('user','')}" for t in history[-3:])
    prompt = (
        "Опиши одним коротким предложением (до 20 слов) суть IT-проблемы сотрудника. "
        "Только суть, без шаблонных фраз типа 'проблема заключается в'.\n\n"
        + (f"Ранее в диалоге:\n{dialog}\n\n" if dialog else "")
        + f"Итоговый вопрос: {question}"
    )
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{api_base}/ask",
                json={"question": prompt, "source": "internal", "history": []},
                headers=headers,
            )
            ans = r.json().get("answer", "").strip()
            # Берём только первую строку, макс 200 символов
            return ans.split("\n")[0][:200] if ans else ""
    except Exception as e:
        logging.warning(f"_make_idea error: {e}")
        return ""


# --- Notify ---

async def notify_admins(
    analysis_id: str,
    question: str,
    answer: str,
    escalated: bool,
    user_chat_id: int,
    chunks: list | None = None,
    dialog_history: list[dict] | None = None,
) -> None:
    admin_ids = _all_admin_ids()
    if not admin_ids or not ADMIN_BOT_TOKEN:
        return

    idea = await _make_idea(question, dialog_history)

    _set_pending(analysis_id, {
        "user_chat_id": user_chat_id,
        "question": question,
        "answer": answer,
        "history": dialog_history or [],
        "idea": idea,
    })

    text = _format_card(analysis_id, question, answer, chunks or [], idea=idea, dialog_history=dialog_history)
    keyboard = _escalation_keyboard(analysis_id) if escalated else None

    bot = Bot(token=ADMIN_BOT_TOKEN)
    for chat_id in admin_ids:
        try:
            msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
            _set_message(analysis_id, chat_id, msg.message_id)
        except Exception as e:
            logging.error(f"notify_admins: chat_id={chat_id} error={e}")


async def user_canceled_request(analysis_id: str) -> None:
    """Уведомляет всех админов, что сотрудник сам решил вопрос."""
    if not ADMIN_BOT_TOKEN:
        return

    bot = Bot(token=ADMIN_BOT_TOKEN)
    admin_ids = _all_admin_ids()
    msg_map = _get_messages(analysis_id)

    for chat_id in admin_ids:
        msg_id = msg_map.get(chat_id)
        try:
            if msg_id:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=_canceled_keyboard(),
                )
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Запрос #{analysis_id[:8]} отменён сотрудником — вопрос решён самостоятельно.",
            )
        except Exception as e:
            logging.warning(f"user_canceled_request: chat_id={chat_id} error={e}")

    _pop_pending(analysis_id)


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
        action, weight_delta = "усилить", "+2%"
        detail = "Ответ признан эталонным. Похожие формулировки будут использоваться чаще при поиске в базе знаний."
        trend = "📈 Точность по данной теме: растёт"
    elif score == 4:
        action, weight_delta = "закрепить", "+1%"
        detail = "Ответ хороший. Модель запомнит этот паттерн как предпочтительный."
        trend = "📈 Точность по данной теме: растёт"
    elif score == 3:
        action, weight_delta = "не менять", "0%"
        detail = "Нейтральная оценка. Модель сохраняет текущее поведение без корректировок."
        trend = "➡️ Точность по данной теме: стабильна"
    elif score == 2:
        action, weight_delta = "скорректировать", "−1%"
        detail = "Ответ неудовлетворительный. Приоритет источников по этой теме будет снижен."
        trend = "📉 Точность по данной теме: корректируется"
    else:
        action, weight_delta = "пересмотреть", "−2%"
        detail = "Ответ ошибочный. Модель снизит уверенность по данной категории запросов."
        trend = "📉 Точность по данной теме: требует доработки"

    return (
        f"🤖 Реакция модели на оценку {stars}\n\n"
        f"Действие: {action} веса источников ({weight_delta})\n"
        f"{detail}\n\n"
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

    if data == "noop":
        return

    # Показать историю диалога
    if data.startswith("history_"):
        _, aid = data.split("_", 1)
        p = _get_pending(aid)
        if not p:
            await query.answer("История недоступна.", show_alert=True)
            return
        history = p.get("history", [])
        lines = [f"📋 История диалога #{aid[:8]}:", ""]
        if history:
            for turn in history:
                lines.append(f"👤 Сотрудник: {turn.get('user', '')}")
                lines.append(f"🤖 AI: {turn.get('assistant', '')[:300]}")
                lines.append("")
        lines.append(f"👤 Итоговый вопрос: {p.get('question', '')}")
        await query.message.reply_text("\n".join(lines)[:4000])
        return

    # Взять диалог
    if data.startswith("claim_"):
        _, aid = data.split("_", 1)
        admin_id = query.from_user.id
        admin_name = query.from_user.username or query.from_user.first_name

        if _get_claimed(aid) is not None:
            await query.answer("Этот запрос уже взят другим оператором.", show_alert=True)
            return

        _set_claimed(aid, admin_id)
        context.user_data["active_aid"] = aid

        # Обновляем кнопку у всех админов
        bot = Bot(token=ADMIN_BOT_TOKEN)
        msg_map = _get_messages(aid)
        for chat_id, msg_id in msg_map.items():
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=_claimed_keyboard(aid, admin_name),
                )
            except Exception:
                pass

        # Уведомляем других
        other_admins = [a for a in _all_admin_ids() if a != admin_id]
        for other_id in other_admins:
            try:
                await bot.send_message(
                    chat_id=other_id,
                    text=f"ℹ️ Запрос #{aid[:8]} взят оператором @{admin_name}.",
                )
            except Exception:
                pass

        # Уведомляем сотрудника
        p = _get_pending(aid) or {}
        user_chat_id = p.get("user_chat_id")
        if user_chat_id:
            main_bot = Bot(token=MAIN_BOT_TOKEN)
            try:
                await main_bot.send_message(
                    chat_id=user_chat_id,
                    text="👨‍💼 Оператор подключился к вашему запросу. Ожидайте ответа.\n\nЕсли вопрос уже решён:",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Отмена, уже решил", callback_data=f"usercanceled_{aid}"),
                    ]]),
                )
            except Exception as e:
                logging.warning(f"notify user about claim: {e}")

        await query.message.reply_text(
            f"✅ Вы взяли запрос #{aid[:8]}.\n"
            "Пишите сообщения — они будут пересылаться сотруднику.\n"
            "Когда закончите — нажмите «Закрыть запрос».",
            reply_markup=_active_chat_keyboard(aid),
        )
        return

    # Закрыть запрос
    if data.startswith("closeticket_"):
        _, aid = data.split("_", 1)
        admin_id = query.from_user.id

        if _get_claimed(aid) != admin_id:
            await query.answer("Вы не ведёте этот запрос.", show_alert=True)
            return

        p = _get_pending(aid) or {}
        user_chat_id = p.get("user_chat_id")

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"✅ Запрос #{aid[:8]} закрыт.")
        context.user_data.pop("active_aid", None)

        # Закрываем у сотрудника и просим оценку
        if user_chat_id:
            main_bot = Bot(token=MAIN_BOT_TOKEN)
            try:
                # Инлайн-клавиатура оценки (aid тот же)
                rating_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(str(i), callback_data=f"rate_{aid}_{i}")
                    for i in range(1, 6)
                ]])
                await main_bot.send_message(
                    chat_id=user_chat_id,
                    text="✅ Оператор закрыл ваш запрос.\n\nПожалуйста, оцените консультацию:",
                    reply_markup=rating_kb,
                )
            except Exception as e:
                logging.warning(f"close ticket notify user: {e}")

        _pop_pending(aid)
        return


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not _is_admin(admin_id):
        return

    aid = context.user_data.get("active_aid")
    if not aid:
        return

    if _get_claimed(aid) != admin_id:
        await update.message.reply_text("Этот запрос взят другим оператором.")
        return

    p = _get_pending(aid)
    if not p:
        await update.message.reply_text("Запрос не найден или уже закрыт.")
        context.user_data.pop("active_aid", None)
        return

    try:
        main_bot = Bot(token=MAIN_BOT_TOKEN)
        await main_bot.send_message(
            chat_id=p["user_chat_id"],
            text=f"👨‍💼 <b>Оператор поддержки:</b>\n\n{update.message.text}",
            parse_mode="HTML",
        )
        await update.message.reply_text(
            "✉️ Отправлено сотруднику.",
            reply_markup=_active_chat_keyboard(aid),
        )
    except Exception as e:
        logging.error(f"forward reply error: {e}")
        await update.message.reply_text(f"Ошибка при отправке: {e}")


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("Нет доступа.")
        return
    w = _load_weights()
    lines = ["Текущие веса изменения модели при оценке:\n"]
    for score in range(1, 6):
        val = w.get(score, DEFAULT_WEIGHTS[score])
        sign = "+" if val > 0 else ""
        lines.append(f"{'⭐' * score} (оценка {score}): {sign}{val}%")
    lines.append("\nЧтобы изменить: /setweight <1-5> <значение>")
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
        "Изменение вступает в силу для всех новых оценок."
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
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
