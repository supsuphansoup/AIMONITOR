"""Collector regressions use only temporary vendor-shaped log files."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from desktop import ai_watcher
from desktop.accounts import AccountRegistry

NOW = datetime(2026, 10, 14, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(ai_watcher, "backend_headers", lambda: {})


def event(tool, identifier="one"):
    if tool == "claude":
        return {"type": "assistant", "timestamp": NOW.isoformat(), "message": {
            "id": identifier, "model": "claude-sonnet-4-5",
            "usage": {"input_tokens": 10, "output_tokens": 20}}}
    if tool == "codex":
        return {"type": "event_msg", "timestamp": NOW.isoformat(), "payload": {
            "type": "token_count", "info": {"last_token_usage": {"input_tokens": 10, "output_tokens": 20},
                                            "total_token_usage": {"total_tokens": 30}}}}
    if tool == "copilot":
        return {"type": "session.shutdown", "id": identifier, "timestamp": NOW.isoformat(),
                "data": {"modelMetrics": {"gpt-5": {"usage": {"inputTokens": 10, "outputTokens": 20},
                                                  "requests": {"count": 1, "cost": 7}}}}}
    return {"source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": NOW.isoformat(),
            "status": "DONE", "content": "synthetic private body"}


def log_path(home, tool):
    return {
        "claude": home / ".claude/projects/p/session.jsonl",
        "codex": home / ".codex/sessions/rollout-session.jsonl",
        "copilot": home / ".copilot/session-state/s/events.jsonl",
        "antigravity": home / ".gemini/antigravity/brain/s/.system_generated/logs/transcript.jsonl",
    }[tool]


def append(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n")


def watcher(home, monkeypatch):
    instance = ai_watcher.UnifiedAIWatcher(
        backend_url="http://127.0.0.1:18772", state_file=home / ".aimonitor/ai_offsets.json",
    )
    instance.accounts = AccountRegistry(state_dir=instance.state_file.parent, home=home, environ={})
    sent = []
    monkeypatch.setattr(instance, "_send_traces", lambda traces: sent.extend(copy.deepcopy(traces)) or True)
    return instance, sent


def test_acc02_antigravity_waits_for_newline_with_multibyte_text(tmp_path, monkeypatch):
    path = log_path(tmp_path, "antigravity")
    path.parent.mkdir(parents=True)
    raw = json.dumps(dict(event("antigravity"), content="한글 출력"), ensure_ascii=False).encode("utf-8")
    cut = raw.index("글".encode("utf-8")) + 1
    path.write_bytes(raw[:cut])
    instance, sent = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 0
    with path.open("ab") as stream:
        stream.write(raw[cut:])
    assert instance.scan_all() == 0
    with path.open("ab") as stream:
        stream.write(b"\n")
    assert instance.scan_all() == 1
    assert len(sent) == 1
    assert instance.scan_all() == 0


@pytest.mark.parametrize("tool", ["claude", "codex", "copilot", "antigravity"])
@pytest.mark.parametrize("bad", ["[]\n", "{broken private body}\n"])
def test_acc04_corrupt_completed_line_only_skips_itself(tmp_path, monkeypatch, caplog, tool, bad):
    path = log_path(tmp_path, tool)
    append(path, [event(tool)])
    path.write_bytes(bad.encode("utf-8") + path.read_bytes())
    instance, sent = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 1
    assert instance.scan_all() == 0
    assert len(sent) == 1
    assert "private body" not in caplog.text


def test_acc04_invalid_claude_usage_does_not_poison_response_seen_key(tmp_path, monkeypatch):
    path = log_path(tmp_path, "claude")
    bad = event("claude")
    bad["message"]["usage"]["cache_creation"] = {"ephemeral_1h_input_tokens": "invalid"}
    append(path, [bad, event("claude")])
    instance, sent = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 1
    assert len(sent) == 1


def test_acc13_antigravity_uses_only_observed_model_and_provider(tmp_path, monkeypatch):
    instance, _ = watcher(tmp_path, monkeypatch)
    path = log_path(tmp_path, "antigravity")
    unknown = instance._parse_antigravity_line(json.dumps(event("antigravity")), path)
    assert (unknown["model_name"], unknown["provider"]) == ("unknown", "unknown")
    observed = instance._parse_antigravity_line(json.dumps(dict(
        event("antigravity"), model="synthetic-model", provider="custom",
    )), path)
    assert (observed["model_name"], observed["provider"]) == ("synthetic-model", "custom")


def test_acc21_antigravity_input_estimate_uses_only_identified_body(tmp_path, monkeypatch):
    instance, _ = watcher(tmp_path, monkeypatch)
    path = log_path(tmp_path, "antigravity")
    obj = event("antigravity")
    missing = instance._parse_antigravity_line(json.dumps(obj), path, curr_offset=100)
    expanded = instance._parse_antigravity_line(json.dumps(dict(obj, unrelated_metadata="x" * 4000)),
                                                path, curr_offset=4100)
    assert missing["prompt_tokens"] == expanded["prompt_tokens"] == 0
    assert missing["metadata"]["input_tokens_estimate"] == "unavailable"
    known = instance._parse_antigravity_line(json.dumps(dict(obj, input_text="x" * 40)), path)
    assert known["prompt_tokens"] == 10
    assert known["metadata"]["tokens"] == "estimated"
    assert "synthetic private body" not in json.dumps(known)
