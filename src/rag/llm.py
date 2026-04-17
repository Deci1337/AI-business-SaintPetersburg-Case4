import os
from openai import OpenAI
from dotenv import load_dotenv
from .retriever import search, format_context

load_dotenv()

client = OpenAI(
    api_key=os.getenv("YANDEX_GPT_API_KEY"),
    base_url=os.getenv("YANDEX_GPT_BASE_URL", "https://llm.api.cloud.yandex.net/foundationModels/v1"),
)

MODEL = os.getenv("YANDEX_GPT_MODEL", "yandexgpt/latest")
FOLDER_ID = os.getenv("YANDEX_GPT_FOLDER_ID")

SYSTEM_PROMPT = """Ты — ИИ-помощник сервис-деска компании «Балтийский Берег».
Отвечай только на основе предоставленного контекста из базы знаний и истории тикетов.
Если ответа в контексте нет — честно скажи, что не знаешь, и предложи обратиться к специалисту.
Отвечай кратко и по делу. Язык — русский."""


def ask(user_query: str) -> str:
    results = search(user_query, n_results=6)
    if not results:
        return "Не нашёл релевантной информации. Рекомендую обратиться к специалисту поддержки."

    context = format_context(results)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Контекст:\n{context}\n\nВопрос: {user_query}"},
    ]

    extra = {}
    if FOLDER_ID:
        extra["extra_headers"] = {"x-folder-id": FOLDER_ID}

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=1024,
        temperature=0.1,
        **extra,
    )

    return response.choices[0].message.content.strip()
