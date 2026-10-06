"""UnifiedAIWatcher: one count per Claude response, one watcher per machine."""
import json
from pathlib import Path

from desktop.ai_watcher import UnifiedAIWatcher


def _watcher(tmp_path: Path) -> UnifiedAIWatcher:
    w = UnifiedAIWatcher(backend_url="http://127.0.0.1:9", state_file=tmp_path / "state" / "offsets.json")
    w.claude_dir = tmp_path / "claude"
    # Other scanners also look at real per-user locations; keep these tests to Claude only.
    w._scan_antigravity = lambda: []
    w._scan_copilot = lambda: []
    w._scan_gpt = lambda: []
    return w


def _assistant_line(msg_id: str, request_id: str, block: str) -> str:
    return json.dumps({
        "type": "assistant",
        "requestId": request_id,
        "timestamp": "2026-10-02T07:00:00Z",
        "message": {
            "id": msg_id,
            "model": "claude-opus-4-1",
            "content": [{"type": block}],
            "usage": {"input_tokens": 10, "output_tokens": 20,
                      "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40},
        },
    })


def _write_session(tmp_path: Path, lines: list, name: str = "s1.jsonl") -> Path:
    d = tmp_path / "claude" / "projects" / "p"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    with f.open("a", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")
    return f


def test_lines_of_one_response_are_counted_once(tmp_path, monkeypatch):
    w = _watcher(tmp_path)
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    # Claude Code splits one response into a line per content block, each repeating usage.
    _write_session(tmp_path, [
        _assistant_line("msg_A", "req_A", "thinking"),
        _assistant_line("msg_A", "req_A", "text"),
        _assistant_line("msg_A", "req_A", "tool_use"),
        _assistant_line("msg_B", "req_B", "text"),
    ])
    assert w.scan_all() == 2
    assert sorted(t["completion_tokens"] for t in sent) == [20, 20]


def test_response_split_across_scans_and_restarts_is_counted_once(tmp_path, monkeypatch):
    w = _watcher(tmp_path)
    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    _write_session(tmp_path, [_assistant_line("msg_A", "req_A", "thinking")])
    assert w.scan_all() == 1

    # A fresh watcher (app restart) sees the rest of the same response.
    w2 = _watcher(tmp_path)
    monkeypatch.setattr(w2, "_send_traces", lambda traces: sent.extend(traces) or True)
    _write_session(tmp_path, [_assistant_line("msg_A", "req_A", "text")])
    assert w2.scan_all() == 0
    assert len(sent) == 1


def test_trace_id_is_content_based_not_path_based(tmp_path):
    w1 = _watcher(tmp_path / "a")
    w2 = _watcher(tmp_path / "b")
    line = _assistant_line("msg_X", "req_X", "text")
    t1 = w1._parse_claude_line(line, Path("/one/place/s1.jsonl"))
    t2 = w2._parse_claude_line(line, Path("/another/place/s9.jsonl"))
    assert t1 is not None and t2 is not None
    assert t1["trace_id"] == t2["trace_id"]
    assert t1["trace_id"].startswith("cc-")
    assert "msg_X" not in t1["trace_id"]


def test_only_one_watcher_runs_per_machine(tmp_path):
    import time

    a = _watcher(tmp_path)
    b = _watcher(tmp_path)
    a.poll_interval = b.poll_interval = 0.05
    try:
        assert a.start() is True
        assert b.start() is False
        assert a.is_active and not b.is_active
        a.stop()
        # The standby watcher takes over once the lock is released.
        deadline = time.monotonic() + 10
        while not b.is_active and time.monotonic() < deadline:
            time.sleep(0.05)
        assert b.is_active
    finally:
        a.stop()
        b.stop()


def test_failed_send_is_retried_not_lost(tmp_path, monkeypatch):
    w = _watcher(tmp_path)
    _write_session(tmp_path, [_assistant_line("msg_A", "req_A", "text")])
    monkeypatch.setattr(w, "_send_traces", lambda traces: False)  # backend down
    assert w.scan_all() == 0

    sent = []
    monkeypatch.setattr(w, "_send_traces", lambda traces: sent.extend(traces) or True)
    assert w.scan_all() == 1
    assert len(sent) == 1


def test_claude_trace_keeps_real_model_and_has_no_invented_latency(tmp_path):
    w = _watcher(tmp_path)
    line = json.dumps({
        "type": "assistant", "requestId": "r1", "timestamp": "2026-10-02T07:00:00Z",
        "message": {"id": "m1", "model": "claude-sonnet-4-5-20250929",
                    "usage": {"input_tokens": 1, "output_tokens": 2,
                              "cache_creation_input_tokens": 300, "cache_read_input_tokens": 0,
                              "cache_creation": {"ephemeral_5m_input_tokens": 100,
                                                 "ephemeral_1h_input_tokens": 200}}},
    })
    t = w._parse_claude_line(line, Path("s.jsonl"))
    assert t["model_name"] == "claude-sonnet-4-5-20250929"
    assert "latency_ms" not in t
    assert t["cache_write_tokens"] == 300
    assert t["cache_write_1h_tokens"] == 200


def test_claude_line_without_model_is_not_given_one(tmp_path):
    w = _watcher(tmp_path)
    line = _assistant_line("m2", "r2", "text").replace('"model": "claude-opus-4-1", ', "")
    assert w._parse_claude_line(line, Path("s.jsonl"))["model_name"] == "unknown"


def _copilot_shutdown(event_id: str, metrics: dict, duration_ms: int = 4000) -> str:
    return json.dumps({
        "type": "session.shutdown", "id": event_id, "timestamp": "2026-10-02T07:00:00Z",
        "data": {"modelMetrics": metrics, "totalApiDurationMs": duration_ms},
    })


def test_copilot_uses_logged_session_totals_not_text_length(tmp_path):
    w = UnifiedAIWatcher(backend_url="http://127.0.0.1:9", state_file=tmp_path / "o.json")
    metrics = {"gpt-5.2-codex": {"requests": {"count": 4, "cost": 1},
                                 "usage": {"inputTokens": 1000, "outputTokens": 50,
                                           "cacheReadTokens": 800, "cacheWriteTokens": 0}}}
    traces = w._parse_copilot_line(_copilot_shutdown("e1", metrics), Path("sess/events.jsonl"))
    assert len(traces) == 1
    t = traces[0]
    assert (t["prompt_tokens"], t["cache_read_tokens"], t["completion_tokens"]) == (200, 800, 50)
    assert t["metadata"]["premium_requests"] == 1
    assert t["latency_ms"] == 1000.0
    assert t["trace_id"].startswith("cp-")
    # Per-message events carry no input tokens and are no longer turned into guesses.
    assert w._parse_copilot_line(json.dumps({"type": "assistant.message", "data": {"content": "x" * 400}}),
                                 Path("sess/events.jsonl")) == []


def test_copilot_multi_model_session_does_not_split_latency_by_guess(tmp_path):
    w = UnifiedAIWatcher(backend_url="http://127.0.0.1:9", state_file=tmp_path / "o.json")
    u = {"inputTokens": 10, "outputTokens": 1, "cacheReadTokens": 0, "cacheWriteTokens": 0}
    metrics = {"gpt-5-mini": {"requests": {"count": 1}, "usage": u},
               "gpt-5.2": {"requests": {"count": 1}, "usage": u}}
    traces = w._parse_copilot_line(_copilot_shutdown("e2", metrics), Path("sess/events.jsonl"))
    assert len(traces) == 2
    assert all("latency_ms" not in t for t in traces)


def test_cursor_scanner_invents_nothing(tmp_path):
    w = UnifiedAIWatcher(backend_url="http://127.0.0.1:9", state_file=tmp_path / "o.json")
    assert w._scan_gpt() == []
