"""Per-AI summary for the desktop app: remaining quota, context estimate, spend.

Nothing here invents a limit. Remaining-usage numbers come from desktop.quota,
which only reports what a vendor (or a limit the user typed) actually says.
Money is split by what it means:

* ``billed``          - pay-as-you-go API traffic; this is what the card owner pays.
* ``api_equivalent``  - subscription traffic priced at API rates. Not charged;
                        shown as "API 환산" so it is never added to the bill.
* ``free``            - local models.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from desktop.quota import windows_for


def hidden_ai_keys(config: Optional[Dict[str, Any]]) -> List[str]:
    """AI card keys the user chose not to see (config ``hidden_ais``)."""
    raw = (config or {}).get("hidden_ais")
    if not isinstance(raw, list):
        return []
    return [str(k) for k in raw]


# Context window per model family, in tokens. Used only for the context estimate.
MODEL_CONTEXT_WINDOWS: Dict[str, int] = {
    # Claude Code has been observed past 900K tokens on these models.
    "claude-fable": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude": 200_000,
    "gemini-3": 1_048_576,
    "gemini-2.5": 1_048_576,
    "gemini": 1_048_576,
    "gpt-5": 400_000,
    "gpt-4.1": 1_047_576,
    "gpt-4o": 128_000,
    "o3": 200_000,
    "o4": 200_000,
    "llama3.1": 131_072,
}

DEFAULT_CONTEXT_WINDOW = 200_000

SUBSCRIPTION_KEYS = {"claude", "antigravity", "copilot", "codex", "cursor", "windsurf"}
LOCAL_KEYS = {"ollama"}

USAGE_WINDOWS = ("5h", "today", "7d", "month")


def get_context_window(model_name: str) -> int:
    """Longest matching prefix wins, so claude-opus-5-5 resolves before claude."""
    name = (model_name or "").lower()
    best = ""
    for key in MODEL_CONTEXT_WINDOWS:
        if key in name and len(key) > len(best):
            best = key
    return MODEL_CONTEXT_WINDOWS[best] if best else DEFAULT_CONTEXT_WINDOW


def cost_meaning(ai: Dict[str, Any]) -> str:
    key = ai.get("key", "")
    if key in LOCAL_KEYS:
        return "free"
    if key == "claude" and "종량제" in (ai.get("plan_name") or ""):
        return "billed"  # Claude Code signed in with an API key, not a subscription
    if key in SUBSCRIPTION_KEYS:
        return "api_equivalent"
    return "billed"


def _usage_block(raw: Optional[Dict[str, Any]], krw_rate: float) -> Dict[str, Any]:
    raw = raw or {}
    out = {
        "requests": int(raw.get("requests") or 0),
        "tokens": int(raw.get("tokens") or 0),
        "cost_usd": float(raw.get("cost_usd") or 0.0),
        "cost_unknown": int(raw.get("cost_unknown") or 0),
    }
    out["cost_krw"] = int(round(out["cost_usd"] * krw_rate))
    return out


def _context_block(act: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tokens = act.get("context_tokens")
    if tokens is None:
        return None
    model = act.get("model_name") or ""
    max_tokens = get_context_window(model)
    return {
        "tokens": int(tokens),
        "max_tokens": max_tokens,
        "used_pct": round(min(100.0, tokens / max_tokens * 100.0), 1) if max_tokens else None,
        "model_name": model,
        # The log has no context window size; this is tokens / known window.
        "estimated": True,
        "as_of": act.get("timestamp"),
        "is_active": bool(act.get("is_active")),
        "mins_ago": act.get("mins_ago"),
    }


def _session_status(act: Optional[Dict[str, Any]]) -> str:
    if not act or act.get("mins_ago") is None:
        return "이 PC에 수집된 사용 기록 없음"
    mins = int(act["mins_ago"])
    if mins <= 15:
        return "🟢 사용 중 (최근 15분 내 요청)"
    if mins < 60:
        return f"⚪ 대기 중 ({mins}분 전 마지막 요청)"
    if mins < 60 * 24:
        return f"⚪ 대기 중 ({mins // 60}시간 전 마지막 요청)"
    return f"⚪ 대기 중 ({mins // 1440}일 전 마지막 요청)"


def _billing_note(meaning: str, ai: Dict[str, Any], month: Dict[str, Any]) -> str:
    if meaning == "free":
        return "로컬 실행 · 요금 없음"
    if meaning == "api_equivalent":
        note = f"{ai.get('plan_name') or '구독'}에 포함 · 금액은 API 단가 환산 (실제 청구 아님)"
        if ai.get("key") == "antigravity":
            note += " · 토큰은 글자 수 추정"
        return note
    if month["cost_unknown"]:
        return f"API 종량제 · 단가 없는 요청 {month['cost_unknown']}건은 계산 불가"
    return "API 종량제 · 이 PC에서 기록된 요청 기준"


def get_connected_ai_summary(
    active_ais: List[Dict[str, Any]],
    usage_by_ai: Optional[Dict[str, Any]] = None,
    krw_rate: float = 1380.0,
    config: Optional[Dict[str, Any]] = None,
    quota_overrides: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    official_spend: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    usage_available: bool = True,
) -> Dict[str, Any]:
    """Build one card per detected AI.

    ``usage_by_ai`` is MetricAggregator.get_active_contexts(); ``quota_overrides``
    lets tests and callers supply windows instead of reading vendor sources;
    ``official_spend`` maps an API card key to the provider's own spend report.
    """
    usage_by_ai = usage_by_ai or {}
    quota_overrides = quota_overrides or {}
    official_spend = official_spend or {}
    hidden_keys = set(hidden_ai_keys(config))

    items: List[Dict[str, Any]] = []
    hidden: List[Dict[str, Any]] = []
    seen = set()
    for ai in active_ais:
        key = ai["key"]
        if key in seen:
            continue
        seen.add(key)
        if key in hidden_keys:
            # 사용자가 숨긴 AI: 카드·위젯·합계 어디에도 넣지 않는다 (합계에는 몇 개 뺐는지만 남긴다).
            hidden.append({"key": key, "display_name": ai.get("display_name", key)})
            continue
        act = usage_by_ai.get(key) or {}
        windows = act.get("windows") or {}
        usage = {w: _usage_block(windows.get(w), krw_rate) for w in USAGE_WINDOWS}
        meaning = cost_meaning(ai)
        quota = quota_overrides.get(key)
        if quota is None:
            quota = windows_for(key, config=config, now=now) if ai.get("use_default_quota", True) else []

        item = {
            "key": key,
            "name": ai.get("name", key),
            "display_name": ai.get("display_name", key),
            "provider": ai.get("provider", ""),
            "plan_name": ai.get("plan_name", ""),
            "detail": ai.get("detail", ""),
            "badge_color": ai.get("badge_color", "#64748b"),
            "path": ai.get("path", ""),
            "cost_meaning": meaning,
            "quota_windows": quota,
            "context": _context_block(act) if act else None,
            "usage": usage,
            "last_request_at": act.get("timestamp"),
            "session_status": _session_status(act),
            "billing_note": _billing_note(meaning, ai, usage["month"]),
            "official_spend": official_spend.get(key),
        }
        items.append(item)

    def _sum(meaning: str, window: str, field: str) -> float:
        return sum(it["usage"][window][field] for it in items if it["cost_meaning"] == meaning)

    billed_today = _sum("billed", "today", "cost_usd")
    billed_month = _sum("billed", "month", "cost_usd")
    equiv_month = _sum("api_equivalent", "month", "cost_usd")
    total = {
        "krw_rate": krw_rate,
        "connected_count": len(items),
        "hidden_count": len(hidden),
        "billed_today_usd": round(billed_today, 6),
        "billed_today_krw": int(round(billed_today * krw_rate)),
        "billed_month_usd": round(billed_month, 6),
        "billed_month_krw": int(round(billed_month * krw_rate)),
        "billed_unknown_month": int(_sum("billed", "month", "cost_unknown")),
        "api_equivalent_month_usd": round(equiv_month, 6),
        "api_equivalent_month_krw": int(round(equiv_month * krw_rate)),
        "requests_today": int(sum(it["usage"]["today"]["requests"] for it in items)),
        "tokens_today": int(sum(it["usage"]["today"]["tokens"] for it in items)),
    }
    if not usage_available:
        for item in items:
            item.update(usage={}, context=None, session_status="사용량 확인 불가")
        for field in total:
            if field not in ("krw_rate", "connected_count", "hidden_count"):
                total[field] = None
    return {"items": items, "by_key": {it["key"]: it for it in items}, "total": total, "hidden": hidden,
            "usage_available": usage_available}
