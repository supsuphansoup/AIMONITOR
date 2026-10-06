"""Anthropic Official Claude Quota Syncer.

Directly queries Anthropic's official OAuth usage endpoint:
`https://api.anthropic.com/api/oauth/usage`
using local Claude Code credentials (`~/.claude/.credentials.json`).
Returns Anthropic's own 5-hour and 7-day utilization. A cached result keeps its
`fetched_at`, so callers can tell a fresh number from a stale one.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("aimonitor.desktop.claude_syncer")

CACHE_FILE = Path.home() / ".aimonitor" / "claude_official_quota.json"
CACHE_TTL_SECONDS = 60.0  # 60s TTL matches Anthropic usage caching policy


class ClaudeQuotaSyncer:
    """Fetches real-time official quota directly from Anthropic."""

    def __init__(self):
        self.cred_file = Path.home() / ".claude" / ".credentials.json"
        self._last_result: Optional[Dict[str, Any]] = self._load_cache()
        self._last_fetch_time: float = 0.0
        self._retry_after: float = 0.0
        self._lock = threading.Lock()

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        try:
            if CACHE_FILE.exists():
                return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def _save_cache(self, data: Dict[str, Any]) -> None:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get_quota(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """Return official quota, refreshing from API if cache is older than TTL."""
        # 메인 창과 위젯의 워커 스레드가 동시에 불러도 API는 한 번만 호출한다.
        with self._lock:
            return self._get_quota_locked(force)

    def _get_quota_locked(self, force: bool) -> Optional[Dict[str, Any]]:
        now = time.time()
        # If previously throttled (429), serve cached quota without hitting API
        if now < self._retry_after:
            return self._last_result

        if not force and self._last_result and (now - self._last_fetch_time) < CACHE_TTL_SECONDS:
            return self._last_result

        fetched = self.fetch_fresh()
        if fetched:
            self._last_result = fetched
            self._last_fetch_time = now
            self._save_cache(fetched)
            return fetched
        else:
            # If fetch failed (or 429), bump fetch time so we don't spam every poll cycle
            self._last_fetch_time = now
            return self._last_result

    def fetch_fresh(self) -> Optional[Dict[str, Any]]:
        """Call Anthropic OAuth usage API directly."""
        if not self.cred_file.exists():
            return None

        try:
            creds = json.loads(self.cred_file.read_text(encoding="utf-8"))
            oauth = creds.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            if not token:
                return None

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "claude-code/2.1.278",
            }
            res = httpx.get("https://api.anthropic.com/api/oauth/usage", headers=headers, timeout=4.0)
            if res.status_code == 429:
                logger.info("Anthropic usage API 429 received, backing off for 120 seconds")
                self._retry_after = time.time() + 120.0
                return None
            if res.status_code != 200:
                logger.debug("Anthropic usage API returned HTTP %s", res.status_code)
                return None

            data = res.json()
            fh = data.get("five_hour") or {}
            sd = data.get("seven_day") or {}

            fh_used = round(float(fh.get("utilization", 0.0)), 1)
            fh_rem = round(max(0.0, 100.0 - fh_used), 1)

            sd_used = round(float(sd.get("utilization", 0.0)), 1)
            sd_rem = round(max(0.0, 100.0 - sd_used), 1)

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


# Global singleton instance
syncer = ClaudeQuotaSyncer()


def get_official_claude_quota(force: bool = False) -> Optional[Dict[str, Any]]:
    """Helper to fetch official Claude quota."""
    return syncer.get_quota(force=force)
