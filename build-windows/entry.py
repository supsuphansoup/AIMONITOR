"""Entry point of the packaged Windows build (PyInstaller, see aimonitor.spec).

One exe plays three roles:
- default: the desktop app (main window, widget, tray), like run_desktop.pyw
- ``--serve-backend [--port N]``: the local API server the desktop app starts (desktop.main.backend_command)
- ``--self-test``: import the whole GUI and API stack and exit 0, so a build can be checked headless
"""

import ipaddress
import os
import sys
import threading
from pathlib import Path

DATA_DIR = Path.home() / ".aimonitor"
LOG_MAX_BYTES = 1024 * 1024


class BoundedLogStream:
    """UTF-8 stdout/stderr with three backups and bounded individual writes."""

    encoding = "utf-8"

    def __init__(self, path: Path):
        self.name = str(path)
        self._path = path
        for index in range(4):
            previous = path if index == 0 else Path(f"{path}.{index}")
            if previous.is_file() and previous.stat().st_size > LOG_MAX_BYTES:
                with open(previous, "rb") as stream:
                    stream.seek(-LOG_MAX_BYTES, os.SEEK_END)
                    tail = stream.read().decode("utf-8", errors="ignore")
                with open(previous, "w", encoding="utf-8", newline="") as stream:
                    stream.write(tail)
        self._stream = open(path, "a", encoding="utf-8", newline="")
        self._lock = threading.Lock()

    def _rotate(self) -> None:
        self._stream.close()
        for index in range(3, 0, -1):
            previous = self._path if index == 1 else Path(f"{self._path}.{index - 1}")
            if previous.exists():
                os.replace(previous, Path(f"{self._path}.{index}"))
        self._stream = open(self._path, "w", encoding="utf-8", newline="")

    def write(self, text: str) -> int:
        data = text.encode("utf-8", errors="replace")
        # Keep even an exceptionally long single traceback/write below one file's limit.
        limit = min(16384, LOG_MAX_BYTES - 1)
        if len(data) > limit:
            data = data[:max(0, limit - 24)] + b"\n[log write truncated]\n"
        text_out = data.decode("utf-8", errors="ignore")
        with self._lock:
            if self._stream.tell() + len(data) > LOG_MAX_BYTES:
                self._rotate()
            self._stream.write(text_out)
            self._stream.flush()
        return len(text)

    def flush(self) -> None:
        with self._lock:
            if not self._stream.closed:
                self._stream.flush()

    def close(self) -> None:
        with self._lock:
            self._stream.close()

    def isatty(self) -> bool:
        return False


def _log_to_file(name: str) -> None:
    """A windowed exe has no console; sys.stdout/stderr are None and uvicorn's logging needs a stream."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = BoundedLogStream(log_dir / f"{name}.log")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def _port(argv: list) -> int:
    if "--port" in argv:
        return int(argv[argv.index("--port") + 1])
    return 8000


def main(argv: list) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # The install folder may be read-only; .env and the default ./aimonitor.db live in the data folder.
    os.chdir(DATA_DIR)

    if "--serve-backend" in argv:
        _log_to_file("backend")
        import uvicorn

        from backend.app.main import app

        host = argv[argv.index("--host") + 1] if "--host" in argv else "127.0.0.1"
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Backend host must be loopback")
        uvicorn.run(app, host=host, port=_port(argv), access_log=False)
        return 0

    if "--self-test" in argv:
        _log_to_file("self-test")
        try:
            import backend.app.main  # noqa: F401
            import desktop.main  # noqa: F401  (pulls in customtkinter, matplotlib, pystray)
        except Exception as exc:
            # Report through the exit code; an uncaught error would open a dialog and hang a headless check.
            print(f"self-test failed: error_type={type(exc).__name__}", file=sys.stderr)
            return 1
        return 0

    _log_to_file("desktop")
    from desktop.config import attach_to_default_desktop
    from desktop.main import main as run_desktop

    attach_to_default_desktop()
    run_desktop(autostart="--autostart" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
