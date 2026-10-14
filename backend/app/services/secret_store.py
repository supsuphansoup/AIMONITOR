"""Protect API keys at rest.

On Windows the key is sealed with DPAPI (CryptProtectData, current-user scope):
the file is useless to another user account or another machine.
On other platforms persistent storage is disabled; environment keys remain usable.
"""

import base64
import ctypes
import os
import sys
import time
from contextlib import contextmanager
from typing import Optional, Tuple

SCHEME_DPAPI = "dpapi"
SCHEME_UNAVAILABLE = "unavailable"

_ENTROPY = b"aimonitor-api-keys-v1"


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _take(blob: _Blob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob.pbData)

    def _protect(data: bytes) -> bytes:
        out = _Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(_blob(data)),
            None,
            ctypes.byref(_blob(_ENTROPY)),
            None,
            None,
            0x01,
            ctypes.byref(out),
        )  # 0x01 = CRYPTPROTECT_UI_FORBIDDEN
        if not ok:
            raise OSError("CryptProtectData failed")
        return _take(out)

    def _unprotect(data: bytes) -> bytes:
        out = _Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(_blob(data)),
            None,
            ctypes.byref(_blob(_ENTROPY)),
            None,
            None,
            0x01,
            ctypes.byref(out),
        )
        if not ok:
            raise OSError("CryptUnprotectData failed")
        return _take(out)

    AVAILABLE_SCHEME = SCHEME_DPAPI
else:  # pragma: no cover - exercised on non-Windows hosts only
    _protect = _unprotect = None
    AVAILABLE_SCHEME = SCHEME_UNAVAILABLE


def seal(secret: str) -> Tuple[str, str]:
    """Return (scheme, stored_value)."""
    if AVAILABLE_SCHEME == SCHEME_DPAPI:
        return SCHEME_DPAPI, base64.b64encode(_protect(secret.encode("utf-8"))).decode("ascii")
    raise ValueError("Encrypted key storage is unavailable on this platform; use environment variables")


def unseal(scheme: str, stored: str) -> Optional[str]:
    """Return the secret, or None if it cannot be opened (other user/machine)."""
    try:
        if scheme == SCHEME_DPAPI:
            if AVAILABLE_SCHEME != SCHEME_DPAPI:
                return None
            return _unprotect(base64.b64decode(stored)).decode("utf-8")
        return None
    except (OSError, ValueError):
        return None


def restrict_file(path: "os.PathLike[str]") -> None:
    """Owner-only permissions where the OS honours POSIX modes."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
class StorageError(ValueError):
    """A key store failure whose message never contains credentials or paths."""


@contextmanager
def lock_file(path, timeout: float = 3.0):
    """Bounded advisory lock shared by independent desktop/backend processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        restrict_file(path)
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StorageError("Key storage is busy") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
