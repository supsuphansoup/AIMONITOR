"""Reject foreign HTTP services and malformed desktop API payloads without opening a GUI."""
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")
pytest.importorskip("pystray")

from desktop import app_gui, display, main, widget

AI = {"key": "openai_api", "provider": "openai", "plan_name": "API"}


def valid_payloads():
    return {"overview": {"total_requests": 0, "total_tokens": 0, "total_cost_usd": 0},
            "breakdown": [], "traces": [], "active_contexts": {}, "spend": {"items": []}}


def mock_transport(monkeypatch, respond):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs))


def controller():
    app = main.DesktopApp.__new__(main.DesktopApp)
    app.__dict__.update(backend_url="http://127.0.0.1:18802", _backend_proc=None,
                        _stop_event=threading.Event(), _backend_lock=threading.RLock(),
                        _spawn_lock=threading.Lock())
    return app


def fake_window(monkeypatch, payloads=None):
    responses = valid_payloads() if payloads is None else payloads
    requests = []

    def respond(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        requests.append(endpoint)
        result = responses[endpoint]
        return result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)

    mock_transport(monkeypatch, respond)
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: [])
    monkeypatch.setattr(app_gui.api_key_manager, "list_keys", lambda: [])
    ui = SimpleNamespace(backend_url="http://127.0.0.1:18802", official_spend={"items": []},
                         breakdown_data=[{"old": "chart"}], traces_data=[{"old": "trace"}],
                         after=lambda _delay, callback: callback(), _apply_data=Mock(), _is_updating=True)
    return ui, requests


@pytest.mark.parametrize("body,status", [
    ({"service": "foreign"}, 200), ({"status": "ok"}, 200), ([], 200), (None, 200),
    ({"app": "AIMONITOR", "status": "down"}, 200),
    ({"app": "AIMONITOR", "status": "ok"}, 503),
])
def test_foreign_health_response_is_port_conflict_without_spawn(monkeypatch, body, status):
    mock_transport(monkeypatch, lambda _request: httpx.Response(status, json=body))
    spawn = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", spawn)
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    app = controller()
    app.ensure_backend()
    spawn.assert_not_called()
    assert "포트 충돌" in app.backend_notice()
    assert "backend_url" in app.backend_notice()


def test_old_aimonitor_health_is_reused_without_version_match(monkeypatch):
    mock_transport(monkeypatch, lambda _request: httpx.Response(200, json={
        "app": "AIMONITOR", "status": "ok", "version": "old-version"}))
    spawn = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", spawn)
    app = controller()
    app.ensure_backend()
    spawn.assert_not_called()
    assert app.backend_notice() == ""


def test_watchdog_waits_out_conflict_and_starts_after_listener_disappears(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) <= 2:
            return httpx.Response(200, json={"service": "foreign"})
        raise httpx.ConnectError("synthetic absent listener")

    mock_transport(monkeypatch, respond)
    app = controller()
    app._stop_event = Mock(wait=Mock(side_effect=[False, False, False, False, True]),
                           is_set=Mock(return_value=False))
    app.ensure_backend = Mock()
    app._watch_backend()
    assert len(calls) == 4
    app.ensure_backend.assert_called_once_with()
    assert app.backend_notice() == ""


def test_spawn_wait_rejects_foreign_service_and_reaps_owned_child(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("synthetic absent listener")
        return httpx.Response(200, json={"service": "foreign"})

    mock_transport(monkeypatch, respond)
    child = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    app = controller()
    app.ensure_backend()
    assert "포트 충돌" in app.backend_notice()
    assert child.terminate.called and child.wait.called
    assert app._backend_proc is None


@pytest.mark.parametrize("endpoint,payload", [
    ("overview", {}), ("overview", {"total_requests": None}),
    ("breakdown", {}), ("breakdown", [{}]),
    ("traces", {}), ("traces", [{}]), ("traces", {"items": None}),
    ("active_contexts", []), ("active_contexts", {"openai_api": {}}),
    ("active_contexts", {"openai_api": {"windows": {"today": {}}}}),
    ("spend", {}), ("spend", {"items": [None]}),
    ("spend", {"items": [], "openai_api": {"kind": "spend", "amount": "bad"}}),
])
def test_malformed_endpoint_is_failure_and_keeps_previous_data(monkeypatch, endpoint, payload):
    responses = valid_payloads()
    responses[endpoint] = payload
    ui, _requests = fake_window(monkeypatch, responses)
    old_spend = {"items": [], "openai_api": {"kind": "balance", "amount": 12, "currency": "USD"}}
    ui.official_spend = deepcopy(old_spend)
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    ov, bd, traces, summary, ok, _auth = ui._apply_data.call_args.args
    if endpoint == "active_contexts":
        assert not ok and summary is None
    else:
        assert summary["endpoint_status"][endpoint] is False
    if endpoint == "breakdown":
        assert bd == ui.breakdown_data
    if endpoint == "traces":
        assert traces == ui.traces_data
    if endpoint == "spend":
        assert ui.official_spend["openai_api"]["amount"] == old_spend["openai_api"]["amount"]


def test_genuine_empty_responses_still_report_zero(monkeypatch):
    ui, _requests = fake_window(monkeypatch)
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    _ov, bd, traces, summary, ok, _auth = ui._apply_data.call_args.args
    assert ok and all(summary["endpoint_status"].values())
    assert summary["total"]["requests_today"] == 0
    assert bd == [] and traces == []


def test_current_backend_payloads_with_and_without_records_are_valid(client):
    paths = {name: "/api/v1/metrics/" + name for name in ("overview", "breakdown", "active_contexts")}
    paths.update(traces="/api/v1/traces", spend="/api/v1/keys/spend")
    for with_record in (False, True):
        if with_record:
            response = client.post("/api/v1/traces", json={
                "provider": "openai", "model_name": "synthetic-review-model", "prompt_tokens": 1})
            assert response.status_code == 201
        for endpoint, path in paths.items():
            response = client.get(path)
            assert response.status_code == 200
            assert display.valid_api_payload(endpoint, response.json()), endpoint


def test_active_contexts_accepts_fractional_request_usage():
    usage = {"requests": 0.33, "tokens": 10, "cost_usd": 0, "cost_unknown": 1, "record_count": 1}
    payload = {"copilot": {"windows": {name: usage for name in ("5h", "today", "7d", "month")}}}
    assert display.valid_api_payload("active_contexts", payload)


@pytest.mark.parametrize("endpoint,payload", [
    ("breakdown", [{"provider": "openai", "model_name": "synthetic", "count": 1, "total_tokens": 1,
                    "total_cost_usd": 0, "cost_unknown_count": "bad"}]),
    ("spend", {"items": [], "openai_api": {"kind": "spend", "amount": 1, "currency": ""}}),
    ("spend", {"items": [], "openai_api": {"kind": "spend", "amount": 1, "currency": "USD",
                                          "source_label": []}}),
])
def test_invalid_optional_display_fields_are_rejected(endpoint, payload):
    assert not display.valid_api_payload(endpoint, payload)


def test_spend_failure_marks_previous_amount_and_success_reference_then_recovers(monkeypatch):
    responses = valid_payloads()
    report = {"kind": "balance", "amount": 0.5, "currency": "USD", "as_of": "2026-10-10T10:00:00+00:00",
              "source_label": "공식 잔액"}
    responses["spend"] = {"items": [{**report, "provider": "openai", "key_id": "synthetic"}],
                          "openai_api": report}
    ui, _requests = fake_window(monkeypatch, responses)
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    assert ui._apply_data.call_args.args[3]["endpoint_status"]["spend"] is True
    responses["spend"] = httpx.Response(500)
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    summary = ui._apply_data.call_args.args[3]
    assert summary["endpoint_status"]["spend"] is False
    for cached in (summary["items"][0]["official_spend"], ui.official_spend["items"][0]):
        assert cached["amount"] == 0.5
        text = display.spend_text(cached, 1400)
        assert "갱신 실패 · 이전 값" in text
        assert "마지막 성공" in text and display.format_as_of(report["as_of"]) in text
    ui.ai_card_widgets, ui.connected_ais, ui.krw_rate = {}, [AI], 1400
    ui.rate_updater = SimpleNamespace(describe=lambda: "synthetic rate")
    for name in ("_refresh_account_choices", "_refresh_keys_list", "_rebuild_unified_cards",
                 "_update_native_charts", "_render_breakdown", "_render_filtered_logs", "_schedule_refresh",
                 "lbl_status_msg", "lbl_last_time", "lbl_total_cost_title", "lbl_total_cost_krw",
                 "lbl_total_cost_usd", "lbl_total_requests", "lbl_total_tokens"):
        setattr(ui, name, Mock())
    app_gui.DesktopMainWindow._apply_data(ui, *ui._apply_data.call_args.args)
    assert "일부 갱신 실패" in ui.lbl_status_msg.configure.call_args.kwargs["text"]
    responses["spend"] = {"items": [], "openai_api": {**report, "amount": 0.25}}
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    assert ui._apply_data.call_args.args[3]["endpoint_status"]["spend"] is True
    assert "갱신 실패" not in display.spend_text(ui.official_spend["openai_api"], 1400)


def test_normal_unavailable_spend_is_success_and_unrequested_spend_is_not_failure(monkeypatch):
    responses = valid_payloads()
    responses["spend"] = {"items": [], "openai_api": {"kind": "unavailable", "amount": None,
                                                     "source_label": "공식 사용액 제공 안 함"}}
    ui, requests = fake_window(monkeypatch, responses)
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    assert ui._apply_data.call_args.args[3]["endpoint_status"]["spend"] is True
    assert "갱신 실패" not in display.spend_text(ui.official_spend["openai_api"], 1400)
    requests.clear()
    app_gui.DesktopMainWindow._fetch_worker(ui, [], {}, 1400)
    assert "spend" not in requests
    assert "spend" not in ui._apply_data.call_args.args[3]["endpoint_status"]


def test_known_conflict_skips_queries_and_shows_remedy(monkeypatch):
    ui, requests = fake_window(monkeypatch)
    notice = "포트 충돌 · 다른 프로그램을 종료하거나 desktop_config.json의 backend_url 포트를 바꾸세요."
    ui.connection_notice = lambda: notice
    app_gui.DesktopMainWindow._fetch_worker(ui, [AI], {}, 1400)
    assert requests == []
    ui.lbl_status_msg, ui._schedule_refresh = Mock(), Mock()
    app_gui.DesktopMainWindow._apply_data(ui, *ui._apply_data.call_args.args)
    assert ui.lbl_status_msg.configure.call_args.kwargs["text"] == notice
    ui._schedule_refresh.assert_called_once()


@pytest.mark.parametrize("mode", ["mini", "expanded"])
def test_widget_port_conflict_message_overrides_connection_wait(monkeypatch, mode):
    monkeypatch.setattr(widget.theme, "appearance_mode", lambda: "light")
    monkeypatch.setattr(widget.tk, "Label", lambda *_args, **_kwargs: Mock())
    notice = "포트 충돌 · 다른 프로그램을 종료하거나 desktop_config.json의 backend_url 포트를 바꾸세요."
    ui = SimpleNamespace(connection_notice=lambda: notice, mode=mode, _theme_mode="light", entries=[],
                         _visible=lambda: [], is_connected=False, _resize=Mock(), mini_text=Mock(),
                         lbl_footer=Mock(), lbl_status=Mock(), _rows_sig=None, rows_frame=Mock(), font_normal=(),
                         rate_updater=SimpleNamespace(describe=lambda: "synthetic rate"), _bind_events=Mock())
    ui.rows_frame.winfo_children.return_value = []
    widget.FloatingWidget._render(ui)
    label = ui.mini_text if mode == "mini" else ui.lbl_footer
    assert label.config.call_args.kwargs["text"] == notice
