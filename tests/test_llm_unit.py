"""Юнит-тесты чистых функций llm.py (без сети).

Проверяют быстрые правила-шорткаты, от которых зависит, уйдёт ли
запрос в LLM вообще: детектор оператора, классификатор по ключевым словам,
эвристика эскалации, ранние отсечки в check_relevance.
"""
import pytest
from unittest.mock import patch

from src.rag import llm


class TestCheckWantsOperator:
    @pytest.mark.parametrize("q", [
        "соедини с оператором",
        "Переключи меня на специалиста, пожалуйста",
        "ХОЧУ ОПЕРАТОРА",
        "не хочу с ботом общаться",
    ])
    def test_detects_operator_request(self, q):
        assert llm.check_wants_operator(q) is True

    @pytest.mark.parametrize("q", [
        "не работает VPN",
        "как настроить Outlook",
        "",
        "оператор сотовой связи мешает",
    ])
    def test_ignores_unrelated(self, q):
        assert llm.check_wants_operator(q) is False


class TestClassify:
    @pytest.mark.parametrize("q,service", [
        ("1С не открывается", "1С и ERP"),
        ("VPN не подключается из дома", "Сеть и VPN"),
        ("Outlook не принимает почту", "Почта"),
        ("принтер зажевал бумагу", "Оргтехника"),
        ("забыл пароль от учётной записи", "Доступ и права"),
        ("ноутбук не включается", "IT-инфраструктура"),
        ("хочу заказать пиццу", "Другое"),
    ])
    def test_service_category(self, q, service):
        assert llm.classify(q)["service"] == service

    @pytest.mark.parametrize("q,priority", [
        ("всё стоит, срочно", "Критичный"),
        ("не подключается VPN", "Критичный"),
        ("ошибка при открытии 1С", "Высокий"),
        ("как настроить почту", "Низкий"),
        ("нужна консультация", "Средний"),
    ])
    def test_priority(self, q, priority):
        assert llm.classify(q)["priority"] == priority


class TestIsEscalated:
    def test_low_score_triggers_escalation(self):
        assert llm._is_escalated(0.3, "Вот краткая инструкция по VPN.") is True

    def test_high_score_normal_answer_no_escalation(self):
        assert llm._is_escalated(0.85, "Откройте настройки VPN и введите логин.") is False

    @pytest.mark.parametrize("answer", [
        "НУЖЕН_СПЕЦИАЛИСТ",
        "Не знаю.",
        "В предоставленном контексте нет данных.",
    ])
    def test_short_refusal_triggers_escalation(self, answer):
        assert llm._is_escalated(0.9, answer) is True

    def test_long_answer_with_trailing_marker_not_escalated(self):
        # Модель дала полезную инструкцию, но в конце добавила «нужен специалист».
        # Не должно считаться отказом — ответ-то полезный.
        answer = (
            "1. Проверьте наличие бумаги в принтере.\n"
            "2. Убедитесь, что картридж установлен правильно.\n"
            "3. Перезагрузите принтер.\n"
            "4. Если не помогло — НУЖЕН_СПЕЦИАЛИСТ."
        )
        assert llm._is_escalated(0.8, answer) is False

    def test_mid_score_with_good_answer_not_escalated(self):
        # Граничный случай: score чуть выше нового порога, ответ полезный.
        assert llm._is_escalated(0.58, "Откройте настройки и включите VPN.") is False

    def test_score_below_new_threshold_escalated(self):
        assert llm._is_escalated(0.5, "Любой ответ.") is True


class TestCheckRelevanceShortcuts:
    """Ранние отсечки check_relevance — срабатывают без обращения к LLM."""

    def test_too_short(self):
        assert llm.check_relevance("ок") is False

    def test_digits_only(self):
        assert llm.check_relevance("123456") is False

    def test_symbols_only(self):
        assert llm.check_relevance("!!!???") is False

    def test_llm_error_fails_open(self):
        with patch.object(llm, "_call_llm", side_effect=RuntimeError("api down")):
            assert llm.check_relevance("не работает принтер в офисе") is True

    def test_llm_says_irrelevant(self):
        with patch.object(llm, "_call_llm", return_value="НЕРЕЛЕВАНТНО"):
            assert llm.check_relevance("какая погода завтра в Москве") is False

    def test_llm_says_relevant(self):
        with patch.object(llm, "_call_llm", return_value="РЕЛЕВАНТНО"):
            assert llm.check_relevance("не открывается 1С") is True


class TestExtractQuery:
    def test_short_query_passthrough(self):
        assert llm.extract_query("VPN не работает") == "VPN не работает"

    def test_long_query_goes_through_llm(self):
        long_q = "Здравствуйте, коллеги, у меня тут такая ситуация — " \
                 "уже второй день не могу подключиться к VPN из дома."
        with patch.object(llm, "_call_llm", return_value="не подключается VPN") as m:
            assert llm.extract_query(long_q) == "не подключается VPN"
            m.assert_called_once()

    def test_llm_error_returns_original(self):
        long_q = "a" * 50
        with patch.object(llm, "_call_llm", side_effect=RuntimeError):
            assert llm.extract_query(long_q) == long_q
