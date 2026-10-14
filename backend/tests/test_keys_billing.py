"""Incomplete billing responses never become zero or a partial successful total."""
import math

import httpx
import pytest

from backend.app.services import provider_spend as ps


def fetch(fetcher, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        return fetcher("synthetic-key", client)


@pytest.mark.parametrize("fetcher", [ps.fetch_anthropic_cost, ps.fetch_openai_cost])
@pytest.mark.parametrize("body", [
    {}, {"data": None}, {"data": {}}, {"data": [None]},
    {"data": [{"results": None}]}, {"data": [{"results": {}}]},
    {"data": [], "has_more": True}, {"data": [], "has_more": "false"},
])
def test_missing_or_invalid_cost_structure_is_unavailable(fetcher, body):
    result = fetch(fetcher, body)
    assert result["kind"] == "unavailable" and result["amount"] is None


@pytest.mark.parametrize("fetcher", [ps.fetch_anthropic_cost, ps.fetch_openai_cost])
def test_valid_empty_cost_period_is_zero(fetcher):
    result = fetch(fetcher, {"data": [], "has_more": False})
    assert result["kind"] == "spend" and result["amount"] == 0


@pytest.mark.parametrize("fetcher", [ps.fetch_anthropic_cost, ps.fetch_openai_cost])
def test_incomplete_cursor_discards_partial_cost(fetcher):
    amount = "100" if fetcher is ps.fetch_anthropic_cost else {"value": 1, "currency": "usd"}
    result = fetch(fetcher, {"data": [{"results": [{"amount": amount, "currency": "USD"}]}],
                             "has_more": True, "next_page": None})
    assert result["kind"] == "unavailable" and result["amount"] is None


@pytest.mark.parametrize("fetcher", [ps.fetch_anthropic_cost, ps.fetch_openai_cost])
def test_cost_currencies_are_not_combined(fetcher):
    rows = [{"amount": "100", "currency": "USD"}, {"amount": "200", "currency": "EUR"}]
    if fetcher is ps.fetch_openai_cost:
        rows = [{"amount": {"value": 1, "currency": "usd"}}, {"amount": {"value": 2, "currency": "eur"}}]
    result = fetch(fetcher, {"data": [{"results": rows}], "has_more": False})
    assert result["kind"] == "unavailable" and result["amount"] is None


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "-Infinity"])
def test_openrouter_monthly_value_must_be_finite_number(value):
    result = fetch(ps.fetch_openrouter, {"data": {"usage_monthly": value}})
    assert result["kind"] == "unavailable" and result["amount"] is None


@pytest.mark.parametrize("fetcher", [ps.fetch_anthropic_cost, ps.fetch_openai_cost])
def test_overflowing_sum_is_unavailable(fetcher):
    amount = "1e308" if fetcher is ps.fetch_anthropic_cost else {"value": 1e308, "currency": "usd"}
    row = {"amount": amount, "currency": "USD"}
    result = fetch(fetcher, {"data": [{"results": [row, row]}], "has_more": False})
    assert result["kind"] == "unavailable" and result["amount"] is None


def test_deepseek_single_result_does_not_discard_another_currency():
    result = fetch(ps.fetch_deepseek, {"balance_infos": [
        {"currency": "CNY", "total_balance": "10"}, {"currency": "USD", "total_balance": "2"}]})
    assert result["kind"] == "unavailable" and result["amount"] is None


@pytest.mark.parametrize("fetcher", [ps.fetch_anthropic_cost, ps.fetch_openai_cost])
def test_second_page_rate_limit_discards_partial_total(fetcher):
    amount = "100" if fetcher is ps.fetch_anthropic_cost else {"value": 1, "currency": "usd"}
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 2:
            return httpx.Response(429, text="synthetic private body")
        return httpx.Response(200, json={"data": [{"results": [{"amount": amount, "currency": "USD"}]}],
                                        "has_more": True, "next_page": "next"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetcher("synthetic-key", client)
    assert len(calls) == 2 and result["amount"] is None and result["kind"] == "unavailable"
    assert "private" not in str(result)
    assert not any(isinstance(v, float) and not math.isfinite(v) for v in result.values())
