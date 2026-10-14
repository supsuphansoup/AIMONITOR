"""Packaged Windows build: one exe serves the API, runs the app and registers itself for autostart."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from desktop import autostart

ROOT = Path(__file__).resolve().parents[2]


def load_entry(data_dir: Path):
    spec = importlib.util.spec_from_file_location("aimonitor_entry", ROOT / "build-windows" / "entry.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DATA_DIR = data_dir  # never the real ~/.aimonitor
    return module


@pytest.fixture
def entry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # restores the cwd that main() changes
    return load_entry(tmp_path / "home" / ".aimonitor")


@pytest.fixture
def desktop_main():
    pytest.importorskip("customtkinter")
    pytest.importorskip("matplotlib")
    pytest.importorskip("pystray")
    from desktop import main

    return main


def test_packaged_autostart_registers_the_exe_itself(monkeypatch):
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", r"C:\Program Files\AIMONITOR\AIMONITOR.exe")
    assert autostart.command() == r'"C:\Program Files\AIMONITOR\AIMONITOR.exe" --autostart'


def test_backend_runs_uvicorn_from_source_and_the_exe_when_packaged(desktop_main, monkeypatch):
    monkeypatch.setattr(desktop_main.sys, "executable", "python.exe")
    assert desktop_main.backend_command()[:4] == ["python.exe", "-m", "uvicorn", "backend.app.main:app"]
    monkeypatch.setattr(desktop_main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_main.sys, "executable", "AIMONITOR.exe")
    assert desktop_main.backend_command() == ["AIMONITOR.exe", "--serve-backend", "--port", "8000",
                                             "--host", "127.0.0.1"]


@pytest.mark.parametrize("argv, port", [(["--serve-backend"], 8000), (["--serve-backend", "--port", "18765"], 18765)])
def test_serve_backend_binds_localhost_from_the_data_folder(entry, monkeypatch, argv, port):
    run = Mock()
    monkeypatch.setattr("uvicorn.run", run)
    assert entry.main(argv) == 0
    from backend.app.main import app

    run.assert_called_once_with(app, host="127.0.0.1", port=port, access_log=False)
    # .env and the default relative SQLite path resolve inside the data folder, not the install folder.
    assert Path.cwd() == entry.DATA_DIR


def test_windowed_exe_without_console_logs_to_a_file(entry, monkeypatch):
    monkeypatch.setattr("uvicorn.run", Mock())
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    entry.main(["--serve-backend"])
    try:
        assert sys.stdout is sys.stderr and sys.stdout.name == str(entry.DATA_DIR / "logs" / "backend.log")
    finally:
        sys.stdout.close()


def test_windowed_log_rotates_and_bounds_large_writes(entry, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(entry, "LOG_MAX_BYTES", 1024, raising=False)
    entry._log_to_file("bounded")
    try:
        for _ in range(8):
            sys.stdout.write("한" * 1024)
        sys.stdout.flush()
        logs = list((entry.DATA_DIR / "logs").glob("bounded.log*"))
        assert len(logs) <= 4
        assert all(path.stat().st_size <= 1024 for path in logs)
    finally:
        sys.stdout.close()


def test_stdio_survives_logging_reconfiguration(entry, monkeypatch):
    import logging

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    entry._log_to_file("reconfigured")
    try:
        # dictConfig (used by uvicorn) closes every registered logging Handler.
        for reference in list(logging._handlerList):
            handler = reference()
            if getattr(handler, "baseFilename", None) == sys.stdout.name:
                handler.close()
        sys.stdout.write("after logging configuration\n")
        sys.stdout.flush()
        assert Path(sys.stdout.name).read_text(encoding="utf-8") == "after logging configuration\n"
    finally:
        sys.stdout.close()


def test_existing_oversized_logs_are_bounded_on_startup(entry, monkeypatch):
    monkeypatch.setattr(entry, "LOG_MAX_BYTES", 1024)
    directory = entry.DATA_DIR / "logs"
    directory.mkdir(parents=True)
    for suffix in ("", ".1", ".2", ".3"):
        (directory / f"old.log{suffix}").write_text("한" * 1024, encoding="utf-8")
    stream = entry.BoundedLogStream(directory / "old.log")
    try:
        assert all(path.stat().st_size <= 1024 for path in directory.glob("old.log*"))
        stream.write("new log\n")
        assert all(path.stat().st_size <= 1024 for path in directory.glob("old.log*"))
    finally:
        stream.close()


def test_self_test_fails_with_exit_code_instead_of_raising(entry, monkeypatch):
    monkeypatch.setitem(sys.modules, "desktop.main", None)  # makes `import desktop.main` fail
    assert entry.main(["--self-test"]) == 1


def test_self_test_imports_the_gui_stack(entry, desktop_main):
    assert entry.main(["--self-test"]) == 0


@pytest.mark.parametrize("automatic", [False, True])
def test_default_mode_runs_the_desktop_app(entry, monkeypatch, automatic):
    attach, run = Mock(), Mock()
    monkeypatch.setitem(sys.modules, "desktop.config", SimpleNamespace(attach_to_default_desktop=attach))
    monkeypatch.setitem(sys.modules, "desktop.main", SimpleNamespace(main=run))
    assert entry.main(["--autostart"] if automatic else []) == 0
    attach.assert_called_once_with()
    run.assert_called_once_with(autostart=automatic)
    assert Path.cwd() == entry.DATA_DIR
def test_source_backend_suppresses_access_log(monkeypatch):
    from desktop import main

    monkeypatch.setattr(main.sys, "frozen", False, raising=False)
    command = main.backend_command(port=18771)
    assert "--no-access-log" in command


def test_desktop_http_client_info_logs_do_not_record_request_urls(caplog):
    import logging

    from desktop import main  # noqa: F401

    caplog.set_level(logging.INFO)
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        assert logger.getEffectiveLevel() >= logging.WARNING
        logger.info("GET https://review.invalid/?token=synthetic-test-secret")
    assert "synthetic-test-secret" not in caplog.text
