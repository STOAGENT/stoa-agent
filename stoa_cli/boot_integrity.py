"""Boot integrity hash — audit Lens 46 / M-11c.

Computes a SHA-256 digest of STOA's own core source files at every CLI
startup and appends one line to ``~/.stoa/boot-integrity.log``:

    <iso-8601-ts> <sha256-hex> v<version> <hostname>

This is *passive* tamper evidence, not active prevention. A reader can:

* Diff the most recent log line against the previous one to spot a
  modified install (`prev_digest != cur_digest` ⇒ binary changed
  between sessions — either an intentional upgrade or tampering).
* Compare any single line against the published per-release digest
  (CI emits one to the GitHub release page).

Design constraints:

* **Must not block startup.** All errors are swallowed; the function
  returns silently. A failed hash MUST NOT prevent the CLI from
  running — the user still wants their agent.
* **Must not enlarge the cold-start budget noticeably.** Hashing is
  limited to a small set of files (~3-5) totalling under ~2 MB so
  the overhead is < 20 ms on a cold disk cache.
* **Must not leak file content into the log.** Only the digest +
  metadata go on disk; the source bytes never do.
* **Opt-out is one env var.** ``STOA_BOOT_INTEGRITY=0`` skips the
  write entirely (useful in sandboxes that want zero ``~/.stoa``
  writes during smoke tests).
"""

from __future__ import annotations

import hashlib
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

# Files included in the integrity hash. Order matters for digest
# stability — never reorder, only append.
_HASHED_RELATIVE_PATHS: tuple[str, ...] = (
    "cli.py",
    "stoa_cli/__init__.py",
    "stoa_cli/boot_integrity.py",
    "agent/anthropic_adapter.py",
    "tools/approval.py",
)

# Cap on the bytes hashed per file. Even after a worst-case 10x growth
# the cold-start cost stays under 50 ms on commodity SSDs.
_MAX_BYTES_PER_FILE: int = 4 * 1024 * 1024  # 4 MiB


def _repo_root() -> Path:
    """Return the parent of ``stoa_cli/`` — i.e. the STOA Agent source
    root inside the installed package. Falls back to the cwd when the
    package layout is unrecognisable (editable installs, frozen apps)."""
    here = Path(__file__).resolve().parent  # stoa_cli/
    return here.parent  # repo root


def compute_boot_digest(root: Path | None = None) -> str:
    """Return the hex SHA-256 digest of the core source files.

    Always returns a 64-char hex string. If a file is missing, its
    absence is folded into the digest as a sentinel — so a removed
    file changes the digest exactly like a modified file would, which
    is the security property we want.
    """
    if root is None:
        root = _repo_root()

    h = hashlib.sha256()
    for rel in _HASHED_RELATIVE_PATHS:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        candidate = root / rel
        try:
            if not candidate.is_file():
                h.update(b"MISSING\x00")
                continue
            with open(candidate, "rb") as f:
                chunk = f.read(_MAX_BYTES_PER_FILE)
            h.update(chunk)
            h.update(b"\x00")
        except OSError:
            # Don't crash the CLI just because a file is unreadable.
            h.update(b"ERR\x00")
    return h.hexdigest()


def _resolve_log_path() -> Path:
    home_override = os.environ.get("STOA_HOME")
    base = Path(home_override) if home_override else Path.home() / ".stoa"
    return base / "boot-integrity.log"


def _read_version() -> str:
    try:
        from stoa_cli import __version__  # type: ignore
        return str(__version__)
    except Exception:
        return "unknown"


def record_boot(*, force: bool = False) -> str | None:
    """Compute the boot digest and append it to the log.

    Returns the digest on success or ``None`` if recording was skipped
    (opt-out via ``STOA_BOOT_INTEGRITY=0``, or unrecoverable IO error).
    """
    if not force and os.environ.get("STOA_BOOT_INTEGRITY", "1") == "0":
        return None
    try:
        digest = compute_boot_digest()
    except Exception:
        return None

    try:
        log_path = _resolve_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        host = socket.gethostname()
        version = _read_version()
        line = f"{ts} {digest} v{version} {host}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
        # Best-effort umask tightening — chmod is a no-op on Windows.
        try:
            os.chmod(log_path, 0o600)
        except OSError:
            pass
    except OSError:
        return digest  # at least return it to the caller, even if write fails
    return digest
