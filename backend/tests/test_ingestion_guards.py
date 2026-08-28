"""Ingestion limits and payload sanitization."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.core.sanitize import REDACTED, normalize_tags, truncate_error_message

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 5}


def test_metadata_prompt_fields_are_redacted(client: TestClient):
    res = client.post("/api/v1/traces", json={
        **TRACE,
        "metadata": {
            "user_id": "usr_1",
            "prompt": "the actual user question",
            "nested": {"response_text": "the actual model answer", "region": "kr"},
        },
    })
    stored = json.loads(res.json()["metadata_json"])

    assert stored["prompt"] == REDACTED
    assert stored["nested"]["response_text"] == REDACTED
    # Non-content metadata survives, which is the whole point of keeping the field.
    assert stored["user_id"] == "usr_1"
    assert stored["nested"]["region"] == "kr"


def test_oversized_metadata_is_replaced_not_stored(client: TestClient):
    huge = {"blob_" + str(i): "x" * 200 for i in range(200)}
    res = client.post("/api/v1/traces", json={**TRACE, "metadata": huge})
    stored = json.loads(res.json()["metadata_json"])
    assert "_truncated" in stored


def test_error_message_is_capped(client: TestClient):
    res = client.post("/api/v1/traces", json={
        **TRACE, "status": "ERROR", "error_message": "E" * (settings.MAX_ERROR_MESSAGE_CHARS + 500),
    })
    stored = res.json()["error_message"]
    assert len(stored) <= settings.MAX_ERROR_MESSAGE_CHARS + len("... [truncated]")
    assert stored.endswith("... [truncated]")


def test_batch_over_limit_is_rejected(client: TestClient):
    oversized = {"traces": [TRACE] * (settings.MAX_BATCH_SIZE + 1)}
    res = client.post("/api/v1/traces/batch", json=oversized)
    assert res.status_code == 422
    assert "Batch too large" in json.dumps(res.json())


def test_empty_batch_is_rejected(client: TestClient):
    assert client.post("/api/v1/traces/batch", json={"traces": []}).status_code == 422


def test_invalid_status_filter_is_rejected(client: TestClient):
    assert client.get("/api/v1/traces?status=NOPE").status_code == 422


def test_invalid_status_on_ingest_is_rejected(client: TestClient):
    assert client.post("/api/v1/traces", json={**TRACE, "status": "WEIRD"}).status_code == 422


def test_ingest_error_does_not_echo_payload(client: TestClient, monkeypatch):
    """A 500 must not return the raw exception, which can quote the payload."""
    import backend.app.routers.traces as traces_router

    def boom(_payload):
        raise RuntimeError("secret prompt text leaked into the exception")

    monkeypatch.setattr(traces_router, "process_single_trace_create", boom)
    res = client.post("/api/v1/traces", json=TRACE)
    assert res.status_code == 500
    assert "secret prompt text" not in json.dumps(res.json())


@pytest.mark.parametrize(
    "tags,expected",
    [
        (None, None),
        ([], None),
        (["  prod  ", "prod", "eu"], "prod,eu"),
        (["a,b"], "a b"),
    ],
)
def test_tag_normalization(tags, expected):
    assert normalize_tags(tags) == expected


def test_tag_count_is_capped():
    result = normalize_tags([f"tag{i}" for i in range(settings.MAX_TAGS + 10)])
    assert len(result.split(",")) == settings.MAX_TAGS


def test_truncate_error_message_passthrough():
    assert truncate_error_message("short") == "short"
    assert truncate_error_message("  ") is None
    assert truncate_error_message(None) is None
