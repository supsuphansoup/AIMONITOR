"""Verified rate card, 1-hour cache writes, and latency the source never measured."""
import pytest
from fastapi.testclient import TestClient

from backend.app.services.cost import (
    SOURCE_PRICING_TABLE,
    SOURCE_UNKNOWN,
    calculate_cost_detail,
    normalize_model_name,
)


@pytest.mark.parametrize(
    "raw, key",
    [
        ("claude-opus-5-5", "claude-opus-5-5"),
        ("claude-opus-5", "claude-opus-5"),
        ("claude-haiku-4-5", "claude-haiku-4-5"),
        ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
        ("gpt-5.2-codex", "gpt-5.2-codex"),
        ("models/gemini-2.5-pro", "gemini-2.5-pro"),
    ],
)
def test_known_models_resolve_to_their_own_rate(raw, key):
    assert normalize_model_name(raw) == key


@pytest.mark.parametrize("raw", ["gpt-5.9", "claude-opus-6", "gemini-4-pro", "my-o1-clone"])
def test_unknown_versions_do_not_borrow_a_neighbours_price(raw):
    assert calculate_cost_detail("openai", raw, 1000, 1000).source == SOURCE_UNKNOWN


@pytest.mark.parametrize("provider, model", [("deepseek", "deepseek-chat"), ("mistral", "mistral-large"),
                                             ("together", "llama3.1-70b")])
def test_hosted_open_weight_models_are_not_free(provider, model):
    # Same weights, sold per token by a hosted API: unknown, never 0 원.
    assert calculate_cost_detail(provider, model, 1000, 1000).source == SOURCE_UNKNOWN


def test_local_provider_is_still_free():
    assert calculate_cost_detail("ollama", "llama3.1:8b", 1000, 1000).cost_usd == 0.0


def test_opus_5_5_uses_its_published_rates():
    # $4 in / $20 out / $0.20 cache read / $5 5m write / $8 1h write per MTok
    m = 1_000_000
    assert calculate_cost_detail("anthropic", "claude-opus-5-5", m, 0).cost_usd == pytest.approx(4.0)
    assert calculate_cost_detail("anthropic", "claude-opus-5-5", 0, m).cost_usd == pytest.approx(20.0)
    assert calculate_cost_detail("anthropic", "claude-opus-5-5", 0, 0, cache_read_tokens=m).cost_usd == pytest.approx(0.2)
    five_min = calculate_cost_detail("anthropic", "claude-opus-5-5", 0, 0, cache_write_tokens=m)
    one_hour = calculate_cost_detail("anthropic", "claude-opus-5-5", 0, 0, cache_write_tokens=m,
                                     cache_write_1h_tokens=m)
    assert five_min.cost_usd == pytest.approx(5.0)
    assert one_hour.cost_usd == pytest.approx(8.0)
    assert one_hour.source == SOURCE_PRICING_TABLE


def test_one_hour_share_cannot_exceed_total_writes():
    capped = calculate_cost_detail("anthropic", "claude-opus-5-5", 0, 0, cache_write_tokens=100,
                                   cache_write_1h_tokens=10_000)
    exact = calculate_cost_detail("anthropic", "claude-opus-5-5", 0, 0, cache_write_tokens=100,
                                  cache_write_1h_tokens=100)
    assert capped.cost_usd == exact.cost_usd


def test_gemini_3_1_pro_uses_published_rate():
    cost = calculate_cost_detail("gemini", "gemini-3.1-pro-preview", 1_000_000, 1_000_000)
    assert cost.cost_usd == pytest.approx(2.0 + 12.0)


def _trace(**kw):
    base = {"provider": "anthropic", "model_name": "claude-opus-5-5", "prompt_tokens": 10, "completion_tokens": 10}
    base.update(kw)
    return base


def test_unmeasured_latency_is_excluded_from_stats(client: TestClient):
    client.post("/api/v1/traces/batch", json={"traces": [
        _trace(latency_ms=100.0),
        _trace(latency_ms=300.0),
        _trace(),  # watcher row: no timing
        _trace(),
    ]})
    ov = client.get("/api/v1/metrics/overview").json()
    assert ov["total_requests"] == 4
    assert ov["latency_measured_count"] == 2
    assert ov["avg_latency_ms"] == pytest.approx(200.0)
    assert ov["p50_latency_ms"] == pytest.approx(200.0)

    bd = client.get("/api/v1/metrics/breakdown?hours_back=1").json()
    assert bd[0]["avg_latency_ms"] == pytest.approx(200.0)


def test_unmeasured_only_reports_no_latency_rather_than_zero(client: TestClient):
    client.post("/api/v1/traces", json=_trace())
    bd = client.get("/api/v1/metrics/breakdown?hours_back=1").json()
    assert bd[0]["avg_latency_ms"] is None
    ts = client.get("/api/v1/metrics/timeseries?hours_back=1").json()
    assert ts[0]["avg_latency_ms"] is None


def test_one_hour_cache_writes_are_priced_at_ingest(client: TestClient):
    res = client.post("/api/v1/traces", json=_trace(prompt_tokens=0, completion_tokens=0,
                                                    cache_write_tokens=1_000_000,
                                                    cache_write_1h_tokens=1_000_000))
    body = res.json()
    assert body["cost_usd"] == pytest.approx(8.0)
    assert body["cache_write_1h_tokens"] == 1_000_000
    assert body["latency_measured"] is False

