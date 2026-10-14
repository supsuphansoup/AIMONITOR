"""SDK data minimization and instrumentation failure isolation."""
import asyncio
import json
import queue
from types import SimpleNamespace

import httpx
import pytest

from backend.app.core import sanitize
from sdk.python.aimonitor import client as sdk


@pytest.fixture
def monitor():
    sent = []

    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(201, json=payload)

    mon = sdk.AIMonitor(api_url="http://testserver/api/v1")
    mon._sync_client.close()
    mon._sync_client = httpx.Client(transport=httpx.MockTransport(handler))
    mon._async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        yield mon, sent
    finally:
        asyncio.run(mon.a_close())
        mon.close()


def test_sdk_sensitive_key_list_matches_server():
    assert sdk._SENSITIVE_KEY_PARTS == sanitize._SENSITIVE_KEY_PARTS


@pytest.mark.parametrize("mode", ["sync", "async", "queue"])
def test_metadata_is_redacted_before_transport_or_queue(monitor, mode):
    mon, sent = monitor
    metadata = {"items": [[{"PrOmPt": {"raw": "SYNTHETIC_SECRET"}, "ok": 7}]],
                "many": list(range(60))}
    kwargs = {"provider": "custom", "model_name": "model", "metadata": metadata}
    if mode == "sync":
        mon.track(**kwargs)
        payload = sent[-1]
    elif mode == "async":
        asyncio.run(mon.a_track(**kwargs))
        payload = sent[-1]
    else:
        mon._queue = queue.Queue()
        assert mon.enqueue(**kwargs)
        payload = mon._queue.get_nowait()
    assert payload["metadata"] == {"items": [[{"PrOmPt": "[redacted]", "ok": 7}]],
                                   "many": list(range(50))}
    assert "SYNTHETIC_SECRET" not in json.dumps(payload)
    assert metadata["items"][0][0]["PrOmPt"] == {"raw": "SYNTHETIC_SECRET"}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_observe_errors_send_only_class_name(monitor, asynchronous):
    mon, sent = monitor
    original = RuntimeError("SYNTHETIC_SECRET_FROM_PROVIDER")
    if asynchronous:
        @mon.observe()
        async def observed():
            raise original
        def call():
            return asyncio.run(observed())
    else:
        @mon.observe()
        def observed():
            raise original
        call = observed
    with pytest.raises(RuntimeError) as raised:
        call()
    assert raised.value is original
    assert sent[-1]["status"] == "ERROR"
    assert sent[-1]["error_message"] == "RuntimeError"
    assert "SYNTHETIC_SECRET" not in json.dumps(sent)


def test_explicit_error_message_is_opt_in(monitor):
    mon, sent = monitor
    mon.track("custom", "model", error_message="explicit diagnostic")
    assert sent[-1]["error_message"] == "explicit diagnostic"


@pytest.mark.parametrize("mode", ["sync", "async", "queue"])
@pytest.mark.parametrize("latency", [None, 0.0, 15.5])
def test_explicit_latency_preserves_missing_and_zero(monitor, mode, latency):
    mon, sent = monitor
    kwargs = {"provider": "custom", "model_name": "model"}
    if latency is not None:
        kwargs["latency_ms"] = latency
    if mode == "sync":
        mon.track(**kwargs)
        payload = sent[-1]
    elif mode == "async":
        asyncio.run(mon.a_track(**kwargs))
        payload = sent[-1]
    else:
        mon._queue = queue.Queue()
        assert mon.enqueue(**kwargs)
        payload = mon._queue.get_nowait()
    assert payload["latency_ms"] == latency


@pytest.mark.parametrize("as_object", [False, True])
def test_anthropic_one_hour_write_is_a_subset(as_object):
    usage = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100,
             "cache_creation": {"ephemeral_5m_input_tokens": 60, "ephemeral_1h_input_tokens": 40}}
    response = {"usage": usage}
    if as_object:
        usage["cache_creation"] = SimpleNamespace(**usage["cache_creation"])
        response = SimpleNamespace(usage=SimpleNamespace(**usage))
    extracted = sdk.extract_usage(response)
    assert extracted["cache_write_tokens"] == 100
    assert extracted["cache_write_1h_tokens"] == 40
    assert sum(extracted[key] for key in (
        "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens",
    )) == 115


@pytest.mark.parametrize("mode", ["sync", "async", "queue", "observe", "async_observe"])
def test_all_sdk_paths_keep_one_hour_write_subset(monitor, mode):
    mon, sent = monitor
    kwargs = {"provider": "anthropic", "model_name": "claude-sonnet-4",
              "cache_write_tokens": 100, "cache_write_1h_tokens": 40}
    if mode == "sync":
        mon.track(**kwargs)
        payload = sent[-1]
    elif mode == "async":
        asyncio.run(mon.a_track(**kwargs))
        payload = sent[-1]
    elif mode == "queue":
        mon._queue = queue.Queue()
        assert mon.enqueue(**kwargs)
        payload = mon._queue.get_nowait()
    else:
        response = {"usage": {"cache_creation_input_tokens": 100,
                              "cache_creation": {"ephemeral_1h_input_tokens": 40}}}
        if mode == "observe":
            @mon.observe(provider="anthropic")
            def observed():
                return response
            assert observed() is response
        else:
            @mon.observe(provider="anthropic")
            async def observed():
                return response
            assert asyncio.run(observed()) is response
        payload = sent[-1]
    assert payload["cache_write_tokens"] == 100
    assert payload["cache_write_1h_tokens"] == 40


@pytest.mark.parametrize("usage", [None, [], {"prompt_tokens": "1"}, {"prompt_tokens": float("inf")},
                                   {"prompt_tokens": 1_000_000_001}, {"status": "ERROR"},
                                   {"latency_ms": 12}, {"prompt_tokens": 1, "completion_tokens": None}])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_invalid_extractor_result_cannot_replace_the_return(monitor, usage, asynchronous):
    mon, sent = monitor
    expected = object()
    if asynchronous:
        @mon.observe(usage_extractor=lambda _: usage)
        async def observed():
            return expected
        result = asyncio.run(observed())
    else:
        @mon.observe(usage_extractor=lambda _: usage)
        def observed():
            return expected
        result = observed()
    assert result is expected
    assert sent[-1]["status"] == "SUCCESS"
    assert sent[-1]["prompt_tokens"] == 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_instrumentation_failure_preserves_user_result_and_exception(monitor, monkeypatch, asynchronous, fails):
    mon, _ = monitor

    def fail(*args, **kwargs):
        raise ValueError("instrumentation failed")
    monkeypatch.setattr(mon, "_build_payload", fail)
    expected = object()
    original = RuntimeError("original failure")
    if asynchronous:
        @mon.observe()
        async def observed():
            if fails:
                raise original
            return expected
        def call():
            return asyncio.run(observed())
    else:
        @mon.observe()
        def observed():
            if fails:
                raise original
            return expected
        call = observed
    if fails:
        with pytest.raises(RuntimeError) as raised:
            call()
        assert raised.value is original
    else:
        assert call() is expected
