"""Windows DPAPI helpers for device credentials stored by AndroidTools."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class CredentialProtectionError(RuntimeError):
    """Raised when a credential cannot be protected or recovered."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _crypt(value: bytes, entropy: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise CredentialProtectionError("La protección DPAPI solo está disponible en Windows.")

    source, source_buffer = _blob(value)
    extra, extra_buffer = _blob(entropy)
    result = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ] if protect else [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    function.restype = wintypes.BOOL
    ok = function(ctypes.byref(source), "AndroidTools device credential" if protect else None,
                  ctypes.byref(extra), None, None, 0, ctypes.byref(result))
    _ = source_buffer, extra_buffer
    if not ok:
        raise CredentialProtectionError(f"Windows no pudo proteger/leer la credencial (error {ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def protect_credential(credential: str, identity: str) -> str:
    """Protects the secret for the current Windows account and device identity."""
    try:
        encrypted = _crypt(credential.encode("utf-8"), identity.encode("utf-8"), protect=True)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    except Exception as exc:
        if isinstance(exc, CredentialProtectionError):
            raise
        raise CredentialProtectionError(str(exc)) from exc


def unprotect_credential(value: str, identity: str) -> str:
    if not value.startswith("dpapi:"):
        raise CredentialProtectionError("La credencial guardada tiene un formato no compatible.")
    try:
        encrypted = base64.b64decode(value[6:], validate=True)
        return _crypt(encrypted, identity.encode("utf-8"), protect=False).decode("utf-8")
    except Exception as exc:
        if isinstance(exc, CredentialProtectionError):
            raise
        raise CredentialProtectionError(str(exc)) from exc
