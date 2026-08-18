from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.config import settings

client = TestClient(app)


def test_root_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION


def test_api_v1_health_check():
    response = client.get(f"{settings.API_V1_STR}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION


def test_spa_fallback():
    """SPA fallback only available when frontend/dist is built. Skip in unit test environments."""
    from pathlib import Path
    import pytest
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not (frontend_dist.exists() and (frontend_dist / "index.html").exists()):
        pytest.skip("frontend/dist not built — skipping SPA fallback test")
    response = client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower() or "<!doctype html>" in response.text.lower()


def test_nonexistent_api_returns_404():
    response = client.get("/api/v1/nonexistent-endpoint")
    assert response.status_code == 404
