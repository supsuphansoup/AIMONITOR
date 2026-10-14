"""Root test setup, loaded before any test module imports the app."""
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# Protect direct engine users (including readiness) before any app imports.
_test_database = TemporaryDirectory(prefix="aimonitor-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(_test_database.name) / 'test.db').as_posix()}"

# Starlette's TestClient sends `Host: testserver`; it must be explicitly allowed.
os.environ.setdefault("ALLOWED_HOSTS", "testserver")


def pytest_sessionfinish(session, exitstatus):
    module = sys.modules.get("backend.app.db.session")
    if module is not None:
        module.engine.dispose()
    _test_database.cleanup()
