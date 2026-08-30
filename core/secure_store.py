"""Small helper for storing a secret string on disk, lightly protected.

Used for the Spotify refresh token (core/spotify_client.py). Kept as a
file under %APPDATA%\\Media Downloader\\options\\ - NOT the OS keyring - on
purpose: that way it survives an app update (the installer never touches
%APPDATA%) and is removed when the user uninstalls (the uninstaller wipes
that whole folder).

On Windows the value is encrypted with DPAPI (CryptProtectData) bound to
the current user account, via ctypes - no extra dependency. Elsewhere, or
if DPAPI is unavailable, it falls back to base64 (obfuscation only). Every
function is best-effort and never raises.
"""
import base64
import ctypes
import ctypes.wintypes
import os
import sys

_IS_WINDOWS = os.name == "nt"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DATA_BLOB) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _dpapi(func_name, data: bytes):
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = getattr(crypt32, func_name)
    out = _DATA_BLOB()
    inp = _blob(data)
    # entropy=None, reserved=None, prompt=None, flags=CRYPTPROTECT_UI_FORBIDDEN(0x1)
    if not fn(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError(f"{func_name} failed")
    try:
        return _blob_bytes(out)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(secret: str) -> bytes:
    """Encrypt `secret` for storage. Returns bytes to write to disk."""
    raw = (secret or "").encode("utf-8")
    if _IS_WINDOWS:
        try:
            return b"DPAPI:" + _dpapi("CryptProtectData", raw)
        except Exception:
            pass
    return b"B64:" + base64.b64encode(raw)


def unprotect(stored: bytes) -> str:
    """Reverse protect(). Returns "" on any failure."""
    try:
        if stored.startswith(b"DPAPI:") and _IS_WINDOWS:
            return _dpapi("CryptUnprotectData", stored[6:]).decode("utf-8", "ignore")
        if stored.startswith(b"B64:"):
            return base64.b64decode(stored[4:]).decode("utf-8", "ignore")
    except Exception:
        pass
    return ""


class FileSecretStore:
    """get()/set()/clear() a single secret at `path`."""

    def __init__(self, path):
        self.path = path

    def get(self):
        try:
            with open(self.path, "rb") as f:
                val = unprotect(f.read())
            return val or None
        except Exception:
            return None

    def set(self, value):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(protect(value))
            os.replace(tmp, self.path)
        except Exception:
            pass

    def clear(self):
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception:
            pass
