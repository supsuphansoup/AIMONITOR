"""Even readiness must use a disposable DB rather than deployment settings."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("configured", [False, True])
def test_readiness_test_does_not_open_deployment_database(tmp_path, configured):
    repo = Path(__file__).resolve().parents[2]
    deployment_db = tmp_path / "deployment.db"
    env = dict(os.environ)
    for name in ("DATABASE_URL", "API_KEY", "CORS_ORIGINS", "ALLOWED_HOSTS"):
        env.pop(name, None)
    if configured:
        env["DATABASE_URL"] = f"sqlite:///{deployment_db.as_posix()}"
    env.update(
        USERPROFILE=str(tmp_path), HOME=str(tmp_path), APPDATA=str(tmp_path / "roaming"),
        LOCALAPPDATA=str(tmp_path / "local"), PYTHONDONTWRITEBYTECODE="1",
        SCHEDULER_ENABLED="false",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         str(repo / "backend/tests/test_system.py") + "::test_readiness_reports_database",
         "-o", f"cache_dir={tmp_path / 'cache'}", "--tb=short"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "aimonitor.db").exists()
    assert not deployment_db.exists()
