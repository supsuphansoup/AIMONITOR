"""USD -> KRW rate, refreshed in the background and shared by every window.

The free sources used here publish a reference rate about once a day, so the
rate carries the provider's own timestamp (`as_of`). The UI shows that date
next to every won amount rather than calling a daily rate "real-time".
"""

import json
import logging
import math
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("aimonitor.desktop.rate_fetcher")

DEFAULT_RATE = 1380.0


def _valid_rate(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 100 < value < 10000 and math.isfinite(value))


def _date(value) -> Optional[datetime]:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _stale(as_of: Optional[datetime], now: datetime) -> bool:
    return as_of is None or now - as_of > timedelta(days=7) or as_of - now > timedelta(days=1)


def _parse_er_api(data: dict) -> Tuple[Optional[float], Optional[datetime]]:
    rate = (data.get("rates") or {}).get("KRW")
    ts = data.get("time_last_update_unix") or data.get("time_last_updated")
    as_of = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    return (float(rate) if rate else None), as_of


def _parse_fawaz(data: dict) -> Tuple[Optional[float], Optional[datetime]]:
    rate = (data.get("usd") or {}).get("krw")
    day = data.get("date")
    as_of = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) if day else None
    return (float(rate) if rate else None), as_of


# (url, label, parser)
EXCHANGE_APIS = [
    ("https://open.er-api.com/v6/latest/USD", "open.er-api.com", _parse_er_api),
    ("https://api.exchangerate-api.com/v4/latest/USD", "exchangerate-api.com", _parse_er_api),
    (
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
        "fawazahmed0 currency-api",
        _parse_fawaz,
    ),
]


def fetch_usd_krw_rate(
    now: Optional[datetime] = None,
) -> Tuple[Optional[float], Optional[str], Optional[datetime]]:
    """Prefer a fresh dated source; otherwise return the newest dated candidate."""
    now = _date(now) or datetime.now(timezone.utc)
    candidates = []
    for url, label, parse in EXCHANGE_APIS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AIMONITOR"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                rate, as_of = parse(json.loads(resp.read().decode("utf-8")))
            if _valid_rate(rate):
                as_of = _date(as_of)
                candidate = (rate, label, as_of)
                if not _stale(as_of, now):
                    return candidate
                candidates.append(candidate)
        except Exception as e:
            logger.debug("Failed to fetch rate from %s: %s", label, type(e).__name__)
    if candidates:
        return max(candidates, key=lambda c: c[2] or datetime.min.replace(tzinfo=timezone.utc))
    return None, None, None


class AutoRateUpdater:
    """Periodically refreshes the USD/KRW rate and notifies every listener."""

    def __init__(
        self,
        current_rate: float = DEFAULT_RATE,
        on_rate_updated: Optional[Callable[[float, str], None]] = None,
        interval_sec: float = 1800.0,
        source: Optional[str] = None,
        as_of: Optional[datetime] = None,
        last_success_at: Optional[datetime] = None,
    ):
        valid = _valid_rate(current_rate)
        self.rate = float(current_rate) if valid else DEFAULT_RATE
        self.interval_sec = interval_sec
        self.source = source if valid and isinstance(source, str) and source.strip() else (
            "저장된 값" if valid and self.rate != DEFAULT_RATE else "기본 환율")
        self.as_of = _date(as_of) if valid else None  # when the provider published the rate
        self.last_success_at = _date(last_success_at) if valid else None
        self.last_failure_at: Optional[datetime] = None
        self.persistence_failed = False
        self._listeners: List[Callable[[float, str], None]] = []
        if on_rate_updated:
            self._listeners.append(on_rate_updated)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def add_listener(self, fn: Callable[[float, str], None]) -> None:
        self._listeners.append(fn)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="RateUpdater")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        self.update_now()
        while not self._stop_event.wait(self.interval_sec):
            self.update_now()

    def describe(self) -> str:
        """`1 USD = ￦1,382.4 (open.er-api.com · 10/04 기준)`"""
        parts = [self.source]
        if self.as_of:
            parts.append(self.as_of.strftime("%Y-%m-%d") + " UTC 기준")
        elif self.source != "기본 환율":
            parts.append("기준일 확인 불가")
        if self.source != "기본 환율" and _stale(self.as_of, datetime.now(timezone.utc)):
            parts.append("오래됨")
        if self.last_success_at:
            parts.append("최종 성공 " + self.last_success_at.strftime("%Y-%m-%d %H:%M UTC"))
        if self.last_failure_at:
            parts.append("온라인 조회 실패 " + self.last_failure_at.strftime("%Y-%m-%d %H:%M UTC"))
        return f"1 USD = ￦{self.rate:,.1f} ({' · '.join(parts)})"

    def update_now(self) -> Optional[float]:
        self.persistence_failed = False
        rate, src, as_of = fetch_usd_krw_rate()
        ok = _valid_rate(rate)
        if ok:
            self.rate = round(rate, 1)
            self.source = src or "온라인"
            self.as_of = _date(as_of)
            self.last_success_at = datetime.now(timezone.utc)
            self.last_failure_at = None
            logger.info("Updated USD/KRW rate: %.1f from %s", self.rate, self.source)
            self.persistence_failed = not _persist(self)
        else:
            self.last_failure_at = datetime.now(timezone.utc)
        for fn in list(self._listeners):
            try:
                fn(self.rate, self.source)
            except Exception as e:
                logger.debug("rate listener failed: %s", type(e).__name__)
        return self.rate if ok else None


def _persist(updater: AutoRateUpdater) -> bool:
    try:
        from desktop.config import update_config

        update_config({"krw_per_usd": updater.rate, "krw_rate_source": updater.source,
                       "krw_rate_as_of": updater.as_of.isoformat() if updater.as_of else None,
                       "krw_rate_last_success": (updater.last_success_at.isoformat()
                                                 if updater.last_success_at else None)})
        return True
    except Exception as e:
        logger.warning("could not persist rate: %s", type(e).__name__)
        return False


_shared: Optional[AutoRateUpdater] = None
_shared_lock = threading.Lock()


def shared_updater() -> AutoRateUpdater:
    """One updater per process, seeded from the last saved rate and started once."""
    global _shared
    with _shared_lock:
        if _shared is None:
            from desktop.config import load_config

            cfg = load_config()
            _shared = AutoRateUpdater(
                current_rate=cfg.get("krw_per_usd", DEFAULT_RATE),
                source=cfg.get("krw_rate_source"),
                as_of=cfg.get("krw_rate_as_of"),
                last_success_at=cfg.get("krw_rate_last_success"),
            )
        _shared.start()
        return _shared
