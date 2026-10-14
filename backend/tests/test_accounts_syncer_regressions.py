"""Claude account retry/cache regressions, using synthetic credentials only."""
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from desktop import claude_quota_syncer, quota
from desktop.accounts import AccountRegistry

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(quota, "_account_syncers", {})
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", Mock(side_effect=AssertionError("no network")))


def credentials(directory):
    (directory / "projects").mkdir(parents=True)
    (directory / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"accountUuid": "synthetic-shared-account"},
    }), encoding="utf-8")
    (directory / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "synthetic-only"},
    }), encoding="utf-8")


def test_acc10_failed_initial_fetch_observes_ttl_and_429(tmp_path, monkeypatch):
    directory = tmp_path / ".claude"
    credentials(directory)
    clock = [1_000_000.0]
    monkeypatch.setattr(claude_quota_syncer.time, "monotonic", lambda: clock[0])
    get = Mock(side_effect=[httpx.Response(401), httpx.Response(429), httpx.Response(401)])
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", get)
    syncer = claude_quota_syncer.ClaudeQuotaSyncer(directory, tmp_path / "cache.json")
    assert syncer.get_quota() is None
    clock[0] += 59
    assert syncer.get_quota() is None
    assert get.call_count == 1
    clock[0] += 1
    assert syncer.get_quota() is None
    clock[0] += 119
    assert syncer.get_quota(force=True) is None
    assert get.call_count == 2
    clock[0] += 1
    syncer.get_quota()
    assert get.call_count == 3


@pytest.mark.parametrize("status", [200, 429])
def test_acc11_same_uuid_shares_requests_even_when_forced(tmp_path, monkeypatch, status):
    for name in (".claude", ".claude-work"):
        credentials(tmp_path / name)
    get = Mock(return_value=httpx.Response(status, json={"five_hour": {"utilization": 20}}))
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", get)
    quota.account_quotas(AccountRegistry(home=tmp_path, environ={}), force=True, now=NOW)
    assert get.call_count == 1
    assert len(quota._account_syncers) == 1


def test_acc11_cache_replaces_atomically(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    syncer = claude_quota_syncer.ClaudeQuotaSyncer(tmp_path, cache)
    original_write = Path.write_text
    direct_writes = []

    def recording_write(path, *args, **kwargs):
        if path == cache:
            direct_writes.append(path)
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording_write)
    syncer._save_cache({"five_hour_used_pct": 20})
    assert json.loads(cache.read_text(encoding="utf-8"))["five_hour_used_pct"] == 20
    assert direct_writes == []


def test_acc19_import_does_not_read_home_cache(tmp_path, monkeypatch):
    cache = tmp_path / ".aimonitor/claude_official_quota.json"
    cache.parent.mkdir()
    cache.write_text("{}", encoding="utf-8")
    original_read = Path.read_text
    reads = []

    def recording_read(path, *args, **kwargs):
        if path == cache:
            reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", recording_read)
    importlib.reload(claude_quota_syncer)
    assert reads == []
