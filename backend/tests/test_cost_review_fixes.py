"""Release-review regressions; official fixtures checked on 2026-10-14."""
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
    for tokens, rate in zip(calls, rates):
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
