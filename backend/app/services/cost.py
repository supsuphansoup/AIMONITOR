from typing import Dict, Tuple, Optional

# Cost per 1,000,000 tokens (USD) -> (Input Price, Output Price)
MODEL_PRICING_USD_PER_1M: Dict[str, Tuple[float, float]] = {
    # OpenAI — 2024~2026
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-2024-11-20": (2.50, 10.00),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o1-mini": (3.00, 12.00),
    "o1-pro": (150.00, 600.00),
    "o3": (10.00, 40.00),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),

    # Anthropic — Claude 3 & 3.5 & 3.7
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-3-7-sonnet-20250219": (3.00, 15.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-sonnet-20240620": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),

    # Google Gemini — 2024~2026
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-flash-lite": (0.019, 0.075),
    "gemini-2.0-pro-exp": (1.25, 5.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-flash-8b": (0.0375, 0.15),

    # Open source / Local models (Ollama, vLLM, etc.)
    "ollama": (0.0, 0.0),
    "llama3": (0.0, 0.0),
    "llama3.1": (0.0, 0.0),
    "llama3.2": (0.0, 0.0),
    "llama3.3": (0.0, 0.0),
    "llama4": (0.0, 0.0),
    "qwen": (0.0, 0.0),
    "qwen2": (0.0, 0.0),
    "qwen3": (0.0, 0.0),
    "mistral": (0.0, 0.0),
    "mixtral": (0.0, 0.0),
    "phi3": (0.0, 0.0),
    "phi4": (0.0, 0.0),
    "deepseek": (0.0, 0.0),
}


def normalize_model_name(name: str) -> str:
    """Normalize model string to standard key, matching longest keys first."""
    cleaned = name.strip().lower()
    # Sort keys by length in descending order to match specific model versions first
    sorted_keys = sorted(MODEL_PRICING_USD_PER_1M.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in cleaned:
            return key
    return cleaned


def calculate_cost(
    provider: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    custom_cost_usd: Optional[float] = None
) -> float:
    """
    Calculate estimated cost in USD based on model pricing per 1M tokens.
    If custom_cost_usd is provided, returns it directly.
    """
    if custom_cost_usd is not None and custom_cost_usd >= 0:
        return round(custom_cost_usd, 6)

    provider_lower = provider.strip().lower()
    if provider_lower in ("ollama", "local", "vllm"):
        return 0.0

    normalized_model = normalize_model_name(model_name)
    pricing = MODEL_PRICING_USD_PER_1M.get(normalized_model)

    if not pricing:
        # Fallback default estimate (approx. mid-tier: $1.00 / $3.00 per 1M)
        input_price_per_1m, output_price_per_1m = 1.00, 3.00
    else:
        input_price_per_1m, output_price_per_1m = pricing

    prompt_tokens = max(0, prompt_tokens)
    completion_tokens = max(0, completion_tokens)

    prompt_cost = (prompt_tokens / 1_000_000.0) * input_price_per_1m
    completion_cost = (completion_tokens / 1_000_000.0) * output_price_per_1m
    
    total_cost = prompt_cost + completion_cost
    return round(total_cost, 6)
