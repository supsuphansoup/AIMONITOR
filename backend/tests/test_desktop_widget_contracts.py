"""RC-01: health identification does not replace validation of each widget response."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")
pytest.importorskip("pystray")

from desktop import config, widget

AI = {"key": "codex", "provider": "openai", "plan_name": "API", "auth_mode": "api",
      "use_default_quota": False}
USAGE = {"requests": 3, "tokens": 13, "cost_usd": 1, "cost_unknown": 0}
ACTIVE = {"codex": {"windows": {name: dict(USAGE) for name in ("5h", "today", "7d", "month")}}}


def fetch(monkeypatch, active, spend=None, health_ready=True, spend_status=200):
    original = httpx.Client
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/health":
            payload = {"app": "AIMONITOR", "status": "ok"} if health_ready else {"status": "ok"}
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/keys/spend"):
            if isinstance(spend, httpx.Response):
                return spend
            return httpx.Response(spend_status, json=spend)
        return httpx.Response(200, json=active)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setattr(widget, "list_accounts", lambda: [])
    monkeypatch.setattr(widget, "account_quotas", lambda **kwargs: [])
    summary = Mock(wraps=widget.get_connected_ai_summary)
    monkeypatch.setattr(widget, "get_connected_ai_summary", summary)
    old = {"total": {"requests_today": 17}, "items": []}
    entries = [{"id": "cached", "label": "요청", "value": "17"}]
    ui = SimpleNamespace(backend_url="http://127.0.0.1:18803", _connected_ais=[AI], _fetching=True,
                         config={}, rate_updater=SimpleNamespace(rate=1400), root=SimpleNamespace(
                             after=lambda _delay, apply: apply()), _closed=False, is_connected=True,
                         summary_data=old, entries=entries, _render=Mock(), _resize=Mock(),
                         _schedule_poll=Mock(), poll_ms=3000)
    if spend is not None:
        ui._connected_ais = [{**AI, "key": "openai_api"}]
    ui._on_data = lambda *args: widget.FloatingWidget._on_data(ui, *args)
    widget.FloatingWidget._fetch_worker(ui)
    return ui, old, entries, summary, requests


@pytest.mark.parametrize("health_ready", [False, True])
@pytest.mark.parametrize("active", [
    {"status": "ok"}, [], None, {"codex": {}}, {"codex": {"windows": {"today": {}}}},
])
def test_malformed_active_contexts_keeps_cached_numbers_and_failure_status(monkeypatch, health_ready, active):
    ui, old, entries, summary, requests = fetch(monkeypatch, active, health_ready=health_ready)
    assert not ui.is_connected
    assert ui.summary_data is old and ui.entries is entries
    assert not ui._fetching
    summary.assert_not_called()
    ui._render.assert_called_once()
    ui._schedule_poll.assert_called_once_with(3000)
    assert requests[0].url.path == "/health"


@pytest.mark.parametrize("active", [{}, ACTIVE, {"codex": {
    **ACTIVE["codex"], "accounts": [{"account_id": None, **ACTIVE["codex"]}],
}}])
def test_empty_legacy_and_account_usage_remain_valid(monkeypatch, active):
    ui, old, _entries, summary, _requests = fetch(monkeypatch, active)
    assert ui.is_connected and ui.summary_data is not old
    summary.assert_called_once()
    assert ui.summary_data["total"]["requests_today"] == (3 if active else 0)


@pytest.mark.parametrize("spend", [
    httpx.Response(200, text="not-json"),
    {"status": "ok"}, {"items": [None]}, {"items": [{"kind": "spend", "amount": "bad", "currency": "USD"}]},
    {"items": [], "openai_api": {"kind": "spend", "amount": "bad", "currency": "USD"}},
    {"items": [], "openai_api": {"kind": "balance", "amount": 1, "currency": ""}},
])
def test_invalid_spend_is_ignored_like_non_success_response(monkeypatch, spend):
    ui, _old, _entries, summary, _requests = fetch(monkeypatch, {}, spend=spend)
    assert ui.is_connected
    summary.assert_called_once()
    assert summary.call_args.kwargs["official_spend"] == {}


@pytest.mark.parametrize("spend", [
    {"items": []}, {"openai_api": {"kind": "balance", "amount": 1, "currency": "USD"}},
    {"items": [{"kind": "unavailable", "amount": None}]},
])
def test_normal_empty_legacy_and_unavailable_spend_are_used(monkeypatch, spend):
    ui, _old, _entries, summary, _requests = fetch(monkeypatch, {}, spend=deepcopy(spend))
    assert ui.is_connected
    assert summary.call_args.kwargs["official_spend"] == spend


def test_non_success_spend_does_not_invalidate_usage(monkeypatch):
    ui, _old, _entries, summary, _requests = fetch(monkeypatch, {}, spend={"error": "synthetic"},
                                                spend_status=503)
    assert ui.is_connected
    assert summary.call_args.kwargs["official_spend"] == {}
