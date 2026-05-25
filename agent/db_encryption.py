"""
Opt-in SQLCipher at-rest encryption for STOA SQLite databases.

Audit context (v12 HIGH-70, 3 findings):

* ``state.db`` (stoa_state.SessionDB) — full session history, model
  configuration, billing metadata.
* ``kanban.db`` (stoa_cli.kanban_db) — task graph and dispatcher events.
* ``memory_store.db`` (plugins.memory.holographic.store.MemoryStore) —
  long-term fact store with trust scores.

All three were opened as plaintext SQLite.  Anyone with filesystem read
access to ``$STOA_HOME`` could ``sqlite3 state.db '.dump'`` and walk away
with the user's complete agent history.  ``PRAGMA secure_delete`` was
also not enabled, so deleted rows lingered as readable plaintext in
free pages until the next ``VACUUM``.

This module adds an *opt-in* encryption layer with three guarantees:

1.  **Default OFF.**  ``STOA_DB_ENCRYPTION`` unset → callers get a stock
    ``sqlite3.connect`` exactly as before.  No behavior change, no
    Windows build pain, no new mandatory dependency.
2.  **Fail-loud when enabled.**  ``STOA_DB_ENCRYPTION=1`` without
    ``pysqlcipher3`` installed or without a resolvable passphrase
    raises ``DbEncryptionError`` immediately.  Silent fall-through to
    plaintext would be worse than crashing — the user explicitly asked
    for encryption.
3.  **No passphrase in code, never logged.**  Passphrase resolution
    order:

    a.  ``STOA_DB_PASSPHRASE`` env var (CI/headless).
    b.  OS keychain — macOS Keychain via ``keyring``, Windows DPAPI via
        ``keyring``'s Win32 backend, Linux Secret Service via the
        same.  All gated behind a single optional ``keyring`` import.
    c.  Hard fail with an actionable message pointing at the env var
        and the keychain setup docs.

The module also enforces ``PRAGMA secure_delete=ON`` on every
connection regardless of encryption mode.  That PRAGMA is independent
of SQLCipher — it zero-fills freed pages on delete, closing the
"VACUUM not run → plaintext leak" gap on the unencrypted default
path too.

Migration: callers create a fresh encrypted DB on first encrypted
open.  Existing plaintext DBs are migrated via the
``stoa db encrypt`` CLI command (registered in ``stoa_cli/main.py``)
which backs up the plaintext file, ``ATTACH``es an encrypted target,
copies all tables via SQLCipher's ``sqlcipher_export``, verifies the
row counts match, then atomically swaps the file into place.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Sentinel keychain identifiers — kept short so DPAPI / Keychain UI shows
# a stable label.  Service name doubles as the SECRET_SERVICE schema's
# ``service`` field on Linux.
_KEYRING_SERVICE = "stoa-agent-db"
_KEYRING_USERNAME = "sqlcipher-master-key"


class DbEncryptionError(RuntimeError):
    """Raised when SQLCipher mode was requested but couldn't be honored.

    Distinct exception type so callers (SessionDB.__init__,
    kanban_db.connect, MemoryStore.__init__) can decide between
    crash-loud (CLI invocations that expect encryption to work) and
    fall-through-to-plaintext (test harnesses that set
    ``STOA_DB_ENCRYPTION=0`` explicitly).  In practice, every caller
    today re-raises — silently downgrading to plaintext after the user
    asked for encryption is a security regression.
    """


def encryption_enabled() -> bool:
    """Return True iff ``STOA_DB_ENCRYPTION`` is set to a truthy value.

    Accepts ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive) as
    truthy.  Anything else — including unset — returns False so the
    default codepath is the existing plaintext sqlite3.
    """
    raw = os.environ.get("STOA_DB_ENCRYPTION", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _try_import_sqlcipher():
    """Import ``pysqlcipher3.dbapi2`` if available; return None otherwise.

    Kept as a function (not a module-level import) so the optional
    dependency doesn't cost anything on the default plaintext path.
    pysqlcipher3 needs a C build with OpenSSL headers — Windows users
    can't always provide that, so ImportError is the expected case for
    a large slice of the userbase.
    """
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore
        return sqlcipher
    except ImportError:
        return None


def _resolve_passphrase() -> Optional[str]:
    """Look up the SQLCipher master key from env or OS keychain.

    Returns ``None`` if neither source has a key.  Caller is responsible
    for turning ``None`` into a ``DbEncryptionError`` — we don't raise
    here so unit tests can probe the resolution path.

    Order:
      1.  ``STOA_DB_PASSPHRASE`` env var.  Wins over keychain so CI
          and Docker can inject a key without provisioning a keyring.
      2.  ``keyring.get_password(service, username)`` — macOS Keychain
          on Darwin, DPAPI on Windows, Secret Service on Linux.  Wrapped
          in try/except so a missing ``keyring`` package or a locked
          keyring degrades to "no passphrase available" instead of
          crashing the import.
    """
    env_pass = os.environ.get("STOA_DB_PASSPHRASE")
    if env_pass:
        return env_pass

    try:
        import keyring  # type: ignore
    except ImportError:
        return None

    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception as exc:  # pragma: no cover — backend-specific
        # Locked keyring, no DBus on headless Linux, broken backend —
        # all degrade to "no key found" so the caller can surface a
        # single actionable error message.  Logged at debug so we
        # don't leak keychain backend errors into INFO/WARN logs.
        logger.debug("keyring lookup failed: %s: %s", type(exc).__name__, exc)
        return None


def store_passphrase_in_keychain(passphrase: str) -> bool:
    """Save the SQLCipher master key in the OS keychain.

    Returns True on success, False if ``keyring`` is unavailable or the
    backend refused.  Helper for the ``stoa db encrypt`` migration so
    users don't have to wire up keyring access manually.
    """
    try:
        import keyring  # type: ignore
    except ImportError:
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, passphrase)
        return True
    except Exception as exc:  # pragma: no cover
        logger.debug("keyring set_password failed: %s: %s", type(exc).__name__, exc)
        return False


def _apply_secure_delete(conn) -> None:
    """Set ``PRAGMA secure_delete=ON``.

    Independent of encryption — closes the "deleted row visible in free
    pages until next VACUUM" gap on both plaintext and SQLCipher
    databases.  Cheap (a few extra zeroed page writes per delete) and
    audit (v12 HIGH-70 finding #4) requires it on all three DBs.
    """
    try:
        conn.execute("PRAGMA secure_delete=ON")
    except sqlite3.Error as exc:
        # secure_delete is supported on every SQLite >=3.6.23 we care
        # about; failure here means a very old amalgamation or a
        # filesystem error — log and continue rather than crash a
        # session open over a hardening pragma.
        logger.warning("PRAGMA secure_delete=ON failed: %s", exc)


def _apply_key_pragmas(conn, passphrase: str) -> None:
    """Bind the SQLCipher master key to ``conn`` and verify it works.

    ``PRAGMA key`` must be the *first* statement executed on a
    SQLCipher connection — before any schema PRAGMA or query.  We
    follow it with ``SELECT count(*) FROM sqlite_master`` which forces
    SQLCipher to actually decrypt page 1.  A wrong passphrase raises
    ``DatabaseError("file is not a database")`` here, which we re-raise
    as a typed ``DbEncryptionError`` so callers can show a clean
    "wrong passphrase" message instead of a misleading "DB corrupt"
    error.

    The passphrase is quoted with the standard SQLCipher single-quote
    doubling rule.  We don't pass it via parameter binding because
    SQLite doesn't bind PRAGMA arguments — they must be literal.  The
    passphrase never appears in logs.
    """
    quoted = "'" + passphrase.replace("'", "''") + "'"
    conn.execute(f"PRAGMA key = {quoted}")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError as exc:
        # SQLCipher reports both "file is not a database" (wrong key on
        # an encrypted DB) and "file is encrypted or is not a database"
        # (encrypted DB opened without a key).  Both mean the same thing
        # for our purposes: the key didn't unlock this file.
        raise DbEncryptionError(
            "SQLCipher could not decrypt the database — wrong passphrase "
            "or the file is not a SQLCipher database.  Re-check "
            "STOA_DB_PASSPHRASE / keychain entry.  Original error: "
            f"{exc}"
        ) from exc


def open_connection(
    db_path: Path,
    *,
    check_same_thread: bool = False,
    timeout: float = 10.0,
    isolation_level: Optional[str] = None,
) -> sqlite3.Connection:
    """Open a SQLite connection, transparently encrypted if env says so.

    Behavior matrix:

    *   ``STOA_DB_ENCRYPTION`` unset → stock ``sqlite3.connect``,
        ``PRAGMA secure_delete=ON``.  Identical to pre-audit behavior
        plus the secure_delete hardening.
    *   ``STOA_DB_ENCRYPTION=1`` + ``pysqlcipher3`` importable + a
        resolvable passphrase → SQLCipher connection, key bound,
        secure_delete on.
    *   ``STOA_DB_ENCRYPTION=1`` and any of those preconditions fail →
        ``DbEncryptionError`` with the missing piece named.  We refuse
        to silently fall back to plaintext after the user explicitly
        asked for encryption.

    The signature mirrors the keyword set used by all three call sites
    (SessionDB, kanban_db.connect, MemoryStore) so they can be patched
    with a near-drop-in substitution.
    """
    if not encryption_enabled():
        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=check_same_thread,
            timeout=timeout,
            isolation_level=isolation_level,
        )
        _apply_secure_delete(conn)
        return conn

    sqlcipher = _try_import_sqlcipher()
    if sqlcipher is None:
        raise DbEncryptionError(
            "STOA_DB_ENCRYPTION=1 but pysqlcipher3 is not installed.  "
            "Install it with `pip install pysqlcipher3` (requires "
            "OpenSSL headers; on Windows the prebuilt wheel may need "
            "VC++ build tools).  Alternatively unset STOA_DB_ENCRYPTION "
            "to use the default plaintext SQLite backend."
        )

    passphrase = _resolve_passphrase()
    if not passphrase:
        raise DbEncryptionError(
            "STOA_DB_ENCRYPTION=1 but no master key was found.  Set "
            "STOA_DB_PASSPHRASE in the environment, or store the key "
            "in the OS keychain under service "
            f"'{_KEYRING_SERVICE}' / username '{_KEYRING_USERNAME}' "
            "(use `stoa db encrypt --set-keychain` to do this "
            "interactively)."
        )

    # NOTE: pysqlcipher3's connect signature accepts the same kwargs as
    # stdlib sqlite3, so this call mirrors the plaintext branch above.
    conn = sqlcipher.connect(
        str(db_path),
        check_same_thread=check_same_thread,
        timeout=timeout,
        isolation_level=isolation_level,
    )
    _apply_key_pragmas(conn, passphrase)
    _apply_secure_delete(conn)
    return conn


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


def migrate_plaintext_to_encrypted(
    src_path: Path,
    *,
    passphrase: Optional[str] = None,
    backup_suffix: str = ".pre-encrypt.bak",
) -> Path:
    """Migrate a plaintext SQLite DB at *src_path* to SQLCipher in-place.

    Steps:

    1.  ``cp src_path src_path + backup_suffix`` so the user has a
        rollback even if a power loss interrupts step 4.
    2.  Open the plaintext source read-only.
    3.  ``ATTACH`` a new encrypted DB next to it (``src + ".enc.tmp"``),
        ``PRAGMA key`` the attachment, then call
        ``SELECT sqlcipher_export('encrypted')`` to copy every table.
    4.  Verify row counts match between source and target for every
        user table.
    5.  ``os.replace`` the encrypted tmp file over the original path.
        ``os.replace`` is atomic on POSIX and on Windows since 3.3.

    Returns the path of the backup file so callers can show it to the
    user.  Raises ``DbEncryptionError`` on any verification failure
    *before* swapping the file, so a corrupt migration never destroys
    the original DB.
    """
    if not src_path.exists():
        raise DbEncryptionError(f"Source database does not exist: {src_path}")

    sqlcipher = _try_import_sqlcipher()
    if sqlcipher is None:
        raise DbEncryptionError(
            "pysqlcipher3 is not installed — cannot perform encryption "
            "migration.  See README for install instructions."
        )

    if passphrase is None:
        passphrase = _resolve_passphrase()
    if not passphrase:
        raise DbEncryptionError(
            "No passphrase available for migration.  Set "
            "STOA_DB_PASSPHRASE or supply --passphrase."
        )

    import shutil

    backup_path = src_path.with_suffix(src_path.suffix + backup_suffix)
    tmp_path = src_path.with_suffix(src_path.suffix + ".enc.tmp")

    # Clean up stray tmp from previous interrupted run — safe because
    # the backup_path is what we'd roll back to, never the .enc.tmp.
    if tmp_path.exists():
        tmp_path.unlink()

    shutil.copy2(str(src_path), str(backup_path))

    # Open plaintext source via stdlib so we don't accidentally try to
    # key it.  Read-only mode (uri=True + mode=ro) keeps us honest —
    # even a buggy migration can't mutate the source.
    src_conn = sqlite3.connect(
        f"file:{src_path}?mode=ro",
        uri=True,
    )
    try:
        # Sanity probe: is the source actually a plaintext SQLite DB?
        # If a user re-runs migration on an already-encrypted DB, this
        # would normally raise "file is encrypted" — surface that as
        # a clear "already encrypted" message.
        try:
            src_conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            raise DbEncryptionError(
                f"{src_path} is not a readable plaintext SQLite database "
                f"(maybe already encrypted?): {exc}"
            ) from exc

        quoted = "'" + passphrase.replace("'", "''") + "'"
        src_conn.execute(f"ATTACH DATABASE '{tmp_path}' AS encrypted KEY {quoted}")
        src_conn.execute("SELECT sqlcipher_export('encrypted')")
        src_conn.execute("DETACH DATABASE encrypted")
    finally:
        src_conn.close()

    # Verify: open the new encrypted DB and confirm every user table
    # has the same row count as the source.  If even one mismatches,
    # we abort *before* swapping the file — the original is untouched
    # and the backup is still there.
    _verify_migration(src_path, tmp_path, passphrase)

    # Atomic swap.  os.replace is atomic on Windows since Python 3.3
    # (uses MoveFileEx with MOVEFILE_REPLACE_EXISTING).
    os.replace(str(tmp_path), str(src_path))
    return backup_path


def _verify_migration(
    src_path: Path,
    enc_path: Path,
    passphrase: str,
) -> None:
    """Row-count parity check between plaintext source and encrypted copy.

    Catches the rare case where ``sqlcipher_export`` silently skips a
    table (it has done this historically when the source had a
    virtual table with no shadow tables).  If any user table differs,
    raise ``DbEncryptionError`` so the caller knows not to swap the
    file in.
    """
    sqlcipher = _try_import_sqlcipher()
    if sqlcipher is None:  # pragma: no cover — guarded by caller
        return

    src_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    enc_conn = sqlcipher.connect(str(enc_path))
    quoted = "'" + passphrase.replace("'", "''") + "'"
    enc_conn.execute(f"PRAGMA key = {quoted}")

    try:
        rows = src_conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (name,) in rows:
            try:
                src_count = src_conn.execute(
                    f'SELECT count(*) FROM "{name}"'
                ).fetchone()[0]
                enc_count = enc_conn.execute(
                    f'SELECT count(*) FROM "{name}"'
                ).fetchone()[0]
            except sqlite3.Error:
                # FTS5 shadow tables ('foo_data', 'foo_idx', ...) may
                # not be directly countable — skip with a debug log
                # rather than abort the migration.
                logger.debug("skipping count check for table %s", name)
                continue
            if src_count != enc_count:
                raise DbEncryptionError(
                    f"Migration verification failed: table '{name}' "
                    f"has {src_count} rows in source but {enc_count} "
                    f"in encrypted copy.  Original DB is untouched; "
                    f"the .enc.tmp file at {enc_path} has been kept "
                    f"for inspection.  Re-run after investigating."
                )
    finally:
        src_conn.close()
        enc_conn.close()
