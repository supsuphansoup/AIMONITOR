"""Text for quota windows and money, shared by the main window and the widget.

One place decides how "unknown", "stale" and "estimated" read, so the two
surfaces cannot drift into showing the same number two different ways.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from desktop import theme

GREEN, BLUE, AMBER, RED, GREY = theme.SUCCESS, theme.ACCENT, theme.WARNING, theme.ERROR, theme.FAINT


def _local(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def format_reset(raw_ts: Optional[str], now: Optional[datetime] = None) -> str:
    """'리셋 04:40 (2시간 45분 뒤)' / '리셋 10/09 17:00 (4일 8시간 뒤)' / ''."""
    dt = _local(raw_ts)
    if dt is None:
        return ""
    now = (now or datetime.now(timezone.utc)).astimezone()
    mins = int((dt - now).total_seconds() // 60)
    if mins <= 0:
        return "곧 리셋"
    h, m = divmod(mins, 60)
    if h >= 24:
        return f"리셋 {dt.strftime('%m/%d %H:%M')} ({h // 24}일 {h % 24}시간 뒤)"
    return f"리셋 {dt.strftime('%H:%M')} ({f'{h}시간 {m}분' if h else f'{m}분'} 뒤)"


def format_reset_suffix(raw_ts: Optional[str]) -> str:
    """Legacy ' │ 리셋: ...' form."""
    text = format_reset(raw_ts)
    return f" │ {text}" if text else ""


def format_as_of(raw_ts: Optional[str], now: Optional[datetime] = None) -> str:
    dt = _local(raw_ts)
    if dt is None:
        return ""
    now = (now or datetime.now(timezone.utc)).astimezone()
    if dt.date() == now.date():
        return f"{dt.strftime('%H:%M')} 기준"
    return f"{dt.strftime('%m/%d %H:%M')} 기준"


def remaining_color(remaining_pct: Optional[float]) -> str:
    if remaining_pct is None:
        return theme.color(GREY)
    if remaining_pct >= 50:
        return theme.color(GREEN)
    if remaining_pct >= 25:
        return theme.color(BLUE)
    if remaining_pct >= 10:
        return theme.color(AMBER)
    return theme.color(RED)


def window_text(w: Dict[str, Any]) -> Tuple[str, str, str]:
    """(headline, color, detail) for one quota window."""
    rem = w.get("remaining_pct")
    if rem is None:
        headline = "확인 불가"
    else:
        headline = f"{rem:.0f}% 남음"
    parts = [w.get("source_label") or ""]
    as_of = format_as_of(w.get("as_of"))
    if as_of and w.get("source") != "unavailable":
        parts.append(as_of + (" (오래됨)" if w.get("stale") else ""))
    reset = format_reset(w.get("resets_at"))
    if reset:
        parts.append(reset)
    if w.get("note"):
        parts.append(w["note"])
    return headline, remaining_color(rem), " · ".join(p for p in parts if p)


def format_num(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n):,}"


def krw(amount_krw: int) -> str:
    return f"￦{amount_krw:,}"


def usage_cost_text(block: Dict[str, Any], meaning: str) -> str:
    """'￦28,428 (API 환산)' / '￦1,200' / '￦0 (로컬)' plus a 계산 불가 note."""
    if meaning == "free":
        return "￦0 (로컬)"
    text = krw(block.get("cost_krw", 0))
    if meaning == "api_equivalent":
        text += " (API 환산)"
    if block.get("cost_unknown"):
        text += f" · {block['cost_unknown']}건 계산 불가"
    return text


def spend_text(spend: Optional[Dict[str, Any]], krw_rate: float) -> Optional[str]:
    """Provider-reported spend/balance line, or None when there is nothing to say."""
    if not spend:
        return None
    if spend.get("kind") == "unavailable" or spend.get("amount") is None:
        return spend.get("source_label") or None
    amount = float(spend["amount"])
    cur = (spend.get("currency") or "USD").upper()
    if cur == "USD":
        money = f"{krw(int(round(amount * krw_rate)))} (${amount:,.2f})"
    else:
        money = f"{amount:,.2f} {cur}"
    label = "잔액" if spend.get("kind") == "balance" else "사용"
    return f"{spend.get('source_label', '')}: {label} {money}"


# --------------------------------------------------------------------------- widget
SHORT_NAMES = {
    "claude": "Claude",
    "antigravity": "Antigravity",
    "copilot": "Copilot",
    "codex": "Codex",
    "cursor": "Cursor",
    "windsurf": "Windsurf",
    "ollama": "Ollama",
}

SOURCE_TAGS = {"official": "공식", "user_limit": "직접 입력 한도", "unavailable": "확인 불가"}


def short_name(item: Dict[str, Any]) -> str:
    key = item.get("key", "")
    if key in SHORT_NAMES:
        return SHORT_NAMES[key]
    if key.endswith("_api"):
        return f"{key[:-4].capitalize()} API"
    return str(item.get("name") or key)[:12]


def widget_entries(summary: Dict[str, Any]) -> list:
    """Every item the widget can show, as dicts with id/group/label/value/color/hint.

    The id is stable across refreshes so a user's selection survives restarts.
    """
    out = []
    for it in summary.get("items", []):
        key, name, meaning = it["key"], short_name(it), it.get("cost_meaning", "billed")
        for w in it.get("quota_windows", []):
            headline, color, detail = window_text(w)
            out.append(
                {
                    "id": f"{key}:{w['id']}",
                    "group": name,
                    "label": w["label"],
                    "value": headline,
                    "color": color,
                    "hint": SOURCE_TAGS.get(w.get("source"), ""),
                    "reset": format_reset(w.get("resets_at")),
                    "detail": detail,
                    "default": True,
                }
            )
        ctx = it.get("context")
        if ctx:
            out.append(
                {
                    "id": f"{key}:context",
                    "group": name,
                    "label": "컨텍스트",
                    "value": f"{format_num(ctx['tokens'])}/{format_num(ctx['max_tokens'])}",
                    "color": remaining_color(100 - ctx["used_pct"]),
                    "hint": "추정",
                    "reset": "",
                    "detail": "마지막 요청의 입력 토큰 ÷ 모델 최대치",
                    "default": False,
                }
            )
        usage = it.get("usage") or {}
        for window, label in (("today", "오늘 비용"), ("month", "이번 달 비용")):
            block = usage.get(window)
            if block is None:
                continue
            out.append(
                {
                    "id": f"{key}:{window}",
                    "group": name,
                    "label": label,
                    "value": usage_cost_text(block, meaning),
                    "color": theme.color(theme.TEXT),
                    "hint": "API 환산" if meaning == "api_equivalent" else "",
                    "reset": "",
                    "detail": f"{block.get('requests', 0):,}회 요청",
                    "default": meaning == "billed" and window == "month",
                }
            )
    tot = summary.get("total") or {}
    if tot:
        unknown = f" · {tot['billed_unknown_month']}건 계산 불가" if tot.get("billed_unknown_month") else ""
        out.append(
            {
                "id": "total:billed_month",
                "group": "합계",
                "label": "이번 달 실제 청구",
                "value": krw(tot.get("billed_month_krw", 0)) + unknown,
                "color": theme.color(theme.TEXT),
                "hint": "API 종량제",
                "reset": "",
                "detail": "",
                "default": True,
            }
        )
        out.append(
            {
                "id": "total:equiv_month",
                "group": "합계",
                "label": "구독 사용 API 환산",
                "value": krw(tot.get("api_equivalent_month_krw", 0)),
                "color": theme.color(GREY),
                "hint": "청구 아님",
                "reset": "",
                "detail": "",
                "default": False,
            }
        )
    return out


def select_entries(entries: list, selected: Optional[list]) -> list:
    """Entries the user picked (in display order); the defaults if they never picked."""
    if selected is None:
        return [e for e in entries if e["default"]]
    chosen = set(selected)
    return [e for e in entries if e["id"] in chosen]
