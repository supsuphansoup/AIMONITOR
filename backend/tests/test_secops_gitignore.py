"""A fresh clone must exclude private local state without global rules (GP-05)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_git(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith("GIT_"):
            environment.pop(name)
    empty = tmp_path / "empty-config"
    empty.touch()
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=str(empty), GIT_CONFIG_GLOBAL=str(empty))
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    command = [git, "-c", f"core.excludesFile={empty.as_posix()}"]
    subprocess.run([*command, "init", "-q", "--template="], cwd=repository, env=environment, check=True)
    return command, environment, repository


@pytest.mark.parametrize("path, ignored", [
    (".env", True),
    (".env.prod", True),
    (".env.test", True),
    ("config/.env.production", True),
    ("aimonitor.db", True),
    ("account.sqlite", True),
    ("nested/account.sqlite", True),
    ("account.sqlite3", True),
    ("aimonitor.db-wal", True),
    ("account.sqlite-wal", True),
    ("account.sqlite-shm", True),
    ("aimonitor.db-journal", True),
    ("nested/account.sqlite-journal", True),
    (".review/secops-fix2.md", True),
    (".env.example", False),
    ("config/.env.example", False),
    ("backend/app/main.py", False),
    ("docs/.review/notes.md", False),
])
def test_fresh_git_repository_excludes_only_local_private_state(isolated_git, path, ignored):
    command, environment, repository = isolated_git
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    result = subprocess.run([*command, "check-ignore", "--no-index", "-q", "--", path],
                            cwd=repository, env=environment, capture_output=True, text=True)
    assert result.returncode == (0 if ignored else 1), result.stderr


def test_project_has_no_tracked_files_excluded_by_ignore_rules(isolated_git):
    command, environment, _repository = isolated_git
    if not (ROOT / ".git").exists():
        pytest.skip("source archive without a git checkout")
    result = subprocess.run([*command, "ls-files", "-ci", "--exclude-standard"],
                            cwd=ROOT, env=environment, capture_output=True, text=True, check=True)
    assert result.stdout == ""
