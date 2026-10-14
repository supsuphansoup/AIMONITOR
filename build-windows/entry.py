"""Entry point of the packaged Windows build (PyInstaller, see aimonitor.spec).

One exe plays three roles:
- default: the desktop app (main window, widget, tray), like run_desktop.pyw
- ``--serve-backend [--port N]``: the local API server the desktop app starts (desktop.main.backend_command)
- ``--self-test``: import the whole GUI and API stack and exit 0, so a build can be checked headless
"""

import ipaddress
import os
import sys
import traceback
from pathlib import Path

DATA_DIR = Path.home() / ".aimonitor"


def _log_to_file(name: str) -> None:
    """A windowed exe has no console; sys.stdout/stderr are None and uvicorn's logging needs a stream."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / f"{name}.log", "w", encoding="utf-8", buffering=1)
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
        uvicorn.run(app, host=host, port=_port(argv))
        return 0

    if "--self-test" in argv:
        _log_to_file("self-test")
        try:
            import backend.app.main  # noqa: F401
            import desktop.main  # noqa: F401  (pulls in customtkinter, matplotlib, pystray)
        except Exception:
            # Report through the exit code; an uncaught error would open a dialog and hang a headless check.
            traceback.print_exc()
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
