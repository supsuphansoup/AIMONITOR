"""Remaining-usage windows for subscription AIs, each with where its number came from.

A window is only ever filled from a source that actually knows the answer:

* ``official``   - the vendor reported it (Claude OAuth usage API, Codex CLI's own
                   rate-limit snapshot in its session log).
* ``user_limit`` - a count taken from this PC's logs, divided by a limit the user
                   typed in. Labelled as such, because other devices are not seen.
* ``unavailable``- nothing on this PC records it. The window says so instead of
                   showing a percentage derived from a guessed limit.

Every window carries ``as_of`` (when the number was observed) and ``stale``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("aimonitor.desktop.quota")

SOURCE_OFFICIAL = "official"
SOURCE_USER_LIMIT = "user_limit"
SOURCE_UNAVAILABLE = "unavailable"

# An official number older than this is shown, but flagged.
STALE_AFTER_SECONDS = 15 * 60

_TAIL_BYTES = 512 * 1024


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def make_window(
    window_id: str,
    label: str,
    *,
    source: str,
    source_label: str,
    used_pct: Optional[float] = None,
    resets_at: Optional[datetime] = None,
    as_of: Optional[datetime] = None,
    note: str = "",
    used_value: Optional[float] = None,
    limit_value: Optional[float] = None,
    unit: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    stale = False
    if used_pct is not None:
        used_pct = round(min(100.0, max(0.0, float(used_pct))), 1)
        if resets_at is not None and resets_at <= now:
            # The window rolled over after the number was taken; the old value is void.
            used_pct = None
            stale = True
            note = note or "리셋 시각이 지나 값이 만료됨 · 다음 갱신 대기"
        elif as_of is not None and (now - as_of).total_seconds() > STALE_AFTER_SECONDS:
            stale = True
    return {
        "id": window_id,
        "label": label,
        "used_pct": used_pct,
        "remaining_pct": None if used_pct is None else round(100.0 - used_pct, 1),
        "resets_at": _iso(resets_at),
        "source": source,
        "source_label": source_label,
        "as_of": _iso(as_of),
        "stale": stale,
        "note": note,
        "used_value": used_value,
        "limit_value": limit_value,
        "unit": unit,
    }


# --------------------------------------------------------------------------- Claude
CLAUDE_EXTRA_WINDOWS = {
    "seven_day_opus": "주간 (Opus)",
    "seven_day_sonnet": "주간 (Sonnet)",
}


def claude_windows(
    official: Optional[Dict[str, Any]], now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Windows from the cached Claude OAuth usage response (see claude_quota_syncer)."""
    src = "Anthropic 공식"
    if not official:
        reason = "Claude 로그인 정보가 없거나 공식 사용량을 아직 받지 못함"
        return [
            make_window(
                "claude.5h", "5시간", source=SOURCE_UNAVAILABLE, source_label=src, note=reason, now=now
            ),
            make_window(
                "claude.weekly", "주간", source=SOURCE_UNAVAILABLE, source_label=src, note=reason, now=now
            ),
        ]
    as_of = _parse_ts(official.get("fetched_at"))
    out = [
        make_window(
            "claude.5h",
            "5시간",
            source=SOURCE_OFFICIAL,
            source_label=src,
            used_pct=official.get("five_hour_used_pct"),
            resets_at=_parse_ts(official.get("five_hour_resets_at")),
            as_of=as_of,
            now=now,
        ),
        make_window(
            "claude.weekly",
            "주간",
            source=SOURCE_OFFICIAL,
            source_label=src,
            used_pct=official.get("weekly_used_pct"),
            resets_at=_parse_ts(official.get("weekly_resets_at")),
            as_of=as_of,
            now=now,
        ),
    ]
    for field, label in CLAUDE_EXTRA_WINDOWS.items():
        extra = official.get(field)
        if isinstance(extra, dict) and extra.get("utilization") is not None:
            out.append(
                make_window(
                    f"claude.{field}",
                    label,
                    source=SOURCE_OFFICIAL,
                    source_label=src,
                    used_pct=extra.get("utilization"),
                    resets_at=_parse_ts(extra.get("resets_at")),
                    as_of=as_of,
                    now=now,
                )
            )
    return out


# --------------------------------------------------------------------------- Codex
def _window_label(minutes: Optional[float]) -> tuple:
    if minutes is None:
        return "window", "사용 한도"
    if abs(minutes - 300) <= 30:
        return "5h", "5시간"
    if abs(minutes - 10080) <= 120:
        return "weekly", "주간"
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)}d", f"{int(minutes // 1440)}일"
    return f"{int(minutes)}m", f"{int(minutes)}분"


def _tail_lines(path: Path, max_bytes: int = _TAIL_BYTES) -> List[str]:
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # first line is probably cut
    return lines


def latest_codex_rate_limits(
    codex_home: Optional[Path] = None, max_files: int = 20
) -> Optional[Dict[str, Any]]:
    """Most recent `rate_limits` snapshot Codex CLI wrote into its session logs.

    Codex records the server's own usage percentages on every token_count event,
    so this is an official number, as of the last Codex request on this PC.
    """
    codex_home = codex_home or (Path.home() / ".codex")
    sessions = codex_home / "sessions"
    if not sessions.exists():
        return None
    try:
        files = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in files[:max_files]:
        try:
            lines = _tail_lines(path)
        except OSError:
            continue
        for line in reversed(lines):
            if "rate_limits" not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
            limits = payload.get("rate_limits") if isinstance(payload, dict) else None
            if isinstance(limits, dict) and (limits.get("primary") or limits.get("secondary")):
                return {"rate_limits": limits, "timestamp": obj.get("timestamp")}
    return None


def codex_windows(snapshot: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    src = "Codex 공식 (마지막 요청 시점)"
    if not snapshot:
        return [
            make_window(
                "codex.limits",
                "사용 한도",
                source=SOURCE_UNAVAILABLE,
                source_label=src,
                note="Codex 세션 로그에 사용 한도 기록이 아직 없음",
                now=now,
            )
        ]
    as_of = _parse_ts(snapshot.get("timestamp"))
    out = []
    for slot in ("primary", "secondary"):
        w = snapshot["rate_limits"].get(slot)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        minutes = w.get("window_minutes")
        suffix, label = _window_label(float(minutes) if minutes is not None else None)
        resets = _parse_ts(w.get("resets_at"))
        if resets is None and w.get("resets_in_seconds") is not None and as_of is not None:
            resets = as_of + timedelta(seconds=float(w["resets_in_seconds"]))
        out.append(
            make_window(
                f"codex.{suffix}",
                label,
                source=SOURCE_OFFICIAL,
                source_label=src,
                used_pct=w.get("used_percent"),
                resets_at=resets,
                as_of=as_of,
                now=now,
                # Codex only reports when it is used, so age alone is not staleness.
                note="" if resets is not None else "리셋 시각 정보 없음",
            )
        )
        # Snapshot age is expected (it is "as of the last request"); only expiry voids it.
        out[-1]["stale"] = out[-1]["used_pct"] is None
    return out or codex_windows(None, now)


# --------------------------------------------------------------------------- Copilot
def copilot_premium_requests_this_month(
    session_state: Optional[Path] = None, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Premium requests Copilot CLI logged on this PC since the 1st (UTC).

    Copilot's monthly allowance resets on the 1st at 00:00 UTC. The count comes
    from `session.shutdown` events, so a session still open is not in it yet.
    """
    session_state = session_state or (Path.home() / ".copilot" / "session-state")
    now = now or datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = 0.0
    last_seen: Optional[datetime] = None
    if session_state.exists():
        for path in session_state.glob("*/events.jsonl"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < month_start:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if '"session.shutdown"' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except ValueError:
                            continue
                        ts = _parse_ts(d.get("timestamp"))
                        if ts is None or ts < month_start:
                            continue
                        metrics = (d.get("data") or {}).get("modelMetrics") or {}
                        for m in metrics.values():
                            cost = ((m or {}).get("requests") or {}).get("cost")
                            if isinstance(cost, (int, float)):
                                total += float(cost)
                        last_seen = max(last_seen, ts) if last_seen else ts
            except OSError:
                continue
    if month_start.month == 12:
        next_reset = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_reset = month_start.replace(month=month_start.month + 1)
    return {"used": total, "month_start": month_start, "resets_at": next_reset, "last_seen": last_seen}


def copilot_windows(
    monthly_limit: Optional[float], usage: Dict[str, Any], now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    used = usage["used"]
    used_txt = f"{used:g}회"
    if monthly_limit and monthly_limit > 0:
        return [
            make_window(
                "copilot.premium_month",
                "월 프리미엄 요청",
                source=SOURCE_USER_LIMIT,
                source_label="이 PC 로그 ÷ 직접 입력한 한도",
                used_pct=used / float(monthly_limit) * 100.0,
                resets_at=usage["resets_at"],
                as_of=usage.get("last_seen") or now,
                now=now,
                note=f"이번 달 {used_txt} / {float(monthly_limit):g}회 · 다른 기기 사용분 미포함",
                used_value=used,
                limit_value=float(monthly_limit),
                unit="회",
            )
        ]
    return [
        make_window(
            "copilot.premium_month",
            "월 프리미엄 요청",
            source=SOURCE_UNAVAILABLE,
            source_label="GitHub 공식 잔여량 미제공",
            resets_at=usage["resets_at"],
            as_of=usage.get("last_seen"),
            now=now,
            note=f"이번 달 이 PC 로그 {used_txt} 사용 · 플랜 한도를 설정에 입력하면 남은 %를 계산",
            used_value=used,
            unit="회",
        )
    ]


# --------------------------------------------------------------------------- dispatcher
UNAVAILABLE_NOTES = {
    "antigravity": "Antigravity는 남은 사용량을 PC에 기록하지 않음",
    "cursor": "Cursor는 남은 사용량을 PC에 기록하지 않음",
    "windsurf": "Windsurf는 남은 사용량을 PC에 기록하지 않음",
}


def windows_for(
    key: str,
    *,
    config: Optional[Dict[str, Any]] = None,
    claude_official: Any = "auto",
    codex_snapshot: Any = "auto",
    copilot_usage: Any = "auto",
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Quota windows for one detected AI. API-key AIs have spend, not quota: []."""
    config = config or {}
    if key == "claude":
        if claude_official == "auto":
            claude_official = _safe(_fetch_claude_official)
        return claude_windows(claude_official, now)
    if key == "codex":
        if codex_snapshot == "auto":
            codex_snapshot = _cached("codex", lambda: _safe(latest_codex_rate_limits))
        return codex_windows(codex_snapshot, now)
    if key == "copilot":
        if copilot_usage == "auto":
            copilot_usage = _cached("copilot", lambda: _safe(copilot_premium_requests_this_month)) or {
                "used": 0.0,
                "resets_at": None,
                "last_seen": None,
            }
        limit = config.get("copilot_monthly_premium_limit")
        try:
            limit = float(limit) if limit not in (None, "") else None
        except (TypeError, ValueError):
            limit = None
        return copilot_windows(limit, copilot_usage, now)
    if key in UNAVAILABLE_NOTES:
        return [
            make_window(
                f"{key}.quota",
                "남은 사용량",
                source=SOURCE_UNAVAILABLE,
                source_label="공식 값 없음",
                note=UNAVAILABLE_NOTES[key],
                now=now,
            )
        ]
    return []


# Local log scans are cheap but the GUI and the widget each poll every few seconds.
LOCAL_SCAN_TTL_SECONDS = 20.0
_scan_cache: Dict[str, tuple] = {}
_scan_lock = threading.Lock()


def _cached(name: str, fn: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _scan_lock:
        hit = _scan_cache.get(name)
        if hit and now - hit[0] < LOCAL_SCAN_TTL_SECONDS:
            return hit[1]
    value = fn()
    with _scan_lock:
        _scan_cache[name] = (now, value)
    return value


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as e:  # a broken source must not take the whole summary down
        logger.debug("quota source failed: %s", type(e).__name__)
        return None


def _fetch_claude_official() -> Optional[Dict[str, Any]]:
    from desktop.claude_quota_syncer import get_official_claude_quota

    return get_official_claude_quota()
