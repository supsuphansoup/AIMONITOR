"""AIMONITOR Native Windows Desktop Application Controller.

Unified Desktop App:
- Launches the 100% Native Desktop Window (CustomTkinter)
- Floating HUD Widget stays always-on-top so the user can check 3 AIs at a glance anytime
- Silently manages background local backend & Unified AI auto-watcher (Claude, Antigravity, GPT)
- System Tray icon for background access
"""

import ipaddress
import logging
import os
import subprocess
import sys
import threading
import time
from typing import List, Optional
from urllib.parse import urlsplit

import httpx

from desktop.ai_watcher import UnifiedAIWatcher
from desktop.app_gui import DesktopMainWindow
from desktop.config import attach_to_default_desktop, backend_headers, load_config
from desktop.single_instance import SingleInstance
from desktop.tray import TrayManager
from desktop.widget import FloatingWidget

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
for _http_logger in ("httpx", "httpcore"):
    logging.getLogger(_http_logger).setLevel(logging.WARNING)
logger = logging.getLogger("aimonitor.desktop")
PORT_CONFLICT_MESSAGE = "포트 충돌 · 다른 프로그램을 종료하거나 설정의 서버 주소를 변경하세요."


def backend_health(url: str, timeout: float = 1.5) -> str:
    """Distinguish an AIMONITOR health response, another HTTP service, and no response."""
    try:
        with httpx.Client(timeout=timeout, headers=backend_headers()) as client:
            response = client.get(f"{url}/health")
    except Exception:
        return "unavailable"
    try:
        payload = response.json()
    except ValueError:
        return "conflict"
    if (response.status_code == 200 and isinstance(payload, dict)
            and payload.get("app") == "AIMONITOR" and payload.get("status") == "ok"):
        return "ready"
    return "conflict"


def backend_command(port: int = 8000, host: str = "127.0.0.1") -> List[str]:
    """Command that serves the local API. A packaged build serves it from its own exe."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve-backend", "--port", str(port), "--host", host]
    return [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", host, "--port", str(port),
            "--no-access-log"]


def local_backend_address(url: str):
    try:
        parts = urlsplit(url)
        host = parts.hostname
        loopback = host == "localhost" or (host is not None and ipaddress.ip_address(host).is_loopback)
        if parts.scheme == "http" and loopback and not parts.username and parts.path in ("", "/"):
            return host, parts.port or 80
    except ValueError:
        pass
    return None


def startup_error(message: str) -> None:
    logger.error(message)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "AIMONITOR", 0x10)


def stop_backend(proc: subprocess.Popen) -> None:
    """Reap only the child this controller created, including a stuck shutdown."""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    except OSError:
        logger.warning("Could not stop the owned backend")


class DesktopApp:
    """Desktop Application Controller."""

    def __init__(self):
        attach_to_default_desktop()
        self.config = load_config()
        self.backend_url = self.config.get("backend_url", "http://127.0.0.1:8000")
        self._backend_proc: Optional[subprocess.Popen] = None
        self.ai_watcher: Optional[UnifiedAIWatcher] = None
        self.tray_manager: Optional[TrayManager] = None
        self.main_window: Optional[DesktopMainWindow] = None
        self.widget: Optional[FloatingWidget] = None
        self._stop_event = threading.Event()
        self._backend_lock = threading.RLock()
        self._spawn_lock = threading.Lock()
        self._shutdown_started = False
        self._backend_status = "unavailable"
        self.instance = SingleInstance()

    def backend_notice(self) -> str:
        return PORT_CONFLICT_MESSAGE if getattr(self, "_backend_status", None) == "conflict" else ""

    def ensure_backend(self) -> None:
        """Ensure backend server is running."""
        with self._spawn_lock:
            if not self._stop_event.is_set():
                self._ensure_backend()

    def _ensure_backend(self) -> None:
        address = local_backend_address(self.backend_url)
        if address is None:
            logger.warning("Configured backend is not a local HTTP URL; automatic server start is disabled")
            return
        self._backend_status = backend_health(self.backend_url)
        if self._backend_status == "ready":
            logger.info("Backend is already running at %s", self.backend_url)
            return
        if self._backend_status == "conflict":
            logger.warning(PORT_CONFLICT_MESSAGE)
            return

        logger.info("Starting local AIMONITOR backend server...")
        with self._backend_lock:
            if self._stop_event.is_set():
                return
            previous, self._backend_proc = self._backend_proc, None
        if previous is not None:
            stop_backend(previous)
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        host, port = address
        cmd = backend_command(port, host)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if self._stop_event.is_set():
            return
        proc = subprocess.Popen(
            cmd,
            cwd=root_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        with self._backend_lock:
            stopped = self._stop_event.is_set()
            if not stopped:
                self._backend_proc = proc
        if stopped:
            stop_backend(proc)
            return

        for _ in range(12):
            if self._stop_event.is_set():
                return
            time.sleep(0.5)
            self._backend_status = backend_health(self.backend_url, timeout=1.0)
            if self._backend_status == "ready":
                logger.info("Backend server ready.")
                return
            if self._backend_status == "conflict":
                logger.warning(PORT_CONFLICT_MESSAGE)
                with self._backend_lock:
                    owned = self._backend_proc is proc
                    if owned:
                        self._backend_proc = None
                if owned:
                    stop_backend(proc)
                return
        logger.warning("Backend did not respond immediately, continuing anyway.")

    def _watch_backend(self) -> None:
        """Restart the local backend if it stops answering (e.g. it crashed)."""
        failures = 0
        while not self._stop_event.wait(10.0):
            self._backend_status = backend_health(self.backend_url, timeout=2.0)
            if self._backend_status == "conflict":
                failures = 0
                continue
            failures = 0 if self._backend_status == "ready" else failures + 1
            if failures >= 2 and not self._stop_event.is_set():
                logger.warning("Backend not responding; restarting it")
                self.ensure_backend()
                failures = 0

    def run(self, autostart: bool = False) -> None:
        """Run the native desktop application."""
        if not self.instance.acquire():
            # 이미 실행 중: 창을 하나 더 띄우지 않고 기존 창을 앞으로 가져온다.
            logger.info("AIMONITOR is already running; asking it to show its window")
            if not self.instance.notify_running(show=not autostart):
                startup_error("인스턴스 확인 포트가 다른 프로그램에 점유되어 AIMONITOR를 시작할 수 없습니다.")
            return
        try:
            self.ensure_backend()
            threading.Thread(target=self._watch_backend, daemon=True, name="BackendWatchdog").start()

            # 1. Start Unified AI watcher (Antigravity, Claude, GPT) in background
            if self.config.get("claude_watch_enabled", True):
                self.ai_watcher = UnifiedAIWatcher(backend_url=self.backend_url)
                self.ai_watcher.start()

            # 2. Initialize Main Native PC Window
            self.main_window = DesktopMainWindow(
                on_switch_to_widget=self.switch_to_widget,
                on_visibility_changed=self._on_visibility_changed,
                start_hidden=autostart,
                connection_notice=self.backend_notice,
            )
            if local_backend_address(self.backend_url) is None:
                self.main_window.lbl_status_msg.configure(
                    text="외부/HTTPS 서버 설정 · 로컬 서버 자동 실행 생략"
                )
            main_window = self.main_window
            self.instance.on_show = lambda: main_window.after(0, self.show_main_window)

            # 3. Initialize Floating Widget (Always-on-top HUD)
            self.widget = FloatingWidget(
                master=self.main_window,
                on_exit=self.shutdown,
                on_open_main_app=self.show_main_window,
                on_topmost_changed=self._sync_topmost,
                connection_notice=self.backend_notice,
            )
            # Show floating widget so it is always on top as requested
            self.widget.show()

            # 4. Start System Tray
            self.tray_manager = TrayManager(
                on_open_dashboard=self.show_main_window,
                on_toggle_widget=self._toggle_widget_tray,
                on_toggle_topmost=self._toggle_topmost,
                on_exit=self.shutdown,
                initial_topmost=self.widget.config.get("always_on_top", True),
            )
            self.tray_manager.start(self.main_window)

            # Center main window on screen
            sw = self.main_window.winfo_screenwidth()
            sh = self.main_window.winfo_screenheight()
            w, h = 1100, 780
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.main_window.geometry(f"{w}x{h}+{x}+{y}")

            logger.info("AIMONITOR desktop application running")
            # Start GUI mainloop
            self.main_window.mainloop()
        finally:
            self.shutdown()

    def switch_to_widget(self) -> None:
        """Minimize/hide main window, leaving only floating HUD widget on screen."""
        if self.main_window:
            self.main_window.withdraw()
        if self.widget:
            self.widget.show()

    def show_main_window(self) -> None:
        """Restore and bring main window to front (keeps widget floating)."""
        if self.main_window:
            self.main_window.show_window()
        if self.widget:
            self.widget.show()

    def _on_visibility_changed(self) -> None:
        """The main window hid/showed an AI; the widget re-reads the setting right away."""
        if self.widget:
            self.widget.reload_config()

    def _toggle_widget_tray(self) -> None:
        """Toggle floating widget from tray."""
        if not self.widget:
            return
        if self.widget.root.winfo_viewable():
            self.widget.hide()
        else:
            self.widget.show()

    def _toggle_topmost(self, is_top: bool) -> None:
        if self.widget:
            self.widget.set_always_on_top(is_top)

    def _sync_topmost(self, value: bool) -> None:
        if self.tray_manager:
            self.tray_manager.set_topmost(value)

    def shutdown(self) -> None:
        """Clean shutdown."""
        logger.info("Shutting down AIMONITOR...")
        with self._backend_lock:
            if getattr(self, "_shutdown_started", False):
                return
            self._shutdown_started = True
            self._stop_event.set()
            proc, self._backend_proc = self._backend_proc, None
        actions = [self.instance.release]
        if self.ai_watcher:
            actions.append(self.ai_watcher.stop)
        if proc is not None:
            actions.append(lambda: stop_backend(proc))
        if getattr(self, "tray_manager", None):
            actions.append(lambda: self.tray_manager.stop(notify=False))
        if getattr(self, "widget", None):
            actions.append(self.widget.close)
        if self.main_window:
            actions.append(self.main_window.destroy)
        for action in actions:
            try:
                action()
            except Exception as exc:
                logger.warning("Shutdown step failed: %s", type(exc).__name__)


def main(autostart: bool = False):
    app = DesktopApp()
    app.run(autostart=autostart)


if __name__ == "__main__":
    main()
