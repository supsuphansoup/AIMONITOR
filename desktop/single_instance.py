"""Keep one AIMONITOR desktop app per PC.

The first instance listens on a fixed localhost port. A second launch (e.g. the
desktop shortcut clicked again) finds the port taken, asks the running instance
to bring its window forward, and exits instead of opening a second window and
widget.
"""

import logging
import socket
import threading
from typing import Callable, Optional

logger = logging.getLogger("aimonitor.desktop.single_instance")

HOST = "127.0.0.1"
PORT = 47821
SHOW_MESSAGE = b"AIMONITOR:show\n"


class SingleInstance:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.on_show: Optional[Callable[[], None]] = None
        self._sock: Optional[socket.socket] = None

    def acquire(self) -> bool:
        """True if this process is the only instance (and now listens for show requests)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Without this, Windows lets another socket bind the same port with SO_REUSEADDR.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(4)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        threading.Thread(target=self._serve, daemon=True, name="SingleInstance").start()
        return True

    def notify_running(self) -> bool:
        """Ask the running instance to show its window. False if it did not answer."""
        try:
            with socket.create_connection((self.host, self.port), timeout=2.0) as conn:
                conn.sendall(SHOW_MESSAGE)
            return True
        except OSError as e:
            logger.warning("Running AIMONITOR did not answer: %s", type(e).__name__)
            return False

    def _serve(self) -> None:
        sock = self._sock
        while sock is not None:
            try:
                conn, _ = sock.accept()
            except OSError:
                return  # socket closed on shutdown
            try:
                conn.settimeout(2.0)
                data = conn.recv(64)
            except OSError:
                data = b""
            finally:
                conn.close()
            if data == SHOW_MESSAGE and self.on_show is not None:
                try:
                    self.on_show()
                except Exception as e:
                    logger.debug("show request failed: %s", type(e).__name__)

    def release(self) -> None:
        if self._sock is not None:
            try:
                # Linux keeps the port bound while _serve() blocks in accept(); shutdown() wakes it first.
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
