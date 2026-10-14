"""Anthropic Official Claude Quota Syncer.

Directly queries Anthropic's official OAuth usage endpoint:
`https://api.anthropic.com/api/oauth/usage`
using local Claude Code credentials (`~/.claude/.credentials.json`).
Returns Anthropic's own 5-hour and 7-day utilization. A cached result keeps its
`fetched_at`, so callers can tell a fresh number from a stale one.
"""

import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.app.core.config import settings

logger = logging.getLogger("aimonitor.desktop.claude_syncer")

CACHE_FILE = Path.home() / ".aimonitor" / "claude_official_quota.json"
CACHE_TTL_SECONDS = 60.0  # 60s TTL matches Anthropic usage caching policy


class ClaudeQuotaSyncer:
    """Fetches real-time official quota directly from Anthropic."""

    def __init__(self, config_dir: Optional[Path] = None, cache_file: Optional[Path] = None):
        self.cred_file = (config_dir or Path.home() / ".claude") / ".credentials.json"
        self.cache_file = cache_file or CACHE_FILE
        self._last_result: Optional[Dict[str, Any]] = self._load_cache()
        self._last_fetch_time: Optional[float] = None
        self._retry_after: float = 0.0
        self._lock = threading.Lock()

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        try:
            if self.cache_file.exists():
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else None
        except Exception:
            pass
        return None

    def _save_cache(self, data: Dict[str, Any]) -> None:
        temporary = None
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.cache_file.parent, suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2)
            temporary.replace(self.cache_file)
        except Exception:
            pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def get_quota(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Return official quota, refreshing from API if cache is older than TTL."""
        # 메인 창과 위젯의 워커 스레드가 동시에 불러도 API는 한 번만 호출한다.
        with self._lock:
            return self._get_quota_locked(force)

    def get_quota_for_account(
        self, account_id: str, config_dirs: List[Path], home: Optional[Path] = None, force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Select a current OAuth source under the same lock as the account cache."""
        from desktop.accounts import account_id_for

        with self._lock:
            for directory in config_dirs:
                if account_id_for("claude_code", directory, home) != account_id:
                    continue
                cred_file = directory / ".credentials.json"
                try:
                    creds = json.loads(cred_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                oauth = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
                token = oauth.get("accessToken") if isinstance(oauth, dict) else None
                if not isinstance(token, str) or not token.strip():
                    continue
                # A login can change while the credential file is being read.
                if account_id_for("claude_code", directory, home) != account_id:
                    continue
                self.cred_file = cred_file
                return self._get_quota_locked(force, oauth)
            # A cached number cannot establish the folder's current login.
            return None

    def _get_quota_locked(
        self, force: bool, oauth: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        # If previously throttled (429), serve cached quota without hitting API
        if now < self._retry_after:
            return self._last_result

        if (not force and self._last_fetch_time is not None
                and (now - self._last_fetch_time) < CACHE_TTL_SECONDS):
            return self._last_result

        fetched = self.fetch_fresh(oauth)
        if fetched:
            self._last_result = fetched
            self._last_fetch_time = now
            self._save_cache(fetched)
            return fetched
        else:
            # If fetch failed (or 429), bump fetch time so we don't spam every poll cycle
            self._last_fetch_time = now
            return self._last_result

    def fetch_fresh(self, oauth: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Call Anthropic OAuth usage API directly."""
        if oauth is None and not self.cred_file.exists():
            return None

        try:
            if oauth is None:
                creds = json.loads(self.cred_file.read_text(encoding="utf-8"))
                oauth = creds.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            if not token:
                return None

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": f"AIMONITOR/{settings.VERSION}",
            }
            res = httpx.get("https://api.anthropic.com/api/oauth/usage", headers=headers, timeout=4.0)
            if res.status_code == 429:
                logger.info("Anthropic usage API 429 received, backing off for 120 seconds")
                self._retry_after = time.monotonic() + 120.0
                return None
            if res.status_code != 200:
                logger.debug("Anthropic usage API returned HTTP %s", res.status_code)
                return None

            data = res.json()
            if not isinstance(data, dict):
                return None
            from desktop.quota import _number

            fh = data.get("five_hour") if isinstance(data.get("five_hour"), dict) else {}
            sd = data.get("seven_day") if isinstance(data.get("seven_day"), dict) else {}

            fh_used = _number(fh.get("utilization"))
            fh_used = round(fh_used, 1) if fh_used is not None else None
            fh_rem = round(max(0.0, 100.0 - fh_used), 1) if fh_used is not None else None

            sd_used = _number(sd.get("utilization"))
            sd_used = round(sd_used, 1) if sd_used is not None else None
            sd_rem = round(max(0.0, 100.0 - sd_used), 1) if sd_used is not None else None

            result = {
                "five_hour_used_pct": fh_used,
                "five_hour_remaining_pct": fh_rem,
                "five_hour_resets_at": fh.get("resets_at"),
                "weekly_used_pct": sd_used,
                "weekly_remaining_pct": sd_rem,
                "weekly_resets_at": sd.get("resets_at"),
                "seven_day_breakdown": data.get("seven_day_breakdown"),
                # Model-specific weekly caps; null unless the plan has them.
                "seven_day_opus": data.get("seven_day_opus"),
                "seven_day_sonnet": data.get("seven_day_sonnet"),
                "subscription_type": oauth.get("subscriptionType"),
                "fetched_at": time.time(),
                "is_official": True,
            }
            return result
        except Exception as e:
            logger.debug("Failed to fetch official Claude quota: %s", type(e).__name__)
            return None


# Importing the module must not read any account files.
syncer: Optional[ClaudeQuotaSyncer] = None
_singleton_lock = threading.Lock()


def get_official_claude_quota(force: bool = False) -> Optional[Dict[str, Any]]:
    """Helper to fetch official Claude quota."""
    global syncer
    with _singleton_lock:
        if syncer is None:
            syncer = ClaudeQuotaSyncer()
    return syncer.get_quota(force=force)
