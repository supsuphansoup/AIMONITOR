import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from desktop import main, widget
from desktop.single_instance import SingleInstance


def test_stuck_backend_is_killed_and_reaped():
    proc = Mock()
    proc.wait.side_effect = [main.subprocess.TimeoutExpired("fake", 5), None]
    main.stop_backend(proc)
    proc.terminate.assert_called_once_with()
    proc.kill.assert_called_once_with()
    assert proc.wait.call_count == 2


def test_external_backend_never_starts_a_local_server(monkeypatch):
    controller = main.DesktopApp.__new__(main.DesktopApp)
    controller.backend_url = "https://example.invalid:18771"
    controller._spawn_lock = threading.Lock()
    controller._stop_event = threading.Event()
    spawn = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", spawn)
    controller.ensure_backend()
    spawn.assert_not_called()


def test_loopback_backend_address_parses_port_and_ipv6():
    assert main.local_backend_address("http://localhost:18771") == ("localhost", 18771)
    assert main.local_backend_address("http://[::1]:18771") == ("::1", 18771)
    assert main.local_backend_address("http://example.invalid:18771") is None


def test_tray_callback_is_queued_on_main_thread_and_topmost_check_tracks_widget():
    from desktop.tray import TrayManager

    master = Mock()
    callback = Mock()
    tray = TrayManager(on_open_dashboard=callback)
    tray._master = master
    item = list(tray._build_menu().items)[0]
    thread = threading.Thread(target=lambda: item(None))
    thread.start()
    thread.join(timeout=2)
    callback.assert_not_called()
    master.after.call_args.args[1]()
    callback.assert_called_once_with()
    tray.set_topmost(False)
    assert list(tray._build_menu().items)[2].checked is not None
    assert tray.is_topmost is False


def test_shutdown_prevents_backend_spawn_after_shutdown_started(monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    controller = main.DesktopApp.__new__(main.DesktopApp)
    controller._backend_lock = threading.RLock()
    controller._spawn_lock = threading.Lock()
    controller.__dict__.update(backend_url="http://127.0.0.1:18771", _backend_proc=None, _stop_event=threading.Event(),
                               instance=Mock(), ai_watcher=None, main_window=None, tray_manager=None)
    proc = Mock()
    def spawn(*_args, **_kwargs):
        entered.set()
        assert resume.wait(2)
        return proc
    monkeypatch.setattr(main.subprocess, "Popen", spawn)
    monkeypatch.setattr(main, "backend_headers", lambda: {})
    monkeypatch.setattr(main.time, "sleep", lambda _delay: None)
    real_client = httpx.Client
    monkeypatch.setattr(main.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
    thread = threading.Thread(target=controller.ensure_backend)
    thread.start()
    assert entered.wait(2)
    controller.shutdown()
    resume.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert proc.terminate.called, "a new backend was assigned after shutdown and never terminated"


def test_startup_failure_cleans_up_owned_backend(monkeypatch):
    controller = main.DesktopApp.__new__(main.DesktopApp)
    controller._backend_lock = threading.RLock()
    controller._spawn_lock = threading.Lock()
    proc = Mock()
    controller.__dict__.update(instance=Mock(acquire=Mock(return_value=True)), ensure_backend=Mock(),
                               _backend_proc=proc, config={"claude_watch_enabled": False},
                               _stop_event=threading.Event(), main_window=None, ai_watcher=None, tray_manager=None)
    monkeypatch.setattr(main.threading, "Thread", Mock())
    monkeypatch.setattr(main, "DesktopMainWindow", Mock(side_effect=RuntimeError("synthetic GUI init failure")))
    with pytest.raises(RuntimeError, match="synthetic GUI init failure"):
        controller.run()
    assert proc.terminate.called and controller.instance.release.called


def test_unrelated_port_listener_is_not_treated_as_running_aimonitor(monkeypatch):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 18771))
        listener.listen(1)
        second = SingleInstance(port=18771)
        assert not second.acquire()
        assert second.notify_running() is False, "TCP connect/send succeeds without an AIMONITOR acknowledgement"


def test_tray_topmost_request_sets_explicit_state(monkeypatch):
    top = []
    floating = SimpleNamespace(config={"always_on_top": False}, root=SimpleNamespace(
        attributes=lambda name, state: top.append(state)))
    floating._save = lambda **values: floating.config.update(values)
    floating.set_always_on_top = lambda value: widget.FloatingWidget.set_always_on_top(floating, value)
    floating.toggle_always_on_top = lambda: widget.FloatingWidget.toggle_always_on_top(floating)
    controller = SimpleNamespace(widget=floating)
    # A tray that still thinks the old value is True asks for False after the widget menu already disabled it.
    main.DesktopApp._toggle_topmost(controller, False)
    assert top == [False] and floating.config["always_on_top"] is False, top


def test_spawned_backend_uses_configured_loopback_port(monkeypatch):
    controller = main.DesktopApp.__new__(main.DesktopApp)
    controller._backend_lock = threading.RLock()
    controller._spawn_lock = threading.Lock()
    controller.__dict__.update(backend_url="http://127.0.0.1:18771", _backend_proc=None,
                               _stop_event=threading.Event())
    real_client = httpx.Client
    monkeypatch.setattr(main.httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
    monkeypatch.setattr(main, "backend_headers", lambda: {})
    monkeypatch.setattr(main.time, "sleep", lambda delay: None)
    spawn = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", spawn)
    controller.ensure_backend()
    command = spawn.call_args.args[0]
    assert "18771" in command, command
