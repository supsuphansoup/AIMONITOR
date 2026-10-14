"""Deterministic query-count and accuracy checks for grouped usage aggregation."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from backend.app.models.trace import TraceEvent
from backend.app.services.aggregator import MetricAggregator

NOW = datetime(2026, 10, 14, 3)


@pytest.mark.parametrize("account_count", [1, 20])
def test_active_context_query_count_is_constant(session, account_count):
    for index in range(account_count):
        session.add(TraceEvent(
            trace_id=str(index), timestamp=NOW, provider="openai", model_name="m", tags="codex",
            account_id=f"codex:dir-{index:03d}", total_tokens=10, cost_source="custom", cost_usd=1,
        ))
    session.commit()
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = MetricAggregator.get_active_contexts(session, now=NOW)["codex"]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(queries) <= 3, len(queries)  # At most one BEGIN, one totals and one latest SELECT.
    assert len(result["accounts"]) == account_count
    assert result["windows"]["5h"]["requests"] == account_count
    assert result["windows"]["5h"]["cost_usd"] == account_count


def test_grouped_usage_matches_windows_accounts_and_latest(session):
    rows = []
    for index, hours in enumerate([0, 1, 6, 23, 24, 100, 168, 169, 400, 9000]):
        for account in (None, "codex:dir-work"):
            row = TraceEvent(
                trace_id=f"{index}-{account}", timestamp=NOW - timedelta(hours=hours),
                provider="openai", model_name=f"m-{index}", tags="codex",
                account_id=account, prompt_tokens=100 + index, cache_read_tokens=3, cache_write_tokens=4,
                total_tokens=10 + index, cost_source="unknown" if index % 2 else "custom",
                cost_usd=123 if index % 2 else 0.25,
            )
            session.add(row)
            rows.append(row)
    session.add(TraceEvent(
        trace_id="future", timestamp=NOW + timedelta(seconds=1), provider="openai", model_name="future",
        tags="codex", account_id="codex:dir-future", total_tokens=999,
    ))
    session.commit()
    result = MetricAggregator.get_active_contexts(session, now=NOW)["codex"]
    assert len(result["accounts"]) == 2
    assert result["context_tokens"] == 107 and result["model_name"] == "m-0"
    assert result["accounts"][0]["account_id"] is None
    for group, candidates in [(result, rows)] + [
        (account, [row for row in rows if row.account_id == account["account_id"]])
        for account in result["accounts"]
    ]:
        for name, window in group["windows"].items():
            since = datetime.fromisoformat(window["since"]).replace(tzinfo=None)
            selected = [row for row in candidates if since <= row.timestamp <= NOW]
            assert window["requests"] == len(selected), name
            assert window["tokens"] == sum(row.total_tokens for row in selected), name
            assert window["cost_usd"] == sum(row.cost_usd for row in selected if row.cost_source != "unknown")
            assert window["cost_unknown"] == sum(row.cost_source == "unknown" for row in selected)
            earliest = min((row.timestamp for row in selected), default=None)
            assert window["earliest"] == (earliest.isoformat() + "+00:00" if earliest else None)


def test_old_account_retains_latest_context_with_empty_windows(session):
    session.add(TraceEvent(
        trace_id="old", timestamp=NOW - timedelta(days=400), provider="p", model_name="old",
        account_id="codex:dir-old", prompt_tokens=50,
    ))
    session.commit()
    result = MetricAggregator.get_active_contexts(session, now=NOW)["p_api"]
    assert result["model_name"] == "old" and result["context_tokens"] == 50
    assert len(result["accounts"]) == 1
    assert all(window["requests"] == 0 and window["earliest"] is None for window in result["windows"].values())
