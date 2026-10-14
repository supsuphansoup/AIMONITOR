"""RR-01/RP-01: identify each backend attempt before sending records or the server key."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from sqlmodel import select

from backend.app.core.config import settings
from backend.app.models.trace import TraceEvent
from backend.tests.test_accounts_watcher_regressions import append, event, log_path, watcher
from desktop import ai_watcher, config

BASE = "http://127.0.0.1:18803"
KEY = "test-only-identity-server-key"
HEALTH = {"app": "AIMONITOR", "status": "ok"}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / ".aimonitor")
    monkeypatch.setattr(config, "CONFIG_FILE", config.CONFIG_DIR / "desktop_config.json")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("AIMONITOR_API_KEY", KEY)


def transport(monkeypatch, respond):
    original = httpx.Client
    requests = []
    attempts = []

    def create(**kwargs):
        attempt = []
        attempts.append(attempt)

        def handle(request):
            requests.append(request)
            attempt.append(request)
            return respond(request)

        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", create)
    return requests, attempts


def collector(home, monkeypatch):
    instance, _ = watcher(home, monkeypatch)
    instance.backend_url = BASE
    monkeypatch.setattr(instance, "_send_traces", ai_watcher.UnifiedAIWatcher._send_traces.__get__(instance))
    return instance


def stored(session):
    return session.exec(select(TraceEvent)).all()


def assert_identified_attempts(attempts, key=KEY):
    for attempt in attempts:
        assert attempt[0].method == "GET" and attempt[0].url.path == "/health"
        assert "X-API-Key" not in attempt[0].headers
        assert all(request.headers.get("X-API-Key") == key for request in attempt[1:])


@pytest.mark.parametrize("restart", [False, True])
def test_foreign_422_rolls_back_and_real_backend_recovers_once(
    tmp_path, monkeypatch, client, session, caplog, restart,
):
    for tool in ("claude", "codex", "copilot", "antigravity"):
        append(log_path(tmp_path, tool), [event(tool)])
    real = [False]

    def respond(request):
        if not real[0]:
            if request.method == "GET":
                return httpx.Response(200, json={"app": "foreign", "status": "ok"})
            return httpx.Response(422, text="foreign rejection")
        return client.request(request.method, request.url.path, content=request.content, headers=request.headers)

    requests, attempts = transport(monkeypatch, respond)
    instance = collector(tmp_path, monkeypatch)
    assert instance.scan_all() == 0
    assert not instance._seen_claude
    assert not any(instance._offsets.values())
    assert not instance._cursors
    assert not instance.state_file.exists()
    assert [request.method for request in requests] == ["GET"]
    assert "X-API-Key" not in requests[0].headers
    assert not stored(session)
    real[0] = True
    if restart:
        instance = collector(tmp_path, monkeypatch)
    assert instance.scan_all() == 4
    assert len(stored(session)) == 4
    assert collector(tmp_path, monkeypatch).scan_all() == 0
    assert len(stored(session)) == 4
    assert_identified_attempts(attempts)
    assert KEY not in caplog.text
    assert "foreign rejection" not in caplog.text


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"service": "other"}), httpx.Response(200, json=[]),
    httpx.Response(200, json=None), httpx.Response(200, text="not-json"),
    httpx.Response(200, json={"app": "AIMONITOR", "status": "down"}),
    httpx.Response(503, json=HEALTH), httpx.Response(401, json=HEALTH),
])
def test_unidentified_health_never_posts_or_sends_key(monkeypatch, response):
    def respond(request):
        return response if request.method == "GET" else httpx.Response(422)

    requests, _ = transport(monkeypatch, respond)
    instance = ai_watcher.UnifiedAIWatcher.__new__(ai_watcher.UnifiedAIWatcher)
    instance.backend_url = BASE
    assert instance._send_traces([{"provider": "openai", "model_name": "gpt-5"}]) is False
    assert [request.url.path for request in requests] == ["/health"]
    assert all("X-API-Key" not in request.headers for request in requests)


def test_health_connection_failure_preserves_record_for_retry(tmp_path, monkeypatch, client, session, caplog):
    caplog.set_level(logging.DEBUG, logger=ai_watcher.logger.name)
    append(log_path(tmp_path, "claude"), [event("claude")])
    available = [False]

    def respond(request):
        if request.url.path == "/health" and not available[0]:
            raise httpx.ConnectError("synthetic-private-error", request=request)
        return client.request(request.method, request.url.path, content=request.content, headers=request.headers)

    requests, attempts = transport(monkeypatch, respond)
    instance = collector(tmp_path, monkeypatch)
    assert instance.scan_all() == 0
    assert not instance._seen_claude and not any(instance._offsets.values())
    assert not stored(session)
    assert [request.url.path for request in requests] == ["/health"]
    available[0] = True
    assert instance.scan_all() == 1
    assert len(stored(session)) == 1
    assert_identified_attempts(attempts)
    assert "synthetic-private-error" not in caplog.text
    # A backend that is still starting is not a port conflict: keep it at debug, as before.
    assert "Failed to send traces: ConnectError" in caplog.text
    assert "Backend identity unavailable" not in caplog.text


def test_old_identified_backend_isolates_individual_422_without_error_schema(
    tmp_path, monkeypatch, client, session,
):
    append(log_path(tmp_path, "claude"), [event("claude", "reject"), event("claude", "accept")])
    rejected = []
    statuses = []

    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200, json=HEALTH)
        traces = json.loads(request.content)["traces"]
        if len(traces) > 1:
            rejected.append(traces[0]["dedupe_key"])
        if len(traces) > 1 or traces[0]["dedupe_key"] in rejected:
            response = httpx.Response(422, text="legacy error format")
        else:
            response = client.post(request.url.path, json={"traces": traces}, headers=request.headers)
        statuses.append(response.status_code)
        return response

    _requests, attempts = transport(monkeypatch, respond)
    instance = collector(tmp_path, monkeypatch)
    assert instance.scan_all() == 2
    assert statuses == [422, 422, 201]
    assert len(stored(session)) == 1
    assert instance.scan_all() == 0
    assert collector(tmp_path, monkeypatch).scan_all() == 0
    assert_identified_attempts(attempts)


def test_identity_is_rechecked_after_retryable_failure(tmp_path, monkeypatch, client, session):
    append(log_path(tmp_path, "claude"), [event("claude")])
    mode = ["retry"]

    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200, json=HEALTH if mode[0] != "foreign" else {"app": "other"})
        if mode[0] == "retry":
            return httpx.Response(503)
        assert mode[0] == "ready", "sent a record to the replacement foreign server"
        return client.post(request.url.path, content=request.content, headers=request.headers)

    requests, attempts = transport(monkeypatch, respond)
    instance = collector(tmp_path, monkeypatch)
    assert instance.scan_all() == 0
    mode[0] = "foreign"
    assert instance.scan_all() == 0
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    mode[0] = "ready"
    assert instance.scan_all() == 1
    assert len(stored(session)) == 1
    assert_identified_attempts(attempts)


def test_stop_during_health_does_not_start_post(tmp_path, monkeypatch):
    instance = collector(tmp_path, monkeypatch)

    def respond(_request):
        instance._stop_event.set()
        return httpx.Response(200, json=HEALTH)

    requests, _ = transport(monkeypatch, respond)
    assert instance._send_traces([{"provider": "openai", "model_name": "gpt-5"}]) is False
    assert [request.method for request in requests] == ["GET"]


def gui_modules():
    for package in ("customtkinter", "matplotlib", "pystray"):
        pytest.importorskip(package)
    from desktop import app_gui, main, widget
    return app_gui, main, widget


@pytest.mark.parametrize("health,status,expected", [(HEALTH, 200, "ready"), ({"app": "other"}, 200, "conflict"),
                                                   (HEALTH, 503, "conflict")])
def test_controller_health_never_sends_server_key(monkeypatch, health, status, expected):
    _app_gui, main, _widget = gui_modules()
    requests, _ = transport(monkeypatch, lambda _request: httpx.Response(status, json=health))
    assert main.backend_health(BASE) == expected
    assert len(requests) == 1
    assert "X-API-Key" not in requests[0].headers


@pytest.mark.parametrize("worker", ["main", "widget"])
@pytest.mark.parametrize("health", ["foreign", "unavailable", "ready"])
@pytest.mark.parametrize("key,require_auth", [(None, False), (KEY, False), (KEY, True), (None, True)])
def test_ui_fetch_identifies_before_keys_and_preserves_read_auth(
    monkeypatch, client, worker, health, key, require_auth,
):
    app_gui, _main, widget = gui_modules()
    monkeypatch.setenv("AIMONITOR_API_KEY", key or "")
    monkeypatch.setattr(settings, "API_KEY", KEY if require_auth else None)
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", require_auth)
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: [])
    monkeypatch.setattr(widget, "list_accounts", lambda: [])
    monkeypatch.setattr(app_gui.api_key_manager, "list_keys", lambda: [])
    monkeypatch.setattr(app_gui, "get_connected_ai_summary", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(widget, "get_connected_ai_summary", lambda *args, **kwargs: {"items": []})

    def respond(request):
        if request.url.path == "/health":
            if health == "unavailable":
                raise httpx.ConnectError("synthetic unavailable", request=request)
            if health == "foreign":
                return httpx.Response(200, json={"app": "other"})
        return client.get(str(request.url), headers=request.headers)

    requests, attempts = transport(monkeypatch, respond)
    callback = Mock()
    if worker == "main":
        ui = SimpleNamespace(backend_url=BASE, official_spend={}, _is_updating=True,
                             after=lambda _delay, apply: apply(), _apply_data=callback)
        app_gui.DesktopMainWindow._fetch_worker(ui, [], {}, 1400)
        ok, auth = callback.call_args.args[-2:]
        assert not ui._is_updating
    else:
        ui = SimpleNamespace(backend_url=BASE, _connected_ais=[], _fetching=True, config={},
                             rate_updater=SimpleNamespace(rate=1400), _on_data=callback,
                             root=SimpleNamespace(after=lambda _delay, apply: apply()))
        widget.FloatingWidget._fetch_worker(ui)
        ok, auth = callback.call_args.args[-2:]
        assert not ui._fetching
    assert_identified_attempts(attempts, key)
    if health != "ready":
        assert len(requests) == 1
        assert not ok and not auth
    elif require_auth and not key:
        assert not ok and auth
    else:
        assert ok and not auth
