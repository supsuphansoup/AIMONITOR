"""Multi-folder collection, conservative identity history and isolated quota requests."""

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from desktop import ai_watcher, quota
from desktop.accounts import AccountRegistry, account_id_for, canonical_path, discover_folders
from desktop.claude_quota_syncer import ClaudeQuotaSyncer

NOW = datetime(2026, 10, 7, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(ai_watcher, "backend_headers", lambda: {})


def folder(home, name=".claude", tool="claude_code", identifier="account-one"):
    path = home / name
    (path / ("projects" if tool == "claude_code" else "sessions")).mkdir(parents=True)
    login(path, tool, identifier)
    return path


def login(path, tool, identifier):
    filename = ".claude.json" if tool == "claude_code" else "auth.json"
    data = {"oauthAccount": {"accountUuid": identifier, "emailAddress": "private@example.com"}}
    if tool == "codex":
        data = {"tokens": {"account_id": identifier, "access_token": "secret-never-persisted"}}
    (path / filename).write_text(json.dumps(data), encoding="utf-8")


def assistant(identifier, timestamp):
    return {"type": "assistant", "timestamp": timestamp.isoformat(),
            "message": {"id": identifier, "model": "claude-opus-4-1",
                        "content": [{"text": "private-prompt"}],
                        "usage": {"input_tokens": 10, "output_tokens": 20}}}


def append(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write((json.dumps(obj) + "\n").encode())


def watcher(home, monkeypatch, registry=None):
    w = ai_watcher.UnifiedAIWatcher(state_file=home / ".aimonitor" / "ai_offsets.json")
    w.accounts = registry or AccountRegistry(home=home, environ={})
    monkeypatch.setattr(w, "_scan_antigravity", lambda: [])
    monkeypatch.setattr(w, "_scan_copilot", lambda: [])
    return w


def test_discovery_defaults_env_globs_manual_and_real_path_dedup(tmp_path):
    default = folder(tmp_path)
    second = folder(tmp_path, ".claude-work")
    codex = folder(tmp_path, ".codex", "codex")
    custom = folder(tmp_path, "external", "codex")
    (tmp_path / ".claude-monitor").mkdir()
    found = discover_folders(tmp_path, {"CLAUDE_CONFIG_DIR": str(default), "CODEX_HOME": str(custom)},
                             [{"tool": "claude_code", "config_dir": str(second), "source": "manual"}])
    assert {r["config_dir"] for r in found} == {str(canonical_path(p))
                                               for p in (default, second, codex, custom)}
    assert len(found) == 4
    assert next(r for r in found if r["config_dir"] == str(canonical_path(second)))["source"] == "manual"


def test_discovery_resolves_directory_junction_or_symlink(tmp_path):
    original = folder(tmp_path, ".codex", "codex")
    alias = tmp_path / "runtime"
    alias.mkdir()
    target = alias / "sessions"
    if os.name == "nt":
        # Both paths are inside pytest's temporary tree. /J needs no administrator rights.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(original / "sessions")],
                       check=True, capture_output=True)
    else:
        target.symlink_to(original / "sessions", target_is_directory=True)
    assert len(discover_folders(tmp_path, {"CODEX_HOME": str(alias)})) == 1


@pytest.mark.parametrize("tool,name", [("claude_code", ".claude"), ("codex", ".codex")])
def test_identifiers_hashed_and_no_credentials_or_email_persisted(tmp_path, tool, name):
    path = folder(tmp_path, name, tool, "stable-raw-identifier")
    registry = AccountRegistry(home=tmp_path, environ={})
    rows = registry.refresh(NOW)
    assert rows[0]["account_id"] == tool + ":" + hashlib.sha256(b"stable-raw-identifier").hexdigest()[:12]
    assert rows[0]["label"] == ("Claude 1" if tool == "claude_code" else "Codex 1")
    persisted = registry.accounts_file.read_text() + registry.timeline_file.read_text()
    for secret in ("stable-raw-identifier", "private@example.com", "secret-never-persisted"):
        assert secret not in persisted
    (path / (".claude.json" if tool == "claude_code" else "auth.json")).unlink()
    assert account_id_for(tool, path, tmp_path).startswith(tool + ":dir-")


def test_claude_default_identity_from_home_json_and_codex_jwt_claim(tmp_path):
    claude = folder(tmp_path)
    (claude / ".claude.json").unlink()
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {"accountUuid": "home-id"}}))
    assert account_id_for("claude_code", claude, tmp_path).endswith(hashlib.sha256(b"home-id").hexdigest()[:12])
    import base64

    codex = folder(tmp_path, ".codex", "codex")
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "jwt-id"}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    (codex / "auth.json").write_text(json.dumps({"tokens": {"id_token": "header." + payload + ".sig"}}))
    assert account_id_for("codex", codex).endswith(hashlib.sha256(b"jwt-id").hexdigest()[:12])


def test_timeline_before_observation_switch_gap_and_restart_are_unknown(tmp_path):
    path = folder(tmp_path)
    registry = AccountRegistry(home=tmp_path, environ={})
    old = registry.refresh(NOW)[0]["account_id"]
    registry.refresh(NOW + timedelta(minutes=10))
    login(path, "claude_code", "second-account")
    new = registry.refresh(NOW + timedelta(minutes=20))[0]["account_id"]
    assert old != new
    assert registry.account_at(path, (NOW - timedelta(seconds=1)).isoformat()) is None
    assert registry.account_at(path, (NOW + timedelta(minutes=5)).isoformat()) == old
    assert registry.account_at(path, (NOW + timedelta(minutes=15)).isoformat()) is None
    assert registry.account_at(path, (NOW + timedelta(minutes=21)).isoformat()) == new
    assert registry.account_at(path, None) is None
    assert registry.account_at(path, "2026-10-07T01:00:00") is None
    registry.refresh(NOW + timedelta(minutes=30))
    restarted = AccountRegistry(home=tmp_path, environ={})
    restarted.refresh(NOW + timedelta(minutes=60))
    assert restarted.account_at(path, (NOW + timedelta(minutes=40)).isoformat()) is None
    assert restarted.account_at(path, (NOW + timedelta(minutes=61)).isoformat()) == new


def test_manual_folder_labels_rename_and_remove(tmp_path):
    path = folder(tmp_path, "external")
    registry = AccountRegistry(home=tmp_path, environ={})
    registry.add_folder("claude_code", path)
    row = registry.list_accounts()[0]
    assert row["label"] == "Claude 1" and row["source"] == "manual"
    registry.rename_account(row["account_id"], "업무 계정")
    assert registry.list_accounts()[0]["label"] == "업무 계정"
    with pytest.raises(ValueError):
        registry.rename_account(row["account_id"], "private@example.com")
    registry.add_folder("claude_code", path)
    assert len(registry.list_accounts()) == 1
    active = registry.add_folder("claude_code", path, label="개인 계정")
    assert active[0]["label"] == "개인 계정"
    with pytest.raises(ValueError):
        registry.add_folder("claude_code", path, label="private@example.com")
    assert "private@example.com" not in registry.accounts_file.read_text()
    registry.remove_folder(path)
    assert registry.list_accounts() == []


def test_manual_folder_before_log_structure_exists_has_complete_manifest(tmp_path):
    path = tmp_path / "not-yet-used"
    path.mkdir()
    registry = AccountRegistry(home=tmp_path, environ={})
    registry.add_folder("claude_code", path)
    row = registry.list_accounts()[0]
    assert row["label"] == "Claude 1"
    assert datetime.fromisoformat(row["first_seen"]).tzinfo is not None
    assert datetime.fromisoformat(row["last_seen"]).tzinfo is not None
    (path / "projects").mkdir()
    assert registry.refresh()[0]["account_id"] == row["account_id"]


def test_multiple_claude_folders_send_hashed_account_ids_using_http_mock(tmp_path, monkeypatch):
    first = folder(tmp_path)
    second = folder(tmp_path, ".claude-work", identifier="second-account")
    registry = AccountRegistry(home=tmp_path, environ={})
    registry.refresh(NOW)
    monkeypatch.setattr(registry, "refresh", lambda: registry.list_accounts())
    append(first / "projects" / "a.jsonl", assistant("a", NOW + timedelta(seconds=1)))
    append(second / "projects" / "b.jsonl", assistant("b", NOW + timedelta(seconds=1)))
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(201, json={"ok": True})

    client_type = httpx.Client
    monkeypatch.setattr(ai_watcher.httpx, "Client", lambda **kwargs:
                        client_type(transport=httpx.MockTransport(handle), **kwargs))
    w = watcher(tmp_path, monkeypatch, registry)
    assert w.scan_all() == 2
    traces = requests[0]["traces"]
    assert {t["account_id"] for t in traces} == {account_id_for("claude_code", p) for p in (first, second)}
    assert "private-prompt" not in json.dumps(requests)
    assert len(list((tmp_path / ".aimonitor" / "folder_offsets").glob("*.json"))) == 2
    assert w.scan_all() == 0


@pytest.mark.parametrize("legacy_name", ["ai_offsets.json", "claude_offsets.json"])
def test_legacy_offsets_migrate_without_resending_and_seen_survives(tmp_path, monkeypatch, legacy_name):
    path = folder(tmp_path)
    log = path / "projects" / "a.jsonl"
    append(log, assistant("already-sent", NOW))
    state_dir = tmp_path / ".aimonitor"
    state_dir.mkdir()
    key = str(log.resolve())
    if legacy_name == "ai_offsets.json":
        key = "claude:" + key
    (state_dir / legacy_name).write_text(json.dumps({key: log.stat().st_size}))
    (state_dir / "ai_seen_claude.json").write_text(json.dumps(["already-sent:"]))
    w = watcher(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert w.scan_all() == 0
    append(log, assistant("already-sent", NOW))  # continuation of a response after old cursor
    append(log, assistant("new-response", NOW))
    assert w.scan_all() == 1
    assert len(sent) == 1
    assert sent[0]["account_id"] is None  # historical event, before first identity observation
    restarted = watcher(tmp_path, monkeypatch)
    monkeypatch.setattr(restarted, "_send_traces", lambda traces: pytest.fail("duplicate after migration"))
    assert restarted.scan_all() == 0


def test_failed_send_rolls_back_cursors_and_incomplete_line_is_not_consumed(tmp_path, monkeypatch):
    path = folder(tmp_path)
    log = path / "projects" / "a.jsonl"
    append(log, assistant("one", NOW))
    w = watcher(tmp_path, monkeypatch)
    monkeypatch.setattr(w, "_send_traces", lambda traces: False)
    assert w.scan_all() == 0
    monkeypatch.setattr(w, "_send_traces", lambda traces: True)
    assert w.scan_all() == 1
    with log.open("ab") as stream:
        stream.write(json.dumps(assistant("two", NOW)).encode())
    assert w.scan_all() == 0
    with log.open("ab") as stream:
        stream.write(b"\n")
    assert w.scan_all() == 1


def test_legacy_migration_cursor_survives_failed_delivery(tmp_path, monkeypatch):
    path = folder(tmp_path)
    log = path / "projects" / "a.jsonl"
    append(log, assistant("old-sent", NOW))
    cursor = log.stat().st_size
    state = tmp_path / ".aimonitor"
    state.mkdir()
    (state / "claude_offsets.json").write_text(json.dumps({str(log.resolve()): cursor}))
    append(log, assistant("new", NOW))
    w = watcher(tmp_path, monkeypatch)
    monkeypatch.setattr(w, "_send_traces", lambda traces: False)
    assert w.scan_all() == 0
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert w.scan_all() == 1
    assert len(sent) == 1 and sent[0]["trace_id"] == "cc-" + hashlib.sha256(b"new:").hexdigest()[:32]


def test_codex_traces_cached_tokens_cumulative_dedup_and_account_at_timestamp(tmp_path, monkeypatch):
    path = folder(tmp_path, ".codex", "codex")
    registry = AccountRegistry(home=tmp_path, environ={})
    account = registry.refresh(NOW)[0]["account_id"]
    monkeypatch.setattr(registry, "refresh", lambda: registry.list_accounts())
    log = path / "sessions" / "rollout-test.jsonl"
    append(log, {"type": "turn_context", "payload": {"model": "gpt-5"}})
    event = {"type": "event_msg", "timestamp": (NOW + timedelta(seconds=1)).isoformat(),
             "payload": {"type": "token_count", "info": {
                 "total_token_usage": {"total_tokens": 120},
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20}}}}
    append(log, event)
    append(log, event)
    w = watcher(tmp_path, monkeypatch, registry)
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert w.scan_all() == 1
    assert (sent[0]["prompt_tokens"], sent[0]["cache_read_tokens"], sent[0]["account_id"]) == (20, 80, account)
    assert sent[0]["model_name"] == "gpt-5" and "latency_ms" not in sent[0]
    append(log, event)
    assert w.scan_all() == 0


def test_claude_account_quotas_use_separate_credentials_and_caches(tmp_path, monkeypatch):
    first = folder(tmp_path)
    second = folder(tmp_path, ".claude-work", identifier="second-account")
    for path, token in ((first, "token-one"), (second, "token-two")):
        (path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": token}}))
    headers = []

    def get(url, **kwargs):
        token = kwargs["headers"]["Authorization"]
        headers.append(token)
        return httpx.Response(200, json={"five_hour": {"utilization": 25 if token.endswith("one") else 75}})

    monkeypatch.setattr("desktop.claude_quota_syncer.httpx.get", get)
    rows = quota.account_quotas(AccountRegistry(home=tmp_path, environ={}), force=True, now=NOW)
    assert set(headers) == {"Bearer token-one", "Bearer token-two"}
    assert sorted(r["quota_windows"][0]["remaining_pct"] for r in rows) == [25, 75]
    assert all(r["quota_windows"][1]["remaining_pct"] is None for r in rows)
    caches = list((tmp_path / ".aimonitor" / "quota").glob("*.json"))
    assert len(caches) == 2
    assert all("token-one" not in p.read_text() and "token-two" not in p.read_text() for p in caches)


def test_codex_quotas_unknown_and_current_accounts_remain_separate(tmp_path):
    path = folder(tmp_path, ".codex", "codex")
    registry = AccountRegistry(home=tmp_path, environ={})
    account = registry.refresh(NOW)[0]["account_id"]
    log = path / "sessions" / "rollout-test.jsonl"
    event = {"timestamp": (NOW - timedelta(minutes=1)).isoformat(), "payload": {"rate_limits": {
        "primary": {"used_percent": 30, "window_minutes": 300,
                    "resets_at": (NOW + timedelta(hours=1)).isoformat()}}}}
    append(log, event)
    rows = quota.account_quotas(registry, now=NOW)
    assert rows[0]["account_id"] is None and rows[0]["quota_windows"][0]["remaining_pct"] == 70
    assert rows[1]["account_id"] == account and rows[1]["quota_windows"][0]["remaining_pct"] is None
    event["timestamp"] = (NOW + timedelta(seconds=1)).isoformat()
    append(log, event)
    rows = quota.account_quotas(registry, now=NOW + timedelta(seconds=2))
    assert len(rows) == 1 and rows[0]["account_id"] == account


def test_historical_manifest_row_cannot_use_current_claude_credentials(tmp_path, monkeypatch):
    path = folder(tmp_path)
    registry = AccountRegistry(home=tmp_path, environ={})
    old_rows = registry.refresh(NOW)
    login(path, "claude_code", "different-account")
    monkeypatch.setattr(registry, "refresh", lambda **kwargs: old_rows)
    monkeypatch.setattr("desktop.claude_quota_syncer.httpx.get",
                        lambda *args, **kwargs: pytest.fail("current credentials used for old account"))
    rows = quota.account_quotas(registry, now=NOW)
    assert rows[0]["account_id"] == old_rows[0]["account_id"]
    assert rows[0]["quota_windows"][0]["remaining_pct"] is None


def test_old_claude_quota_api_and_custom_cache_path_remain_compatible(tmp_path, monkeypatch):
    path = folder(tmp_path)
    (path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "test-token"}}))
    cache = tmp_path / "isolated-cache.json"
    monkeypatch.setattr("desktop.claude_quota_syncer.CACHE_FILE", cache)
    monkeypatch.setattr("desktop.claude_quota_syncer.httpx.get",
                        lambda *args, **kwargs: httpx.Response(200, json={"five_hour": {"utilization": 0}}))
    result = ClaudeQuotaSyncer().get_quota(force=True)
    assert result["five_hour_remaining_pct"] == 100 and result["weekly_remaining_pct"] is None
    assert cache.exists()


def test_manual_copilot_and_antigravity_folders_attribute_known_log_times(tmp_path, monkeypatch):
    copilot = tmp_path / "custom-copilot"
    antigravity = tmp_path / "custom-antigravity"
    copilot.mkdir()
    antigravity.mkdir()
    registry = AccountRegistry(home=tmp_path, environ={})
    registry.add_folder("copilot", copilot)
    registry.add_folder("antigravity", antigravity)
    rows = registry.refresh(NOW + timedelta(days=1))
    monkeypatch.setattr(registry, "refresh", lambda: rows)
    # add_folder() stamps the real clock as the account's start, so the log must follow the real clock too.
    timestamp = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
    append(copilot / "session-state" / "session" / "events.jsonl", {
        "type": "session.shutdown", "timestamp": timestamp, "data": {"modelMetrics": {
            "gpt-5": {"usage": {"inputTokens": 10, "outputTokens": 20}}}}})
    append(antigravity / "brain" / "session" / ".system_generated" / "logs" / "transcript.jsonl", {
        "source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": timestamp,
        "status": "DONE", "content": "private-body"})
    w = ai_watcher.UnifiedAIWatcher(state_file=tmp_path / ".aimonitor" / "ai_offsets.json")
    w.accounts = registry
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert w.scan_all() == 2
    assert {t["account_id"] for t in sent} == {r["account_id"] for r in rows}
    assert "private-body" not in json.dumps(sent)
