"""Cost attribution: unpriced models, cache rates, and custom overrides."""
from fastapi.testclient import TestClient

from backend.app.services.cost import (
    SOURCE_CUSTOM,
    SOURCE_LOCAL_FREE,
    SOURCE_PRICING_TABLE,
    SOURCE_UNKNOWN,
    cache_rates,
    calculate_cost_detail,
    resolve_pricing,
)


def test_unknown_model_is_not_priced_as_zero_or_guessed():
    """An unpriced model must be reported as not computable, not invented."""
    result = calculate_cost_detail("acme", "some-unreleased-model", 1_000_000, 1_000_000)
    assert result.source == SOURCE_UNKNOWN
    assert result.is_computable is False
    assert result.cost_usd == 0.0


def test_known_model_reports_pricing_table():
    result = calculate_cost_detail("openai", "gpt-4o", 10_000, 5_000)
    assert result.source == SOURCE_PRICING_TABLE
    assert result.is_computable is True
    assert result.cost_usd == 0.075


def test_local_provider_is_free_not_unknown():
    result = calculate_cost_detail("ollama", "llama3", 50_000, 20_000)
    assert result.source == SOURCE_LOCAL_FREE
    assert result.is_computable is True
    assert result.cost_usd == 0.0


def test_custom_cost_wins():
    result = calculate_cost_detail("whoever", "whatever", 100, 100, custom_cost_usd=0.0123)
    assert result == (0.0123, SOURCE_CUSTOM)


def test_cache_read_and_write_have_different_rates():
    """Collapsing both into one rate is exactly the mistake this guards against."""
    read_only = calculate_cost_detail("anthropic", "claude-3-5-sonnet", 0, 0, cache_read_tokens=1_000_000)
    write_only = calculate_cost_detail("anthropic", "claude-3-5-sonnet", 0, 0, cache_write_tokens=1_000_000)
    plain_input = calculate_cost_detail("anthropic", "claude-3-5-sonnet", 1_000_000, 0)

    assert read_only.cost_usd < plain_input.cost_usd < write_only.cost_usd
    assert read_only.cost_usd == 0.3    # 0.10x of the 3.00 input rate
    assert write_only.cost_usd == 3.75  # 1.25x of the 3.00 input rate


def test_cache_rates_fall_back_to_provider_policy():
    pricing = resolve_pricing("openai", "gpt-4o")
    write_rate, read_rate = cache_rates("openai", pricing)
    assert write_rate == pricing.input_per_1m          # writes billed as input
    assert read_rate == pricing.input_per_1m * 0.50    # reads discounted


def test_unknown_provider_cache_rates_do_not_discount():
    pricing = resolve_pricing("openai", "gpt-4o")
    write_rate, read_rate = cache_rates("some-new-provider", pricing)
    assert write_rate == read_rate == pricing.input_per_1m


def test_ingest_records_cost_source(client: TestClient):
    known = client.post("/api/v1/traces", json={
        "provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 1000, "completion_tokens": 500,
    }).json()
    unknown = client.post("/api/v1/traces", json={
        "provider": "acme", "model_name": "mystery-1", "prompt_tokens": 1000, "completion_tokens": 500,
    }).json()

    assert known["cost_source"] == SOURCE_PRICING_TABLE
    assert unknown["cost_source"] == SOURCE_UNKNOWN
    assert unknown["cost_usd"] == 0.0


def test_overview_counts_unpriced_calls(client: TestClient):
    client.post("/api/v1/traces", json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10})
    client.post("/api/v1/traces", json={"provider": "acme", "model_name": "mystery-1", "prompt_tokens": 10})
    client.post("/api/v1/traces", json={"provider": "acme", "model_name": "mystery-2", "prompt_tokens": 10})

    overview = client.get("/api/v1/metrics/overview").json()
    assert overview["total_requests"] == 3
    assert overview["cost_unknown_count"] == 2


def test_breakdown_reports_unpriced_calls(client: TestClient):
    client.post("/api/v1/traces", json={"provider": "acme", "model_name": "mystery-1", "prompt_tokens": 10})
    rows = client.get("/api/v1/metrics/breakdown").json()
    assert rows[0]["cost_unknown_count"] == 1


def test_cache_tokens_are_aggregated(client: TestClient):
    client.post("/api/v1/traces", json={
        "provider": "anthropic", "model_name": "claude-3-5-sonnet",
        "prompt_tokens": 100, "completion_tokens": 50,
        "cache_read_tokens": 4000, "cache_write_tokens": 1000,
    })
    overview = client.get("/api/v1/metrics/overview").json()
    assert overview["cache_read_tokens"] == 4000
    assert overview["cache_write_tokens"] == 1000
    # total_tokens defaults to the sum of every token class.
    assert overview["total_tokens"] == 5150
