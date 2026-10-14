"""Old Windows path cursors must survive collector upgrades without replay."""

import json
from pathlib import Path

import httpx
import pytest

from backend.tests.test_accounts_watcher_regressions import batch_results
from desktop import ai_watcher
from desktop.accounts import AccountRegistry, canonical_path


def _append(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write((json.dumps(event) + "\n").encode("utf-8"))


def _event(tool, number):
    timestamp = f"2026-10-07T01:00:0{number}+00:00"
    if tool == "claude":
        return {"type": "assistant", "timestamp": timestamp, "message": {
            "id": f"Response-{number}", "model": "claude-opus-4-1",
            "usage": {"input_tokens": 10, "output_tokens": 20}}}
    if tool == "antigravity":
        return {"source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
                "created_at": timestamp, "content": "synthetic-output"}
    return {"type": "session.shutdown", "id": f"Shutdown-{number}", "timestamp": timestamp,
            "data": {"modelMetrics": {"gpt-5": {"usage": {"inputTokens": 10, "outputTokens": 20},
                                                "requests": {"count": 1}}}}}


def _cursors(path, offset, form, prefix):
    original = str(path.resolve())  # Exactly the 4dcfb8e state format.
    normalized = str(canonical_path(path))
    if form == "legacy":
        values = {original: offset}
    elif form == "canonical":
        values = {normalized: offset}
    elif form == "both_legacy_max":
        values = {normalized: 0, original: offset}
    elif form == "both_canonical_max":
        values = {original: 0, normalized: offset}
    else:
        # Mixed separators and unresolved dot segments occur in imported cursors.
        spelling = str(path.parent.resolve()) + "/../" + path.parent.name + "/" + path.name
        values = {spelling: offset}
    return {f"{prefix}:{key}" if prefix else key: value for key, value in values.items()}


def _watcher(home, state, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    watcher = ai_watcher.UnifiedAIWatcher(state_file=state / "ai_offsets.json")
    watcher.accounts = AccountRegistry(state_dir=state, home=home, environ={})
    monkeypatch.setattr(ai_watcher, "backend_headers", lambda: {})
    sent = []

    def receive(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"app": "AIMONITOR", "status": "ok"})
        traces = json.loads(request.content)["traces"]
        sent.extend(traces)
        return httpx.Response(201, json=batch_results(traces))

    client = httpx.Client
    monkeypatch.setattr(ai_watcher.httpx, "Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(receive), **kwargs))
    return watcher, sent


@pytest.mark.parametrize("form", ["legacy", "canonical", "both_legacy_max", "both_canonical_max", "spelling"])
def test_old_path_cursors_skip_first_scan_and_collect_only_appended_lines(tmp_path, monkeypatch, form):
    home = tmp_path / "MiXeD-Home"
    state = tmp_path / "state"
    state.mkdir()
    files = {
        "antigravity": home / ".gemini/antigravity/brain/Conversation/.system_generated/logs/transcript.jsonl",
        "copilot": home / ".copilot/session-state/Session/events.jsonl",
        "claude": home / ".claude/projects/Project/Session.jsonl",
    }
    cursors = {}
    for tool, path in files.items():
        _append(path, _event(tool, 1))
        cursors.update(_cursors(path, path.stat().st_size, form, tool))
    (state / "ai_offsets.json").write_text(json.dumps(cursors), encoding="utf-8")
    watcher, sent = _watcher(home, state, monkeypatch)

    assert watcher.scan_all() == 0
    assert sent == []
    for tool, path in files.items():
        _append(path, _event(tool, 2))
    assert watcher.scan_all() == 3
    assert len(sent) == 3
    assert {trace["timestamp"] for trace in sent} == {"2026-10-07T01:00:02+00:00"}
    assert watcher.scan_all() == 0

    # A restart uses one saved path spelling and keeps the advanced cursors.
    restarted = ai_watcher.UnifiedAIWatcher(state_file=state / "ai_offsets.json")
    restarted.accounts = AccountRegistry(state_dir=state, home=home, environ={})
    assert restarted.scan_all() == 0
    stored = json.loads((state / "ai_offsets.json").read_text(encoding="utf-8"))
    for tool in ("antigravity", "copilot"):
        keys = [key for key in stored if key.startswith(tool + ":")]
        assert keys == [f"{tool}:{canonical_path(files[tool])}"]
        assert stored[keys[0]] == files[tool].stat().st_size


@pytest.mark.parametrize("source,form", [
    (source, form) for source in ("standalone", "folder")
    for form in ("legacy", "both_legacy_max", "spelling")
] + [("folder", "session_spelling")])
def test_claude_legacy_and_folder_cursors_preserve_seen_and_append(tmp_path, monkeypatch, source, form):
    home = tmp_path / "MiXeD-Home"
    state = tmp_path / "state"
    state.mkdir()
    directory = home / ".claude"
    path = directory / "projects/Project/Session.jsonl"
    _append(path, _event("claude", 1))
    watcher, sent = _watcher(home, state, monkeypatch)
    if source == "standalone":
        target = state / "claude_offsets.json"
        prefix = ""
    else:
        target = watcher._folder_state("claude_code", directory)
        target.parent.mkdir(parents=True)
        prefix = "claude"
    cursors = _cursors(path, path.stat().st_size, form, prefix)
    if form == "session_spelling":
        cursors = {"claude:session:Project/./Session.jsonl": path.stat().st_size}
    target.write_text(json.dumps(cursors), encoding="utf-8")
    # Vendor IDs and opaque content hashes are case sensitive, never path keys.
    seen = ["MiXeD-Response:Request-ID", "cc-0123456789ABCDEF"]
    (state / "ai_seen_claude.json").write_text(json.dumps(seen), encoding="utf-8")
    watcher._seen_claude = watcher._load_seen()

    assert watcher.scan_all() == 0
    assert sent == []
    assert list(watcher._seen_claude) == seen
    _append(path, _event("claude", 2))
    assert watcher.scan_all() == 1
    assert len(sent) == 1
    assert sent[0]["timestamp"] == "2026-10-07T01:00:02+00:00"


@pytest.mark.parametrize("tool", ["antigravity", "copilot"])
def test_folder_path_cursors_merge_legacy_spellings(tmp_path, monkeypatch, tool):
    home = tmp_path / "MiXeD-Home"
    state = tmp_path / "state"
    state.mkdir()
    directory = home / "MaNuAl"
    relative = ("brain/Conversation/.system_generated/logs/transcript.jsonl"
                if tool == "antigravity" else "session-state/Session/events.jsonl")
    path = directory / relative
    _append(path, _event(tool, 1))
    watcher, sent = _watcher(home, state, monkeypatch)
    watcher.accounts.add_folder(tool, directory)
    target = watcher._folder_state(tool, directory)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(_cursors(path, path.stat().st_size, "both_legacy_max", tool)),
                      encoding="utf-8")
    assert watcher.scan_all() == 0
    assert sent == []
    _append(path, _event(tool, 2))
    assert watcher.scan_all() == 1
    assert len(sent) == 1


def test_normalized_unified_session_cursor_still_overrides_stale_standalone_state(tmp_path, monkeypatch):
    home = tmp_path / "MiXeD-Home"
    state = tmp_path / "state"
    state.mkdir()
    path = home / ".claude/projects/Project/Session.jsonl"
    _append(path, _event("claude", 1))
    (state / "ai_offsets.json").write_text(
        json.dumps({"claude:session:Project/./Session.jsonl": 0}), encoding="utf-8")
    (state / "claude_offsets.json").write_text(
        json.dumps({str(path.resolve()): path.stat().st_size}), encoding="utf-8")
    watcher, sent = _watcher(home, state, monkeypatch)
    assert watcher.scan_all() == 1
    assert len(sent) == 1
