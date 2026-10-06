"""Protect API keys at rest.

On Windows the key is sealed with DPAPI (CryptProtectData, current-user scope):
the file is useless to another user account or another machine.
On other platforms persistent storage is disabled; environment keys remain usable.
"""

import base64
import ctypes
import os
import sys
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
