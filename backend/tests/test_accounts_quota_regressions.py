"""Unknown/partial local counters and independently validated official windows."""
import builtins
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from backend.app.services.account_quotas import get_account_quota_windows
from desktop import claude_quota_syncer, quota
from desktop.accounts import AccountRegistry, account_id_for

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
IDENTITY = "codex:0123456789ab"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(quota, "_account_syncers", {})
    monkeypatch.setattr(quota, "_scan_cache", {})
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", Mock(side_effect=AssertionError("no network")))


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def shutdown(timestamp=NOW, cost=7):
    return {"type": "session.shutdown", "timestamp": timestamp.isoformat(),
            "data": {"modelMetrics": {"gpt-5": {"requests": {"cost": cost}}}}}


def snapshot(timestamp=NOW, used=20):
    return {"timestamp": timestamp.isoformat(), "payload": {"rate_limits": {
        "primary": {"used_percent": used, "window_minutes": 300}}}}


@pytest.mark.parametrize("partial", [False, True])
def test_acc06_failed_copilot_read_never_shows_full_allowance(tmp_path, monkeypatch, partial):
    root = tmp_path / ".copilot/session-state"
    denied = root / "denied/events.jsonl"
    write(denied, [shutdown()])
    if partial:
        write(root / "good/events.jsonl", [shutdown(cost=2)])
    original_open = builtins.open

    def open_file(file, *args, **kwargs):
        if Path(file) == denied:
            raise PermissionError("synthetic-only")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", open_file)
    usage = quota.copilot_premium_requests_this_month(root, NOW)
    window = quota.copilot_windows(300, usage, NOW)[0]
    assert usage["used"] is None
    assert window["source"] == "unavailable"
    assert window["remaining_pct"] is None
    assert usage["status"] == ("partial" if partial else "unavailable")
    assert window["partial"] == partial


@pytest.mark.parametrize("cost", [None, "invalid", float("nan")])
def test_acc06_missing_copilot_cost_is_unknown(tmp_path, cost):
    write(tmp_path / "s/events.jsonl", [shutdown(cost=cost)])
    window = quota.copilot_windows(300, quota.copilot_premium_requests_this_month(tmp_path, NOW), NOW)[0]
    assert window["remaining_pct"] is None
    assert window["source"] == "unavailable"


def test_acc12_latest_codex_uses_record_time_across_files_and_lines(tmp_path):
    fresh = tmp_path / "sessions/rollout-fresh.jsonl"
    touched = tmp_path / "sessions/rollout-touched.jsonl"
    write(fresh, [snapshot(used=90), snapshot(NOW - timedelta(hours=2), 5)])
    write(touched, [snapshot(NOW - timedelta(hours=1), 10)])
    os.utime(fresh, (1000, 1000))
    os.utime(touched, (2000, 2000))
    result = quota.latest_codex_rate_limits(tmp_path)
    assert result["rate_limits"]["primary"]["used_percent"] == 90


@pytest.mark.parametrize("timestamp", [None, "invalid", "2026-10-14T01:00:00"])
def test_acc12_unprovable_codex_snapshot_is_unknown(tmp_path, timestamp):
    write(tmp_path / "sessions/rollout-one.jsonl", [dict(snapshot(), timestamp=timestamp)])
    assert quota.latest_codex_rate_limits(tmp_path) is None


def test_acc12_old_files_are_not_read_after_proven_latest_snapshot(tmp_path, monkeypatch):
    fresh = tmp_path / "sessions/rollout-new.jsonl"
    old = tmp_path / "sessions/rollout-old.jsonl"
    write(fresh, [snapshot()])
    write(old, [snapshot(NOW - timedelta(days=1))])
    os.utime(fresh, (NOW.timestamp(), NOW.timestamp()))
    os.utime(old, ((NOW - timedelta(days=1)).timestamp(), (NOW - timedelta(days=1)).timestamp()))
    original_tail = quota._tail_lines
    reads = []

    def tail(path):
        reads.append(path)
        return original_tail(path)

    monkeypatch.setattr(quota, "_tail_lines", tail)
    assert quota.latest_codex_rate_limits(tmp_path)["timestamp"] == NOW.isoformat()
    assert reads == [fresh]


def test_acc12_scan_limit_is_explicitly_unavailable(tmp_path):
    for index in range(3):
        path = tmp_path / f"sessions/rollout-{index}.jsonl"
        write(path, [snapshot(NOW - timedelta(hours=1), used=index)])
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    result = quota.latest_codex_rate_limits(tmp_path, max_files=2)
    assert result["limited"] is True
    window = quota.codex_windows(result, NOW)[0]
    assert window["remaining_pct"] is None
    assert "상한" in window["note"]


def test_acc14_copilot_month_is_utc_and_excludes_future_events(tmp_path):
    local_now = datetime(2026, 10, 1, 9, 30, tzinfo=timezone(timedelta(hours=9)))
    write(tmp_path / "s/events.jsonl", [
        shutdown(datetime(2026, 9, 30, 23, 45, tzinfo=timezone.utc)),
        shutdown(local_now + timedelta(days=40)), shutdown(local_now, cost=2),
    ])
    usage = quota.copilot_premium_requests_this_month(tmp_path, local_now)
    assert usage["used"] == 2
    assert usage["month_start"] == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_acc16_duplicate_folder_empty_quota_cannot_replace_valid_snapshot(tmp_path, monkeypatch):
    first, second = tmp_path / "one", tmp_path / "two"
    rows = [{"tool": "codex", "account_id": IDENTITY, "config_dir": str(path), "label": "Codex"}
            for path in (first, second)]
    registry = SimpleNamespace(state_dir=tmp_path, refresh=lambda now: rows, account_at=lambda *args: IDENTITY)
    monkeypatch.setattr(quota, "latest_codex_rate_limits", lambda path, account_at:
                        {"rate_limits": snapshot()["payload"]["rate_limits"], "timestamp": NOW.isoformat(),
                         "account_id": IDENTITY} if path == first else None)
    result = quota.account_quotas(registry, now=NOW)
    assert len(result) == 1
    assert result[0]["quota_windows"][0]["remaining_pct"] == 80


def test_acc16_backend_merges_latest_valid_candidates(tmp_path, monkeypatch):
    (tmp_path / "accounts.json").write_text(json.dumps([
        {"tool": "codex", "account_id": IDENTITY, "config_dir": str(tmp_path)},
    ]), encoding="utf-8")
    good = quota.codex_windows({"rate_limits": snapshot()["payload"]["rate_limits"],
                               "timestamp": NOW.isoformat()}, NOW)
    older = quota.codex_windows({"rate_limits": snapshot(used=90)["payload"]["rate_limits"],
                                "timestamp": (NOW - timedelta(hours=1)).isoformat()}, NOW)
    monkeypatch.setattr(quota, "account_quotas", lambda registry: [
        {"tool": "codex", "account_id": IDENTITY, "quota_windows": windows}
        for windows in (good, older, quota.codex_windows(None, NOW))
    ])
    result = get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, tmp_path)
    assert result[("codex", IDENTITY)][0]["remaining_pct"] == 80


@pytest.mark.parametrize("used", [float("nan"), float("inf"), "invalid", [], True])
def test_acc17_bad_percentage_is_unavailable(used):
    window = quota.make_window("test", "test", source="official", source_label="test", used_pct=used, now=NOW)
    assert window["remaining_pct"] is None
    assert window["source"] == "unavailable"


@pytest.mark.parametrize("bad", ["invalid", float("inf"), []])
def test_acc17_invalid_codex_window_preserves_other_window(bad):
    record = snapshot()
    record["payload"]["rate_limits"]["primary"]["window_minutes"] = bad
    record["payload"]["rate_limits"]["secondary"] = {"used_percent": 40, "window_minutes": 10080}
    result = quota.codex_windows({"rate_limits": record["payload"]["rate_limits"], "timestamp": NOW.isoformat()}, NOW)
    assert any(window["source"] == "unavailable" for window in result)
    assert next(window for window in result if window["id"] == "codex.weekly")["remaining_pct"] == 60


def test_acc17_bad_claude_window_preserves_weekly_value(tmp_path, monkeypatch):
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "synthetic"}}))
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={
        "five_hour": {"utilization": "invalid"}, "seven_day": {"utilization": 40},
    }))
    official = claude_quota_syncer.ClaudeQuotaSyncer(tmp_path, tmp_path / "cache.json").get_quota()
    five, weekly = quota.claude_windows(official, NOW)
    assert five["source"] == "unavailable"
    assert weekly["remaining_pct"] == 60


def test_acc18_inactive_claude_syncer_is_evicted_after_one_hour(tmp_path, monkeypatch):
    directory = tmp_path / ".claude"
    (directory / "projects").mkdir(parents=True)
    account = account_id_for("claude_code", directory, tmp_path)
    clock = [10.0]
    monkeypatch.setattr(quota.time, "monotonic", lambda: clock[0])
    registry = AccountRegistry(home=tmp_path, environ={})
    quota.account_quotas(registry, now=NOW)
    assert any(account in key for key in quota._account_syncers)
    monkeypatch.setattr(registry, "refresh", lambda now: [])
    clock[0] += 3601
    quota.account_quotas(registry, now=NOW)
    assert quota._account_syncers == {}
