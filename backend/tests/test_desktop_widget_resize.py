"""Widget width: dragging the edges, per-mode saved widths and '…' on a narrow one-line widget."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("customtkinter")

from desktop import widget


class Label:
    def __init__(self, text=""):
        self.text = text

    def cget(self, _key):
        return self.text

    def config(self, **kwargs):
        self.text = kwargs.get("text", self.text)


def measure(text):
    return 10 * len(text)


def make(monkeypatch, mode="mini", **config):
    monkeypatch.setattr(widget, "monitor_work_areas", lambda root: [(0, 0, 1920, 1040)])
    ui = widget.FloatingWidget.__new__(widget.FloatingWidget)
    ui.mode = mode
    ui.config = dict(config)
    ui.root = Mock()
    ui.root.winfo_screenwidth.return_value = 1920
    ui.root.winfo_x.return_value = 1000
    ui.root.winfo_y.return_value = 60
    ui.root.winfo_width.return_value = 600
    ui.font_bold = SimpleNamespace(measure=measure)
    ui.mini_text = Label()
    ui._visible = lambda: ["a", "b", "c"]
    ui._resize_drag = ui._live_width = ui._live_position = None
    ui._mini_full = ui._mini_shown = None
    ui._row_notes = []
    ui._save = Mock(side_effect=lambda **changes: ui.config.update(changes) or True)
    return ui


def geometry(ui):
    return ui.root.geometry.call_args.args[0]


def test_fit_text_keeps_short_text_and_ends_long_text_with_ellipsis():
    assert widget.fit_text("abc", measure, 30) == "abc"
    out = widget.fit_text("Claude 5시간 58% 남음", measure, 100)
    assert out.endswith("…") and measure(out) <= 100 and "Claude 5시간 58% 남음".startswith(out[:-1])
    assert widget.fit_text("abcdef", measure, 5) == "…"


def test_saved_width_per_mode_overrides_content_fit_within_bounds(monkeypatch):
    ui = make(monkeypatch)
    ui.mini_text.text = "x" * 300
    assert ui._size() == (1880, 36)  # content fit, capped by the screen
    ui.config["widget_width_mini"] = 640
    assert ui._size() == (640, 36)
    ui.config["widget_width_mini"] = 120
    assert ui._size() == (widget.MIN_WIDTH["mini"], 36)
    expanded = make(monkeypatch, "expanded", widget_width_mini=640, widget_width_expanded=520)
    assert expanded._size() == (520, 80 + 3 * widget.ROW_HEIGHT)
    assert make(monkeypatch, "expanded", widget_width_mini=640)._custom_width() is None


def test_narrow_one_line_widget_shortens_text_and_widening_restores_it(monkeypatch):
    ui = make(monkeypatch, widget_width_mini=400)
    full = "A" * 100
    ui.mini_text.text = full  # what _render puts on the label
    ui._resize()
    assert geometry(ui) == "400x36+1000+60"
    assert ui.mini_text.text == "A" * 31 + "…" and measure(ui.mini_text.text) <= 400 - widget.MINI_CHROME
    ui._resize()  # a later refresh does not mistake the shortened text for new content
    assert ui._mini_full == full
    ui.config["widget_width_mini"] = None
    ui._resize()
    assert ui.mini_text.text == full and geometry(ui) == "1080x36+840+60"  # kept on the monitor


def test_dragging_left_edge_keeps_right_edge_and_saves_only_this_mode(monkeypatch):
    ui = make(monkeypatch)
    ui.mini_text.text = "A" * 100
    ui._resize()
    ui._on_resize_start(SimpleNamespace(x_root=1000), "left")
    ui._on_resize_motion(SimpleNamespace(x_root=800))
    assert geometry(ui) == "800x36+800+60"  # right edge stays at 1600
    ui._on_resize_release(SimpleNamespace(x_root=800))
    ui._save.assert_called_once_with(widget_width_mini=800, widget_x=800, widget_y=60)
    assert "widget_width_expanded" not in ui.config and ui._live_width is None


def test_dragging_right_edge_is_bounded_by_minimum_and_monitor(monkeypatch):
    ui = make(monkeypatch)
    ui._on_resize_start(SimpleNamespace(x_root=1600), "right")
    ui._on_resize_motion(SimpleNamespace(x_root=900))
    assert geometry(ui) == f"{widget.MIN_WIDTH['mini']}x36+1000+60"
    ui._on_resize_motion(SimpleNamespace(x_root=4000))
    assert geometry(ui) == "1920x36+0+60"
    ui._on_resize_release(SimpleNamespace(x_root=4000))
    assert ui.config["widget_width_mini"] == 1920


def test_reset_width_returns_to_content_fit(monkeypatch):
    ui = make(monkeypatch, widget_width_mini=400)
    ui.mini_text.text = "A" * 10
    ui.reset_width()
    ui._save.assert_called_once_with(widget_width_mini=None)
    assert geometry(ui) == "260x36+1000+60"


def test_narrow_card_rows_shorten_notes_but_keep_name_and_value(monkeypatch):
    ui = make(monkeypatch, "expanded", widget_width_expanded=300)
    ui.font_normal = ui.font_small = SimpleNamespace(measure=measure)
    full = "Anthropic 공식 · 13:07 기준 · 리셋 15:14"
    note = Label(full)
    ui._row_notes = [(note, full, "Claude · 5시간", "58% 남음")]
    ui._resize()
    room = 300 - 2 * 12 - 2 - measure("Claude · 5시간") - 12 - measure("58% 남음") - 8 - 3 * widget.LABEL_CHROME
    assert note.text.endswith("…") and measure(note.text) <= room
    ui.config["widget_width_expanded"] = 1200
    ui._resize()
    assert note.text == full


def test_switching_back_to_one_line_mode_skips_the_destroyed_card_footer(monkeypatch):
    monkeypatch.setattr(widget.theme, "appearance_mode", lambda: "light")
    stale_footer = Mock()
    stale_footer.config.side_effect = widget.tk.TclError('invalid command name ".!toplevel.!frame.!label"')
    ui = SimpleNamespace(mode="expanded", _theme_mode="light", entries=[], _visible=lambda: [],
                         is_connected=True, mini_text=Label(), lbl_footer=stale_footer, lbl_status=Mock(),
                         _save=Mock(), _build_ui=Mock(), _bind_events=Mock(), _resize=Mock(),
                         rate_updater=SimpleNamespace(describe=lambda: "synthetic rate"))
    ui._render = lambda: widget.FloatingWidget._render(ui)
    widget.FloatingWidget.toggle_mode(ui)  # raised TclError before, which also stopped polling
    stale_footer.config.assert_not_called()
    assert ui.mini_text.text == "표시할 항목 없음"
    ui._resize.assert_called_once()
