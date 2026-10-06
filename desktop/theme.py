"""Shared desktop tokens, matched to the web dashboard's light/dark palette."""

# CustomTkinter accepts (light, dark); Tk and Matplotlib use color().
APP = ("#f6f7fb", "#10141d")
CARD = ("#ffffff", "#181e2a")
SUBTLE = ("#f6f7fb", "#1e2634")
HOVER = ("#f1f3f9", "#252e3e")
BORDER = ("#d8dee9", "#394457")
GRID = ("#e8ecf3", "#2b3546")
CONTROL = ("#7b899d", "#65748b")
TEXT = ("#182230", "#f1f5f9")
MUTED = ("#475569", "#bac6d8")
FAINT = ("#58677d", "#9aaac0")
ACCENT = ("#4f46e5", "#a5b4fc")
ACCENT_HOVER = ("#4338ca", "#c7d2fe")
ACCENT_SOFT = ("#eef2ff", "#252d4a")
ACCENT_ON = ("#ffffff", "#182230")
SECONDARY = ("#64748b", "#9aaac0")
SUCCESS = ("#047857", "#6ee7b7")
ERROR = ("#be123c", "#fda4af")
WARNING = ("#92400e", "#fcd34d")
ERROR_SOFT = ("#fff1f2", "#3b222e")

SPACE_HALF = 2
SPACE_1, SPACE_2, SPACE_3, SPACE_4 = 4, 8, 12, 16
SPACE_5, SPACE_6, SPACE_8 = 20, 24, 32
RADIUS_SM, RADIUS_MD = 6, 12
FONT_SANS, FONT_MONO = "Segoe UI", "Consolas"
FONT_XS, FONT_SM, FONT_BODY, FONT_TITLE = 11, 12, 13, 15
FONT_METRIC = 28
ROW_HEIGHT = 28
CHART_FONT = "Malgun Gothic"
CHART_TITLE, CHART_TICK = 11, 9


def appearance_mode() -> str:
    import customtkinter as ctk

    return ctk.get_appearance_mode().lower()


def color(token: tuple, mode: str = None) -> str:
    """Resolve a token for a native Tk/Pillow/Matplotlib surface."""
    return token[1 if (mode or appearance_mode()).lower() == "dark" else 0]


def font(size: int = FONT_BODY, weight: str = "normal", family: str = FONT_SANS):
    import customtkinter as ctk

    return ctk.CTkFont(family=family, size=size, weight=weight)


def install() -> None:
    """Set in-memory control defaults; no theme/config file is written."""
    import customtkinter as ctk

    ctk.set_appearance_mode("system")
    defaults = ctk.ThemeManager.theme
    for name in ("CTk", "CTkToplevel"):
        defaults[name]["fg_color"] = APP
    defaults["CTkFont"].update(family=FONT_SANS, size=FONT_BODY, weight="normal")
    defaults["CTkFrame"].update(
        fg_color=CARD, top_fg_color=SUBTLE, border_color=BORDER, border_width=0, corner_radius=RADIUS_MD
    )
    defaults["CTkLabel"]["text_color"] = TEXT
    for name in ("CTkButton", "CTkCheckBox", "CTkSwitch", "CTkRadioButton", "CTkSlider"):
        defaults[name].update(fg_color=ACCENT, hover_color=ACCENT_HOVER)
    defaults["CTkButton"].update(text_color=ACCENT_ON, text_color_disabled=FAINT, corner_radius=RADIUS_SM)
    defaults["CTkEntry"].update(
        fg_color=CARD,
        border_color=CONTROL,
        text_color=TEXT,
        placeholder_text_color=FAINT,
        corner_radius=RADIUS_SM,
    )
    defaults["CTkCheckBox"].update(text_color=TEXT, checkmark_color=ACCENT_ON, border_color=CONTROL)
    defaults["CTkProgressBar"].update(fg_color=GRID, progress_color=ACCENT, border_width=0)
    for name in ("CTkOptionMenu", "CTkComboBox"):
        defaults[name].update(
            fg_color=SUBTLE,
            button_color=HOVER,
            button_hover_color=GRID,
            text_color=TEXT,
            corner_radius=RADIUS_SM,
        )
    defaults["CTkComboBox"].update(border_color=CONTROL)
    defaults["DropdownMenu"].update(fg_color=CARD, hover_color=HOVER, text_color=TEXT)
    defaults["CTkSegmentedButton"].update(
        fg_color=SUBTLE,
        unselected_color=SUBTLE,
        unselected_hover_color=HOVER,
        selected_color=ACCENT_SOFT,
        selected_hover_color=ACCENT_SOFT,
        text_color=TEXT,
        corner_radius=RADIUS_SM,
    )
    defaults["CTkScrollbar"].update(button_color=BORDER, button_hover_color=CONTROL)
    defaults["CTkScrollableFrame"]["label_fg_color"] = SUBTLE
