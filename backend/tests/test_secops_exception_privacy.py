"""Background and packaged failures must not persist exception text (GD-01/GP-01)."""
import ast
import asyncio
import builtins
import importlib.util
import logging
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlmodel import create_engine

from backend.app.services.scheduler import BackgroundScheduler

ROOT = Path(__file__).resolve().parents[2]
SECRET = "SYNTHETIC_SECOPS_PRIVATE_EXCEPTION"
CAUSE = "SYNTHETIC_SECOPS_PRIVATE_CAUSE"


def assert_safe_records(caplog, logger_name, error_type):
    records = [record for record in caplog.records if record.name == logger_name]
    assert len(records) == 1
    assert f"error_type={error_type}" in records[0].getMessage()
    assert records[0].exc_info is None and records[0].exc_text is None
    assert SECRET not in caplog.text and CAUSE not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize("directory", ["backend", "desktop", "build-windows", "sdk/python"])
def test_production_sources_never_dump_exception_tracebacks(directory):
    violations = []
    for path in sorted((ROOT / directory).rglob("*.py")):
        if "tests" in path.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
            owner = getattr(getattr(function, "value", None), "id", "")
            unsafe = name == "exception" and owner in ("logger", "logging")
            unsafe |= owner == "traceback" and name in ("print_exc", "format_exc")
            unsafe |= name == "print_exception"
            unsafe |= any(keyword.arg == "exc_info" and isinstance(keyword.value, ast.Constant)
                          and keyword.value.value is True for keyword in node.keywords)
            if unsafe:
                violations.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert violations == []


@pytest.mark.asyncio
async def test_scheduler_logs_only_error_type_and_resumes_after_chained_failure(caplog):
    scheduler = BackgroundScheduler()
    calls = []

    def job():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError(SECRET) from ValueError(CAUSE)
        scheduler._stop_event.set()

    caplog.set_level(logging.ERROR, logger="backend.app.services.scheduler")
    try:
        await asyncio.wait_for(scheduler._loop("alert-evaluation", 0, job), timeout=2)
    finally:
        await scheduler.stop()
    assert len(calls) == 2
    assert scheduler.last_alert_error == "alert-evaluation: RuntimeError"
    assert scheduler._running_jobs == set()
    assert_safe_records(caplog, "backend.app.services.scheduler", "RuntimeError")


@pytest.mark.asyncio
async def test_scheduler_sqlite_lock_does_not_log_sql_or_traceback(tmp_path, caplog):
    path = tmp_path / "locked.sqlite"
    engine = create_engine(f"sqlite:///{path.as_posix()}", hide_parameters=True,
                           connect_args={"timeout": 0.01, "check_same_thread": False})
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE synthetic_probe (secret TEXT)"))
    lock = sqlite3.connect(path)
    lock.execute("BEGIN IMMEDIATE")
    scheduler = BackgroundScheduler()

    def job():
        try:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO synthetic_probe VALUES (:secret)"), {"secret": SECRET})
        finally:
            scheduler._stop_event.set()

    caplog.set_level(logging.ERROR, logger="backend.app.services.scheduler")
    try:
        await asyncio.wait_for(scheduler._loop("alert-evaluation", 0, job), timeout=2)
    finally:
        await scheduler.stop()
        lock.rollback()
        lock.close()
        engine.dispose()
    assert scheduler.last_alert_error == "alert-evaluation: OperationalError"
    assert_safe_records(caplog, "backend.app.services.scheduler", "OperationalError")
    assert "database is locked" not in caplog.text and "INSERT INTO" not in caplog.text


@pytest.mark.parametrize("windowed", [False, True])
@pytest.mark.parametrize("error_class", [RuntimeError, ModuleNotFoundError])
def test_packaged_self_test_failure_logs_only_type(tmp_path, monkeypatch, capsys, windowed, error_class):
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path))
    spec = importlib.util.spec_from_file_location("secops_privacy_entry", ROOT / "build-windows" / "entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    entry.DATA_DIR = tmp_path / "synthetic-app"
    monkeypatch.chdir(tmp_path)
    original_import = builtins.__import__

    def import_failure(name, *args, **kwargs):
        if name == "backend.app.main":
            raise error_class(SECRET) from ValueError(CAUSE)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_failure)
    with monkeypatch.context() as stdio:
        if windowed:
            stdio.setattr(sys, "stdout", None)
            stdio.setattr(sys, "stderr", None)
        try:
            assert entry.main(["--self-test"]) == 1
        finally:
            if windowed and sys.stderr is not None:
                sys.stderr.close()
    captured = capsys.readouterr()
    if windowed:
        output = (entry.DATA_DIR / "logs" / "self-test.log").read_text(encoding="utf-8")
        assert captured.out == captured.err == ""
    else:
        assert captured.out == ""
        output = captured.err
    assert output.strip() == f"self-test failed: error_type={error_class.__name__}"
    assert SECRET not in output and CAUSE not in output and "Traceback" not in output


@pytest.mark.parametrize("action", ["read", "write"])
def test_autostart_failures_log_only_type_without_opening_gui(monkeypatch, caplog, action):
    pytest.importorskip("customtkinter")
    pytest.importorskip("matplotlib")
    from desktop import app_gui

    def failure():
        raise PermissionError(SECRET) from RuntimeError(CAUSE)

    ui = SimpleNamespace(autostart_var=Mock(), autostart_switch=Mock(),
                         lbl_autostart_status=Mock(), lbl_status_msg=Mock())
    ui.autostart_var.get.return_value = True
    ui._refresh_autostart_status = lambda: app_gui.DesktopMainWindow._refresh_autostart_status(ui)
    monkeypatch.setattr(app_gui.autostart, "get_status", lambda: app_gui.autostart.DISABLED)
    caplog.set_level(logging.WARNING, logger=app_gui.logger.name)
    if action == "read":
        monkeypatch.setattr(app_gui.autostart, "get_status", failure)
        ui._refresh_autostart_status()
    else:
        monkeypatch.setattr(app_gui.autostart, "enable", failure)
        app_gui.DesktopMainWindow._toggle_autostart(ui)
    assert_safe_records(caplog, app_gui.logger.name, "PermissionError")
