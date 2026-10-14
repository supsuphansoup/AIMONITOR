"""Startup registration and launch behavior without changing the real registry."""

import builtins
import runpy
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop import autostart
from desktop.single_instance import SingleInstance

ROOT = Path(__file__).resolve().parents[2]


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.accesses = []
        self.exists = False

    @contextmanager
    def OpenKey(self, hive, path, reserved, access):
        self.accesses.append((hive, path, reserved, access))
        if not self.exists:
            raise FileNotFoundError
        yield self

    @contextmanager
    def CreateKeyEx(self, hive, path, reserved, access):
        self.accesses.append((hive, path, reserved, access))
        self.exists = True
        yield self

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]


@pytest.fixture
def registry(monkeypatch):
    fake = FakeRegistry()
    # Replace winreg before any production function is called, even on Windows.
    monkeypatch.setattr(autostart, "winreg", fake)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    return fake


def test_enable_read_disable_preserves_other_run_values(registry):
    assert autostart.get_status() == autostart.DISABLED
    assert autostart.disable()
    assert autostart.enable()
    assert registry.values["AIMONITOR"] == (autostart.command(), registry.REG_SZ)
    registry.values["OtherApp"] = ("leave-this-alone", registry.REG_SZ)
    assert autostart.get_status() == autostart.ENABLED
    assert autostart.disable()
    assert autostart.get_status() == autostart.DISABLED
    assert registry.values == {"OtherApp": ("leave-this-alone", registry.REG_SZ)}
    assert autostart.disable()
    assert all(hive == "HKCU" and path == autostart.RUN_KEY and reserved == 0
               for hive, path, reserved, _access in registry.accesses)


@pytest.mark.parametrize("registered", ['"C:\\old checkout\\run_desktop.pyw"', '"another python.exe"', ""])
def test_other_location_is_replaced_with_current_command(registry, registered):
    registry.exists = True
    registry.values["AIMONITOR"] = (registered, registry.REG_SZ)
    assert autostart.get_status() == autostart.OTHER_LOCATION
    autostart.enable()
    assert autostart.get_status() == autostart.ENABLED


def test_registry_changes_are_read_fresh_and_case_is_ignored(registry):
    autostart.enable()
    registry.values["AIMONITOR"] = (autostart.command().upper(), registry.REG_SZ)
    assert autostart.get_status() == autostart.ENABLED
    registry.values["AIMONITOR"] = ("old location", registry.REG_SZ)
    assert autostart.get_status() == autostart.OTHER_LOCATION
    del registry.values["AIMONITOR"]
    assert autostart.get_status() == autostart.DISABLED


def test_non_windows_never_accesses_registry(registry, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert autostart.get_status() == autostart.UNSUPPORTED
    assert autostart.enable() is False
    assert autostart.disable() is False
    assert registry.accesses == []
    assert registry.values == {}


@pytest.mark.parametrize("pythonw_exists", [True, False])
def test_command_quotes_paths_and_selects_pythonw(tmp_path, monkeypatch, pythonw_exists):
    python = tmp_path / "Python with spaces" / "python.exe"
    python.parent.mkdir()
    python.touch()
    pythonw = python.with_name("pythonw.exe")
    if pythonw_exists:
        pythonw.touch()
    monkeypatch.setattr(autostart.sys, "executable", str(python))
    expected = pythonw if pythonw_exists else python
    assert autostart.command() == f'"{expected}" "{ROOT / "run_desktop.pyw"}" --autostart'
    monkeypatch.setattr(autostart.sys, "executable", str(pythonw))
    assert autostart.command().startswith(f'"{pythonw}" ')


def test_permission_errors_are_not_misreported_as_disabled(registry, monkeypatch):
    monkeypatch.setattr(registry, "OpenKey", Mock(side_effect=PermissionError("private error")))
    monkeypatch.setattr(registry, "CreateKeyEx", Mock(side_effect=PermissionError("private error")))
    for action in (autostart.get_status, autostart.enable, autostart.disable):
        with pytest.raises(PermissionError):
            action()


@pytest.mark.parametrize("launcher", ["run_desktop.py", "run_desktop.pyw"])
@pytest.mark.parametrize("automatic", [False, True])
def test_launchers_fix_cwd_before_import_and_resolve_relative_db(tmp_path, monkeypatch, launcher, automatic):
    # Use a copied launcher and synthetic .env/DB, never the running app's files.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    script = checkout / launcher
    script.write_bytes((ROOT / launcher).read_bytes())
    (checkout / ".env").write_text("DATABASE_URL=sqlite:///./startup-test.db", encoding="utf-8")
    (checkout / "startup-test.db").write_text("expected database", encoding="utf-8")
    elsewhere = tmp_path / "System32"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", [str(script)] + (["--autostart"] if automatic else []))
    original_import = builtins.__import__
    attach, launch = Mock(), Mock()
    imports = []

    def import_desktop(name, *args, **kwargs):
        if name in ("desktop.config", "desktop.main"):
            imports.append(name)
            assert Path.cwd() == checkout
            db_url = (Path.cwd() / ".env").read_text(encoding="utf-8").split("=", 1)[1]
            db_path = Path(db_url.removeprefix("sqlite:///"))
            assert db_path.resolve() == checkout / "startup-test.db"
            assert db_path.read_text(encoding="utf-8") == "expected database"
            return SimpleNamespace(attach_to_default_desktop=attach, main=launch)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_desktop)
    runpy.run_path(str(script), run_name="__main__")
    assert imports == ["desktop.config", "desktop.main"]
    attach.assert_called_once_with()
    launch.assert_called_once_with(autostart=automatic)


def test_real_instance_lock_rejects_second_launch_without_using_live_port():
    first = SingleInstance(port=0)
    try:
        assert first.acquire()
        second = SingleInstance(port=first._sock.getsockname()[1])
        try:
            assert second.acquire() is False
            assert second._sock is None
        finally:
            second.release()
    finally:
        first.release()


@pytest.fixture
def gui_modules():
    pytest.importorskip("customtkinter")
    pytest.importorskip("matplotlib")
    pytest.importorskip("pystray")
    from desktop import app_gui, main

    return app_gui, main


@pytest.mark.parametrize("automatic", [False, True])
def test_duplicate_launch_never_starts_services_and_autostart_stays_silent(gui_modules, automatic):
    _gui, main = gui_modules
    app = main.DesktopApp.__new__(main.DesktopApp)
    app.instance = Mock()
    app.instance.acquire.return_value = False
    app.ensure_backend = Mock()
    app.run(autostart=automatic)
    app.ensure_backend.assert_not_called()
    app.instance.notify_running.assert_called_once_with(show=not automatic)


@pytest.mark.parametrize("automatic", [False, True])
def test_autostart_controller_keeps_widget_tray_and_hides_main(gui_modules, monkeypatch, automatic):
    _gui, main = gui_modules
    monkeypatch.setattr(main.threading, "Thread", Mock())
    window, widget, tray = Mock(), Mock(), Mock()
    window.winfo_screenwidth.return_value = 1920
    window.winfo_screenheight.return_value = 1080
    constructor = Mock(return_value=window)
    monkeypatch.setattr(main, "DesktopMainWindow", constructor)
    monkeypatch.setattr(main, "FloatingWidget", Mock(return_value=widget))
    monkeypatch.setattr(main, "TrayManager", Mock(return_value=tray))
    watcher = Mock()
    monkeypatch.setattr(main, "UnifiedAIWatcher", watcher)
    app = main.DesktopApp.__new__(main.DesktopApp)
    app.instance = Mock()
    app.instance.acquire.return_value = True
    app.config = {"claude_watch_enabled": False}
    app.backend_url = "http://127.0.0.1:18771"
    app._backend_lock = threading.RLock()
    app._stop_event = threading.Event()
    app._backend_proc = None
    app.ai_watcher = None
    app.ensure_backend = Mock()
    app.run(autostart=automatic)
    assert constructor.call_args.kwargs["start_hidden"] is automatic
    widget.show.assert_called_once_with()
    tray.start.assert_called_once_with(window)
    window.mainloop.assert_called_once_with()
    watcher.assert_not_called()
    window.destroy.assert_called_once_with()
    widget.close.assert_called_once_with()
    tray.stop.assert_called_once_with(notify=False)


def startup_ui(gui):
    ui = SimpleNamespace(autostart_var=Mock(), autostart_switch=Mock(),
                         lbl_autostart_status=Mock(), lbl_status_msg=Mock())
    ui._refresh_autostart_status = lambda: gui.DesktopMainWindow._refresh_autostart_status(ui)
    return ui


@pytest.mark.parametrize("state", [autostart.ENABLED, autostart.DISABLED, autostart.OTHER_LOCATION, autostart.UNSUPPORTED])
def test_settings_display_current_registry_state(gui_modules, monkeypatch, state):
    gui, _main = gui_modules
    monkeypatch.setattr(autostart, "get_status", lambda: state)
    ui = startup_ui(gui)
    ui._refresh_autostart_status()
    ui.autostart_var.set.assert_called_once_with(state == autostart.ENABLED)
    ui.autostart_switch.configure.assert_called_once_with(state="disabled" if state == autostart.UNSUPPORTED else "normal")
    if state == autostart.OTHER_LOCATION:
        assert "다른 위치를 가리킴" in ui.lbl_autostart_status.configure.call_args.kwargs["text"]


@pytest.mark.parametrize("enabled", [False, True])
def test_settings_toggle_reads_registry_after_write(gui_modules, registry, enabled):
    gui, _main = gui_modules
    autostart.enable()
    ui = startup_ui(gui)
    ui.autostart_var.get.return_value = enabled
    gui.DesktopMainWindow._toggle_autostart(ui)
    assert autostart.get_status() == (autostart.ENABLED if enabled else autostart.DISABLED)
    ui.autostart_var.set.assert_called_once_with(enabled)


@pytest.mark.parametrize("action", ["read", "write"])
def test_settings_errors_revert_toggle_and_do_not_show_exception(gui_modules, registry, monkeypatch, action):
    gui, _main = gui_modules
    ui = startup_ui(gui)
    ui.autostart_var.get.return_value = True
    if action == "read":
        monkeypatch.setattr(autostart, "get_status", Mock(side_effect=PermissionError("sensitive-private-path")))
        ui._refresh_autostart_status()
        ui.autostart_switch.configure.assert_called_once_with(state="disabled")
    else:
        monkeypatch.setattr(autostart, "enable", Mock(side_effect=PermissionError("sensitive-private-path")))
        gui.DesktopMainWindow._toggle_autostart(ui)
    ui.autostart_var.set.assert_called_once_with(False)
    for label in (ui.lbl_autostart_status, ui.lbl_status_msg):
        text = label.configure.call_args.kwargs["text"]
        assert "sensitive-private-path" not in text
        assert "실패" in text or "못했습니다" in text
