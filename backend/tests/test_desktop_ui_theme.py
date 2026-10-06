"""Desktop display semantics and callbacks, without running the application."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")
pytest.importorskip("pystray")

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from desktop import app_gui, display, theme, widget
from desktop.tray import TrayManager


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_display_preserves_unknown_values_money_and_reset(monkeypatch, mode):
    monkeypatch.setattr(theme, "appearance_mode", lambda: mode)
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    headline, color, detail = display.window_text({"remaining_pct": None, "source": "unavailable",
        "source_label": "공식 값 없음", "as_of": None, "resets_at": None, "note": None})
    assert (headline, color, detail) == ("확인 불가", theme.color(theme.FAINT), "공식 값 없음")
    assert display.usage_cost_text({"cost_krw": 1200, "cost_unknown": 2}, "api_equivalent") == \
        "￦1,200 (API 환산) · 2건 계산 불가"
    assert display.usage_cost_text({}, "free") == "￦0 (로컬)"
    assert display.spend_text({"kind": "balance", "amount": 2, "currency": "USD",
        "source_label": "공식"}, 1400) == "공식: 잔액 ￦2,800 ($2.00)"
    assert display.spend_text({"kind": "unavailable", "amount": None}, 1400) is None
    assert display.format_reset(None, now) == ""
    assert "2시간 45분 뒤" in display.format_reset((now + timedelta(hours=2, minutes=45)).isoformat(), now)
    assert display.format_reset(now.isoformat(), now) == "곧 리셋"


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("remaining,token", [(None, theme.FAINT), (50, theme.SUCCESS),
    (25, theme.ACCENT), (10, theme.WARNING), (0, theme.ERROR)])
def test_remaining_thresholds_use_current_theme(monkeypatch, mode, remaining, token):
    monkeypatch.setattr(theme, "appearance_mode", lambda: mode)
    assert display.remaining_color(remaining) == theme.color(token, mode)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_chart_values_theme_and_tick_legibility_at_minimum_width(monkeypatch, mode):
    current_mode = [mode]
    monkeypatch.setattr(theme, "appearance_mode", lambda: current_mode[0])
    fig = Figure(figsize=(8.84, 2.7), dpi=100)
    canvas = FigureCanvasAgg(fig)
    ui = SimpleNamespace(fig=fig, ax_cost=fig.add_subplot(121), ax_tokens=fig.add_subplot(122),
        krw_rate=1400, _chart_sig=None, canvas=canvas)
    ui._style_axis = lambda ax, title: app_gui.DesktopMainWindow._style_axis(ui, ax, title)
    rows = [{"model_name": "b", "total_cost_usd": 0.14, "total_tokens": 34500},
            {"model_name": "a", "total_cost_usd": 550.37, "total_tokens": 863800000}]
    app_gui.DesktopMainWindow._update_native_charts(ui, rows)
    canvas.draw()
    assert [bar.get_width() for bar in ui.ax_cost.patches] == [round(550.37 * 1400), round(0.14 * 1400)]
    assert [bar.get_width() for bar in ui.ax_tokens.patches] == [863800000, 34500]
    assert [label.get_text() for label in ui.ax_cost.get_yticklabels()] == ["a", "b"]
    boxes = [label.get_window_extent(canvas.get_renderer()) for label in ui.ax_cost.get_xticklabels()]
    assert all(left.x1 < right.x0 for left, right in zip(boxes, boxes[1:], strict=False))
    first_sig = ui._chart_sig
    current_mode[0] = "dark" if mode == "light" else "light"
    app_gui.DesktopMainWindow._update_native_charts(ui, rows)
    assert ui._chart_sig != first_sig
    assert to_hex(fig.get_facecolor()) == theme.color(theme.CARD)
    assert to_hex(ui.ax_cost.title.get_color()) == theme.color(theme.TEXT)
    assert all(to_hex(bar.get_facecolor()) == theme.color(theme.ACCENT) for bar in ui.ax_cost.patches)
    app_gui.DesktopMainWindow._update_native_charts(ui, [])
    assert not ui.ax_cost.patches
    assert ui.ax_cost.texts[0].get_text() == "데이터 수집 대기 중"


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("connected", [True, False])
def test_empty_widget_switches_to_mini_size(monkeypatch, mode, connected):
    monkeypatch.setattr(theme, "appearance_mode", lambda: mode)
    ui = SimpleNamespace(mode="expanded", entries=[], is_connected=connected, _theme_mode=mode,
        mini_text=Mock(), _save=Mock(), _build_ui=Mock(), _bind_events=Mock())
    ui.mini_text.cget.return_value = "표시할 항목 없음"
    ui.font_bold = SimpleNamespace(measure=lambda text: len(text) * 12)
    ui.root = SimpleNamespace(winfo_screenwidth=lambda: 1920, winfo_x=lambda: 100,
                             winfo_y=lambda: 100, geometry=Mock())
    ui._size = lambda: widget.FloatingWidget._size(ui)
    ui._resize = lambda: widget.FloatingWidget._resize(ui)
    ui._visible = lambda: []
    ui._render = lambda: widget.FloatingWidget._render(ui)
    widget.FloatingWidget.toggle_mode(ui)
    assert ui.mode == "mini"
    ui._save.assert_called_once_with(widget_mode="mini")
    assert ui.root.geometry.call_args.args[0] == "260x36+100+100"
    ui.mini_text.config.assert_called_once_with(text="표시할 항목 없음" if connected else "연결 대기 중…")


def test_widget_entries_preserve_selection_and_nullable_context(monkeypatch):
    monkeypatch.setattr(theme, "appearance_mode", lambda: "dark")
    entries = display.widget_entries({"items": [{"key": "claude", "context": None,
        "quota_windows": [{"id": "5h", "label": "5시간", "remaining_pct": None,
            "source": "unavailable", "resets_at": None}], "usage": {"today": None,
        "month": {"cost_krw": 1200, "cost_unknown": 2}}, "cost_meaning": "api_equivalent"}]})
    assert display.select_entries(entries, None)[0]["value"] == "확인 불가"
    assert display.select_entries(entries, []) == []
    chosen = display.select_entries(entries, ["claude:month"])
    assert chosen[0]["value"] == "￦1,200 (API 환산) · 2건 계산 불가"
    assert chosen[0]["color"] == theme.color(theme.TEXT)


def test_api_key_save_delete_and_visibility_callbacks(monkeypatch):
    manager = Mock()
    manager.verify_key.return_value = {"valid": True, "balance": "정상"}
    monkeypatch.setattr(app_gui, "api_key_manager", manager)
    ui = SimpleNamespace(byok_opt_prov=SimpleNamespace(get=lambda: "OpenAI"),
        byok_entry_key=Mock(), lbl_status_msg=Mock(), _refresh_keys_list=Mock(),
        _refresh_diagnostic_table=Mock(), refresh_data=Mock(), byok_show_var=Mock(), btn_toggle_eye=Mock())
    ui.byok_entry_key.get.return_value = "  test-only-value  "
    app_gui.DesktopMainWindow._save_api_key_dedicated(ui)
    manager.set_key.assert_called_once_with("openai", "test-only-value")
    manager.verify_key.assert_called_once_with("openai")
    ui.byok_entry_key.delete.assert_called_once_with(0, "end")
    app_gui.DesktopMainWindow._delete_key_action(ui, "openai")
    manager.delete_key.assert_called_once_with("openai")
    assert ui.refresh_data.call_count == 2
    ui.byok_show_var.get.return_value = False
    app_gui.DesktopMainWindow._toggle_key_visibility(ui)
    ui.byok_entry_key.configure.assert_called_with(show="")


@pytest.mark.parametrize("value,expected", [("300", 300.0), ("", None)])
def test_settings_save_keeps_widget_preferences(monkeypatch, value, expected):
    monkeypatch.setattr(app_gui, "load_config", lambda: {"widget_opacity": 0.7})
    save = Mock()
    monkeypatch.setattr(app_gui, "save_config", save)
    ui = SimpleNamespace(entry_copilot_limit=SimpleNamespace(get=lambda: value),
                         lbl_status_msg=Mock(), refresh_data=Mock())
    app_gui.DesktopMainWindow._save_copilot_limit(ui)
    save.assert_called_once_with({"widget_opacity": 0.7, "copilot_monthly_premium_limit": expected})
    ui.refresh_data.assert_called_once()


def test_tray_menu_dispatches_all_actions_without_starting_icon():
    opened, toggled, topmost, exited = Mock(), Mock(), Mock(), Mock()
    tray = TrayManager(opened, toggled, topmost, exited)
    actions = list(tray._build_menu().items)
    actions[0](None)
    actions[1](None)
    actions[2](None)
    actions[-1](None)
    opened.assert_called_once_with()
    toggled.assert_called_once_with()
    topmost.assert_called_once_with(False)
    exited.assert_called_once_with()
