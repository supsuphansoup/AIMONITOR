"""Claude Code CLI local session watcher.

Monitors ~/.claude/projects/**/*.jsonl and ~/.claude/history.jsonl
Extracts assistant token usage (prompt, completion, cache read, cache write)
and ingests them automatically into AIMONITOR backend.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from desktop.config import backend_headers

logger = logging.getLogger("aimonitor.desktop.claude_watcher")


class ClaudeWatcher:
    """Watches local Claude Code session files and streams usage events."""

    def __init__(
        self,
        backend_url: str = "http://127.0.0.1:8000",
        state_file: Optional[Path] = None,
        poll_interval: float = 2.5,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.claude_dir = Path.home() / ".claude"
        self.state_file = state_file or (Path.home() / ".aimonitor" / "claude_offsets.json")
        self.poll_interval = poll_interval
        self._offsets: Dict[str, int] = self._load_offsets()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _load_offsets(self) -> Dict[str, int]:
        try:
            if self.state_file.exists():
                return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Failed to read claude offsets: %s", type(e).__name__)
        return {}

    def _save_offsets(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._offsets), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to save claude offsets: %s", type(e).__name__)

    def start(self) -> None:
        """Start watcher in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ClaudeWatcher")
        self._thread.start()
        logger.info("ClaudeWatcher started for %s", self.claude_dir)

    def stop(self) -> None:
        """Stop watcher thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        # Initial scan
        while not self._stop_event.is_set():
            try:
                self.scan_and_ingest()
            except Exception as e:
                logger.debug("Error during Claude log scan: %s", type(e).__name__)
            self._stop_event.wait(self.poll_interval)

    def scan_and_ingest(self) -> int:
        """Scan Claude log files and ingest any new assistant events."""
        if not self.claude_dir.exists():
            return 0

        projects_dir = self.claude_dir / "projects"
        if not projects_dir.exists():
            return 0

        jsonl_files = list(projects_dir.glob("**/*.jsonl"))
        new_traces: List[Dict[str, Any]] = []

        for fpath in jsonl_files:
            file_key = str(fpath.resolve())
            last_offset = self._offsets.get(file_key, 0)
            file_size = fpath.stat().st_size

            # If file was truncated or recreated, reset offset
            if file_size < last_offset:
                last_offset = 0

            if file_size == last_offset:
                continue

            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_offset)
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        parsed_trace = self._parse_line(line_str, fpath)
                        if parsed_trace:
                            new_traces.append(parsed_trace)
                    self._offsets[file_key] = f.tell()
            except Exception as e:
                logger.debug("Error reading %s: %s", fpath, type(e).__name__)

        if new_traces:
            self._send_traces(new_traces)
            self._save_offsets()

        return len(new_traces)

    def _parse_line(self, line: str, fpath: Path) -> Optional[Dict[str, Any]]:
        """Parse an assistant usage event line."""
        try:
            data = json.loads(line)
        except Exception:
            return None

        # Look for assistant response messages with usage
        if data.get("type") != "assistant":
            return None

        msg = data.get("message")
        if not isinstance(msg, dict):
            return None

        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return None

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)

        # If zero tokens overall, skip
        if (input_tokens + output_tokens + cache_write + cache_read) == 0:
            return None

        model_name = msg.get("model", "claude-3-5-sonnet")
        # Normalize model name if needed (e.g. claude-sonnet-5 -> claude-3-5-sonnet)
        if (
            "sonnet" in model_name.lower()
            and "3-5" not in model_name
            and "3.5" not in model_name
            and "3-7" not in model_name
        ):
            model_name = "claude-3-5-sonnet"

        # Timestamp
        ts_str = data.get("timestamp")
        if ts_str:
            try:
                # Validate ISO format
                datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except Exception:
                ts_str = datetime.now(timezone.utc).isoformat()
        else:
            ts_str = datetime.now(timezone.utc).isoformat()

        # Session ID from filename
        session_id = fpath.stem

        return {
            "timestamp": ts_str,
            "session_id": session_id,
            "provider": "anthropic",
            "model_name": model_name,
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "latency_ms": 500.0,
            "status": "SUCCESS",
            "tags": ["claude-code", "cli"],
            "metadata": {
                "source": "claude-code-watcher",
                "file": fpath.name,
            },
        }

    def _send_traces(self, traces: List[Dict[str, Any]]) -> None:
        """Send parsed traces to local backend in chunks of 200."""
        url = f"{self.backend_url}/api/v1/traces/batch"
        chunk_size = 200
        total = len(traces)
        ingested = 0

        # Only ingest up to the latest 1000 traces during cold start to avoid huge delay
        to_send = traces[-1000:] if total > 1000 else traces

        try:
            with httpx.Client(timeout=10.0, headers=backend_headers()) as client:
                for i in range(0, len(to_send), chunk_size):
                    chunk = to_send[i : i + chunk_size]
                    res = client.post(url, json={"traces": chunk})
                    if res.status_code in (200, 201):
                        ingested += len(chunk)
                    else:
                        if res.status_code in (401, 403):
                            logger.warning("인증 필요: 백엔드 수집 권한을 확인하세요")
                        logger.warning("Failed to ingest Claude chunk: HTTP %s", res.status_code)
            logger.info("Ingested %d Claude Code traces into AIMONITOR (out of %d found)", ingested, total)
        except Exception as e:
            logger.debug("Failed to connect to backend for Claude ingestion: %s", type(e).__name__)
