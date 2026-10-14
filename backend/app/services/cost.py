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
import math
import re
from typing import Dict, NamedTuple, Optional, Tuple

# --- Cost source markers ---------------------------------------------------
SOURCE_CUSTOM = "custom"          # caller supplied the cost outright
SOURCE_PRICING_TABLE = "pricing_table"
SOURCE_LOCAL_FREE = "local_free"  # self-hosted inference, no per-token charge
SOURCE_UNKNOWN = "unknown"        # no rate available -> cost is not computable

class ModelPricing(NamedTuple):
    """USD per 1,000,000 tokens."""
    input_per_1m: float
    output_per_1m: float
    cache_write_per_1m: Optional[float] = None
    cache_read_per_1m: Optional[float] = None
    cache_write_1h_per_1m: Optional[float] = None


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

#: When the published prices below were last checked against the sources.
#: Bump it together with the table; /system/pricing reports it so a stale card
#: is visible rather than silently wrong.
PRICING_AS_OF = "2026-10-14"
PRICING_SOURCES: Dict[str, str] = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "gemini_changelog": "https://ai.google.dev/gemini-api/docs/changelog",
    "gemini_flash_8b": (
        "https://developers.googleblog.com/en/gemini-15-flash-8b-is-now-generally-available-for-use/"
    ),
    "openai_gpt_6_sol": "https://developers.openai.com/api/docs/models/gpt-6-sol",
    "openai_gpt_5_6_terra": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
    "openai_gpt_5_6_luna": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
    "openai_o1_mini": "https://developers.openai.com/api/docs/models/o1-mini",
}


def _openai(
    input_rate: float, output_rate: float, cached: Optional[float], write: Optional[float] = None,
) -> ModelPricing:
    """Use each model's published cache rates; older models write at the input rate."""
    return ModelPricing(input_rate, output_rate, input_rate if write is None else write,
                        input_rate if cached is None else cached)


def _anthropic(input_rate: float, output_rate: float, write_5m: float, read: float) -> ModelPricing:
    # PRICING_SOURCES["anthropic"], verified 2026-10-14: 1-hour writes cost 2x input.
    return ModelPricing(input_rate, output_rate, write_5m, read, input_rate * 2)


def _gemini(input_rate: float, output_rate: float, cached: float) -> ModelPricing:
    # Prompt rates are the <=200k-token tier; hourly cache storage is not modelled.
    return ModelPricing(input_rate, output_rate, input_rate, cached)


# Paid APIs only. Open-weight models are free when self-hosted, which is decided
# by provider (LOCAL_PROVIDERS), never by model name: the same weights behind a
# hosted API (mistral, deepseek, together, ...) have a price we do not know.
#
# A dated snapshot only inherits its family's price when it has no key of its
# own - some snapshots are priced differently (gpt-4o-2024-05-13), so list those.
MODEL_PRICING_USD_PER_1M: Dict[str, ModelPricing] = {
    # --- OpenAI, verified PRICING_AS_OF (Standard tier; "<272K" models at the short-context rate)
    "gpt-6-astra": _openai(10.00, 50.00, 1.00, write=12.50),
    "gpt-6.1-sol": _openai(2.00, 10.00, 0.10, write=2.50),
    "gpt-6-sol": _openai(2.00, 10.00, 0.20, write=2.50),
    "gpt-6-luna": _openai(0.10, 0.50, 0.01, write=0.125),
    "gpt-5.6-sol": _openai(4.00, 20.00, 0.40, write=5.00),
    "gpt-5.6-terra": _openai(2.00, 12.00, 0.20, write=2.50),
    "gpt-5.6-luna": _openai(0.20, 1.20, 0.02, write=0.25),
    "gpt-5.5": _openai(5.00, 30.00, 0.50),
    "gpt-5.5-pro": _openai(30.00, 180.00, None),
    "gpt-5.4": _openai(2.50, 15.00, 0.25),
    "gpt-5.4-mini": _openai(0.75, 4.50, 0.075),
    "gpt-5.4-nano": _openai(0.20, 1.25, 0.02),
    "gpt-5.4-pro": _openai(30.00, 180.00, None),
    "gpt-5.2": _openai(1.75, 14.00, 0.175),
    "gpt-5.2-pro": _openai(21.00, 168.00, None),
    "gpt-5.1": _openai(1.25, 10.00, 0.125),
    "gpt-5": _openai(1.25, 10.00, 0.125),
    "gpt-5-mini": _openai(0.25, 2.00, 0.025),
    "gpt-5-nano": _openai(0.05, 0.40, 0.005),
    "gpt-5-pro": _openai(15.00, 120.00, None),
    "gpt-4.1": _openai(2.00, 8.00, 0.50),
    "gpt-4.1-mini": _openai(0.40, 1.60, 0.10),
    "gpt-4.1-nano": _openai(0.10, 0.40, 0.025),
    "gpt-4o": _openai(2.50, 10.00, 1.25),
    "gpt-4o-2024-05-13": _openai(5.00, 15.00, None),
    "gpt-4o-mini": _openai(0.15, 0.60, 0.075),
    "o1": _openai(15.00, 60.00, 7.50),
    "o1-pro": _openai(150.00, 600.00, None),
    "o3-pro": _openai(20.00, 80.00, None),
    "o3": _openai(2.00, 8.00, 0.50),
    "o4-mini": _openai(1.10, 4.40, 0.275),
    "o3-mini": _openai(1.10, 4.40, 0.55),
    "o1-mini": _openai(1.10, 4.40, 0.55),
    "gpt-4-turbo": _openai(10.00, 30.00, None),          # gpt-4-turbo-2024-04-09
    "gpt-4": _openai(30.00, 60.00, None),                # gpt-4-0613
    "gpt-3.5-turbo": _openai(0.50, 1.50, None),
    "gpt-3.5-turbo-1106": _openai(1.00, 2.00, None),
    "gpt-3.5-turbo-instruct": _openai(1.50, 2.00, None),
    "davinci-002": _openai(2.00, 2.00, None),
    "babbage-002": _openai(0.40, 0.40, None),
    # OpenAI, retired or unlisted - kept at their last known price, not re-verified
    "gpt-4o-2024-11-20": _openai(2.50, 10.00, 1.25),
    "gpt-4o-2024-08-06": _openai(2.50, 10.00, 1.25),

    # --- Anthropic, verified PRICING_AS_OF (first-party API; Bedrock/Vertex regional endpoints differ)
    "claude-fable-5-1": _anthropic(10.00, 50.00, 12.50, 0.25),
    "claude-mythos-5-1": _anthropic(10.00, 50.00, 12.50, 0.25),
    "claude-fable-5": _anthropic(10.00, 50.00, 12.50, 1.00),
    "claude-mythos-5": _anthropic(10.00, 50.00, 12.50, 1.00),
    "claude-opus-5-5": _anthropic(4.00, 20.00, 5.00, 0.20),
    "claude-opus-5": _anthropic(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-8": _anthropic(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-7": _anthropic(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-6": _anthropic(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-5": _anthropic(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-1": _anthropic(15.00, 75.00, 18.75, 1.50),
    "claude-opus-4": _anthropic(15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-5-5": _anthropic(2.00, 10.00, 2.50, 0.10),
    "claude-sonnet-5": _anthropic(2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-4-6": _anthropic(3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-5": _anthropic(3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4": _anthropic(3.00, 15.00, 3.75, 0.30),
    "claude-haiku-4-5": _anthropic(1.00, 5.00, 1.25, 0.10),
    "claude-3-5-haiku": _anthropic(0.80, 4.00, 1.00, 0.08),
    "claude-3-5-haiku-20241022": _anthropic(0.80, 4.00, 1.00, 0.08),
    # Anthropic, retired - kept at their last known price, not re-verified
    "claude-3-7-sonnet": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-7-sonnet-20250219": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet-20241022": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-5-sonnet-20240620": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-opus": ModelPricing(15.00, 75.00, 18.75, 1.50),
    "claude-3-sonnet": ModelPricing(3.00, 15.00, 3.75, 0.30),
    "claude-3-haiku": ModelPricing(0.25, 1.25, 0.30, 0.03),

    # --- Google Gemini, verified PRICING_AS_OF (paid tier, text input)
    # 3.6-3.8 Flash are at promotional rates through 2026-12-31; they double on
    # 2027-01-01, so this card must be refreshed before then.
    "gemini-3.8-flash": _gemini(0.75, 3.75, 0.075),
    "gemini-3.7-flash": _gemini(0.75, 3.75, 0.075),
    "gemini-3.6-flash": _gemini(0.75, 3.75, 0.075),
    # gemini-3.5-flash: routing to 3.6 is documented, alias billing is not confirmed.
    "gemini-3.5-flash-lite": _gemini(0.30, 2.50, 0.03),
    "gemini-3.1-flash-lite": _gemini(0.25, 1.50, 0.025),
    "gemini-3.1-pro-preview": _gemini(2.00, 12.00, 0.20),
    "gemini-2.5-pro": _gemini(1.25, 10.00, 0.125),
    "gemini-2.5-flash": _gemini(0.30, 2.50, 0.03),
    # Gemini, retired or unlisted - kept at their last known price, not re-verified
    "gemini-2.0-flash": ModelPricing(0.10, 0.40),
    "gemini-2.0-flash-lite": ModelPricing(0.075, 0.30),
    "gemini-1.5-pro": ModelPricing(1.25, 5.00),
    "gemini-1.5-flash": ModelPricing(0.075, 0.30),
    # Historical <128K price verified in PRICING_SOURCES["gemini_flash_8b"].
    "gemini-1.5-flash-8b": _gemini(0.0375, 0.15, 0.01),
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


def _is_finite(value: float) -> bool:
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def calculate_cost_detail(
    provider: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    custom_cost_usd: Optional[float] = None,
    cache_write_1h_tokens: int = 0,
) -> CostResult:
    """Price a single call, reporting where the number came from.

    ``prompt_tokens`` counts uncached input only; cached tokens are billed
    through their own rates and must not be folded into it.
    """
    inputs = (prompt_tokens, completion_tokens, cache_read_tokens, cache_write_tokens, cache_write_1h_tokens)
    if not all(_is_finite(value) for value in inputs):
        return CostResult(0.0, SOURCE_UNKNOWN)
    if custom_cost_usd is not None:
        if not _is_finite(custom_cost_usd):
            return CostResult(0.0, SOURCE_UNKNOWN)
        if custom_cost_usd >= 0:
            return CostResult(round(custom_cost_usd, 12), SOURCE_CUSTOM)

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

    cache_write_1h_tokens = min(max(0, cache_write_1h_tokens), cache_write_tokens)
    if cache_write_1h_tokens and pricing.cache_write_1h_per_1m is None:
        return CostResult(0.0, SOURCE_UNKNOWN)

    total = (
        (prompt_tokens / 1_000_000.0) * pricing.input_per_1m
        + (completion_tokens / 1_000_000.0) * pricing.output_per_1m
        + ((cache_write_tokens - cache_write_1h_tokens) / 1_000_000.0) * write_rate
        + (cache_write_1h_tokens / 1_000_000.0) * (pricing.cache_write_1h_per_1m or 0.0)
        + (cache_read_tokens / 1_000_000.0) * read_rate
    )
    if not _is_finite(total):
        return CostResult(0.0, SOURCE_UNKNOWN)
    return CostResult(round(total, 12), SOURCE_PRICING_TABLE)


def calculate_cost(
    provider: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    custom_cost_usd: Optional[float] = None,
    cache_write_1h_tokens: int = 0,
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
        cache_write_1h_tokens=cache_write_1h_tokens,
    ).cost_usd
