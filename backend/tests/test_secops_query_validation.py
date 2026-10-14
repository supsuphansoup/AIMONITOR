"""Query validation must match the safe 422 array and OpenAPI contract (GC-04)."""
import pytest

DATE_PATHS = ("/metrics/overview", "/traces", "/traces/export.csv")
QUERY_PATHS = (*DATE_PATHS, "/metrics/timeseries", "/metrics/breakdown")


def assert_query_validation(response, field, error_type=None):
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert isinstance(errors, list) and len(errors) == 1
    error = errors[0]
    assert set(error) == {"loc", "type", "msg"}
    assert error["loc"] == ["query", field]
    assert error["msg"] == "Invalid field value"
    assert isinstance(error["type"], str) and error["type"]
    if error_type is not None:
        assert error["type"] == error_type


@pytest.mark.parametrize("path", DATE_PATHS)
@pytest.mark.parametrize("field", ["start_time", "end_time"])
@pytest.mark.parametrize("stamp", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_utc_range_errors_use_safe_query_validation_array(client, path, field, stamp):
    response = client.get("/api/v1" + path, params={field: stamp})
    assert_query_validation(response, field, "datetime_parsing")
    assert stamp not in response.text


@pytest.mark.parametrize("path", DATE_PATHS)
@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_unparseable_dates_do_not_echo_input(client, path, field):
    private = "SYNTHETIC_PRIVATE_QUERY_DATE"
    response = client.get("/api/v1" + path, params={field: private})
    assert_query_validation(response, field)
    assert private not in response.text


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_conflicting_account_filters_use_safe_query_validation_array(client, path):
    account_id = "codex:SYNTHETIC_PRIVATE_ACCOUNT"
    response = client.get("/api/v1" + path, params={"account_id": account_id, "account_unknown": "true"})
    assert_query_validation(response, "account_unknown", "value_error")
    assert account_id not in response.text


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_invalid_account_filter_does_not_echo_input(client, path):
    private = "SYNTHETIC_PRIVATE_INVALID_ACCOUNT"
    response = client.get("/api/v1" + path, params={"account_id": private})
    assert_query_validation(response, "account_id")
    assert private not in response.text


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_normal_query_constraints_keep_safe_array(client, path):
    field = "limit" if path.startswith("/traces") else "hours_back"
    response = client.get("/api/v1" + path, params={field: "0"})
    assert_query_validation(response, field)


@pytest.mark.parametrize("path", ["/traces", "/traces/export.csv"])
def test_invalid_status_filter_uses_safe_query_validation_array(client, path):
    private = "SYNTHETIC_PRIVATE_STATUS"
    response = client.get("/api/v1" + path, params={"status": private})
    assert_query_validation(response, "status", "value_error")
    assert private not in response.text


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_query_422_matches_published_openapi_schema(client, path):
    api_path = "/api/v1" + path
    schema = client.get("/api/v1/openapi.json").json()
    advertised = schema["paths"][api_path]["get"]["responses"]["422"]["content"]["application/json"]["schema"]
    assert advertised == {"$ref": "#/components/schemas/HTTPValidationError"}
    components = schema["components"]["schemas"]
    detail_schema = components["HTTPValidationError"]["properties"]["detail"]
    assert detail_schema["type"] == "array"
    assert detail_schema["items"] == {"$ref": "#/components/schemas/ValidationError"}
    error_schema = components["ValidationError"]
    assert set(error_schema["required"]) == {"loc", "msg", "type"}
    properties = error_schema["properties"]
    assert properties["loc"]["type"] == "array"
    assert {item["type"] for item in properties["loc"]["items"]["anyOf"]} == {"string", "integer"}
    assert properties["msg"]["type"] == properties["type"]["type"] == "string"
    response = client.get(api_path, params={"account_id": "codex:synthetic", "account_unknown": "true"})
    assert_query_validation(response, "account_unknown", "value_error")
