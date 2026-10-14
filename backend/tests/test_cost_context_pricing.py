"""CST-04/05/07: whole-call context tiers, UTC price periods, and the audited card."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services import cost

# Independent Standard text-token fixtures checked 2026-10-14, USD/1M.
# OpenAI pricing page's expanded table (its official pricing JS supplies long rates):
# https://developers.openai.com/api/docs/pricing
# The expanded table's context selector defines <=272K and >272K input tokens.
# Gemini tiers: https://ai.google.dev/gemini-api/docs/pricing
# Rates are (input, output, cache write, cache read); absent OpenAI cache discounts
# use input price, matching the existing no-discount fallback for those models.
CONTEXT_RATES = [
    ("openai", "gpt-6-astra", 272_000, (10.0, 50.0, 12.5, 1.0), (20.0, 75.0, 25.0, 2.0)),
    ("openai", "gpt-6.1-sol", 272_000, (2.0, 10.0, 2.5, 0.1), (4.0, 15.0, 5.0, 0.2)),
    ("openai", "gpt-6-sol", 272_000, (2.0, 10.0, 2.5, 0.2), (4.0, 15.0, 5.0, 0.4)),
    ("openai", "gpt-6-luna", 272_000, (0.1, 0.5, 0.125, 0.01), (0.2, 0.75, 0.25, 0.02)),
    ("openai", "gpt-5.6-sol", 272_000, (4.0, 20.0, 5.0, 0.4), (8.0, 30.0, 10.0, 0.8)),
    ("openai", "gpt-5.6-terra", 272_000, (2.0, 12.0, 2.5, 0.2), (4.0, 18.0, 5.0, 0.4)),
    ("openai", "gpt-5.6-luna", 272_000, (0.2, 1.2, 0.25, 0.02), (0.4, 1.8, 0.5, 0.04)),
    ("openai", "gpt-5.5", 272_000, (5.0, 30.0, 5.0, 0.5), (10.0, 45.0, 10.0, 1.0)),
    ("openai", "gpt-5.5-pro", 272_000, (30.0, 180.0, 30.0, 30.0), (60.0, 270.0, 60.0, 60.0)),
    ("openai", "gpt-5.4", 272_000, (2.5, 15.0, 2.5, 0.25), (5.0, 22.5, 5.0, 0.5)),
    ("openai", "gpt-5.4-pro", 272_000, (30.0, 180.0, 30.0, 30.0), (60.0, 270.0, 60.0, 60.0)),
    ("gemini", "gemini-3.1-pro-preview", 200_000, (2.0, 12.0, 2.0, 0.2), (4.0, 18.0, 4.0, 0.4)),
    ("gemini", "gemini-2.5-pro", 200_000, (1.25, 10.0, 1.25, 0.125), (2.5, 15.0, 2.5, 0.25)),
]


@pytest.mark.parametrize("provider,model,threshold,short,long", CONTEXT_RATES)
def test_context_boundary_prices_the_whole_call(provider, model, threshold, short, long):
    for tokens, rates in [(threshold, short), (threshold + 1, long)]:
        result = cost.calculate_cost_detail(provider, model, tokens, 1000)
        assert result.source == cost.SOURCE_PRICING_TABLE
        assert result.cost_usd == pytest.approx((tokens * rates[0] + 1000 * rates[1]) / 1e6, abs=1e-12)


@pytest.mark.parametrize("provider,model,threshold,short,long", CONTEXT_RATES)
@pytest.mark.parametrize("field,index", [("cache_write_tokens", 2), ("cache_read_tokens", 3)])
def test_cached_input_alone_selects_long_context(provider, model, threshold, short, long, field, index):
    tokens = threshold + 1
    result = cost.calculate_cost_detail(provider, model, 0, 1000, **{field: tokens})
    assert result.source == cost.SOURCE_PRICING_TABLE
    assert result.cost_usd == pytest.approx((tokens * long[index] + 1000 * long[1]) / 1e6, abs=1e-12)


@pytest.mark.parametrize("read,write,rates", [(30_000, 20_000, (1.25, 10.0, 1.25, 0.125)),
                                           (30_001, 20_000, (2.5, 15.0, 2.5, 0.25))])
def test_all_input_classes_choose_one_context_tier(read, write, rates):
    result = cost.calculate_cost_detail("gemini", "gemini-2.5-pro", 150_000, 1000,
                                        cache_read_tokens=read, cache_write_tokens=write)
    expected = (150_000 * rates[0] + 1000 * rates[1] + write * rates[2] + read * rates[3]) / 1e6
    assert result.cost_usd == pytest.approx(expected, abs=1e-12)


def test_verified_snapshot_uses_its_models_context_tier():
    result = cost.calculate_cost_detail("openai", "gpt-5.4-2026-03-05", 300_000, 1000)
    assert result.cost_usd == 1.5225


# Official page states calendar dates, without an effective time/timezone.
# Product decision: use 2027-01-01 00:00 UTC conservatively.
# https://ai.google.dev/gemini-api/docs/pricing (checked 2026-10-14)
@pytest.mark.parametrize("model", ["gemini-3.6-flash", "gemini-3.8-flash"])
@pytest.mark.parametrize("timestamp,rates", [
    (datetime(2026, 12, 31, 23, 59, 59, 999999), (0.75, 3.75, 0.75, 0.075)),
    (datetime(2027, 1, 1), (1.5, 7.5, 1.5, 0.15)),
    (datetime(2027, 1, 1, 8, 59, 59, tzinfo=timezone(timedelta(hours=9))), (0.75, 3.75, 0.75, 0.075)),
    (datetime(2027, 1, 1, 9, 0, 0, tzinfo=timezone(timedelta(hours=9))), (1.5, 7.5, 1.5, 0.15)),
])
def test_gemini_price_period_uses_record_utc_timestamp(model, timestamp, rates):
    result = cost.calculate_cost_detail("gemini", model, 1000, 2000, cache_write_tokens=3000,
                                        cache_read_tokens=4000, timestamp=timestamp)
    expected = (1000 * rates[0] + 2000 * rates[1] + 3000 * rates[2] + 4000 * rates[3]) / 1e6
    assert result.cost_usd == pytest.approx(expected, abs=1e-12)


def test_backfilled_trace_uses_record_time_while_default_uses_current_utc(monkeypatch):
    monkeypatch.setattr(cost, "utc_now", lambda: datetime(2028, 1, 1), raising=False)
    assert cost.calculate_cost("gemini", "gemini-3.8-flash", 1000, 0) == 0.0015
    assert cost.calculate_cost("gemini", "gemini-3.8-flash", 1000, 0,
                               timestamp=datetime(2026, 12, 31)) == 0.00075


def test_pricing_endpoint_exposes_one_hour_rates_and_unconfirmed_null(client):
    models = client.get("/api/v1/system/pricing").json()["models"]
    assert models["claude-opus-5-5"]["cache_write_1h"] == 8.0
    assert models["claude-3-5-sonnet"]["cache_write_1h"] is None
    assert models["gpt-6-astra"]["cache_write_1h"] is None


def test_pricing_endpoint_exposes_context_tiers(client):
    model = client.get("/api/v1/system/pricing").json()["models"]["gemini-2.5-pro"]
    assert model["long_context"] == {
        "input_tokens_gt": 200_000, "input": 2.5, "output": 15.0,
        "cache_write": 2.5, "cache_read": 0.25, "cache_write_1h": None,
    }


def test_pricing_endpoint_exposes_periods_and_current_effective_price(client, monkeypatch):
    monkeypatch.setattr(cost, "utc_now", lambda: datetime(2027, 1, 1), raising=False)
    card = client.get("/api/v1/system/pricing").json()
    model = card["models"]["gemini-3.8-flash"]
    assert card["effective_at"] == "2027-01-01T00:00:00+00:00"
    assert model["input"] == 1.5
    assert model["price_periods"] == [
        {"effective_from": None, "effective_until": "2027-01-01T00:00:00+00:00",
         "input": 0.75, "output": 3.75, "cache_write": 0.75, "cache_read": 0.075, "cache_write_1h": None},
        {"effective_from": "2027-01-01T00:00:00+00:00", "effective_until": None,
         "input": 1.5, "output": 7.5, "cache_write": 1.5, "cache_read": 0.15, "cache_write_1h": None},
    ]
