"""Detector failures and non-default profiles use only synthetic local fixtures."""
import json
from pathlib import Path

import httpx
import pytest

from desktop import ai_detector, config, context_manager


@pytest.fixture
def detector(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for variable, directory in (
        ("HOME", tmp_path), ("USERPROFILE", tmp_path),
        ("APPDATA", tmp_path / "roaming"), ("LOCALAPPDATA", tmp_path / "local"),
        ("CLAUDE_CONFIG_DIR", tmp_path / ".claude"), ("CODEX_HOME", tmp_path / ".codex"),
    ):
        monkeypatch.setenv(variable, str(directory))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    original_client = httpx.Client
    monkeypatch.setattr(ai_detector, "detector", ai_detector.AIDetector())
    monkeypatch.setattr(ai_detector.AIDetector, "_check_registered_api_keys", lambda self: [])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setattr(context_manager, "windows_for", lambda *a, **k: [])
    monkeypatch.setattr("backend.app.services.api_key_manager.spend_service.spend_all", lambda keys: {})
    return ai_detector.detector


@pytest.mark.parametrize("error", [PermissionError, FileNotFoundError])
def test_cursor_folder_failure_isolated(client, detector, monkeypatch, tmp_path, error):
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    denied = detector.appdata / "Cursor" / "User" / "workspaceStorage"
    denied.mkdir(parents=True)
    original = Path.iterdir

    def iterdir(path):
        if path == denied:
            raise error("private-path-or-secret")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    candidates = detector.scan_all_candidates()
    assert candidates["codex"]["is_connected"] is True
    failed = candidates["cursor"]
    assert failed["is_connected"] is None
    assert failed["detection_status"] == "unavailable"
    assert failed["plan_name"] == "감지 불가"
    assert "private-path-or-secret" not in str(candidates)
    assert candidates["windsurf"]["is_connected"] is False
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    assert response.json()["by_key"]["cursor"]["detection_status"] == "unavailable"
    assert "codex" in response.json()["by_key"]
    assert "private-path-or-secret" not in response.text


def test_one_unexpected_detector_failure_does_not_abort_scan(detector, monkeypatch):
    def broken():
        raise ValueError("private-credential")

    monkeypatch.setattr(detector, "_check_claude", broken)
    candidates = detector.scan_all_candidates()
    assert candidates["claude"]["detection_status"] == "unavailable"
    assert "codex" in candidates
    assert "private-credential" not in str(candidates)


@pytest.mark.parametrize("source", ["environment", "sibling", "manual"])
def test_custom_profile_is_connected_without_writes(client, detector, monkeypatch, tmp_path, source):
    folders = []
    for tool, environment, sibling, structure in (
        ("claude_code", "CLAUDE_CONFIG_DIR", ".claude-work", "projects"),
        ("codex", "CODEX_HOME", ".codex-work", "sessions"),
    ):
        directory = tmp_path / (sibling if source == "sibling" else f"custom-{tool}")
        directory.mkdir()
        if source == "environment":
            monkeypatch.setenv(environment, str(directory))
            auth_name = ".credentials.json" if tool == "claude_code" else "auth.json"
            (directory / auth_name).write_text("{}", encoding="utf-8")
        else:
            (directory / structure).mkdir()
        if source == "manual":
            folders.append({"tool": tool, "config_dir": str(directory), "source": "manual"})
    if folders:
        manifest = tmp_path / ".aimonitor" / "accounts.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps(folders), encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    assert {row["key"] for row in response.json()["items"]} == {"claude", "codex"}
    assert all(row["quota_windows"] == [] for row in response.json()["items"])
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


def test_custom_account_does_not_inherit_default_plan(client, session, detector, monkeypatch, tmp_path):
    from backend.app.core.timeutil import utc_now
    from backend.app.models.trace import TraceEvent

    default = tmp_path / ".claude"
    default.mkdir()
    (default / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"subscriptionType": "max"},
    }), encoding="utf-8")
    session.add(TraceEvent(
        trace_id="other-account", timestamp=utc_now(), provider="anthropic", model_name="m",
        tags="claude-code", account_id="claude_code:dir-abcdef",
    ))
    session.commit()
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    item = response.json()["by_key"]["claude"]
    assert item["plan_name"] == "Claude Max"
    assert item["accounts"][0]["plan_name"] == "플랜 확인 불가"
    assert item["accounts"][0]["quota_windows"] is None
