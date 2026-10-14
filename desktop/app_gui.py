"""100% Native Windows Desktop Application for AIMONITOR.

Pure Desktop Program Interface built with CustomTkinter & Matplotlib:
- Tab 1: ⚡ PC 연결 AI 자동 모니터
  - 사용자의 PC에 실제로 설치/연동 및 결제된 AI만 자동으로 찾아 동적으로 화면 구성
  - 5시간 롤링 쿼터(잔여 %, 리셋 카운트다운), 대화방 메모리 버퍼, 주간 누적 작업량 게이지
  - 용량제(남은 용량, %)와 종량제(들어간 돈 ￦/$, 추가 결제 여부) 실시간 관제
- Tab 2: 📊 실시간 차트 & 비용 분석 (순수 네이티브 Matplotlib 다크 테마 캔버스 임베딩)
  - 모델별 비용 점유율 및 토큰 소진량 가로 바 차트
  - 모델별 상세 호출 횟수, 토큰 구성, 소요 비용 집계표
- Tab 3: 📋 실시간 AI 로그 & 상세 분석
  - 프로바이더/상태 필터링, 실시간 호출 스트림
  - 행 클릭 시 정밀 토큰/레이턴시/비용 검사기 팝업
- Tab 4: 🔑 BYOK API 키 관리 센터 (종량제 사용자 전용)
  - OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq API 키 로컬 보안 관리
  - 키 마스킹 저장, 실시간 잔액 및 유효성 검증
- Tab 5: ⚙️ 시스템 진단 & 환경설정
  - PC 내 7개 AI 플랫폼 자동 진단표, 실시간 인터넷 환율 동기화, 주간 한도 및 위젯 설정
"""

import logging
import math
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
import httpx
import matplotlib

from desktop import accounts, autostart, theme

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

# Windows Hangul font support for Matplotlib
if sys.platform == "win32":
    plt.rcParams["font.family"] = theme.CHART_FONT
    plt.rcParams["axes.unicode_minus"] = False

from backend.app.services.api_key_manager import api_key_manager
from desktop.ai_detector import detect_connected_ais, scan_all_ai_status
from desktop.config import attach_to_default_desktop, backend_headers, load_config, update_config
from desktop.context_manager import get_connected_ai_summary, hidden_ai_keys
from desktop.display import (
    GREY,
    AccountQuotaView,
    account_label,
    account_options,
    account_query,
    account_tools,
    account_usage_lines,
    attach_accounts,
    format_num,
    krw,
    remaining_color,
    spend_text,
    usage_cost_text,
    window_text,
)
from desktop.quota import account_quotas
from desktop.rate_fetcher import shared_updater

theme.install()

logger = logging.getLogger("aimonitor.desktop.app_gui")


# Label in the BYOK dropdown -> provider id in api_key_manager.
BYOK_PROVIDERS = {
    "OpenAI": "openai",
    "OpenAI 관리자 키 (비용 조회)": "openai_admin",
    "Anthropic": "anthropic",
    "Anthropic 관리자 키 (비용 조회)": "anthropic_admin",
    "Google Gemini": "gemini",
    "DeepSeek": "deepseek",
    "OpenRouter": "openrouter",
    "Groq": "groq",
}

KEY_STATUS_TEXT = {
    "valid": "유효", "invalid": "인증 실패", "verification_unavailable": "확인 불가",
    "storage_error": "저장소 확인 불가", "unreadable": "복호화 불가",
    "connected": "미검증", "unconfigured": "미등록",
}


class DesktopMainWindow(ctk.CTk):
    """100% Native Windows GUI Desktop Application."""

    def __init__(
        self,
        on_switch_to_widget: Optional[Callable[[], None]] = None,
        on_visibility_changed: Optional[Callable[[], None]] = None,
        start_hidden: bool = False,
    ):
        attach_to_default_desktop()
        super().__init__()
        if start_hidden:
            self.withdraw()

        self.on_switch_to_widget = on_switch_to_widget
        # Called after the user hides/shows an AI here, so the widget can refresh at once.
        self.on_visibility_changed = on_visibility_changed
        self.config = load_config()
        self.backend_url = self.config.get("backend_url", "http://127.0.0.1:8000")
        # One rate for the whole process; the widget reads the same updater.
        self.rate_updater = shared_updater()
        self.krw_rate = self.rate_updater.rate

        # Auto-detected connected AIs
        self.connected_ais: List[Dict[str, Any]] = detect_connected_ais()

        # Window settings
        self.title("AIMONITOR · PC 연결 AI 모니터")
        self.geometry("1120x820")
        self.minsize(1020, 720)

        # State data
        self.breakdown_data: List[Dict[str, Any]] = []
        self.traces_data: List[Dict[str, Any]] = []
        self.is_connected = False
        self._is_updating = False
        self.cached_summary: Optional[Dict[str, Any]] = None
        # One pending refresh timer at a time; manual refreshes replace it instead of adding a loop.
        self._refresh_job: Optional[str] = None
        self._search_job: Optional[str] = None
        # Last rendered inputs, so an unchanged poll does not redraw the chart or rebuild rows.
        self._chart_sig: Any = None
        self._breakdown_sig: Any = None
        self._log_rows: List[Dict[str, Any]] = []
        self._font_log = theme.font(theme.FONT_XS)
        self._font_log_bold = theme.font(theme.FONT_XS)
        self._font_log_main = theme.font(theme.FONT_SM)
        self._font_log_normal = theme.font(theme.FONT_SM)
        self._font_log_button = theme.font(theme.FONT_XS)

        # Filter state for Tab 3 (Logs)
        self.log_filter_provider = "전체"
        self.log_filter_status = "전체"
        self.log_filter_search = ""
        self.account_rows = accounts.list_accounts()
        self.connected_ais = account_tools(self.connected_ais, self.account_rows)
        self.account_choices = account_options(self.account_rows)
        self.log_account_selection = "전체"
        self.chart_account_selection = "전체"

        self.official_spend: Dict[str, Any] = {}
        self.rate_updater.add_listener(self._on_rate_updated)

        self._build_header()
        self._build_statusbar()
        self._build_tabs()
        # Handle close button (minimize/hide to widget instead of crashing entire app)
        self.protocol("WM_DELETE_WINDOW", self._handle_switch_to_widget)

        # Start periodic update
        self.after(300, self.refresh_data)

    def _set_appearance_mode(self, mode_string: str) -> None:
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "fig"):
            self._chart_sig = None
            self._update_native_charts(self.breakdown_data)
        if getattr(self, "cached_summary", None) is not None:
            for item in self.cached_summary["items"]:
                widgets = self.ai_card_widgets.get(item["key"])
                if widgets:
                    if self._render_account_card(widgets, item):
                        continue
                    self._render_quota_rows(widgets, item["quota_windows"] or [])
                    context = item.get("context")
                    if context and context.get("used_pct") is not None:
                        widgets["prog_ctx"].configure(
                            progress_color=remaining_color(100 - context["used_pct"])
                        )

    def _on_rate_updated(self, rate: float, src: str) -> None:
        # Called from the updater thread; the updater itself persists the rate.
        self.krw_rate = rate
        try:
            self.after(0, self._apply_rate_ui)
        except Exception:
            pass

    def _apply_rate_ui(self) -> None:
        if hasattr(self, "lbl_rate_live"):
            self.lbl_rate_live.configure(text=self.rate_updater.describe())
        self.refresh_data()

    def rescan_ais(self) -> None:
        """Trigger re-scan of PC connected and paid AIs."""
        self.connected_ais = account_tools(detect_connected_ais(), accounts.list_accounts())
        self._rebuild_unified_cards()
        self._refresh_visibility_list()
        self._refresh_diagnostic_table()
        self._refresh_keys_list()
        self.refresh_data()

    def _build_header(self) -> None:
        """Top bar navigation and action controls."""
        header = ctk.CTkFrame(self, fg_color=theme.CARD, corner_radius=0, height=64)
        header.pack(fill="x", side="top")

        brand_frame = ctk.CTkFrame(header, fg_color="transparent")
        brand_frame.pack(side="left", padx=theme.SPACE_5, pady=theme.SPACE_3)

        lbl_logo = ctk.CTkLabel(
            brand_frame,
            text="AIMONITOR",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.ACCENT,
        )
        lbl_logo.pack(side="left", padx=(0, theme.SPACE_3))

        self.lbl_detected_status = ctk.CTkLabel(
            brand_frame,
            text=self._format_detected_badge(),
            font=theme.font(theme.FONT_SM),
            text_color=theme.FAINT,
            wraplength=440,
            justify="left",
        )
        self.lbl_detected_status.pack(side="left")

        # Right buttons
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right", padx=theme.SPACE_5, pady=theme.SPACE_3)

        btn_rescan = ctk.CTkButton(
            btn_frame,
            text="PC 연결 AI 스캔",
            width=130,
            height=34,
            fg_color=theme.SUBTLE,
            hover_color=theme.HOVER,
            text_color=theme.ACCENT,
            command=self.rescan_ais,
        )
        btn_rescan.pack(side="left", padx=theme.SPACE_1)

        btn_refresh = ctk.CTkButton(
            btn_frame,
            text="새로고침",
            width=80,
            height=34,
            fg_color=theme.SUBTLE,
            hover_color=theme.HOVER,
            text_color=theme.TEXT,
            command=self.refresh_data,
        )
        btn_refresh.pack(side="left", padx=theme.SPACE_1)

        btn_to_widget = ctk.CTkButton(
            btn_frame,
            text="위젯 모드로 축소",
            width=135,
            height=34,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color=theme.ACCENT_ON,
            font=theme.font(theme.FONT_BODY),
            command=self._handle_switch_to_widget,
        )
        btn_to_widget.pack(side="left", padx=theme.SPACE_1)

    def _format_detected_badge(self) -> str:
        names = [a["display_name"] for a in self.connected_ais]
        if names:
            return f"● 자동 감지된 AI ({len(names)}개): " + " · ".join(names)
        return "● 감지된 AI 없음 (스캔 필요)"

    def _build_tabs(self) -> None:
        """Build 5 dedicated desktop tabs."""
        self.tabview = ctk.CTkTabview(
            self,
            fg_color=theme.APP,
            segmented_button_fg_color=theme.SUBTLE,
            segmented_button_selected_color=theme.ACCENT_SOFT,
            segmented_button_selected_hover_color=theme.ACCENT_SOFT,
            text_color=theme.TEXT,
            command=self._on_tab_changed,
        )
        self.tabview.pack(fill="both", expand=True, padx=theme.SPACE_5, pady=(theme.SPACE_3, theme.SPACE_3))

        self.tab_unified = self.tabview.add("PC 연결 AI")
        self.tab_analytics = self.tabview.add("차트 · 비용 분석")
        self.tab_logs = self.tabview.add("AI 호출 로그")
        self.tab_byok = self.tabview.add("API 키 관리")
        self.tab_settings = self.tabview.add("환경설정")

        self._init_unified_tab()
        self._init_analytics_tab()
        self._init_logs_tab()
        self._init_byok_tab()
        self._init_settings_tab()

    # =========================================================================
    # TAB 1: ⚡ PC 연결 AI 자동 모니터
    # =========================================================================
    def _init_unified_tab(self) -> None:
        parent = ctk.CTkScrollableFrame(self.tab_unified, fg_color=theme.APP)
        parent.pack(fill="both", expand=True)

        lbl_sec = ctk.CTkLabel(
            parent,
            text="내 PC에 연결된 AI · 남은 사용량과 비용",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.TEXT,
        )
        lbl_sec.pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_HALF))

        self.lbl_sec_desc = ctk.CTkLabel(
            parent,
            text=(
                "모든 숫자에 출처를 표시합니다. 공식 값이 없는 항목은 '확인 불가'로 두고, "
                "지어낸 한도로 %를 계산하지 않습니다. 보기 싫은 AI는 환경설정 탭에서 숨길 수 있습니다."
            ),
            font=theme.font(theme.FONT_SM),
            text_color=theme.FAINT,
        )
        self.lbl_sec_desc.configure(wraplength=960, justify="left")
        self.lbl_sec_desc.pack(anchor="w", padx=theme.SPACE_4, pady=(0, theme.SPACE_2))

        self.ai_card_widgets: Dict[str, Dict[str, Any]] = {}
        tot_card = ctk.CTkFrame(
            parent,
            fg_color=theme.CARD,
            border_color=theme.ACCENT,
            border_width=0,
            corner_radius=theme.RADIUS_MD,
        )
        tot_card.pack(fill="x", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_3))
        tot_row = ctk.CTkFrame(tot_card, fg_color="transparent")
        tot_row.pack(fill="x", padx=theme.SPACE_5, pady=theme.SPACE_3)

        t_left = ctk.CTkFrame(tot_row, fg_color="transparent")
        t_left.pack(side="left")
        self.lbl_total_cost_title = ctk.CTkLabel(
            t_left,
            text="이번 달 실제 청구 (API 종량제)",
            font=theme.font(theme.FONT_SM),
            text_color=theme.ACCENT,
        )
        self.lbl_total_cost_title.pack(anchor="w")
        self.lbl_total_cost_krw = ctk.CTkLabel(
            t_left, text="—", font=theme.font(theme.FONT_METRIC, weight="bold"), text_color=theme.TEXT
        )
        self.lbl_total_cost_krw.pack(anchor="w")
        self.lbl_total_cost_usd = ctk.CTkLabel(
            t_left, text="", font=theme.font(theme.FONT_SM), text_color=theme.FAINT
        )
        self.lbl_total_cost_usd.pack(anchor="w")

        t_right = ctk.CTkFrame(tot_row, fg_color="transparent")
        t_right.pack(side="right")
        self.lbl_total_requests = ctk.CTkLabel(
            t_right,
            text="오늘 —회 요청",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.TEXT,
            anchor="e",
        )
        self.lbl_total_requests.pack(anchor="e")
        self.lbl_total_tokens = ctk.CTkLabel(
            t_right, text="", font=theme.font(theme.FONT_SM), text_color=theme.FAINT, anchor="e"
        )
        self.lbl_total_tokens.pack(anchor="e")

        self.cards_container = ctk.CTkScrollableFrame(
            parent, fg_color=theme.APP, orientation="horizontal", height=470
        )
        self.cards_container.pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_1)
        self._rebuild_unified_cards()

    def _rebuild_unified_cards(self) -> None:
        """One card per detected AI. Quota rows are built per update (their count varies)."""
        for child in self.cards_container.winfo_children():
            child.destroy()
        self.ai_card_widgets.clear()

        if hasattr(self, "lbl_detected_status"):
            self.lbl_detected_status.configure(text=self._format_detected_badge())

        if not self.connected_ais:
            ctk.CTkLabel(
                self.cards_container,
                text="연결된 AI가 감지되지 않았습니다. 상단의 [ PC 연결 AI 스캔]을 눌러주세요.",
                text_color=theme.ERROR,
                font=theme.font(theme.FONT_TITLE, weight="bold"),
            ).pack(pady=theme.SPACE_8)
            return

        hidden = set(hidden_ai_keys(self.config))
        shown = [ai for ai in self.connected_ais if ai["key"] not in hidden]
        if not shown:
            ctk.CTkLabel(
                self.cards_container,
                text="연결된 AI를 모두 숨겼습니다. [환경설정] 탭의 '표시할 AI'에서 다시 켤 수 있습니다.",
                text_color=theme.FAINT,
                font=theme.font(theme.FONT_TITLE, weight="bold"),
            ).pack(pady=theme.SPACE_8)
            return

        for ai in shown:
            key = ai["key"]
            color = theme.FAINT
            card = ctk.CTkFrame(
                self.cards_container,
                fg_color=theme.CARD,
                border_color=color,
                border_width=0,
                corner_radius=theme.RADIUS_MD,
                width=330,
            )
            card.pack(side="left", fill="y", padx=theme.SPACE_2, pady=theme.SPACE_1)

            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_HALF))
            lbl_title = ctk.CTkLabel(
                head,
                text=ai.get("display_name", key),
                font=theme.font(theme.FONT_TITLE, weight="bold"),
                text_color=theme.TEXT,
            )
            lbl_title.pack(side="left")
            lbl_plan = ctk.CTkLabel(
                card, text=ai.get("plan_name", ""), font=theme.font(theme.FONT_XS), text_color=color
            )
            lbl_plan.pack(anchor="w", padx=theme.SPACE_4)
            lbl_session_status = ctk.CTkLabel(
                card, text="", font=theme.font(theme.FONT_XS), text_color=theme.FAINT
            )
            lbl_session_status.pack(anchor="w", padx=theme.SPACE_4, pady=(0, theme.SPACE_1))

            quota_box = ctk.CTkFrame(card, fg_color="transparent")
            quota_box.pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_HALF)

            account_box = ctk.CTkScrollableFrame(card, fg_color="transparent", width=300, height=330)

            ctx_box = ctk.CTkFrame(card, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
            ctx_box.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_1)
            lbl_ctx = ctk.CTkLabel(
                ctx_box,
                text="컨텍스트 (추정): 기록 없음",
                font=theme.font(theme.FONT_XS),
                text_color=theme.MUTED,
            )
            lbl_ctx.pack(anchor="w", padx=theme.SPACE_2, pady=(theme.SPACE_1, 0))
            prog_ctx = ctk.CTkProgressBar(ctx_box, height=6, corner_radius=theme.RADIUS_SM)
            prog_ctx.pack(fill="x", padx=theme.SPACE_2, pady=(theme.SPACE_HALF, theme.SPACE_HALF))
            prog_ctx.set(0)
            lbl_ctx_sub = ctk.CTkLabel(
                ctx_box, text="", font=theme.font(theme.FONT_XS), text_color=theme.FAINT
            )
            lbl_ctx_sub.pack(anchor="w", padx=theme.SPACE_2, pady=(0, theme.SPACE_1))

            cost_box = ctk.CTkFrame(card, fg_color="transparent")
            cost_box.pack(fill="x", padx=theme.SPACE_4, pady=(theme.SPACE_1, theme.SPACE_HALF))
            lbl_cost_today = ctk.CTkLabel(
                cost_box, text="", font=theme.font(theme.FONT_BODY), text_color=theme.TEXT
            )
            lbl_cost_today.pack(anchor="w")
            lbl_cost_month = ctk.CTkLabel(
                cost_box, text="", font=theme.font(theme.FONT_XS), text_color=theme.MUTED
            )
            lbl_cost_month.pack(anchor="w")
            lbl_spend = ctk.CTkLabel(
                cost_box,
                text="",
                font=theme.font(theme.FONT_XS),
                text_color=theme.ACCENT,
                wraplength=300,
                justify="left",
            )
            lbl_spend.pack(anchor="w")
            lbl_billing_note = ctk.CTkLabel(
                cost_box,
                text="",
                font=theme.font(theme.FONT_XS),
                text_color=theme.FAINT,
                wraplength=300,
                justify="left",
            )
            lbl_billing_note.pack(anchor="w", pady=(theme.SPACE_HALF, theme.SPACE_3))

            self.ai_card_widgets[key] = {
                "lbl_title": lbl_title,
                "lbl_plan": lbl_plan,
                "lbl_session_status": lbl_session_status,
                "quota_box": quota_box,
                "account_box": account_box,
                "account_signature": None,
                "ctx_box": ctx_box,
                "cost_box": cost_box,
                "quota_rows": {},
                "quota_ids": [],
                "lbl_ctx": lbl_ctx,
                "prog_ctx": prog_ctx,
                "lbl_ctx_sub": lbl_ctx_sub,
                "lbl_cost_today": lbl_cost_today,
                "lbl_cost_month": lbl_cost_month,
                "lbl_spend": lbl_spend,
                "lbl_billing_note": lbl_billing_note,
            }

    def _render_quota_rows(self, w: Dict[str, Any], windows: List[Dict[str, Any]]) -> None:
        ids = [q["id"] for q in windows]
        if ids != w["quota_ids"]:
            for child in w["quota_box"].winfo_children():
                child.destroy()
            w["quota_rows"] = {}
            for q in windows:
                box = ctk.CTkFrame(w["quota_box"], fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
                box.pack(fill="x", padx=theme.SPACE_1, pady=theme.SPACE_1)
                row = ctk.CTkFrame(box, fg_color="transparent")
                row.pack(fill="x", padx=theme.SPACE_2, pady=(theme.SPACE_2, 0))
                lbl_name = ctk.CTkLabel(
                    row, text=q["label"], font=theme.font(theme.FONT_SM), text_color=theme.TEXT
                )
                lbl_name.pack(side="left")
                lbl_val = ctk.CTkLabel(row, text="", font=theme.font(theme.FONT_TITLE, weight="bold"))
                lbl_val.pack(side="right")
                bar = ctk.CTkProgressBar(box, height=8, corner_radius=theme.RADIUS_SM)
                bar.pack(fill="x", padx=theme.SPACE_2, pady=(theme.SPACE_HALF, 1))
                lbl_detail = ctk.CTkLabel(
                    box,
                    text="",
                    font=theme.font(theme.FONT_XS),
                    text_color=theme.FAINT,
                    wraplength=290,
                    justify="left",
                )
                lbl_detail.pack(anchor="w", padx=theme.SPACE_2, pady=(0, theme.SPACE_2))
                w["quota_rows"][q["id"]] = (lbl_val, bar, lbl_detail)
            w["quota_ids"] = ids
        for q in windows:
            lbl_val, bar, lbl_detail = w["quota_rows"][q["id"]]
            headline, color, detail = window_text(q)
            lbl_val.configure(text=headline, text_color=color)
            rem = q.get("remaining_pct")
            bar.set(0 if rem is None else max(0.0, min(1.0, rem / 100.0)))
            bar.configure(progress_color=color if rem is not None else GREY)
            lbl_detail.configure(text=detail)

    # =========================================================================
    # TAB 2: 📊 실시간 차트 & 비용 분석 (순수 네이티브 Matplotlib 캔버스)
    # =========================================================================
    def _init_analytics_tab(self) -> None:
        parent = self.tab_analytics
        self.chart_account_menu = ctk.CTkOptionMenu(
            parent, values=list(self.account_choices), width=320, command=self._on_chart_account_changed,
        )
        self.chart_account_menu.pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_2, 0))

        lbl_sec = ctk.CTkLabel(
            parent,
            text="AI 모델별 실시간 차트 및 비용/토큰 분석",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.TEXT,
        )
        lbl_sec.pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_1))

        # Embedded Matplotlib Canvas Area (Dark Mode)
        chart_card = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=theme.RADIUS_MD)
        chart_card.pack(fill="x", padx=theme.SPACE_4, pady=(0, theme.SPACE_3))

        # Setup Figure & Axes
        self.fig = Figure(figsize=(9, 2.7), dpi=100, facecolor=theme.color(theme.CARD))
        self.ax_cost = self.fig.add_subplot(121)
        self.ax_tokens = self.fig.add_subplot(122)
        self._style_axis(self.ax_cost, "모델별 소진 비용 (￦)")
        self._style_axis(self.ax_tokens, "모델별 사용 토큰 수")
        self.fig.tight_layout(pad=2.0)

        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_card)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=theme.SPACE_2, pady=theme.SPACE_2)

        # Bottom Breakdown List
        ctk.CTkLabel(
            parent,
            text="상세 모델별 집계 내역",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.ACCENT,
        ).pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_1, theme.SPACE_1))

        self.frame_breakdown = ctk.CTkScrollableFrame(
            parent, fg_color=theme.CARD, height=240, corner_radius=theme.RADIUS_MD
        )
        self.frame_breakdown.pack(fill="both", expand=True, padx=theme.SPACE_4, pady=(0, theme.SPACE_3))

    def _style_axis(self, ax, title: str) -> None:
        """Use the same surface, text and grid tokens as the dashboard."""
        ax.set_facecolor(theme.color(theme.CARD))
        ax.set_title(
            title,
            color=theme.color(theme.TEXT),
            fontsize=theme.CHART_TITLE,
            fontweight="normal",
            pad=theme.SPACE_2,
        )
        ax.tick_params(colors=theme.color(theme.FAINT), labelsize=theme.CHART_TICK, length=0)
        # Keep long KRW tick labels apart at the minimum window width.
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis="x", color=theme.color(theme.GRID), linewidth=0.6)

    def _update_native_charts(self, bd: List[Dict[str, Any]]) -> None:
        """Render live horizontal bar charts for cost and tokens."""
        # Sort and take top 5 models
        top_bd = sorted(bd, key=lambda x: x.get("total_cost_usd", 0.0), reverse=True)[:5]
        labels = [item.get("model_name", "unknown")[:14] for item in top_bd]
        costs_krw = [int(item.get("total_cost_usd", 0.0) * self.krw_rate) for item in top_bd]
        tokens = [item.get("total_tokens", 0) for item in top_bd]
        unknown = [item.get("cost_unknown_count", 0) for item in top_bd]
        counts = [item.get("count", 0) for item in top_bd]

        # Skip expensive matplotlib redraws when nothing moved.
        sig = (theme.appearance_mode(), tuple(labels), tuple(costs_krw), tuple(tokens),
               tuple(unknown), tuple(counts))
        if sig == self._chart_sig:
            return
        self._chart_sig = sig

        self.fig.set_facecolor(theme.color(theme.CARD))
        self.ax_cost.clear()
        self.ax_tokens.clear()
        self._style_axis(self.ax_cost, "모델별 소진 비용 (￦)")
        self._style_axis(self.ax_tokens, "모델별 사용 토큰 수")

        if not bd:
            self.ax_cost.text(
                0.5, 0.5, "데이터 수집 대기 중", color=theme.color(theme.FAINT), ha="center", va="center"
            )
            self.ax_tokens.text(
                0.5, 0.5, "데이터 수집 대기 중", color=theme.color(theme.FAINT), ha="center", va="center"
            )
            self.canvas.draw_idle()
            return

        colors_cost = theme.color(theme.ACCENT)
        colors_tok = theme.color(theme.SECONDARY)

        y_pos = range(len(labels))
        known_rows = [i for i in y_pos if not unknown[i] or unknown[i] < counts[i]]
        self.ax_cost.barh(known_rows, [costs_krw[i] for i in known_rows], color=colors_cost, height=0.55)
        for i in y_pos:
            if unknown[i]:
                note = "계산 불가" if unknown[i] >= counts[i] else f" + {unknown[i]}건 계산 불가"
                self.ax_cost.text(costs_krw[i], i, note, color=theme.color(theme.WARNING),
                                  va="center", fontsize=theme.CHART_TICK)
        self.ax_cost.set_yticks(y_pos)
        self.ax_cost.set_yticklabels(labels, color=theme.color(theme.MUTED), fontsize=theme.CHART_TICK)
        self.ax_cost.invert_yaxis()

        self.ax_tokens.barh(y_pos, tokens, color=colors_tok, height=0.55)
        self.ax_tokens.set_yticks(y_pos)
        self.ax_tokens.set_yticklabels(labels, color=theme.color(theme.MUTED), fontsize=theme.CHART_TICK)
        self.ax_tokens.invert_yaxis()

        self.fig.tight_layout(pad=2.0)
        self.canvas.draw_idle()

    # =========================================================================
    # TAB 3: 📋 실시간 AI 로그 & 상세 분석
    # =========================================================================
    def _init_logs_tab(self) -> None:
        parent = self.tab_logs

        # Filter bar
        filt_card = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=theme.RADIUS_MD)
        filt_card.pack(fill="x", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_2))
        self.log_account_menu = ctk.CTkOptionMenu(
            filt_card, values=list(self.account_choices), width=320, command=self._on_log_account_changed,
        )
        self.log_account_menu.pack(anchor="w", padx=theme.SPACE_3, pady=(theme.SPACE_2, 0))

        f_row = ctk.CTkFrame(filt_card, fg_color="transparent")
        f_row.pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_3)

        ctk.CTkLabel(f_row, text="프로바이더:", font=theme.font(theme.FONT_SM), text_color=theme.TEXT).pack(
            side="left", padx=(0, theme.SPACE_2)
        )
        self.opt_filter_prov = ctk.CTkOptionMenu(
            f_row,
            values=["전체", "anthropic", "google", "openai", "copilot", "openrouter"],
            width=120,
            height=30,
            command=self._on_filter_changed,
        )
        self.opt_filter_prov.pack(side="left", padx=(0, theme.SPACE_4))

        ctk.CTkLabel(f_row, text="상태:", font=theme.font(theme.FONT_SM), text_color=theme.TEXT).pack(
            side="left", padx=(0, theme.SPACE_2)
        )
        self.opt_filter_stat = ctk.CTkOptionMenu(
            f_row,
            values=["전체", "SUCCESS", "ERROR"],
            width=110,
            height=30,
            command=self._on_filter_changed,
        )
        self.opt_filter_stat.pack(side="left", padx=(0, theme.SPACE_4))

        self.entry_search_logs = ctk.CTkEntry(
            f_row, placeholder_text="모델명 또는 Trace ID 검색...", width=220, height=30
        )
        self.entry_search_logs.pack(side="left", padx=(0, theme.SPACE_2))
        self.entry_search_logs.bind("<KeyRelease>", self._on_search_key)

        btn_search_clear = ctk.CTkButton(
            f_row,
            text="초기화",
            width=70,
            height=30,
            fg_color=theme.HOVER,
            hover_color=theme.GRID,
            command=self._clear_log_filters,
            text_color=theme.TEXT,
        )
        btn_search_clear.pack(side="left")

        # Live Scrollable Log View
        self.logs_scroll = ctk.CTkScrollableFrame(parent, fg_color=theme.CARD, corner_radius=theme.RADIUS_MD)
        self.logs_scroll.pack(fill="both", expand=True, padx=theme.SPACE_4, pady=(0, theme.SPACE_3))
        self.lbl_logs_empty = ctk.CTkLabel(self.logs_scroll, text="", text_color=theme.FAINT)

    def _on_search_key(self, _event=None) -> None:
        # Filter once typing pauses, not on every keystroke.
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, self._run_search)

    def _run_search(self) -> None:
        self._search_job = None
        self._on_filter_changed()

    def _clear_log_filters(self) -> None:
        self.log_account_menu.set("전체")
        self._on_log_account_changed("전체")
        self.opt_filter_prov.set("전체")
        self.opt_filter_stat.set("전체")
        self.entry_search_logs.delete(0, "end")
        self._on_filter_changed()

    def _on_filter_changed(self, _=None) -> None:
        self.log_filter_provider = self.opt_filter_prov.get()
        self.log_filter_status = self.opt_filter_stat.get()
        self.log_filter_search = self.entry_search_logs.get().strip().lower()
        self._render_filtered_logs()

    def _on_log_account_changed(self, selection: str) -> None:
        self.log_account_selection = selection
        self.traces_data = []
        self._render_filtered_logs()
        self.refresh_data()

    def _on_chart_account_changed(self, selection: str) -> None:
        self.chart_account_selection = selection
        self._update_native_charts([])
        self._render_breakdown([])
        self.refresh_data()

    def _refresh_account_choices(self) -> None:
        choices = account_options(self.account_rows)
        # Include IDs found in traces even when this desktop has no registered folder.
        for trace in self.traces_data:
            aid = trace.get("account_id")
            if aid and aid not in choices.values():
                choices[f"{account_label(aid, self.account_rows)} · {aid}"] = aid
        old = self.account_choices
        self.account_choices = choices
        for menu, attr in ((self.log_account_menu, "log_account_selection"),
                           (self.chart_account_menu, "chart_account_selection")):
            selection = getattr(self, attr)
            aid = old.get(selection)
            if aid:
                selection = next((label for label, value in choices.items() if value == aid), "전체")
            menu.configure(values=list(choices))
            menu.set(selection)
            setattr(self, attr, selection)

    def _open_trace_detail_dialog(self, t: Dict[str, Any]) -> None:
        """Open detailed modal inspection dialog for a single trace."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("호출 상세 검사기 (Trace Inspector)")
        dialog.geometry("540x480")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="AI 호출 상세 기록",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.ACCENT,
        ).pack(pady=(theme.SPACE_4, theme.SPACE_2))

        box = ctk.CTkFrame(dialog, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        box.pack(fill="both", expand=True, padx=theme.SPACE_5, pady=(theme.SPACE_1, theme.SPACE_4))

        fields = [
            ("Trace ID", t.get("trace_id", "N/A")),
            ("시각 (Timestamp)", t.get("timestamp", "N/A")),
            ("프로바이더 (Provider)", t.get("provider", "N/A")),
            ("모델명 (Model)", t.get("model_name", "N/A")),
            ("상태 (Status)", t.get("status", "SUCCESS")),
            (
                "지연 시간 (Latency)",
                f"{t.get('latency_ms', 0):.1f} ms"
                if t.get("latency_measured", True)
                else "측정값 없음 (로그에 기록 안 됨)",
            ),
            ("총 토큰 (Total Tokens)", f"{t.get('total_tokens', 0):,} 토큰"),
            ("입력 토큰 (Prompt)", f"{t.get('prompt_tokens', 0):,} 토큰"),
            ("출력 토큰 (Completion)", f"{t.get('completion_tokens', 0):,} 토큰"),
            ("캐시 읽기 (Cache Read)", f"{t.get('cache_read_tokens', 0):,} 토큰"),
            (
                "소요 비용 (USD)",
                f"${t.get('cost_usd', 0.0):.6f}"
                if t.get("cost_source") != "unknown"
                else "계산 불가 (단가 정보 없음)",
            ),
            (
                "소요 비용 (KRW)",
                f"￦ {int(round(t.get('cost_usd', 0.0) * self.krw_rate)):,} 원"
                if t.get("cost_source") != "unknown"
                else "계산 불가",
            ),
            ("비용 계산 소스", t.get("cost_source", "catalog")),
        ]

        if t.get("error_message"):
            fields.append(("에러 메시지", t.get("error_message")))

        for label, val in fields:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_1)
            ctk.CTkLabel(
                row,
                text=f"{label}:",
                font=theme.font(theme.FONT_SM),
                text_color=theme.FAINT,
                width=160,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=str(val), font=theme.font(theme.FONT_SM), text_color=theme.TEXT, anchor="w"
            ).pack(side="left")

        ctk.CTkButton(dialog, text="닫기", width=100, height=32, command=dialog.destroy).pack(
            pady=(0, theme.SPACE_4)
        )

    # =========================================================================
    # TAB 4: 🔑 BYOK API 키 관리 센터 (종량제 사용자 전용)
    # =========================================================================
    def _init_byok_tab(self) -> None:
        parent = self.tab_byok

        lbl_sec = ctk.CTkLabel(
            parent,
            text="종량제 API 키 등록 및 실시간 추적 센터 (BYOK)",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.TEXT,
        )
        lbl_sec.pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_1))

        desc = ctk.CTkLabel(
            parent,
            text=(
                "OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq 등 종량제 API 키를 등록하면 "
                "실시간 잔액 및 사용량을 안전하게 모니터링합니다. "
                "(Windows에서는 키를 로컬 PC ~/.aimonitor에 DPAPI로 암호화하여 보관합니다)"
            ),
            font=theme.font(theme.FONT_SM),
            text_color=theme.FAINT,
            wraplength=950,
            justify="left",
        )
        desc.pack(anchor="w", padx=theme.SPACE_4, pady=(0, theme.SPACE_3))

        # Add Key Card
        add_card = ctk.CTkFrame(parent, fg_color=theme.CARD, corner_radius=theme.RADIUS_MD)
        add_card.pack(fill="x", padx=theme.SPACE_4, pady=(0, theme.SPACE_3))

        ctk.CTkLabel(
            add_card,
            text="새로운 API 키 등록",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.ACCENT,
        ).pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_2))

        r_form = ctk.CTkFrame(add_card, fg_color="transparent")
        r_form.pack(fill="x", padx=theme.SPACE_4, pady=(0, theme.SPACE_4))

        ctk.CTkLabel(r_form, text="프로바이더:", font=theme.font(theme.FONT_SM), text_color=theme.TEXT).pack(
            side="left", padx=(0, theme.SPACE_2)
        )
        self.byok_opt_prov = ctk.CTkOptionMenu(r_form, values=list(BYOK_PROVIDERS), width=210, height=34)
        self.byok_opt_prov.pack(side="left", padx=(0, theme.SPACE_3))

        self.byok_entry_label = ctk.CTkEntry(r_form, placeholder_text="키 라벨 (40자 이내)", width=150)
        self.byok_entry_label.pack(side="left", padx=(0, theme.SPACE_2))

        self.byok_entry_key = ctk.CTkEntry(
            r_form, placeholder_text="API 키 입력", width=230, height=34, show="•"
        )
        self.byok_entry_key.pack(side="left", padx=(0, theme.SPACE_2))

        self.byok_show_var = ctk.BooleanVar(value=False)
        self.btn_toggle_eye = ctk.CTkButton(
            r_form,
            text="보기",
            width=60,
            height=34,
            fg_color=theme.HOVER,
            hover_color=theme.GRID,
            command=self._toggle_key_visibility,
            text_color=theme.TEXT,
        )
        self.btn_toggle_eye.pack(side="left", padx=(0, theme.SPACE_3))

        btn_save_key = ctk.CTkButton(
            r_form,
            text="키 추가",
            width=100,
            height=34,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            font=theme.font(theme.FONT_BODY),
            command=self._save_api_key_dedicated,
        )
        btn_save_key.pack(side="left")

        # Registered Keys Container
        ctk.CTkLabel(
            parent,
            text="등록된 API 키 현황 및 실시간 잔액",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.SUCCESS,
        ).pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_1, theme.SPACE_1))

        self.byok_keys_scroll = ctk.CTkScrollableFrame(
            parent, fg_color=theme.CARD, corner_radius=theme.RADIUS_MD
        )
        self.byok_keys_scroll.pack(fill="both", expand=True, padx=theme.SPACE_4, pady=(0, theme.SPACE_3))

        self._refresh_keys_list()

    def _toggle_key_visibility(self) -> None:
        curr = self.byok_show_var.get()
        new_val = not curr
        self.byok_show_var.set(new_val)
        if new_val:
            self.byok_entry_key.configure(show="")
            self.btn_toggle_eye.configure(text="🔒 숨기기")
        else:
            self.byok_entry_key.configure(show="•")
            self.btn_toggle_eye.configure(text="보기")

    def _save_api_key_dedicated(self) -> None:
        prov = BYOK_PROVIDERS.get(self.byok_opt_prov.get(), "openai")
        val = self.byok_entry_key.get().strip()
        if not val:
            self.lbl_status_msg.configure(text="API 키를 입력해주세요.", text_color=theme.ERROR)
            return

        try:
            if hasattr(self, "byok_entry_label"):
                api_key_manager.add_key(prov, val, self.byok_entry_label.get().strip() or "기본")
                self.byok_entry_label.delete(0, "end")
                ver = {}
            else:
                # Older embedders used the single-key form.
                api_key_manager.set_key(prov, val)
                ver = api_key_manager.verify_key(prov)
            self.byok_entry_key.delete(0, "end")
            msg = f"✓ {prov.upper()} API 키가 등록되었습니다."
            if ver.get("valid"):
                msg += " (검증 완료)"
            self.lbl_status_msg.configure(text=msg, text_color=theme.SUCCESS)
            self._refresh_keys_list()
            self._refresh_diagnostic_table()
            self.refresh_data()
        except Exception as e:
            # The exception can quote its input; never echo a key back to the screen.
            self.lbl_status_msg.configure(text=f"키 저장 실패 ({type(e).__name__})", text_color=theme.ERROR)

    def _refresh_keys_list(self) -> None:
        """Only masked metadata reaches widgets; bills are joined by provider and key ID."""
        if getattr(self, "_key_verifying", None):
            return  # Polling must not replace an in-flight row with an enabled button.
        for child in self.byok_keys_scroll.winfo_children():
            child.destroy()
        spend = {(s["provider"], s["key_id"]): s
                 for s in self.official_spend.get("items", [])}
        for provider in api_key_manager.list_keys():
            keys = provider.get("keys") or []
            if not keys:
                continue
            ctk.CTkLabel(self.byok_keys_scroll, text=provider["name"],
                         font=theme.font(theme.FONT_TITLE, weight="bold"),
                         text_color=theme.TEXT).pack(anchor="w", padx=theme.SPACE_3, pady=theme.SPACE_2)
            for key in keys:
                prov, kid = key["provider"], key["key_id"]
                card = ctk.CTkFrame(self.byok_keys_scroll, fg_color=theme.SUBTLE)
                card.pack(fill="x", padx=theme.SPACE_2, pady=theme.SPACE_1)
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_2)
                label = ctk.CTkEntry(row, width=160)
                label.insert(0, key["label"])
                label.pack(side="left", padx=(0, theme.SPACE_2))
                ctk.CTkLabel(row, text=key.get("masked") or key.get("masked_key") or "마스킹 없음",
                             font=theme.font(theme.FONT_SM, family=theme.FONT_MONO),
                             text_color=theme.ACCENT, width=170).pack(side="left")
                status = ctk.CTkLabel(row, text=KEY_STATUS_TEXT.get(key.get("status"), "알 수 없음"),
                                      text_color=theme.MUTED, width=100)
                status.pack(side="left")
                verify = ctk.CTkButton(row, text="검증", width=60)
                verify.configure(command=lambda p=prov, k=kid, b=verify, s=status:
                                 self._verify_key_action(p, k, b, s),
                                 state="normal" if key.get("has_key") else "disabled")
                verify.pack(side="right", padx=theme.SPACE_1)
                bill = spend.get((prov, kid))
                bill_text = (spend_text(bill, self.krw_rate) if bill and bill.get("amount") is not None
                             else "청구액 확인 불가")
                ctk.CTkLabel(card, text=bill_text, text_color=theme.MUTED,
                             font=theme.font(theme.FONT_SM)).pack(anchor="w", padx=theme.SPACE_3)
                if key.get("source") != "env":
                    ctk.CTkButton(row, text="라벨 저장", width=80,
                                  command=lambda p=prov, k=kid, e=label:
                                  self._rename_key_action(p, k, e.get())).pack(
                                      side="right", padx=theme.SPACE_1)
                    ctk.CTkButton(row, text="삭제", width=60, fg_color=theme.ERROR_SOFT,
                                  text_color=theme.ERROR,
                                  command=lambda p=prov, k=kid: self._delete_key_action(p, k)).pack(
                                      side="right")
                else:
                    label.configure(state="disabled")
                    ctk.CTkLabel(row, text="환경변수 · 읽기 전용", text_color=theme.FAINT).pack(side="right")
        if not self.byok_keys_scroll.winfo_children():
            ctk.CTkLabel(self.byok_keys_scroll, text="등록된 API 키가 없습니다.",
                         text_color=theme.FAINT).pack(pady=theme.SPACE_4)

    def _verify_key_action(self, provider: str, key_id: str, button: Any, status: Any) -> None:
        busy = getattr(self, "_key_verifying", None)
        if busy is None:
            self._key_verifying = busy = set()
        identity = (provider, key_id)
        if identity in busy:
            return
        busy.add(identity)
        button.configure(state="disabled")
        status.configure(text="검증 중…")

        def worker():
            state = "verification_unavailable"
            try:
                result = api_key_manager.verify_key(provider, key_id)
                if result.get("valid") is True:
                    state = "valid"
                elif str(result.get("error", "")).startswith(("HTTP 401", "HTTP 403", "인증 실패")):
                    state = "invalid"
            except Exception:
                pass

            def finish():
                busy.discard(identity)
                try:
                    status.configure(text=KEY_STATUS_TEXT[state])
                    button.configure(state="normal")
                except Exception:
                    pass  # The row/window may have been destroyed while verifying.
            try:
                self.after(0, finish)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True, name="KeyVerification").start()

    def _rename_key_action(self, provider: str, key_id: str, label: str) -> None:
        try:
            result = api_key_manager.update_label(provider, key_id, label.strip())
            if result is None:
                raise ValueError("missing key")
            self.lbl_status_msg.configure(text="키 라벨을 변경했습니다.", text_color=theme.SUCCESS)
            self._refresh_keys_list()
        except Exception:
            self.lbl_status_msg.configure(text="라벨 변경 실패 · 40자 이내, 이메일·키 원문 제외",
                                          text_color=theme.ERROR)

    def _delete_key_action(self, prov: str, key_id: Optional[str] = None) -> None:
        if key_id is None:
            api_key_manager.delete_key(prov)
        else:
            api_key_manager.delete_key(prov, key_id)
        self.lbl_status_msg.configure(text=f"{prov.upper()} 키가 삭제되었습니다.", text_color=theme.ACCENT)
        self._refresh_keys_list()
        self._refresh_diagnostic_table()
        self.refresh_data()

    # =========================================================================
    # TAB 5: ⚙️ 시스템 진단 & 환경설정
    # =========================================================================
    def _init_accounts_settings(self, parent: Any) -> None:
        section = ctk.CTkFrame(parent, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        section.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_3)
        ctk.CTkLabel(section, text="계정", font=theme.font(theme.FONT_TITLE, weight="bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=theme.SPACE_3, pady=theme.SPACE_2)
        ctk.CTkLabel(section,
                     text="계정 전환 전 기록이나 앱이 꺼진 사이 전환된 기록은 ‘계정 미상’으로 남습니다.\n"
                     "라벨에는 이메일·토큰을 넣지 마세요. 수동 폴더 삭제는 사용 기록을 지우지 않습니다.",
                     justify="left", text_color=theme.FAINT,
                     font=theme.font(theme.FONT_XS)).pack(anchor="w", padx=theme.SPACE_3)
        form = ctk.CTkFrame(section, fg_color="transparent")
        form.pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_2)
        self.account_tool_menu = ctk.CTkOptionMenu(form, values=["claude_code", "codex"], width=140)
        self.account_tool_menu.pack(side="left", padx=(0, theme.SPACE_2))
        self.account_folder_entry = ctk.CTkEntry(form, placeholder_text="수동 설정 폴더 절대 경로", width=260)
        self.account_folder_entry.pack(side="left", padx=(0, theme.SPACE_2))
        ctk.CTkButton(form, text="폴더 선택", width=80, command=self._choose_account_folder).pack(side="left")
        self.account_label_entry = ctk.CTkEntry(form, placeholder_text="라벨 (선택)", width=150)
        self.account_label_entry.pack(side="left", padx=theme.SPACE_2)
        ctk.CTkButton(form, text="폴더 추가", width=90, command=self._add_account_folder).pack(side="left")
        self.accounts_list_frame = ctk.CTkFrame(section, fg_color="transparent")
        self.accounts_list_frame.pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_2)
        self._refresh_accounts_list()

    def _choose_account_folder(self) -> None:
        directory = filedialog.askdirectory(parent=self, title="AI 설정 폴더 선택")
        if directory:
            self.account_folder_entry.delete(0, "end")
            self.account_folder_entry.insert(0, directory)

    def _refresh_accounts_list(self) -> None:
        self.account_rows = accounts.list_accounts()
        for child in self.accounts_list_frame.winfo_children():
            child.destroy()
        for account in sorted(self.account_rows, key=lambda a: (a["tool"], a["label"])):
            row = ctk.CTkFrame(self.accounts_list_frame, fg_color=theme.CARD)
            row.pack(fill="x", pady=theme.SPACE_1)
            head = ctk.CTkFrame(row, fg_color="transparent")
            head.pack(fill="x", padx=theme.SPACE_2, pady=theme.SPACE_1)
            entry = ctk.CTkEntry(head, width=160)
            entry.insert(0, account["label"])
            entry.pack(side="left", padx=(0, theme.SPACE_2))
            source = "수동" if account["source"] == "manual" else "자동"
            observed = account.get("last_seen")
            try:
                observed = datetime.fromisoformat(observed).astimezone().strftime("%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                observed = "확인 불가"
            ctk.CTkLabel(head, text=f"{account['tool']} · {source} · 마지막 관측 {observed}",
                         text_color=theme.MUTED, font=theme.font(theme.FONT_XS)).pack(side="left")
            ctk.CTkButton(head, text="라벨 저장", width=80,
                          command=lambda a=account["account_id"], e=entry:
                          self._rename_account(a, e.get())).pack(side="right", padx=theme.SPACE_1)
            if account["source"] == "manual":
                ctk.CTkButton(head, text="폴더 삭제", width=80, fg_color=theme.ERROR_SOFT,
                              text_color=theme.ERROR, command=lambda p=account["config_dir"]:
                              self._remove_account_folder(p)).pack(side="right")
            ctk.CTkLabel(row, text=account["config_dir"], text_color=theme.FAINT,
                         font=theme.font(theme.FONT_XS), wraplength=900, justify="left").pack(
                             anchor="w", padx=theme.SPACE_2, pady=(0, theme.SPACE_1))
        if not self.account_rows:
            ctk.CTkLabel(self.accounts_list_frame,
                         text="감지된 계정이 없습니다. 수집 후 새로고침하거나 폴더를 추가하세요.",
                         text_color=theme.FAINT).pack(pady=theme.SPACE_2)
        if hasattr(self, "log_account_menu"):
            self._refresh_account_choices()

    def _account_changed(self) -> None:
        self._refresh_accounts_list()
        self.connected_ais = account_tools(detect_connected_ais(), accounts.list_accounts())
        self.refresh_data()
        if self.on_visibility_changed:
            self.on_visibility_changed()

    def _rename_account(self, account_id: str, label: str) -> None:
        try:
            accounts.rename_account(account_id, label.strip())
            self._account_changed()
        except Exception:
            self.lbl_status_msg.configure(text="계정 라벨 변경 실패 · 이메일·토큰을 제외하세요.",
                                          text_color=theme.ERROR)

    def _add_account_folder(self) -> None:
        try:
            path = Path(self.account_folder_entry.get().strip())
            if not path.is_absolute():
                raise ValueError("absolute path required")
            accounts.add_folder(self.account_tool_menu.get(), path,
                                self.account_label_entry.get().strip() or None)
            self.account_folder_entry.delete(0, "end")
            self.account_label_entry.delete(0, "end")
            self._account_changed()
        except Exception:
            self.lbl_status_msg.configure(text="폴더 추가 실패 · 경로와 라벨을 확인하세요.",
                                          text_color=theme.ERROR)

    def _remove_account_folder(self, path: str) -> None:
        try:
            accounts.remove_folder(Path(path))
            self._account_changed()
        except Exception:
            self.lbl_status_msg.configure(text="수동 폴더 삭제 실패", text_color=theme.ERROR)

    def _init_settings_tab(self) -> None:
        parent = self.tab_settings

        lbl_sec = ctk.CTkLabel(
            parent,
            text="PC AI 자동 감지 진단표 & 환경 설정",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.TEXT,
        )
        lbl_sec.pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_1))

        s_scroll = ctk.CTkScrollableFrame(parent, fg_color=theme.CARD, corner_radius=theme.RADIUS_MD)
        s_scroll.pack(fill="both", expand=True, padx=theme.SPACE_4, pady=(0, theme.SPACE_3))
        self._init_accounts_settings(s_scroll)

        startup = ctk.CTkFrame(s_scroll, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        startup.pack(fill="x", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_2))
        startup_inner = ctk.CTkFrame(startup, fg_color="transparent")
        startup_inner.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_3)
        self.autostart_var = ctk.BooleanVar(value=False)
        self.autostart_switch = ctk.CTkSwitch(
            startup_inner, text="Windows 시작 시 자동 실행", variable=self.autostart_var,
            command=self._toggle_autostart, font=theme.font(theme.FONT_BODY), text_color=theme.TEXT,
            progress_color=theme.ACCENT, button_color=theme.ACCENT_ON, fg_color=theme.CONTROL,
        )
        self.autostart_switch.pack(anchor="w")
        self.lbl_autostart_status = ctk.CTkLabel(
            startup_inner, text="", font=theme.font(theme.FONT_SM), text_color=theme.MUTED,
        )
        self.lbl_autostart_status.pack(anchor="w", pady=(theme.SPACE_1, 0))
        ctk.CTkLabel(
            startup_inner, text="로그인하면 위젯과 트레이로 시작합니다. 메인 창은 트레이에서 열 수 있습니다.",
            font=theme.font(theme.FONT_XS), text_color=theme.FAINT,
        ).pack(anchor="w")
        self._refresh_autostart_status()

        # 1. Exchange Rate Row (100% Automatic)
        r1 = ctk.CTkFrame(s_scroll, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        r1.pack(fill="x", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_2))

        r1_inner = ctk.CTkFrame(r1, fg_color="transparent")
        r1_inner.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_3)

        ctk.CTkLabel(
            r1_inner, text="실시간 금융 환율:", font=theme.font(theme.FONT_BODY), text_color=theme.ACCENT
        ).pack(side="left")
        self.lbl_rate_live = ctk.CTkLabel(
            r1_inner,
            text=self.rate_updater.describe(),
            font=theme.font(theme.FONT_BODY),
            text_color=theme.SUCCESS,
        )
        self.lbl_rate_live.pack(side="left", padx=theme.SPACE_3)

        self.btn_rate_refresh = ctk.CTkButton(
            r1_inner,
            text="환율 즉시 갱신",
            width=120,
            height=30,
            fg_color=theme.HOVER,
            hover_color=theme.GRID,
            command=self._manual_refresh_rate,
            text_color=theme.TEXT,
        )
        self.btn_rate_refresh.pack(side="right")

        # 2. Where each remaining-usage number comes from
        r2 = ctk.CTkFrame(s_scroll, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        r2.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_2)
        r2_inner = ctk.CTkFrame(r2, fg_color="transparent")
        r2_inner.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_3)
        ctk.CTkLabel(
            r2_inner, text="남은 사용량의 출처", font=theme.font(theme.FONT_BODY), text_color=theme.SUCCESS
        ).pack(anchor="w")
        ctk.CTkLabel(
            r2_inner,
            text=(
                "• Claude: Anthropic 공식 사용량 API (5시간·주간)\n"
                "• Codex CLI: Codex가 세션 로그에 남기는 공식 한도 (마지막 요청 시점)\n"
                "• Copilot: 이 PC 로그의 이번 달 프리미엄 요청 수 · "
                "남은 %는 아래에 플랜 한도를 입력해야 계산\n"
                "• Antigravity / Cursor / Windsurf: PC에 기록이 없어 '확인 불가'"
            ),
            font=theme.font(theme.FONT_SM),
            text_color=theme.MUTED,
            justify="left",
        ).pack(anchor="w", pady=(theme.SPACE_1, theme.SPACE_2))

        # 3. The one limit the user may type in
        r3 = ctk.CTkFrame(s_scroll, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        r3.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_2)
        r3_inner = ctk.CTkFrame(r3, fg_color="transparent")
        r3_inner.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_3)
        ctk.CTkLabel(
            r3_inner,
            text="Copilot 월 프리미엄 요청 한도:",
            font=theme.font(theme.FONT_BODY),
            text_color=theme.ACCENT,
        ).pack(side="left")
        self.entry_copilot_limit = ctk.CTkEntry(r3_inner, width=90, placeholder_text="예: 300")
        cur = self.config.get("copilot_monthly_premium_limit")
        if cur:
            self.entry_copilot_limit.insert(0, str(cur))
        self.entry_copilot_limit.pack(side="left", padx=theme.SPACE_3)
        ctk.CTkButton(
            r3_inner,
            text="저장",
            width=60,
            height=28,
            fg_color=theme.HOVER,
            hover_color=theme.GRID,
            command=self._save_copilot_limit,
            text_color=theme.TEXT,
        ).pack(side="left")
        ctk.CTkLabel(
            r3_inner,
            text="비우면 '확인 불가'로 표시 (GitHub 플랜 페이지의 값을 입력)",
            font=theme.font(theme.FONT_XS),
            text_color=theme.FAINT,
        ).pack(side="left", padx=theme.SPACE_3)

        # 4. Which connected AIs to show
        r4 = ctk.CTkFrame(s_scroll, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_MD)
        r4.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_2)
        r4_inner = ctk.CTkFrame(r4, fg_color="transparent")
        r4_inner.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_3)
        ctk.CTkLabel(
            r4_inner, text="표시할 AI", font=theme.font(theme.FONT_BODY), text_color=theme.ACCENT
        ).pack(anchor="w")
        ctk.CTkLabel(
            r4_inner,
            text=(
                "체크를 끄면 그 AI는 카드·위젯·합계에서 빠집니다. "
                "로그 수집은 계속되므로 다시 켜면 그대로 보입니다."
            ),
            font=theme.font(theme.FONT_XS),
            text_color=theme.FAINT,
        ).pack(anchor="w", pady=(theme.SPACE_HALF, theme.SPACE_2))
        self.visibility_frame = ctk.CTkFrame(r4_inner, fg_color="transparent")
        self.visibility_frame.pack(fill="x")
        self._visibility_vars: Dict[str, ctk.BooleanVar] = {}
        self._refresh_visibility_list()

        # 5. Diagnostic Table of All AI Services on this PC
        ctk.CTkLabel(
            s_scroll,
            text="PC AI 서비스 자동 진단 현황 (설치 및 구독 여부):",
            font=theme.font(theme.FONT_TITLE, weight="bold"),
            text_color=theme.ACCENT,
        ).pack(anchor="w", padx=theme.SPACE_4, pady=(theme.SPACE_3, theme.SPACE_2))

        self.diag_frame = ctk.CTkFrame(s_scroll, fg_color="transparent")
        self.diag_frame.pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_1)
        self._refresh_diagnostic_table()

    def _on_tab_changed(self) -> None:
        if self.tabview.get() == "환경설정":
            self._refresh_autostart_status()
            self._refresh_accounts_list()

    def _refresh_autostart_status(self) -> None:
        try:
            status = autostart.get_status()
        except OSError:
            logger.warning("Cannot read Windows startup registration", exc_info=True)
            self.autostart_var.set(False)
            self.autostart_switch.configure(state="disabled")
            self.lbl_autostart_status.configure(
                text="상태 확인 실패 · 레지스트리 접근 권한을 확인하세요.", text_color=theme.ERROR,
            )
            self.lbl_status_msg.configure(
                text="자동 실행 상태를 확인하지 못했습니다.", text_color=theme.ERROR,
            )
            return
        self.autostart_var.set(status == autostart.ENABLED)
        self.autostart_switch.configure(state="disabled" if status == autostart.UNSUPPORTED else "normal")
        messages = {
            autostart.ENABLED: "켜짐 · 현재 위치의 AIMONITOR로 시작합니다.",
            autostart.DISABLED: "꺼짐 · Windows에 자동 실행이 등록되어 있지 않습니다.",
            autostart.OTHER_LOCATION: "다른 위치를 가리킴 · 켜면 현재 위치로 교체합니다.",
            autostart.UNSUPPORTED: "사용 불가 · Windows에서만 지원합니다.",
        }
        self.lbl_autostart_status.configure(
            text=messages[status],
            text_color=theme.WARNING if status == autostart.OTHER_LOCATION else theme.MUTED,
        )

    def _toggle_autostart(self) -> None:
        try:
            if self.autostart_var.get():
                autostart.enable()
            else:
                autostart.disable()
        except OSError:
            logger.warning("Cannot update Windows startup registration", exc_info=True)
            self._refresh_autostart_status()
            message = "자동 실행 변경 실패 · 레지스트리 접근 권한을 확인한 뒤 다시 시도하세요."
            self.lbl_autostart_status.configure(text=message, text_color=theme.ERROR)
            self.lbl_status_msg.configure(text=message, text_color=theme.ERROR)
            return
        self._refresh_autostart_status()
        self.lbl_status_msg.configure(text="자동 실행 상태를 확인했습니다.", text_color=theme.SUCCESS)

    def _refresh_visibility_list(self) -> None:
        """One checkbox per connected AI; checked = shown."""
        for child in self.visibility_frame.winfo_children():
            child.destroy()
        self._visibility_vars = {}
        if not self.connected_ais:
            ctk.CTkLabel(self.visibility_frame, text="연결된 AI가 없습니다.", text_color=theme.FAINT).pack(
                anchor="w"
            )
            return
        hidden = set(hidden_ai_keys(self.config))
        for ai in self.connected_ais:
            key = ai["key"]
            var = ctk.BooleanVar(value=key not in hidden)
            self._visibility_vars[key] = var
            ctk.CTkCheckBox(
                self.visibility_frame,
                text=ai.get("display_name", key),
                variable=var,
                command=lambda k=key: self._toggle_ai_visibility(k),
            ).pack(side="left", padx=(0, 18), pady=theme.SPACE_HALF)

    def _sync_visibility_checkboxes(self) -> None:
        """Reflect a hide/show made elsewhere (the widget menu) in the checkboxes."""
        hidden = set(hidden_ai_keys(self.config))
        for key, var in self._visibility_vars.items():
            if var.get() == (key in hidden):
                var.set(key not in hidden)

    def _toggle_ai_visibility(self, key: str) -> None:
        def change(cfg: Dict[str, Any]) -> None:
            hidden = [k for k in hidden_ai_keys(cfg) if k != key]
            if not self._visibility_vars[key].get():
                hidden.append(key)
            cfg["hidden_ais"] = hidden
        try:
            self.config = update_config(change)
        except Exception:
            self.lbl_status_msg.configure(text="설정을 저장하지 못했습니다.", text_color=theme.ERROR)
            return
        hidden = hidden_ai_keys(self.config)
        name = next((a.get("display_name", key) for a in self.connected_ais if a["key"] == key), key)
        state = "표시" if key not in hidden else "숨김"
        self.lbl_status_msg.configure(text=f"{name}: {state}", text_color=theme.SUCCESS)
        self._rebuild_unified_cards()
        self.refresh_data()
        if self.on_visibility_changed:
            self.on_visibility_changed()

    def _save_copilot_limit(self) -> None:
        raw = self.entry_copilot_limit.get().strip()
        try:
            value = float(raw) if raw else None
            if value is not None and (not math.isfinite(value) or not 0 < value <= 1000000000):
                raise ValueError
        except ValueError:
            self.lbl_status_msg.configure(text="한도는 0 초과 10억 이하의 유한한 수로 입력하세요.",
                                          text_color=theme.ERROR)
            return
        try:
            self.config = update_config({"copilot_monthly_premium_limit": value})
        except Exception:
            self.lbl_status_msg.configure(text="설정을 저장하지 못했습니다.", text_color=theme.ERROR)
            return
        self.lbl_status_msg.configure(text="Copilot 한도를 저장했습니다.", text_color=theme.SUCCESS)
        self.refresh_data()

    def _refresh_diagnostic_table(self) -> None:
        """Render diagnostic rows for all possible AIs."""
        for child in self.diag_frame.winfo_children():
            child.destroy()

        diag_data = scan_all_ai_status()
        for info in diag_data.values():
            rx = ctk.CTkFrame(self.diag_frame, fg_color="transparent")
            rx.pack(fill="x", pady=theme.SPACE_1)

            is_on = info["is_connected"]
            if is_on is None or info.get("detection_status") == "unavailable":
                color, status_text = theme.WARNING, "감지 불가 (다음 갱신에서 재시도)"
            else:
                color = theme.SUCCESS if is_on else theme.FAINT
                status_text = "🟢 연결됨 (모니터링 중)" if is_on else "⚪ 미연동 (로그인/결제 없음)"

            ctk.CTkLabel(
                rx,
                text=f"{info['display_name']}:",
                font=theme.font(theme.FONT_BODY),
                text_color=theme.TEXT,
                width=180,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                rx,
                text=f"[{info['plan_name']}]",
                font=theme.font(theme.FONT_SM),
                text_color=theme.MUTED,
                width=220,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                rx,
                text=info["detail"],
                font=theme.font(theme.FONT_SM),
                text_color=theme.FAINT,
                width=280,
                anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(rx, text=status_text, font=theme.font(theme.FONT_SM), text_color=color).pack(
                side="left"
            )

    def _build_statusbar(self) -> None:
        """Bottom status indicator bar."""
        bar = ctk.CTkFrame(self, fg_color=theme.APP, height=32, corner_radius=0)
        bar.pack(fill="x", side="bottom")

        self.lbl_status_msg = ctk.CTkLabel(
            bar,
            text="● 백엔드 연결 완료 (3초 주기 자동 동기화)",
            font=theme.font(theme.FONT_XS),
            text_color=theme.FAINT,
        )
        self.lbl_status_msg.pack(side="left", padx=theme.SPACE_5, pady=theme.SPACE_1)

        self.lbl_last_time = ctk.CTkLabel(
            bar,
            text="",
            font=theme.font(theme.FONT_XS),
            text_color=theme.FAINT,
        )
        self.lbl_last_time.pack(side="right", padx=theme.SPACE_5, pady=theme.SPACE_1)

    def _handle_switch_to_widget(self) -> None:
        self.withdraw()
        if self.on_switch_to_widget:
            self.on_switch_to_widget()

    def show_window(self) -> None:
        self._refresh_autostart_status()
        self.deiconify()
        self.lift()
        self.focus_force()
        # Polling pauses while hidden; bring the numbers up to date right away.
        self.refresh_data()

    def _manual_refresh_rate(self) -> None:
        """Trigger immediate internet exchange rate refresh."""
        if getattr(self, "_rate_refreshing", False):
            return
        self._rate_refreshing = True
        self.btn_rate_refresh.configure(state="disabled")
        self.lbl_status_msg.configure(text="환율 갱신 중…", text_color=theme.MUTED)
        def worker() -> None:
            try:
                ok = self.rate_updater.update_now()
            except Exception:
                ok = None
            persistence_failed = getattr(self.rate_updater, "persistence_failed", False)
            def finish() -> None:
                self._rate_refreshing = False
                self.btn_rate_refresh.configure(state="normal")
                self.lbl_status_msg.configure(
                    text="환율 임시 적용 · 설정 저장 실패" if persistence_failed else (
                        f"환율 갱신: {self.rate_updater.describe()}" if ok
                        else "환율 서버에 연결하지 못해 이전 환율을 유지합니다."
                    ),
                    text_color=theme.SUCCESS if ok and not persistence_failed else theme.WARNING,
                )
                self.refresh_data()
            try:
                self.after(0, finish)
            except RuntimeError:
                pass
        self._rate_refresh_thread = threading.Thread(target=worker, daemon=True, name="ManualRateRefresh")
        self._rate_refresh_thread.start()

    # =========================================================================
    # Data Fetching & UI Update
    # =========================================================================
    def refresh_data(self) -> None:
        if self._is_updating:
            return
        if self.state() in ("withdrawn", "iconic"):
            # 위젯 모드(창 숨김)에서는 아무도 보지 않는 화면을 다시 그리지 않는다.
            # 위젯은 자체적으로 폴링하고, show_window()가 즉시 갱신한다.
            self._schedule_refresh()
            return
        self._is_updating = True
        # Re-read: the widget menu can hide/show an AI too, and both windows must agree.
        old_hidden = hidden_ai_keys(self.config)
        self.config = load_config()
        if hidden_ai_keys(self.config) != old_hidden:
            self._sync_visibility_checkboxes()
        ais = self.connected_ais
        config = self.config
        krw_rate = self.krw_rate
        threading.Thread(target=self._fetch_worker, args=(ais, config, krw_rate), daemon=True).start()

    def _schedule_refresh(self, delay_ms: int = 3000) -> None:
        if self._refresh_job is not None:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = self.after(delay_ms, self._on_refresh_timer)

    def _on_refresh_timer(self) -> None:
        self._refresh_job = None
        self.refresh_data()

    def _fetch_worker(self, ais: List[Dict[str, Any]], config: Dict[str, Any], krw_rate: float) -> None:
        url_ov = f"{self.backend_url}/api/v1/metrics/overview?hours_back=168"
        url_bd = f"{self.backend_url}/api/v1/metrics/breakdown?hours_back=168"
        url_tr = f"{self.backend_url}/api/v1/traces?limit=50"
        url_act = f"{self.backend_url}/api/v1/metrics/active_contexts"
        selections = (getattr(self, "log_account_selection", "전체"),
                      getattr(self, "chart_account_selection", "전체"))
        choices = getattr(self, "account_choices", {})
        log_params = account_query(selections[0], choices)
        chart_params = account_query(selections[1], choices)

        overview = {}
        breakdown = getattr(self, "breakdown_data", [])
        traces = getattr(self, "traces_data", [])
        act_contexts = {}
        ok = False
        endpoint_status = dict.fromkeys(("overview", "breakdown", "traces", "active_contexts"), False)
        auth_required = False

        try:
            with httpx.Client(timeout=3.0, headers=backend_headers(config)) as client:
                try:
                    r_ov = client.get(url_ov, params=chart_params)
                    auth_required = auth_required or r_ov.status_code in (401, 403)
                    if r_ov.status_code == 200:
                        overview = r_ov.json()
                        endpoint_status["overview"] = True
                except Exception as e:
                    logger.debug("overview fetch err: %s", type(e).__name__)

                try:
                    r_bd = client.get(url_bd, params=chart_params)
                    auth_required = auth_required or r_bd.status_code in (401, 403)
                    if r_bd.status_code == 200:
                        breakdown = r_bd.json()
                        endpoint_status["breakdown"] = True
                except Exception as e:
                    logger.debug("breakdown fetch err: %s", type(e).__name__)

                try:
                    r_tr = client.get(url_tr, params=log_params)
                    auth_required = auth_required or r_tr.status_code in (401, 403)
                    if r_tr.status_code == 200:
                        tr_data = r_tr.json()
                        traces = tr_data if isinstance(tr_data, list) else tr_data.get("items", [])
                        endpoint_status["traces"] = True
                except Exception as e:
                    logger.debug("traces fetch err: %s", type(e).__name__)

                try:
                    r_act = client.get(url_act)
                    auth_required = auth_required or r_act.status_code in (401, 403)
                    if r_act.status_code == 200:
                        act_contexts = r_act.json()
                        endpoint_status["active_contexts"] = isinstance(act_contexts, dict)
                except Exception as e:
                    logger.debug("active_contexts fetch err: %s", type(e).__name__)

                if (any(a["key"].endswith("_api") for a in ais)
                        or any(a.get("has_key") for a in api_key_manager.list_keys())):
                    try:
                        r_sp = client.get(f"{self.backend_url}/api/v1/keys/spend", timeout=15.0)
                        auth_required = auth_required or r_sp.status_code in (401, 403)
                        if r_sp.status_code == 200:
                            self.official_spend = r_sp.json()
                    except Exception as e:
                        logger.debug("spend fetch err: %s", type(e).__name__)
        except Exception as e:
            logger.debug("fetch_worker client err: %s", type(e).__name__)

        summary: Optional[Dict[str, Any]] = None
        try:
            ok = endpoint_status["active_contexts"]
            if ok:
                # 여기서 만든다: 공식 사용량 조회(네트워크)와 로그 읽기가 들어 있어
                # UI 스레드에서 돌리면 창 전체가 멈춘다.
                self.account_rows = accounts.list_accounts()
                ais = account_tools(ais, self.account_rows)
                self.connected_ais = ais
                summary = get_connected_ai_summary(
                    ais,
                    usage_by_ai=act_contexts,
                    krw_rate=krw_rate,
                    config=config,
                    official_spend=self.official_spend,
                    usage_available=endpoint_status["active_contexts"],
                )
                summary["endpoint_status"] = endpoint_status
                try:
                    quotas = account_quotas(registry=AccountQuotaView(self.account_rows)) if any(
                        i["key"] in ("claude", "codex", "antigravity", "copilot", "cursor")
                        for i in summary.get("items", [])
                    ) else []
                except Exception:
                    quotas = []
                attach_accounts(summary, act_contexts, self.account_rows, quotas, krw_rate)
        except Exception as e:
            logger.debug("summary build err: %s", type(e).__name__)
            ok = False
        finally:
            # 어떤 예외가 나도 해제해야 한다. 안 그러면 refresh_data()가 영구히 조기 반환한다.
            self._is_updating = False

        try:
            def apply_current() -> None:
                if selections != (getattr(self, "log_account_selection", "전체"),
                                  getattr(self, "chart_account_selection", "전체")):
                    self._schedule_refresh(0)
                    return
                self._apply_data(overview, breakdown, traces, summary, ok, auth_required)
            self.after(0, apply_current)
        except RuntimeError:
            # 창이 이미 닫혀 mainloop가 끝난 경우
            pass

    def _apply_data(
        self,
        ov: Dict[str, Any],
        bd: List[Dict[str, Any]],
        tr: List[Dict[str, Any]],
        summary: Optional[Dict[str, Any]],
        ok: bool,
        auth_required: bool = False,
    ) -> None:
        self.is_connected = ok
        if not ok or summary is None:
            self.lbl_status_msg.configure(
                text="인증 필요" if auth_required else (
                    "갱신 실패 · 이전 값 표시 중" if getattr(self, "cached_summary", None)
                    else "연결 대기 중..."
                ), text_color=theme.ERROR
            )
            self._schedule_refresh()
            return

        self.breakdown_data = bd
        self.traces_data = tr
        self._refresh_account_choices()
        key_signature = repr((api_key_manager.list_keys(), self.official_spend.get("items")))
        if key_signature != getattr(self, "_key_signature", None):
            self._key_signature = key_signature
            self._refresh_keys_list()

        self.cached_summary = summary

        if set(self.ai_card_widgets.keys()) != {it["key"] for it in summary["items"]}:
            self._rebuild_unified_cards()

        for item in summary["items"]:
            w = self.ai_card_widgets.get(item["key"])
            if not w:
                continue
            groups = item.get("accounts") or []
            w["lbl_plan"].configure(text=f"계정별 사용량 · {len(groups)}개" if len(groups) > 1
                                    else item["plan_name"])
            status = item["session_status"]
            if len(groups) == 1 and groups[0].get("account_id") is None:
                status = f"계정 미상 · {status}"
            w["lbl_session_status"].configure(text=status)
            if self._render_account_card(w, item):
                continue
            windows = item["quota_windows"]
            self._render_quota_rows(w, windows or [{"id": "unavailable", "label": "한도",
                                                   "source": "unavailable", "remaining_pct": None}])

            ctx = item.get("context")
            if ctx and ctx.get("max_tokens") and ctx.get("used_pct") is not None:
                w["lbl_ctx"].configure(
                    text=(
                        f"컨텍스트 (추정): {format_num(ctx['tokens'])} / "
                        f"{format_num(ctx['max_tokens'])} · {ctx['used_pct']:.0f}%"
                    )
                )
                w["prog_ctx"].set(min(1.0, ctx["used_pct"] / 100.0))
                w["prog_ctx"].configure(progress_color=remaining_color(100 - ctx["used_pct"]))
                w["lbl_ctx_sub"].configure(
                    text=f"{ctx['model_name']} · 마지막 요청의 입력 토큰 ÷ 모델 최대치"
                )
            else:
                w["lbl_ctx"].configure(text="컨텍스트: 이 도구의 로그로는 계산 불가")
                w["prog_ctx"].set(0)
                w["lbl_ctx_sub"].configure(text="")

            u = item["usage"]
            meaning = item["cost_meaning"]
            for window, label, target in (("today", "오늘", "lbl_cost_today"),
                                          ("month", "이번 달", "lbl_cost_month")):
                block = u.get(window) or {}
                count = block.get("requests")
                text = (f"{label} {count:,}회 · {usage_cost_text(block, meaning)}"
                        if count is not None else f"{label} · 기록 없음")
                w[target].configure(text=text)
            w["lbl_spend"].configure(text=spend_text(item.get("official_spend"), self.krw_rate) or "")
            w["lbl_billing_note"].configure(text=item["billing_note"])

        tot = summary["total"]
        self.lbl_total_cost_title.configure(text="이번 달 실제 청구 (API 종량제 · 이 PC 기록)")
        unknown = f" · {tot['billed_unknown_month']}건 계산 불가" if tot["billed_unknown_month"] else ""
        self.lbl_total_cost_krw.configure(text=f"{krw(tot['billed_month_krw'])}{unknown}")
        self.lbl_total_cost_usd.configure(
            text=(
                f"구독 사용분 API 환산 {krw(tot['api_equivalent_month_krw'])} (청구 아님) · "
                f"{self.rate_updater.describe()}"
            )
        )
        self.lbl_total_requests.configure(text=f"오늘 {tot['requests_today']:,}회 요청")
        hidden_note = f" (숨긴 {tot['hidden_count']}개 제외)" if tot.get("hidden_count") else ""
        self.lbl_total_tokens.configure(
            text=(
                f"오늘 {format_num(tot['tokens_today'])} 토큰 · "
                f"표시 중인 AI {tot['connected_count']}개{hidden_note}"
            )
        )

        # 2. Update Native Matplotlib Charts & Breakdown Tab
        self._update_native_charts(bd)
        self._render_breakdown(bd)

        # 3. Update Logs Tab with Filters
        self._render_filtered_logs()

        # Update statusbar
        partial = not all(summary.get("endpoint_status", {"all": True}).values())
        self.lbl_status_msg.configure(
            text="인증 필요" if auth_required else (
                "일부 갱신 실패 · 실패한 항목은 이전 값" if partial
                else f"● 동기화 완료 (연결 AI {len(self.connected_ais)}개)"
            ),
            text_color=theme.ERROR if auth_required else (theme.WARNING if partial else theme.SUCCESS),
        )
        self.lbl_last_time.configure(text=f"마지막 갱신: {datetime.now().strftime('%H:%M:%S')}")

        # Schedule next refresh
        self._schedule_refresh()

    def _render_account_card(self, w: dict, item: dict) -> bool:
        groups = item.get("accounts") or []
        multiple = len(groups) > 1
        signature = repr((groups, item.get("cost_meaning"), ctk.get_appearance_mode()))
        if signature == w.get("account_signature"):
            return multiple
        w["account_signature"] = signature
        for child in w["account_box"].winfo_children():
            child.destroy()
        if not multiple:
            w["account_box"].pack_forget()
            w["quota_box"].pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_HALF)
            w["ctx_box"].pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_1)
            w["cost_box"].pack(fill="x", padx=theme.SPACE_4, pady=theme.SPACE_1)
            return False
        for box in ("quota_box", "ctx_box", "cost_box"):
            w[box].pack_forget()
        w["account_box"].pack(fill="x", padx=theme.SPACE_3, pady=theme.SPACE_HALF)
        for account in groups:
            block = ctk.CTkFrame(w["account_box"], fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_SM)
            block.pack(fill="x", pady=theme.SPACE_1)
            ctk.CTkLabel(block, text=account.get("label") or "계정 미상", text_color=theme.TEXT, height=20,
                         font=theme.font(theme.FONT_BODY, weight="bold")).pack(anchor="w", padx=theme.SPACE_2)
            windows = account.get("quota_windows") or []
            quota_text = " · ".join(f"{q['label']} {window_text(q)[0]}" for q in windows) or "한도 확인 불가"
            ctk.CTkLabel(block, text=quota_text, text_color=theme.ACCENT,
                         wraplength=300, justify="left", height=20,
                         font=theme.font(theme.FONT_XS)).pack(anchor="w", padx=theme.SPACE_2)
            lines = account_usage_lines(account, item["cost_meaning"])
            ctk.CTkLabel(block, text="\n".join(lines), text_color=theme.MUTED, height=1,
                         font=theme.font(theme.FONT_XS), wraplength=300, justify="left").pack(
                             anchor="w", padx=theme.SPACE_2, pady=(0, theme.SPACE_1))
        return True

    def _render_breakdown(self, bd: List[Dict[str, Any]]) -> None:
        """Per-model rows in Tab 2; rebuilt only when a value actually changed."""
        sig = (
            self.krw_rate,
            tuple(
                (
                    it.get("provider"),
                    it.get("model_name"),
                    it.get("total_cost_usd"),
                    it.get("count"),
                    it.get("total_tokens"),
                    it.get("cost_unknown_count"),
                )
                for it in bd
            ),
        )
        if sig == self._breakdown_sig:
            return
        self._breakdown_sig = sig

        for child in self.frame_breakdown.winfo_children():
            child.destroy()

        if not bd:
            ctk.CTkLabel(
                self.frame_breakdown, text="아직 수집된 모델 내역이 없습니다.", text_color=theme.FAINT
            ).pack(pady=theme.SPACE_3)
            return

        font_name = theme.font(theme.FONT_BODY)
        font_detail = theme.font(theme.FONT_SM)
        for item in bd:
            m_name = item.get("model_name", "unknown")
            prov = item.get("provider", "unknown")
            m_cost = item.get("total_cost_usd", 0.0)
            m_krw = int(m_cost * self.krw_rate)
            m_reqs = item.get("count", 0)
            m_tkns = item.get("total_tokens", 0)
            unknown = item.get("cost_unknown_count", 0)
            cost_text = "계산 불가" if unknown and unknown >= m_reqs else f"￦ {m_krw:,} 원 (${m_cost:.4f})"
            if unknown and unknown < m_reqs:
                cost_text += f" · {unknown}건 계산 불가"

            row = ctk.CTkFrame(self.frame_breakdown, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_SM)
            row.pack(fill="x", pady=theme.SPACE_1, padx=theme.SPACE_HALF)

            ctk.CTkLabel(row, text=f"[{prov.upper()}] {m_name}", font=font_name, text_color=theme.TEXT).pack(
                side="left", padx=theme.SPACE_3, pady=theme.SPACE_2
            )
            ctk.CTkLabel(
                row,
                text=f"{m_reqs}회 호출 · {format_num(m_tkns)} 토큰",
                font=font_detail,
                text_color=theme.FAINT,
            ).pack(side="left", padx=theme.SPACE_2)
            ctk.CTkLabel(
                row, text=cost_text, font=font_name, text_color=theme.ACCENT
            ).pack(side="right", padx=theme.SPACE_3)

    def _create_log_row(self) -> Dict[str, Any]:
        """One reusable trace row. Its trace is swapped in by _render_filtered_logs."""
        r: Dict[str, Any] = {"trace": None, "vals": None, "shown": False}

        def open_detail(_event: Any = None) -> None:
            if r["trace"] is not None:
                self._open_trace_detail_dialog(r["trace"])

        frame = ctk.CTkFrame(
            self.logs_scroll, fg_color=theme.SUBTLE, corner_radius=theme.RADIUS_SM, cursor="hand2"
        )
        frame.bind("<Button-1>", open_detail)
        r["frame"] = frame

        def label(name: str, side: str, padx: int, **kw: Any) -> None:
            lbl = ctk.CTkLabel(frame, text="", **kw)
            lbl.pack(side=side, padx=padx)
            lbl.bind("<Button-1>", open_detail)
            r[name] = lbl

        label("time", "left", 6, font=self._font_log, text_color=theme.FAINT, width=65)
        label("stat", "left", 4, font=self._font_log_bold, width=65)
        label("model", "left", 6, font=self._font_log_main, text_color=theme.TEXT, width=180, anchor="w")
        label("account", "left", 6, font=self._font_log, text_color=theme.MUTED, width=120, anchor="w")
        label("tokens", "left", 10, font=self._font_log_normal, text_color=theme.MUTED)
        ctk.CTkButton(
            frame,
            text="상세",
            width=55,
            height=24,
            font=self._font_log_button,
            fg_color=theme.HOVER,
            hover_color=theme.GRID,
            command=open_detail,
            text_color=theme.TEXT,
        ).pack(side="right", padx=theme.SPACE_2)
        label("cost", "right", 10, font=self._font_log_main, text_color=theme.ACCENT)
        label("latency", "right", 8, font=self._font_log, text_color=theme.FAINT)
        return r

    def _render_filtered_logs(self) -> None:
        """Filter and display traces in Tab 3.

        Rows are created once and reused. Destroying and recreating ~400 CTk widgets
        every poll froze the whole UI for about a second each time.
        """
        filtered = []
        for t in self.traces_data:
            prov = t.get("provider", "").lower()
            stat = t.get("status", "SUCCESS").upper()
            m_name = t.get("model_name", "").lower()
            t_id = t.get("trace_id", "").lower()

            if self.log_filter_provider != "전체" and self.log_filter_provider.lower() not in prov:
                continue
            if self.log_filter_status != "전체" and self.log_filter_status != stat:
                continue
            if self.log_filter_search and (
                self.log_filter_search not in m_name and self.log_filter_search not in t_id
            ):
                continue
            filtered.append(t)

        if not self.traces_data:
            empty_text = "최근 호출 기록이 없습니다."
        elif not filtered:
            empty_text = "필터 조건에 부합하는 호출 기록이 없습니다."
        else:
            empty_text = ""
        if empty_text:
            self.lbl_logs_empty.configure(text=empty_text)
            if not self.lbl_logs_empty.winfo_manager():
                self.lbl_logs_empty.pack(pady=theme.SPACE_5)
        elif self.lbl_logs_empty.winfo_manager():
            self.lbl_logs_empty.pack_forget()

        for i, t in enumerate(filtered):
            if i == len(self._log_rows):
                self._log_rows.append(self._create_log_row())
            r = self._log_rows[i]
            r["trace"] = t

            ts_str = t.get("timestamp", "")
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone()
                    ts_display = dt.strftime("%H:%M:%S")
                except Exception:
                    ts_display = ts_str[-8:]
            else:
                ts_display = "--:--:--"

            st = t.get("status", "SUCCESS")
            c_krw = int(round(t.get("cost_usd", 0.0) * self.krw_rate))
            vals = (
                ts_display,
                st,
                f"[{t.get('provider', '')}] {t.get('model_name', 'unknown')}",
                f"토큰: {t.get('total_tokens', 0):,}",
                f"￦ {c_krw:,}원" if t.get("cost_source") != "unknown" else "계산 불가",
                f"{int(t.get('latency_ms', 0.0))}ms" if t.get("latency_measured", True) else "-",
                account_label(t.get("account_id"), self.account_rows),
            )
            if vals != r["vals"]:
                r["vals"] = vals
                r["time"].configure(text=vals[0])
                r["stat"].configure(
                    text=vals[1], text_color=theme.SUCCESS if st == "SUCCESS" else theme.ERROR
                )
                r["model"].configure(text=vals[2])
                r["tokens"].configure(text=vals[3])
                r["cost"].configure(text=vals[4])
                r["latency"].configure(text=vals[5])
                r["account"].configure(text=vals[6])
            if not r["shown"]:
                # Hidden rows are always a suffix, so re-packing in index order keeps the order.
                r["frame"].pack(fill="x", pady=theme.SPACE_HALF, padx=theme.SPACE_HALF)
                r["shown"] = True

        for r in self._log_rows[len(filtered) :]:
            r["trace"] = None
            if r["shown"]:
                r["frame"].pack_forget()
                r["shown"] = False


def run():
    app = DesktopMainWindow()
    app.mainloop()


if __name__ == "__main__":
    run()
