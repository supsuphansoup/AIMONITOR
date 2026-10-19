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
PING_MESSAGE = b"AIMONITOR:ping\n"
ACK_MESSAGE = b"AIMONITOR:ack:v1\n"


class SingleInstance:
    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.on_show: Optional[Callable[[], None]] = None
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

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
        self._thread = threading.Thread(target=self._serve, daemon=True, name="SingleInstance")
        self._thread.start()
        return True

    def notify_running(self, show: bool = True) -> bool:
        """Ask the running instance to show its window. False if it did not answer."""
        try:
            with socket.create_connection((self.host, self.port), timeout=2.0) as conn:
                conn.sendall(SHOW_MESSAGE if show else PING_MESSAGE)
                reply = b""
                while len(reply) < len(ACK_MESSAGE):
                    chunk = conn.recv(len(ACK_MESSAGE) - len(reply))
                    if not chunk:
                        break
                    reply += chunk
            return reply == ACK_MESSAGE
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
                data = b""
                while len(data) < 64 and not data.endswith(b"\n"):
                    chunk = conn.recv(64 - len(data))
                    if not chunk:
                        break
                    data += chunk
                if data in (SHOW_MESSAGE, PING_MESSAGE):
                    conn.sendall(ACK_MESSAGE)
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
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            # Linux frees the port only once accept() has returned in _serve().
            thread.join(timeout=2.0)
