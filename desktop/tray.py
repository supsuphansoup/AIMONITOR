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

    def _menu_toggle_topmost(self, icon, item):
        self.is_topmost = not self.is_topmost
        if self.on_toggle_topmost:
            self.on_toggle_topmost(self.is_topmost)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "📊 대시보드 열기",
                lambda icon, item: self.on_open_dashboard and self.on_open_dashboard(),
                default=True,
            ),
            pystray.MenuItem(
                "🎛️ 위젯 보이기/숨기기", lambda icon, item: self.on_toggle_widget and self.on_toggle_widget()
            ),
            pystray.MenuItem(
                "📌 항상 위에 고정",
                self._menu_toggle_topmost,
                checked=lambda item: self.is_topmost,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ 종료", lambda icon, item: self.stop()),
        )

    def start(self) -> None:
        """Start tray icon in a dedicated daemon thread."""
        img = create_tray_icon_image()
        self._icon = pystray.Icon("aimonitor", img, "AIMONITOR - AI Observability", menu=self._build_menu())
        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="TrayThread")
        self._thread.start()

    def stop(self) -> None:
        """Stop tray icon and notify exit."""
        if self._icon:
            self._icon.stop()
        if self.on_exit:
            self.on_exit()
