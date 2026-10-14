"""Desktop application configuration management."""

import json
import math
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Union

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
_config_lock = threading.RLock()


def _validated(data: Dict[str, Any]) -> Dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    config.update({key: value for key, value in data.items() if key not in OBSOLETE_KEYS})
    for key, value in list(config.items()):
        if key not in DEFAULT_CONFIG:
            continue
        valid = True
        number = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if key in ("widget_x", "widget_y"):
            valid = value is None or (isinstance(value, int) and not isinstance(value, bool))
        elif key == "poll_interval_ms":
            valid = isinstance(value, int) and not isinstance(value, bool) and 500 <= value <= 3600000
        elif key == "widget_opacity":
            valid = number and 0.1 <= value <= 1
        elif key == "krw_per_usd":
            valid = number and 100 < value < 10000
        elif key == "copilot_monthly_premium_limit":
            valid = value is None or (number and value > 0)
        elif key in ("always_on_top", "claude_watch_enabled"):
            valid = isinstance(value, bool)
        elif key == "widget_mode":
            valid = value in ("mini", "expanded")
        elif key in ("widget_items", "hidden_ais"):
            valid = (value is None and key == "widget_items") or (
                isinstance(value, list) and all(isinstance(item, str) for item in value))
        elif key == "backend_url":
            valid = isinstance(value, str) and value.startswith(("http://", "https://"))
        elif key in ("api_key", "krw_rate_source", "krw_rate_as_of"):
            valid = value is None or isinstance(value, str)
        if not valid:
            config[key] = deepcopy(DEFAULT_CONFIG[key])
    return config


def load_config() -> Dict[str, Any]:
    """Load config from disk with default fallback."""
    with _config_lock:
        return _load_config()


def _load_config() -> Dict[str, Any]:
    config = _validated({})
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config = _validated(data)
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
    """Atomically replace settings; failures propagate without exposing secret values."""
    with _config_lock:
        _save_config(config)


def update_config(changes: Union[Dict[str, Any], Callable[[Dict[str, Any]], None]]) -> Dict[str, Any]:
    """Apply only changed fields to the latest settings under the shared writer lock."""
    with _config_lock:
        config = _load_config()
        if callable(changes):
            changes(config)
        else:
            config.update(changes)
        config = _validated(config)
        _save_config(config)
        return config


def _save_config(config: Dict[str, Any]) -> None:
    from backend.app.services.secret_store import restrict_file, seal

    data = _validated(config)
    key = data.pop("api_key", None)
    if key:
        data["api_key_scheme"], data["api_key_sealed"] = seal(key.strip())
    else:
        data.pop("api_key_scheme", None)
        data.pop("api_key_sealed", None)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_DIR,
                                         prefix="desktop_config-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        restrict_file(temporary)
        os.replace(temporary, CONFIG_FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
