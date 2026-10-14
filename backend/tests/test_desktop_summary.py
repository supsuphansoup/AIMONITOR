"""Desktop summary: every remaining-usage number has a source, none is invented."""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from desktop.context_manager import get_connected_ai_summary, get_context_window
from desktop.display import select_entries, widget_entries, window_text
from desktop.quota import (
    claude_windows,
    codex_windows,
    copilot_premium_requests_this_month,
    copilot_windows,
    latest_codex_rate_limits,
    windows_for,
)

NOW = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)

# Colour helpers and the main window need the GUI stack; the Windows CI job installs it.
needs_gui = pytest.mark.skipif(importlib.util.find_spec("customtkinter") is None,
                               reason="desktop GUI dependencies not installed")


def _ai(key, provider="anthropic", plan="Claude Pro"):
    return {"key": key, "name": key, "display_name": key, "provider": provider, "plan_name": plan,
            "detail": "", "badge_color": "#fff", "path": ""}


def _usage(requests=1, tokens=100, cost=1.0, unknown=0, context=None, model="claude-opus-5-5"):
    win = {"requests": requests, "tokens": tokens, "cost_usd": cost, "cost_unknown": unknown}
    return {"model_name": model, "context_tokens": context, "timestamp": NOW.isoformat(), "mins_ago": 3,
            "is_active": True, "windows": {w: dict(win) for w in ("5h", "today", "7d", "month")}}


# ---------------------------------------------------------------- Claude (official)
def test_claude_windows_come_from_official_values():
    official = {"five_hour_used_pct": 70.0, "five_hour_resets_at": "2026-10-05T03:40:00+00:00",
                "weekly_used_pct": 53.0, "weekly_resets_at": "2026-10-09T08:00:00+00:00",
                "fetched_at": (NOW - timedelta(minutes=1)).timestamp()}
    five, week = claude_windows(official, now=NOW)
    assert (five["used_pct"], five["remaining_pct"], five["source"]) == (70.0, 30.0, "official")
    assert (week["remaining_pct"], week["stale"]) == (47.0, False)


def test_claude_value_past_its_reset_is_void_not_shown():
    official = {"five_hour_used_pct": 90.0, "five_hour_resets_at": "2026-10-05T00:30:00+00:00",
                "weekly_used_pct": 10.0, "weekly_resets_at": "2026-10-09T08:00:00+00:00",
                "fetched_at": (NOW - timedelta(hours=2)).timestamp()}
    five, week = claude_windows(official, now=NOW)
    assert five["remaining_pct"] is None and five["stale"] is True
    assert week["remaining_pct"] == 90.0 and week["stale"] is True  # old, but not reset yet


@needs_gui
def test_claude_without_official_data_is_unavailable_not_100_percent():
    for w in claude_windows(None, now=NOW):
        assert w["remaining_pct"] is None
        assert w["source"] == "unavailable"
        assert window_text(w)[0] == "확인 불가"


def test_claude_model_specific_weekly_cap_is_shown_when_present():
    official = {"five_hour_used_pct": 1, "weekly_used_pct": 2, "fetched_at": NOW.timestamp(),
                "seven_day_opus": {"utilization": 40.0, "resets_at": "2026-10-09T08:00:00+00:00"}}
    labels = [w["label"] for w in claude_windows(official, now=NOW)]
    assert labels == ["5시간", "주간", "주간 (Opus)"]


# ---------------------------------------------------------------- Codex (official, from its log)
def _codex_log(tmp_path: Path, rate_limits: dict, ts: str = "2026-10-05T00:50:00Z") -> Path:
    d = tmp_path / ".codex" / "sessions" / "2026" / "10" / "05"
    d.mkdir(parents=True)
    f = d / "rollout-2026-10-05T00-00-00-abc.jsonl"
    lines = [
        {"timestamp": ts, "type": "event_msg", "payload": {"type": "user_message"}},
        {"timestamp": ts, "type": "event_msg",
         "payload": {"type": "token_count", "info": None, "rate_limits": rate_limits}},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return tmp_path / ".codex"


def test_codex_rate_limits_are_read_from_session_log(tmp_path):
    home = _codex_log(tmp_path, {
        "primary": {"used_percent": 25.0, "window_minutes": 300, "resets_in_seconds": 3600},
        "secondary": {"used_percent": 60.0, "window_minutes": 10080,
                      "resets_at": int(datetime(2026, 10, 9, tzinfo=timezone.utc).timestamp())},
    })
    snap = latest_codex_rate_limits(home)
    five, week = codex_windows(snap, now=NOW)
    assert (five["id"], five["remaining_pct"], five["source"]) == ("codex.5h", 75.0, "official")
    assert five["resets_at"] == "2026-10-05T01:50:00+00:00"  # log time + resets_in_seconds
    assert (week["id"], week["remaining_pct"]) == ("codex.weekly", 40.0)


def test_codex_without_logs_is_unavailable(tmp_path):
    assert latest_codex_rate_limits(tmp_path / "nothing") is None
    (w,) = codex_windows(None, now=NOW)
    assert w["source"] == "unavailable" and w["remaining_pct"] is None


# ---------------------------------------------------------------- Copilot (local count)
def _copilot_session(root: Path, name: str, ts: str, cost: float):
    d = root / name
    d.mkdir(parents=True)
    ev = {"type": "session.shutdown", "id": name, "timestamp": ts,
          "data": {"modelMetrics": {"gpt-5.2": {"requests": {"count": 3, "cost": cost},
                                                "usage": {"inputTokens": 1, "outputTokens": 1}}}}}
    (d / "events.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")


def test_copilot_counts_only_this_months_premium_requests(tmp_path):
    _copilot_session(tmp_path, "a", "2026-10-02T10:00:00Z", 2)
    _copilot_session(tmp_path, "b", "2026-10-04T10:00:00Z", 1.5)
    _copilot_session(tmp_path, "c", "2026-09-30T23:00:00Z", 9)  # last month
    usage = copilot_premium_requests_this_month(tmp_path, now=NOW)
    assert usage["used"] == 3.5
    assert usage["resets_at"] == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_copilot_percentage_needs_a_user_limit():
    usage = {"used": 30.0, "resets_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "last_seen": NOW}
    (unknown,) = copilot_windows(None, usage, now=NOW)
    assert unknown["remaining_pct"] is None and "30회" in unknown["note"]
    (known,) = copilot_windows(300, usage, now=NOW)
    assert (known["remaining_pct"], known["source"]) == (90.0, "user_limit")


def test_tools_without_local_quota_say_so():
    (w,) = windows_for("antigravity", now=NOW)
    assert w["source"] == "unavailable" and w["remaining_pct"] is None
    assert windows_for("openai_api", now=NOW) == []  # API keys have spend, not quota


# ---------------------------------------------------------------- summary
def test_summary_splits_billed_from_api_equivalent():
    ais = [_ai("claude"), _ai("openai_api", "openai", "OpenAI API 종량제"), _ai("ollama", "ollama", "로컬")]
    usage = {"claude": _usage(cost=10.0, context=250_000), "openai_api": _usage(cost=2.0, unknown=1),
             "ollama": _usage(cost=0.0)}
    s = get_connected_ai_summary(ais, usage, krw_rate=1400.0, quota_overrides={k["key"]: [] for k in ais}, now=NOW)
    by = s["by_key"]
    assert by["claude"]["cost_meaning"] == "api_equivalent"
    assert "실제 청구 아님" in by["claude"]["billing_note"]
    assert by["openai_api"]["cost_meaning"] == "billed"
    assert "계산 불가" in by["openai_api"]["billing_note"]
    assert by["ollama"]["cost_meaning"] == "free"
    assert s["total"]["billed_month_krw"] == 2800
    assert s["total"]["billed_unknown_month"] == 1
    assert s["total"]["api_equivalent_month_krw"] == 14000
    ctx = by["claude"]["context"]
    assert (ctx["max_tokens"], ctx["used_pct"], ctx["estimated"]) == (1_000_000, 25.0, True)


def test_claude_code_on_api_key_is_billed():
    s = get_connected_ai_summary([_ai("claude", plan="Anthropic API 종량제")], {}, quota_overrides={"claude": []})
    assert s["items"][0]["cost_meaning"] == "billed"


def test_no_usage_means_no_context_and_zero_counts():
    s = get_connected_ai_summary([_ai("copilot", "openai", "GitHub Copilot")], {}, quota_overrides={"copilot": []})
    it = s["items"][0]
    assert it["context"] is None
    assert it["usage"]["today"]["requests"] == 0
    assert "기록 없음" in it["session_status"]


@needs_gui
def test_hidden_ai_is_left_out_of_cards_widget_and_totals():
    ais = [_ai("claude"), _ai("openai_api", "openai", "OpenAI API 종량제")]
    usage = {"claude": _usage(cost=10.0), "openai_api": _usage(cost=2.0)}
    s = get_connected_ai_summary(ais, usage, krw_rate=1400.0, config={"hidden_ais": ["openai_api"]},
                                 quota_overrides={k["key"]: [] for k in ais}, now=NOW)
    assert [it["key"] for it in s["items"]] == ["claude"]
    assert s["hidden"] == [{"key": "openai_api", "display_name": "openai_api"}]
    assert s["total"]["hidden_count"] == 1
    assert s["total"]["billed_month_krw"] == 0  # the hidden API's spend is not in the total
    assert not any(e["id"].startswith("openai_api:") for e in widget_entries(s))


def test_hidden_ais_ignores_a_malformed_setting():
    s = get_connected_ai_summary([_ai("claude")], {}, config={"hidden_ais": "claude"},
                                 quota_overrides={"claude": []}, now=NOW)
    assert [it["key"] for it in s["items"]] == ["claude"]
    assert s["total"]["hidden_count"] == 0


def test_context_window_prefers_most_specific_family():
    assert get_context_window("claude-opus-5-5") == 1_000_000
    assert get_context_window("claude-3-5-haiku") == 200_000
    assert get_context_window("gemini-3.1-pro") == 1_048_576
    assert get_context_window("something-new") == 200_000


# ---------------------------------------------------------------- widget selection
def _summary_for_widget():
    official = {"five_hour_used_pct": 70.0, "weekly_used_pct": 53.0, "fetched_at": NOW.timestamp()}
    return get_connected_ai_summary(
        [_ai("claude"), _ai("antigravity", "gemini", "Google AI Pro")],
        {"claude": _usage(context=1000)},
        quota_overrides={"claude": claude_windows(official, now=NOW),
                         "antigravity": windows_for("antigravity", now=NOW)},
        now=NOW,
    )


@needs_gui
def test_widget_defaults_show_every_quota_window_and_the_bill():
    ids = [e["id"] for e in select_entries(widget_entries(_summary_for_widget()), None)]
    assert ids == ["claude:claude.5h", "claude:claude.weekly", "antigravity:antigravity.quota",
                   "total:billed_month"]


@needs_gui
def test_widget_shows_only_what_the_user_picked_in_display_order():
    entries = widget_entries(_summary_for_widget())
    picked = select_entries(entries, ["total:billed_month", "claude:claude.weekly", "gone:item"])
    assert [e["id"] for e in picked] == ["claude:claude.weekly", "total:billed_month"]
    assert picked[0]["value"] == "47% 남음"
    assert select_entries(entries, []) == []


@needs_gui
def test_widget_marks_unavailable_and_estimated_items():
    by_id = {e["id"]: e for e in widget_entries(_summary_for_widget())}
    assert by_id["antigravity:antigravity.quota"]["value"] == "확인 불가"
    assert by_id["claude:context"]["hint"] == "추정"
    assert by_id["claude:month"]["hint"] == "API 환산"


# ---------------------------------------------------------------- launchers / modules
def test_ai_detector_reports_known_tools(tmp_path, monkeypatch):
    import sqlite3

    import httpx

    from desktop.ai_detector import detector, scan_all_ai_status

    for name in ("home", "appdata", "localappdata"):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setattr(detector, name, folder)
    for name, folder in (("USERPROFILE", "home"), ("HOME", "home"), ("APPDATA", "appdata"),
                         ("LOCALAPPDATA", "localappdata"), ("CLAUDE_CONFIG_DIR", "home/.claude"),
                         ("CODEX_HOME", "home/.codex")):
        monkeypatch.setenv(name, str(tmp_path / folder))
    fake_claude = tmp_path / "home/.claude"
    fake_claude.mkdir()
    (fake_claude / ".credentials.json").write_text("{}", encoding="utf-8")
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"models": []})), **kwargs))

    outside_reads = []
    real_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if not path.resolve().is_relative_to(tmp_path.resolve()):
            outside_reads.append(str(path))
            raise AssertionError("scan must read only synthetic fixtures")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    real_connect = sqlite3.connect
    def guarded_connect(path, *args, **kwargs):
        if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
            outside_reads.append("SQLite outside fixture")
            raise AssertionError("scan must open only synthetic databases")
        return real_connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    real_transport = []
    def no_network(*args, **kwargs):
        real_transport.append(True)
        raise AssertionError("scan must use MockTransport")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)

    status = scan_all_ai_status()
    for key in ("claude", "antigravity", "copilot", "cursor", "codex"):
        assert key in status
        assert "plan_name" in status[key]
    assert not outside_reads
    assert not real_transport


def test_desktop_launchers_exist():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # The desktop shortcut and the Windows Run entry point at run_desktop.pyw; the exe has its own entry.
    for name in ("run_desktop.py", "run_desktop.pyw", os.path.join("build-windows", "entry.py")):
        assert os.path.exists(os.path.join(root, name))


@needs_gui
def test_desktop_gui_module():
    from desktop.app_gui import DesktopMainWindow, format_num

    assert format_num(200_000) == "200.0K"
    assert format_num(1_500_000) == "1.50M"
    assert issubclass(DesktopMainWindow, object)


@pytest.mark.parametrize("bad", ["", None, "not-a-date"])
def test_reset_text_handles_missing_values(bad):
    from desktop.display import format_reset

    assert format_reset(bad) == ""
