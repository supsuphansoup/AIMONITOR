"""GC-01/GA-01: isolate permanent log errors without losing retryable events."""
import copy
import json
from pathlib import Path

import httpx
import pytest
from sqlmodel import select

from backend.app.models.trace import MAX_TOKENS_PER_TRACE, TraceEvent
from backend.tests.test_accounts_watcher_regressions import append, event, log_path, watcher
from desktop import ai_watcher


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(ai_watcher, "backend_headers", lambda: {})


class Backend:
    """Use the real batch endpoint unless a test injects a response or transport error."""

    def __init__(self, client, override=None):
        self.client = client
        self.override = override
        self.bodies = []
        self.statuses = []
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        return self.client.get(url)

    def post(self, url, json):
        self.bodies.append(copy.deepcopy(json))
        response = self.override(self, url, json) if self.override else None
        if response is None:
            response = self.client.post(url, json=json)
        self.statuses.append(response.status_code)
        return response


def collector(home, monkeypatch, backend):
    instance, _ = watcher(home, monkeypatch)
    instance.backend_url = "http://127.0.0.1:18803"
    monkeypatch.setattr(instance, "_send_traces", ai_watcher.UnifiedAIWatcher._send_traces.__get__(instance))
    monkeypatch.setattr(ai_watcher.httpx, "Client", lambda **kwargs: backend)
    return instance


def rows(session):
    return session.exec(select(TraceEvent)).all()


def set_tokens(row, tool, incoming, outgoing):
    if tool == "claude":
        row["message"]["usage"].update(input_tokens=incoming, output_tokens=outgoing)
    elif tool == "codex":
        row["payload"]["info"]["last_token_usage"].update(input_tokens=incoming, output_tokens=outgoing)
    else:
        row["data"]["modelMetrics"]["gpt-5"]["usage"].update(inputTokens=incoming, outputTokens=outgoing)


@pytest.mark.parametrize("tool", ["claude", "codex", "copilot"])
@pytest.mark.parametrize("bad_kind", ["field", "total", "utc_low", "utc_high"])
def test_invalid_complete_event_cannot_block_valid_tools_or_reappear(
    tmp_path, monkeypatch, client, session, caplog, tool, bad_kind,
):
    bad = event(tool, "private-fixture-identifier")
    if bad_kind == "field":
        set_tokens(bad, tool, MAX_TOKENS_PER_TRACE + 1, 0)
    elif bad_kind == "total":
        set_tokens(bad, tool, 600_000_000, 600_000_000)
    else:
        bad["timestamp"] = ("0001-01-01T00:00:00+14:00" if bad_kind == "utc_low"
                            else "9999-12-31T23:59:59-14:00")
    append(log_path(tmp_path, tool), [bad])
    for good_tool in ("claude", "codex", "copilot"):
        append(log_path(tmp_path, good_tool), [event(good_tool, "good")])
    backend = Backend(client)
    instance = collector(tmp_path, monkeypatch, backend)
    assert instance.scan_all() == 3
    assert len(rows(session)) == 3
    assert backend.statuses == [201]
    calls = len(backend.bodies)
    assert instance.scan_all() == 0
    restarted = collector(tmp_path, monkeypatch, backend)
    assert restarted.scan_all() == 0
    assert len(backend.bodies) == calls
    assert "Skipped invalid" in caplog.text
    assert "private-fixture-identifier" not in caplog.text
    assert str(tmp_path) not in caplog.text


@pytest.mark.parametrize("tool", ["claude", "codex", "copilot"])
def test_exact_token_limit_is_accepted(tmp_path, monkeypatch, client, session, tool):
    row = event(tool)
    set_tokens(row, tool, MAX_TOKENS_PER_TRACE, 0)
    append(log_path(tmp_path, tool), [row])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    assert rows(session)[0].total_tokens == MAX_TOKENS_PER_TRACE


def test_hour_cache_write_is_not_counted_twice(tmp_path, monkeypatch, client, session):
    row = event("claude")
    row["message"]["usage"] = {
        "input_tokens": 500_000_000, "cache_creation_input_tokens": 500_000_000,
        "cache_creation": {"ephemeral_1h_input_tokens": 500_000_000},
    }
    append(log_path(tmp_path, "claude"), [row])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    assert rows(session)[0].total_tokens == MAX_TOKENS_PER_TRACE
    assert rows(session)[0].cache_write_1h_tokens == 500_000_000


def test_rejected_claude_trace_does_not_mark_response_seen(tmp_path, monkeypatch, client, session):
    bad = event("claude")
    bad["timestamp"] = "0001-01-01T00:00:00+14:00"
    append(log_path(tmp_path, "claude"), [bad, event("claude")])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    assert len(rows(session)) == 1


def test_antigravity_utc_overflow_only_skips_bad_line(tmp_path, monkeypatch, client, session):
    bad = event("antigravity")
    bad["created_at"] = "0001-01-01T00:00:00+14:00"
    append(log_path(tmp_path, "antigravity"), [bad, event("antigravity")])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    assert len(rows(session)) == 1


@pytest.mark.parametrize("field, limit", [("model", 200), ("provider", 64)])
def test_antigravity_observed_names_obey_ingestion_lengths(
    tmp_path, monkeypatch, client, session, field, limit,
):
    bad = dict(event("antigravity"), **{field: "x" * (limit + 1)})
    good = dict(event("antigravity"), **{field: "x" * limit})
    append(log_path(tmp_path, "antigravity"), [bad, good])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    stored = rows(session)
    assert len(stored) == 1
    assert getattr(stored[0], "model_name" if field == "model" else field) == "x" * limit


@pytest.mark.parametrize("field", ["output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"])
def test_claude_each_token_component_limit_skips_only_bad_response(
    tmp_path, monkeypatch, client, session, field,
):
    bad = event("claude")
    bad["message"]["usage"][field] = MAX_TOKENS_PER_TRACE + 1
    append(log_path(tmp_path, "claude"), [bad, event("claude")])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    assert len(rows(session)) == 1


def test_invalid_copilot_model_does_not_discard_other_models(
    tmp_path, monkeypatch, client, session, caplog,
):
    row = event("copilot")
    models = row["data"]["modelMetrics"]
    models["gpt-5"]["usage"]["inputTokens"] = MAX_TOKENS_PER_TRACE + 1
    models["claude-sonnet-4-5"] = {
        "usage": {"inputTokens": 10, "outputTokens": 20}, "requests": {"count": 1},
    }
    append(log_path(tmp_path, "copilot"), [row])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == 1
    assert rows(session)[0].model_name == "claude-sonnet-4-5"
    assert "Skipped invalid Copilot" in caplog.text


@pytest.mark.parametrize("duration, accepted", [(1_000_000_000, True), (1_000_000_001, False)])
def test_copilot_latency_shares_numeric_ingestion_limit(
    tmp_path, monkeypatch, client, session, duration, accepted,
):
    row = event("copilot")
    row["data"]["totalApiDurationMs"] = duration
    append(log_path(tmp_path, "copilot"), [row])
    instance = collector(tmp_path, monkeypatch, Backend(client))
    assert instance.scan_all() == int(accepted)
    assert len(rows(session)) == int(accepted)


def test_remote_422_isolates_only_bad_event_and_persists_progress(
    tmp_path, monkeypatch, client, session, caplog,
):
    append(log_path(tmp_path, "claude"), [event("claude", str(i)) for i in range(3)])

    def reject_middle(backend, url, body):
        if any(trace["prompt_tokens"] == 11 for trace in body["traces"]):
            return httpx.Response(422, json={"detail": "synthetic private error"})
        return None

    path = log_path(tmp_path, "claude")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[1]["message"]["usage"]["input_tokens"] = 11
    path.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
    backend = Backend(client, reject_middle)
    instance = collector(tmp_path, monkeypatch, backend)
    assert instance.scan_all() == 3
    assert backend.statuses == [422, 201, 422, 201]
    assert len(rows(session)) == 2
    calls = len(backend.bodies)
    assert collector(tmp_path, monkeypatch, backend).scan_all() == 0
    assert len(backend.bodies) == calls
    assert "synthetic private error" not in caplog.text


@pytest.mark.parametrize("failure", [401, 403, 500, "connect"])
def test_failed_individual_retry_rolls_back_with_identical_dedupe_keys(
    tmp_path, monkeypatch, client, session, failure,
):
    append(log_path(tmp_path, "claude"), [event("claude", str(i)) for i in range(3)])

    def fail_individual(backend, url, body):
        if len(backend.bodies) == 1:
            return httpx.Response(422)
        if len(backend.bodies) == 3:
            if failure == "connect":
                raise httpx.ConnectError("synthetic private error")
            return httpx.Response(failure)
        return None

    backend = Backend(client, fail_individual)
    instance = collector(tmp_path, monkeypatch, backend)
    assert instance.scan_all() == 0
    assert len(rows(session)) == 1
    assert not instance._seen_claude
    original_keys = [row["dedupe_key"] for row in backend.bodies[0]["traces"]]
    backend.override = None
    assert instance.scan_all() == 3
    assert len(rows(session)) == 3
    assert [row["dedupe_key"] for row in backend.bodies[-1]["traces"]] == original_keys


@pytest.mark.parametrize("body", [{"status": "ok"}, [], [{}], ["unexpected"], None])
def test_success_status_with_wrong_batch_contract_rolls_back(
    tmp_path, monkeypatch, client, session, body,
):
    append(log_path(tmp_path, "claude"), [event("claude")])
    backend = Backend(client, lambda *args: httpx.Response(200, json=body))
    instance = collector(tmp_path, monkeypatch, backend)
    assert instance.scan_all() == 0
    assert not instance._seen_claude
    assert all(value == 0 for value in instance._offsets.values())
    backend.override = None
    assert instance.scan_all() == 1
    assert len(rows(session)) == 1


def test_success_contract_must_match_sent_event(tmp_path, monkeypatch, client, session):
    append(log_path(tmp_path, "claude"), [event("claude")])

    def wrong_identity(backend, url, body):
        response = client.post(url, json=body)
        payload = response.json()
        payload[0]["dedupe_key"] = "unrelated-event"
        return httpx.Response(200, json=payload)

    backend = Backend(client, wrong_identity)
    instance = collector(tmp_path, monkeypatch, backend)
    assert instance.scan_all() == 0
    assert not instance._seen_claude
    backend.override = None
    assert instance.scan_all() == 1
    assert len(rows(session)) == 1


def test_malformed_individual_success_rolls_back_partial_progress(tmp_path, monkeypatch, client, session):
    append(log_path(tmp_path, "claude"), [event("claude", str(i)) for i in range(3)])

    def malformed_individual(backend, url, body):
        if len(backend.bodies) == 1:
            return httpx.Response(422)
        if len(backend.bodies) == 3:
            return httpx.Response(201, content="synthetic private response")
        return None

    backend = Backend(client, malformed_individual)
    instance = collector(tmp_path, monkeypatch, backend)
    assert instance.scan_all() == 0
    assert len(rows(session)) == 1
    assert not instance._seen_claude
    backend.override = None
    assert instance.scan_all() == 3
    assert len(rows(session)) == 3


def test_stop_prevents_new_requests_during_422_isolation(tmp_path, monkeypatch, client, session):
    append(log_path(tmp_path, "claude"), [event("claude", str(i)) for i in range(3)])
    backend = Backend(client)
    instance = collector(tmp_path, monkeypatch, backend)

    def stop_after_one(backend, url, body):
        if len(backend.bodies) == 1:
            return httpx.Response(422)
        instance._stop_event.set()
        return None

    backend.override = stop_after_one
    assert instance.scan_all() == 0
    assert len(backend.bodies) == 2
    assert len(rows(session)) == 1
    assert not instance._seen_claude
