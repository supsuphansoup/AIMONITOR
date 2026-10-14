"""Destination policy, payloads, and bounded requests (ALR-03/05/08/09)."""
import asyncio
import json
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.models import alert as alert_models
from backend.app.models.alert import AlertRule
from backend.app.services import alert as alert_service
from backend.app.services.alert import AlertEvaluator

RULE = {"name": "test", "metric_type": "total_requests", "operator": "<", "threshold_value": 1}
PUBLIC = "https://hooks.example.test/webhook/FAKE"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    # Models use a bound resolver so tests never replace the process-wide socket API.
    monkeypatch.setattr(alert_models, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))], raising=False)
    monkeypatch.setitem(settings.__dict__, "ALERT_ALLOW_PRIVATE_WEBHOOKS", False)


@pytest.fixture
def mock_http(monkeypatch):
    requests = []
    status = [204]

    def handler(request):
        requests.append(request)
        return httpx.Response(status[0], headers={"Location": "http://169.254.169.254/"})

    transport = httpx.MockTransport(handler)
    sync_client, async_client = httpx.Client, httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: sync_client(transport=transport, **kwargs))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: async_client(transport=transport, **kwargs))
    return requests, status


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/hook", "http://[::1]/hook", "http://10.0.0.1/hook",
    "http://169.254.169.254/hook", "http://[fe80::1]/hook", "http://0.0.0.0/hook",
    "http://224.0.0.1/hook", "http://240.0.0.1/hook", "http://100.64.0.1/hook",
    "http://2130706433/hook", "http://0177.0.0.1/hook", "http://127.1/hook",
    "http://0x7f000001/hook", "https://user:FAKE@hooks.example.test/hook",
    "http://[::ffff:127.0.0.1]/hook",
])
def test_private_or_credentialed_webhooks_rejected_on_create_patch_and_dispatch(client, mock_http, url):
    assert client.post("/api/v1/alerts/rules", json={**RULE, "webhook_url": url}).status_code == 422
    rule_id = client.post("/api/v1/alerts/rules", json=RULE).json()["id"]
    assert client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"webhook_url": url}).status_code == 422
    assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=url), 0, datetime.utcnow()) is False
    assert mock_http[0] == []


def test_dns_mixed_private_answer_and_send_time_change_are_rejected(client, mock_http, monkeypatch):
    rule_id = client.post("/api/v1/alerts/rules", json={**RULE, "webhook_url": PUBLIC}).json()["id"]
    monkeypatch.setattr(alert_models, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 443, 0, 0)),
    ])
    assert client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"webhook_url": PUBLIC}).status_code == 422
    assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=PUBLIC), 0, datetime.utcnow()) is False
    assert mock_http[0] == []


def test_dns_failure_rejects_without_exposing_resolver_exception(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise socket.gaierror("resolver detail FAKE")

    monkeypatch.setattr(alert_models, "getaddrinfo", unavailable)
    response = client.post("/api/v1/alerts/rules", json={**RULE, "webhook_url": PUBLIC})
    assert response.status_code == 422
    assert "resolver detail" not in response.text


def test_private_webhook_opt_in_is_explicit(client, mock_http, monkeypatch):
    monkeypatch.setitem(settings.__dict__, "ALERT_ALLOW_PRIVATE_WEBHOOKS", True)
    url = "http://user:FAKE@127.0.0.1:18777/hook"
    assert client.post("/api/v1/alerts/rules", json={**RULE, "webhook_url": url}).status_code == 201
    assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=url), 0, datetime.utcnow()) is True
    assert len(mock_http[0]) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 429, 500])
def test_only_2xx_webhook_responses_succeed_without_following_redirects(session, mock_http, status):
    mock_http[1][0] = status
    session.add(AlertRule(**RULE, webhook_url=PUBLIC))
    session.commit()
    result = AlertEvaluator.evaluate_rules(session)
    assert len(result) == 1 and result[0].sent_successfully is False
    assert len(mock_http[0]) == 1


@pytest.mark.parametrize("url,discord", [
    ("https://discord.com/api/v10/webhooks/123/FAKE", True),
    ("https://DISCORD.COM/api/webhooks/123/FAKE", True),
    ("https://discordapp.com/api/webhooks/123/FAKE", True),
    ("https://hooks.example.test/discord.com/api/webhooks/FAKE", False),
    ("https://discord.com.evil.test/api/webhooks/FAKE", False),
])
def test_discord_payload_uses_normalized_host_and_versioned_path(mock_http, url, discord):
    assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=url), 0, datetime.utcnow()) is True
    payload = json.loads(mock_http[0][0].content)
    assert ("content" in payload) is discord
    assert ("text" in payload) is not discord


@pytest.mark.parametrize("mode", ["body", "headers"])
def test_local_slow_response_is_closed_and_deadline_stops_request(monkeypatch, mode):
    monkeypatch.setitem(settings.__dict__, "ALERT_ALLOW_PRIVATE_WEBHOOKS", True)
    closed = threading.Event()
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                if mode == "body":
                    self.send_response(200)
                    self.send_header("Content-Length", "70000")
                    self.end_headers()
                    pieces = [b"x" * 10000] * 7
                    delay = 1.0
                else:
                    pieces = [bytes([byte]) for byte in b"HTTP/1.1 200 OK\r\nX-Test: " + b"a" * 70 + b"\r\nContent-Length: 0\r\n\r\n"]
                    # About 10 s for the whole header: well past the 5 s deadline even on a loaded machine.
                    delay = 0.1
                for piece in pieces:
                    if stop.wait(delay):
                        break
                    self.wfile.write(piece)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                closed.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        result = AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=url), 0, datetime.utcnow())
        elapsed = time.monotonic() - started
        if mode == "body":
            # The body needs about 7 s; success before the 5 s deadline means it was not awaited.
            assert result is True and elapsed < 4.5
        else:
            assert result is False and 4.5 <= elapsed < 8
        # Windows may accept one more write after the peer closes, so allow a few server writes.
        assert closed.wait(10), "server must observe the client closing its request"
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_total_deadline_cancels_the_transport_and_closes_client(monkeypatch):
    cancelled = threading.Event()
    closed = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        async def aclose(self):
            closed.append(True)

    original_async = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_async(transport=Transport(), **kwargs))
    # The old sync implementation must stay offline too.
    original_sync = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_sync(transport=httpx.MockTransport(lambda request: httpx.Response(204)), **kwargs))
    monkeypatch.setattr(alert_service, "WEBHOOK_TIMEOUT_SECONDS", 0.05)
    assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=PUBLIC), 0, datetime.utcnow()) is False
    assert cancelled.is_set() and closed == [True]


def test_dns_deadline_never_starts_a_late_http_request(monkeypatch, mock_http):
    release = threading.Event()
    finished = threading.Event()

    def resolver(*args, **kwargs):
        try:
            release.wait(2)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        finally:
            finished.set()

    monkeypatch.setattr(alert_models, "getaddrinfo", resolver)
    monkeypatch.setattr(alert_service, "WEBHOOK_TIMEOUT_SECONDS", 0.05)
    try:
        started = time.monotonic()
        assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=PUBLIC), 0, datetime.utcnow()) is False
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        assert finished.wait(1)
    assert mock_http[0] == []


@pytest.mark.parametrize("host", ["8.8.8.8", "134744072", "010.010.010.010", "0x08080808", "8.8.2056"])
def test_public_legacy_ip_forms_remain_valid(client, mock_http, host):
    url = f"https://{host}/hook"
    assert client.post("/api/v1/alerts/rules", json={**RULE, "webhook_url": url}).status_code == 201
    assert AlertEvaluator.dispatch_webhook(AlertRule(**RULE, webhook_url=url), 0, datetime.utcnow()) is True
