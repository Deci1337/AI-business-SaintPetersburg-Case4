import os
import requests
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from .retriever import search, format_context

load_dotenv()

API_KEY = os.getenv("YANDEX_GPT_API_KEY")
FOLDER_ID = os.getenv("YANDEX_GPT_FOLDER_ID")
MODEL = os.getenv("YANDEX_GPT_MODEL", "yandexgpt/latest")
COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = """Ты — ИИ-помощник сервис-деска компании «Балтийский Берег».
Используй предоставленный контекст из базы знаний и истории тикетов как основной источник.
Если контекст содержит похожие случаи — опирайся на них и давай практические рекомендации.
Если контекст не покрывает вопрос полностью — дай общие шаги диагностики для IT-проблемы и предложи создать заявку.
Никогда не говори «не знаю» без попытки помочь. Отвечай кратко, по пунктам, на русском языке."""

CLASSIFY_PROMPT = """На основе вопроса пользователя определи:
1. Категорию сервиса (одно из: IT-инфраструктура, 1С и ERP, Сеть и VPN, Оргтехника, Почта, Доступ и права, Другое)
2. Приоритет (одно из: Критичный, Высокий, Средний, Низкий)

Ответь строго в формате:
Сервис: <категория>
Приоритет: <приоритет>

Вопрос: {query}"""

FALLBACK = (
    "Не нашёл точного ответа в базе знаний. "
    "Рекомендую обратиться к специалисту поддержки или создать заявку в системе."
)

EXTRACT_PROMPT = "Выдели суть IT-проблемы из сообщения пользователя одним коротким предложением на русском. Только суть, без лишних слов. Сообщение: {query}"

RELEVANCE_PROMPT = """Ты — фильтр входящих запросов в IT сервис-деск компании «Балтийский Берег» (пищевое производство, ~1000 сотрудников).

Твоя задача: определить, является ли запрос пользователя релевантным для IT-поддержки.

РЕЛЕВАНТНО — запрос касается:
- компьютеров, ноутбуков, принтеров, сканеров
- программ: 1С, ERP, Outlook, Windows, Office и любого ПО
- сети, интернета, VPN, удалённого доступа
- паролей, доступа, учётных записей, Active Directory
- серверов, баз данных, IT-инфраструктуры
- любой технической проблемы на рабочем месте
- заявок, тикетов, обращений в поддержку

НЕ РЕЛЕВАНТНО — запрос:
- на посторонние темы (погода, рецепты, политика, развлечения)
- содержит оскорбления, угрозы, нецензурную лексику
- бессмысленный набор слов или символов
- касается личных (не рабочих) вопросов не связанных с IT

Ответь СТРОГО одним словом:
- "РЕЛЕВАНТНО" — если запрос относится к IT-поддержке
- "НЕРЕЛЕВАНТНО" — если запрос не по теме или неуместен

Запрос пользователя: {query}"""

_NO_INFO_MARKERS = [
    "нет информации",
    "не содержит информации",
    "не могу найти",
    "отсутствует в контексте",
    "нет данных",
    "предоставленном контексте нет",
    "в контексте не",
]


def _is_escalated(top_score: float, answer: str) -> bool:
    a = answer.lower()
    return top_score < 0.45 or "не знаю" in a or any(m in a for m in _NO_INFO_MARKERS)


def check_relevance(user_query: str) -> bool:
    """Возвращает True если запрос релевантен IT-поддержке, False если оффтоп/бред."""
    # Очень короткие бессмысленные сообщения — сразу нерелевантны
    stripped = user_query.strip()
    if len(stripped) < 3:
        return False
    # Только цифры/символы без букв — бред
    if not any(c.isalpha() for c in stripped):
        return False
    try:
        result = _call_llm([
            {"role": "user", "content": RELEVANCE_PROMPT.format(query=user_query)},
        ])
        return "РЕЛЕВАНТНО" in result.upper() and "НЕРЕЛЕВАНТНО" not in result.upper()
    except Exception:
        return True  # при ошибке LLM — не блокируем, пропускаем дальше


def extract_query(user_query: str) -> str:
    """Очищает запрос от шума через LLM перед поиском."""
    if len(user_query) < 30:
        return user_query
    try:
        result = _call_llm([
            {"role": "user", "content": EXTRACT_PROMPT.format(query=user_query)},
        ])
        return result if result else user_query
    except Exception:
        return user_query


def _call_llm(messages: list, retries: int = 3) -> str:
    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/{MODEL}",
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": 1024},
        "messages": [
            {"role": m["role"], "text": m.get("content", m.get("text", ""))}
            for m in messages
        ],
    }
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(
                COMPLETION_URL,
                json=payload,
                headers={"Authorization": f"Api-Key {API_KEY}", "x-folder-id": FOLDER_ID},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["result"]["alternatives"][0]["message"]["text"].strip()
        except Exception as e:
            last_err = e
    raise last_err


_SERVICE_RULES = [
    ("1С и ERP",        ["1с", "erp", "инфобух", "бухгалт", "отчёт", "отчет", "зарплат", "проводк"]),
    ("Сеть и VPN",      ["vpn", "впн", "сеть", "интернет", "связь", "wifi", "wi-fi", "ethernet", "удалённый", "удаленный", "l2tp", "openvpn"]),
    ("IT-инфраструктура", ["сервер", "компьютер", "пк", "ноутбук", "windows", "драйвер", "установ", "обновлен"]),
    ("Почта",           ["почта", "email", "outlook", "письм", "ящик"]),
    ("Оргтехника",      ["принтер", "сканер", "мфу", "картридж", "копир"]),
    ("Доступ и права",  ["доступ", "права", "пароль", "учётная", "учетная", "заблокир", "active directory", "ad"]),
]
_PRIORITY_RULES = [
    ("Критичный",  ["не работает", "не подключается", "упал", "критич", "срочно", "всё стоит", "все стоит"]),
    ("Высокий",    ["не открывается", "ошибка", "проблема", "медленно", "завис"]),
    ("Низкий",     ["вопрос", "как настроить", "подскажите", "узнать"]),
]


def classify(user_query: str) -> dict:
    """Классифицирует заявку по ключевым словам — без LLM-вызова."""
    q = user_query.lower()
    service = "Другое"
    for svc, keywords in _SERVICE_RULES:
        if any(kw in q for kw in keywords):
            service = svc
            break
    priority = "Средний"
    for pri, keywords in _PRIORITY_RULES:
        if any(kw in q for kw in keywords):
            priority = pri
            break
    return {"service": service, "priority": priority}


def ask(user_query: str) -> tuple[str, bool]:
    """Возвращает (ответ, escalated)."""
    search_query = extract_query(user_query)
    results = search(search_query, n_results=6)
    if not results or results[0]["score"] < 0.4:
        return FALLBACK, True

    context = format_context(results)
    answer = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст:\n{context}\n\nВопрос: {user_query}"},
    ])

    escalated = _is_escalated(results[0]["score"], answer)
    return answer, escalated


def _build_history_block(history: list[dict]) -> str:
    """Форматирует историю диалога для вставки в промпт."""
    if not history:
        return ""
    lines = ["Предыдущий диалог:"]
    for turn in history:
        lines.append(f"Сотрудник: {turn['user']}")
        lines.append(f"Ассистент: {turn['assistant']}")
    return "\n".join(lines)


def ask_full(user_query: str, history: list[dict] | None = None) -> dict:
    """Полный результат: ответ + эскалация + классификация + топ-чанки.

    history — список dict {"user": str, "assistant": str}, последние N пар.
    Если запрос нерелевантен — возвращает irrelevant=True, статистика не пишется.
    """
    if not check_relevance(user_query):
        return {
            "answer": "",
            "escalated": False,
            "irrelevant": True,
            "classification": {},
            "top_source": None,
        }

    search_query = extract_query(user_query)
    results = search(search_query, n_results=6)

    if not results or results[0]["score"] < 0.4:
        return {
            "answer": FALLBACK,
            "escalated": True,
            "irrelevant": False,
            "classification": {"service": "Другое", "priority": "Средний"},
            "top_source": None,
        }

    context = format_context(results)
    history_block = _build_history_block(history or [])

    user_content_parts = [f"Контекст:\n{context}"]
    if history_block:
        user_content_parts.append(history_block)
    user_content_parts.append(f"Текущий вопрос: {user_query}")

    answer = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_content_parts)},
    ])
    classification = classify(user_query)
    escalated = _is_escalated(results[0]["score"], answer)

    top = results[0]
    top_source = {
        "title": top["meta"].get("title", ""),
        "source": top["meta"].get("source", ""),
        "score": round(top["score"], 3),
    }

    return {
        "answer": answer,
        "escalated": escalated,
        "irrelevant": False,
        "classification": classification,
        "top_source": top_source,
    }
