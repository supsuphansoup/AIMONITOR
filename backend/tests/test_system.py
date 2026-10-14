"""System endpoints: readiness, info, pricing, retention."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.core.timeutil import utc_now
from backend.app.models.alert import AlertHistory
from backend.app.models.trace import TraceEvent
from backend.app.services.cost import PRICING_AS_OF
from backend.app.services.retention import purge_older_than

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 5}


def test_readiness_reports_database(client: TestClient):
    res = client.get("/api/v1/system/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": "ok"}


def test_system_info_exposes_runtime_config(client: TestClient):
    body = client.get("/api/v1/system/info").json()
    assert body["app"] == "AIMONITOR"
    assert body["auth_enabled"] is False
    assert body["priced_models"] > 0
    assert "scheduler" in body


def test_pricing_table_is_auditable(client: TestClient):
    body = client.get("/api/v1/system/pricing").json()
    assert body["unit"].startswith("USD per 1,000,000")
    assert body["models"]["gpt-4o"]["input"] == 2.50
    assert body["models"]["claude-3-5-sonnet"]["cache_read"] == 0.30
    assert body["provider_cache_multipliers"]["anthropic"]["cache_read"] == 0.10
    assert "ollama" in body["local_free_providers"]
    # The card says how old it is, so a stale price can be spotted.
    assert body["as_of"] == PRICING_AS_OF
    assert body["sources"]["anthropic"].startswith("https://")
    # Open weights are free by provider, so no model in the rate card may be priced at zero.
    assert all(m["input"] > 0 or m["output"] > 0 for m in body["models"].values())


def test_purge_removes_only_old_rows(client: TestClient, session: Session):
    client.post("/api/v1/traces", json=TRACE)
    old = TraceEvent(
        trace_id="ancient", provider="openai", model_name="gpt-4o",
        timestamp=utc_now() - timedelta(days=45),
    )
    session.add(old)
    session.commit()
    assert len(session.exec(select(TraceEvent)).all()) == 2

    result = client.post("/api/v1/system/purge?days=30").json()
    assert result["trace_events"] == 1
    remaining = session.exec(select(TraceEvent)).all()
    assert len(remaining) == 1
    assert remaining[0].trace_id != "ancient"


def test_purge_rejects_non_positive_days(client: TestClient):
    assert client.post("/api/v1/system/purge?days=0").status_code == 422


def test_purge_helper_is_a_noop_when_disabled(session: Session):
    session.add(TraceEvent(trace_id="t", provider="p", model_name="m",
                           timestamp=utc_now() - timedelta(days=999)))
    session.commit()
    assert purge_older_than(session, 0) == {"trace_events": 0, "alert_histories": 0}
    assert len(session.exec(select(TraceEvent)).all()) == 1


def test_purge_also_trims_alert_history(session: Session):
    session.add(AlertHistory(
        rule_id=1, rule_name="old", triggered_value=1.0, message="m",
        created_at=utc_now() - timedelta(days=10),
    ))
    session.commit()
    assert purge_older_than(session, 5)["alert_histories"] == 1
    assert session.exec(select(AlertHistory)).all() == []


@pytest.mark.parametrize("days", [utc_now().toordinal(), 999999999, 10**30])
def test_purge_rejects_out_of_range_days_without_deleting(client, session, days):
    session.add(TraceEvent(trace_id="keep", provider="p", model_name="m"))
    session.commit()
    assert client.post(f"/api/v1/system/purge?days={days}").status_code == 422
    assert len(session.exec(select(TraceEvent)).all()) == 1


@pytest.mark.parametrize("days", [-1, utc_now().toordinal(), 999999999, 10**30])
def test_retention_configuration_rejects_invalid_days(days):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, RETENTION_DAYS=days)


def test_largest_representable_retention_period_is_accepted(client):
    days = utc_now().toordinal() - 1
    assert Settings(_env_file=None, RETENTION_DAYS=days).RETENTION_DAYS == days
    assert client.post(f"/api/v1/system/purge?days={days}").status_code == 200
