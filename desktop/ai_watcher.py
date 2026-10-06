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

        # Base directories
        self.claude_dir = Path.home() / ".claude"
        self.antigravity_dir = Path.home() / ".gemini" / "antigravity"
        self.cursor_dir = Path.home() / ".cursor"
        self.copilot_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "github-copilot"

    def _offset_key(self, prefix: str, fpath: Path) -> str:
        raw = str(fpath)
        resolved = self._resolved.get(raw)
        if resolved is None:
            resolved = str(fpath.resolve())
            self._resolved[raw] = resolved
        return f"{prefix}:{resolved}"

    def _load_offsets(self) -> Dict[str, int]:
        try:
            if self.state_file.exists():
                return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Failed to read offsets: %s", type(e).__name__)
        return {}

    def _save_offsets(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._offsets), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to save offsets: %s", type(e).__name__)

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
        offsets_before = dict(self._offsets)
        seen_before = OrderedDict(self._seen_claude)
        traces = []
        traces.extend(self._scan_claude())
        traces.extend(self._scan_antigravity())
        traces.extend(self._scan_copilot())
        traces.extend(self._scan_gpt())

        if traces:
            if not self._send_traces(traces):
                # 전송 실패: 읽은 위치를 되돌려 다음 주기에 같은 줄을 다시 보낸다.
                self._offsets = offsets_before
                self._seen_claude = seen_before
                return 0
            self._save_offsets()
            self._save_seen()
        return len(traces)

    # 1. Claude Code Scanner
    def _scan_claude(self) -> List[Dict[str, Any]]:
        traces = []
        if not self.claude_dir.exists():
            return traces

        projects_dir = self.claude_dir / "projects"
        if not projects_dir.exists():
            return traces

        for fpath, size in _iter_files(projects_dir, ".jsonl"):
            key = self._offset_key("claude", fpath)
            offset = self._offsets.get(key, 0)
            if size < offset:
                offset = 0
            if size == offset:
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        tr = self._parse_claude_line(line, fpath)
                        if tr:
                            traces.append(tr)
                    self._offsets[key] = f.tell()
            except Exception:
                pass
        return traces

    def _parse_claude_line(self, line: str, fpath: Path) -> Optional[Dict[str, Any]]:
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

        # 로그에 적힌 모델 그대로. 모르면 지어내지 않고 unknown -> 가격 "계산 불가".
        model_name = msg.get("model") or "unknown"
        cache_creation = usage.get("cache_creation")
        c_write_1h = 0
        if isinstance(cache_creation, dict):
            c_write_1h = int(cache_creation.get("ephemeral_1h_input_tokens") or 0)

        ts_str = data.get("timestamp") or datetime.now(timezone.utc).isoformat()
        trace = {
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

    # 2. Antigravity Scanner
    def _scan_antigravity(self) -> List[Dict[str, Any]]:
        traces = []
        if not self.antigravity_dir.exists():
            return traces

        brain_dir = self.antigravity_dir / "brain"
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
    def _scan_copilot(self) -> List[Dict[str, Any]]:
        traces: List[Dict[str, Any]] = []
        session_state = Path.home() / ".copilot" / "session-state"
        if not session_state.exists():
            return traces

        for fpath in session_state.glob("*/events.jsonl"):
            key = f"copilot:{fpath.resolve()}"
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
                            traces.extend(self._parse_copilot_line(line, fpath))
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
            with httpx.Client(timeout=8.0) as client:
                for i in range(0, len(traces), chunk_size):
                    chunk = traces[i : i + chunk_size]
                    res = client.post(url, json={"traces": chunk})
                    if res.status_code >= 300:
                        logger.warning("Backend rejected %d traces: HTTP %d", len(chunk), res.status_code)
                        return False
            logger.info("Auto-ingested %d traces across Antigravity, Claude, and GPT", len(traces))
            return True
        except Exception as e:
            logger.debug("Failed to send traces: %s", type(e).__name__)
            return False
