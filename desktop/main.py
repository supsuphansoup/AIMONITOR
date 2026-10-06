"""AIMONITOR Native Windows Desktop Application Controller.

Unified Desktop App:
- Launches the 100% Native Desktop Window (CustomTkinter)
- Floating HUD Widget stays always-on-top so the user can check 3 AIs at a glance anytime
- Silently manages background local backend & Unified AI auto-watcher (Claude, Antigravity, GPT)
- System Tray icon for background access
"""

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional

import httpx

from desktop.ai_watcher import UnifiedAIWatcher
from desktop.app_gui import DesktopMainWindow
from desktop.config import attach_to_default_desktop, load_config
from desktop.single_instance import SingleInstance
from desktop.tray import TrayManager
from desktop.widget import FloatingWidget

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("aimonitor.desktop")


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
        self.instance = SingleInstance()

    def ensure_backend(self) -> None:
        """Ensure backend server is running."""
        try:
            with httpx.Client(timeout=1.5) as client:
                res = client.get(f"{self.backend_url}/health")
                if res.status_code == 200:
                    logger.info("Backend is already running at %s", self.backend_url)
                    return
        except Exception:
            pass

        logger.info("Starting local AIMONITOR backend server...")
        if self._backend_proc and self._backend_proc.poll() is None:
            # Our previous backend is alive but not answering: replace it so the port frees up.
            try:
                self._backend_proc.terminate()
                self._backend_proc.wait(timeout=5)
            except Exception:
                pass
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self._backend_proc = subprocess.Popen(
            cmd,
            cwd=root_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        for _ in range(12):
            time.sleep(0.5)
            try:
                with httpx.Client(timeout=1.0) as client:
                    if client.get(f"{self.backend_url}/health").status_code == 200:
                        logger.info("Backend server ready.")
                        return
            except Exception:
                pass
        logger.warning("Backend did not respond immediately, continuing anyway.")

    def _watch_backend(self) -> None:
        """Restart the local backend if it stops answering (e.g. it crashed)."""
        failures = 0
        while not self._stop_event.wait(10.0):
            try:
                with httpx.Client(timeout=2.0) as client:
                    healthy = client.get(f"{self.backend_url}/health").status_code == 200
            except Exception:
                healthy = False
            failures = 0 if healthy else failures + 1
            if failures >= 2 and not self._stop_event.is_set():
                logger.warning("Backend not responding; restarting it")
                self.ensure_backend()
                failures = 0

    def run(self) -> None:
        """Run the native desktop application."""
        if not self.instance.acquire():
            # 이미 실행 중: 창을 하나 더 띄우지 않고 기존 창을 앞으로 가져온다.
            logger.info("AIMONITOR is already running; asking it to show its window")
            self.instance.notify_running()
            return
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
        )
        main_window = self.main_window
        self.instance.on_show = lambda: main_window.after(0, self.show_main_window)

        # 3. Initialize Floating Widget (Always-on-top HUD)
        self.widget = FloatingWidget(
            master=self.main_window,
            on_exit=self.shutdown,
            on_open_main_app=self.show_main_window,
        )
        # Show floating widget so it is always on top as requested
        self.widget.show()

        # 4. Start System Tray
        self.tray_manager = TrayManager(
            on_open_dashboard=self.show_main_window,
            on_toggle_widget=self._toggle_widget_tray,
            on_toggle_topmost=self._toggle_topmost,
            on_exit=self.shutdown,
            initial_topmost=self.config.get("always_on_top", True),
        )
        self.tray_manager.start()

        # Center main window on screen
        sw = self.main_window.winfo_screenwidth()
        sh = self.main_window.winfo_screenheight()
        w, h = 1100, 780
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.main_window.geometry(f"{w}x{h}+{x}+{y}")

        logger.info("AIMONITOR Native Desktop Application running with Main Window & Always-on-top Widget!")
        # Start GUI mainloop
        self.main_window.mainloop()

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
            self.widget.toggle_always_on_top()

    def shutdown(self) -> None:
        """Clean shutdown."""
        logger.info("Shutting down AIMONITOR...")
        self._stop_event.set()
        self.instance.release()
        if self.ai_watcher:
            self.ai_watcher.stop()
        if self._backend_proc:
            try:
                self._backend_proc.terminate()
            except Exception:
                pass
        if self.main_window:
            try:
                self.main_window.destroy()
            except Exception:
                pass


def main():
    app = DesktopApp()
    app.run()


if __name__ == "__main__":
    main()
