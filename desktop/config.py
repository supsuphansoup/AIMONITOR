"""Desktop application configuration management."""

import json
import os
from pathlib import Path
from typing import Any, Dict

CONFIG_DIR = Path.home() / ".aimonitor"
CONFIG_FILE = CONFIG_DIR / "desktop_config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "krw_per_usd": 1380.0,
    "widget_x": None,
    "widget_y": 60,
    "widget_opacity": 0.94,
    "widget_mode": "mini",  # "mini" or "expanded"
    "always_on_top": True,
    "poll_interval_ms": 3000,
    "claude_watch_enabled": True,
    "backend_url": "http://127.0.0.1:8000",
    "api_key": None,  # Decrypted in memory only; save_config seals it on disk.
    "krw_rate_source": None,
    "krw_rate_as_of": None,
    # Widget items the user picked (ids from desktop.display.widget_entries); None = defaults.
    "widget_items": None,
    # Copilot has no public remaining-quota value; the user may type their plan's limit.
    "copilot_monthly_premium_limit": None,
    # Connected AI card keys the user hid (e.g. ["antigravity"]); they stay detected, just not shown.
    "hidden_ais": [],
}

# Keys older versions wrote with invented limits; dropped on load so they cannot resurface.
OBSOLETE_KEYS = ("weekly_limits", "five_hour_limits", "calibrations")


def load_config() -> Dict[str, Any]:
    """Load config from disk with default fallback."""
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update(data)
                if data.get("api_key_sealed"):
                    from backend.app.services.secret_store import unseal

                    config["api_key"] = unseal(data.get("api_key_scheme", ""), data["api_key_sealed"])
                elif data.get("api_key"):
                    # Migrate a manually configured/legacy clear-text value.
                    save_config(config)
                for key in OBSOLETE_KEYS:
                    config.pop(key, None)
    except Exception:
        pass
    return config


def save_config(config: Dict[str, Any]) -> None:
    """Save config to disk."""
    try:
        from backend.app.services.secret_store import restrict_file, seal

        data = dict(config)
        key = data.pop("api_key", None)
        if key:
            data["api_key_scheme"], data["api_key_sealed"] = seal(str(key).strip())
        else:
            data.pop("api_key_scheme", None)
            data.pop("api_key_sealed", None)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        restrict_file(CONFIG_FILE)
    except Exception:
        pass


def backend_headers(config: Dict[str, Any] = None) -> Dict[str, str]:
    """Resolve the server key, independently from provider/OAuth credentials."""
    settings = load_config() if config is None else config
    key = str(settings.get("api_key") or "").strip() or os.environ.get("AIMONITOR_API_KEY", "").strip()
    return {"X-API-Key": key} if key else {}


def attach_to_default_desktop() -> bool:
    """Attach current thread to the interactive user desktop WinSta0\\Default."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            res = user32.SetThreadDesktop(h_default)
            return bool(res)
    except Exception:
        pass
    return False
