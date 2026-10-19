"""Keep release dependencies and workflow privileges reproducible."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def test_constraints_pin_runtime_desktop_and_build_dependencies():
    constraints = [Requirement(line) for raw in (ROOT / "constraints.txt").read_text().splitlines()
                   if (line := raw.split("#", 1)[0].strip())]
    pins = {canonicalize_name(requirement.name): requirement for requirement in constraints
            if not requirement.marker or requirement.marker.evaluate()}
    all_names = {canonicalize_name(requirement.name) for requirement in constraints}
    assert len({str(requirement) for requirement in constraints}) == len(constraints)
    assert len(pins) == sum(not requirement.marker or requirement.marker.evaluate() for requirement in constraints)
    for requirement in constraints:
        assert len(requirement.specifier) == 1
        assert next(iter(requirement.specifier)).operator == "=="
    for path in ("backend/requirements.txt", "desktop/requirements.txt", "build-windows/requirements.txt"):
        for raw in (ROOT / path).read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                requirement = Requirement(line)
                pin = next(iter(pins[canonicalize_name(requirement.name)].specifier)).version
                assert pin in requirement.specifier
    assert {"starlette", "pydantic-core", "sqlalchemy", "httpcore", "pyinstaller-hooks-contrib",
            "uvloop", "tomli", "exceptiongroup", "backports-asyncio-runner", "greenlet",
            "opentelemetry-api"} <= all_names
    assert not {"pip", "setuptools", "vulture"} & all_names


@pytest.mark.parametrize("path", ["Dockerfile", ".github/workflows/ci.yml", ".github/workflows/release.yml"])
def test_runtime_installers_use_constraints(path):
    contents = (ROOT / path).read_text(encoding="utf-8")
    installs = [line for line in contents.splitlines() if re.search(r"\bpip install\b.*-r ", line)]
    assert installs
    assert all("-c constraints.txt" in line for line in installs)
    if path == "Dockerfile":
        assert "COPY constraints.txt" in contents


def _workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def test_ci_permissions_are_read_only_and_checkout_drops_credentials():
    workflow = _workflow("ci.yml")
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_release_build_and_tag_publish_have_separate_permissions():
    workflow = _workflow("release.yml")
    assert workflow["permissions"] == {"contents": "read"}
    build = workflow["jobs"]["windows-exe"]
    publish = workflow["jobs"]["publish"]
    assert build["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {"contents": "write"}
    assert publish["needs"] == "windows-exe"
    assert "github.ref_type == 'tag'" in publish["if"]
    assert "github.event_name == 'push'" in publish["if"]
    assert "GH_TOKEN" not in str(build)
    for step in build["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    publication = next(step["run"] for step in publish["steps"] if "gh release create" in step.get("run", ""))
    assert publication.index("sha256sum -c") < publication.index("gh release create")
    assert "--draft" in publication and "--prerelease" in publication


def test_pages_deploy_is_the_only_job_with_pages_write_and_ships_existing_images():
    workflow = _workflow("pages.yml")
    assert workflow["permissions"] == {"contents": "read"}
    deploy = workflow["jobs"]["deploy"]
    assert deploy["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    for step in deploy["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    assemble = next(step["run"] for step in deploy["steps"] if step.get("name") == "Assemble site")
    page = (ROOT / "site/index.html").read_text(encoding="utf-8")
    for image in re.findall(r'src="images/([\w.-]+)"', page):
        assert f"docs/images/{image}" in assemble
        assert (ROOT / "docs/images" / image).is_file()


@pytest.mark.parametrize("name", ["ci.yml", "release.yml", "pages.yml"])
def test_actions_are_pinned_to_full_commit_shas(name):
    contents = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    assert "pull_request_target" not in contents
    uses = [line for line in contents.splitlines() if "uses:" in line]
    assert uses and all(re.search(r"uses: actions/[\w-]+@[0-9a-f]{40}\s+# v\d+", line) for line in uses)


def _version_guard():
    steps = _workflow("release.yml")["jobs"]["windows-exe"]["steps"]
    index = next(index for index, step in enumerate(steps) if step.get("name") == "Validate release version")
    assert index < next(index for index, step in enumerate(steps) if step.get("name") == "Install dependencies")
    return steps[index]["run"].split("@'\n", 1)[1].split("\n'@ | python -", 1)[0]


@pytest.mark.parametrize("tag,success", [
    ("v0.6.0", True), ("v0.6.0-alpha.1", True), ("v0.6.0-beta.2", True), ("v0.6.0-rc.1", True),
    ("v0.6.1", False), ("0.6.0", False), ("v0.6.0-rc.0", False), ("v0.6.0-rc", False),
    ("v00.6.0", False), ("v0.6.0+build", False), ("v0.6.0-PRIVATE_$(echo bad)", False),
])
def test_release_version_guard_checks_tag_and_prereleases(tmp_path, tag, success):
    (tmp_path / "backend/app/core").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
    (tmp_path / "backend/app/core/config.py").write_text('class Settings:\n    VERSION: str = "0.6.0"\n')
    result = subprocess.run([sys.executable, "-c", _version_guard()], cwd=tmp_path, capture_output=True,
                            text=True, env={**os.environ, "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": tag})
    assert (result.returncode == 0) == success, result.stderr


def test_release_version_guard_checks_project_and_settings(tmp_path):
    (tmp_path / "backend/app/core").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
    (tmp_path / "backend/app/core/config.py").write_text('class Settings:\n    VERSION: str = "0.5.0"\n')
    result = subprocess.run([sys.executable, "-c", _version_guard()], cwd=tmp_path, capture_output=True,
                            text=True, env={**os.environ, "GITHUB_REF_TYPE": "branch"})
    assert result.returncode != 0


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell release packaging runs on Windows")
def test_release_checksum_is_portable_between_windows_and_linux(tmp_path):
    import hashlib

    distribution = tmp_path / "dist/AIMONITOR"
    distribution.mkdir(parents=True)
    (distribution / "synthetic.txt").write_text("synthetic release artifact")
    script = next(step["run"] for step in _workflow("release.yml")["jobs"]["windows-exe"]["steps"]
                  if step.get("name") == "Zip and checksum")
    result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.6.0",
                                 "GITHUB_ENV": str(tmp_path / "github-env")},
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    archive = tmp_path / "AIMONITOR-v0.6.0-windows-x64.zip"
    checksum = archive.with_suffix(".zip.sha256").read_bytes()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum == f"{digest}  {archive.name}\n".encode("ascii")
