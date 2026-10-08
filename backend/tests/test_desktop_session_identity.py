"""Aliases/copies share one collector cursor without replaying migrated sessions."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop.accounts import canonical_path
from desktop.ai_watcher import UnifiedAIWatcher, _session_key


@pytest.fixture(autouse=True)
def isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)


def _events(tool, message_id=True):
    if tool == "claude":
        message = {"model": "claude-opus-4-1", "usage": {"input_tokens": 10, "output_tokens": 20}}
        if message_id:
            message["id"] = "response-one"
        return [{"type": "assistant", "timestamp": "2026-10-07T01:00:00+00:00", "message": message}]
    return [{"type": "turn_context", "payload": {"model": "gpt-5"}},
            {"type": "event_msg", "timestamp": "2026-10-07T01:00:00+00:00", "payload": {
                "type": "token_count", "info": {"total_token_usage": {"total_tokens": 30},
                                                "last_token_usage": {"input_tokens": 10,
                                                                     "output_tokens": 20}}}}]


def _append(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        for event in events:
            stream.write((json.dumps(event) + "\n").encode())


def _pair(tmp_path, tool, kind, message_id=True, renamed=False):
    name = ".claude" if tool == "claude" else ".codex"
    structure = "projects" if tool == "claude" else "sessions"
    first, second = tmp_path / name, tmp_path / (name + "-copy")
    root1, root2 = first / structure, second / structure
    relative = Path("project/session.jsonl" if tool == "claude" else "2026/10/07/rollout-session.jsonl")
    file1 = root1 / relative
    _append(file1, _events(tool, message_id))
    second.mkdir(parents=True)
    if kind == "junction":
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(root2), str(root1)],
                           check=True, capture_output=True)
        else:
            root2.symlink_to(root1, target_is_directory=True)
    else:
        file2 = root2 / (relative.with_name("renamed.jsonl") if renamed else relative)
        file2.parent.mkdir(parents=True)
        if kind == "hardlink":
            os.link(file1, file2)
        else:
            shutil.copyfile(file1, file2)
    file2 = root2 / (relative.with_name("renamed.jsonl") if renamed else relative)
    return first, second, file1, file2


def _watcher(tmp_path, monkeypatch, tool, directories, state="state"):
    w = UnifiedAIWatcher(state_file=tmp_path / state / "ai_offsets.json")
    account_tool = "claude_code" if tool == "claude" else "codex"
    rows = [{"tool": account_tool, "config_dir": str(directory), "account_id": account_tool + ":" + str(i)}
            for i, directory in enumerate(directories)]
    accounts = {str(canonical_path(Path(row["config_dir"]))): row["account_id"] for row in rows}
    w.accounts = SimpleNamespace(
        refresh=lambda: rows, account_at=lambda directory, timestamp: accounts[str(canonical_path(directory))]
    )
    monkeypatch.setattr(w, "_scan_antigravity", lambda: [])
    monkeypatch.setattr(w, "_scan_copilot", lambda: [])
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    return w, sent


@pytest.mark.parametrize("tool", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["hardlink", "junction", "copy"])
def test_aliases_and_copies_generate_one_trace_owned_by_first_account(tmp_path, monkeypatch, tool, kind):
    first, second, _, _ = _pair(tmp_path, tool, kind)
    w, sent = _watcher(tmp_path, monkeypatch, tool, [first, second])
    assert w.scan_all() == 1
    assert len(sent) == 1 and sent[0]["account_id"].endswith(":0")
    assert w.duplicate_session_files == 1
    assert len(w.file_alias_conflicts) == 1
    assert w.file_alias_conflicts[0]["first_config_dir"] == str(first)
    assert w.scan_all() == 0
    restarted, again = _watcher(tmp_path, monkeypatch, tool, [second], state="state")
    assert restarted.scan_all() == 0 and again == []


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_hardlinks_with_different_relative_names_are_still_one_file(tmp_path, monkeypatch, tool):
    first, second, _, _ = _pair(tmp_path, tool, "hardlink", renamed=True)
    w, sent = _watcher(tmp_path, monkeypatch, tool, [first, second])
    assert w.scan_all() == 1 and len(sent) == 1
    restarted, again = _watcher(tmp_path, monkeypatch, tool, [second])
    assert restarted.scan_all() == 0 and again == []


@pytest.mark.parametrize("tool,message_id", [("claude", True), ("claude", False), ("codex", True)])
def test_trace_id_is_independent_of_configuration_folder(tmp_path, monkeypatch, tool, message_id):
    first, second, _, _ = _pair(tmp_path, tool, "copy", message_id=message_id)
    w1, sent1 = _watcher(tmp_path, monkeypatch, tool, [first], state="one")
    w2, sent2 = _watcher(tmp_path, monkeypatch, tool, [second], state="two")
    assert w1.scan_all() == w2.scan_all() == 1
    assert sent1[0]["trace_id"] == sent2[0]["trace_id"]


@pytest.mark.parametrize("tool", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["hardlink", "copy"])
def test_migration_uses_largest_legacy_absolute_cursor_without_replay(tmp_path, monkeypatch, tool, kind):
    first, second, file1, file2 = _pair(tmp_path, tool, kind, message_id=False)
    state = tmp_path / "state"
    state.mkdir()
    (state / "ai_offsets.json").write_text(json.dumps({
        f"{tool}:{file1.resolve()}": 0, f"{tool}:{file2.resolve()}": file2.stat().st_size,
    }))
    w, sent = _watcher(tmp_path, monkeypatch, tool, [first, second])
    assert w.scan_all() == 0 and sent == []
    stored = json.loads((state / "ai_offsets.json").read_text(encoding="utf-8"))
    key = _session_key(tool, file1, first / ("projects" if tool == "claude" else "sessions"))
    assert stored[key] == file1.stat().st_size
    assert not any(str(file1.resolve()) in key or str(file2.resolve()) in key for key in stored)
    folders = list((state / "folder_offsets").glob("*.json"))
    assert len(folders) == 2
    assert all(json.loads(path.read_text(encoding="utf-8"))[key] == file1.stat().st_size for path in folders)


@pytest.mark.parametrize("tool", ["claude", "codex"])
def test_folder_state_migration_preserves_maximum_cursor(tmp_path, monkeypatch, tool):
    first, second, file1, file2 = _pair(tmp_path, tool, "copy", message_id=False)
    w, sent = _watcher(tmp_path, monkeypatch, tool, [first, second])
    tool_name = "claude_code" if tool == "claude" else "codex"
    for directory, file, offset in ((first, file1, 0), (second, file2, file2.stat().st_size)):
        target = w._folder_state(tool_name, directory)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({f"{tool}:{file.resolve()}": offset}))
    assert w.scan_all() == 0 and sent == []


def test_shorter_copy_cannot_reset_migrated_codex_cursor(tmp_path, monkeypatch):
    first, second, file1, file2 = _pair(tmp_path, "codex", "copy")
    event = _events("codex")[-1]
    event["payload"]["info"]["total_token_usage"]["total_tokens"] = 60
    _append(file2, [event])
    state = tmp_path / "state"
    state.mkdir()
    (state / "ai_offsets.json").write_text(json.dumps({f"codex:{file2.resolve()}": file2.stat().st_size}))
    w, sent = _watcher(tmp_path, monkeypatch, "codex", [first, second])
    assert file1.stat().st_size < file2.stat().st_size
    assert w.scan_all() == 0 and sent == []


def test_standalone_claude_cursor_and_seen_keys_survive_copy_migration(tmp_path, monkeypatch):
    first, second, file1, _ = _pair(tmp_path, "claude", "copy")
    state = tmp_path / "state"
    state.mkdir()
    (state / "claude_offsets.json").write_text(json.dumps({str(file1.resolve()): file1.stat().st_size}))
    (state / "ai_seen_claude.json").write_text(json.dumps(["response-one:"]))
    w, sent = _watcher(tmp_path, monkeypatch, "claude", [first, second])
    assert w.scan_all() == 0 and sent == []
    _append(file1, _events("claude"))  # another content block of the already sent response
    assert w.scan_all() == 0 and sent == []


def test_standalone_claude_multiple_absolute_aliases_migrate_maximum_cursor(tmp_path, monkeypatch):
    first, second, file1, file2 = _pair(tmp_path, "claude", "copy", message_id=False)
    state = tmp_path / "state"
    state.mkdir()
    (state / "claude_offsets.json").write_text(json.dumps({
        str(file1.resolve()): 0, str(file2.resolve()): file2.stat().st_size,
    }))
    w, sent = _watcher(tmp_path, monkeypatch, "claude", [first, second])
    assert w.scan_all() == 0 and sent == []


def test_migrated_cursor_survives_failure_and_retry(tmp_path, monkeypatch):
    first, second, file1, _ = _pair(tmp_path, "codex", "hardlink")
    old_cursor = file1.stat().st_size
    event = _events("codex")[-1]
    event["payload"]["info"]["total_token_usage"]["total_tokens"] = 60
    _append(file1, [event])
    state = tmp_path / "state"
    state.mkdir()
    (state / "ai_offsets.json").write_text(json.dumps({f"codex:{file1.resolve()}": old_cursor}))
    w, sent = _watcher(tmp_path, monkeypatch, "codex", [first, second])
    monkeypatch.setattr(w, "_send_traces", lambda traces: False)
    assert w.scan_all() == 0
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert w.scan_all() == 1 and len(sent) == 1


def test_zero_file_identifiers_do_not_merge_unrelated_sessions(tmp_path, monkeypatch):
    first, _, file1, _ = _pair(tmp_path, "codex", "copy")
    different = file1.with_name("different-session.jsonl")
    shutil.copyfile(file1, different)
    real_stat = Path.stat

    def stat_with_no_identity(path, *args, **kwargs):
        stat = list(real_stat(path, *args, **kwargs))
        stat[1] = stat[2] = 0
        return os.stat_result(stat)

    monkeypatch.setattr(Path, "stat", stat_with_no_identity)
    w, sent = _watcher(tmp_path, monkeypatch, "codex", [first])
    assert w.scan_all() == 2 and len(sent) == 2
    assert len({trace["trace_id"] for trace in sent}) == 2
