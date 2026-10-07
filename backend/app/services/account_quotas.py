"""Read desktop account manifests without changing collector attribution history."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.core.accounts import ACCOUNT_ID_MAX_LENGTH, ACCOUNT_ID_PATTERN
from backend.app.core.timeutil import to_naive_utc

TOOL_KEYS = {"claude_code": "claude", "codex": "codex"}


def _read_list(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


class ReadOnlyAccountRegistry:
    """Adapter for quota readers; never instantiate or refresh the desktop's writable registry."""

    def __init__(self, state_dir: Path, identities: Dict[str, List[Optional[str]]]):
        self.state_dir = state_dir
        self._timeline = _read_list(state_dir / "account_timeline.json")
        self._rows = []
        for row in _read_list(state_dir / "accounts.json"):
            tool = row.get("tool")
            identity = row.get("account_id")
            directory = row.get("config_dir")
            if (
                tool in TOOL_KEYS and isinstance(identity, str) and isinstance(directory, str)
                and len(identity) <= ACCOUNT_ID_MAX_LENGTH and re.fullmatch(ACCOUNT_ID_PATTERN, identity)
                and identity.startswith(tool + ":") and identity in identities.get(TOOL_KEYS[tool], [])
            ):
                # Human account labels stay in the desktop-owned file, not the HTTP response.
                self._rows.append({
                    "tool": tool, "account_id": identity, "config_dir": directory, "label": "",
                })

    def refresh(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Quota reader compatibility method: return the snapshot, with no discovery or writes."""
        return list(self._rows)

    def account_at(self, config_dir: Path, timestamp: Any) -> Optional[str]:
        from desktop.accounts import config_dir_hash

        try:
            instant = to_naive_utc(datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")))
        except (ValueError, TypeError):
            return None
        directory_hash = config_dir_hash(config_dir)
        chosen, chosen_time = None, None
        for entry in self._timeline:
            if entry.get("config_dir_hash") != directory_hash:
                continue
            try:
                since = to_naive_utc(datetime.fromisoformat(str(entry.get("since")).replace("Z", "+00:00")))
            except (ValueError, TypeError):
                continue
            if since <= instant and (chosen_time is None or since >= chosen_time):
                chosen, chosen_time = entry.get("account_id"), since
        return chosen


def get_account_quota_windows(
    active: Dict[str, Any], state_dir: Optional[Path] = None,
) -> Dict[tuple, List[Dict[str, Any]]]:
    identities = {key: [a["account_id"] for a in item.get("accounts", [])] for key, item in active.items()}
    registry = ReadOnlyAccountRegistry(state_dir or Path.home() / ".aimonitor", identities)
    if not registry.refresh():
        return {}
    try:
        from desktop.quota import account_quotas

        rows = account_quotas(registry=registry)
    except Exception:
        # Servers without desktop credentials/helpers still serve local usage.
        return {}
    results = {}
    for row in rows:
        tool_key = TOOL_KEYS.get(row.get("tool"))
        account_id = row.get("account_id")
        if tool_key and account_id in identities.get(tool_key, []):
            results[(tool_key, account_id)] = row.get("quota_windows")
    return results
