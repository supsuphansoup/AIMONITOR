"""Streaming limits and metadata on responses produced before routing."""
import asyncio
import json

import pytest

from backend.app.core.config import settings
from backend.app.main import app

ORIGIN = "http://localhost:8000"


@pytest.mark.parametrize("headers,status", [
    ({"Host": "untrusted.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Content-Length": "999999999"}, 413),
    ({"Content-Length": "invalid"}, 400),
    ({"Content-Length": "-1"}, 400),
    ({"Access-Control-Request-Method": "POST"}, 200),
    ({"Access-Control-Request-Method": "POST", "Host": "untrusted.example"}, 403),
])
def test_early_responses_have_request_id(client, headers, status):
    method = "OPTIONS" if "Access-Control-Request-Method" in headers else "POST"
    response = client.request(method, "/api/v1/traces", headers={
        "Origin": ORIGIN, "X-Request-ID": "early-response", **headers,
    })
    assert response.status_code == status
    assert response.headers["X-Request-ID"] == "early-response"
    if headers.get("Origin") != "null":
        assert response.headers["Access-Control-Allow-Origin"] == ORIGIN


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [None, "1"])
async def test_streaming_body_stops_at_limit(client, monkeypatch, declared):
    monkeypatch.setattr(settings, "MAX_REQUEST_BYTES", 64)
    chunks = [b" " * 32, b" " * 33, b"PRIVATE_UNREAD_TAIL"]
    reads = []
    sent = []
    finished = asyncio.Event()

    async def receive():
        if chunks:
            reads.append(chunks.pop(0))
            return {"type": "http.request", "body": reads[-1], "more_body": bool(chunks)}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            finished.set()

    headers = [(b"host", b"testserver"), (b"origin", ORIGIN.encode()),
               (b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")]
    if declared is not None:
        headers.append((b"content-length", declared.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/api/v1/traces", "raw_path": b"/api/v1/traces",
             "query_string": b"", "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 12345), "server": ("testserver", 80)}
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413
    assert len(reads) == 2 and chunks == [b"PRIVATE_UNREAD_TAIL"]
    response_headers = dict(start["headers"])
    assert response_headers[b"access-control-allow-origin"] == ORIGIN.encode()
    assert response_headers[b"x-request-id"]
    assert response_headers[b"connection"] == b"close"


def test_body_exactly_at_limit_is_accepted(client, monkeypatch):
    body = json.dumps({"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 1}).encode()
    headers = {"Content-Type": "application/json"}
    monkeypatch.setattr(settings, "MAX_REQUEST_BYTES", len(body))
    assert client.post("/api/v1/traces", content=body, headers=headers).status_code == 201
    assert client.post("/api/v1/traces", content=body + b" ", headers=headers).status_code == 413
