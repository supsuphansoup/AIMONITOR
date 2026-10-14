"""Secrets must not escape through exception or third-party request logs."""
import logging

import httpx
from sqlalchemy import text

import backend.app.db.session as db_module
import backend.app.routers.metrics as metrics_router


def test_runtime_engine_hides_bound_parameters():
    assert db_module.engine.hide_parameters is True


def test_database_failure_logs_do_not_include_webhook(client, session, caplog):
    session.exec(text("PRAGMA query_only=ON"))
    try:
        response = client.post("/api/v1/alerts/rules", json={
            "name": "synthetic", "metric_type": "cost", "threshold_value": 1,
            "webhook_url": "https://hooks.slack.com/services/FAKE/PRIVATE_WEBHOOK",
        })
        assert response.status_code == 500
        assert "PRIVATE_WEBHOOK" not in response.text
        assert "PRIVATE_WEBHOOK" not in caplog.text
    finally:
        session.rollback()
        session.exec(text("PRAGMA query_only=OFF"))


def test_unhandled_exception_logs_only_safe_diagnostics(client, monkeypatch, caplog):
    def fail(*args, **kwargs):
        try:
            raise ValueError("PRIVATE_CAUSE")
        except ValueError as cause:
            raise RuntimeError("PRIVATE_EXCEPTION") from cause

    monkeypatch.setattr(metrics_router.MetricAggregator, "get_overview_metrics", fail)
    response = client.get("/api/v1/metrics/overview")
    assert response.status_code == 500
    assert "PRIVATE" not in response.text
    assert "PRIVATE" not in caplog.text
    errors = [record for record in caplog.records if record.name == "backend.app.main"
              and record.levelno >= logging.ERROR]
    assert errors and all(record.exc_info is None for record in errors)
    assert "RuntimeError" in caplog.text


def test_readiness_logs_only_exception_type(monkeypatch, caplog):
    def fail():
        raise RuntimeError("PRIVATE_DATABASE")

    monkeypatch.setattr(db_module.engine, "connect", fail)
    assert db_module.check_db() is False
    assert "PRIVATE_DATABASE" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_http_clients_do_not_log_secret_request_urls(caplog):
    caplog.set_level(logging.INFO)
    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    with httpx.Client(transport=transport) as client:
        client.get("https://hooks.example/PRIVATE_WEBHOOK?token=PRIVATE_QUERY")
    logging.getLogger("httpcore").info("PRIVATE_TRANSPORT_DIAGNOSTIC")
    assert "PRIVATE" not in caplog.text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_uvicorn_default_access_logger_is_disabled(caplog):
    logger = logging.getLogger("uvicorn.access")
    logger.info("GET /health?token=PRIVATE_QUERY")
    assert logger.disabled
    assert not logger.propagate
    assert "PRIVATE_QUERY" not in caplog.text
