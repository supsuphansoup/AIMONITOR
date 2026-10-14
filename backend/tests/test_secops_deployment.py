"""Deployment inputs must exclude local secrets and SQLite sidecars."""
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _docker_ignored(path):
    ignored = False
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        pattern = pattern.lstrip("!").rstrip("/")
        if fnmatchcase(path, pattern) or path.startswith(pattern + "/"):
            ignored = not negate
    return ignored


@pytest.mark.parametrize("path", [
    "aimonitor.db-wal", "aimonitor.db-shm", "aimonitor.db-journal", "history.sqlite-wal",
    ".env.production", ".env.secrets", ".review/secops.json",
])
def test_docker_context_excludes_private_state(path):
    assert _docker_ignored(path)


def test_docker_context_keeps_environment_example():
    assert not _docker_ignored(".env.example")


def test_docker_disables_raw_access_logs():
    assert '"--no-access-log"' in (ROOT / "Dockerfile").read_text(encoding="utf-8")
