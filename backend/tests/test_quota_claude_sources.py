"""GA-04: shared Claude quota uses a current account's active OAuth folder."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from desktop import claude_quota_syncer, quota
from desktop.accounts import AccountRegistry, account_id_for

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(quota, "_account_syncers", {})
    monkeypatch.setattr(claude_quota_syncer.time, "time", lambda: NOW.timestamp())

    def response(*args, headers, **kwargs):
        used = {"Bearer synthetic-a": 20, "Bearer synthetic-b": 65}[headers["Authorization"]]
        return httpx.Response(200, json={"five_hour": {"utilization": used}})

    monkeypatch.setattr(claude_quota_syncer.httpx, "get", Mock(side_effect=response))


def profile(directory, uuid="synthetic-account-a", token="synthetic-a"):
    (directory / "projects").mkdir(parents=True, exist_ok=True)
    (directory / ".claude.json").write_text(json.dumps({
        "oauthAccount": {"accountUuid": uuid},
    }), encoding="utf-8")
    (directory / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": token},
    }), encoding="utf-8")


def remaining(rows, account):
    row = next(row for row in rows if row["account_id"] == account)
    return next(window for window in row["quota_windows"]
                if window["id"] == "claude.5h")["remaining_pct"]


def test_ga04_shared_source_reselected_after_first_folder_moves(tmp_path):
    first, other = tmp_path / ".claude", tmp_path / ".claude-work"
    profile(first)
    profile(other, token="synthetic-b")
    registry = AccountRegistry(home=tmp_path, environ={})
    account = account_id_for("claude_code", first, tmp_path)
    assert remaining(quota.account_quotas(registry, force=True, now=NOW), account) == 80

    first.rename(tmp_path / "archived-profile")
    assert remaining(quota.account_quotas(registry, force=True, now=NOW), account) == 35
    assert claude_quota_syncer.httpx.get.call_count == 2
    assert len(quota._account_syncers) == 1
    assert next(iter(quota._account_syncers.values())).cred_file == other / ".credentials.json"


def test_ga04_switched_first_folder_never_overwrites_old_account_cache(tmp_path):
    first, other = tmp_path / ".claude", tmp_path / ".claude-work"
    profile(first)
    profile(other)
    registry = AccountRegistry(home=tmp_path, environ={})
    account_a = account_id_for("claude_code", first, tmp_path)
    quota.account_quotas(registry, force=True, now=NOW)

    profile(first, uuid="synthetic-account-b", token="synthetic-b")
    account_b = account_id_for("claude_code", first, tmp_path)
    rows = quota.account_quotas(registry, force=True, now=NOW)
    assert remaining(rows, account_a) == 80
    assert remaining(rows, account_b) == 35
    assert claude_quota_syncer.httpx.get.call_count == 3
    cache = registry.state_dir / "quota" / f"{account_a.replace(':', '-')}.json"
    assert json.loads(cache.read_text(encoding="utf-8"))["five_hour_used_pct"] == 20


@pytest.mark.parametrize("credentials", [
    {}, {"anthropicApiKey": "synthetic-api"}, {"claudeAiOauth": {}},
    {"claudeAiOauth": {"accessToken": ""}}, {"claudeAiOauth": {"accessToken": 123}},
])
def test_ga04_selects_active_oauth_instead_of_first_inactive_folder(tmp_path, credentials):
    first, other = tmp_path / ".claude", tmp_path / ".claude-work"
    profile(first)
    profile(other, token="synthetic-b")
    (first / ".credentials.json").write_text(json.dumps(credentials), encoding="utf-8")
    registry = AccountRegistry(home=tmp_path, environ={})
    account = account_id_for("claude_code", other, tmp_path)
    assert remaining(quota.account_quotas(registry, force=True, now=NOW), account) == 35
    assert claude_quota_syncer.httpx.get.call_count == 1


def test_ga04_no_active_source_returns_unavailable_without_serving_cached_number(tmp_path):
    directory = tmp_path / ".claude"
    profile(directory)
    registry = AccountRegistry(home=tmp_path, environ={})
    account = account_id_for("claude_code", directory, tmp_path)
    assert remaining(quota.account_quotas(registry, force=True, now=NOW), account) == 80
    cache = registry.state_dir / "quota" / f"{account.replace(':', '-')}.json"
    saved = cache.read_bytes()

    (directory / ".credentials.json").unlink()
    assert remaining(quota.account_quotas(registry, force=True, now=NOW), account) is None
    assert claude_quota_syncer.httpx.get.call_count == 1
    assert cache.read_bytes() == saved


def test_ga04_identity_is_rechecked_after_reading_credentials_under_syncer_lock(tmp_path, monkeypatch):
    first, other = tmp_path / ".claude", tmp_path / ".claude-work"
    profile(first)
    profile(other)
    registry = AccountRegistry(home=tmp_path, environ={})
    account = account_id_for("claude_code", first, tmp_path)
    quota.account_quotas(registry, force=True, now=NOW)
    syncer = next(iter(quota._account_syncers.values()))
    original_read = Path.read_text
    switched = []

    def switch_on_credential_read(path, *args, **kwargs):
        if path == first / ".credentials.json" and syncer._lock.locked() and not switched:
            switched.append(True)
            profile(first, uuid="synthetic-account-b", token="synthetic-b")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", switch_on_credential_read)
    assert remaining(quota.account_quotas(registry, force=True, now=NOW), account) == 80
    assert switched == [True]
    assert [call.kwargs["headers"]["Authorization"] for call in claude_quota_syncer.httpx.get.call_args_list] == [
        "Bearer synthetic-a", "Bearer synthetic-a",
    ]
