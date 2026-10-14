"""Collector regressions use only temporary vendor-shaped log files."""
import copy
import json
import re
import threading
import time
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


def test_acc05_registry_creation_failure_preserves_collection(tmp_path, monkeypatch):
    append(log_path(tmp_path, "claude"), [event("claude")])

    def denied(*args, **kwargs):
        raise PermissionError("synthetic registry failure")

    monkeypatch.setattr(ai_watcher, "AccountRegistry", denied)
    instance = ai_watcher.UnifiedAIWatcher(state_file=tmp_path / ".aimonitor/ai_offsets.json")
    sent = []
    monkeypatch.setattr(instance, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert instance.scan_all() == 1
    assert sent[0]["account_id"] is None


def test_acc05_registry_refresh_failure_preserves_collection(tmp_path, monkeypatch):
    append(log_path(tmp_path, "claude"), [event("claude")])
    instance, sent = watcher(tmp_path, monkeypatch)

    def denied():
        raise PermissionError("synthetic registry failure")

    monkeypatch.setattr(instance.accounts, "refresh", denied)
    assert instance.scan_all() == 1
    assert sent[0]["account_id"] is None


def test_acc18_timeline_is_read_once_per_scan(tmp_path, monkeypatch):
    append(log_path(tmp_path, "claude"), [event("claude", str(i)) for i in range(20)])
    instance, _ = watcher(tmp_path, monkeypatch)
    reads = []
    original = instance.accounts.read_timeline

    def counted():
        reads.append(1)
        return original()

    monkeypatch.setattr(instance.accounts, "read_timeline", counted)
    assert instance.scan_all() == 20
    assert len(reads) == 1


def test_acc15_machine_lock_survives_stop_join_timeout(tmp_path, monkeypatch):
    instance, _ = watcher(tmp_path, monkeypatch)
    successor, _ = watcher(tmp_path, monkeypatch)
    entered, finish = threading.Event(), threading.Event()

    def blocked_scan():
        entered.set()
        finish.wait(5)

    monkeypatch.setattr(instance, "scan_all", blocked_scan)
    try:
        assert instance.start()
        assert entered.wait(2)
        instance.stop()
        assert instance._thread.is_alive()
        assert instance.is_active
        assert not successor._acquire_lock()
        finish.set()
        instance._thread.join(2)
        assert not instance.is_active
        assert successor._acquire_lock()
    finally:
        finish.set()
        instance.stop()
        successor.stop()


@pytest.mark.parametrize("stop_after_first", [False, True])
def test_acc15_no_new_network_requests_after_stop(tmp_path, monkeypatch, stop_after_first):
    import httpx

    instance, _ = watcher(tmp_path, monkeypatch)
    calls = []

    def accept(request):
        calls.append(request)
        instance._stop_event.set()
        return httpx.Response(200, json=[])

    real_client = httpx.Client
    monkeypatch.setattr(ai_watcher.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(accept), **kw,
    ))
    if not stop_after_first:
        instance._stop_event.set()
    assert ai_watcher.UnifiedAIWatcher._send_traces(instance, [{"dedupe_key": "cc-synthetic"}] * 201) is False
    assert len(calls) == (1 if stop_after_first else 0)


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


@pytest.mark.parametrize("tool", ["claude", "codex", "copilot", "antigravity"])
@pytest.mark.parametrize("vendor_id", [False, True])
def test_acc01_keys_survive_failed_delivery_and_restart(tmp_path, monkeypatch, tool, vendor_id):
    obj = event(tool)
    if not vendor_id:
        obj.pop("id", None)
        if tool == "claude":
            obj["message"].pop("id")
    append(log_path(tmp_path, tool), [obj])
    instance, _ = watcher(tmp_path, monkeypatch)
    attempts = []
    monkeypatch.setattr(instance, "_send_traces", lambda rows: attempts.append(copy.deepcopy(rows)) or False)
    assert instance.scan_all() == 0
    assert instance.scan_all() == 0
    restarted, _ = watcher(tmp_path, monkeypatch)
    monkeypatch.setattr(restarted, "_send_traces", lambda rows: attempts.append(copy.deepcopy(rows)) or True)
    assert restarted.scan_all() == 1
    keys = [rows[0]["dedupe_key"] for rows in attempts]
    assert len(set(keys)) == 1
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", keys[0])
    assert all(rows[0]["trace_id"] == keys[0] for rows in attempts)


def test_acc01_batch_posts_keep_keys_after_partial_success(tmp_path, monkeypatch):
    import httpx

    append(log_path(tmp_path, "claude"), [event("claude", str(index)) for index in range(201)])
    instance, _ = watcher(tmp_path, monkeypatch)
    monkeypatch.setattr(instance, "_send_traces", ai_watcher.UnifiedAIWatcher._send_traces.__get__(instance))
    bodies = []

    def receive(request):
        bodies.append(json.loads(request.content)["traces"])
        return httpx.Response(503 if len(bodies) == 2 else 201)

    client = httpx.Client
    monkeypatch.setattr(ai_watcher.httpx, "Client", lambda **kwargs:
                        client(transport=httpx.MockTransport(receive), **kwargs))
    assert instance.scan_all() == 0
    assert instance.scan_all() == 201
    assert [len(body) for body in bodies] == [200, 1, 200, 1]
    assert [row["dedupe_key"] for row in bodies[0]] == [row["dedupe_key"] for row in bodies[2]]
    assert bodies[1][0]["dedupe_key"] == bodies[3][0]["dedupe_key"]


@pytest.mark.parametrize("tool", ["claude", "codex", "copilot", "antigravity"])
def test_acc03_replaced_same_size_file_starts_new_generation(tmp_path, monkeypatch, tool):
    path = log_path(tmp_path, tool)
    append(path, [event(tool)])
    instance, sent = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 1
    replacement = json.loads(json.dumps(event(tool)).replace('"one"', '"two"')
                             .replace('"total_tokens": 30', '"total_tokens": 31')
                             .replace("private body", "changed body"))
    temporary = path.with_suffix(".replacement")
    append(temporary, [replacement])
    assert temporary.stat().st_size == path.stat().st_size
    temporary.replace(path)
    assert instance.scan_all() == 1
    assert len(sent) == 2
    assert sent[0]["dedupe_key"] != sent[1]["dedupe_key"]


def test_acc03_in_place_rewrite_with_same_context_does_not_reuse_old_cursor(tmp_path, monkeypatch):
    path = log_path(tmp_path, "codex")
    context = {"type": "turn_context", "payload": {"model": "gpt-5"}}
    append(path, [context, event("codex")])
    instance, sent = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 1
    replacement = event("codex")
    replacement["payload"]["info"]["total_token_usage"]["total_tokens"] = 31
    path.write_bytes(b"")
    append(path, [context, replacement, {"type": "padding", "value": "x" * 100}])
    assert instance.scan_all() == 1
    assert sent[0]["dedupe_key"] != sent[1]["dedupe_key"]


def test_acc08_codex_reads_only_new_bytes_and_persists_model(tmp_path, monkeypatch):
    path = log_path(tmp_path, "codex")
    append(path, [{"type": "turn_context", "payload": {"model": "gpt-5"}},
                  *[{"type": "padding", "value": "x" * 1000} for _ in range(1000)], event("codex")])
    instance, _ = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 1
    original_open = Path.open
    read_bytes = [0]

    class CountingReader:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, *args):
            raw = self.stream.read(*args)
            read_bytes[0] += len(raw)
            return raw

        def readline(self, *args):
            raw = self.stream.readline(*args)
            read_bytes[0] += len(raw)
            return raw

        def __iter__(self):
            for raw in self.stream:
                read_bytes[0] += len(raw)
                yield raw

    def open_file(file, mode="r", *args, **kwargs):
        stream = original_open(file, mode, *args, **kwargs)
        return CountingReader(stream) if file == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", open_file)
    updated = event("codex")
    updated["payload"]["info"]["total_token_usage"]["total_tokens"] = 60
    append(path, [updated])
    restarted, sent = watcher(tmp_path, monkeypatch)
    assert restarted.scan_all() == 1
    assert sent[0]["model_name"] == "gpt-5"
    assert read_bytes[0] < 8192


def test_acc18_missing_offsets_are_pruned_after_30_days(tmp_path, monkeypatch):
    clock = [NOW.timestamp()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    path = log_path(tmp_path, "codex")
    append(path, [event("codex")])
    instance, _ = watcher(tmp_path, monkeypatch)
    assert instance.scan_all() == 1
    path.unlink()
    instance.scan_all()
    assert instance._offsets
    clock[0] += 30 * 86400 - 1
    instance.scan_all()
    assert instance._offsets
    clock[0] += 2
    instance.scan_all()
    assert instance._offsets == {}
    for state in (instance.state_file.parent / "folder_offsets").glob("*.json"):
        assert json.loads(state.read_text(encoding="utf-8")) == {}
    append(path, [event("codex")])
    restarted, sent = watcher(tmp_path, monkeypatch)
    assert restarted.scan_all() == 1
    assert sent[0]["dedupe_key"]
