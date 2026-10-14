"""USD -> KRW rate, refreshed in the background and shared by every window.

The free sources used here publish a reference rate about once a day, so the
rate carries the provider's own timestamp (`as_of`). The UI shows that date
next to every won amount rather than calling a daily rate "real-time".
"""

import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("aimonitor.desktop.rate_fetcher")

DEFAULT_RATE = 1380.0


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


def fetch_usd_krw_rate() -> Tuple[Optional[float], Optional[str], Optional[datetime]]:
    """Return (rate, source label, provider timestamp) from the first source that answers."""
    for url, label, parse in EXCHANGE_APIS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AIMONITOR"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                rate, as_of = parse(json.loads(resp.read().decode("utf-8")))
            if rate and 100 < rate < 10000:  # reject an obviously broken payload
                return rate, label, as_of
        except Exception as e:
            logger.debug("Failed to fetch rate from %s: %s", label, type(e).__name__)
    return None, None, None


class AutoRateUpdater:
    """Periodically refreshes the USD/KRW rate and notifies every listener."""

    def __init__(
        self,
        current_rate: float = DEFAULT_RATE,
        on_rate_updated: Optional[Callable[[float, str], None]] = None,
        interval_sec: float = 1800.0,
        source: str = "저장된 값",
        as_of: Optional[datetime] = None,
    ):
        self.rate = current_rate
        self.interval_sec = interval_sec
        self.source = source
        self.as_of = as_of  # when the provider published the rate
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
        when = f" · {self.as_of.astimezone().strftime('%m/%d')} 기준" if self.as_of else ""
        return f"1 USD = ￦{self.rate:,.1f} ({self.source}{when})"

    def update_now(self) -> Optional[float]:
        rate, src, as_of = fetch_usd_krw_rate()
        if not rate:
            return None
        self.rate = round(rate, 1)
        self.source = src or "온라인"
        self.as_of = as_of
        logger.info("Updated USD/KRW rate: %.1f from %s", self.rate, self.source)
        _persist(self)
        for fn in list(self._listeners):
            try:
                fn(self.rate, self.source)
            except Exception as e:
                logger.debug("rate listener failed: %s", type(e).__name__)
        return self.rate


def _persist(updater: AutoRateUpdater) -> None:
    try:
        from desktop.config import update_config

        update_config({"krw_per_usd": updater.rate, "krw_rate_source": updater.source,
                       "krw_rate_as_of": updater.as_of.isoformat() if updater.as_of else None})
    except Exception as e:
        logger.debug("could not persist rate: %s", type(e).__name__)


_shared: Optional[AutoRateUpdater] = None
_shared_lock = threading.Lock()


def shared_updater() -> AutoRateUpdater:
    """One updater per process, seeded from the last saved rate and started once."""
    global _shared
    with _shared_lock:
        if _shared is None:
            from desktop.config import load_config

            cfg = load_config()
            as_of = None
            try:
                if cfg.get("krw_rate_as_of"):
                    as_of = datetime.fromisoformat(cfg["krw_rate_as_of"])
            except ValueError:
                pass
            _shared = AutoRateUpdater(
                current_rate=float(cfg.get("krw_per_usd") or DEFAULT_RATE),
                source=cfg.get("krw_rate_source") or "저장된 값",
                as_of=as_of,
            )
        _shared.start()
        return _shared
