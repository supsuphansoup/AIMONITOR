"""Malformed account state cannot stop collection or manufacture attribution."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.account_quotas import ReadOnlyAccountRegistry, get_account_quota_windows
from desktop.accounts import AccountRegistry, account_id_for, config_dir_hash

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)
IDENTITY = "codex:0123456789ab"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def timeline_row(directory, since=NOW, identity=IDENTITY):
    return {"config_dir_hash": config_dir_hash(directory), "account_id": identity, "since": since.isoformat()}


@pytest.mark.parametrize("bad", [None, [], {"tool": []}, {"tool": "codex", "config_dir": []}])
def test_acc05_bad_manifest_rows_preserve_valid_state(tmp_path, bad):
    directory = tmp_path / ".codex"
    (directory / "sessions").mkdir(parents=True)
    registry = AccountRegistry(home=tmp_path, environ={})
    valid = registry.refresh(NOW)[0]
    save(registry.accounts_file, [bad, valid])
    save(registry.timeline_file, [bad, timeline_row(directory, identity=valid["account_id"])])
    assert registry.refresh(NOW + timedelta(seconds=1))[0]["account_id"] == valid["account_id"]
    assert registry.account_at(directory, NOW.isoformat()) == valid["account_id"]
    assert len(registry.list_accounts()) == 1


@pytest.mark.parametrize("bad", [{"not": "a list"}, None, 12])
def test_acc05_invalid_top_level_is_unknown_and_not_overwritten(tmp_path, bad):
    directory = tmp_path / ".codex"
    (directory / "sessions").mkdir(parents=True)
    registry = AccountRegistry(home=tmp_path, environ={})
    save(registry.accounts_file, bad)
    save(registry.timeline_file, bad)
    before = registry.timeline_file.read_bytes()
    registry.refresh(NOW)
    assert registry.account_at(directory, NOW.isoformat()) is None
    assert registry.timeline_file.read_bytes() == before


def test_acc05_unhashable_backend_manifest_is_ignored(tmp_path):
    save(tmp_path / "accounts.json", [{"tool": [], "account_id": IDENTITY, "config_dir": str(tmp_path)}])
    assert get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, tmp_path) == {}


def test_acc05_registry_creation_failure_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.app.services.account_quotas.ReadOnlyAccountRegistry",
                        lambda *args: (_ for _ in ()).throw(OSError("synthetic-only")))
    assert get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, tmp_path) == {}


def test_acc09_backend_rejects_naive_log_and_timeline_times(tmp_path):
    save(tmp_path / "account_timeline.json", [timeline_row(tmp_path)])
    registry = ReadOnlyAccountRegistry(tmp_path, {})
    assert registry.account_at(tmp_path, NOW.replace(tzinfo=None).isoformat()) is None
    save(tmp_path / "account_timeline.json", [dict(timeline_row(tmp_path), since="2026-10-14T00:00:00")])
    registry = ReadOnlyAccountRegistry(tmp_path, {})
    assert registry.account_at(tmp_path, NOW.isoformat()) is None


def test_acc18_timeline_compacts_and_bounds_history_without_guessing(tmp_path):
    directory = tmp_path / ".codex"
    (directory / "sessions").mkdir(parents=True)
    registry = AccountRegistry(home=tmp_path, environ={})
    identity = account_id_for("codex", directory)
    rows = [timeline_row(directory, NOW + timedelta(seconds=i), identity if i % 2 else None)
            for i in range(5004)]
    rows.insert(1, rows[0].copy())
    save(registry.timeline_file, rows)
    registry.refresh(NOW + timedelta(seconds=5010))
    saved = json.loads(registry.timeline_file.read_text(encoding="utf-8"))
    assert len(saved) <= 5000
    assert registry.account_at(directory, (NOW + timedelta(seconds=1)).isoformat()) is None
    assert registry.account_at(directory, (NOW + timedelta(seconds=5011)).isoformat()) == identity
    assert all(left != right for left, right in zip(saved, saved[1:], strict=False))
