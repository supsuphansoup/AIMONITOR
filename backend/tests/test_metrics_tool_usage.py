"""Stored SDK usage reaches detected tools and both desktop worker data paths."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.routers import metrics, traces
from backend.app.services import aggregator, api_key_manager
from desktop import ai_detector, app_gui, config, context_manager, display, widget
from sdk.python.aimonitor.client import AIMonitor

NOW = datetime(2026, 10, 14, 3)


@pytest.fixture
def tool_bridge(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for variable in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        directory = tmp_path / variable
        directory.mkdir()
        monkeypatch.setenv(variable, str(directory))
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AIMONITOR_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(settings, "API_KEY", None)
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", False)
    for module in (aggregator, metrics, traces, context_manager):
        monkeypatch.setattr(module, "utc_now", lambda: NOW)
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setattr(ai_detector, "detector", ai_detector.AIDetector())
    monkeypatch.setattr(ai_detector.AIDetector, "_check_registered_api_keys", lambda self: [])
    monkeypatch.setattr(api_key_manager.api_key_manager, "list_keys", lambda: [])
    monkeypatch.setattr(api_key_manager.spend_service, "spend_all", lambda keys: {})
    monkeypatch.setattr(context_manager, "windows_for", lambda *args, **kwargs: [])
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: [])
    monkeypatch.setattr(app_gui, "account_quotas", lambda **kwargs: [])
    monkeypatch.setattr(widget, "list_accounts", lambda: [])
    monkeypatch.setattr(widget, "account_quotas", lambda **kwargs: [])
    calls = []

    def handle(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.host == "testserver":
            response = client.request(
                request.method, request.url.raw_path.decode(), content=request.content, headers=request.headers,
            )
            return httpx.Response(response.status_code, json=response.json())
        assert request.url.host == "127.0.0.1" and request.url.port == 11434
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.1"}]})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    return SimpleNamespace(home=tmp_path, calls=calls)


def worker_summaries(monkeypatch, ais):
    main_data, widget_data = [], []
    main = SimpleNamespace(
        backend_url="http://testserver", official_spend={}, _is_updating=True,
        after=lambda _delay, callback: callback(), _apply_data=lambda *args: main_data.append(args),
    )
    app_gui.DesktopMainWindow._fetch_worker(main, ais, {}, 1400)
    floating = SimpleNamespace(
        backend_url="http://testserver", _connected_ais=ais, config={}, _fetching=True,
        rate_updater=SimpleNamespace(rate=1400), root=SimpleNamespace(after=lambda _delay, callback: callback()),
        _on_data=lambda *args: widget_data.append(args),
    )
    widget.FloatingWidget._fetch_worker(floating)
    assert main_data[0][4] is True and widget_data[0][1] is True
    assert main._is_updating is False and floating._fetching is False
    return main_data[0][3], widget_data[0][0]


def test_ollama_sdk_usage_reaches_connected_main_and_widget(client, monkeypatch, tool_bridge):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    try:
        stored = monitor.track(provider="ollama", model_name="llama3.1", prompt_tokens=100, completion_tokens=50)
    finally:
        monitor.close()
    assert stored["cost_source"] == "local_free" and stored["total_tokens"] == 150
    ais = ai_detector.detect_connected_ais()
    assert [ai["key"] for ai in ais] == ["ollama"]
    response = client.get("/api/v1/metrics/active_contexts")
    assert response.status_code == 200
    active = response.json()
    assert set(active) == {"ollama"}
    assert active["ollama"]["context_tokens"] == 100
    connected = client.get("/api/v1/metrics/connected_ais")
    assert connected.status_code == 200
    summaries = [connected.json(), *worker_summaries(monkeypatch, ais)]
    for summary in summaries:
        item = summary["by_key"]["ollama"]
        assert summary["total"]["requests_today"] == 1
        assert item["cost_meaning"] == "free" and item["context"]["tokens"] == 100
        for usage in item["usage"].values():
            assert usage["requests"] == 1 and usage["tokens"] == 150
            assert usage["cost_usd"] == 0 and usage["cost_unknown"] == 0
        assert display.usage_cost_text(item["usage"]["today"], "free") == "￦0 (로컬)"
        entries = display.widget_entries(summary)
        assert next(row for row in entries if row["id"] == "ollama:today")["detail"] == "1회 요청"
        assert next(row for row in entries if row["id"] == "ollama:context")["value"].startswith("100/")
    assert ("POST", "testserver", "/api/v1/traces") in tool_bridge.calls


@pytest.mark.parametrize("provider,tags,key", [
    ("ollama", None, "ollama"), ("openai", None, "openai_api"),
    ("custom", None, "custom_api"), ("ollama", ["copilot", "claude-code"], "claude"),
    ("ollama", ["copilot"], "copilot"), ("ollama", ["CODEX"], "ollama"),
])
def test_local_tool_key_preserves_watcher_priority(client, tool_bridge, provider, tags, key):
    response = client.post("/api/v1/traces", json={
        "provider": provider, "model_name": "synthetic", "prompt_tokens": 100, "tags": tags,
    })
    assert response.status_code == 201
    active = client.get("/api/v1/metrics/active_contexts").json()
    assert set(active) == {key}
    assert active[key]["windows"]["5h"]["requests"] == 1
    assert active[key]["accounts"][0]["model_name"] == "synthetic"
