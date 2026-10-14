"""Unified AI Auto-Watcher for Antigravity, Claude Code, and GPT/OpenAI.

Automatically discovers and watches:
1. Claude Code: ~/.claude/projects/**/*.jsonl
2. Antigravity: ~/.gemini/antigravity/brain/**/transcript.jsonl
3. GPT / Cursor / Copilot: ~/.cursor, %APPDATA%/Cursor, %LOCALAPPDATA%/github-copilot
Streams usage events to local AIMONITOR backend.
"""

import copy
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.app.models import trace as trace_models
from desktop.accounts import AccountRegistry, canonical_path, config_dir_hash, discover_folders
from desktop.config import backend_headers

logger = logging.getLogger("aimonitor.desktop.ai_watcher")


def _validate_trace(trace: Dict[str, Any]) -> None:
    # Validate before committing seen/cumulative state. Never log validation input.
    # The ingestion schema carries every server limit, including cost and latency.
    trace_models.TraceEventCreate.model_validate(trace)


def _validate_batch_response(response: httpx.Response, traces: List[Dict[str, Any]]) -> None:
    result = response.json()
    if not isinstance(result, list) or len(result) != len(traces):
        raise ValueError("Invalid batch result count")
    for item, sent in zip(result, traces, strict=True):
        if not isinstance(item, dict):
            raise ValueError("Invalid batch result")
        saved = trace_models.TraceEventRead.model_validate(item)
        if (saved.dedupe_key != sent.get("dedupe_key")
                or (sent.get("trace_id") is not None and saved.trace_id != sent["trace_id"])):
            raise ValueError("Invalid batch result identity")


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Invalid usage counter")
    number = int(value)
    if number < 0:
        raise ValueError("Invalid usage counter")
    return number


def _decode_line(raw: bytes) -> tuple:
    line = raw.decode("utf-8", errors="replace").strip()
    obj = json.loads(line) if line else {}
    if not isinstance(obj, dict):
        raise ValueError("Invalid log object")
    return line, obj


def _event_timestamp(value: Any) -> str:
    if value is None or value == "":
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ValueError("Invalid event timestamp")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _event_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, OverflowError):
        return None


def _write_state(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _iter_files(root: Path, suffix: str):
    """Yield (path, size) for files under root ending with suffix.

    Path.glob("**") stats every file again; os.scandir already carries size on
    Windows, which matters because this runs every few seconds over hundreds of logs.
    """
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.name.endswith(suffix):
                            yield Path(entry.path), entry.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue


def _session_key(prefix: str, path: Path, root: Optional[Path] = None) -> str:
    """A session's relative name survives copies and changes of configuration folder."""
    if root is not None:
        relative = path.relative_to(root)
    else:
        path = canonical_path(path)
        marker = "sessions" if prefix.startswith("codex") else "projects"
        try:
            relative = Path(*path.parts[path.parts.index(marker) + 1:])
        except ValueError:
            return f"{prefix}:{path}"
    relative_name = Path(os.path.normcase(str(relative))).as_posix()
    return f"{prefix}:session:{relative_name}"


def _normalize_offset_key(key: str) -> str:
    """Merge old resolve() cursors and normcase cursors using the filesystem's case rules."""
    prefix, separator, path = key.partition(":")
    if not separator:
        return key
    if prefix in ("claude", "codex", "codex_total"):
        if path.startswith("session:"):
            relative = os.path.normcase(os.path.normpath(path[len("session:"):]))
            return f"{prefix}:session:{Path(relative).as_posix()}"
        return _session_key(prefix, Path(path))
    return f"{prefix}:{canonical_path(Path(path))}"


class UnifiedAIWatcher:
    """Watches local AI sessions for Antigravity, Claude, and GPT."""

    def __init__(
        self,
        backend_url: str = "http://127.0.0.1:8000",
        state_file: Optional[Path] = None,
        poll_interval: float = 3.0,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.state_file = state_file or (Path.home() / ".aimonitor" / "ai_offsets.json")
        self.poll_interval = poll_interval
        self._offsets: Dict[str, int] = self._load_offsets()
        self.cursor_file = self.state_file.with_name("ai_cursors.json")
        self._cursors = self._load_cursors()
        self._present = set()
        self._present_paths = set()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_fh = None
        self.seen_file = self.state_file.with_name("ai_seen_claude.json")
        self._seen_claude: "OrderedDict[str, None]" = self._load_seen()
        # Path.resolve() is a filesystem call on Windows; offset keys only need it once per file.
        self._resolved: Dict[str, str] = {}
        try:
            self.accounts = AccountRegistry(state_dir=self.state_file.parent)
        except Exception:
            self.accounts = None
            logger.warning("Account registry unavailable; collecting with unknown attribution")
        self._account_timeline = None
        self._accounts_available = self.accounts is not None
        self._last_folders: List[Dict[str, Any]] = []
        self._collection_dirs: List[tuple] = []
        self._session_files: Dict[tuple, List[tuple]] = {}
        self._session_aliases: Dict[str, set] = {}
        self._folder_identities: Dict[tuple, set] = {}
        self.file_alias_conflicts: List[Dict[str, Any]] = []
        self.duplicate_session_files = 0

        # Base directories
        self.claude_dir = Path.home() / ".claude"
        self.antigravity_dir = Path.home() / ".gemini" / "antigravity"
        self.copilot_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "github-copilot"

    def _offset_key(self, prefix: str, fpath: Path) -> str:
        raw = str(fpath)
        resolved = self._resolved.get(raw)
        if resolved is None:
            resolved = str(canonical_path(fpath))
            self._resolved[raw] = resolved
        return f"{prefix}:{resolved}"

    def _load_offsets(self) -> Dict[str, int]:
        self._identity_state_keys = set()
        try:
            if self.state_file.exists():
                offsets = json.loads(self.state_file.read_text(encoding="utf-8"))
                normalized = {}
                for key, offset in offsets.items() if isinstance(offsets, dict) else []:
                    if not isinstance(key, str) or type(offset) is not int or offset < 0:
                        continue
                    is_identity = ":session:" in key
                    key = _normalize_offset_key(key)
                    if is_identity:
                        self._identity_state_keys.add(key)
                    normalized[key] = max(normalized.get(key, 0), offset)
                return normalized
        except Exception as e:
            logger.debug("Failed to read offsets: %s", type(e).__name__)
        return {}

    def _load_cursors(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.cursor_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        result = {}
        for key, meta in data.items() if isinstance(data, dict) else []:
            if not isinstance(key, str) or not isinstance(meta, dict):
                continue
            missing = meta.get("missing_since")
            if missing is not None and (type(missing) not in (float, int) or not math.isfinite(missing)):
                continue
            generation = meta.get("generation")
            if generation is None:
                if missing is not None:
                    result[_normalize_offset_key(key)] = {"missing_since": missing}
                continue
            if not isinstance(generation, str) or re.fullmatch(r"[a-f0-9]{64}", generation) is None:
                continue
            if any(type(meta.get(field, 0)) is not int or meta.get(field, 0) < 0
                   for field in ("size", "created_ns", "prefix_len", "anchor_offset", "anchor_len")):
                continue
            if meta.get("prefix_len", 0) > 4096 or meta.get("anchor_len", 0) > 64:
                continue
            if any(not isinstance(meta.get(field, ""), str) for field in (
                "prefix_hash", "first_hash", "anchor_hash", "path_hash", "event_generation", "model",
            )):
                continue
            file_id = meta.get("file_id", [])
            if not isinstance(file_id, list) or any(type(value) is not int for value in file_id):
                continue
            result[_normalize_offset_key(key)] = dict(meta)
        return result

    def _cursor_offset(self, key: str, path: Path, stream: Any) -> tuple:
        """Inspect the same open file that supplies bytes, including in-place rewrites."""
        self._present.add(key)
        self._present_paths.add(str(path))
        stat = os.fstat(stream.fileno())
        size = stat.st_size
        file_id = [stat.st_dev, stat.st_ino] if stat.st_dev and stat.st_ino else []
        created = getattr(stat, "st_birthtime_ns", 0) or (stat.st_ctime_ns if os.name == "nt" else 0)
        path_hash = hashlib.sha256(str(canonical_path(path)).encode()).hexdigest()
        previous = self._cursors.get(key, {})
        offset = self._offsets.get(key, 0)
        stream.seek(0)
        first = stream.readline(4096)
        first_hash = hashlib.sha256(first).hexdigest()
        prefix_len = previous.get("prefix_len", min(size, 4096))
        stream.seek(0)
        prefix = stream.read(prefix_len)
        prefix_hash = hashlib.sha256(prefix).hexdigest()
        same_source = previous.get("path_hash") == path_hash or (
            bool(file_id) and previous.get("file_id") == file_id
        )
        if (previous.get("generation") and not same_source and size < offset
                and previous.get("first_hash", previous.get("prefix_hash")) == first_hash):
            # An older, shorter mirror cannot replace the cursor's physical source.
            return offset, size
        replaced = bool(previous.get("generation")) and (
            (previous.get("path_hash") == path_hash and (
                previous.get("file_id", []) != file_id or previous.get("created_ns", 0) != created
            )) or previous.get("prefix_hash") != prefix_hash
            or (same_source and (size < offset or size < previous.get("size", 0)))
        )
        anchor_len = previous.get("anchor_len", 0)
        if not replaced and anchor_len and offset <= size:
            stream.seek(previous.get("anchor_offset", 0))
            replaced = hashlib.sha256(stream.read(anchor_len)).hexdigest() != previous.get("anchor_hash")
        if replaced:
            seed = f"{previous['generation']}:{file_id}:{created}:{first_hash}"
            offset = 0
            self._offsets.pop(key.replace("codex:", "codex_total:", 1), None)
            previous = {}
        else:
            seed = f"{key}:{first_hash}"
            if "missing_since" in previous or (not previous and size < offset):
                offset = 0
        generation = previous.get("generation") or hashlib.sha256(seed.encode()).hexdigest()
        meta = dict(previous, generation=generation, file_id=file_id, created_ns=created,
                    path_hash=path_hash, size=size)
        meta.pop("missing_since", None)
        target_len = min(size, 4096)
        if len(prefix) != target_len:
            stream.seek(0)
            prefix = stream.read(target_len)
        meta.update(prefix_len=len(prefix), prefix_hash=hashlib.sha256(prefix).hexdigest(),
                    first_hash=first_hash)
        # Delivery identity must be reproducible after a missing cursor is expired.
        event_seed = f"{key}:{file_id}:{created}" if file_id or created else f"{key}:{first_hash}"
        meta["event_generation"] = hashlib.sha256(event_seed.encode()).hexdigest()
        self._cursors[key] = meta
        self._offsets[key] = offset
        return offset, size

    def _finish_cursor(self, key: str, stream: Any, offset: int, model: Optional[str] = None) -> None:
        self._offsets[key] = offset
        meta = self._cursors[key]
        anchor_offset = max(0, offset - 64)
        stream.seek(anchor_offset)
        anchor = stream.read(offset - anchor_offset)
        meta.update(size=max(meta["size"], offset), anchor_offset=anchor_offset, anchor_len=len(anchor),
                    anchor_hash=hashlib.sha256(anchor).hexdigest())
        if model is not None:
            meta["model"] = model

    def _event_key(self, prefix: str, identity: Optional[str], offset: int, line: str,
                   model: str = "") -> str:
        # Logical Claude/Codex sessions survive physical copies. Other logs use
        # stable physical generation data, rather than the retained cursor history.
        generation = (identity or "") if prefix in ("cc", "cx") else self._cursors.get(identity, {}).get(
            "event_generation", identity or "",
        )
        digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
        seed = f"{generation}:{offset}:{digest}:{model}"
        return prefix + "-" + hashlib.sha256(seed.encode()).hexdigest()[:32]

    def _prune_cursors(self) -> None:
        now = time.time()
        removed = set()
        for key in list(self._offsets):
            if key.startswith("codex_total:") or key in self._present:
                continue
            meta = self._cursors.setdefault(key, {})
            missing = meta.setdefault("missing_since", now)
            if now - missing >= 30 * 86400:
                removed.update((key, key.replace("codex:", "codex_total:", 1)))
        for key in removed:
            self._offsets.pop(key, None)
            self._cursors.pop(key, None)
        if removed:
            paths = list((self.state_file.parent / "folder_offsets").glob("*.json"))
            paths.append(self.state_file.with_name("claude_offsets.json"))
            for path in paths:
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(old, dict):
                        continue
                    kept = {key: value for key, value in old.items()
                            if _normalize_offset_key(key if path.name != "claude_offsets.json"
                                                     else "claude:" + key) not in removed}
                    if kept != old:
                        _write_state(path, kept)
                except (OSError, ValueError, TypeError):
                    logger.debug("Failed to prune old folder cursors")
        self._resolved = {key: value for key, value in self._resolved.items() if key in self._present_paths}

    def _save_offsets(self) -> None:
        try:
            # Keep aliases in sync so removing the first folder or renaming a hardlink
            # cannot resurrect an older cursor on the next launch.
            for key, aliases in self._session_aliases.items():
                for alias in aliases:
                    if key in self._offsets:
                        self._offsets[alias] = self._offsets[key]
                    if key in self._cursors:
                        self._cursors[alias] = dict(self._cursors[key])
                    total_key = key.replace("codex:", "codex_total:", 1)
                    if key.startswith("codex:") and total_key in self._offsets:
                        self._offsets[alias.replace("codex:", "codex_total:", 1)] = self._offsets[total_key]
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            _write_state(self.state_file, self._offsets)
            _write_state(self.cursor_file, self._cursors)
            self._identity_state_keys.update(k for k in self._offsets if ":session:" in k)
            for tool, directory in self._collection_dirs:
                root = str(canonical_path(directory))
                offsets = {k: v for k, v in self._offsets.items() if root in k}
                prefix = {"claude_code": "claude", "codex": "codex"}.get(tool)
                for key in self._folder_identities.get((prefix, root), set()):
                    if key in self._offsets:
                        offsets[key] = self._offsets[key]
                    total_key = key.replace("codex:", "codex_total:", 1)
                    if prefix == "codex" and total_key in self._offsets:
                        offsets[total_key] = self._offsets[total_key]
                target = self._folder_state(tool, directory)
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_state(target, offsets)
        except Exception as e:
            logger.debug("Failed to save offsets: %s", type(e).__name__)

    def _folder_state(self, tool: str, directory: Path) -> Path:
        return self.state_file.parent / "folder_offsets" / f"{tool}-{config_dir_hash(directory)}.json"

    def _prepare_folder(self, tool: str, directory: Path) -> None:
        if (tool, directory) in self._collection_dirs:
            return
        self._collection_dirs.append((tool, directory))
        try:
            target = self._folder_state(tool, directory)
            if target.exists():
                stored = json.loads(target.read_text(encoding="utf-8"))
                for key, offset in stored.items():
                    is_identity = ":session:" in key
                    key = _normalize_offset_key(key)
                    if is_identity:
                        self._identity_state_keys.add(key)
                    self._offsets[key] = max(self._offsets.get(key, 0), offset)
            if tool == "claude_code":
                legacy = self.state_file.with_name("claude_offsets.json")
                if legacy.exists():
                    for path, offset in json.loads(legacy.read_text(encoding="utf-8")).items():
                        key = _session_key("claude", Path(path))
                        # Once a unified identity cursor exists, the old standalone
                        # file is no longer authoritative (e.g. after truncation).
                        if key not in self._identity_state_keys:
                            self._offsets[key] = max(self._offsets.get(key, 0), offset)
        except (OSError, ValueError, TypeError):
            logger.debug("Failed to load folder offsets")

    def _prepare_session_files(self, sources: List[tuple]) -> None:
        """Unify logical copies and physical aliases before migrating any cursors.

        The earliest folder owns attribution. The longest copy supplies bytes so
        a stale, shorter mirror cannot reset an already advanced shared cursor.
        """
        parents: Dict[str, str] = {}
        order: Dict[str, int] = {}
        physical: Dict[tuple, str] = {}
        records = []

        def find(key: str) -> str:
            while parents[key] != key:
                parents[key] = parents[parents[key]]
                key = parents[key]
            return key

        for prefix, directory, account_id in sources:
            root = directory / ("projects" if prefix == "claude" else "sessions")
            for path, size in sorted(_iter_files(root, ".jsonl"), key=lambda item: str(item[0])):
                key = _session_key(prefix, path, root)
                if key not in parents:
                    parents[key] = key
                    order[key] = len(order)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                # Some Windows filesystems report 0 instead of a usable file ID.
                if stat.st_dev and stat.st_ino:
                    identity = (prefix, stat.st_dev, stat.st_ino)
                    previous = physical.get(identity)
                    if previous is not None:
                        left, right = find(previous), find(key)
                        if order[left] > order[right]:
                            left, right = right, left
                        parents[right] = left
                    physical[identity] = key
                records.append((prefix, directory, account_id, path, size, key))

        groups: Dict[str, List[tuple]] = {}
        for record in records:
            groups.setdefault(find(record[-1]), []).append(record)
        self._session_files = {}
        self._session_aliases = {}
        self._folder_identities = {
            (prefix, str(canonical_path(directory))): set() for prefix, directory, _ in sources
        }
        self.file_alias_conflicts = []
        self.duplicate_session_files = len(records) - len(groups)
        for key, members in groups.items():
            owner = members[0]
            prefix, directory, account_id = owner[:3]
            selected = max(members, key=lambda item: item[4])
            aliases = {item[-1] for item in members}
            self._session_aliases[key] = aliases
            self._session_files.setdefault((prefix, str(canonical_path(directory))), []).append(
                (selected[3], selected[4], key)
            )
            for alias in aliases:
                self._present.add(alias)
                if alias in self._cursors and (key not in self._cursors
                                              or self._offsets.get(alias, 0) > self._offsets.get(key, 0)):
                    self._cursors[key] = dict(self._cursors[alias])
                if alias in self._offsets:
                    self._offsets[key] = max(self._offsets.get(key, 0), self._offsets[alias])
                total_alias = alias.replace("codex:", "codex_total:", 1)
                total_key = key.replace("codex:", "codex_total:", 1)
                if prefix == "codex" and total_alias in self._offsets:
                    self._offsets[total_key] = max(
                        self._offsets.get(total_key, 0), self._offsets[total_alias]
                    )
            for member in members:
                folder = (prefix, str(canonical_path(member[1])))
                self._folder_identities.setdefault(folder, set()).update(aliases)
                if member[2] is not None and account_id is not None and member[2] != account_id:
                    self.file_alias_conflicts.append({
                        "identity_hash": hashlib.sha256(key.encode()).hexdigest()[:12],
                        "first_config_dir": str(directory), "other_config_dir": str(member[1]),
                        "first_account_id": account_id, "other_account_id": member[2],
                    })

    def _files_for(self, prefix: str, directory: Path) -> List[tuple]:
        folder = (prefix, str(canonical_path(directory)))
        # Every source, including a fully deduplicated one, must be registered.
        if folder not in self._folder_identities:
            self._prepare_session_files([(prefix, directory, None)])
        return self._session_files.get(folder, [])

    # Claude Code writes one JSONL line per content block, and every line of the
    # same response repeats the full usage. Remember recent responses so a
    # response is counted once even when its lines straddle two scans.
    _SEEN_MAX = 20000

    def _load_seen(self) -> "OrderedDict[str, None]":
        try:
            if self.seen_file.exists():
                keys = json.loads(self.seen_file.read_text(encoding="utf-8"))
                if isinstance(keys, list):
                    return OrderedDict((str(k), None) for k in keys[-self._SEEN_MAX :])
        except Exception as e:
            logger.debug("Failed to read seen keys: %s", type(e).__name__)
        return OrderedDict()

    def _save_seen(self) -> None:
        try:
            self.seen_file.parent.mkdir(parents=True, exist_ok=True)
            _write_state(self.seen_file, list(self._seen_claude))
        except Exception as e:
            logger.debug("Failed to save seen keys: %s", type(e).__name__)

    def _mark_seen(self, key: str) -> bool:
        """Return True if key is new (and remember it), False if already counted."""
        if key in self._seen_claude:
            return False
        self._seen_claude[key] = None
        while len(self._seen_claude) > self._SEEN_MAX:
            self._seen_claude.popitem(last=False)
        return True

    def _acquire_lock(self) -> bool:
        """Take the machine-wide watcher lock so only one watcher ever ingests.

        The backend lifespan and the desktop app both start a watcher, and the
        user can launch the app twice. Each watcher keeps its own in-memory
        offsets, so two of them ingest every new log line twice.
        """
        lock_path = self.state_file.with_name("ai_watcher.lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lock_path, "a+b")
        except OSError as e:
            logger.warning("Cannot open watcher lock %s: %s", lock_path, type(e).__name__)
            return False
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._lock_fh = fh
        return True

    def _release_lock(self) -> None:
        fh = getattr(self, "_lock_fh", None)
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()
        self._lock_fh = None

    @property
    def is_active(self) -> bool:
        """True while this instance holds the machine-wide lock and is ingesting."""
        return self._lock_fh is not None

    def start(self) -> bool:
        """Start auto-watcher in background thread.

        Returns True if this instance became the active watcher right away. If
        another watcher holds the lock, this one stands by and takes over when
        that lock is released (e.g. the owning process exits).
        """
        if self._thread and self._thread.is_alive():
            return self.is_active
        self._stop_event.clear()
        got_lock = self._acquire_lock()
        if got_lock:
            # Re-read offsets: the previous lock holder may have advanced them.
            self._offsets = self._load_offsets()
            self._cursors = self._load_cursors()
            self._seen_claude = self._load_seen()
            logger.info("UnifiedAIWatcher started for Antigravity, Claude, and GPT")
        else:
            logger.info("Another UnifiedAIWatcher is already running; standing by")
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="UnifiedAIWatcher")
        self._thread.start()
        return got_lock

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        if self._thread is None:
            self._release_lock()

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                if not self.is_active:
                    if not self._acquire_lock():
                        self._stop_event.wait(max(self.poll_interval, 5.0))
                        continue
                    self._offsets = self._load_offsets()
                    self._cursors = self._load_cursors()
                    self._seen_claude = self._load_seen()
                    logger.info("UnifiedAIWatcher took over AI log watching")
                if self._stop_event.is_set():
                    break
                try:
                    self.scan_all()
                except Exception as e:
                    logger.debug("Error during AI log scan: %s", type(e).__name__)
                self._stop_event.wait(self.poll_interval)
        finally:
            self._release_lock()

    def _refresh_accounts(self) -> List[Dict[str, Any]]:
        self._account_timeline = None
        try:
            if self.accounts is None:
                raise ValueError("Registry unavailable")
            folders = self.accounts.refresh()
            self._last_folders = folders
            self._accounts_available = True
            reader = getattr(self.accounts, "read_timeline", None)
            if reader is not None:
                self._account_timeline = reader()
            return folders
        except Exception:
            self._accounts_available = False
            logger.warning("Account registry unavailable; collecting with unknown attribution")
            folders = [dict(row, account_id=None) for row in self._last_folders]
            try:
                for row in discover_folders():
                    if not any((r["tool"], r["config_dir"]) == (row["tool"], row["config_dir"])
                               for r in folders):
                        folders.append(dict(row, account_id=None))
            except (OSError, ValueError):
                pass
            return folders

    def _account_at(self, directory: Path, timestamp: Any) -> Optional[str]:
        if not self._accounts_available:
            return None
        try:
            if self._account_timeline is not None:
                return self.accounts.account_at(directory, timestamp, timeline=self._account_timeline)
            return self.accounts.account_at(directory, timestamp)
        except Exception:
            return None

    def scan_all(self) -> int:
        """Scan all AI providers and stream traces."""
        self._present.clear()
        self._present_paths.clear()
        overridden = self.claude_dir != Path.home() / ".claude"
        if overridden:
            folders = []
            try:
                reader = getattr(self.accounts, "read_timeline", None)
                self._account_timeline = reader() if reader is not None else None
            except Exception:
                self._account_timeline = []
        else:
            folders = self._refresh_accounts()
        if overridden:
            self._prepare_folder("claude_code", self.claude_dir)
        else:
            for row in folders:
                self._prepare_folder(row["tool"], Path(row["config_dir"]))
        if overridden:
            sources = [("claude", self.claude_dir, None)]
        else:
            sources = [("claude" if row["tool"] == "claude_code" else "codex",
                        Path(row["config_dir"]), row["account_id"])
                       for row in folders if row["tool"] in ("claude_code", "codex")]
        self._prepare_session_files(sources)
        # Migration is part of the initial state, so failed delivery rolls back to
        # the migrated cursor rather than to an empty, pre-migration dictionary.
        offsets_before = dict(self._offsets)
        cursors_before = copy.deepcopy(self._cursors)
        seen_before = OrderedDict(self._seen_claude)
        traces = []
        scanned_antigravity = False
        scanned_copilot = False
        # Preserve explicit claude_dir overrides used by legacy callers/tests.
        if overridden:
            traces.extend(self._scan_claude())
        else:
            for row in folders:
                directory = Path(row["config_dir"])
                tool = row["tool"]
                if tool == "claude_code":
                    traces.extend(self._scan_claude(directory))
                elif tool == "codex":
                    traces.extend(self._scan_codex(directory))
                elif tool == "antigravity":
                    traces.extend(self._scan_antigravity(directory))
                    scanned_antigravity |= directory == canonical_path(self.antigravity_dir)
                elif tool == "copilot":
                    traces.extend(self._scan_copilot(directory))
                    scanned_copilot |= directory == canonical_path(Path.home() / ".copilot")
        if not scanned_antigravity:
            traces.extend(self._scan_antigravity())
        if not scanned_copilot:
            traces.extend(self._scan_copilot())
        traces.extend(self._scan_gpt())

        if traces:
            if not self._send_traces(traces):
                # 전송 실패: 읽은 위치를 되돌려 다음 주기에 같은 줄을 다시 보낸다.
                self._offsets = offsets_before
                self._cursors = cursors_before
                self._seen_claude = seen_before
                return 0
        # Persist consumed non-usage/duplicate lines too, so restart does not rescan them.
        self._prune_cursors()
        self._save_offsets()
        self._save_seen()
        return len(traces)

    # 1. Claude Code Scanner
    def _scan_claude(self, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
        traces = []
        directory = directory or self.claude_dir
        if not directory.exists():
            return traces

        projects_dir = directory / "projects"
        if not projects_dir.exists():
            return traces

        for fpath, size, key in self._files_for("claude", directory):
            invalid = 0
            try:
                with fpath.open("rb") as f:
                    offset, size = self._cursor_offset(key, fpath, f)
                    if size == offset:
                        continue
                    f.seek(offset)
                    consumed = offset
                    for raw in f:
                        if not raw.endswith(b"\n"):
                            break
                        start = consumed
                        consumed += len(raw)
                        try:
                            line, obj = _decode_line(raw)
                            tr = self._parse_claude_line(line, fpath, key, start)
                            if tr:
                                tr["account_id"] = self._account_at(directory, obj.get("timestamp"))
                                traces.append(tr)
                        except (ValueError, TypeError, AttributeError, OverflowError):
                            invalid += 1
                            continue
                    self._finish_cursor(key, f, consumed)
            except OSError:
                logger.debug("Failed to read Claude usage")
            if invalid:
                logger.warning("Skipped invalid Claude log lines: %s", invalid)
        return traces

    def _parse_claude_line(self, line: str, fpath: Path, identity_key: Optional[str] = None,
                           line_offset: int = 0) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(line)
        except Exception:
            return None

        if not isinstance(data, dict) or data.get("type") != "assistant":
            return None

        msg = data.get("message")
        if not isinstance(msg, dict):
            return None

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return None

        in_tok = _count(usage.get("input_tokens", 0))
        out_tok = _count(usage.get("output_tokens", 0))
        c_write = _count(usage.get("cache_creation_input_tokens", 0))
        c_read = _count(usage.get("cache_read_input_tokens", 0))

        if (in_tok + out_tok + c_write + c_read) == 0:
            return None

        # 응답 단위 내용 기반 키. 파일 경로·오프셋은 넣지 않는다.
        trace_id = None
        msg_id = msg.get("id")
        if msg_id is not None and (not isinstance(msg_id, str) or len(msg_id) > 512):
            raise ValueError("Invalid response identifier")
        request_id = data.get("requestId")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 512):
            raise ValueError("Invalid request identifier")
        if msg_id:
            dedupe_key = f"{msg_id}:{data.get('requestId') or ''}"
            trace_id = "cc-" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:32]
        else:
            # Responses with vendor IDs retain their existing content-based ID and
            # seen key. Without an ID, use the shared session identity and cursor.
            trace_id = self._event_key("cc", identity_key, line_offset, line)
            dedupe_key = trace_id

        # 로그에 적힌 모델 그대로. 모르면 지어내지 않고 unknown -> 가격 "계산 불가".
        model_name = msg.get("model") or "unknown"
        if not isinstance(model_name, str) or len(model_name) > 200:
            raise ValueError("Invalid model name")
        cache_creation = usage.get("cache_creation")
        c_write_1h = 0
        if isinstance(cache_creation, dict):
            c_write_1h = _count(cache_creation.get("ephemeral_1h_input_tokens", 0))

        ts_str = _event_timestamp(data.get("timestamp"))
        trace = {
            "account_id": None,
            "timestamp": ts_str,
            "session_id": fpath.stem,
            "provider": "anthropic",
            "model_name": model_name,
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "cache_read_tokens": c_read,
            "cache_write_tokens": c_write,
            "cache_write_1h_tokens": min(c_write_1h, c_write),
            "status": "SUCCESS",
            "tags": ["claude-code", "cli"],
            "metadata": {"source": "claude-code-watcher"},
        }
        if trace_id:
            trace["trace_id"] = trace_id
            trace["dedupe_key"] = trace_id
        _validate_trace(trace)
        if not self._mark_seen(dedupe_key):
            return None
        return trace

    def _restore_codex_model(self, stream: Any, offset: int) -> str:
        start = max(0, offset - 512 * 1024)
        stream.seek(start)
        lines = stream.read(offset - start).splitlines()
        if start:
            lines = lines[1:]
        for raw in reversed(lines):
            try:
                _, obj = _decode_line(raw)
                payload = obj.get("payload")
                if obj.get("type") == "turn_context" and isinstance(payload, dict):
                    model = payload.get("model")
                    if isinstance(model, str) and 0 < len(model) <= 200:
                        return model
            except (ValueError, TypeError, AttributeError):
                continue
        return "unknown"

    def _scan_codex(self, directory: Path) -> List[Dict[str, Any]]:
        traces = []
        for fpath, size, key in self._files_for("codex", directory):
            total_key = key.replace("codex:", "codex_total:", 1)
            try:
                with fpath.open("rb") as stream:
                    offset, size = self._cursor_offset(key, fpath, stream)
                    model = self._cursors[key].get("model")
                    if model is None:
                        model = self._restore_codex_model(stream, offset) if offset else "unknown"
                    if size == offset:
                        self._finish_cursor(key, stream, offset, model)
                        continue
                    stream.seek(offset)
                    consumed = offset
                    invalid = 0
                    for raw in stream:
                        if not raw.endswith(b"\n"):
                            break
                        start = consumed
                        consumed += len(raw)
                        try:
                            line, obj = _decode_line(raw)
                            payload = obj.get("payload") or {}
                            if not isinstance(payload, dict):
                                raise ValueError("Invalid Codex payload")
                            if obj.get("type") == "turn_context":
                                candidate = payload.get("model")
                                if isinstance(candidate, str) and 0 < len(candidate) <= 200:
                                    model = candidate
                            if payload.get("type") != "token_count":
                                continue
                            info = payload.get("info") or {}
                            usage = info.get("last_token_usage") or {}
                            total = (info.get("total_token_usage") or {}).get("total_tokens")
                            if total is not None:
                                total = _count(total)
                            if total is not None and self._offsets.get(total_key) == total:
                                continue
                            incoming = _count(usage.get("input_tokens", 0))
                            cached = min(incoming, _count(usage.get("cached_input_tokens", 0)))
                            outgoing = _count(usage.get("output_tokens", 0))
                            if incoming + outgoing == 0:
                                continue
                            trace_id = self._event_key("cx", key, start, line)
                            trace = {
                                "account_id": self._account_at(directory, obj.get("timestamp")),
                                "timestamp": _event_timestamp(obj.get("timestamp")),
                                "trace_id": trace_id, "dedupe_key": trace_id,
                                "session_id": fpath.stem, "provider": "openai", "model_name": model,
                                "prompt_tokens": incoming - cached, "cache_read_tokens": cached,
                                "completion_tokens": outgoing, "status": "SUCCESS", "tags": ["codex", "cli"],
                                "metadata": {"source": "codex-watcher"},
                            }
                            _validate_trace(trace)
                            traces.append(trace)
                            if total is not None:
                                self._offsets[total_key] = total
                        except (ValueError, TypeError, AttributeError, OverflowError):
                            invalid += 1
                            continue
                    self._finish_cursor(key, stream, consumed, model)
                    if invalid:
                        logger.warning("Skipped invalid Codex log lines: %s", invalid)
            except (OSError, ValueError, TypeError, AttributeError):
                logger.debug("Failed to read Codex usage")
        return traces

    # 2. Antigravity Scanner
    def _scan_antigravity(self, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
        traces = []
        directory = directory or self.antigravity_dir
        if not directory.exists():
            return traces

        brain_dir = directory / "brain"
        if not brain_dir.exists():
            return traces

        # Transcripts live at brain/<conversation>/.system_generated/logs/transcript.jsonl
        # (the session id below is read from that layout). Looking only there instead of
        # globbing "**" skips the thousands of artifact files in each conversation folder.
        found = []
        try:
            conversations = list(os.scandir(brain_dir))
        except OSError:
            return traces
        for conv in conversations:
            fpath = Path(conv.path) / ".system_generated" / "logs" / "transcript.jsonl"
            try:
                st = fpath.stat()
            except OSError:
                continue
            found.append((st.st_mtime, fpath, st.st_size))
        # Sorted by mtime so the active session is scanned last
        found.sort(key=lambda item: item[0])

        for _, fpath, size in found:
            key = self._offset_key("antigravity", fpath)
            try:
                with fpath.open("rb") as f:
                    offset, size = self._cursor_offset(key, fpath, f)
                    if size == offset:
                        continue
                    f.seek(offset)
                    line_start = offset
                    invalid = 0
                    for raw in f:
                        if not raw.endswith(b"\n"):
                            break
                        line_end = line_start + len(raw)
                        try:
                            line, obj = _decode_line(raw)
                            tr = self._parse_antigravity_line(
                                line, fpath, prev_offset=line_start, curr_offset=line_end, identity_key=key
                            )
                            if tr:
                                tr["account_id"] = self._account_at(directory, obj.get("created_at"))
                                traces.append(tr)
                        except (ValueError, TypeError, AttributeError, OverflowError):
                            invalid += 1
                        line_start = line_end
                    self._finish_cursor(key, f, line_start)
                    if invalid:
                        logger.warning("Skipped invalid Antigravity log lines: %s", invalid)
            except Exception as e:
                logger.debug("Error reading Antigravity transcript: %s", type(e).__name__)
        return traces

    def _parse_antigravity_line(
        self, line: str, fpath: Path, prev_offset: int = 0, curr_offset: int = 0,
        identity_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(line)
        except Exception:
            return None

        if (not isinstance(obj, dict) or obj.get("source") != "MODEL"
                or obj.get("type") != "PLANNER_RESPONSE"):
            return None

        content = obj.get("content", "")
        thinking = obj.get("thinking", "")

        # Model output tokens estimate
        comp_tokens = (len(content) if isinstance(content, str) else 0) // 4
        comp_tokens += (len(thinking) if isinstance(thinking, str) else 0) // 4
        input_body = next((obj[field] for field in ("input_text", "prompt", "input")
                           if isinstance(obj.get(field), str)), None)
        prompt_tokens = len(input_body) // 4 if input_body is not None else 0
        model = obj.get("model") or obj.get("model_name")
        provider = obj.get("provider")

        conv_id = fpath.parents[2].name if len(fpath.parents) > 2 else "session"
        ts = _event_timestamp(obj.get("created_at"))
        status = "SUCCESS" if obj.get("status") == "DONE" else "ERROR"

        trace_id = self._event_key("ag", identity_key, prev_offset, line)
        trace = {
            "trace_id": trace_id, "dedupe_key": trace_id,
            "account_id": None,
            "timestamp": ts,
            "session_id": conv_id,
            "provider": provider if isinstance(provider, str) and provider else "unknown",
            "model_name": model if isinstance(model, str) and model else "unknown",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": comp_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "status": status,
            "tags": ["antigravity", "ide", "estimated-tokens"],
            # 로그에 토큰 수가 없어 글자 수로 추정한 값이다.
            "metadata": {"source": "antigravity-watcher", "tokens": "estimated",
                         "input_tokens_estimate": "body_length" if input_body is not None else "unavailable"},
        }
        _validate_trace(trace)
        return trace

    # 3. GitHub Copilot Scanner (~/.copilot/session-state/**/events.jsonl)
    #
    # Copilot CLI writes the real per-model usage (tokens, request count, premium
    # requests) into a `session.shutdown` event each time a session segment ends.
    # Per-message events carry no input tokens, so only the shutdown totals are
    # ingested. A session still running is counted when it ends.
    def _scan_copilot(self, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
        traces: List[Dict[str, Any]] = []
        directory = directory or Path.home() / ".copilot"
        session_state = directory / "session-state"
        if not session_state.exists():
            return traces

        for fpath in session_state.glob("*/events.jsonl"):
            key = self._offset_key("copilot", fpath)
            try:
                with fpath.open("rb") as f:
                    last_offset, cur_size = self._cursor_offset(key, fpath, f)
                    if cur_size == last_offset:
                        continue
                    f.seek(last_offset)
                    consumed = last_offset
                    invalid = 0
                    for raw in f:
                        # A line still being written has no newline yet; read it next time.
                        if not raw.endswith(b"\n"):
                            break
                        start = consumed
                        consumed += len(raw)
                        try:
                            line, obj = _decode_line(raw)
                            parsed = self._parse_copilot_line(line, fpath, key, start)
                            for trace in parsed:
                                trace["account_id"] = self._account_at(
                                    directory, obj.get("timestamp")
                                )
                            traces.extend(parsed)
                        except (ValueError, TypeError, AttributeError, OverflowError):
                            invalid += 1
                    self._finish_cursor(key, f, consumed)
                    if invalid:
                        logger.warning("Skipped invalid Copilot log lines: %s", invalid)
            except Exception as e:
                logger.debug("Error reading Copilot file: %s", type(e).__name__)

        return traces

    def _parse_copilot_line(
        self, line: str, fpath: Path, identity_key: Optional[str] = None, line_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        try:
            d = json.loads(line)
        except Exception:
            return []
        if not isinstance(d, dict) or d.get("type") != "session.shutdown":
            return []
        data = d.get("data")
        if not isinstance(data, dict):
            return []
        metrics = data.get("modelMetrics")
        if not isinstance(metrics, dict) or not metrics:
            return []

        ts = _event_timestamp(d.get("timestamp"))
        event_id = d.get("id") or ""
        if not isinstance(event_id, str) or len(event_id) > 512:
            raise ValueError("Invalid event identifier")
        duration_ms = _event_number(data.get("totalApiDurationMs"))
        traces = []
        invalid = 0
        for model_name, m in metrics.items():
            if not isinstance(m, dict) or not isinstance(model_name, str) or not 0 < len(model_name) <= 200:
                invalid += 1
                continue
            usage = m.get("usage") or {}
            requests = m.get("requests") or {}
            input_tok = _count(usage.get("inputTokens", 0))
            cache_read = min(_count(usage.get("cacheReadTokens", 0)), input_tok)
            out_tok = _count(usage.get("outputTokens", 0))
            cache_write = _count(usage.get("cacheWriteTokens", 0))
            count = _count(requests.get("count", 0))
            if input_tok + out_tok + cache_write == 0:
                continue

            lower = model_name.lower()
            prov = "anthropic" if "claude" in lower else "gemini" if "gemini" in lower else "openai"
            trace = {
                "account_id": None,
                "timestamp": ts,
                "session_id": fpath.parent.name,
                "provider": prov,
                "model_name": model_name,
                # Copilot reports cached tokens inside inputTokens (OpenAI style).
                "prompt_tokens": input_tok - cache_read,
                "completion_tokens": out_tok,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "status": "SUCCESS",
                "tags": ["copilot", "session"],
                "metadata": {
                    "source": "copilot-watcher",
                    "requests": count,
                    "premium_requests": _event_number(requests.get("cost")),
                },
            }
            if event_id:
                trace["trace_id"] = (
                    "cp-" + hashlib.sha256(f"{event_id}:{model_name}".encode("utf-8")).hexdigest()[:32]
                )
            else:
                trace["trace_id"] = self._event_key("cp", identity_key, line_offset, line, model_name)
            trace["dedupe_key"] = trace["trace_id"]
            # The API time is a session total; it maps to one model only when one was used.
            if len(metrics) == 1 and count > 0 and isinstance(duration_ms, (int, float)) and duration_ms > 0:
                trace["latency_ms"] = float(duration_ms) / count
            try:
                _validate_trace(trace)
            except ValueError:
                invalid += 1
                continue
            traces.append(trace)
        if invalid:
            logger.warning("Skipped invalid Copilot events: %s", invalid)
        return traces

    # 4. GPT / Cursor
    def _scan_gpt(self) -> List[Dict[str, Any]]:
        # Cursor 로컬 파일에는 호출별 토큰·모델 기록이 없다. 예전 코드는 파일이
        # 바뀔 때마다 gpt-4o 850/280 토큰을 지어냈으므로 아무것도 보내지 않는다.
        return []

    def _send_traces(self, traces: List[Dict[str, Any]]) -> bool:
        """Send chunks, isolating permanent 422s; retry other failures without advancing cursors."""
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None and stop_event.is_set():
            return False
        url = f"{self.backend_url}/api/v1/traces/batch"
        chunk_size = 200

        def accepted(response: httpx.Response, chunk: List[Dict[str, Any]]) -> bool:
            if 200 <= response.status_code < 300:
                try:
                    _validate_batch_response(response, chunk)
                    return True
                except (ValueError, TypeError):
                    logger.warning("Backend returned invalid batch response for %d traces", len(chunk))
                    return False
            if response.status_code in (401, 403):
                logger.warning("인증 필요: 백엔드 수집 권한을 확인하세요")
            logger.warning("Backend rejected %d traces: HTTP %d", len(chunk), response.status_code)
            return False

        try:
            stored = 0
            with httpx.Client(timeout=8.0, headers=backend_headers()) as client:
                for i in range(0, len(traces), chunk_size):
                    if stop_event is not None and stop_event.is_set():
                        return False
                    chunk = traces[i : i + chunk_size]
                    res = client.post(url, json={"traces": chunk})
                    if res.status_code == 422:
                        invalid = 0
                        for trace in chunk:
                            if stop_event is not None and stop_event.is_set():
                                return False
                            single = [trace]
                            response = client.post(url, json={"traces": single})
                            if response.status_code == 422:
                                invalid += 1
                                continue
                            if not accepted(response, single):
                                return False
                            stored += 1
                        if invalid:
                            logger.warning("Skipped invalid backend traces: %d", invalid)
                    elif not accepted(res, chunk):
                        return False
                    else:
                        stored += len(chunk)
            logger.info("Auto-ingested %d traces across Antigravity, Claude, and GPT", stored)
            return True
        except Exception as e:
            logger.debug("Failed to send traces: %s", type(e).__name__)
            return False
