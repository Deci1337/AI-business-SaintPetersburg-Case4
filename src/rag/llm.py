import os
import requests
from dotenv import load_dotenv
from .retriever import search, format_context

load_dotenv()

API_KEY = os.getenv("YANDEX_GPT_API_KEY")
FOLDER_ID = os.getenv("YANDEX_GPT_FOLDER_ID")
MODEL = os.getenv("YANDEX_GPT_MODEL", "yandexgpt/latest")
COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = """Ты — ИИ-помощник сервис-деска компании «Балтийский Берег».
Отвечай только на основе предоставленного контекста из базы знаний и истории тикетов.
Если ответа в контексте нет — честно скажи, что не знаешь, и предложи обратиться к специалисту.
Отвечай кратко и по делу. Язык — русский."""

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


def _call_llm(messages: list) -> str:
    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/{MODEL}",
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": 1024},
        "messages": [
            {"role": m["role"], "text": m.get("content", m.get("text", ""))}
            for m in messages
        ],
    }
    r = requests.post(
        COMPLETION_URL,
        json=payload,
        headers={"Authorization": f"Api-Key {API_KEY}", "x-folder-id": FOLDER_ID},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["result"]["alternatives"][0]["message"]["text"].strip()


def classify(user_query: str) -> dict:
    """Классифицирует заявку — возвращает {service, priority}."""
    try:
        text = _call_llm([
            {"role": "user", "content": CLASSIFY_PROMPT.format(query=user_query)}
        ])
        result = {}
        for line in text.splitlines():
            if line.startswith("Сервис:"):
                result["service"] = line.split(":", 1)[1].strip()
            elif line.startswith("Приоритет:"):
                result["priority"] = line.split(":", 1)[1].strip()
        return result
    except Exception:
        return {"service": "Другое", "priority": "Средний"}


def ask(user_query: str) -> tuple[str, bool]:
    """Возвращает (ответ, escalated)."""
    results = search(user_query, n_results=6)
    if not results or results[0]["score"] < 0.4:
        return FALLBACK, True

    context = format_context(results)
    answer = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст:\n{context}\n\nВопрос: {user_query}"},
    ])

    escalated = results[0]["score"] < 0.55 or "не знаю" in answer.lower()
    return answer, escalated


def ask_full(user_query: str) -> dict:
    """Полный результат: ответ + эскалация + классификация + топ-чанки."""
    results = search(user_query, n_results=6)

    if not results or results[0]["score"] < 0.4:
        return {
            "answer": FALLBACK,
            "escalated": True,
            "classification": {"service": "Другое", "priority": "Средний"},
            "top_source": None,
        }

    context = format_context(results)
    answer = _call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст:\n{context}\n\nВопрос: {user_query}"},
    ])

    classification = classify(user_query)
    escalated = results[0]["score"] < 0.55 or "не знаю" in answer.lower()

    top = results[0]
    top_source = {
        "title": top["meta"].get("title", ""),
        "source": top["meta"].get("source", ""),
        "score": round(top["score"], 3),
    }

    return {
        "answer": answer,
        "escalated": escalated,
        "classification": classification,
        "top_source": top_source,
    }
