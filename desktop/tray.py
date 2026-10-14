"""Windows System Tray Icon integration using pystray."""

import threading
from typing import Callable, Optional

import pystray
from PIL import Image, ImageDraw

from desktop import theme


def create_tray_icon_image(width: int = 64, height: int = 64) -> Image.Image:
    """Generate a quiet indigo badge using the shared product palette."""
    image = Image.new("RGBA", (width, height), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Neutral rounded surface
    draw.rounded_rectangle(
        [(4, 4), (width - 4, height - 4)],
        radius=theme.RADIUS_MD,
        fill=theme.color(theme.CARD),
    )

    # Indigo center dot
    cx, cy = width // 2, height // 2
    r = 12
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=theme.color(theme.ACCENT))

    # Inner bright dot
    r_in = 5
    draw.ellipse([(cx - r_in, cy - r_in), (cx + r_in, cy + r_in)], fill=theme.color(theme.ACCENT_ON))

    return image


class TrayManager:
    """Manages the Windows system tray icon and menu."""

    def __init__(
        self,
        on_open_dashboard: Optional[Callable[[], None]] = None,
        on_toggle_widget: Optional[Callable[[], None]] = None,
        on_toggle_topmost: Optional[Callable[[bool], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        initial_topmost: bool = True,
    ):
        self.on_open_dashboard = on_open_dashboard
        self.on_toggle_widget = on_toggle_widget
        self.on_toggle_topmost = on_toggle_topmost
        self.on_exit = on_exit
        self.is_topmost = initial_topmost
        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None
        self._unsubscribe_theme: Optional[Callable[[], None]] = None
        self._master = None
        self._stopped = False

    def _dispatch(self, callback: Callable[[], None]) -> None:
        if self._master is not None:
            try:
                self._master.after(0, callback)
            except RuntimeError:
                pass
        else:
            callback()

    def set_topmost(self, value: bool) -> None:
        self.is_topmost = value
        if self._icon is not None:
            self._icon.update_menu()

    def _menu_toggle_topmost(self, icon, item):
        if self.on_toggle_topmost:
            self._dispatch(lambda: self.on_toggle_topmost(not self.is_topmost))
        else:
            self.set_topmost(not self.is_topmost)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "📊 대시보드 열기",
                lambda icon, item: self.on_open_dashboard and self._dispatch(self.on_open_dashboard),
                default=True,
            ),
            pystray.MenuItem(
                "🎛️ 위젯 보이기/숨기기",
                lambda icon, item: self.on_toggle_widget and self._dispatch(self.on_toggle_widget)
            ),
            pystray.MenuItem(
                "📌 항상 위에 고정",
                self._menu_toggle_topmost,
                checked=lambda item: self.is_topmost,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ 종료", lambda icon, item: self._dispatch(self.stop)),
        )

    def start(self, master=None) -> None:
        """Start tray icon in a dedicated daemon thread."""
        img = create_tray_icon_image()
        self._master = master
        self._icon = pystray.Icon("aimonitor", img, "AIMONITOR - AI Observability", menu=self._build_menu())
        if master is not None:
            self._unsubscribe_theme = theme.observe(master, self._on_theme_changed)
        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="TrayThread")
        self._thread.start()

    def _on_theme_changed(self, _mode: str) -> None:
        if self._icon is not None:
            self._icon.icon = create_tray_icon_image()

    def stop(self, notify: bool = True) -> None:
        """Stop tray icon and notify exit."""
        if self._stopped:
            return
        self._stopped = True
        if self._unsubscribe_theme is not None:
            self._unsubscribe_theme()
            self._unsubscribe_theme = None
        if self._icon:
            self._icon.stop()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if notify and self.on_exit:
            self.on_exit()
