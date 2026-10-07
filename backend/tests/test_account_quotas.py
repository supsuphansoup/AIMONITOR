"""Backend quota reads cannot create or update desktop account attribution history."""
import json
from datetime import timedelta

import pytest

from backend.app.core.timeutil import utc_now
from backend.app.services.account_quotas import ReadOnlyAccountRegistry, get_account_quota_windows
from backend.app.services.api_key_manager import spend_service
from desktop import accounts, ai_detector, config, context_manager, quota

IDENTITY = "codex:0123456789ab"


@pytest.fixture
def saved_accounts(tmp_path):
    directory = tmp_path / ".codex"
    directory.mkdir()
    manifest = tmp_path / "accounts.json"
    timeline = tmp_path / "account_timeline.json"
    manifest.write_text(json.dumps([{
        "tool": "codex", "account_id": IDENTITY, "config_dir": str(directory),
        "label": "private@example.com",
    }]), encoding="utf-8")
    timeline.write_text(json.dumps([{
        "config_dir_hash": accounts.config_dir_hash(directory), "account_id": IDENTITY,
        "since": "2026-10-01T01:00:00+00:00",
    }, {
        "config_dir_hash": accounts.config_dir_hash(directory), "account_id": None,
        "since": "2026-10-02T01:00:00+00:00",
    }]), encoding="utf-8")
    return tmp_path, directory, manifest, timeline


def test_quota_adapter_read_only_files_unchanged_and_no_writable_refresh(saved_accounts, monkeypatch):
    root, directory, manifest, timeline = saved_accounts
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in (manifest, timeline)}

    def forbidden(*args, **kwargs):
        raise AssertionError("Writable registry refresh must never run")

    monkeypatch.setattr(accounts.AccountRegistry, "refresh", forbidden)

    def read_quota(registry):
        rows = registry.refresh()
        assert rows[0]["label"] == ""
        assert registry.account_at(directory, "2026-10-01T03:00:00+09:00") is None
        assert registry.account_at(directory, "2026-10-01T02:00:00+00:00") == IDENTITY
        assert registry.account_at(directory, "2026-10-02T02:00:00+00:00") is None
        return [{"tool": "codex", "account_id": IDENTITY,
                 "label": "private@example.com", "config_dir": str(directory),
                 "quota_windows": [{"id": "codex.5h", "remaining_pct": 42}]}]

    monkeypatch.setattr(quota, "account_quotas", read_quota)
    result = get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, root)
    assert result[("codex", IDENTITY)][0]["remaining_pct"] == 42
    assert before == {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in (manifest, timeline)}
    assert "private@example.com" not in json.dumps(list(result.values()))


def test_missing_accounts_never_create_files_or_call_quota(tmp_path, monkeypatch):
    state_dir = tmp_path / "absent"
    monkeypatch.setattr(quota, "account_quotas", lambda **kwargs: pytest.fail("No manifest"))
    assert get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, state_dir) == {}
    assert not state_dir.exists()


def test_manifest_read_failure_and_quota_failure_are_unknown(saved_accounts, monkeypatch):
    root, _, manifest, _ = saved_accounts
    manifest.write_text("invalid JSON", encoding="utf-8")
    assert get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, root) == {}
    manifest.write_text(json.dumps([{"tool": "codex", "account_id": IDENTITY,
                                    "config_dir": str(root)}]), encoding="utf-8")

    def failed(**kwargs):
        raise RuntimeError("private-token-value")

    monkeypatch.setattr(quota, "account_quotas", failed)
    assert get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, root) == {}


def test_registry_never_assigns_unknown_time_to_current_account(saved_accounts):
    root, directory, _, _ = saved_accounts
    registry = ReadOnlyAccountRegistry(root, {"codex": [IDENTITY]})
    assert registry.account_at(directory, "malformed") is None
    assert registry.account_at(directory, None) is None
    assert registry.account_at(directory, "2026-09-01T00:00:00+00:00") is None


def test_actual_desktop_quota_reader_accepts_read_only_adapter(saved_accounts, monkeypatch):
    root, _, manifest, timeline = saved_accounts
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in (manifest, timeline)}

    def forbidden(*args, **kwargs):
        pytest.fail("Writable discovery was called")

    monkeypatch.setattr(accounts.AccountRegistry, "refresh", forbidden)
    monkeypatch.setattr(quota, "latest_codex_rate_limits", lambda directory, account_at: {
        "account_id": IDENTITY, "timestamp": "2026-10-01T02:00:00+00:00",
        "rate_limits": {"primary": {"used_percent": 20, "window_minutes": 300}},
    })
    result = get_account_quota_windows({"codex": {"accounts": [{"account_id": IDENTITY}]}}, root)
    assert result[("codex", IDENTITY)]
    assert before == {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in (manifest, timeline)}


def test_historical_claude_account_never_uses_current_credentials(saved_accounts, monkeypatch):
    from desktop import claude_quota_syncer

    root, directory, manifest, _ = saved_accounts
    identity = "claude_code:0123456789ab"
    manifest.write_text(json.dumps([{"tool": "claude_code", "account_id": identity,
                                    "config_dir": str(directory)}]), encoding="utf-8")
    monkeypatch.setattr(accounts, "account_id_for", lambda *args: "claude_code:aaaaaaaaaaaa")
    monkeypatch.setattr(claude_quota_syncer, "ClaudeQuotaSyncer",
                        lambda **kwargs: pytest.fail("Historical account cannot use current credentials"))
    result = get_account_quota_windows({"claude": {"accounts": [{"account_id": identity}]}}, root)
    assert result[("claude", identity)]
    assert all(window["remaining_pct"] is None for window in result[("claude", identity)])


@pytest.mark.parametrize("have_manifest", [True, False])
def test_connected_quota_identity_missing_manifest_null_and_no_paths(
    client, saved_accounts, monkeypatch, have_manifest,
):
    root, directory, manifest, timeline = saved_accounts
    if not have_manifest:
        manifest.unlink()
    before = timeline.stat().st_mtime_ns
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setattr(ai_detector, "detect_connected_ais", lambda: [{"key": "codex", "provider": "openai"}])
    monkeypatch.setattr(spend_service, "spend_all", lambda keys: {})
    monkeypatch.setattr(context_manager, "windows_for", lambda *args, **kwargs: [])
    monkeypatch.setattr(quota, "account_quotas", lambda registry: [{
        "tool": "codex", "account_id": IDENTITY, "label": "private@example.com",
        "config_dir": str(directory), "quota_windows": [{"id": "codex.5h", "remaining_pct": 42,
                                                          "as_of": "2026-10-07T09:00:00+09:00"}],
    }])
    monkeypatch.setattr("backend.app.routers.metrics.get_account_quota_windows",
                        lambda active: get_account_quota_windows(active, root))
    assert client.post("/api/v1/traces", json={
        "provider": "openai", "model_name": "gpt-5.4", "account_id": IDENTITY, "tags": ["codex"],
        "timestamp": (utc_now() - timedelta(minutes=1)).isoformat(),
    }).status_code == 201
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    item = response.json()["items"][0]["accounts"][0]
    assert item["account_id"] == IDENTITY
    if have_manifest:
        assert item["quota_windows"][0]["remaining_pct"] == 42
        assert item["quota_windows"][0]["as_of"].endswith("+00:00")
    else:
        assert item["quota_windows"] is None and not manifest.exists()
    assert timeline.stat().st_mtime_ns == before
    assert "private@example.com" not in response.text and str(directory) not in response.text
