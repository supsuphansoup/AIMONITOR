"""Shared fixtures.

Each test gets its own in-memory database so ordering never matters. Modules
that predate this file define their own `session`/`client` fixtures, which take
precedence automatically.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from backend.app.core.config import settings
from backend.app.db.session import get_session
from backend.app.main import app


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(name="client")
def client_fixture(session: Session):
    # Constructed without the context manager on purpose: entering it would run
    # the lifespan, which touches the real database and starts the scheduler.
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(name="api_key")
def api_key_fixture(monkeypatch: pytest.MonkeyPatch) -> str:
    """Turn on API key auth for the duration of one test."""
    key = "test-secret-key"
    monkeypatch.setattr(settings, "API_KEY", key)
    return key
