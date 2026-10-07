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
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import httpx

from backend.app.core.timeutil import to_aware_utc, utc_now

logger = logging.getLogger("aimonitor.provider_spend")

CACHE_TTL_SECONDS = 15 * 60
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
    currency = "USD"
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
        body = resp.json()
        for bucket in body.get("data") or []:
            for r in bucket.get("results") or []:
                # `amount` is a decimal string in the currency's lowest unit (cents).
                total_cents += _amount(r.get("amount"))
                currency = r.get("currency") or currency
        if not body.get("has_more") or not body.get("next_page"):
            break
        params["page"] = body["next_page"]
    else:
        return _result("anthropic", KIND_UNAVAILABLE, source_label="비용 리포트 페이지 상한 초과")
    return _result(
        "anthropic",
        KIND_SPEND,
        amount=round(total_cents / 100.0, 4),
        currency=currency,
        period_start=start.isoformat(),
        source_label="Anthropic 공식 비용 리포트 (이번 달)",
    )


def fetch_openai_cost(admin_key: str, client: httpx.Client, now: Optional[datetime] = None) -> Dict[str, Any]:
    start = month_start_utc(now)
    params: Dict[str, Any] = {"start_time": int(start.timestamp()), "bucket_width": "1d", "limit": 31}
    headers = {"Authorization": f"Bearer {admin_key}"}
    total = 0.0
    currency = "usd"
    for _ in range(MAX_PAGES):
        resp = client.get("https://api.openai.com/v1/organization/costs", params=params, headers=headers)
        if resp.status_code != 200:
            return _result(
                "openai",
                KIND_UNAVAILABLE,
                error=f"HTTP {resp.status_code}",
                source_label="OpenAI 비용 조회 실패",
            )
        body = resp.json()
        for bucket in body.get("data") or []:
            for r in bucket.get("results") or []:
                amount = r.get("amount") or {}
                total += _amount(amount.get("value"))
                currency = amount.get("currency") or currency
        if not body.get("has_more") or not body.get("next_page"):
            break
        params["page"] = body["next_page"]
    else:
        return _result("openai", KIND_UNAVAILABLE, source_label="비용 리포트 페이지 상한 초과")
    return _result(
        "openai",
        KIND_SPEND,
        amount=round(total, 4),
        currency=currency.upper(),
        period_start=start.isoformat(),
        source_label="OpenAI 공식 비용 (이번 달)",
    )


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
            amount=float(monthly),
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
    first = infos[0]
    return _result(
        "deepseek",
        KIND_BALANCE,
        amount=_amount(first.get("total_balance")),
        currency=first.get("currency") or "CNY",
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
    def __init__(self, get_key: Callable[[str], Optional[str]], ttl: float = CACHE_TTL_SECONDS):
        self._get_key = get_key
        self._ttl = ttl
        self._cache: Dict[Any, tuple] = {}
        self._lock = threading.Lock()

    def spend_for(self, card_key: str, force: bool = False) -> Optional[Dict[str, Any]]:
        if card_key in NO_SPEND_API:
            return _result(
                card_key.replace("_api", ""), KIND_UNAVAILABLE, source_label=NO_SPEND_API[card_key]
            )
        if card_key not in FETCHERS:
            return None
        key_provider, fetch = FETCHERS[card_key]
        secret = self._get_key(key_provider)
        if not secret:
            if key_provider.endswith("_admin"):
                return _result(
                    card_key.replace("_api", ""),
                    KIND_UNAVAILABLE,
                    source_label="관리자 키를 등록하면 공식 사용 금액을 표시",
                )
            return None
        return self._spend_secret(card_key, secret, fetch, force, card_key)

    def spend_for_key(
        self, provider: str, key_id: str, secret: Optional[str], force: bool = False,
    ) -> Dict[str, Any]:
        """Query exactly one registered key, without substituting an admin/default key."""
        card_key = provider.replace("_admin", "") + "_api"
        fetcher = FETCHERS.get(card_key)
        if not secret or fetcher is None or fetcher[0] != provider:
            return _result(provider, KIND_UNAVAILABLE, source_label="이 키로 공식 사용 금액을 조회할 수 없음")
        return self._spend_secret(card_key, secret, fetcher[1], force, (provider, key_id))

    def _spend_secret(self, card_key, secret, fetch, force, cache_key) -> Dict[str, Any]:
        identity = hashlib.sha256(secret.encode("utf-8")).digest()
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit and hit[1] == identity and not force and time.monotonic() - hit[0] < self._ttl:
                return hit[2]
        try:
            with httpx.Client(timeout=10.0) as client:
                result = fetch(secret, client)
        except Exception as e:
            # Never log the exception text: an HTTP error can echo request headers.
            logger.debug("spend fetch failed for %s: %s", card_key, type(e).__name__)
            result = _result(
                card_key.replace("_api", ""),
                KIND_UNAVAILABLE,
                error=type(e).__name__,
                source_label="조회 실패 (네트워크)",
            )
        with self._lock:
            self._cache[cache_key] = (time.monotonic(), identity, result)
        return result

    def spend_all(self, card_keys, force: bool = False) -> Dict[str, Dict[str, Any]]:
        out = {}
        for k in card_keys:
            r = self.spend_for(k, force=force)
            if r is not None:
                out[k] = r
        return out
