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
from backend.app.core.timeutil import to_aware_utc

TOOLS = {"claude_code", "codex", "antigravity", "copilot", "cursor", "api"}
NAMES = {"claude_code": "Claude", "codex": "Codex"}
_LOCK = threading.RLock()
logger = logging.getLogger("aimonitor.desktop.accounts")
TIMELINE_MAX = 5000
AUTH_MODES = {"subscription", "api", "unknown"}
AUTH_FIELDS = ("auth_mode", "auth_mode_since", "auth_mode_changed_at")


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
    except (ValueError, TypeError, OverflowError):
        return None


def auth_info_for(tool: str, config_dir: Path, home: Optional[Path] = None,
                  environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return only mode/plan evidence from this folder; never return credential values."""
    unknown = {"auth_mode": "unknown", "plan_name": "플랜 확인 불가"}
    if tool not in ("claude_code", "codex"):
        return unknown
    home = home or Path.home()
    environ = os.environ if environ is None else environ
    filename = ".credentials.json" if tool == "claude_code" else "auth.json"
    try:
        data = json.loads((config_dir / filename).read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        return unknown
    if not isinstance(data, dict):
        return unknown
    field = "anthropicApiKey" if tool == "claude_code" else "OPENAI_API_KEY"
    value = data.pop(field, None)
    has_key = isinstance(value, str) and bool(value.strip())
    invalid_key = value is not None and not isinstance(value, str)
    del value
    if invalid_key:
        return unknown
    if tool == "claude_code":
        oauth = data.get("claudeAiOauth")
        if "claudeAiOauth" in data and not isinstance(oauth, dict):
            return unknown
        has_oauth = isinstance(oauth, dict)
        sub = oauth.get("subscriptionType") if has_oauth else None
        data.clear()
        if has_oauth:
            oauth.clear()
        plans = {"free": "Claude Free", "pro": "Claude Pro", "max": "Claude Max",
                 "team": "Claude Team", "enterprise": "Claude Enterprise"}
        plan = plans.get(sub.lower() if isinstance(sub, str) else "", "Claude 구독 (플랜 확인 불가)")
    else:
        tokens = data.get("tokens")
        if tokens is not None and not isinstance(tokens, dict):
            return unknown
        tokens = tokens or {}
        has_oauth = False
        for name in ("id_token", "access_token", "refresh_token"):
            token = tokens.get(name)
            if token is not None and not isinstance(token, (str, dict) if name == "id_token" else str):
                return unknown
            has_oauth = has_oauth or bool(token.strip() if isinstance(token, str) else token)
        del token
        from desktop.ai_detector import _codex_plan_type

        sub = _codex_plan_type(config_dir / filename, data=data) if has_oauth else None
        data.clear()
        tokens.clear()
        known_plans = {"free", "go", "plus", "pro", "team", "business", "enterprise", "edu"}
        plan = f"ChatGPT {sub.capitalize()}" if sub in known_plans else "ChatGPT 플랜 (확인 불가)"
    if has_oauth:
        return unknown if has_key else {"auth_mode": "subscription", "plan_name": plan}
    default = home / (".claude" if tool == "claude_code" else ".codex")
    environment = "ANTHROPIC_API_KEY" if tool == "claude_code" else "OPENAI_API_KEY"
    if canonical_path(config_dir) == canonical_path(default):
        has_key = has_key or bool(environ.get(environment, "").strip())
    if has_key:
        plan = "Anthropic API 종량제" if tool == "claude_code" else "OpenAI API 종량제"
        return {"auth_mode": "api", "plan_name": plan}
    return unknown


def account_billing_info(rows: List[Dict[str, Any]], home: Optional[Path] = None,
                         environ: Optional[Dict[str, str]] = None,
                         now: Optional[datetime] = None) -> Dict[tuple, Dict[str, Any]]:
    """Read-only current evidence joined to saved mode history by opaque account ID."""
    home = home or Path.home()
    now = to_aware_utc(now or datetime.now(timezone.utc))
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for folder in discover_folders(home, environ, rows):
        tool, path = folder["tool"], Path(folder["config_dir"])
        if tool not in ("claude_code", "codex"):
            continue
        identity = account_id_for(tool, path, home)
        key = ("claude" if tool == "claude_code" else "codex", identity)
        groups.setdefault(key, []).append(auth_info_for(tool, path, home, environ))
    results = {}
    for key, infos in groups.items():
        modes = {info["auth_mode"] for info in infos}
        mode = next(iter(modes)) if len(modes) == 1 else "unknown"
        plans = {info["plan_name"] for info in infos}
        plan = next(iter(plans)) if len(plans) == 1 and mode != "unknown" else "플랜 확인 불가"
        saved = [row for row in rows if row.get("account_id") == key[1]
                 and row.get("auth_mode") in AUTH_MODES]
        previous = max(saved, key=lambda row: _timestamp(row.get("auth_mode_since"))
                       or datetime.min.replace(tzinfo=timezone.utc), default={})
        since = _timestamp(previous.get("auth_mode_since"))
        changed = max((_timestamp(row.get("auth_mode_changed_at")) for row in saved
                       if _timestamp(row.get("auth_mode_changed_at"))), default=None)
        if previous and previous["auth_mode"] != mode:
            since = changed = now
        results[key] = {"auth_mode": mode, "plan_name": plan,
                        "auth_mode_since": (since or now).isoformat(),
                        "auth_mode_changed_at": changed.isoformat() if changed else None}
    return results


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
        clean = {key: row[key] for key in (
            "account_id", "tool", "config_dir", "label", "source", "first_seen", "last_seen",
        ) if key in row}
        if "auth_mode" in row:
            mode = row["auth_mode"]
            valid = isinstance(mode, str) and mode in AUTH_MODES
            for field in AUTH_FIELDS[1:]:
                if row.get(field) is not None:
                    when = _timestamp(row[field])
                    valid = valid and when is not None
                    if when:
                        clean[field] = when.isoformat()
            clean["auth_mode"] = mode if valid else "unknown"
        rows.append(clean)
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
        now = to_aware_utc(now or datetime.now(timezone.utc))
        since = now.isoformat()
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
            billing = account_billing_info(rows, self.home, self.environ, now)
            for row in rows + active:
                key = ("claude" if row["tool"] == "claude_code" else row["tool"], row["account_id"])
                if key in billing:
                    row.update({field: billing[key][field] for field in AUTH_FIELDS})
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
