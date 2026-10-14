from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")
pytest.importorskip("pystray")

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from desktop import app_gui, display, theme, widget
from desktop.context_manager import get_connected_ai_summary


def test_unknown_copilot_account_keeps_calculable_quota():
    usage = {"account_id": None, "windows": {"month": {"requests": 1, "tokens": 30,
             "cost_usd": 1, "cost_unknown": 0}}}
    active = {"copilot": dict(usage, accounts=[usage])}
    quota = [{"id": "month", "remaining_pct": 97.7, "source": "user_limit"}]
    summary = get_connected_ai_summary([{"key": "copilot"}], active,
                                       quota_overrides={"copilot": quota})
    display.attach_accounts(summary, active, [], [], 1400)
    assert summary["items"][0]["quota_windows"] == quota
    assert summary["items"][0]["accounts"][0]["quota_windows"] == quota


def test_unavailable_usage_is_distinct_from_successful_empty_usage():
    args = {"active_ais": [{"key": "review_api"}], "quota_overrides": {"review_api": []}}
    failed = get_connected_ai_summary(**args, usage_available=False)
    empty = get_connected_ai_summary(**args, usage_available=True)
    assert failed["total"]["requests_today"] is None
    assert failed["items"][0]["usage"] == {}
    assert empty["total"]["requests_today"] == 0


def test_widget_cached_values_are_labelled_after_disconnect(monkeypatch):
    monkeypatch.setattr(theme, "appearance_mode", lambda: "light")
    entry = {"group": "Test", "label": "잔여", "value": "67%", "hint": "공식", "detail": ""}
    ui = SimpleNamespace(mini_text=Mock(), _resize=Mock(), mode="mini", _theme_mode="light",
                         entries=[entry], _visible=lambda: [entry], is_connected=False)
    ui.mini_text.cget.return_value = ""
    widget.FloatingWidget._render(ui)
    assert "이전 값" in ui.mini_text.config.call_args.kwargs["text"]


def test_widget_stale_quota_is_visible_in_default_mini_text(monkeypatch):
    monkeypatch.setattr(theme, "appearance_mode", lambda: "light")
    as_of = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    quota = {"id": "5h", "label": "5시간", "remaining_pct": 67, "source": "official",
             "source_label": "공식", "as_of": as_of, "stale": True}
    summary = {"items": [{"key": "claude", "quota_windows": [quota]}]}
    entries = display.widget_entries(summary)
    assert "오래됨" in entries[0]["detail"]  # the formatter knows the value is stale
    ui = SimpleNamespace(root=Mock(), mini_text=Mock(), _resize=Mock(), mode="mini", _theme_mode="light",
                         entries=entries, _visible=lambda: entries, is_connected=True)
    ui.mini_text.cget.return_value = ""
    widget.FloatingWidget._render(ui)
    rendered = ui.mini_text.config.call_args.kwargs["text"]
    assert "오래됨" in rendered or "기준" in rendered, rendered


def test_widget_estimated_context_is_visible_in_mini_text(monkeypatch):
    monkeypatch.setattr(theme, "appearance_mode", lambda: "light")
    summary = {"items": [{"key": "claude", "context": {"tokens": 100, "max_tokens": 200, "used_pct": 50}}]}
    entries = display.widget_entries(summary)
    ui = SimpleNamespace(root=Mock(), mini_text=Mock(), _resize=Mock(), mode="mini", _theme_mode="light",
                         entries=entries, _visible=lambda: entries, is_connected=True)
    ui.mini_text.cget.return_value = ""
    widget.FloatingWidget._render(ui)
    rendered = ui.mini_text.config.call_args.kwargs["text"]
    assert "추정" in rendered, rendered


def test_model_breakdown_unknown_price_is_not_shown_as_zero(monkeypatch):
    labels = []
    monkeypatch.setattr(theme, "font", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(app_gui.ctk, "CTkFrame", lambda *_args, **_kwargs: Mock())
    monkeypatch.setattr(app_gui.ctk, "CTkLabel", lambda *_args, **kwargs: labels.append(kwargs.get("text")) or Mock())
    ui = SimpleNamespace(krw_rate=1400, _breakdown_sig=None, frame_breakdown=Mock())
    ui.frame_breakdown.winfo_children.return_value = []
    rows = [{"provider": "openai", "model_name": "review-unknown-model", "count": 1,
             "total_tokens": 2, "total_cost_usd": 0.0, "cost_unknown_count": 1}]
    app_gui.DesktopMainWindow._render_breakdown(ui, rows)
    assert any("계산 불가" in text for text in labels), labels


def test_chart_does_not_plot_unknown_price_as_zero():
    figure = Figure()
    ui = SimpleNamespace(fig=figure, canvas=FigureCanvasAgg(figure), krw_rate=1400,
                         ax_cost=figure.add_subplot(121), ax_tokens=figure.add_subplot(122), _chart_sig=None)
    ui._style_axis = lambda axis, title: app_gui.DesktopMainWindow._style_axis(ui, axis, title)
    rows = [{"provider": "openai", "model_name": "review-unknown-model", "count": 1,
             "total_tokens": 2, "total_cost_usd": 0.0, "cost_unknown_count": 1}]
    app_gui.DesktopMainWindow._update_native_charts(ui, rows)
    assert not ui.ax_cost.patches or any("계산 불가" in text.get_text() for text in ui.ax_cost.texts)
    rows[0].update(count=2, total_cost_usd=1.0)
    app_gui.DesktopMainWindow._update_native_charts(ui, rows)
    assert ui.ax_cost.patches[0].get_width() == 1400
    assert any("1건 계산 불가" in text.get_text() for text in ui.ax_cost.texts)
    rows[0]["cost_unknown_count"] = 0
    app_gui.DesktopMainWindow._update_native_charts(ui, rows)
    assert not ui.ax_cost.texts


def test_expanded_widget_keeps_freshness_and_estimate_notes(monkeypatch):
    labels = []
    monkeypatch.setattr(theme, "appearance_mode", lambda: "light")
    monkeypatch.setattr(widget.tk, "Frame", lambda *_args, **_kwargs: Mock())
    monkeypatch.setattr(widget.tk, "Label", lambda *_args, **kw: labels.append(kw["text"]) or Mock())
    entry = {"id": "claude:quota", "group": "Claude", "label": "한도", "value": "67%",
             "color": "grey", "reset": "리셋", "hint": "공식", "detail": "공식 · 10:00 기준 (오래됨)"}
    ui = SimpleNamespace(mode="expanded", _theme_mode="light", entries=[entry], _visible=lambda: [entry],
                         is_connected=False, rate_updater=SimpleNamespace(describe=lambda: "환율"),
                         lbl_footer=Mock(), _rows_sig=None, rows_frame=Mock(), font_normal=(), font_bold=(),
                         font_small=(), _bind_events=Mock(), _resize=Mock())
    ui.rows_frame.winfo_children.return_value = []
    widget.FloatingWidget._render(ui)
    assert any("기준 (오래됨)" in text for text in labels)
    assert "이전 값" in ui.lbl_footer.config.call_args.kwargs["text"]


def test_active_context_failure_does_not_publish_zero_usage_as_fresh(monkeypatch):
    received = []
    real_client = httpx.Client
    def respond(request):
        if request.url.path.endswith("active_contexts"):
            return httpx.Response(503)
        return httpx.Response(200, json=[] if request.url.path.endswith(("breakdown", "traces")) else {})
    monkeypatch.setattr(app_gui.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(app_gui.api_key_manager, "list_keys", lambda: [])
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: [])
    monkeypatch.setattr(app_gui, "account_tools", lambda ais, rows: ais)
    monkeypatch.setattr(app_gui, "attach_accounts", lambda *args: None)
    ui = SimpleNamespace(backend_url="http://review.invalid", official_spend={}, account_choices={},
                         after=lambda delay, callback: callback(),
                         _apply_data=lambda *args: received.append(args))
    ais = [{"key": "local_review", "display_name": "Review", "provider": "review", "plan_name": "API"}]
    app_gui.DesktopMainWindow._fetch_worker(ui, ais, {}, 1400)
    overview, breakdown, traces, summary, ok, auth_required = received[0]
    assert not ok or summary is None, f"fresh={ok}, requests={summary['total']['requests_today']}"
