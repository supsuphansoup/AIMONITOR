import pytest

from backend.app.services.cost import calculate_cost, normalize_model_name


def test_normalize_model_name():
    assert normalize_model_name("gpt-4o-2024-05-13") == "gpt-4o"
    assert normalize_model_name("Claude-3-5-Sonnet-20241022") == "claude-3-5-sonnet-20241022"
    assert normalize_model_name("gemini-1.5-pro-001") == "gemini-1.5-pro"
    # Open weights are not in the rate card (free-ness is decided by provider),
    # so the name comes back cleaned rather than mapped.
    assert normalize_model_name("Ollama/Llama3:8B") == "ollama/llama3:8b"


def test_calculate_cost_openai():
    # gpt-4o: $2.50 / 1M prompt, $10.00 / 1M completion
    cost = calculate_cost("openai", "gpt-4o", prompt_tokens=10_000, completion_tokens=5_000)
    # prompt: 0.010 * 2.5 = 0.025, completion: 0.005 * 10 = 0.05 => total 0.075
    assert cost == pytest.approx(0.075, rel=1e-3)


def test_calculate_cost_local_ollama():
    cost = calculate_cost("ollama", "llama3", prompt_tokens=50_000, completion_tokens=20_000)
    assert cost == 0.0


def test_calculate_cost_custom_override():
    cost = calculate_cost("custom", "any-model", prompt_tokens=100, completion_tokens=100, custom_cost_usd=0.0123)
    assert cost == 0.0123
