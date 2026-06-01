"""Optional Windows DPAPI at-rest wrapper for credential blobs.

Gap-audit 2026-06-01 (WIN-06, defense-in-depth): the high-value secret stores
(``.env`` API keys, ``auth.json`` / OAuth refresh tokens, the wallet binding)
sit plaintext on disk, protected only by the file ACL (see ``win_acl``). On a
broad-ACL home an ACL leak therefore equals a credential leak. DPAPI user-scope
(``CryptProtectData``) ties the ciphertext to the current Windows user so a copy
of the blob is useless off-box / out-of-account — a second layer beneath the
ACL.

This is strictly OPT-IN and non-breaking: callers should only encrypt when
``dpapi_enabled()`` is true (env ``STOA_DPAPI=1``). With the flag unset, or on
non-Windows, or when ``pywin32`` is missing, ``protect`` / ``unprotect`` are an
identity no-op that returns the input bytes unchanged — so default on-disk
format and behaviour are exactly as before.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Sentinel prefix stamped on DPAPI ciphertext so ``unprotect`` can tell a
# wrapped blob from a plaintext (legacy / no-op) blob and round-trip both.
_MAGIC = b"STOADPAPIv1\x00"


def dpapi_enabled() -> bool:
    """True only when the operator has opted in via ``STOA_DPAPI=1`` on Windows.

    Default (env unset) is False so behaviour is unchanged and non-breaking.
    """
    if os.name != "nt":
        return False
    return os.environ.get("STOA_DPAPI", "").strip() in ("1", "true", "True", "yes", "on")


def _win32crypt():
    try:
        import win32crypt  # type: ignore

        return win32crypt
    except Exception:  # noqa: BLE001 — pywin32 missing/broken degrades to no-op
        return None


def protect(data: bytes) -> bytes:
    """Wrap *data* with DPAPI user-scope when enabled+available, else identity.

    Returns the (possibly wrapped) bytes. Best-effort: any failure degrades to
    returning *data* unchanged so a write can never be lost to encryption.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("win_dpapi.protect expects bytes")
    data = bytes(data)
    if not dpapi_enabled():
        return data
    crypt = _win32crypt()
    if crypt is None:
        logger.warning("win_dpapi: STOA_DPAPI=1 set but pywin32 unavailable; storing plaintext")
        return data
    try:
        # CryptProtectData(data, desc, optional_entropy, reserved, prompt, flags)
        blob = crypt.CryptProtectData(data, "stoa", None, None, None, 0)
        return _MAGIC + blob
    except Exception as exc:  # noqa: BLE001 — never lose a write over encryption
        logger.warning("win_dpapi: CryptProtectData failed (%s); storing plaintext", exc)
        return data


def unprotect(token: bytes) -> bytes:
    """Unwrap a blob produced by :func:`protect`; identity for plaintext blobs.

    Detects the DPAPI sentinel so plaintext (legacy / no-op) blobs round-trip
    unchanged. Best-effort: a decrypt failure returns *token* unchanged.
    """
    if not isinstance(token, (bytes, bytearray)):
        raise TypeError("win_dpapi.unprotect expects bytes")
    token = bytes(token)
    if not token.startswith(_MAGIC):
        # Not DPAPI-wrapped (plaintext / legacy) — return as-is.
        return token
    crypt = _win32crypt()
    if crypt is None:
        logger.warning("win_dpapi: DPAPI blob found but pywin32 unavailable; cannot decrypt")
        return token
    try:
        # CryptUnprotectData returns (description, data).
        _desc, data = crypt.CryptUnprotectData(token[len(_MAGIC):], None, None, None, 0)
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("win_dpapi: CryptUnprotectData failed (%s)", exc)
        return token
