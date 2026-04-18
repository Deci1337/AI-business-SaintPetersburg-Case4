"""Датасет запросов для оценки качества работы нейросети.

Категории:
- relevant_answerable   — должен ответить, НЕ эскалировать
- relevant_escalate     — должен эскалировать (нет в базе / узкая проблема)
- operator_request      — явный запрос оператора
- irrelevant            — оффтоп/бред, блокируется check_relevance
"""

CASES = [
    # --- relevant, должен ответить ---
    # NB: «VPN из дома» в корпусе не покрыт — ретривер ловит «Не заходит в 1С ДО»,
    # поэтому честная эскалация — корректное поведение.
    {"query": "Не могу подключиться к VPN из дома, что делать?",
     "kind": "relevant_escalate"},
    {"query": "Как настроить рабочую почту Outlook на новом ноутбуке?",
     "kind": "relevant_answerable",
     "expected_service": "Почта"},
    {"query": "Забыл пароль от учётной записи, как восстановить?",
     "kind": "relevant_answerable",
     "expected_service": "Доступ и права"},
    {"query": "Принтер не печатает, горит красная лампочка",
     "kind": "relevant_answerable",
     "expected_service": "Оргтехника"},
    {"query": "1С зависает при формировании отчёта",
     "kind": "relevant_answerable",
     "expected_service": "1С и ERP"},

    # --- relevant, но ожидаем эскалацию (слишком специфично/редко) ---
    {"query": "У меня сломался квантовый ускоритель в BIOS Lenovo T14s гена 9",
     "kind": "relevant_escalate"},
    {"query": "На сервере AX-77 в стойке B12 моргает индикатор NVMe slot 14",
     "kind": "relevant_escalate"},

    # --- явный запрос оператора ---
    {"query": "Соедините меня с живым специалистом",
     "kind": "operator_request"},
    {"query": "не хочу с ботом, переключи на оператора",
     "kind": "operator_request"},

    # --- нерелевантные ---
    {"query": "Какая погода завтра в Санкт-Петербурге?",
     "kind": "irrelevant"},
    {"query": "Расскажи рецепт борща",
     "kind": "irrelevant"},
    {"query": "asdkfjhaskjdfhaksjdhf",
     "kind": "irrelevant"},
    {"query": "!!!",
     "kind": "irrelevant"},
]
