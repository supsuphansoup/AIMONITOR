"""Exchange-rate fallback and freshness policy; never calls external services."""
import io
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from desktop import config
from desktop import rate_fetcher as fx

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize("saved", [
    {"krw_per_usd": "bad"}, {"krw_rate_as_of": 123}, {"krw_per_usd": "NaN"},
    {"krw_per_usd": "Infinity"}, {"krw_per_usd": -100},
    {"krw_rate_source": ["bad"], "krw_rate_last_success": 123},
])
def test_bad_saved_rate_fields_fall_back_without_crashing(monkeypatch, saved):
    monkeypatch.setattr(config, "load_config", lambda: saved)
    monkeypatch.setattr(fx, "_shared", None)
    monkeypatch.setattr(fx.AutoRateUpdater, "start", lambda _self: None)
    updater = fx.shared_updater()
    assert math.isfinite(updater.rate) and 100 < updater.rate < 10000
    assert "기본 환율" in updater.describe()


@pytest.mark.parametrize("rate", ["bad", float("nan"), float("inf"), -100, True, 2 ** 2048])
def test_rate_updater_constructor_rejects_invalid_rate(rate):
    updater = fx.AutoRateUpdater(current_rate=rate)
    assert updater.rate == fx.DEFAULT_RATE
    assert "기본 환율" in updater.describe()


def test_offline_first_launch_distinguishes_default_rate(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: dict(config.DEFAULT_CONFIG))
    monkeypatch.setattr(fx, "_shared", None)
    monkeypatch.setattr(fx.AutoRateUpdater, "start", lambda _self: None)
    monkeypatch.setattr(fx, "fetch_usd_krw_rate", lambda: (None, None, None))
    updater = fx.shared_updater()
    assert updater.update_now() is None
    assert "기본 환율" in updater.describe()
    assert "온라인 조회 실패" in updater.describe()
    assert "최종 성공" not in updater.describe()


def payload(rate, date):
    return {"rates": {"KRW": rate}, "time_last_update_unix": date.timestamp() if date else None}


def mock_sources(monkeypatch, data):
    replies = iter(data)
    monkeypatch.setattr(fx, "EXCHANGE_APIS", [(f"https://review.invalid/{i}", f"test-{i}", fx._parse_er_api)
                                            for i in range(len(data))])
    monkeypatch.setattr(fx.urllib.request, "urlopen", lambda *_a, **_kw:
                        io.BytesIO(json.dumps(next(replies)).encode("utf-8")))


@pytest.mark.parametrize("date", [NOW - timedelta(days=8), NOW + timedelta(days=2), None])
def test_stale_future_or_undated_rate_does_not_hide_fresh_fallback(monkeypatch, date):
    mock_sources(monkeypatch, [payload(1200, date), payload(1500, NOW)])
    assert fx.fetch_usd_krw_rate(now=NOW) == (1500, "test-1", NOW)


@pytest.mark.parametrize("date", [NOW - timedelta(days=7), NOW + timedelta(days=1)])
def test_rate_freshness_boundaries_are_inclusive(monkeypatch, date):
    mock_sources(monkeypatch, [payload(1500, date)])
    assert fx.fetch_usd_krw_rate(now=NOW)[0] == 1500


def test_all_stale_sources_use_latest_dated_candidate(monkeypatch):
    newest = NOW - timedelta(days=8)
    mock_sources(monkeypatch, [payload(1200, NOW - timedelta(days=30)), payload(1450, newest),
                              payload(1500, None)])
    assert fx.fetch_usd_krw_rate(now=NOW) == (1450, "test-1", newest)


def test_only_undated_rate_is_marked_stale(monkeypatch):
    mock_sources(monkeypatch, [payload(1400, None)])
    result = fx.fetch_usd_krw_rate(now=NOW)
    monkeypatch.setattr(fx, "fetch_usd_krw_rate", lambda: result)
    monkeypatch.setattr(config, "update_config", lambda _patch: {})
    updater = fx.AutoRateUpdater()
    assert updater.update_now() == 1400
    assert "오래됨" in updater.describe()
    assert "기준일 확인 불가" in updater.describe()


def test_failed_refresh_keeps_rate_and_last_success_and_notifies_ui(monkeypatch):
    patches, notices = [], []
    monkeypatch.setattr(config, "update_config", lambda patch: patches.append(patch))
    replies = iter([(1500, "test-source", NOW), (None, None, None)])
    monkeypatch.setattr(fx, "fetch_usd_krw_rate", lambda: next(replies))
    updater = fx.AutoRateUpdater(on_rate_updated=lambda *_args: notices.append(updater.describe()))
    assert updater.update_now() == 1500
    succeeded = updater.last_success_at
    assert "최종 성공" in updater.describe()
    assert patches[0]["krw_rate_last_success"] == succeeded.isoformat()
    assert updater.update_now() is None
    assert updater.rate == 1500 and updater.last_success_at == succeeded
    assert "최종 성공" in updater.describe() and "온라인 조회 실패" in updater.describe()
    assert len(notices) == 2 and len(patches) == 1


def test_saved_success_time_is_restored(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {
        "krw_per_usd": 1500, "krw_rate_source": "test-source",
        "krw_rate_as_of": NOW.isoformat(), "krw_rate_last_success": NOW.isoformat()})
    monkeypatch.setattr(fx, "_shared", None)
    monkeypatch.setattr(fx.AutoRateUpdater, "start", lambda _self: None)
    updater = fx.shared_updater()
    assert updater.last_success_at == NOW
    assert "최종 성공" in updater.describe()
