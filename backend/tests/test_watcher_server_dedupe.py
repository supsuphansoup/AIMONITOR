"""Collector retries against the real ingestion API store each log event once (ACC-01 with dedupe_key)."""
from pathlib import Path

import httpx
import pytest
from sqlmodel import func, select

from backend.app.models.trace import TraceEvent
from backend.tests.test_accounts_watcher_regressions import append, event, log_path, watcher
from desktop import ai_watcher


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(ai_watcher, "backend_headers", lambda: {})


class FlakyBackend:
    """Stands in for httpx.Client: forwards to the app, failing chosen POSTs.

    "drop" never reaches the server; "lost" is stored by the server but the
    collector only sees the failure, as when a response is lost in transit.
    """

    def __init__(self, client, failures):
        self.client, self.failures, self.calls = client, failures, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json):
        self.calls += 1
        mode = self.failures.get(self.calls)
        if mode == "drop":
            return httpx.Response(503)
        response = self.client.post(url, json=json)
        assert response.status_code == 201, response.text
        return httpx.Response(503) if mode == "lost" else response


def stored(session):
    return session.exec(select(func.count()).select_from(TraceEvent)).one()


def real_sender(instance, monkeypatch, backend):
    monkeypatch.setattr(instance, "_send_traces", ai_watcher.UnifiedAIWatcher._send_traces.__get__(instance))
    monkeypatch.setattr(ai_watcher.httpx, "Client", lambda **kwargs: backend)


@pytest.mark.parametrize("tool", ["claude", "codex", "copilot", "antigravity"])
def test_lost_response_retry_is_stored_once(tmp_path, monkeypatch, client, session, tool):
    append(log_path(tmp_path, tool), [event(tool)])
    instance, _ = watcher(tmp_path, monkeypatch)
    real_sender(instance, monkeypatch, FlakyBackend(client, {1: "lost"}))
    assert instance.scan_all() == 0
    assert stored(session) == 1
    assert instance.scan_all() == 1
    assert stored(session) == 1


def test_partially_accepted_batch_is_not_stored_twice(tmp_path, monkeypatch, client, session):
    append(log_path(tmp_path, "claude"), [event("claude", str(index)) for index in range(201)])
    instance, _ = watcher(tmp_path, monkeypatch)
    real_sender(instance, monkeypatch, FlakyBackend(client, {2: "drop"}))
    assert instance.scan_all() == 0
    assert stored(session) == 200
    assert instance.scan_all() == 201
    assert stored(session) == 201


def test_restarted_collector_resends_without_duplicates(tmp_path, monkeypatch, client, session):
    append(log_path(tmp_path, "claude"), [event("claude", "a"), event("claude", "b")])
    first, _ = watcher(tmp_path, monkeypatch)
    real_sender(first, monkeypatch, FlakyBackend(client, {1: "lost"}))
    assert first.scan_all() == 0
    restarted, _ = watcher(tmp_path, monkeypatch)
    real_sender(restarted, monkeypatch, FlakyBackend(client, {}))
    assert restarted.scan_all() == 2
    assert stored(session) == 2
