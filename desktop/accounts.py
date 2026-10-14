"""Desktop-owned account discovery and conservative log attribution.

Only hashes of vendor identifiers are persisted. A new observation after restart
cannot prove which account was used while the collector was offline.
"""

import base64
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.core.accounts import ACCOUNT_ID_MAX_LENGTH, ACCOUNT_ID_PATTERN

TOOLS = {"claude_code", "codex", "antigravity", "copilot", "cursor", "api"}
NAMES = {"claude_code": "Claude", "codex": "Codex"}
_LOCK = threading.RLock()
logger = logging.getLogger("aimonitor.desktop.accounts")
TIMELINE_MAX = 5000


def canonical_path(path: Path) -> Path:
    return Path(os.path.normcase(str(path.expanduser().resolve())))


def config_dir_hash(path: Path) -> str:
    return hashlib.sha256(str(canonical_path(path)).encode("utf-8")).hexdigest()[:12]


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def account_id_for(tool: str, config_dir: Path, home: Optional[Path] = None) -> str:
    """Hash a stable vendor identifier, falling back to the resolved folder."""
    if tool not in TOOLS:
        raise ValueError("지원하지 않는 도구")
    config_dir = canonical_path(config_dir)
    identifier = None
    if tool == "claude_code":
        paths = [config_dir / ".claude.json", config_dir.with_suffix(".json")]
        if config_dir == canonical_path((home or Path.home()) / ".claude"):
            paths.append((home or Path.home()) / ".claude.json")
        for path in paths:
            data = _read(path, {})
            if isinstance(data, dict):
                oauth = data.get("oauthAccount") or {}
                if isinstance(oauth, dict):
                    identifier = oauth.get("accountUuid")
            if identifier:
                break
    elif tool == "codex":
        data = _read(config_dir / "auth.json", {})
        if isinstance(data, dict):
            tokens = data.get("tokens") or {}
            if isinstance(tokens, dict):
                identifier = tokens.get("account_id") or data.get("account_id")
                if not identifier:
                    try:
                        token = tokens.get("id_token") or ""
                        payload = token.split(".")[1]
                        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                        identifier = (claims.get("https://api.openai.com/auth") or {}).get(
                            "chatgpt_account_id"
                        )
                    except (ValueError, IndexError, AttributeError, TypeError):
                        pass
    if isinstance(identifier, str) and identifier:
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12]
        return f"{tool}:{digest}"
    return f"{tool}:dir-{config_dir_hash(config_dir)}"


def discover_folders(
    home: Optional[Path] = None, environ: Optional[Dict[str, str]] = None,
    manual: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Resolve junctions, including shared projects/sessions trees, before deduplication."""
    home = home or Path.home()
    environ = os.environ if environ is None else environ
    candidates = []
    for tool, folder, env, structure in (
        ("claude_code", ".claude", "CLAUDE_CONFIG_DIR", "projects"),
        ("codex", ".codex", "CODEX_HOME", "sessions"),
    ):
        paths = [home / folder]
        if environ.get(env):
            paths.append(Path(environ[env]))
        paths.extend(sorted(home.glob(folder + "-*")))
        candidates.extend((tool, p, "auto", structure) for p in paths)
    for row in manual or []:
        if (isinstance(row, dict) and row.get("source") == "manual"
                and isinstance(row.get("tool"), str) and row["tool"] in TOOLS
                and isinstance(row.get("config_dir"), str) and row["config_dir"]):
            tool = row["tool"]
            structure = {"claude_code": "projects", "codex": "sessions"}.get(tool, "")
            candidates.insert(0, (tool, Path(row["config_dir"]), "manual", structure))
    seen = set()
    result = []
    for tool, path, source, structure in candidates:
        try:
            resolved = canonical_path(path)
            data_root = resolved / structure if structure else resolved
            if not data_root.is_dir():
                continue
            key = (tool, str(canonical_path(data_root)))
            if key in seen:
                continue
            seen.add(key)
            result.append({"tool": tool, "config_dir": str(resolved), "source": source})
        except OSError:
            continue
    return result


def _timestamp(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def _label(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 40 or "@" in value or re.search(r"sk-|eyJ|Bearer", value):
        raise ValueError("계정 라벨을 확인하세요")
    return value


def _valid_identity(value: Any, tool: Optional[str] = None) -> bool:
    return (isinstance(value, str) and len(value) <= ACCOUNT_ID_MAX_LENGTH
            and re.fullmatch(ACCOUNT_ID_PATTERN, value) is not None
            and (tool is None or value.startswith(tool + ":")))


def _account_rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for row in value:
        if not isinstance(row, dict):
            continue
        tool, directory = row.get("tool"), row.get("config_dir")
        if (not isinstance(tool, str) or tool not in TOOLS or not _valid_identity(row.get("account_id"), tool)
                or not isinstance(directory, str) or not directory or "\x00" in directory
                or row.get("source", "auto") not in ("auto", "manual")):
            continue
        if any(row.get(field) is not None and _timestamp(row[field]) is None
               for field in ("first_seen", "last_seen")):
            continue
        try:
            if row.get("label") is not None:
                _label(row["label"])
        except (AttributeError, ValueError, TypeError):
            continue
        rows.append({key: row[key] for key in (
            "account_id", "tool", "config_dir", "label", "source", "first_seen", "last_seen",
        ) if key in row})
    return rows


def _timeline_rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for row in value:
        if not isinstance(row, dict):
            continue
        folder_hash, identity = row.get("config_dir_hash"), row.get("account_id")
        when = _timestamp(row.get("since"))
        if (not isinstance(folder_hash, str) or re.fullmatch(r"[a-f0-9]{12}", folder_hash) is None
                or when is None):
            continue
        # An invalid identity with a known timestamp is an unknown interval.
        rows.append({"config_dir_hash": folder_hash,
                     "account_id": identity if _valid_identity(identity) else None,
                     "since": when.isoformat()})
    return rows


def _compact_timeline(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    previous = {}
    compacted = []
    for row in sorted(rows, key=lambda entry: _timestamp(entry["since"])):
        key = row["config_dir_hash"]
        if key not in previous or previous[key] != row["account_id"]:
            compacted.append(row)
            previous[key] = row["account_id"]
    return compacted[-TIMELINE_MAX:]


class AccountRegistry:
    def __init__(self, state_dir: Optional[Path] = None, home: Optional[Path] = None,
                 environ: Optional[Dict[str, str]] = None):
        self.home = home or Path.home()
        self.state_dir = state_dir or self.home / ".aimonitor"
        self.environ = os.environ if environ is None else environ
        self.accounts_file = self.state_dir / "accounts.json"
        self.timeline_file = self.state_dir / "account_timeline.json"
        self._observed: Dict[str, tuple] = {}

    def list_accounts(self) -> List[Dict[str, Any]]:
        rows = _read(self.accounts_file, [])
        return _account_rows(rows)

    def read_timeline(self) -> List[Dict[str, Any]]:
        return _timeline_rows(_read(self.timeline_file, []))

    def refresh(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        since = now.astimezone(timezone.utc).isoformat()
        with _LOCK:
            rows = self.list_accounts()
            raw_timeline = _read(self.timeline_file, None if self.timeline_file.exists() else [])
            writable_timeline = isinstance(raw_timeline, list)
            timeline = _timeline_rows(raw_timeline)
            if not writable_timeline or len(timeline) != len(raw_timeline):
                logger.warning("Invalid account timeline state; valid rows retained: %s", len(timeline))
            active = []
            for folder in discover_folders(self.home, self.environ, rows):
                path = Path(folder["config_dir"])
                account_id = account_id_for(folder["tool"], path, self.home)
                last_seen = max((_timestamp(r.get("last_seen")) for r in rows
                                 if r.get("config_dir") == str(path) and _timestamp(r.get("last_seen"))),
                                default=None)
                row = next((r for r in rows if r.get("account_id") == account_id
                            and r.get("config_dir") == str(path)), None)
                if row is None:
                    number = 1 + sum(r.get("tool") == folder["tool"] for r in rows)
                    existing_label = next((r.get("label") for r in rows
                                           if r.get("account_id") == account_id and r.get("label")), None)
                    default_label = f"{NAMES.get(folder['tool'], folder['tool'])} {number}"
                    row = dict(folder, account_id=account_id,
                               label=existing_label or default_label,
                               first_seen=since, last_seen=since)
                    rows.append(row)
                if not row.get("label"):
                    number = sum(r.get("tool") == folder["tool"] for r in rows)
                    row["label"] = f"{NAMES.get(folder['tool'], folder['tool'])} {number}"
                    row["first_seen"] = since
                row["source"] = folder["source"]
                folder_hash = config_dir_hash(path)
                previous = self._observed.get(folder_hash)
                if previous is None or previous[0] != account_id:
                    # On restart or a changed login, the unobserved interval is unknown.
                    prior = [e for e in timeline if e.get("config_dir_hash") == folder_hash]
                    if prior:
                        last = previous[1] if previous else (
                            last_seen.isoformat() if last_seen else max(e["since"] for e in prior)
                        )
                        timeline.append(dict(config_dir_hash=folder_hash, account_id=None, since=last))
                    timeline.append(dict(config_dir_hash=folder_hash, account_id=account_id, since=since))
                self._observed[folder_hash] = (account_id, since)
                row["last_seen"] = since
                active.append(dict(row))
            _write(self.accounts_file, rows)
            if writable_timeline:
                _write(self.timeline_file, _compact_timeline(timeline))
            active_hashes = {config_dir_hash(Path(r["config_dir"])) for r in active}
            self._observed = {k: v for k, v in self._observed.items() if k in active_hashes}
            return active

    def account_at(self, config_dir: Path, timestamp: Any,
                   timeline: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        ts = _timestamp(timestamp)
        if ts is None:
            return None
        folder_hash = config_dir_hash(config_dir)
        selected = None
        selected_time = None
        for entry in self.read_timeline() if timeline is None else timeline:
            when = _timestamp(entry.get("since"))
            if entry.get("config_dir_hash") == folder_hash and when and when <= ts:
                if selected_time is None or when >= selected_time:
                    selected, selected_time = entry.get("account_id"), when
        return selected

    def rename_account(self, account_id: str, label: str) -> None:
        label = _label(label)
        with _LOCK:
            rows = self.list_accounts()
            matches = [r for r in rows if r.get("account_id") == account_id]
            if not matches:
                raise ValueError("계정을 찾을 수 없습니다")
            for row in matches:
                row["label"] = label
            _write(self.accounts_file, rows)

    def add_folder(self, tool: str, config_dir: Path, label: Optional[str] = None) -> List[Dict[str, Any]]:
        if label is not None:
            label = _label(label)
        if tool not in TOOLS or not config_dir.is_dir():
            raise ValueError("수집 폴더를 확인하세요")
        path = canonical_path(config_dir)
        with _LOCK:
            rows = self.list_accounts()
            matches = [r for r in rows if r.get("tool") == tool and r.get("config_dir") == str(path)]
            if matches:
                for row in matches:
                    row["source"] = "manual"
            else:
                since = datetime.now(timezone.utc).isoformat()
                number = 1 + sum(r.get("tool") == tool for r in rows)
                default_label = f"{NAMES.get(tool, tool)} {number}"
                rows.append(dict(account_id=account_id_for(tool, path, self.home), tool=tool,
                                 config_dir=str(path), source="manual", label=label or default_label,
                                 first_seen=since, last_seen=since))
            _write(self.accounts_file, rows)
            active = self.refresh()
            if label:
                account_id = account_id_for(tool, path, self.home)
                self.rename_account(account_id, label)
                for row in active:
                    if row["account_id"] == account_id:
                        row["label"] = label
            return active

    def remove_folder(self, config_dir: Path) -> None:
        """Remove a manual registration; auto-detected folders remain discoverable."""
        path = str(canonical_path(config_dir))
        with _LOCK:
            rows = [r for r in self.list_accounts()
                    if not (r.get("config_dir") == path and r.get("source") == "manual")]
            _write(self.accounts_file, rows)


def list_accounts() -> List[Dict[str, Any]]:
    return AccountRegistry().list_accounts()


def rename_account(account_id: str, label: str) -> None:
    AccountRegistry().rename_account(account_id, label)


def add_folder(tool: str, config_dir: Path, label: Optional[str] = None) -> List[Dict[str, Any]]:
    return AccountRegistry().add_folder(tool, config_dir, label)


def remove_folder(config_dir: Path) -> None:
    AccountRegistry().remove_folder(config_dir)
