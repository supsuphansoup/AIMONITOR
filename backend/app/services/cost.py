"""Token cost calculation.

Two invariants this module exists to protect:

1. **A missing price is not a zero price.** When no rate is known the cost is
   reported as ``unknown`` so the UI can render "계산 불가" instead of a
   fabricated number. Guessing a mid-tier rate silently turns an unpriced model
   into a plausible-looking-but-wrong line item.
2. **Cache reads and cache writes are priced separately.** Providers charge a
   premium to write a prompt into the cache and a steep discount to read it
   back; collapsing both into the plain input rate misprices every cached call.
"""
import re
from typing import Dict, NamedTuple, Optional, Tuple

# --- Cost source markers ---------------------------------------------------
SOURCE_CUSTOM = "custom"          # caller supplied the cost outright
SOURCE_PRICING_TABLE = "pricing_table"
SOURCE_LOCAL_FREE = "local_free"  # self-hosted inference, no per-token charge
SOURCE_UNKNOWN = "unknown"        # no rate available -> cost is not computable

ALL_COST_SOURCES = frozenset(
    {SOURCE_CUSTOM, SOURCE_PRICING_TABLE, SOURCE_LOCAL_FREE, SOURCE_UNKNOWN}
)


class ModelPricing(NamedTuple):
    """USD per 1,000,000 tokens."""
    input_per_1m: float
    output_per_1m: float
    cache_write_per_1m: Optional[float] = None
    cache_read_per_1m: Optional[float] = None


class CostResult(NamedTuple):
    cost_usd: float
    source: str

    @property
    def is_computable(self) -> bool:
        return self.source != SOURCE_UNKNOWN


#: Providers whose inference runs on hardware the operator already pays for.
LOCAL_PROVIDERS = frozenset({"ollama", "local", "vllm", "llamacpp", "lmstudio"})

#: Fallback cache multipliers applied to the base input rate when a model has no
#: explicit cache pricing, following each published provider policy:
#: (cache_write_multiplier, cache_read_multiplier).
PROVIDER_CACHE_MULTIPLIERS: Dict[str, Tuple[float, float]] = {
    "anthropic": (1.25, 0.10),
    "openai": (1.00, 0.50),
    "gemini": (1.00, 0.25),
    "google": (1.00, 0.25),
}
DEFAULT_CACHE_MULTIPLIERS: Tuple[float, float] = (1.00, 1.00)

# Paid APIs only. Open-weight models are free when self-hosted, which is decided
# by provider (LOCAL_PROVIDERS), never by model name: the same weights behind a
# hosted API (mistral, deepseek, together, ...) have a price we do not know.
MODEL_PRICING_USD_PER_1M: Dict[str, ModelPricing] = {
    # OpenAI
    "gpt-4o": ModelPricing(2.50, 10.00),
    "gpt-4o-mini": ModelPricing(0.15, 0.60),
    "gpt-4o-2024-11-20": ModelPricing(2.50, 10.00),
    "gpt-4o-2024-08-06": ModelPricing(2.50, 10.00),
    "gpt-4.1": ModelPricing(2.00, 8.00),
    "gpt-4.1-mini": ModelPricing(0.40, 1.60),
    "gpt-4.1-nano": ModelPricing(0.10, 0.40),
    "gpt-4-turbo": ModelPricing(10.00, 30.00),
    "gpt-4": ModelPricing(30.00, 60.00),
    "gpt-3.5-turbo": ModelPricing(0.50, 1.50),
    "o1": ModelPricing(15.00, 60.00),
    "o1-mini": ModelPricing(3.00, 12.00),
    "o1-pro": ModelPricing(150.00, 600.00),
    "o3": ModelPricing(10.00, 40.00),
    "o3-mini": ModelPricing(1.10, 4.40),
    "o4-mini": ModelPricing(1.10, 4.40),

    # Anthropic
    "claude-3-7-sonnet": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-7-sonnet-20250219": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet-20241022": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet-20240620": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-haiku": ModelPricing(0.80, 4.00, 1.00, 0.08),
    "claude-3-5-haiku-20241022": ModelPricing(0.80, 4.00, 1.00, 0.08),
    "claude-3-opus": ModelPricing(15.00, 75.00, 18.75, 1.50),
    "claude-3-sonnet": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-haiku": ModelPricing(0.25, 1.25, 0.30, 0.03),

    # Google Gemini
    "gemini-2.5-pro": ModelPricing(1.25, 10.00),
    "gemini-2.5-flash": ModelPricing(0.075, 0.30),
    "gemini-2.5-flash-lite": ModelPricing(0.019, 0.075),
    "gemini-2.0-pro-exp": ModelPricing(1.25, 5.00),
    "gemini-2.0-flash": ModelPricing(0.10, 0.40),
    "gemini-2.0-flash-lite": ModelPricing(0.075, 0.30),
    "gemini-1.5-pro": ModelPricing(1.25, 5.00),
    "gemini-1.5-flash": ModelPricing(0.075, 0.30),
    "gemini-1.5-flash-8b": ModelPricing(0.0375, 0.15),
}

#: What may follow a pricing key for a name to still mean that model: dated
#: snapshots, numbered revisions, "-latest" aliases and Bedrock/Vertex version
#: tags. Anything else ("-preview", "-pro", ".5", "-32k") is a different model
#: with its own price, so it must not inherit this one.
_VERSION_SUFFIX = r"(?:[-@](?:\d{8}|\d{4}-\d{2}-\d{2}|\d{3,4}|latest|v\d+(?::\d+)?))*"

#: Longest key first, so "gpt-4o-mini-2024-07-18" resolves to gpt-4o-mini, not gpt-4o.
#: A key may be preceded by a routing prefix ("models/", "us.anthropic.").
_PRICING_KEY_PATTERNS = [
    (key, re.compile(r"(?:^|[/.])" + re.escape(key) + _VERSION_SUFFIX + "$"))
    for key in sorted(MODEL_PRICING_USD_PER_1M.keys(), key=len, reverse=True)
]


def normalize_model_name(name: str) -> str:
    """Map a raw model string onto a pricing key, or return it cleaned if none applies."""
    cleaned = name.strip().lower()
    for key, pattern in _PRICING_KEY_PATTERNS:
        if pattern.search(cleaned):
            return key
    return cleaned


def resolve_pricing(provider: str, model_name: str) -> Optional[ModelPricing]:
    """Look up the rate card for a call, or None when the model is unpriced."""
    if provider.strip().lower() in LOCAL_PROVIDERS:
        return ModelPricing(0.0, 0.0, 0.0, 0.0)
    return MODEL_PRICING_USD_PER_1M.get(normalize_model_name(model_name))


def cache_rates(provider: str, pricing: ModelPricing) -> Tuple[float, float]:
    """Resolve (cache_write, cache_read) rates, falling back to provider policy."""
    write_mult, read_mult = PROVIDER_CACHE_MULTIPLIERS.get(
        provider.strip().lower(), DEFAULT_CACHE_MULTIPLIERS
    )
    write_rate = (
        pricing.cache_write_per_1m
        if pricing.cache_write_per_1m is not None
        else pricing.input_per_1m * write_mult
    )
    read_rate = (
        pricing.cache_read_per_1m
        if pricing.cache_read_per_1m is not None
        else pricing.input_per_1m * read_mult
    )
    return write_rate, read_rate


def calculate_cost_detail(
    provider: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    custom_cost_usd: Optional[float] = None,
) -> CostResult:
    """Price a single call, reporting where the number came from.

    ``prompt_tokens`` counts uncached input only; cached tokens are billed
    through their own rates and must not be folded into it.
    """
    if custom_cost_usd is not None and custom_cost_usd >= 0:
        return CostResult(round(custom_cost_usd, 6), SOURCE_CUSTOM)

    if provider.strip().lower() in LOCAL_PROVIDERS:
        return CostResult(0.0, SOURCE_LOCAL_FREE)

    pricing = resolve_pricing(provider, model_name)
    if pricing is None:
        # No rate card: report as not computable rather than inventing a price.
        return CostResult(0.0, SOURCE_UNKNOWN)

    write_rate, read_rate = cache_rates(provider, pricing)

    prompt_tokens = max(0, prompt_tokens)
    completion_tokens = max(0, completion_tokens)
    cache_read_tokens = max(0, cache_read_tokens)
    cache_write_tokens = max(0, cache_write_tokens)

    total = (
        (prompt_tokens / 1_000_000.0) * pricing.input_per_1m
        + (completion_tokens / 1_000_000.0) * pricing.output_per_1m
        + (cache_write_tokens / 1_000_000.0) * write_rate
        + (cache_read_tokens / 1_000_000.0) * read_rate
    )
    return CostResult(round(total, 6), SOURCE_PRICING_TABLE)


def calculate_cost(
    provider: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    custom_cost_usd: Optional[float] = None,
) -> float:
    """Cost in USD; 0.0 when unpriced.

    Prefer `calculate_cost_detail` when the caller needs to distinguish
    "genuinely free" from "we have no rate for this model".
    """
    return calculate_cost_detail(
        provider=provider,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        custom_cost_usd=custom_cost_usd,
    ).cost_usd
