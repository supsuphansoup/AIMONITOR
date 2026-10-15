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
    # Width the user dragged per mode, in pixels; None = fit the content.
    "widget_width_mini": None,
    "widget_width_expanded": None,
    "always_on_top": True,
    "poll_interval_ms": 3000,
    "claude_watch_enabled": True,
    "backend_url": "http://127.0.0.1:8000",
    "api_key": None,  # Decrypted in memory only; save_config seals it on disk.
    "krw_rate_source": None,
    "krw_rate_as_of": None,
    "krw_rate_last_success": None,
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
        number = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and (isinstance(value, int) or math.isfinite(value)))
        if key in ("widget_x", "widget_y"):
            valid = value is None or (isinstance(value, int) and not isinstance(value, bool))
        elif key in ("widget_width_mini", "widget_width_expanded"):
            valid = value is None or (isinstance(value, int) and not isinstance(value, bool)
                                      and 120 <= value <= 20000)
        elif key == "poll_interval_ms":
            valid = isinstance(value, int) and not isinstance(value, bool) and 500 <= value <= 3600000
        elif key == "widget_opacity":
            valid = number and 0.1 <= value <= 1
        elif key == "krw_per_usd":
            valid = number and 100 < value < 10000
        elif key == "copilot_monthly_premium_limit":
            valid = value is None or (number and 0 < value <= 1000000000)
        elif key in ("always_on_top", "claude_watch_enabled"):
            valid = isinstance(value, bool)
        elif key == "widget_mode":
            valid = value in ("mini", "expanded")
        elif key in ("widget_items", "hidden_ais"):
            valid = (value is None and key == "widget_items") or (
                isinstance(value, list) and all(isinstance(item, str) for item in value))
        elif key == "backend_url":
            valid = isinstance(value, str) and value.startswith(("http://", "https://"))
        elif key in ("api_key", "krw_rate_source", "krw_rate_as_of", "krw_rate_last_success"):
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
    config["api_key_unreadable"] = False
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config = _validated(data)
                config["api_key_unreadable"] = False
                if data.get("api_key_sealed"):
                    from backend.app.services.secret_store import unseal

                    try:
                        config["api_key"] = unseal(data.get("api_key_scheme", ""), data["api_key_sealed"])
                    except Exception:
                        config["api_key"] = None
                    config["api_key_unreadable"] = config["api_key"] is None
                elif data.get("api_key"):
                    # Migrate a manually configured/legacy clear-text value.
                    save_config(config)
                for key in OBSOLETE_KEYS:
                    config.pop(key, None)
    except Exception:
        pass
    return config


def save_config(config: Dict[str, Any], *, api_key_changed: bool = False) -> None:
    """Atomically replace settings; failures propagate without exposing secret values."""
    with _config_lock:
        _save_config(config, api_key_changed=api_key_changed)


def update_config(changes: Union[Dict[str, Any], Callable[[Dict[str, Any]], None]]) -> Dict[str, Any]:
    """Apply only changed fields to the latest settings under the shared writer lock."""
    with _config_lock:
        config = _load_config()
        previous_key = config.get("api_key")
        if callable(changes):
            changes(config)
            api_key_changed = config.get("api_key") != previous_key
        else:
            config.update(changes)
            api_key_changed = "api_key" in changes
        config = _validated(config)
        _save_config(config, api_key_changed=api_key_changed)
        return _load_config()


def _save_config(config: Dict[str, Any], *, api_key_changed: bool = False) -> None:
    from backend.app.services.secret_store import restrict_file, seal

    data = _validated(config)
    key = data.pop("api_key", None)
    data.pop("api_key_unreadable", None)  # Runtime state, never a persisted preference.
    data.pop("api_key_scheme", None)
    data.pop("api_key_sealed", None)
    if key:
        data["api_key_scheme"], data["api_key_sealed"] = seal(key.strip())
    elif not api_key_changed and CONFIG_FILE.exists():
        # A missing plaintext key may mean DPAPI cannot open it, not that the user deleted it.
        try:
            previous = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            previous = {}
        if isinstance(previous, dict):
            for field in ("api_key_scheme", "api_key_sealed"):
                if field in previous:
                    data[field] = previous[field]
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
    if settings.get("api_key_unreadable"):
        return {}
    key = str(settings.get("api_key") or "").strip() or os.environ.get("AIMONITOR_API_KEY", "").strip()
    return {"X-API-Key": key} if key else {}


def is_aimonitor_health(response: Any) -> bool:
    """Recognize the public health contract without requiring a matching version."""
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("app") == "AIMONITOR" and payload.get("status") == "ok"


def identify_backend(client: Any, url: str, headers: Dict[str, str]) -> bool:
    """Probe with an unauthenticated client, attaching the server key only after identification."""
    if not is_aimonitor_health(client.get(f"{url}/health")):
        return False
    client.headers.update(headers)
    return True


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
