"""Unified AI Auto-Watcher for Antigravity, Claude Code, and GPT/OpenAI.

Automatically discovers and watches:
1. Claude Code: ~/.claude/projects/**/*.jsonl
2. Antigravity: ~/.gemini/antigravity/brain/**/transcript.jsonl
3. GPT / Cursor / Copilot: ~/.cursor, %APPDATA%/Cursor, %LOCALAPPDATA%/github-copilot
Streams usage events to local AIMONITOR backend.
"""

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from desktop.accounts import AccountRegistry, canonical_path, config_dir_hash
from desktop.config import backend_headers

logger = logging.getLogger("aimonitor.desktop.ai_watcher")


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
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_fh = None
        self.seen_file = self.state_file.with_name("ai_seen_claude.json")
        self._seen_claude: "OrderedDict[str, None]" = self._load_seen()
        # Path.resolve() is a filesystem call on Windows; offset keys only need it once per file.
        self._resolved: Dict[str, str] = {}
        self.accounts = AccountRegistry(state_dir=self.state_file.parent)
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
                for key, offset in offsets.items():
                    is_identity = ":session:" in key
                    key = _normalize_offset_key(key)
                    if is_identity:
                        self._identity_state_keys.add(key)
                    normalized[key] = max(normalized.get(key, 0), offset)
                return normalized
        except Exception as e:
            logger.debug("Failed to read offsets: %s", type(e).__name__)
        return {}

    def _save_offsets(self) -> None:
        try:
            # Keep aliases in sync so removing the first folder or renaming a hardlink
            # cannot resurrect an older cursor on the next launch.
            for key, aliases in self._session_aliases.items():
                for alias in aliases:
                    if key in self._offsets:
                        self._offsets[alias] = self._offsets[key]
                    total_key = key.replace("codex:", "codex_total:", 1)
                    if key.startswith("codex:") and total_key in self._offsets:
                        self._offsets[alias.replace("codex:", "codex_total:", 1)] = self._offsets[total_key]
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._offsets), encoding="utf-8")
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
                target.write_text(json.dumps(offsets), encoding="utf-8")
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
            self.seen_file.write_text(json.dumps(list(self._seen_claude)), encoding="utf-8")
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
            self._seen_claude = self._load_seen()
            logger.info("UnifiedAIWatcher started for Antigravity, Claude, and GPT")
        else:
            logger.info("Another UnifiedAIWatcher is already running; standing by")
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="UnifiedAIWatcher")
        self._thread.start()
        return got_lock

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._release_lock()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.is_active:
                if not self._acquire_lock():
                    self._stop_event.wait(max(self.poll_interval, 5.0))
                    continue
                self._offsets = self._load_offsets()
                self._seen_claude = self._load_seen()
                logger.info("UnifiedAIWatcher took over AI log watching")
            try:
                self.scan_all()
            except Exception as e:
                logger.debug("Error during AI log scan: %s", type(e).__name__)
            self._stop_event.wait(self.poll_interval)

    def scan_all(self) -> int:
        """Scan all AI providers and stream traces."""
        overridden = self.claude_dir != Path.home() / ".claude"
        folders = [] if overridden else self.accounts.refresh()
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
                self._seen_claude = seen_before
                return 0
        # Persist consumed non-usage/duplicate lines too, so restart does not rescan them.
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
            offset = self._offsets.get(key, 0)
            if size < offset:
                offset = 0
            if size == offset:
                continue

            try:
                with open(fpath, "rb") as f:
                    f.seek(offset)
                    consumed = offset
                    for raw in f:
                        if not raw.endswith(b"\n"):
                            break
                        start = consumed
                        consumed += len(raw)
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        tr = self._parse_claude_line(line, fpath, key, start)
                        if tr:
                            timestamp = json.loads(line).get("timestamp")
                            tr["account_id"] = self.accounts.account_at(directory, timestamp)
                            traces.append(tr)
                    self._offsets[key] = consumed
            except Exception:
                pass
        return traces

    def _parse_claude_line(self, line: str, fpath: Path, identity_key: Optional[str] = None,
                           line_offset: int = 0) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(line)
        except Exception:
            return None

        if data.get("type") != "assistant":
            return None

        msg = data.get("message")
        if not isinstance(msg, dict):
            return None

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return None

        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        c_write = usage.get("cache_creation_input_tokens", 0)
        c_read = usage.get("cache_read_input_tokens", 0)

        if (in_tok + out_tok + c_write + c_read) == 0:
            return None

        # 응답 단위 내용 기반 키. 파일 경로·오프셋은 넣지 않는다.
        trace_id = None
        msg_id = msg.get("id")
        if msg_id:
            dedupe_key = f"{msg_id}:{data.get('requestId') or ''}"
            if not self._mark_seen(dedupe_key):
                return None
            trace_id = "cc-" + hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:32]
        else:
            # Responses with vendor IDs retain their existing content-based ID and
            # seen key. Without an ID, use the shared session identity and cursor.
            content_key = f"{identity_key}:{line_offset}" if identity_key else line
            trace_id = "cc-" + hashlib.sha256(content_key.encode("utf-8")).hexdigest()[:32]
            if not self._mark_seen(trace_id):
                return None

        # 로그에 적힌 모델 그대로. 모르면 지어내지 않고 unknown -> 가격 "계산 불가".
        model_name = msg.get("model") or "unknown"
        cache_creation = usage.get("cache_creation")
        c_write_1h = 0
        if isinstance(cache_creation, dict):
            c_write_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)

        ts_str = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
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
        return trace

    def _scan_codex(self, directory: Path) -> List[Dict[str, Any]]:
        traces = []
        for fpath, size, key in self._files_for("codex", directory):
            total_key = key.replace("codex:", "codex_total:", 1)
            offset = self._offsets.get(key, 0)
            if size == offset:
                continue
            if size < offset:
                offset = 0
                self._offsets.pop(total_key, None)
            try:
                # Read context lines preceding the cursor too; no usage before cursor is sent.
                model = "unknown"
                with fpath.open("rb") as stream:
                    consumed = 0
                    for raw in stream:
                        if not raw.endswith(b"\n"):
                            break
                        start = consumed
                        consumed += len(raw)
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        payload = obj.get("payload") or {}
                        if obj.get("type") == "turn_context":
                            model = payload.get("model") or "unknown"
                        if start < offset or payload.get("type") != "token_count":
                            continue
                        info = payload.get("info") or {}
                        usage = info.get("last_token_usage") or {}
                        total = (info.get("total_token_usage") or {}).get("total_tokens")
                        if total is not None:
                            if self._offsets.get(total_key) == total:
                                continue
                            self._offsets[total_key] = total
                        incoming = int(usage.get("input_tokens") or 0)
                        cached = min(incoming, int(usage.get("cached_input_tokens") or 0))
                        outgoing = int(usage.get("output_tokens") or 0)
                        if incoming + outgoing == 0:
                            continue
                        traces.append({
                            "account_id": self.accounts.account_at(directory, obj.get("timestamp")),
                            "timestamp": obj.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                            "trace_id": "cx-" + hashlib.sha256(f"{key}:{start}".encode()).hexdigest()[:32],
                            "session_id": fpath.stem, "provider": "openai", "model_name": model,
                            "prompt_tokens": incoming - cached, "cache_read_tokens": cached,
                            "completion_tokens": outgoing, "status": "SUCCESS", "tags": ["codex", "cli"],
                            "metadata": {"source": "codex-watcher"},
                        })
                    self._offsets[key] = consumed
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
            offset = self._offsets.get(key, 0)

            if size < offset:
                offset = 0
            if size == offset:
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    line_start = offset
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        line_end = f.tell()
                        line_clean = line.strip()
                        if not line_clean:
                            line_start = line_end
                            continue
                        tr = self._parse_antigravity_line(
                            line_clean, fpath, prev_offset=line_start, curr_offset=line_end
                        )
                        if tr:
                            timestamp = json.loads(line_clean).get("created_at")
                            tr["account_id"] = self.accounts.account_at(directory, timestamp)
                            traces.append(tr)
                        line_start = line_end
                    self._offsets[key] = f.tell()
            except Exception as e:
                logger.debug("Error reading transcript %s: %s", fpath, type(e).__name__)
        return traces

    def _parse_antigravity_line(
        self, line: str, fpath: Path, prev_offset: int = 0, curr_offset: int = 0
    ) -> Optional[Dict[str, Any]]:
        try:
            obj = json.loads(line)
        except Exception:
            return None

        if obj.get("source") != "MODEL" or obj.get("type") != "PLANNER_RESPONSE":
            return None

        content = obj.get("content", "")
        thinking = obj.get("thinking", "")
        tool_calls = obj.get("tool_calls", [])

        # Model output tokens estimate
        comp_tokens = max(10, (len(content) + len(thinking)) // 4)

        # In Google Antigravity (Gemini 3.1 Pro):
        # Record actual incremental turn tokens for the trace event.
        # Context size is tracked separately from Antigravity's gen_metadata DB.
        if curr_offset > 0:
            turn_bytes = max(100, curr_offset - prev_offset)
            turn_tokens = max(50, turn_bytes // 4)
            prompt_tokens = turn_tokens
        else:
            prompt_tokens = max(50, (len(str(tool_calls)) + 400) // 4)

        conv_id = fpath.parents[2].name if len(fpath.parents) > 2 else "session"
        ts = obj.get("created_at") or datetime.now(timezone.utc).isoformat()
        status = "SUCCESS" if obj.get("status") == "DONE" else "ERROR"

        return {
            "account_id": None,
            "timestamp": ts,
            "session_id": conv_id,
            "provider": "gemini",
            "model_name": "gemini-3.1-pro",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": comp_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "status": status,
            "tags": ["antigravity", "ide", "estimated-tokens"],
            # 로그에 토큰 수가 없어 글자 수로 추정한 값이다.
            "metadata": {"source": "antigravity-watcher", "tokens": "estimated"},
        }

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
            last_offset = self._offsets.get(key, 0)
            try:
                cur_size = fpath.stat().st_size
                if cur_size < last_offset:
                    last_offset = 0
                if cur_size == last_offset:
                    continue

                with open(fpath, "rb") as f:
                    f.seek(last_offset)
                    consumed = last_offset
                    for raw in f:
                        # A line still being written has no newline yet; read it next time.
                        if not raw.endswith(b"\n"):
                            break
                        consumed += len(raw)
                        line = raw.decode("utf-8", errors="replace").strip()
                        if line:
                            parsed = self._parse_copilot_line(line, fpath)
                            timestamp = json.loads(line).get("timestamp")
                            for trace in parsed:
                                trace["account_id"] = self.accounts.account_at(directory, timestamp)
                            traces.extend(parsed)
                    self._offsets[key] = consumed
            except Exception as e:
                logger.debug("Error reading Copilot file %s: %s", fpath, type(e).__name__)

        return traces

    def _parse_copilot_line(self, line: str, fpath: Path) -> List[Dict[str, Any]]:
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

        ts = d.get("timestamp") or datetime.now(timezone.utc).isoformat()
        event_id = d.get("id") or ""
        duration_ms = data.get("totalApiDurationMs")
        traces = []
        for model_name, m in metrics.items():
            if not isinstance(m, dict):
                continue
            usage = m.get("usage") or {}
            requests = m.get("requests") or {}
            input_tok = int(usage.get("inputTokens") or 0)
            cache_read = min(int(usage.get("cacheReadTokens") or 0), input_tok)
            out_tok = int(usage.get("outputTokens") or 0)
            cache_write = int(usage.get("cacheWriteTokens") or 0)
            count = int(requests.get("count") or 0)
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
                    "premium_requests": requests.get("cost"),
                },
            }
            if event_id:
                trace["trace_id"] = (
                    "cp-" + hashlib.sha256(f"{event_id}:{model_name}".encode("utf-8")).hexdigest()[:32]
                )
            # The API time is a session total; it maps to one model only when one was used.
            if len(metrics) == 1 and count > 0 and isinstance(duration_ms, (int, float)) and duration_ms > 0:
                trace["latency_ms"] = float(duration_ms) / count
            traces.append(trace)
        return traces

    # 4. GPT / Cursor
    def _scan_gpt(self) -> List[Dict[str, Any]]:
        # Cursor 로컬 파일에는 호출별 토큰·모델 기록이 없다. 예전 코드는 파일이
        # 바뀔 때마다 gpt-4o 850/280 토큰을 지어냈으므로 아무것도 보내지 않는다.
        return []

    def _send_traces(self, traces: List[Dict[str, Any]]) -> bool:
        """Send all traces in chunks. Returns False if any chunk was not accepted."""
        url = f"{self.backend_url}/api/v1/traces/batch"
        chunk_size = 200
        try:
            with httpx.Client(timeout=8.0, headers=backend_headers()) as client:
                for i in range(0, len(traces), chunk_size):
                    chunk = traces[i : i + chunk_size]
                    res = client.post(url, json={"traces": chunk})
                    if res.status_code >= 300:
                        if res.status_code in (401, 403):
                            logger.warning("인증 필요: 백엔드 수집 권한을 확인하세요")
                        logger.warning("Backend rejected %d traces: HTTP %d", len(chunk), res.status_code)
                        return False
            logger.info("Auto-ingested %d traces across Antigravity, Claude, and GPT", len(traces))
            return True
        except Exception as e:
            logger.debug("Failed to send traces: %s", type(e).__name__)
            return False
