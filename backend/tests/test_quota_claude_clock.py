"""GA-05: process-local quota intervals ignore changes to the system clock."""

import json
from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest

from desktop import claude_quota_syncer, quota


@pytest.fixture
def quota_clock(tmp_path, monkeypatch):
    (tmp_path / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "synthetic-only"},
    }), encoding="utf-8")
    clock = {"wall": 1_792_000_000.0, "elapsed": 10_000.0}
    monkeypatch.setattr(claude_quota_syncer.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(claude_quota_syncer.time, "monotonic", lambda: clock["elapsed"])
    get = Mock(return_value=httpx.Response(200, json={"five_hour": {"utilization": 20}}))
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", get)
    syncer = claude_quota_syncer.ClaudeQuotaSyncer(tmp_path, tmp_path / "quota-cache.json")
    return syncer, clock, get


@pytest.mark.parametrize("wall_shift", [-86_400, 86_400])
def test_ga05_successful_cache_expires_after_sixty_elapsed_seconds(quota_clock, wall_shift):
    syncer, clock, get = quota_clock
    first = syncer.get_quota()
    clock["wall"] += wall_shift
    clock["elapsed"] += 59
    assert syncer.get_quota() == first
    assert get.call_count == 1
    clock["elapsed"] += 1
    syncer.get_quota()
    assert get.call_count == 2


@pytest.mark.parametrize("wall_shift", [-86_400, 86_400])
def test_ga05_failed_attempt_retries_after_sixty_elapsed_seconds(quota_clock, wall_shift):
    syncer, clock, get = quota_clock
    get.return_value = httpx.Response(401)
    assert syncer.get_quota() is None
    clock["wall"] += wall_shift
    clock["elapsed"] += 59
    assert syncer.get_quota() is None
    assert get.call_count == 1
    clock["elapsed"] += 1
    assert syncer.get_quota() is None
    assert get.call_count == 2


@pytest.mark.parametrize("wall_shift", [-86_400, 86_400])
def test_ga05_429_backoff_is_120_elapsed_seconds_even_when_forced(quota_clock, wall_shift):
    syncer, clock, get = quota_clock
    get.side_effect = [httpx.Response(429), httpx.Response(200, json={})]
    assert syncer.get_quota() is None
    clock["wall"] += wall_shift
    clock["elapsed"] += 119
    assert syncer.get_quota(force=True) is None
    assert get.call_count == 1
    clock["elapsed"] += 1
    assert syncer.get_quota(force=True) is not None
    assert get.call_count == 2


def test_ga05_saved_fetched_at_and_window_as_of_still_use_actual_utc_time(quota_clock):
    syncer, clock, get = quota_clock
    first = syncer.get_quota()
    assert first["fetched_at"] == clock["wall"]
    clock["wall"] -= 86_400
    clock["elapsed"] += 60
    fresh = syncer.get_quota()
    assert get.call_count == 2
    saved = json.loads(syncer.cache_file.read_text(encoding="utf-8"))
    assert saved["fetched_at"] == fresh["fetched_at"] == clock["wall"]
    window = quota.claude_windows(fresh, datetime.fromtimestamp(clock["wall"], timezone.utc))[0]
    assert window["as_of"] == datetime.fromtimestamp(clock["wall"], timezone.utc).isoformat()
