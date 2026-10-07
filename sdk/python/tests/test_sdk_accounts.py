"""Account identity survives all SDK delivery modes without a default attribution."""
import asyncio
import json
import queue

import httpx

from sdk.python.aimonitor.client import AIMonitor

ACCOUNT = "api:0123456789ab"


def test_sync_track_and_observe_keep_account_id():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json=sent[-1])

    with AIMonitor() as monitor:
        monitor._sync_client.close()
        monitor._sync_client = httpx.Client(transport=httpx.MockTransport(handler))
        assert monitor.track("openai", "gpt-4o", account_id=ACCOUNT)["account_id"] == ACCOUNT
        assert monitor.track("openai", "gpt-4o")["account_id"] is None

        @monitor.observe(account_id=ACCOUNT)
        def observed():
            return "ok"

        assert observed() == "ok"
        assert sent[-1]["account_id"] == ACCOUNT


def test_async_track_and_observe_keep_account_id():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json=sent[-1])

    async def run():
        monitor = AIMonitor()
        monitor._async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert (await monitor.a_track("openai", "gpt-4o", account_id=ACCOUNT))["account_id"] == ACCOUNT

            @monitor.observe(account_id=ACCOUNT)
            async def observed():
                return "ok"

            assert await observed() == "ok"
            assert sent[-1]["account_id"] == ACCOUNT
        finally:
            await monitor.a_close()
            monitor.close()

    asyncio.run(run())


def test_enqueue_and_batch_keep_account_id():
    sent = []
    with AIMonitor() as monitor:
        monitor._sync_client.close()
        monitor._sync_client = httpx.Client(transport=httpx.MockTransport(
            lambda request: (sent.append(json.loads(request.content)) or httpx.Response(201, json=[]))
        ))
        monitor._queue = queue.Queue()
        assert monitor.enqueue(provider="openai", model_name="gpt-4o", account_id=ACCOUNT)
        assert monitor.enqueue(provider="openai", model_name="gpt-4o")
        monitor._post_batch([monitor._queue.get_nowait(), monitor._queue.get_nowait()])
        assert [trace["account_id"] for trace in sent[0]["traces"]] == [ACCOUNT, None]
