"""Window boundaries and mutually exclusive tool classification."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from backend.app.models.trace import TraceEvent
from backend.app.services.aggregator import MetricAggregator

NOW = datetime(2026, 10, 14, 3)


def add_trace(session, name, **kwargs):
    values = {
        "timestamp": NOW, "provider": "openai", "model_name": "m",
        "total_tokens": 10, "cost_source": "custom", "cost_usd": 1.0,
    }
    session.add(TraceEvent(trace_id=name, **{**values, **kwargs}))
    session.commit()


@pytest.mark.parametrize("endpoint", ["overview", "timeseries", "breakdown"])
def test_rolling_window_excludes_future_and_reads_now_once(client, session, monkeypatch, endpoint):
    calls = []

    def now():
        calls.append(True)
        return NOW

    monkeypatch.setattr("backend.app.services.aggregator.utc_now", now)
    monkeypatch.setattr("backend.app.routers.metrics.utc_now", now)
    add_trace(session, "start", timestamp=NOW - timedelta(hours=1))
    add_trace(session, "now")
    add_trace(session, "future", timestamp=NOW + timedelta(microseconds=1))
    add_trace(session, "old", timestamp=NOW - timedelta(hours=1, microseconds=1))
    response = client.get(f"/api/v1/metrics/{endpoint}", params={"hours_back": 1})
    assert response.status_code == 200
    body = response.json()
    count = (body["total_requests"] if endpoint == "overview" else
             sum(row["request_count" if endpoint == "timeseries" else "count"] for row in body))
    assert count == 2
    assert len(calls) == 1


@pytest.mark.parametrize("params", [
    {},
    {"start_time": NOW.isoformat()},
    {"start_time": NOW.isoformat(), "hours_back": 1},
    {"end_time": (NOW + timedelta(days=2)).isoformat(), "hours_back": 1},
])
def test_explicit_and_all_time_overview_contracts(client, session, monkeypatch, params):
    monkeypatch.setattr("backend.app.routers.metrics.utc_now", lambda: NOW)
    add_trace(session, "now")
    add_trace(session, "future", timestamp=NOW + timedelta(days=1))
    response = client.get("/api/v1/metrics/overview", params=params)
    assert response.status_code == 200
    assert response.json()["total_requests"] == 2


@pytest.mark.parametrize("tags,key", [
    (None, "openai_api"), ("", "openai_api"), ("CODEX", "openai_api"),
    ("Codex", "openai_api"), ("prefix-codex", "openai_api"), ("codex-suffix", "openai_api"),
    ("junk,codex,other", "codex"), ("codex,codex", "codex"),
    ("codex,claude-code", "claude"), ("claude-code,codex", "claude"),
    ("copilot,cursor,antigravity,codex", "antigravity"), ("cursor,copilot", "copilot"),
])
def test_tool_tags_are_case_sensitive_and_exclusive(session, tags, key):
    add_trace(session, "tagged", tags=tags)
    result = MetricAggregator.get_active_contexts(session, now=NOW)
    assert set(result) == {key}
    assert result[key]["windows"]["5h"]["requests"] == 1


def test_tool_request_totals_match_source_and_accounts(session):
    for index, tags in enumerate(["claude-code,codex", "copilot,cursor", "CODEX", "codex", None]):
        add_trace(session, str(index), tags=tags, account_id=None if index % 2 else "codex:dir-a")
    result = MetricAggregator.get_active_contexts(session, now=NOW)
    assert sum(item["windows"]["5h"]["requests"] for item in result.values()) == 5
    for item in result.values():
        assert item["windows"]["5h"]["requests"] == sum(
            account["windows"]["5h"]["requests"] for account in item["accounts"]
        )


def test_active_context_queries_compile_for_postgres(session):
    add_trace(session, "tagged", tags="codex,claude-code")
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if context.compiled:
            statements.append(context.compiled.statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        MetricAggregator.get_active_contexts(session, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements
    for statement in statements:
        assert str(statement.compile(dialect=postgresql.dialect()))
