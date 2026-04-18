"""Интеграционный прогон: реальные запросы → ask_full → оценка результата.

Запуск (требует .env с YANDEX_GPT_API_KEY и построенный ChromaDB):
    RUN_LLM_EVAL=1 pytest tests/test_llm_integration.py -v -s

Без RUN_LLM_EVAL=1 тесты пропускаются, чтобы не бить по внешнему API
в обычном прогоне pytest.

Проверяет для каждого случая:
- operator_request  → wants_operator=True, escalated=True
- irrelevant        → irrelevant=True
- relevant_*        → irrelevant=False, классификация непустая
- relevant_answerable → НЕ эскалировано, ответ непустой и осмысленный
- relevant_escalate   → эскалировано
"""
import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from src.rag.llm import ask_full
from eval_dataset import CASES


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LLM_EVAL") != "1",
    reason="set RUN_LLM_EVAL=1 to run live LLM evaluation",
)


def _assert_case(case: dict, result: dict) -> None:
    kind = case["kind"]
    q = case["query"]

    if kind == "operator_request":
        assert result.get("wants_operator") is True, f"operator not detected for: {q}"
        assert result["escalated"] is True
        return

    if kind == "irrelevant":
        assert result.get("irrelevant") is True, f"should be irrelevant: {q}\n{result}"
        return

    # relevant_*
    assert result.get("irrelevant") is False, f"wrongly blocked as irrelevant: {q}"
    assert result["classification"], "classification should not be empty"

    if kind == "relevant_answerable":
        assert result["escalated"] is False, (
            f"unexpected escalation for answerable query: {q}\n"
            f"answer={result['answer']!r}\ntop_source={result.get('top_source')}"
        )
        assert len(result["answer"].strip()) > 10
        if "expected_service" in case:
            assert result["classification"]["service"] == case["expected_service"], (
                f"service mismatch: {q} -> {result['classification']}"
            )

    if kind == "relevant_escalate":
        assert result["escalated"] is True, (
            f"expected escalation but got answer: {q}\n{result['answer']!r}"
        )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["kind"] + ":" + c["query"][:40])
def test_ask_full_case(case):
    result = ask_full(case["query"])
    print(f"\n[{case['kind']}] {case['query']}\n  -> {result}")
    _assert_case(case, result)


def test_eval_summary():
    """Сводка точности по категориям — полезно смотреть глазами."""
    buckets: dict[str, list[bool]] = {}
    for case in CASES:
        try:
            result = ask_full(case["query"])
            _assert_case(case, result)
            ok = True
        except AssertionError as e:
            print(f"FAIL [{case['kind']}] {case['query']}: {e}")
            ok = False
        buckets.setdefault(case["kind"], []).append(ok)

    print("\n=== LLM eval summary ===")
    total_ok, total = 0, 0
    for kind, results in buckets.items():
        n_ok = sum(results)
        n = len(results)
        total_ok += n_ok
        total += n
        print(f"  {kind:22s} {n_ok}/{n}")
    print(f"  {'TOTAL':22s} {total_ok}/{total} ({100*total_ok/total:.1f}%)")
    assert total_ok / total >= 0.8, "LLM quality below 80% — review prompts/thresholds"
