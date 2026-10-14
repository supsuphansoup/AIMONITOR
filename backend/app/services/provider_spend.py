"""Money a provider itself says was spent, read with the user's own keys.

Only endpoints that return the provider's books are used:

* Anthropic  ``GET /v1/organizations/cost_report``  (Admin key ``sk-ant-admin...``)
* OpenAI     ``GET /v1/organization/costs``         (Admin key ``sk-admin-...``)
* OpenRouter ``GET /api/v1/auth/key``               (usage on that key)
* DeepSeek   ``GET /user/balance``                  (remaining balance)

Gemini and Groq have no spend endpoint for an API key, so they report
``unavailable`` and the UI falls back to the cost this app recorded locally.
Results are cached so the desktop poll loop cannot hammer billing APIs.
"""

import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional

import httpx

from backend.app.core.timeutil import to_aware_utc, utc_now

logger = logging.getLogger("aimonitor.provider_spend")

CACHE_TTL_SECONDS = 15 * 60
SPEND_BUDGET_SECONDS = 10.0
MAX_CACHE_ENTRIES = 128
MAX_PENDING_FETCHES = 16
_SPEND_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="aimonitor-spend")
ANTHROPIC_VERSION = "2023-06-01"
MAX_PAGES = 10

KIND_SPEND = "spend"  # amount spent in a period
KIND_BALANCE = "balance"  # prepaid credit left
KIND_UNAVAILABLE = "unavailable"


def _amount(value: Any) -> float:
    """Missing billing values cannot be converted into a reported zero."""
    if value is None or isinstance(value, bool):
        raise ValueError("Missing billing amount")
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError("Invalid billing amount")
    return amount


def month_start_utc(now: Optional[datetime] = None) -> datetime:
    now = to_aware_utc(now) if now is not None else to_aware_utc(utc_now())
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _result(provider: str, kind: str, **kw: Any) -> Dict[str, Any]:
    out = {
        "provider": provider,
        "kind": kind,
        "amount": None,
        "currency": None,
        "period_start": None,
        "as_of": to_aware_utc(utc_now()).isoformat(),
        "source_label": "",
        "error": None,
    }
    out.update(kw)
    return out


def _unavailable_on_invalid(provider):
    def decorate(fetch):
        @wraps(fetch)
        def safe_fetch(*args, **kwargs):
            try:
                return fetch(*args, **kwargs)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                return _result(provider, KIND_UNAVAILABLE, source_label="청구 정보 확인 불가")
        return safe_fetch
    return decorate


def _currency(value):
    if not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isalpha():
        raise ValueError("Missing currency")
    return value.upper()


def _cost_page(body, pages):
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("Missing cost data")
    if not isinstance(body.get("has_more"), bool):
        raise ValueError("Missing pagination status")
    rows = []
    for bucket in body["data"]:
        if not isinstance(bucket, dict) or not isinstance(bucket.get("results"), list):
            raise ValueError("Missing cost results")
        for row in bucket["results"]:
            if not isinstance(row, dict):
                raise ValueError("Invalid cost row")
            rows.append(row)
    cursor = body.get("next_page") if body["has_more"] else None
    if body["has_more"]:
        if not isinstance(cursor, str) or not cursor.strip() or cursor in pages:
            raise ValueError("Incomplete cost pagination")
        pages.add(cursor)
    return rows, cursor


@_unavailable_on_invalid("anthropic")
def fetch_anthropic_cost(
    admin_key: str, client: httpx.Client, now: Optional[datetime] = None
) -> Dict[str, Any]:
    start = month_start_utc(now)
    params: Dict[str, Any] = {
        "starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket_width": "1d",
        "limit": 31,
    }
    headers = {"x-api-key": admin_key, "anthropic-version": ANTHROPIC_VERSION}
    total_cents = 0.0
    currency, pages = None, set()
    for _ in range(MAX_PAGES):
        resp = client.get(
            "https://api.anthropic.com/v1/organizations/cost_report", params=params, headers=headers
        )
        if resp.status_code != 200:
            return _result(
                "anthropic",
                KIND_UNAVAILABLE,
                error=f"HTTP {resp.status_code}",
                source_label="Anthropic 비용 리포트 조회 실패",
            )
        rows, cursor = _cost_page(resp.json(), pages)
        for row in rows:
            current = _currency(row.get("currency"))
            if currency is not None and current != currency:
                raise ValueError("Mixed currencies")
            currency = current
            total_cents = _amount(total_cents + _amount(row.get("amount")))
        if cursor is None:
            break
        params["page"] = cursor
    else:
        return _result("anthropic", KIND_UNAVAILABLE, source_label="비용 리포트 페이지 상한 초과")
    return _result(
        "anthropic",
        KIND_SPEND,
        amount=round(total_cents / 100.0, 4),
        currency=currency or "USD",
        period_start=start.isoformat(),
        source_label="Anthropic 공식 비용 리포트 (이번 달)",
    )


@_unavailable_on_invalid("openai")
def fetch_openai_cost(admin_key: str, client: httpx.Client, now: Optional[datetime] = None) -> Dict[str, Any]:
    start = month_start_utc(now)
    params: Dict[str, Any] = {"start_time": int(start.timestamp()), "bucket_width": "1d", "limit": 31}
    headers = {"Authorization": f"Bearer {admin_key}"}
    total = 0.0
    currency, pages = None, set()
    for _ in range(MAX_PAGES):
        resp = client.get("https://api.openai.com/v1/organization/costs", params=params, headers=headers)
        if resp.status_code != 200:
            return _result(
                "openai",
                KIND_UNAVAILABLE,
                error=f"HTTP {resp.status_code}",
                source_label="OpenAI 비용 조회 실패",
            )
        rows, cursor = _cost_page(resp.json(), pages)
        for row in rows:
            amount = row["amount"]
            current = _currency(amount.get("currency"))
            if currency is not None and current != currency:
                raise ValueError("Mixed currencies")
            currency = current
            total = _amount(total + _amount(amount.get("value")))
        if cursor is None:
            break
        params["page"] = cursor
    else:
        return _result("openai", KIND_UNAVAILABLE, source_label="비용 리포트 페이지 상한 초과")
    return _result(
        "openai",
        KIND_SPEND,
        amount=round(total, 4),
        currency=currency or "USD",
        period_start=start.isoformat(),
        source_label="OpenAI 공식 비용 (이번 달)",
    )


@_unavailable_on_invalid("openrouter")
def fetch_openrouter(key: str, client: httpx.Client, now: Optional[datetime] = None) -> Dict[str, Any]:
    resp = client.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"})
    if resp.status_code != 200:
        return _result(
            "openrouter",
            KIND_UNAVAILABLE,
            error=f"HTTP {resp.status_code}",
            source_label="OpenRouter 조회 실패",
        )
    data = resp.json().get("data") or {}
    monthly = data.get("usage_monthly")
    if monthly is not None:
        return _result(
            "openrouter",
            KIND_SPEND,
            amount=_amount(monthly),
            currency="USD",
            period_start=month_start_utc(now).isoformat(),
            source_label="OpenRouter 공식 (이 키의 이번 달 사용액)",
        )
    return _result(
        "openrouter",
        KIND_SPEND,
        amount=_amount(data.get("usage")),
        currency="USD",
        source_label="OpenRouter 공식 (이 키의 누적 사용액)",
    )


@_unavailable_on_invalid("deepseek")
def fetch_deepseek(key: str, client: httpx.Client, now: Optional[datetime] = None) -> Dict[str, Any]:
    resp = client.get("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"})
    if resp.status_code != 200:
        return _result(
            "deepseek",
            KIND_UNAVAILABLE,
            error=f"HTTP {resp.status_code}",
            source_label="DeepSeek 잔액 조회 실패",
        )
    infos = resp.json().get("balance_infos") or []
    if not infos:
        return _result("deepseek", KIND_UNAVAILABLE, source_label="DeepSeek 잔액 정보 없음")
    if not isinstance(infos, list):
        raise ValueError("Invalid balances")
    currency, total = None, 0.0
    for info in infos:
        current = _currency(info.get("currency"))
        if currency is not None and current != currency:
            raise ValueError("Mixed currencies")
        currency = current
        total = _amount(total + _amount(info.get("total_balance")))
    return _result(
        "deepseek",
        KIND_BALANCE,
        amount=total,
        currency=currency,
        source_label="DeepSeek 공식 잔액",
    )


# card key -> (key provider whose secret to use, fetcher)
FETCHERS: Dict[str, tuple] = {
    "anthropic_api": ("anthropic_admin", fetch_anthropic_cost),
    "openai_api": ("openai_admin", fetch_openai_cost),
    "openrouter_api": ("openrouter", fetch_openrouter),
    "deepseek_api": ("deepseek", fetch_deepseek),
}

NO_SPEND_API = {
    "gemini_api": "Gemini API 키로는 사용 금액을 조회할 수 없음 (Cloud Billing 필요)",
    "groq_api": "Groq API 키로는 사용 금액을 조회할 수 없음",
}


class SpendService:
    def __init__(self, get_key: Callable[[str], Optional[str]], ttl: float = CACHE_TTL_SECONDS,
                 budget: float = SPEND_BUDGET_SECONDS):
        self._get_key = get_key
        self._ttl = ttl
        self._budget = budget
        self._cache: Dict[Any, tuple] = OrderedDict()
        self._inflight: Dict[Any, Future] = {}
        self._pending = 0
        self._lock = threading.RLock()

    def invalidate(self, provider: str) -> None:
        with self._lock:
            for mapping in (self._cache, self._inflight):
                for identity in list(mapping):
                    if identity[0] == provider:
                        del mapping[identity]

    def _prune(self):
        current_month = month_start_utc().isoformat()
        for identity, (created, month, result) in list(self._cache.items()):
            monthly = result.get("kind") == KIND_SPEND and result.get("period_start") is not None
            if time.monotonic() - created >= self._ttl or (monthly and month != current_month):
                del self._cache[identity]
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    @staticmethod
    def _completed(result):
        future = Future()
        future.set_result(result)
        return future

    @staticmethod
    def _timed_out(provider):
        return _result(provider, KIND_UNAVAILABLE, source_label="청구 조회 시간 초과")

    def _wait(self, future, provider, deadline):
        try:
            return future.result(timeout=max(0, deadline - time.monotonic()))
        except TimeoutError:
            return self._timed_out(provider)

    def _fetch(self, card_key, secret, fetch, deadline):
        provider = card_key.replace("_api", "")

        def limit_request(request):
            remaining = min(5.0, self._budget, deadline - time.monotonic())
            if remaining <= 0:
                raise httpx.TimeoutException("Billing deadline exceeded")
            request.extensions["timeout"] = dict.fromkeys(("connect", "read", "write", "pool"), remaining)

        if time.monotonic() >= deadline:
            return self._timed_out(provider)
        try:
            with httpx.Client(timeout=5.0, event_hooks={"request": [limit_request]}) as client:
                result = fetch(secret, client)
            return result if time.monotonic() < deadline else self._timed_out(provider)
        except Exception as error:
            logger.debug("spend fetch failed for %s: %s", card_key, type(error).__name__)
            return _result(provider, KIND_UNAVAILABLE, error=type(error).__name__,
                           source_label="조회 실패 (네트워크)")

    def _finish(self, identity, future, month):
        with self._lock:
            self._pending -= 1
            if self._inflight.get(identity) is not future:
                return
            del self._inflight[identity]
            self._cache[identity] = (time.monotonic(), month, future.result())
            self._cache.move_to_end(identity)
            self._prune()

    def _start(self, card_key, secret, fetch, force, deadline):
        provider = FETCHERS.get(card_key, (card_key.replace("_api", ""),))[0]
        identity = (provider, hashlib.sha256(secret.encode("utf-8")).digest())
        with self._lock:
            self._prune()
            if identity in self._inflight:
                if not force or not self._inflight[identity].done():
                    return self._inflight[identity]
                del self._inflight[identity]
            if identity in self._cache and not force:
                self._cache.move_to_end(identity)
                return self._completed(self._cache[identity][2])
            if self._pending >= MAX_PENDING_FETCHES or time.monotonic() >= deadline:
                return self._completed(self._timed_out(provider))
            month = month_start_utc().isoformat()
            future = _SPEND_EXECUTOR.submit(self._fetch, card_key, secret, fetch, deadline)
            self._inflight[identity] = future
            self._pending += 1
            future.add_done_callback(lambda done: self._finish(identity, done, month))
            return future

    def spend_for(self, card_key: str, force: bool = False) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + self._budget
        future = self._card_future(card_key, force, deadline)
        return self._wait(future, card_key.replace("_api", ""), deadline) if future is not None else None

    def _card_future(self, card_key, force, deadline):
        if card_key in NO_SPEND_API:
            return self._completed(_result(
                card_key.replace("_api", ""), KIND_UNAVAILABLE, source_label=NO_SPEND_API[card_key]
            ))
        if card_key not in FETCHERS:
            return None
        key_provider, fetch = FETCHERS[card_key]
        secret = self._get_key(key_provider)
        if not secret:
            if key_provider.endswith("_admin"):
                return self._completed(_result(
                    card_key.replace("_api", ""),
                    KIND_UNAVAILABLE,
                    source_label="관리자 키를 등록하면 공식 사용 금액을 표시",
                ))
            return None
        return self._start(card_key, secret, fetch, force, deadline)

    def spend_for_key(
        self, provider: str, key_id: str, secret: Optional[str], force: bool = False,
    ) -> Dict[str, Any]:
        """Query exactly one registered key, without substituting an admin/default key."""
        deadline = time.monotonic() + self._budget
        return self._wait(self._key_future(provider, secret, force, deadline), provider, deadline)

    def _key_future(self, provider, secret, force, deadline):
        card_key = provider.replace("_admin", "") + "_api"
        fetcher = FETCHERS.get(card_key)
        if not secret or fetcher is None or fetcher[0] != provider:
            return self._completed(_result(provider, KIND_UNAVAILABLE,
                                           source_label="이 키로 공식 사용 금액을 조회할 수 없음"))
        return self._start(card_key, secret, fetcher[1], force, deadline)

    def _spend_secret(self, card_key, secret, fetch, force, cache_key) -> Dict[str, Any]:
        deadline = time.monotonic() + self._budget
        return self._wait(self._start(card_key, secret, fetch, force, deadline),
                          card_key.replace("_api", ""), deadline)

    def spend_all(self, card_keys, force: bool = False) -> Dict[str, Dict[str, Any]]:
        deadline = time.monotonic() + self._budget
        futures = {key: self._card_future(key, force, deadline) for key in dict.fromkeys(card_keys)}
        out = {}
        for key, future in futures.items():
            if future is not None:
                out[key] = self._wait(future, key.replace("_api", ""), deadline)
        return out

    def spend_snapshot(self, entries, force: bool = False) -> Dict[str, Any]:
        """Fetch each secret once and reuse exact key results for legacy cards."""
        deadline = time.monotonic() + self._budget
        identities = {(e["provider"], hashlib.sha256(e["secret"].encode()).digest()) for e in entries}
        with self._lock:
            for mapping in (self._cache, self._inflight):
                for identity in list(mapping):
                    if identity not in identities:
                        del mapping[identity]
        requests, jobs = {}, []
        for entry in entries:
            identity = (entry["provider"], hashlib.sha256(entry["secret"].encode()).digest())
            if identity not in requests:
                requests[identity] = self._key_future(entry["provider"], entry["secret"], force, deadline)
            jobs.append((entry, requests[identity]))
        items = []
        for entry, future in jobs:
            result = self._wait(future, entry["provider"], deadline)
            items.append({**result, "provider": entry["provider"], "key_id": entry["key_id"],
                          "label": entry["label"]})
        cards = {e["provider"].replace("_admin", "") + "_api" for e in entries}
        out = {"items": items}
        for card in sorted(cards):
            provider = FETCHERS.get(card, (card.replace("_api", ""),))[0]
            matching = [item for item in items if item["provider"] == provider]
            chosen = next((item for item in matching if item["key_id"] == "default"), None)
            chosen = chosen or (matching[0] if matching else None)
            out[card] = ({**{key: value for key, value in chosen.items() if key not in ("key_id", "label")},
                          "provider": card.replace("_api", "")} if chosen else _result(
                              card.replace("_api", ""), KIND_UNAVAILABLE,
                              source_label="관리자 키를 등록하면 공식 사용 금액을 표시"))
        return out
