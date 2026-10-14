"""Draining includes pending batches and in-flight sends, across sync/async shutdown."""
import asyncio
import gc
import json
import subprocess
import sys
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from sdk.python.aimonitor import client as sdk

TRACE = {"provider": "custom", "model_name": "model"}


@pytest.fixture
def factory():
    monitors, gates = [], []

    def create(handler=None, **kwargs):
        sent = []

        def transport(request):
            sent.append((request.url.path, json.loads(request.content)))
            return handler(request) if handler else httpx.Response(201, json=[])

        mon = sdk.AIMonitor(api_url="http://testserver/api/v1", **kwargs)
        mon._sync_client.close()
        mon._sync_client = httpx.Client(transport=httpx.MockTransport(transport))
        monitors.append(mon)
        return mon, sent

    def gate():
        event = threading.Event()
        gates.append(event)
        return event

    create.gate = gate
    try:
        yield create
    finally:
        for event in gates:
            event.set()
        for mon in monitors:
            mon.close()


def test_flush_forces_pending_batch_and_reports_success(factory):
    mon, sent = factory(background=True, batch_size=25, flush_interval=30)
    assert mon.enqueue(**TRACE)
    assert mon.flush(timeout=1) is True
    assert len(sent) == 1
    assert mon.pending_count == 0


def test_flush_waits_for_inflight_and_reports_timeout(factory):
    entered, release = factory.gate(), factory.gate()

    def handler(_request):
        entered.set()
        assert release.wait(5)
        return httpx.Response(201, json=[])

    mon, _ = factory(handler, background=True, batch_size=1)
    mon.enqueue(**TRACE)
    assert entered.wait(1)
    assert mon.flush(timeout=0.03) is False
    assert mon.pending_count == 1
    assert mon.dropped_count == 0
    release.set()
    assert mon.flush(timeout=1) is True


@pytest.mark.parametrize("background", [False, True])
def test_close_rejects_new_enqueues(factory, background):
    mon, sent = factory(background=background)
    mon.close()
    assert mon.enqueue(**TRACE) is False
    assert mon.track(**TRACE) is None
    assert mon.pending_count == 0
    assert sent == []


def test_close_waits_before_closing_transport_and_drains_every_batch(factory):
    entered, release, finished = factory.gate(), factory.gate(), factory.gate()

    def handler(_request):
        entered.set()
        assert release.wait(5)
        assert not mon._sync_client.is_closed
        return httpx.Response(201, json=[])

    mon, sent = factory(handler, background=True, batch_size=1, timeout=0.01, flush_interval=0.1)
    for i in range(6):
        mon.enqueue(**TRACE, prompt_tokens=i)
    assert entered.wait(1)
    closer = threading.Thread(target=lambda: (mon.close(), finished.set()))
    closer.start()
    try:
        assert not finished.wait(0.3)
        assert mon.enqueue(**TRACE) is False
    finally:
        release.set()
        closer.join(2)
    assert not closer.is_alive()
    assert len(sent) == 6
    assert not mon._worker.is_alive()
    assert mon._sync_client.is_closed
    assert mon.pending_count == 0


@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_batch_delivery_loss_is_observable(factory, failure):
    def handler(_request):
        if failure == "timeout":
            raise httpx.ReadTimeout("SYNTHETIC_PROVIDER_SECRET")
        return httpx.Response(500)

    mon, _ = factory(handler, background=True, batch_size=2)
    assert mon.enqueue(**TRACE)
    assert mon.enqueue(**TRACE)
    assert mon.flush(timeout=1) is False
    assert mon.pending_count == 0
    assert mon.dropped_count == 2
    assert mon.close() is False


def test_concurrent_enqueue_and_flush_keep_every_accepted_trace(factory):
    mon, sent = factory(background=True, batch_size=25)

    def produce(i):
        return mon.enqueue(**TRACE, trace_id=f"trace-{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(produce, range(200)))
    assert mon.flush(timeout=2) is True
    traces = [trace for _, batch in sent for trace in batch["traces"]]
    assert len({trace["trace_id"] for trace in traces}) == len(traces) == 200
    assert all(len(batch["traces"]) <= 25 for _, batch in sent)


def test_close_also_waits_for_inline_sync_send(factory):
    entered, release, finished = factory.gate(), factory.gate(), factory.gate()

    def handler(_request):
        entered.set()
        assert release.wait(3)
        assert not mon._sync_client.is_closed
        return httpx.Response(201, json={"id": 1})

    mon, _ = factory(handler)
    sender = threading.Thread(target=lambda: mon.track(**TRACE))
    sender.start()
    assert entered.wait(1)
    closer = threading.Thread(target=lambda: (mon.close(), finished.set()))
    closer.start()
    try:
        assert not finished.wait(0.03)
        assert mon.pending_count == 1
    finally:
        release.set()
        sender.join(1)
        closer.join(1)
    assert finished.is_set()
    assert mon._sync_client.is_closed
    assert mon.dropped_count == 0


@pytest.mark.asyncio
async def test_async_close_waits_for_inline_async_send(factory):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(_request):
        entered.set()
        await release.wait()
        assert not transport.is_closed
        return httpx.Response(201, json={"id": 1})

    mon, _ = factory()
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    mon._async_client = transport
    sender = asyncio.create_task(mon.a_track(**TRACE))
    await entered.wait()
    closer = asyncio.create_task(mon.a_close())
    try:
        await asyncio.sleep(0.03)
        assert not closer.done()
        assert mon.pending_count == 1
    finally:
        release.set()
        await asyncio.wait_for(sender, 1)
        await asyncio.wait_for(closer, 1)
    assert transport.is_closed
    assert mon.dropped_count == 0


@pytest.mark.asyncio
async def test_async_observe_uses_background_batch(factory):
    mon, sent = factory(background=True, batch_size=25)

    async def wrong_transport(**kwargs):
        raise AssertionError("background observe used inline async transport")
    mon.a_track = wrong_transport

    @mon.observe()
    async def observed():
        return "ok"

    assert await observed() == "ok"
    assert await asyncio.to_thread(mon.flush, 1) is True
    assert len(sent) == 1
    assert sent[0][0].endswith("/traces/batch")


@pytest.mark.asyncio
async def test_async_context_closes_both_transports_and_worker(factory):
    mon, sent = factory(background=True, batch_size=25)
    async_transport = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201, json={})))
    mon._async_client = async_transport
    async with mon:
        mon.enqueue(**TRACE)
    assert len(sent) == 1
    assert not mon._worker.is_alive()
    assert mon._sync_client.is_closed
    assert async_transport.is_closed
    await mon.a_close()


@pytest.mark.asyncio
async def test_async_close_keeps_event_loop_responsive(factory):
    entered, release = factory.gate(), factory.gate()

    def handler(_request):
        entered.set()
        assert release.wait(2)
        return httpx.Response(201, json=[])

    mon, _ = factory(handler, background=True, batch_size=1)
    mon.enqueue(**TRACE)
    assert await asyncio.to_thread(entered.wait, 1)
    close_task = asyncio.create_task(mon.a_close())
    await asyncio.sleep(0.03)
    assert not close_task.done()
    release.set()
    await asyncio.wait_for(close_task, 1)
    assert mon._sync_client.is_closed


def test_close_unregisters_atexit_and_releases_monitor(monkeypatch):
    registered = []
    monkeypatch.setattr(sdk.atexit, "register", lambda callback: registered.append(callback))
    monkeypatch.setattr(sdk.atexit, "unregister", lambda callback: registered.remove(callback))
    mon = sdk.AIMonitor(api_url="http://testserver/api/v1", background=True)
    ref = weakref.ref(mon)
    mon.close()
    mon.close()
    assert registered == []
    del mon
    gc.collect()
    assert ref() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_cancelled_observe_records_error_without_delaying_cancellation(factory, background):
    entered, release = factory.gate(), factory.gate()
    async_release = asyncio.Event()
    sent_async = []

    def sync_handler(_request):
        entered.set()
        assert release.wait(3)
        return httpx.Response(201, json=[])

    async def async_handler(request):
        sent_async.append(json.loads(request.content))
        await async_release.wait()
        return httpx.Response(201, json={})

    mon, sent = factory(sync_handler, background=background, batch_size=1)
    mon._async_client = httpx.AsyncClient(transport=httpx.MockTransport(async_handler))
    started = asyncio.Event()

    @mon.observe()
    async def observed():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(observed())
    await started.wait()
    task.cancel("SYNTHETIC_PRIVATE_CANCELLATION")
    try:
        await asyncio.sleep(0.03)
        assert task.done()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(entered.wait, 1)
        trace = sent[0][1]["traces"][0]
        assert trace["status"] == "ERROR"
        assert trace["error_message"] == "CancelledError"
        assert "SYNTHETIC_PRIVATE" not in json.dumps(sent)
        assert sent_async == []
    finally:
        release.set()
        async_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await mon.a_close()


def test_atexit_drains_more_than_one_batch():
    code = '''
import atexit, json, time
import httpx
from sdk.python.aimonitor.client import AIMonitor
sent = []
atexit.register(lambda: print(json.dumps(sent)))
def handler(request):
    time.sleep(0.06)
    sent.extend(json.loads(request.content)["traces"])
    return httpx.Response(201, json=[])
mon = AIMonitor(api_url="http://testserver/api/v1", background=True,
                batch_size=1, timeout=0.01, flush_interval=0.1)
mon._sync_client.close()
mon._sync_client = httpx.Client(transport=httpx.MockTransport(handler))
for i in range(6):
    mon.enqueue(provider="custom", model_name="model", trace_id=str(i))
'''
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 6
