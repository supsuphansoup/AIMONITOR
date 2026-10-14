"""Release-review regressions; official fixtures checked on 2026-10-14."""
import math

import pytest

from backend.app.services import cost

# Independent fixtures, USD/1M: (input, output, cache write, cache read).
# https://platform.claude.com/docs/en/about-claude/pricing
# https://developers.openai.com/api/docs/pricing
# https://developers.openai.com/api/docs/models/gpt-6-sol
# https://developers.openai.com/api/docs/models/gpt-5.6-terra
# https://developers.openai.com/api/docs/models/gpt-5.6-luna
# https://developers.openai.com/api/docs/models/o1-mini
# https://developers.googleblog.com/en/gemini-15-flash-8b-is-now-generally-available-for-use/
OFFICIAL_SHORT_RATES = [
    ("anthropic", "claude-sonnet-5-5", (2.0, 10.0, 2.5, 0.1)),  # CST-01
    ("openai", "gpt-6-astra", (10.0, 50.0, 12.5, 1.0)),  # CST-02
    ("openai", "gpt-6.1-sol", (2.0, 10.0, 2.5, 0.1)),
    ("openai", "gpt-6-sol", (2.0, 10.0, 2.5, 0.2)),
    ("openai", "gpt-6-luna", (0.1, 0.5, 0.125, 0.01)),
    ("openai", "gpt-5.6-sol", (4.0, 20.0, 5.0, 0.4)),
    ("openai", "gpt-5.6-terra", (2.0, 12.0, 2.5, 0.2)),
    ("openai", "gpt-5.6-luna", (0.2, 1.2, 0.25, 0.02)),
    ("openai", "o1-mini", (1.1, 4.4, 1.1, 0.55)),  # CST-06
    ("gemini", "gemini-1.5-flash-8b", (0.0375, 0.15, 0.0375, 0.01)),  # CST-15: <128K
]


@pytest.mark.parametrize("provider,model,rates", OFFICIAL_SHORT_RATES)
def test_review_official_short_context_rates(provider, model, rates):
    # 100K isolates short-context prices even when a model has a long tier.
    calls = [
        {"prompt_tokens": 100_000, "completion_tokens": 0},
        {"prompt_tokens": 0, "completion_tokens": 100_000},
        {"prompt_tokens": 0, "completion_tokens": 0, "cache_write_tokens": 100_000},
        {"prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 100_000},
    ]
    for tokens, rate in zip(calls, rates, strict=True):
        result = cost.calculate_cost_detail(provider, model, **tokens)
        assert result.source == cost.SOURCE_PRICING_TABLE
        assert result.cost_usd == pytest.approx(rate / 10, abs=1e-12)


def test_review_unconfirmed_gemini35_alias_is_unknown():
    # CST-03: routing is documented, alias billing is not; do not infer a price.
    # https://ai.google.dev/gemini-api/docs/changelog (2026-10-08 entry; checked 2026-10-14)
    result = cost.calculate_cost_detail("gemini", "gemini-3.5-flash", 1000, 1000)
    assert result == cost.CostResult(0.0, cost.SOURCE_UNKNOWN)


def test_review_o1mini_price_update_does_not_recalculate_existing_rows(session):
    from backend.app.models.trace import TraceEvent

    row = TraceEvent(trace_id="old-o1-mini", provider="openai", model_name="o1-mini", prompt_tokens=1000,
                     cost_usd=0.003, cost_source=cost.SOURCE_PRICING_TABLE)
    session.add(row)
    session.commit()
    assert cost.calculate_cost_detail("openai", "o1-mini", 1000, 0).cost_usd == 0.0011
    session.refresh(row)
    assert row.cost_usd == 0.003


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), 10 ** 400])
def test_review_nonfinite_custom_cost_is_unknown(value):
    # CST-08: even an explicit override cannot introduce a non-finite stored cost.
    result = cost.calculate_cost_detail("openai", "gpt-4o", 1000, 1000, custom_cost_usd=value)
    assert result == cost.CostResult(0.0, cost.SOURCE_UNKNOWN)


@pytest.mark.parametrize("field", [
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens",
])
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), 10 ** 400])
def test_review_nonfinite_tokens_are_unknown_even_with_override(field, value):
    tokens = {"prompt_tokens": 0, "completion_tokens": 0, field: value}
    result = cost.calculate_cost_detail("openai", "gpt-4o", custom_cost_usd=0.25, **tokens)
    assert result == cost.CostResult(0.0, cost.SOURCE_UNKNOWN)


@pytest.mark.parametrize("rate", [float("inf"), float("nan"), 1e308])
def test_review_nonfinite_computed_total_is_unknown(rate, monkeypatch):
    monkeypatch.setitem(cost.MODEL_PRICING_USD_PER_1M, "gpt-4o", cost.ModelPricing(rate, 10.0))
    result = cost.calculate_cost_detail("openai", "gpt-4o", 10_000_000, 0)
    assert math.isfinite(result.cost_usd)
    assert result == cost.CostResult(0.0, cost.SOURCE_UNKNOWN)


def test_review_submicrodollar_calls_accumulate_without_losing_cost():
    # CST-13: 9 * $0.05/1M = $0.00000045; 100K calls must retain $0.045.
    result = cost.calculate_cost_detail("openai", "gpt-5-nano", 9, 0)
    assert result.cost_usd == pytest.approx(0.00000045, abs=1e-12)
    assert math.fsum([result.cost_usd] * 100_000) == pytest.approx(0.045, abs=1e-12)


def test_review_custom_cost_retains_storage_precision():
    result = cost.calculate_cost_detail("custom", "unpriced", 0, 0, custom_cost_usd=0.0000001234567)
    assert result == cost.CostResult(0.000000123457, cost.SOURCE_CUSTOM)
