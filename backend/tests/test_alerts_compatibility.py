"""Keep legacy alias semantics and document the manual-evaluation contract."""
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.core.timeutil import utc_now
from backend.app.models.trace import TraceEvent


@pytest.mark.parametrize("window,expected", [(5, 2.0), (30, 5.0)])
def test_cost_hourly_remains_window_sum_without_hourly_conversion(client, session, window, expected):
    now = utc_now()
    for index, minutes, cost in [(1, 2, 2.0), (2, 20, 3.0)]:
        session.add(TraceEvent(trace_id=f"cost-{index}", provider="test", model_name="test",
                               cost_usd=cost, cost_source="manual", timestamp=now - timedelta(minutes=minutes)))
    session.commit()
    client.post("/api/v1/alerts/rules", json={"name": "legacy", "metric_type": "cost_hourly", "operator": ">",
                                            "threshold_value": 0, "window_minutes": window})
    fired = client.post("/api/v1/alerts/evaluate").json()
    assert len(fired) == 1 and fired[0]["triggered_value"] == expected


def test_spec_labels_cost_hourly_as_a_backward_compatible_window_alias():
    spec = (Path(__file__).resolve().parents[2] / "docs/spec/alerts.md").read_text(encoding="utf-8")
    statement = next(line for line in spec.splitlines() if line.startswith("- `cost_hourly`"))
    assert "하위 호환" in statement and "window_minutes" in statement


def test_spec_limits_unchanged_cooldown_to_explicit_ignore_cooldown():
    spec = (Path(__file__).resolve().parents[2] / "docs/spec/alerts.md").read_text(encoding="utf-8")
    requirement = next(line for line in spec.splitlines() if line.startswith("- ALR-5"))
    assert "기본" in requirement and "ignore_cooldown=true" in requirement
