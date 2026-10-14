"""Theme notifications and backend authentication without launching the app."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")
pytest.importorskip("pystray")

import customtkinter as ctk
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from backend.app.services import secret_store
from backend.tests.test_accounts_watcher_regressions import batch_results
from desktop import app_gui, claude_quota_syncer, config, main, theme, widget
from desktop.ai_detector import AIDetector
from desktop.ai_watcher import UnifiedAIWatcher
from desktop.tray import TrayManager, create_tray_icon_image


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "desktop_config.json")
    monkeypatch.delenv("AIMONITOR_API_KEY", raising=False)


@pytest.mark.parametrize("source", ["app", "os"])
def test_mode_notification_updates_chart_widget_and_tray_without_fetch(monkeypatch, source):
    tracker = ctk.AppearanceModeTracker
    root = Mock()
    monkeypatch.setattr(tracker, "callback_list", [])
    monkeypatch.setattr(tracker, "app_list", [])
    monkeypatch.setattr(tracker, "update_loop_running", False)
    monkeypatch.setattr(tracker, "appearance_mode", 0)
    monkeypatch.setattr(tracker, "appearance_mode_set_by", "system")
    monkeypatch.setattr(tracker, "get_tk_root_of_widget", lambda _widget: root)
    # The real CTk root paints its own children; exercise its existing chart callback separately.
    monkeypatch.setattr(ctk.CTk, "_set_appearance_mode", Mock())
    client = Mock()
    monkeypatch.setattr(httpx, "Client", client)
    fig = Figure()
    rows = [{"model_name": "model", "total_cost_usd": 1, "total_tokens": 10}]
    window = app_gui.DesktopMainWindow.__new__(app_gui.DesktopMainWindow)
    window.__dict__.update(fig=fig, ax_cost=fig.add_subplot(121), ax_tokens=fig.add_subplot(122),
        krw_rate=1400, _chart_sig=None, canvas=FigureCanvasAgg(fig), breakdown_data=rows,
        cached_summary=None)
    window._style_axis = lambda ax, title: app_gui.DesktopMainWindow._style_axis(window, ax, title)
    window._update_native_charts = lambda bd: app_gui.DesktopMainWindow._update_native_charts(window, bd)
    floating = SimpleNamespace(root=root, frame=Mock(), mode="mini", _theme_mode="light",
        summary_data={}, entries=[], is_connected=False, mini_text=Mock(), _resize=Mock(),
        _build_ui=Mock(), _bind_events=Mock(), _visible=lambda: [])
    tray = TrayManager()
    tray._icon = SimpleNamespace(icon=create_tray_icon_image(), stop=Mock())
    old_image = tray._icon.icon
    theme.observe(root, lambda mode: app_gui.DesktopMainWindow._set_appearance_mode(window, mode))
    theme.observe(root, lambda _mode: widget.FloatingWidget._render(floating))
    tray._unsubscribe_theme = theme.observe(root, tray._on_theme_changed)

    if source == "app":
        ctk.set_appearance_mode("dark")
    else:
        monkeypatch.setattr(tracker, "detect_appearance_mode", lambda: 1)
        tracker.update()

    assert to_hex(fig.get_facecolor()) == theme.CARD[1]
    assert to_hex(window.ax_cost.title.get_color()) == theme.TEXT[1]
    assert to_hex(window.ax_cost.patches[0].get_facecolor()) == theme.ACCENT[1]
    assert floating._theme_mode == "dark"
    root.configure.assert_called_with(bg=theme.CARD[1])
    floating._build_ui.assert_called_once()
    assert tray._icon.icon is not old_image
    assert tray._icon.icon.getpixel((10, 32))[:3] == (24, 30, 42)
    dark_image = tray._icon.icon
    ctk.set_appearance_mode("light")
    assert to_hex(fig.get_facecolor()) == theme.CARD[0]
    assert to_hex(window.ax_cost.patches[0].get_facecolor()) == theme.ACCENT[0]
    assert floating._theme_mode == "light"
    assert tray._icon.icon is not dark_image
    assert tray._icon.icon.getpixel((10, 32))[:3] == (255, 255, 255)
    client.assert_not_called()
    tray.stop()
    assert len(tracker.callback_list) == 2
    for call in root.bind.call_args_list:
        call.args[1](SimpleNamespace(widget=root))
    assert not tracker.callback_list


@pytest.mark.parametrize("settings,environment,expected", [
    ({}, "", {}), ({"api_key": "  "}, "", {}),
    ({}, " env-test-key ", {"X-API-Key": "env-test-key"}),
    ({"api_key": " config-test-key "}, "env-test-key", {"X-API-Key": "config-test-key"}),
])
def test_server_key_precedence_and_no_key_header(monkeypatch, settings, environment, expected):
    monkeypatch.setenv("AIMONITOR_API_KEY", environment)
    assert config.backend_headers(settings) == expected


@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="Windows DPAPI")
def test_config_roundtrip_seals_key_and_preserves_preferences(caplog):
    settings = {**config.DEFAULT_CONFIG, "api_key": "test-only-server-secret", "widget_opacity": 0.7}
    config.save_config(settings)
    raw = config.CONFIG_FILE.read_text(encoding="utf-8")
    assert "test-only-server-secret" not in raw
    assert "api_key" not in json.loads(raw)
    assert json.loads(raw)["api_key_scheme"] == "dpapi"
    restored = config.load_config()
    assert restored["api_key"] == settings["api_key"]
    assert restored["widget_opacity"] == 0.7
    assert config.backend_headers() == {"X-API-Key": settings["api_key"]}
    assert "test-only-server-secret" not in caplog.text


def test_config_fails_closed_if_sealing_fails(monkeypatch, caplog):
    monkeypatch.setattr(secret_store, "AVAILABLE_SCHEME", secret_store.SCHEME_UNAVAILABLE)
    with pytest.raises(ValueError):
        config.save_config({"api_key": "test-only-server-secret"})
    assert not config.CONFIG_FILE.exists()
    assert "test-only-server-secret" not in caplog.text


def transport_client(monkeypatch, responder, include_health=False):
    real_client = httpx.Client
    requests = []

    def handler(request):
        if request.url.path == "/health":
            if include_health:
                requests.append(request)
            return httpx.Response(200, json={"app": "AIMONITOR", "status": "ok"})
        requests.append(request)
        return responder(request)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    return requests


@pytest.mark.parametrize("status", [201, 401, 403])
def test_watcher_attaches_header_and_survives_auth_errors(monkeypatch, caplog, status):
    monkeypatch.setenv("AIMONITOR_API_KEY", "test-only-server-secret")

    def respond(request):
        body = batch_results(json.loads(request.content)["traces"]) if status == 201 else []
        return httpx.Response(status, json=body)

    requests = transport_client(monkeypatch, respond, include_health=True)
    watcher = UnifiedAIWatcher.__new__(UnifiedAIWatcher)
    watcher.backend_url = "http://testserver"
    result = watcher._send_traces([{"provider": "openai", "model_name": "gpt-4o"}])
    assert requests[0].url.path == "/health"
    assert "X-API-Key" not in requests[0].headers
    assert requests[1].headers["X-API-Key"] == "test-only-server-secret"
    assert requests[1].url.path == "/api/v1/traces/batch"
    assert result is (status == 201)
    if status in (401, 403):
        assert "인증 필요" in caplog.text
    assert "test-only-server-secret" not in caplog.text


@pytest.mark.parametrize("key", [None, "test-only-server-secret"])
def test_health_probe_and_watchdog_never_send_server_key(monkeypatch, key):
    if key:
        monkeypatch.setenv("AIMONITOR_API_KEY", key)
    requests = transport_client(monkeypatch, lambda _request: httpx.Response(
        200, json={"app": "AIMONITOR", "status": "ok"}), include_health=True)
    controller = main.DesktopApp.__new__(main.DesktopApp)
    import threading

    controller._spawn_lock = threading.Lock()
    controller.backend_url = "http://127.0.0.1:18771"
    controller._stop_event = Mock()
    controller._stop_event.is_set.return_value = False
    controller._stop_event.wait.side_effect = [False, True]
    controller.ensure_backend()
    controller._watch_backend()
    assert len(requests) == 2
    assert all("X-API-Key" not in request.headers for request in requests)


def test_ollama_and_oauth_never_receive_backend_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMONITOR_API_KEY", "test-only-server-secret")
    requests = transport_client(monkeypatch, lambda _request: httpx.Response(200, json={"models": []}))
    detector = AIDetector.__new__(AIDetector)
    assert detector._check_ollama()["is_connected"] is True
    assert "X-API-Key" not in requests[0].headers
    assert requests[0].url.port == 11434

    monkeypatch.setattr(claude_quota_syncer, "CACHE_FILE", tmp_path / "quota.json")
    syncer = claude_quota_syncer.ClaudeQuotaSyncer()
    syncer.cred_file = tmp_path / "credentials.json"
    syncer.cred_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": "test-only-oauth-token"}}))
    get = Mock(return_value=httpx.Response(200, json={}))
    monkeypatch.setattr(claude_quota_syncer.httpx, "get", get)
    assert syncer.fetch_fresh() is not None
    headers = get.call_args.kwargs["headers"]
    assert "X-API-Key" not in headers
    assert "test-only-server-secret" not in str(headers)
    assert headers["Authorization"] == "Bearer test-only-oauth-token"
    assert headers["User-Agent"].startswith("AIMONITOR/")  # never impersonate Claude Code


@pytest.mark.parametrize("status", [200, 401, 403])
def test_main_fetch_attaches_header_to_all_queries_and_reports_auth(monkeypatch, status, caplog):
    requests = transport_client(monkeypatch, lambda _request: httpx.Response(status, json={}))
    monkeypatch.setattr(app_gui, "get_connected_ai_summary", lambda *_args, **_kwargs: {"items": []})
    ui = SimpleNamespace(backend_url="http://testserver", official_spend={}, _is_updating=True,
        after=lambda _delay, callback: callback(), _apply_data=Mock())
    app_gui.DesktopMainWindow._fetch_worker(ui, [{"key": "openai_api"}],
        {"api_key": "test-only-server-secret"}, 1400)
    assert len(requests) == 5
    assert all(request.headers["X-API-Key"] == "test-only-server-secret" for request in requests)
    assert ui._apply_data.call_args.args[-1] is (status in (401, 403))
    assert ui._is_updating is False
    assert "test-only-server-secret" not in caplog.text


def test_main_auth_error_uses_statusbar_and_keeps_polling():
    ui = SimpleNamespace(lbl_status_msg=Mock(), _schedule_refresh=Mock())
    app_gui.DesktopMainWindow._apply_data(ui, {}, [], [], None, False, True)
    assert ui.lbl_status_msg.configure.call_args.kwargs["text"] == "인증 필요"
    ui._schedule_refresh.assert_called_once()


@pytest.mark.parametrize("status", [401, 403])
def test_partial_query_success_does_not_hide_spend_auth_failure(monkeypatch, status):
    def respond(request):
        code = status if request.url.path.endswith("/keys/spend") else 200
        return httpx.Response(code, json={})

    transport_client(monkeypatch, respond)
    monkeypatch.setattr(app_gui, "get_connected_ai_summary", lambda *_args, **_kwargs: {"items": []})
    ui = SimpleNamespace(backend_url="http://testserver", official_spend={}, _is_updating=True,
        after=lambda _delay, callback: callback(), _apply_data=Mock())
    app_gui.DesktopMainWindow._fetch_worker(ui, [{"key": "openai_api"}], {}, 1400)
    assert ui._apply_data.call_args.args[-2:] == (True, True)


def test_http_error_message_cannot_echo_server_key(monkeypatch, caplog):
    def fail(_request):
        raise httpx.RequestError("test-only-server-secret upstream text")

    monkeypatch.setenv("AIMONITOR_API_KEY", "test-only-server-secret")
    transport_client(monkeypatch, fail)
    ui = SimpleNamespace(backend_url="http://testserver", official_spend={}, _is_updating=True,
        after=lambda _delay, callback: callback(), _apply_data=Mock())
    with caplog.at_level("DEBUG", logger="aimonitor.desktop.app_gui"):
        app_gui.DesktopMainWindow._fetch_worker(ui, [], {}, 1400)
    assert "RequestError" in caplog.text
    assert "test-only-server-secret" not in caplog.text
    assert ui._is_updating is False


@pytest.mark.parametrize("status", [200, 401, 403])
def test_widget_queries_use_header_and_report_auth(monkeypatch, status):
    monkeypatch.setenv("AIMONITOR_API_KEY", "test-only-server-secret")
    requests = transport_client(monkeypatch, lambda _request: httpx.Response(status, json={}))
    monkeypatch.setattr(widget, "get_connected_ai_summary", lambda *_args, **_kwargs: {"items": []})
    ui = SimpleNamespace(backend_url="http://testserver", _connected_ais=[{"key": "openai_api"}],
        rate_updater=SimpleNamespace(rate=1400), config={}, _fetching=True, _on_data=Mock(),
        root=SimpleNamespace(after=lambda _delay, callback: callback()))
    widget.FloatingWidget._fetch_worker(ui)
    assert len(requests) == 2
    assert all(request.headers["X-API-Key"] == "test-only-server-secret" for request in requests)
    assert ui._on_data.call_args.args[-1] is (status in (401, 403))
    assert ui._fetching is False


def test_widget_auth_error_shows_message_with_cached_data_and_recovers(monkeypatch):
    monkeypatch.setattr(theme, "appearance_mode", lambda: "light")
    ui = SimpleNamespace(mode="mini", _theme_mode="light", mini_text=Mock(), lbl_status=Mock(),
        _resize=Mock(), _visible=lambda: [], root=Mock(), poll_ms=3000,
        entries=[{"group": "AI", "label": "cost", "value": "old"}], summary_data={}, schedule_update=Mock())
    ui._render = lambda: widget.FloatingWidget._render(ui)
    ui._closed = False
    ui._poll_job = None
    ui._cancel_poll = lambda: widget.FloatingWidget._cancel_poll(ui)
    ui._schedule_poll = lambda delay: widget.FloatingWidget._schedule_poll(ui, delay)
    widget.FloatingWidget._on_data(ui, {}, False, True)
    ui.mini_text.config.assert_called_with(text="인증 필요")
    ui.root.after.assert_called_once_with(3000, ui.schedule_update)
    assert ui._auth_required is True
    # A successful next poll clears the auth message; no process restart is needed.
    widget.FloatingWidget._on_data(ui, {"items": []}, True, False)
    assert ui._auth_required is False
    ui.mini_text.config.assert_called_with(text="표시할 항목 없음")
