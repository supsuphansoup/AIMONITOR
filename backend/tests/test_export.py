"""CSV export: shape, privacy, and how an unpriced call is represented."""
import csv
import io

from fastapi.testclient import TestClient

from backend.app.routers.traces import CSV_COLUMNS

PRICED = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 1000,
          "completion_tokens": 500, "latency_ms": 200.0}
UNPRICED = {"provider": "acme", "model_name": "mystery-v9", "prompt_tokens": 1000}


def _read(res) -> list:
    return list(csv.DictReader(io.StringIO(res.text)))


def test_export_returns_a_csv_attachment(client: TestClient):
    client.post("/api/v1/traces", json=PRICED)
    res = client.get("/api/v1/traces/export.csv")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    assert ".csv" in res.headers["content-disposition"]


def test_export_header_matches_declared_columns(client: TestClient):
    client.post("/api/v1/traces", json=PRICED)
    reader = csv.reader(io.StringIO(client.get("/api/v1/traces/export.csv").text))
    assert next(reader) == CSV_COLUMNS


def test_export_omits_content_bearing_columns(client: TestClient):
    """An export is the easiest route for trace content to leave the system."""
    client.post("/api/v1/traces", json={
        **PRICED, "status": "ERROR", "error_message": "upstream said SECRET",
        "metadata": {"user_id": "u1"},
    })
    body = client.get("/api/v1/traces/export.csv").text

    assert "error_message" not in CSV_COLUMNS
    assert "metadata_json" not in CSV_COLUMNS
    assert "SECRET" not in body


def test_unpriced_calls_export_as_blank_not_zero(client: TestClient):
    """Blank keeps a spreadsheet from summing "unknown" as if it were free."""
    client.post("/api/v1/traces", json=UNPRICED)
    row = _read(client.get("/api/v1/traces/export.csv"))[0]

    assert row["cost_source"] == "unknown"
    assert row["cost_usd"] == ""


def test_priced_calls_export_a_number(client: TestClient):
    client.post("/api/v1/traces", json=PRICED)
    row = _read(client.get("/api/v1/traces/export.csv"))[0]

    assert row["cost_source"] == "pricing_table"
    assert float(row["cost_usd"]) > 0


def test_export_timestamps_carry_utc_offset(client: TestClient):
    client.post("/api/v1/traces", json=PRICED)
    row = _read(client.get("/api/v1/traces/export.csv"))[0]
    assert row["timestamp"].endswith("+00:00")


def test_export_respects_filters_and_limit(client: TestClient):
    for _ in range(3):
        client.post("/api/v1/traces", json=PRICED)
    client.post("/api/v1/traces", json=UNPRICED)

    assert len(_read(client.get("/api/v1/traces/export.csv?provider=acme"))) == 1
    assert len(_read(client.get("/api/v1/traces/export.csv?limit=2"))) == 2


def test_export_of_empty_table_is_header_only(client: TestClient):
    res = client.get("/api/v1/traces/export.csv")
    assert res.text.strip() == ",".join(CSV_COLUMNS)


def test_export_is_not_shadowed_by_the_trace_id_route(client: TestClient):
    """`/traces/export.csv` must not be read as a trace_id lookup."""
    res = client.get("/api/v1/traces/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")


def test_export_neutralises_spreadsheet_formulas(client: TestClient):
    """Caller-chosen strings must not run as formulas when the CSV is opened."""
    client.post("/api/v1/traces", json={
        "provider": "acme",
        "model_name": '=HYPERLINK("http://evil.example","x")',
        "trace_id": "-2+3",
        "session_id": "+cmd",
        "tags": ["@SUM(A1)"],
    })
    row = _read(client.get("/api/v1/traces/export.csv"))[0]

    assert row["model_name"] == '\'=HYPERLINK("http://evil.example","x")'
    assert row["trace_id"] == "'-2+3"
    assert row["session_id"] == "'+cmd"
    assert row["tags"] == "'@SUM(A1)"


def test_export_leaves_ordinary_values_and_numbers_alone(client: TestClient):
    client.post("/api/v1/traces", json={**PRICED, "session_id": "s-1", "tags": ["prod"]})
    row = _read(client.get("/api/v1/traces/export.csv"))[0]

    assert row["model_name"] == "gpt-4o"
    assert row["session_id"] == "s-1"
    assert row["tags"] == "prod"
    assert float(row["cost_usd"]) > 0
