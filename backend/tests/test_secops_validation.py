"""Validation failures must serialize safely without returning submitted values."""
import json

import pytest
from fastapi.exceptions import RequestValidationError

from backend.app.main import safe_validation_error

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 1}
TOKEN_FIELDS = (
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_write_1h_tokens", "total_tokens",
)


def assert_safe_validation(response):
    assert response.status_code == 422
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "type", "msg"}
        assert error["msg"] == "Invalid field value"


def test_batch_validation_does_not_return_prompt(client):
    private = {**TRACE, "metadata": {"prompt": "PRIVATE_PROMPT_MARKER"}}
    response = client.post("/api/v1/traces/batch", json={"traces": [private] + [TRACE] * 500})
    assert_safe_validation(response)
    assert "PRIVATE_PROMPT_MARKER" not in response.text


@pytest.mark.parametrize("field", TOKEN_FIELDS)
@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN", "1e10000"])
@pytest.mark.parametrize("batch", [False, True])
def test_nonfinite_token_validation_is_422(client, field, value, batch):
    payload = '{"provider":"openai","model_name":"gpt-4o","' + field + '":' + value + '}'
    if batch:
        payload = '{"traces":[' + payload + ']}'
    response = client.post(
        "/api/v1/traces/batch" if batch else "/api/v1/traces",
        content=payload, headers={"Content-Type": "application/json"},
    )
    assert_safe_validation(response)


@pytest.mark.asyncio
async def test_validation_handler_never_serializes_unsafe_values():
    exception = RequestValidationError([
        {"loc": ("body", "prompt_tokens"), "type": "value_error",
         "msg": "PRIVATE_MESSAGE", "input": 10 ** 5000,
         "ctx": {"error": ValueError("PRIVATE_CONTEXT")}},
        {"loc": ("body", "completion_tokens"), "type": "finite_number",
         "msg": "PRIVATE_MESSAGE", "input": float("inf")},
    ])
    response = await safe_validation_error(None, exception)
    assert response.status_code == 422
    data = json.loads(response.body)
    assert len(data["detail"]) == 2
    assert "PRIVATE" not in response.body.decode()
    assert all(set(error) == {"loc", "type", "msg"} for error in data["detail"])


def test_key_validation_keeps_generic_contract(client):
    response = client.post("/api/v1/keys", json={"provider": "bad", "api_key": "PRIVATE_KEY"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid key request"}
    assert "PRIVATE_KEY" not in response.text
