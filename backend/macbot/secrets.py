"""Keep MCP credentials out of SQLite plaintext on Windows."""
import base64
import ctypes
import os
from ctypes import wintypes

PREFIX = "dpapi:"


def protect(value, decrypt=False):
    if os.name != "nt":
        raise ValueError("Credential storage requires Windows Data Protection.")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    raw = base64.b64decode(value[len(PREFIX):]) if decrypt else value.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    incoming, outgoing = Blob(len(raw), buffer), Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    ok = function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise ValueError("This connection's credentials belong to another Windows profile. Remove it and add it again.")
    try:
        result = ctypes.string_at(outgoing.data, outgoing.size)
        return result.decode("utf-8") if decrypt else PREFIX + base64.b64encode(result).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.data)


def seal_config(config):
    return {**config, **{field: {key: value if value.startswith(PREFIX) else protect(value)
                                for key, value in config.get(field, {}).items()}
                         for field in ("env", "headers")}}


def open_config(config):
    return {**config, **{field: {key: protect(value, decrypt=True) if value.startswith(PREFIX) else value
                                for key, value in config.get(field, {}).items()}
                         for field in ("env", "headers")}}
