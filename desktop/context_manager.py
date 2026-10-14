"""Per-AI summary for the desktop app: remaining quota, context estimate, spend.

Nothing here invents a limit. Remaining-usage numbers come from desktop.quota,
which only reports what a vendor (or a limit the user typed) actually says.
Money is split by what it means:

* ``billed``          - pay-as-you-go API traffic; this is what the card owner pays.
* ``api_equivalent``  - subscription traffic priced at API rates. Not charged;
                        shown as "API 환산" so it is never added to the bill.
* ``free``            - local models.
* ``unknown``         - billing mode is unproven or changed inside the window.
* ``mixed``           - a tool total contains several account billing meanings.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.app.core.timeutil import local_period_start_utc, to_aware_utc, utc_now
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
    if key in ("claude", "codex") and "auth_mode" in ai:
        return {"subscription": "api_equivalent", "api": "billed"}.get(ai["auth_mode"], "unknown")
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
    if raw.get("since"):
        out["since"] = raw["since"]
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
    if meaning == "unknown":
        return "구분 미상 · 로그인 근거 없음 또는 기간 중 방식 변경 · 청구·구독 환산 합계와 별도"
    if meaning == "mixed":
        return "계정별 청구 구분 혼합 · API 종량제·구독 환산·구분 미상은 따로 합산"
    if meaning == "api_equivalent":
        note = f"{ai.get('plan_name') or '구독'}에 포함 · 금액은 API 단가 환산 (실제 청구 아님)"
        if ai.get("key") == "antigravity":
            note += " · 토큰은 글자 수 추정"
        return note
    if month["cost_unknown"]:
        return f"API 종량제 · 단가 없는 요청 {month['cost_unknown']}건은 계산 불가"
    return "API 종량제 · 이 PC에서 기록된 요청 기준"


def _cost_totals(items: List[Dict[str, Any]], rate: float) -> Dict[str, Any]:
    # Account rows replace their tool's subtotal; they are never added on top of it.
    sources = [account for item in items for account in (
        item.get("accounts") or [item] if item["key"] in ("claude", "codex") else [item]
    )]

    def _sum(meaning: str, window: str, field: str) -> float:
        return sum((block.get(field) or 0) for item in sources
                   if (block := (item.get("usage") or {}).get(window, {})).get(
                       "cost_meaning", item.get("cost_meaning", cost_meaning(item))) == meaning)

    total = {}
    for meaning, prefix, windows in (("billed", "billed", ("today", "month")),
                                     ("api_equivalent", "api_equivalent", ("month",)),
                                     ("unknown", "unclassified", ("today", "month"))):
        for window in windows:
            amount = _sum(meaning, window, "cost_usd")
            total[f"{prefix}_{window}_usd"] = round(amount, 6)
            total[f"{prefix}_{window}_krw"] = int(round(amount * rate))
    total["billed_unknown_month"] = int(_sum("billed", "month", "cost_unknown"))
    total["unclassified_unknown_month"] = int(_sum("unknown", "month", "cost_unknown"))
    total["unclassified_month_requests"] = int(_sum("unknown", "month", "requests"))
    return total


def apply_account_billing(summary: Dict[str, Any], billing: Dict[tuple, Dict[str, Any]],
                          now: Optional[datetime] = None) -> Dict[str, Any]:
    """Classify account windows from folder evidence and recalculate cost totals once."""
    from desktop.accounts import _timestamp

    now = to_aware_utc(now or utc_now())
    starts = {"5h": now - timedelta(hours=5), "today": to_aware_utc(local_period_start_utc(now)),
              "7d": now - timedelta(days=7), "month": to_aware_utc(local_period_start_utc(now, True))}
    for item in summary.get("items", []):
        key = item["key"]
        if key not in ("claude", "codex"):
            continue
        accounts = item.get("accounts") or [item]
        for account in accounts:
            info = billing.get((key, account.get("account_id")), {})
            if account is item:
                info = {"auth_mode": item.get("auth_mode", "unknown"), "plan_name": item["plan_name"]}
            mode = info.get("auth_mode", "unknown")
            account["auth_mode"] = mode
            account["plan_name"] = info.get("plan_name", "플랜 확인 불가")
            meaning = {"subscription": "api_equivalent", "api": "billed"}.get(mode, "unknown")
            changed = _timestamp(info.get("auth_mode_changed_at"))
            for window, block in (account.get("usage") or {}).items():
                start = _timestamp(block.get("since")) or starts[window]
                block["cost_meaning"] = "unknown" if changed and start <= changed <= now else meaning
            account["cost_meaning"] = (account.get("usage") or {}).get("month", {}).get(
                "cost_meaning", meaning)
            account["billing_note"] = _billing_note(account["cost_meaning"], account, {
                "cost_unknown": (account.get("usage") or {}).get("month", {}).get("cost_unknown", 0)})
        if accounts[0] is not item:
            for window, block in (item.get("usage") or {}).items():
                meanings = {(account.get("usage") or {}).get(window, {}).get(
                    "cost_meaning", account["cost_meaning"]) for account in accounts}
                block["cost_meaning"] = next(iter(meanings)) if len(meanings) == 1 else "mixed"
            meanings = {account["cost_meaning"] for account in accounts}
            item["cost_meaning"] = next(iter(meanings)) if len(meanings) == 1 else "mixed"
            modes = {account["auth_mode"] for account in accounts}
            item["auth_mode"] = next(iter(modes)) if len(modes) == 1 else "unknown"
            plans = {account["plan_name"] for account in accounts}
            if len(plans) == 1 and "플랜 확인 불가" not in plans:
                item["plan_name"] = next(iter(plans))
            elif item["cost_meaning"] == "mixed":
                item["plan_name"] = "계정별 청구 구분 혼합"
            item["billing_note"] = _billing_note(item["cost_meaning"], item, {
                "cost_unknown": (item.get("usage") or {}).get("month", {}).get("cost_unknown", 0)})
    if summary.get("usage_available", True):
        rate = summary.get("total", {}).get("krw_rate", 1380.0)
        summary.setdefault("total", {}).update(_cost_totals(summary.get("items", []), rate))
    return summary


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
        for block in usage.values():
            block["cost_meaning"] = meaning
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
        if "auth_mode" in ai:
            item["auth_mode"] = ai["auth_mode"]
        items.append(item)

    total = {
        "krw_rate": krw_rate,
        "connected_count": len(items),
        "hidden_count": len(hidden),
        **_cost_totals(items, krw_rate),
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
