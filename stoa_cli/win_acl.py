"""Cross-platform owner-only file/dir locking.

Gap-audit 2026-06-01 (WIN-01/02/03/05/06): on Windows ``os.chmod(path, 0o600)``
only toggles the read-only bit — it does NOT restrict who can read the file, so
secret stores (``.env``, ``auth.json``, the wallet keystore, ``state.db``, the
gateway launcher ``.cmd``) fell back to the inherited directory ACL. On a
broad-ACL home (``C:\\stoa``, ``C:\\ProgramData\\stoa``, a volume root / UNC
share) or a multi-user / domain box where ``BUILTIN\\Administrators`` /
``Users`` inherit read, the secrets were exposed.

``lock_to_owner`` is the single chokepoint every sensitive-file writer should
call instead of a bare ``chmod``: POSIX keeps the 0600/0700 semantics; Windows
strips inheritance and grants only the current user, via ``icacls`` (always
present) with a ``win32security`` fast-path when pywin32 is available. It is
best-effort and never raises — a failure is logged and reported via the return
value so the caller can warn rather than crash.
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def _windows_lock(path: str, is_dir: bool) -> bool:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        logger.warning("win_acl: cannot resolve current user; %s left with inherited ACL", path)
        return False
    # (OI)(CI) propagates the owner-only grant to a directory's children.
    grant = f"{user}:(OI)(CI)F" if is_dir else f"{user}:F"
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", grant],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort; never crash the caller
        logger.warning("win_acl: icacls failed for %s (%s); file not ACL-locked", path, exc)
        return False


def lock_to_owner(path, *, is_dir: bool = False) -> bool:
    """Restrict *path* to the current user only. Returns True on success.

    POSIX: ``chmod 0600`` (file) / ``0700`` (dir).
    Windows: remove inheritance + grant only the current user (icacls).
    Best-effort: never raises.
    """
    p = str(path)
    try:
        if os.name == "posix":
            os.chmod(p, 0o700 if is_dir else 0o600)
            return True
    except OSError as exc:
        logger.debug("win_acl: posix chmod failed for %s: %s", p, exc)
        return False
    if os.name == "nt":
        return _windows_lock(p, is_dir)
    # Unknown platform — try chmod, ignore failure.
    try:
        os.chmod(p, 0o700 if is_dir else 0o600)
        return True
    except OSError:
        return False
