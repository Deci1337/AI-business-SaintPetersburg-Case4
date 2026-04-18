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
В контексте тебе даны фрагменты базы знаний и похожие закрытые тикеты — используй их как основу ответа.

Как отвечать:
— Если в контексте есть релевантная информация (хотя бы частично пересекающаяся с вопросом) — ответь на её основе. Можно опираться на то, как похожие проблемы решались в прошлых тикетах.
— Ответ: 2–5 коротких пунктов с конкретными шагами. Никаких шаблонных фраз про «обратитесь к специалисту», если можешь помочь по контексту.
— Если вопрос неполный, но из контекста видна частая причина — предложи её как гипотезу и попроси уточнение.
— ТОЛЬКО если контекст совсем не про это (например, в контексте про VPN, а спрашивают про принтер) — ответь одним словом: НУЖЕН_СПЕЦИАЛИСТ

Отвечай на русском. По делу, без воды."""

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

TOPIC_PROMPT = """Определи тему IT-обращения сотрудника компании. Дай краткое название темы (2-4 слова, существительное или словосочетание) на русском языке. Только название темы, без пояснений.

Примеры правильных ответов: "Корпоративный портал", "Резервное копирование", "Удалённая работа", "Антивирусная защита", "Телефония и гарнитуры", "Мобильные устройства".

Обращение сотрудника: {query}"""

RELEVANCE_PROMPT = """Ты — фильтр входящих запросов в IT сервис-деск компании «Балтийский Берег» (пищевое производство, ~1000 сотрудников).

Твоя задача: определить, является ли запрос пользователя релевантным для IT-поддержки.

РЕЛЕВАНТНО — запрос касается:
- компьютеров, ноутбуков, принтеров, сканеров
- программ: 1С, ERP, Outlook, Windows, Office и любого ПО
- сети, интернета, VPN, удалённого доступа
- паролей, доступа, учётных записей, Active Directory
- серверов, баз данных, IT-инфраструктуры
- любой технической проблемы на рабочем месте
- мобильных телефонов и планшетов сотрудников (настройка, приложения, почта)
- заявок, тикетов, обращений в поддержку
- вопросов от имени сотрудника о рабочих устройствах или ПО (даже если сформулировано от третьего лица)

НЕ РЕЛЕВАНТНО — запрос:
- на посторонние темы (погода, рецепты, политика, спорт, личные развлечения)
- содержит оскорбления, угрозы, нецензурную лексику
- бессмысленный набор слов или символов
- касается сугубо личных вопросов, никак не связанных с рабочим местом или устройствами

Ответь СТРОГО одним словом:
- "РЕЛЕВАНТНО" — если запрос относится к IT-поддержке
- "НЕРЕЛЕВАНТНО" — если запрос не по теме или неуместен

Запрос пользователя: {query}"""

_OPERATOR_KEYWORDS = [
    "соедини с оператором", "соедини со специалистом",
    "живой человек", "живой специалист", "реальный человек",
    "переключи на", "переведи на оператора", "переведи на специалиста",
    "хочу поговорить с", "позови оператора", "позови специалиста",
    "нужен оператор", "хочу оператора", "хочу специалиста",
    "свяжи меня с", "передай оператору", "передай специалисту",
    "поговорить с человеком", "не хочу с ботом",
    "переключи меня", "переключите меня", "переведите меня",
    "соедините меня", "соедини меня",
    "вызови оператора", "вызовите оператора",
    "хочу к оператору", "хочу к специалисту",
]


def check_wants_operator(user_query: str) -> bool:
    """Возвращает True если сотрудник явно просит живого оператора."""
    q = user_query.lower().strip()
    return any(kw in q for kw in _OPERATOR_KEYWORDS)


_NO_INFO_MARKERS = [
    "нет информации",
    "не содержит информации",
    "не могу найти",
    "отсутствует в контексте",
    "нет данных",
    "предоставленном контексте нет",
    "в контексте не",
    "нужен_специалист",
]


def _is_escalated(top_score: float, answer: str) -> bool:
    a = answer.lower().strip()
    # Явный сигнал от LLM — ответ ровно НУЖЕН_СПЕЦИАЛИСТ
    if a == "нужен_специалист" or a.startswith("нужен_специалист"):
        return True
    # Совсем отсутствие контекста — дубль страховки (основной cutoff в ask_full)
    if top_score < 0.3:
        return True
    # Явное "не знаю" в коротком ответе
    if "не знаю" in a and len(a) < 120:
        return True
    # Отказные маркеры "в контексте нет информации"
    if any(m in a for m in _NO_INFO_MARKERS):
        return True
    return False


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


def classify_topic(user_query: str) -> str:
    """Определяет реальную тему для запросов категории 'Другое' через LLM."""
    try:
        result = _call_llm([
            {"role": "user", "content": TOPIC_PROMPT.format(query=user_query)},
        ])
        topic = result.strip().strip('"').strip("'")
        # Берём только первую строку, не длиннее 50 символов
        topic = topic.split("\n")[0].strip()[:50]
        return topic if len(topic) > 2 else "Другое"
    except Exception:
        return "Другое"


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


_VAGUE_PATTERNS = [
    "помогите", "помоги", "нужна помощь", "подскажите",
    "не работает", "не могу", "проблема",
]


def _is_vague(query: str, history_len: int) -> bool:
    """Слишком общий запрос без конкретики — просим уточнение вместо эскалации.
    Срабатывает только в начале диалога (без истории)."""
    if history_len > 0:
        return False
    q = query.lower().strip()
    words = q.split()
    if len(words) > 7:
        return False
    # Короткий запрос + общая фраза без конкретной детали
    return any(p in q for p in _VAGUE_PATTERNS)


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

    if check_wants_operator(user_query):
        cl = classify(user_query)
        if cl["service"] == "Другое":
            cl["service"] = classify_topic(user_query)
        return {
            "answer": "Понял, переключаю на специалиста поддержки.",
            "escalated": True,
            "irrelevant": False,
            "wants_operator": True,
            "classification": cl,
            "top_source": None,
        }

    search_query = extract_query(user_query)
    results = search(search_query, n_results=6)
    top_score = results[0]["score"] if results else 0.0

    # Если вопрос общий И в базе нет сильного совпадения — просим уточнение
    if top_score < 0.55 and _is_vague(user_query, len(history or [])):
        cl = classify(user_query)
        return {
            "answer": (
                "Уточните, пожалуйста, детали: что именно не работает, "
                "какое сообщение об ошибке видите, когда началось? "
                "Чем подробнее — тем быстрее помогу."
            ),
            "escalated": False,
            "irrelevant": False,
            "classification": cl,
            "top_source": None,
            "clarify": True,
        }

    if not results or top_score < 0.3:
        cl = classify(user_query)
        if cl["service"] == "Другое":
            cl["service"] = classify_topic(user_query)
        return {
            "answer": FALLBACK,
            "escalated": True,
            "irrelevant": False,
            "classification": cl,
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
    if classification["service"] == "Другое":
        classification["service"] = classify_topic(user_query)
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
