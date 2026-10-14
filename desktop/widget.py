"""Always-on-top floating widget for AIMONITOR.

Shows only the items the user picked (right-click > 표시 항목) from what the
connected AIs actually report: remaining quota windows with their source,
context estimates, and spend. Nothing it shows is a guessed limit; an item with
no official value reads "확인 불가".

- Frameless, draggable, semi-transparent, optional always-on-top
- Mini (one line) and expanded (one row per item) modes
- Double-click opens the native dashboard
"""

import logging
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable, Dict, List, Optional

import httpx

from desktop import theme
from desktop.accounts import list_accounts
from desktop.ai_detector import detect_connected_ais
from desktop.config import (
    attach_to_default_desktop,
    backend_headers,
    identify_backend,
    load_config,
    update_config,
)
from desktop.context_manager import get_connected_ai_summary, hidden_ai_keys
from desktop.display import (
    AccountQuotaView,
    account_tools,
    attach_accounts,
    select_entries,
    valid_api_payload,
    widget_entries,
    widget_entry_note,
)
from desktop.quota import account_quotas
from desktop.rate_fetcher import shared_updater

ROW_HEIGHT = theme.ROW_HEIGHT
logger = logging.getLogger(__name__)


def monitor_work_areas(root: tk.Misc) -> list:
    """Logical work rectangles, including monitors left/above the primary display."""
    areas = []
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                        ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        user32 = ctypes.windll.user32
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        def collect(monitor, _dc, _rect, _data):
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.work
                areas.append((r.left, r.top, r.right, r.bottom))
            return True
        user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
    return areas or [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]


def fit_work_area(x: int, y: int, width: int, height: int, areas: list) -> tuple:
    candidates = []
    for left, top, right, bottom in areas:
        w, h = min(width, right - left), min(height, bottom - top)
        cx, cy = max(left, min(x, right - w)), max(top, min(y, bottom - h))
        candidates.append(((x - cx) ** 2 + (y - cy) ** 2, cx, cy, w, h))
    return min(candidates)[1:]


class FloatingWidget:
    """Always-on-top floating HUD widget."""

    def __init__(
        self,
        master: Optional[tk.Misc] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_open_main_app: Optional[Callable[[], None]] = None,
        on_topmost_changed: Optional[Callable[[bool], None]] = None,
        connection_notice: Optional[Callable[[], str]] = None,
    ):
        attach_to_default_desktop()
        self.config = load_config()
        self._theme_mode = theme.appearance_mode()
        self.on_exit = on_exit
        self.on_open_main_app = on_open_main_app
        self.on_topmost_changed = on_topmost_changed
        self.connection_notice = connection_notice or (lambda: "")

        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.title("AIMONITOR 위젯")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.config.get("always_on_top", True))
        self.root.attributes("-alpha", self.config.get("widget_opacity", 0.96))
        self.root.lift()
        self.root.configure(bg=theme.color(theme.CARD))

        self.backend_url = self.config.get("backend_url", "http://127.0.0.1:8000")
        self.mode = self.config.get("widget_mode", "expanded")
        self.poll_ms = int(self.config.get("poll_interval_ms", 3000))
        self.rate_updater = shared_updater()

        self._drag_start_x = 0
        self._drag_start_y = 0
        self.summary_data: Dict[str, Any] = {}
        self.entries: List[Dict[str, Any]] = []
        self.is_connected = False
        self._connected_ais: Optional[List[Dict[str, Any]]] = None
        self._fetching = False
        self._closed = False
        self._poll_job = None
        self._auth_required = False
        self._rows_sig: Any = None

        self.font_bold = tkfont.Font(family=theme.FONT_SANS, size=-theme.FONT_SM, weight="bold")
        self.font_normal = tkfont.Font(family=theme.FONT_SANS, size=-theme.FONT_SM)
        self.font_small = tkfont.Font(family=theme.FONT_SANS, size=-theme.FONT_XS)
        self.font_title = tkfont.Font(family=theme.FONT_SANS, size=-theme.FONT_BODY, weight="bold")

        self.frame = tk.Frame(
            self.root,
            bg=theme.color(theme.CARD),
            highlightbackground=theme.color(theme.BORDER),
            highlightcolor=theme.color(theme.BORDER),
            highlightthickness=1,
            cursor="fleur",
        )
        self.frame.pack(fill="both", expand=True)

        self._build_ui()
        self._bind_events()
        self._place()
        theme.observe(self.root, lambda _mode: self._render())
        self._schedule_poll(200)

    # ------------------------------------------------------------------ layout
    def _selected_ids(self) -> Optional[List[str]]:
        sel = self.config.get("widget_items")
        return list(sel) if isinstance(sel, list) else None

    def _visible(self) -> List[Dict[str, Any]]:
        return select_entries(self.entries, self._selected_ids())

    def _size(self) -> tuple:
        if self.mode == "mini":
            # Fit the line to its text (status dot + two icons ≈ 80px), within the screen.
            text = self.mini_text.cget("text") if hasattr(self, "mini_text") else ""
            width = self.font_bold.measure(text) + 80
            return max(260, min(width, self.root.winfo_screenwidth() - 40)), 36
        rows = max(1, len(self._visible()))
        visible = self._visible()
        content_width = max(
            (
                self.font_normal.measure(f"{e['group']} · {e['label']}")
                + self.font_bold.measure(e["value"])
                + self.font_small.measure(widget_entry_note(e))
                + theme.SPACE_8
                + theme.SPACE_6
                for e in visible
            ),
            default=0,
        )
        return min(max(460, content_width), self.root.winfo_screenwidth() - 40), 80 + rows * ROW_HEIGHT

    def _place(self) -> None:
        self.root.update_idletasks()
        w, h = self._size()
        x, y = self.config.get("widget_x"), self.config.get("widget_y")
        areas = monitor_work_areas(self.root)
        if x is None or y is None:
            left, top, right, _bottom = areas[0]
            x, y = max(left, right - w - 24), top + 42
        x, y, w, h = fit_work_area(x, y, w, h, areas)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _resize(self) -> None:
        w, h = self._size()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        x, y, w, h = fit_work_area(x, y, w, h, monitor_work_areas(self.root))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self) -> None:
        self._rows_sig = None  # new, empty rows frame: the next render must fill it
        for child in self.frame.winfo_children():
            child.destroy()
        if self.mode == "mini":
            self._build_mini_ui()
        else:
            self._build_expanded_ui()

    def _icon(
        self, parent: tk.Misc, text: str, command: Callable[[], None], fg: Optional[str] = None
    ) -> None:
        lbl = tk.Label(
            parent,
            text=text,
            fg=fg or theme.color(theme.FAINT),
            bg=theme.color(theme.CARD),
            font=self.font_bold,
            cursor="hand2",
        )
        lbl.pack(side="right", padx=theme.SPACE_1)
        lbl.bind("<Button-1>", lambda e: command())

    def _build_mini_ui(self) -> None:
        bar = tk.Frame(self.frame, bg=theme.color(theme.CARD))
        bar.pack(fill="both", expand=True)
        self.lbl_status = tk.Label(
            bar, text="●", fg=theme.color(theme.ERROR), bg=theme.color(theme.CARD), font=self.font_bold
        )
        self.lbl_status.pack(side="left", padx=(theme.SPACE_2, theme.SPACE_1))
        self._icon(bar, "↗", self.open_dashboard, fg=theme.color(theme.ACCENT))
        self._icon(bar, "⤢", self.toggle_mode)
        self.mini_text = tk.Label(
            bar,
            text="연결 대기 중…",
            fg=theme.color(theme.TEXT),
            bg=theme.color(theme.CARD),
            font=self.font_bold,
            anchor="w",
        )
        self.mini_text.pack(side="left", fill="x", expand=True)

    def _build_expanded_ui(self) -> None:
        top = tk.Frame(self.frame, bg=theme.color(theme.CARD))
        top.pack(fill="x", padx=theme.SPACE_3, pady=(theme.SPACE_2, theme.SPACE_HALF))
        self.lbl_status = tk.Label(
            top, text="●", fg=theme.color(theme.ERROR), bg=theme.color(theme.CARD), font=self.font_bold
        )
        self.lbl_status.pack(side="left")
        tk.Label(
            top,
            text="AIMONITOR",
            fg=theme.color(theme.TEXT),
            bg=theme.color(theme.CARD),
            font=self.font_title,
        ).pack(side="left", padx=theme.SPACE_2)
        self._icon(top, "✕", self.hide, fg=theme.color(theme.FAINT))
        self._icon(top, "↗", self.open_dashboard, fg=theme.color(theme.ACCENT))
        self._icon(top, "⤡", self.toggle_mode)
        tk.Frame(self.frame, bg=theme.color(theme.SUBTLE), height=1).pack(
            fill="x", padx=theme.SPACE_2, pady=theme.SPACE_HALF
        )
        self.rows_frame = tk.Frame(self.frame, bg=theme.color(theme.CARD))
        self.rows_frame.pack(fill="both", expand=True, padx=theme.SPACE_3, pady=theme.SPACE_HALF)
        self.lbl_footer = tk.Label(
            self.frame,
            text="",
            fg=theme.color(theme.FAINT),
            bg=theme.color(theme.CARD),
            font=self.font_small,
            anchor="w",
        )
        self.lbl_footer.pack(fill="x", padx=theme.SPACE_3, pady=(0, theme.SPACE_1))

    # ------------------------------------------------------------------ events
    def _bind_events(self) -> None:
        # Rows are rebuilt every refresh; bind each widget once so handlers do not pile up.
        def bind(widget: tk.Misc) -> None:
            for child in widget.winfo_children():
                bind(child)
            if getattr(widget, "_aim_bound", False):
                return
            widget._aim_bound = True  # type: ignore[attr-defined]
            widget.bind("<Button-1>", self._on_drag_start, add="+")
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_release)
            widget.bind("<Double-Button-1>", lambda e: self.open_dashboard())
            widget.bind("<Button-3>", self._show_context_menu)

        bind(self.frame)

    def _on_drag_start(self, event) -> None:
        self._drag_start_x = event.x_root - self.root.winfo_x()
        self._drag_start_y = event.y_root - self.root.winfo_y()

    def _on_drag_motion(self, event) -> None:
        self.root.geometry(f"+{event.x_root - self._drag_start_x}+{event.y_root - self._drag_start_y}")

    def _on_drag_release(self, event) -> None:
        self._save(widget_x=self.root.winfo_x(), widget_y=self.root.winfo_y())

    def _save(self, **changes: Any) -> bool:
        try:
            self.config = update_config(changes)
            return True
        except Exception:
            self.root.bell()
            logger.warning("Could not save widget settings")
            return False

    def _show_context_menu(self, event) -> None:
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=theme.color(theme.SUBTLE),
            fg=theme.color(theme.TEXT),
            activebackground=theme.color(theme.ACCENT_SOFT),
            activeforeground=theme.color(theme.TEXT),
        )
        menu.add_command(label="📊 전체 프로그램 창 열기", command=self.open_dashboard)
        menu.add_command(label="⤢ 한 줄 / 카드 모드 전환", command=self.toggle_mode)
        menu.add_separator()
        menu.add_cascade(label="👁 표시할 AI", menu=self._ais_menu(menu))
        menu.add_cascade(label="☑ 표시 항목 선택", menu=self._items_menu(menu))
        menu.add_command(label="↺ 표시 항목 기본값으로", command=lambda: self._set_selection(None))
        menu.add_separator()
        self._top_var = tk.BooleanVar(value=self.config.get("always_on_top", True))
        menu.add_checkbutton(
            label="📌 항상 위에 고정", variable=self._top_var, command=self.toggle_always_on_top
        )
        sub_opacity = tk.Menu(menu, tearoff=0, bg=theme.color(theme.SUBTLE), fg=theme.color(theme.TEXT))
        for op in (1.0, 0.95, 0.85, 0.70):
            sub_opacity.add_command(label=f"{int(op * 100)}%", command=lambda v=op: self.set_opacity(v))
        menu.add_cascade(label="🌓 투명도", menu=sub_opacity)
        menu.add_command(label="🔍 AI 다시 찾기", command=self.rescan)
        menu.add_command(label="🔄 지금 새로고침", command=self.schedule_update)
        menu.add_separator()
        menu.add_command(label="❌ 위젯 숨기기", command=self.hide)
        menu.add_command(label="⏻ 프로그램 종료", command=self.quit_app)
        menu.tk_popup(event.x_root, event.y_root)

    def _ais_menu(self, parent: tk.Menu) -> tk.Menu:
        """One checkbox per connected AI; unchecking hides it everywhere (saved immediately)."""
        sub = tk.Menu(parent, tearoff=0, bg=theme.color(theme.SUBTLE), fg=theme.color(theme.TEXT))
        if not self._connected_ais:
            sub.add_command(label="(연결된 AI를 찾는 중)", state="disabled")
            return sub
        hidden = set(hidden_ai_keys(self.config))
        self._ai_vars: Dict[str, tk.BooleanVar] = {}
        for ai in self._connected_ais:
            key = ai["key"]
            var = tk.BooleanVar(value=key not in hidden)
            self._ai_vars[key] = var
            sub.add_checkbutton(
                label=ai.get("display_name", key), variable=var, command=lambda k=key: self._toggle_ai(k)
            )
        return sub

    def _toggle_ai(self, key: str) -> None:
        hidden = [k for k in hidden_ai_keys(load_config()) if k != key]
        if not self._ai_vars[key].get():
            hidden.append(key)
        self._save(hidden_ais=hidden)
        self.schedule_update()

    def reload_config(self) -> None:
        """Pick up settings the main window saved (e.g. a hidden AI) and refresh now."""
        self.config = load_config()
        self.schedule_update()

    def _items_menu(self, parent: tk.Menu) -> tk.Menu:
        """One submenu per AI; each item is a checkbox that is saved immediately."""
        root_menu = tk.Menu(parent, tearoff=0, bg=theme.color(theme.SUBTLE), fg=theme.color(theme.TEXT))
        if not self.entries:
            root_menu.add_command(label="(데이터를 불러오는 중)", state="disabled")
            return root_menu
        visible = {e["id"] for e in self._visible()}
        self._item_vars: Dict[str, tk.BooleanVar] = {}
        groups: Dict[str, tk.Menu] = {}
        for e in self.entries:
            sub = groups.get(e["group"])
            if sub is None:
                sub = tk.Menu(root_menu, tearoff=0, bg=theme.color(theme.SUBTLE), fg=theme.color(theme.TEXT))
                groups[e["group"]] = sub
                root_menu.add_cascade(label=e["group"], menu=sub)
            var = tk.BooleanVar(value=e["id"] in visible)
            self._item_vars[e["id"]] = var
            hint = f"  [{e['hint']}]" if e["hint"] else ""
            sub.add_checkbutton(
                label=f"{e['label']}{hint}", variable=var, command=lambda i=e["id"]: self._toggle_item(i)
            )
        return root_menu

    def _toggle_item(self, item_id: str) -> None:
        current = [e["id"] for e in self._visible()]
        if self._item_vars[item_id].get():
            current.append(item_id)
        else:
            current = [i for i in current if i != item_id]
        self._set_selection(current)

    def _set_selection(self, ids: Optional[List[str]]) -> None:
        self._save(widget_items=ids)
        self._render()

    def toggle_mode(self) -> None:
        self.mode = "mini" if self.mode == "expanded" else "expanded"
        self._save(widget_mode=self.mode)
        self._build_ui()
        self._bind_events()
        self._render()

    def toggle_always_on_top(self) -> None:
        self.set_always_on_top(not self.config.get("always_on_top", True))

    def set_always_on_top(self, value: bool) -> None:
        if self._save(always_on_top=value) is False:
            return
        self.root.attributes("-topmost", value)
        callback = getattr(self, "on_topmost_changed", None)
        if callback:
            callback(value)

    def set_opacity(self, opacity: float) -> None:
        self.root.attributes("-alpha", opacity)
        self._save(widget_opacity=opacity)

    def rescan(self) -> None:
        self._connected_ais = None
        self.schedule_update()

    def open_dashboard(self) -> None:
        if self.on_open_main_app:
            self.on_open_main_app()

    def hide(self) -> None:
        self.root.withdraw()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()

    def quit_app(self) -> None:
        if self.on_exit:
            self.on_exit()
        self.root.quit()

    def close(self) -> None:
        self._closed = True
        self._cancel_poll()
        self.root.destroy()

    def _cancel_poll(self) -> None:
        job = getattr(self, "_poll_job", None)
        if job is not None:
            self.root.after_cancel(job)
            self._poll_job = None

    def _schedule_poll(self, delay: int) -> None:
        self._cancel_poll()
        if not self._closed:
            self._poll_job = self.root.after(delay, self.schedule_update)

    # ------------------------------------------------------------------ data
    def schedule_update(self) -> None:
        self._cancel_poll()
        if self._closed:
            return
        if self._fetching:
            return
        self._fetching = True
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self) -> None:
        if getattr(self, "connection_notice", lambda: "")():
            self._fetching = False
            try:
                self.root.after(0, lambda: self._on_data({}, False))
            except RuntimeError:
                pass
            return
        summary: Dict[str, Any] = {}
        ok = False
        auth_required = False
        try:
            if self._connected_ais is None:
                self._connected_ais = detect_connected_ais()
            with httpx.Client(timeout=3.0) as client:
                if not identify_backend(client, self.backend_url, backend_headers()):
                    raise ValueError("Backend identity unavailable")
                res = client.get(f"{self.backend_url}/api/v1/metrics/active_contexts")
                auth_required = res.status_code in (401, 403)
                spend: Dict[str, Any] = {}
                if any(a["key"].endswith("_api") for a in self._connected_ais):
                    r_sp = client.get(f"{self.backend_url}/api/v1/keys/spend", timeout=15.0)
                    auth_required = auth_required or r_sp.status_code in (401, 403)
                    if r_sp.status_code == 200:
                        try:
                            payload = r_sp.json()
                        except ValueError:
                            payload = None
                        if valid_api_payload("spend", payload):
                            spend = payload
            if res.status_code == 200:
                active = res.json()
                if not valid_api_payload("active_contexts", active):
                    raise ValueError("Invalid active contexts response")
                account_rows = list_accounts()
                self._connected_ais = account_tools(self._connected_ais, account_rows)
                summary = get_connected_ai_summary(
                    self._connected_ais,
                    usage_by_ai=active,
                    krw_rate=self.rate_updater.rate,
                    config=self.config,
                    official_spend=spend,
                )
                try:
                    quotas = account_quotas(registry=AccountQuotaView(account_rows)) if any(
                        i["key"] in ("claude", "codex", "antigravity", "copilot", "cursor")
                        for i in summary.get("items", [])
                    ) else []
                except Exception:
                    quotas = []
                attach_accounts(summary, active, account_rows, quotas, self.rate_updater.rate)
                ok = True
        except Exception:
            pass
        finally:
            self._fetching = False
        try:
            self.root.after(0, lambda: self._on_data(summary, ok, auth_required))
        except RuntimeError:
            pass  # window already destroyed

    def _on_data(self, summary: Dict[str, Any], ok: bool, auth_required: bool = False) -> None:
        if self._closed:
            return
        self.is_connected = ok
        self._auth_required = auth_required
        if ok:
            self.summary_data = summary
            self.entries = widget_entries(summary)
        self._render()
        self._resize()
        self._schedule_poll(self.poll_ms)

    # ------------------------------------------------------------------ render
    def _render(self) -> None:
        mode = theme.appearance_mode()
        if mode != self._theme_mode:
            self._theme_mode = mode
            self.root.configure(bg=theme.color(theme.CARD))
            self.frame.configure(
                bg=theme.color(theme.CARD),
                highlightbackground=theme.color(theme.BORDER),
                highlightcolor=theme.color(theme.BORDER),
            )
            self.entries = widget_entries(self.summary_data)
            self._build_ui()
            self._bind_events()
        if hasattr(self, "lbl_status"):
            self.lbl_status.config(
                fg=theme.color(theme.SUCCESS)
                if self.is_connected and not getattr(self, "_auth_required", False)
                else theme.color(theme.ERROR)
            )
        visible = self._visible()

        notice = getattr(self, "connection_notice", lambda: "")()
        if notice:
            if self.mode == "mini":
                self.mini_text.config(text=notice)
                self._resize()
                return
            self.lbl_footer.config(text=notice, fg=theme.color(theme.ERROR))
        elif getattr(self, "_auth_required", False):
            if self.mode == "mini":
                self.mini_text.config(text="인증 필요")
                self._resize()
                return
            self.lbl_footer.config(text="인증 필요", fg=theme.color(theme.ERROR))
        elif hasattr(self, "lbl_footer"):
            status = "연결 실패 · 이전 값 · " if not self.is_connected and self.entries else ""
            self.lbl_footer.config(text=status + self.rate_updater.describe(), fg=theme.color(theme.FAINT))

        if self.mode == "mini":
            if not self.entries:
                self.mini_text.config(text="연결 대기 중…" if not self.is_connected else "표시할 항목 없음")
                self._resize()
                return
            parts = [f"{e['group']} {e['label']} {e['value']} {widget_entry_note(e)}" for e in visible]
            status = "연결 실패 · 이전 값 │ " if not self.is_connected else ""
            text = status + ("  │  ".join(parts) or "우클릭 > 표시 항목 선택")
            if text != self.mini_text.cget("text"):
                self.mini_text.config(text=text)
                self._resize()
            return

        # Rebuild rows only when what they show changed; most polls change nothing.
        sig = (
            bool(self.entries),
            self.rate_updater.describe(),
            tuple((e["group"], e["label"], e["value"], e["color"], widget_entry_note(e)) for e in visible),
        )
        if sig == self._rows_sig:
            return
        self._rows_sig = sig

        for child in self.rows_frame.winfo_children():
            child.destroy()
        if not visible:
            tk.Label(
                self.rows_frame,
                text="우클릭 > 표시 항목 선택" if self.entries else "연결 대기 중…",
                fg=theme.color(theme.FAINT),
                bg=theme.color(theme.CARD),
                font=self.font_normal,
            ).pack(anchor="w")
        for e in visible:
            row = tk.Frame(self.rows_frame, bg=theme.color(theme.CARD), height=ROW_HEIGHT)
            row.pack(fill="x")
            row.pack_propagate(False)
            tk.Label(
                row,
                text=f"{e['group']} · {e['label']}",
                fg=theme.color(theme.MUTED),
                bg=theme.color(theme.CARD),
                font=self.font_normal,
                anchor="w",
            ).pack(side="left", padx=(0, theme.SPACE_3))
            tk.Label(
                row, text=e["value"], fg=e["color"], bg=theme.color(theme.CARD), font=self.font_bold
            ).pack(side="left")
            tail = widget_entry_note(e)
            if tail:
                tk.Label(
                    row,
                    text=tail,
                    fg=theme.color(theme.FAINT),
                    bg=theme.color(theme.CARD),
                    font=self.font_small,
                ).pack(side="right")
        self._bind_events()
        self._resize()

    def run(self) -> None:
        self.root.mainloop()
