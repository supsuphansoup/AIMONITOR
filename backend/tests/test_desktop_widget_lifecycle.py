import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("customtkinter")
pytest.importorskip("matplotlib")
pytest.importorskip("pystray")

from desktop import app_gui, widget


class Timers:
    def __init__(self):
        self.jobs = {}
        self.serial = 0

    def after(self, delay, callback):
        self.serial += 1
        self.jobs[self.serial] = (delay, callback)
        return self.serial

    def after_cancel(self, job):
        self.jobs.pop(job, None)


def test_widget_refresh_and_close_keep_at_most_one_poll_timer():
    ui = widget.FloatingWidget.__new__(widget.FloatingWidget)
    ui.root = Timers()
    ui.root.destroy = Mock()
    ui._closed = False
    ui._poll_job = None
    ui.poll_ms = 3000
    ui._render = Mock()
    ui._resize = Mock()
    for _ in range(3):
        ui._on_data({}, False)
    assert len(ui.root.jobs) == 1
    ui.close()
    assert ui.root.jobs == {}
    ui._on_data({}, False)
    assert ui.root.jobs == {}


def test_widget_placement_clamps_entire_rectangle_to_nearest_monitor(monkeypatch):
    ui = widget.FloatingWidget.__new__(widget.FloatingWidget)
    ui.root = Mock()
    ui._size = lambda: (460, 116)
    ui.config = {"widget_x": 100, "widget_y": 2400}
    monkeypatch.setattr(widget, "monitor_work_areas", lambda root: [(0, 0, 1920, 1040)])
    ui._place()
    assert ui.root.geometry.call_args.args[0] == "460x116+100+924"
    monkeypatch.setattr(widget, "monitor_work_areas", lambda root: [(-1920, 0, 0, 1040), (0, 0, 1920, 1040)])
    ui.config.update(widget_x=-1000, widget_y=60)
    ui._place()
    assert ui.root.geometry.call_args.args[0] == "460x116+-1000+60"


def test_widget_resize_fits_small_work_area_after_dpi_change(monkeypatch):
    ui = widget.FloatingWidget.__new__(widget.FloatingWidget)
    ui.root = Mock()
    ui.root.winfo_x.return_value = -1000
    ui.root.winfo_y.return_value = 2000
    ui._size = lambda: (1200, 1000)
    monkeypatch.setattr(widget, "monitor_work_areas", lambda root: [(0, 0, 800, 600)])
    ui._resize()
    assert ui.root.geometry.call_args.args[0] == "800x600+0+0"


def test_manual_rate_refresh_runs_in_worker_and_disables_button():
    entered, resume = threading.Event(), threading.Event()
    callback_threads = []
    def update():
        callback_threads.append(threading.current_thread())
        entered.set()
        assert resume.wait(2)
        return 1400
    ui = app_gui.DesktopMainWindow.__new__(app_gui.DesktopMainWindow)
    ui.rate_updater = SimpleNamespace(update_now=update, describe=lambda: "1400")
    ui._rate_refreshing = False
    ui.lbl_status_msg = Mock()
    ui.btn_rate_refresh = Mock()
    ui.refresh_data = Mock()
    callbacks = []
    ui.after = lambda delay, callback: callbacks.append(callback)
    try:
        ui._manual_refresh_rate()
        assert entered.wait(1)
        assert callback_threads[0] is not threading.current_thread()
        assert ui.btn_rate_refresh.configure.call_args.kwargs["state"] == "disabled"
        ui._manual_refresh_rate()
        assert len(callback_threads) == 1
    finally:
        resume.set()
        ui._rate_refresh_thread.join(timeout=3)
    callbacks[0]()
    assert ui.btn_rate_refresh.configure.call_args.kwargs["state"] == "normal"
