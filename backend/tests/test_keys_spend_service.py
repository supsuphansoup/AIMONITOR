"""Bounded parallel billing, shared requests, and cache lifecycle."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest

from backend.app.services import api_key_manager as km
from backend.app.services import provider_spend as ps

REAL_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def fake_provider_client(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(ps.httpx, "Client", Client)


def test_month_change_invalidates_monthly_cache(monkeypatch):
    clock = [datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(ps, "utc_now", lambda: clock[0])
    calls = []

    def fetch(secret, client):
        calls.append(secret)
        return ps._result("openrouter", "spend", amount=1, period_start=ps.month_start_utc().isoformat())

    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = ps.SpendService(lambda provider: "synthetic-key")
    service.spend_for("openrouter_api")
    clock[0] = datetime(2026, 11, 1, 0, 1, tzinfo=timezone.utc)
    result = service.spend_for("openrouter_api")
    assert len(calls) == 2 and result["period_start"].startswith("2026-11-01")


def test_simultaneous_misses_share_one_query(monkeypatch):
    ready, release = threading.Event(), threading.Event()
    calls = []

    def fetch(secret, client):
        calls.append(secret)
        ready.set()
        assert release.wait(3)
        return ps._result("openrouter", "spend", amount=1)

    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = ps.SpendService(lambda provider: "synthetic-key")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.spend_for, "openrouter_api")
        assert ready.wait(3)
        second = pool.submit(service.spend_for_key, "openrouter", "second-id", "synthetic-key")
        time.sleep(0.04)
        release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert len(calls) == 1


def test_expired_cache_entries_are_pruned(monkeypatch):
    service = ps.SpendService(lambda provider: None, ttl=0.01)
    def fetch(secret, client):
        return ps._result("openrouter", "spend", amount=1)
    for index in range(12):
        service._spend_secret("openrouter_api", f"synthetic-{index}", fetch, False, ("openrouter", str(index)))
    time.sleep(0.02)
    service._spend_secret("openrouter_api", "synthetic-new", fetch, False, ("openrouter", "new"))
    assert len(service._cache) == 1


def test_cache_has_size_limit(monkeypatch):
    monkeypatch.setattr(ps, "MAX_CACHE_ENTRIES", 4, raising=False)
    service = ps.SpendService(lambda provider: None)
    def fetch(secret, client):
        return ps._result("openrouter", "spend", amount=1)
    for index in range(8):
        service._spend_secret("openrouter_api", f"synthetic-{index}", fetch, False, ("openrouter", str(index)))
    assert len(service._cache) <= 4


def test_batch_preserves_fast_results_and_bounds_slow_wait(monkeypatch):
    release = threading.Event()

    def fetch(secret, client):
        if secret == "synthetic-slow":
            release.wait(3)
        return ps._result("openrouter", "spend", amount=1)

    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = ps.SpendService(lambda provider: None, budget=0.15)
    entries = [{"provider": "openrouter", "key_id": str(i), "label": "키", "secret": secret}
               for i, secret in enumerate(("synthetic-slow", "synthetic-fast"))]
    try:
        started = time.monotonic()
        result = service.spend_snapshot(entries)
        elapsed = time.monotonic() - started
        assert elapsed < 0.8
        assert result["items"][0]["kind"] == "unavailable"
        assert result["items"][1]["amount"] == 1
    finally:
        release.set()


def test_parallel_spend_all_has_one_total_budget(monkeypatch):
    release = threading.Event()

    def fetch(secret, client):
        release.wait(3)
        return ps._result("openrouter", "spend", amount=1)

    for card, provider in (("openrouter_api", "openrouter"), ("deepseek_api", "deepseek")):
        monkeypatch.setitem(ps.FETCHERS, card, (provider, fetch))
    service = ps.SpendService(lambda provider: "synthetic-" + provider, budget=0.1)
    try:
        started = time.monotonic()
        result = service.spend_all(["openrouter_api", "deepseek_api"])
        assert time.monotonic() - started < 0.5
        assert all(value["kind"] == "unavailable" for value in result.values())
    finally:
        release.set()


def test_snapshot_deduplicates_representative_and_duplicate_secrets(monkeypatch):
    calls = []

    def fetch(secret, client):
        calls.append(secret)
        return ps._result("openrouter", "spend", amount=len(calls))

    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = ps.SpendService(lambda provider: None)
    entries = [{"provider": "openrouter", "key_id": str(i), "label": "키", "secret": "synthetic-same"}
               for i in range(2)]
    result = service.spend_snapshot(entries)
    assert len(calls) == 1 and len(result["items"]) == 2
    assert result["openrouter_api"]["amount"] == result["items"][0]["amount"]
    assert "synthetic-same" not in str(service._cache)


@pytest.mark.skipif(km.secret_store.AVAILABLE_SCHEME != km.secret_store.SCHEME_DPAPI, reason="real DPAPI")
def test_key_deletion_invalidates_cached_identity(tmp_path, monkeypatch):
    manager = km.APIKeyManager(tmp_path / "keys.json")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    service = ps.SpendService(manager.get_registered_key)
    monkeypatch.setattr(km, "spend_service", service)
    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", (
        "openrouter", lambda secret, client: ps._result("openrouter", "spend", amount=1)))
    item = manager.add_key("openrouter", "synthetic-key")
    service.spend_for_key("openrouter", item["key_id"], "synthetic-key")
    assert service._cache
    manager.delete_key("openrouter", item["key_id"])
    assert not service._cache


def test_pending_work_is_bounded(monkeypatch):
    release = threading.Event()
    calls = []

    def fetch(secret, client):
        calls.append(secret)
        release.wait(3)
        return ps._result("openrouter", "spend", amount=1)

    monkeypatch.setattr(ps, "MAX_PENDING_FETCHES", 2)
    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = ps.SpendService(lambda provider: None, budget=0.1)
    entries = [{"provider": "openrouter", "key_id": str(i), "label": "키", "secret": f"synthetic-{i}"}
               for i in range(20)]
    try:
        result = service.spend_snapshot(entries)
        assert service._pending <= 2 and len(calls) <= 2
        assert len(result["items"]) == 20 and all(k["kind"] == "unavailable" for k in result["items"])
    finally:
        release.set()


def test_http_timeout_uses_remaining_budget(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"data": {"usage_monthly": 1}})

    monkeypatch.setattr(ps.httpx, "Client", lambda **kwargs:
                        REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    service = ps.SpendService(lambda provider: "synthetic-key", budget=0.3)
    assert service.spend_for("openrouter_api")["amount"] == 1
    assert seen and all(0 < timeout <= 0.3 for timeout in seen[0].values())


def test_invalidation_during_query_does_not_repopulate_deleted_cache(monkeypatch):
    ready, release = threading.Event(), threading.Event()

    def fetch(secret, client):
        ready.set()
        assert release.wait(3)
        return ps._result("openrouter", "spend", amount=1)

    monkeypatch.setitem(ps.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = ps.SpendService(lambda provider: "synthetic-key")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.spend_for, "openrouter_api")
        assert ready.wait(3)
        service.invalidate("openrouter")
        release.set()
        future.result(timeout=3)
    assert not service._cache
