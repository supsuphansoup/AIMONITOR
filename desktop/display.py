"""Text for quota windows and money, shared by the main window and the widget.

One place decides how "unknown", "stale" and "estimated" read, so the two
surfaces cannot drift into showing the same number two different ways.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from desktop import theme
from desktop.accounts import AccountRegistry

GREEN, BLUE, AMBER, RED, GREY = theme.SUCCESS, theme.ACCENT, theme.WARNING, theme.ERROR, theme.FAINT


def _api_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (isinstance(value, int) or math.isfinite(value)))


def _api_counts(row: Any, fields: tuple) -> bool:
    return isinstance(row, dict) and all(
        isinstance(row.get(field), int) and not isinstance(row[field], bool) and row[field] >= 0
        for field in fields)


def _api_usage(row: Any) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("windows"), dict):
        return False
    return all(_api_counts(row["windows"].get(name), ("tokens", "cost_unknown"))
               and _api_number(row["windows"][name].get("requests"))
               and row["windows"][name]["requests"] >= 0
               and _api_number(row["windows"][name].get("cost_usd"))
               for name in ("5h", "today", "7d", "month"))


def valid_api_payload(endpoint: str, data: Any) -> bool:
    """Check the structure the desktop consumes, while allowing valid empty/legacy responses."""
    if endpoint == "overview":
        return (_api_counts(data, ("total_requests", "total_tokens"))
                and _api_number(data.get("total_cost_usd")))
    if endpoint == "breakdown":
        return isinstance(data, list) and all(
            _api_counts(row, ("count", "total_tokens")) and _api_number(row.get("total_cost_usd"))
            and ("cost_unknown_count" not in row or _api_counts(row, ("cost_unknown_count",)))
            and all(isinstance(row.get(field), str) for field in ("provider", "model_name")) for row in data)
    if endpoint == "traces":
        rows = data.get("items") if isinstance(data, dict) else data
        return isinstance(rows, list) and all(
            _api_counts(row, ("total_tokens",)) and _api_number(row.get("cost_usd"))
            and all(isinstance(row.get(field), str) for field in ("trace_id", "provider", "model_name",
                                                                  "timestamp", "status")) for row in rows)
    if endpoint == "active_contexts":
        return isinstance(data, dict) and all(
            isinstance(key, str) and _api_usage(row)
            and ("accounts" not in row or (isinstance(row["accounts"], list)
                                           and all(_api_usage(account) for account in row["accounts"])))
            for key, row in data.items())
    if endpoint == "spend":
        if not isinstance(data, dict):
            return False
        cards = [value for key, value in data.items() if key.endswith("_api")]
        if "items" in data:
            if not isinstance(data["items"], list):
                return False
            cards += data["items"]
        elif not cards:
            return False
        for row in cards:
            if not isinstance(row, dict) or row.get("kind") not in ("spend", "balance", "unavailable"):
                return False
            if "source_label" in row and not isinstance(row["source_label"], str):
                return False
            if row["kind"] != "unavailable":
                currency = row.get("currency")
                if (not _api_number(row.get("amount")) or not isinstance(currency, str)
                        or len(currency) != 3 or not currency.isascii() or not currency.isalpha()):
                    return False
        return True
    return False


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
    if w.get("partial"):
        parts.append("일부 데이터")
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
    meaning = block.get("cost_meaning", meaning)
    if meaning == "free":
        return "￦0 (로컬)"
    if not block or block.get("cost_krw") is None:
        return "계산 불가" if block.get("cost_unknown") else "기록 없음"
    if block.get("cost_unknown") and block.get("cost_unknown") == block.get(
        "record_count", block.get("requests"),
    ):
        return "계산 불가"
    text = krw(block.get("cost_krw", 0))
    if meaning == "api_equivalent":
        text += " (API 환산)"
    elif meaning == "unknown":
        text += " (구분 미상)"
    elif meaning == "mixed":
        text += " (계정별 구분 혼합)"
    if block.get("cost_unknown"):
        text += f" · {block['cost_unknown']}건 계산 불가"
    return text


def unclassified_total_text(total: Dict[str, Any]) -> Optional[str]:
    if not (total.get("unclassified_month_requests") or total.get("unclassified_month_usd")
            or total.get("unclassified_unknown_month")):
        return None
    unknown = total.get("unclassified_unknown_month") or 0
    if unknown == total.get("unclassified_month_requests") and unknown:
        return "계산 불가"
    text = krw(total.get("unclassified_month_krw") or 0)
    return f"{text} · {unknown}건 계산 불가" if unknown else text


def _spend_status_text(text: Optional[str], spend: Dict[str, Any], now: Optional[datetime]) -> Optional[str]:
    if not spend.get("fetch_failed"):
        return text
    as_of = format_as_of(spend.get("last_success_at") or spend.get("as_of"), now)
    reference = f"마지막 성공 {as_of}" if as_of else "마지막 성공 시각 확인 불가"
    return f"{text or '공식 사용액 확인 불가'} · 갱신 실패 · 이전 값 · {reference}"


def spend_text(spend: Optional[Dict[str, Any]], krw_rate: float,
               now: Optional[datetime] = None) -> Optional[str]:
    """Provider-reported spend/balance line, or None when there is nothing to say."""
    if not spend:
        return None
    if spend.get("kind") == "unavailable" or spend.get("amount") is None:
        return _spend_status_text(spend.get("source_label") or None, spend, now)
    amount = float(spend["amount"])
    cur = (spend.get("currency") or "USD").upper()
    if cur == "USD":
        money = f"{krw(int(round(amount * krw_rate)))} (${amount:,.2f})"
    else:
        money = f"{amount:,.2f} {cur}"
    label = "잔액" if spend.get("kind") == "balance" else "사용"
    source = spend.get("source_label", "")
    if spend.get("kind") == "spend":
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period = _local(spend.get("period_start"))
        if period != month_start:
            period_label = period.astimezone(timezone.utc).strftime("%Y-%m") if period else "기간 확인 불가"
            source = source.replace("이번 달", period_label)
            if period_label not in source:
                source += f" ({period_label})"
    return _spend_status_text(f"{source}: {label} {money}", spend, now)


# --------------------------------------------------------------------------- widget
TOOL_KEYS = {"claude_code": "claude", "codex": "codex", "antigravity": "antigravity",
             "copilot": "copilot", "cursor": "cursor"}


class AccountQuotaView(AccountRegistry):
    """The collector owns observation: UI quota reads must not advance its timeline."""

    def __init__(self, rows: list):
        super().__init__()
        self.rows = rows

    def refresh(self, now: Optional[datetime] = None) -> list:
        return self.rows


def account_label(account_id: Optional[str], rows: list) -> str:
    if account_id is None:
        return "계정 미상"
    for row in rows:
        if row.get("account_id") == account_id:
            return row.get("label") or "이름 없는 계정"
    return f"{account_id.split(':')[0]} · {account_id.split(':')[-1][:12]}"


def account_options(rows: list) -> Dict[str, Optional[str]]:
    # IDs distinguish identical labels; no login identity is included.
    options = {"전체": None, "계정 미상": None}
    for row in rows:
        aid = row.get("account_id")
        if aid:
            options[f"{account_label(aid, rows)} · {aid}"] = aid
    return options


def account_query(selection: str, options: dict) -> dict:
    if selection == "계정 미상":
        return {"account_unknown": "true"}
    aid = options.get(selection)
    return {"account_id": aid} if aid else {}


def account_tools(ais: list, rows: list) -> list:
    """Manual/alternate folders remain visible even without a default tool folder."""
    tools = list(ais)
    seen = {ai["key"] for ai in tools}
    names = {"claude": "Claude Code", "codex": "OpenAI Codex CLI", "antigravity": "Antigravity",
             "copilot": "GitHub Copilot", "cursor": "Cursor"}
    for row in rows:
        key = TOOL_KEYS.get(row.get("tool"))
        if key and key not in seen:
            tools.append({"key": key, "name": names[key], "display_name": names[key],
                          "plan_name": "요금제 확인 불가", "provider": ""})
            seen.add(key)
    return tools


def attach_accounts(summary: dict, active: dict, rows: list, quotas: list, rate: float) -> dict:
    """Join opaque usage groups to desktop labels and independently queried quotas."""
    from desktop.accounts import account_billing_info
    from desktop.context_manager import apply_account_billing, get_connected_ai_summary, utc_now

    for item in summary.get("items", []):
        key = item["key"]
        groups = {a["account_id"]: a for a in active.get(key, {}).get("accounts", [])}
        registered = [r for r in rows if TOOL_KEYS.get(r.get("tool")) == key]
        relevant_quotas = [q for q in quotas if TOOL_KEYS.get(q.get("tool")) == key]
        ids = list(dict.fromkeys([r["account_id"] for r in registered] + list(groups)
                                 + [q["account_id"] for q in relevant_quotas]))
        accounts = []
        for aid in ids:
            raw = groups.get(aid)
            account = get_connected_ai_summary(
                [item], usage_by_ai={key: raw} if raw else {}, krw_rate=rate,
                quota_overrides={key: []}, official_spend={},
            )["items"][0]
            account.update(account_id=aid, label=account_label(aid, rows), quota_windows=[])
            if raw is None:
                account["usage"] = {}
            for quota in relevant_quotas:
                if quota.get("account_id") == aid:
                    account["quota_windows"] = quota.get("quota_windows") or []
                    break
            if key == "copilot" and aid is None and len(ids) == 1 and not account["quota_windows"]:
                # The local premium-request counter does not require a known login.
                account["quota_windows"] = item.get("quota_windows") or []
            accounts.append(account)
        item["accounts"] = accounts
        if len(accounts) == 1:
            # Keep the one-account card shape without borrowing another login's quota.
            if accounts[0]["quota_windows"] or key != "copilot":
                item["quota_windows"] = accounts[0]["quota_windows"]
            if not accounts[0]["usage"]:
                item["usage"] = {}
    now = utc_now()
    return apply_account_billing(summary, account_billing_info(rows, now=now), now=now)


def account_usage_lines(account: dict, meaning: str) -> List[str]:
    meaning = account.get("cost_meaning", meaning)
    lines = []
    for key, label in (("5h", "5시간"), ("today", "오늘"), ("7d", "7일"), ("month", "월")):
        block = (account.get("usage") or {}).get(key)
        if not block or block.get("requests") is None:
            lines.append(f"{label} · 기록 없음")
        else:
            lines.append(f"{label} · {block['requests']:,}회 · {format_num(block['tokens'])} 토큰 · "
                         f"{usage_cost_text(block, meaning)}")
    return lines


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
        accounts = it.get("accounts") or []
        if len(accounts) == 1 and accounts[0].get("account_id") is None:
            name += " · 계정 미상"
        if len(accounts) > 1:
            for account in accounts[:3]:
                windows = account.get("quota_windows") or []
                window = windows[0] if windows else {}
                headline, color, detail = window_text(window)
                out.append({"id": f"{key}:account:{account.get('account_id') or 'unknown'}",
                            "group": name, "label": account.get("label") or "계정 미상",
                            "value": headline, "color": color,
                            "hint": window.get("label") or "한도",
                            "reset": format_reset(window.get("resets_at")),
                            "detail": detail, "default": True})
            if len(accounts) > 3:
                out.append({"id": f"{key}:more", "group": name, "label": "더보기",
                            "value": f"+{len(accounts) - 3}개 · 메인 창", "color": theme.color(theme.FAINT),
                            "hint": "두 번 클릭", "reset": "", "detail": "", "default": True})
            continue
        quota_windows = it.get("quota_windows") or []
        if accounts and not quota_windows:
            quota_windows = [{"id": "unavailable", "label": "한도", "source": "unavailable"}]
        for w in quota_windows:
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
                    "hint": {"api_equivalent": "API 환산", "unknown": "구분 미상", "mixed": "혼합"}.get(
                        block.get("cost_meaning", meaning), ""),
                    "reset": "",
                    "detail": f"{block.get('requests', 0):,}회 요청",
                    "default": block.get("cost_meaning", meaning) == "billed" and window == "month",
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
        unclassified = unclassified_total_text(tot)
        if unclassified is not None:
            out.append({"id": "total:unclassified_month", "group": "합계", "label": "이번 달 구분 미상",
                        "value": unclassified, "color": theme.color(theme.WARNING),
                        "hint": "청구·구독 환산과 별도", "reset": "", "detail": "", "default": True})
    return out


def widget_entry_note(entry: dict) -> str:
    """Keep freshness and estimate markers in both widget layouts."""
    if ":context" in entry.get("id", ""):
        return "추정"
    return entry.get("detail") or entry.get("reset") or entry.get("hint") or ""


def select_entries(entries: list, selected: Optional[list]) -> list:
    """Entries the user picked (in display order); the defaults if they never picked."""
    if selected is None:
        return [e for e in entries if e["default"]]
    chosen = set(selected)
    tool_choices = {s.split(":")[0] for s in chosen if ":account:" not in s and not s.endswith(":more")}
    return [e for e in entries if e["id"] in chosen or (
        (":account:" in e["id"] or e["id"].endswith((":more", ":unavailable")))
        and e["id"].split(":")[0] in tool_choices
    )]
