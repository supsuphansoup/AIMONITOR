"""Stored SDK usage reaches detected tools and both desktop worker data paths."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlmodel import select

from backend.app.core.config import settings
from backend.app.models.trace import TraceEvent
from backend.app.routers import metrics, traces
from backend.app.services import aggregator, api_key_manager
from desktop import ai_detector, ai_watcher, app_gui, config, context_manager, display, widget
from sdk.python.aimonitor.client import AIMonitor

NOW = datetime(2026, 10, 14, 3)


@pytest.fixture
def tool_bridge(client, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for variable in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        directory = tmp_path / variable
        directory.mkdir()
        monkeypatch.setenv(variable, str(directory))
    for variable in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AIMONITOR_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(settings, "API_KEY", None)
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", False)
    for module in (aggregator, metrics, traces, context_manager):
        monkeypatch.setattr(module, "utc_now", lambda: NOW)
    monkeypatch.setattr(config, "load_config", lambda: {})
    monkeypatch.setattr(ai_detector, "detector", ai_detector.AIDetector())
    monkeypatch.setattr(ai_detector.AIDetector, "_check_registered_api_keys", lambda self: [])
    monkeypatch.setattr(api_key_manager.api_key_manager, "list_keys", lambda: [])
    monkeypatch.setattr(api_key_manager.spend_service, "spend_all", lambda keys: {})
    monkeypatch.setattr(context_manager, "windows_for", lambda *args, **kwargs: [])
    monkeypatch.setattr(app_gui.accounts, "list_accounts", lambda: [])
    monkeypatch.setattr(app_gui, "account_quotas", lambda **kwargs: [])
    monkeypatch.setattr(widget, "list_accounts", lambda: [])
    monkeypatch.setattr(widget, "account_quotas", lambda **kwargs: [])
    calls = []

    def handle(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.host == "testserver":
            response = client.request(
                request.method, request.url.raw_path.decode(), content=request.content, headers=request.headers,
            )
            return httpx.Response(response.status_code, json=response.json())
        assert request.url.host == "127.0.0.1" and request.url.port == 11434
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.1"}]})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    return SimpleNamespace(home=tmp_path, calls=calls)


def worker_summaries(monkeypatch, ais):
    main_data, widget_data = [], []
    main = SimpleNamespace(
        backend_url="http://testserver", official_spend={}, _is_updating=True,
        after=lambda _delay, callback: callback(), _apply_data=lambda *args: main_data.append(args),
    )
    app_gui.DesktopMainWindow._fetch_worker(main, ais, {}, 1400)
    floating = SimpleNamespace(
        backend_url="http://testserver", _connected_ais=ais, config={}, _fetching=True,
        rate_updater=SimpleNamespace(rate=1400), root=SimpleNamespace(after=lambda _delay, callback: callback()),
        _on_data=lambda *args: widget_data.append(args),
    )
    widget.FloatingWidget._fetch_worker(floating)
    assert main_data[0][4] is True and widget_data[0][1] is True
    assert main._is_updating is False and floating._fetching is False
    return main_data[0][3], widget_data[0][0]


def test_ollama_sdk_usage_reaches_connected_main_and_widget(client, monkeypatch, tool_bridge):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    try:
        stored = monitor.track(provider="ollama", model_name="llama3.1", prompt_tokens=100, completion_tokens=50)
    finally:
        monitor.close()
    assert stored["cost_source"] == "local_free" and stored["total_tokens"] == 150
    ais = ai_detector.detect_connected_ais()
    assert [ai["key"] for ai in ais] == ["ollama"]
    response = client.get("/api/v1/metrics/active_contexts")
    assert response.status_code == 200
    active = response.json()
    assert set(active) == {"ollama"}
    assert active["ollama"]["context_tokens"] == 100
    connected = client.get("/api/v1/metrics/connected_ais")
    assert connected.status_code == 200
    summaries = [connected.json(), *worker_summaries(monkeypatch, ais)]
    for summary in summaries:
        item = summary["by_key"]["ollama"]
        assert summary["total"]["requests_today"] == 1
        assert item["cost_meaning"] == "free" and item["context"]["tokens"] == 100
        for usage in item["usage"].values():
            assert usage["requests"] == 1 and usage["tokens"] == 150
            assert usage["cost_usd"] == 0 and usage["cost_unknown"] == 0
        assert display.usage_cost_text(item["usage"]["today"], "free") == "￦0 (로컬)"
        entries = display.widget_entries(summary)
        assert next(row for row in entries if row["id"] == "ollama:today")["detail"] == "1회 요청"
        assert next(row for row in entries if row["id"] == "ollama:context")["value"].startswith("100/")
    assert ("POST", "testserver", "/api/v1/traces") in tool_bridge.calls


@pytest.mark.parametrize("provider,tags,key", [
    ("ollama", None, "ollama"), ("openai", None, "openai_api"),
    ("custom", None, "custom_api"), ("ollama", ["copilot", "claude-code"], "claude"),
    ("ollama", ["copilot"], "copilot"), ("ollama", ["CODEX"], "ollama"),
])
def test_local_tool_key_preserves_watcher_priority(client, tool_bridge, provider, tags, key):
    response = client.post("/api/v1/traces", json={
        "provider": provider, "model_name": "synthetic", "prompt_tokens": 100, "tags": tags,
    })
    assert response.status_code == 201
    active = client.get("/api/v1/metrics/active_contexts").json()
    assert set(active) == {key}
    assert active[key]["windows"]["5h"]["requests"] == 1
    assert active[key]["accounts"][0]["model_name"] == "synthetic"


@pytest.mark.parametrize("known_cost", [False, True])
def test_copilot_shutdown_requests_reach_connected_main_and_widget(
    client, session, monkeypatch, tool_bridge, known_cost,
):
    path = tool_bridge.home / ".copilot" / "session-state" / "synthetic-session" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "type": "session.shutdown", "id": "synthetic-shutdown", "timestamp": NOW.isoformat() + "Z",
        "data": {"modelMetrics": {
            "gpt-4o" if known_cost else "unpriced-model-one": {
                "requests": {"count": 5}, "usage": {"inputTokens": 100, "outputTokens": 50},
            },
            "unpriced-model-two": {
                "requests": {"count": 2}, "usage": {"inputTokens": 40, "outputTokens": 10},
            },
        }},
    }) + "\n", encoding="utf-8")
    watcher = ai_watcher.UnifiedAIWatcher(
        backend_url="http://testserver", state_file=tool_bridge.home / "state" / "offsets.json",
    )
    assert watcher.scan_all() == 2
    assert watcher.scan_all() == 0
    rows = session.exec(select(TraceEvent)).all()
    assert sorted(json.loads(row.metadata_json)["requests"] for row in rows) == [2, 5]
    unknown = sum(row.cost_source == "unknown" for row in rows)
    expected_cost = round(sum(row.cost_usd for row in rows if row.cost_source != "unknown"), 6)
    assert unknown == (1 if known_cost else 2)
    active = client.get("/api/v1/metrics/active_contexts").json()["copilot"]
    for item in [active, *active["accounts"]]:
        for usage in item["windows"].values():
            assert usage["requests"] == 7 and usage["record_count"] == 2
            assert usage["tokens"] == 200 and usage["cost_usd"] == expected_cost
            assert usage["cost_unknown"] == unknown
    overview = client.get("/api/v1/metrics/overview").json()
    assert overview["total_requests"] == 2 and overview["cost_unknown_count"] == unknown
    assert sum(row["request_count"] for row in client.get("/api/v1/metrics/timeseries").json()) == 2
    assert sum(row["count"] for row in client.get("/api/v1/metrics/breakdown").json()) == 2
    ais = ai_detector.detect_connected_ais()
    summaries = [client.get("/api/v1/metrics/connected_ais").json(), *worker_summaries(monkeypatch, ais)]
    for summary in summaries:
        item = summary["by_key"]["copilot"]
        assert summary["total"]["requests_today"] == 7
        for group in [item, *item["accounts"]]:
            for usage in group["usage"].values():
                assert usage["requests"] == 7 and usage["record_count"] == 2
                assert usage["tokens"] == 200 and usage["cost_unknown"] == unknown
                if known_cost:
                    assert usage["cost_usd"] == expected_cost
                    assert "1건 계산 불가" in display.usage_cost_text(usage, group["cost_meaning"])
                else:
                    assert display.usage_cost_text(usage, group["cost_meaning"]) == "계산 불가"
        lines = display.account_usage_lines(item["accounts"][0], item["cost_meaning"])
        assert all("7회" in line and "계산 불가" in line for line in lines)
        entries = display.widget_entries(summary)
        today = next(row for row in entries if row["id"] == "copilot:today")
        assert today["detail"] == "7회 요청"
        if not known_cost:
            assert today["value"] == "계산 불가"
    if not known_cost:
        assert summaries[0]["by_key"]["copilot"]["usage"]["today"]["cost_usd"] is None
    assert ("POST", "testserver", "/api/v1/traces/batch") in tool_bridge.calls


@pytest.mark.parametrize("metadata,expected", [
    ('{"requests": 5}', 5), ('{"requests": 1}', 1), ('{"requests": 9223372036854775817}', 9223372036854775817),
    ('{"requests": null}', 1), ('{"requests": true}', 1), ('{"requests": false}', 1),
    ('{"requests": 2.0}', 1), ('{"requests": 1.5}', 1), ('{"requests": "5"}', 1),
    ('{"requests": 0}', 1), ('{"requests": -3}', 1), ('{"requests": NaN}', 1),
    ('{"requests": Infinity}', 1), ('{"other": 5}', 1), ('[]', 1), ('null', 1),
    ('broken', 1), ('', 1), (None, 1),
])
def test_copilot_only_weights_valid_positive_integer_requests(session, metadata, expected):
    session.add(TraceEvent(
        trace_id="synthetic", timestamp=NOW, provider="openai", model_name="m", tags="copilot",
        metadata_json=metadata, total_tokens=150, cost_source="custom", cost_usd=0.25,
    ))
    session.commit()
    result = aggregator.MetricAggregator.get_active_contexts(session, now=NOW)["copilot"]
    for group in [result, *result["accounts"]]:
        for usage in group["windows"].values():
            assert usage["requests"] == expected
            assert usage["record_count"] == 1 and usage["tokens"] == 150
            assert usage["cost_usd"] == 0.25 and usage["cost_unknown"] == 0


@pytest.mark.parametrize("tags,key", [("claude-code,copilot", "claude"), ("copilot,CODEX", "copilot")])
def test_copilot_weight_respects_exclusive_tool_classification(session, tags, key):
    session.add(TraceEvent(
        trace_id="synthetic", timestamp=NOW, provider="openai", model_name="m", tags=tags,
        metadata_json='{"requests": 5}',
    ))
    session.commit()
    result = aggregator.MetricAggregator.get_active_contexts(session, now=NOW)
    assert set(result) == {key}
    assert result[key]["windows"]["5h"]["requests"] == (5 if key == "copilot" else 1)


@pytest.mark.parametrize("account_count", [1, 20])
def test_copilot_request_windows_accounts_and_query_count(session, account_count):
    rows = []
    for index in range(account_count):
        for hours, requests in [(0, 5), (6, 2), (192, 3), (-1, 99)]:
            row = TraceEvent(
                trace_id=f"synthetic-{index}-{hours}", timestamp=NOW - timedelta(hours=hours),
                provider="openai", model_name="m", tags="copilot", account_id=f"copilot:synthetic-{index}",
                total_tokens=150, cost_source="custom", cost_usd=0.25,
                metadata_json=json.dumps({"requests": requests}),
            )
            rows.append(row)
            session.add(row)
    session.commit()
    queries, statements = [], []

    def capture(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)
        if context.compiled:
            statements.append(context.compiled.statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = aggregator.MetricAggregator.get_active_contexts(session, now=NOW)["copilot"]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(queries) <= 4  # One BEGIN and at most three SELECTs, regardless of accounts.
    for statement in statements:
        assert str(statement.compile(dialect=postgresql.dialect()))
    assert len(result["accounts"]) == account_count
    for group in [result, *result["accounts"]]:
        for usage in group["windows"].values():
            since = datetime.fromisoformat(usage["since"]).replace(tzinfo=None)
            selected = [row for row in rows if since <= row.timestamp <= NOW and (
                group is result or row.account_id == group["account_id"]
            )]
            assert usage["record_count"] == len(selected)
            assert usage["requests"] == sum(json.loads(row.metadata_json)["requests"] for row in selected)
            assert usage["tokens"] == 150 * len(selected) and usage["cost_usd"] == 0.25 * len(selected)


@pytest.mark.parametrize("record_count,requests,unknown,text", [
    (1, 5, 1, "계산 불가"), (2, 7, 1, "￦0 · 1건 계산 불가"), (2, 7, 0, "￦0"),
    (None, 1, 1, "계산 불가"),
])
def test_copilot_cost_display_uses_record_count_with_legacy_fallback(record_count, requests, unknown, text):
    usage = {"requests": requests, "cost_krw": 0, "cost_unknown": unknown}
    if record_count is not None:
        usage["record_count"] = record_count
    assert display.usage_cost_text(usage, "billed") == text


@pytest.mark.parametrize("sources,unknown,amount", [
    (["unknown", "unknown"], 2, None), (["custom", "unknown"], 1, 0), (["custom", "custom"], 0, 0),
])
def test_copilot_known_zero_and_unknown_costs_stay_distinct(client, session, tool_bridge, sources, unknown, amount):
    (tool_bridge.home / ".copilot" / "session-state" / "synthetic").mkdir(parents=True)
    for index, source in enumerate(sources):
        session.add(TraceEvent(
            trace_id=f"synthetic-{index}", timestamp=NOW, provider="openai", model_name="m", tags="copilot",
            metadata_json=json.dumps({"requests": [5, 2][index]}), cost_source=source,
            cost_usd=123 if source == "unknown" else 0,
        ))
    session.commit()
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    item = response.json()["by_key"]["copilot"]
    for group in [item, *item["accounts"]]:
        usage = group["usage"]["today"]
        assert usage["requests"] == 7 and usage["record_count"] == 2
        assert usage["cost_unknown"] == unknown and usage["cost_usd"] == amount
        assert usage["cost_krw"] == amount
