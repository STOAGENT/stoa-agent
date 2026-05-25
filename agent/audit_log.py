"""
STOA tool-call audit log.

Audit v9 HIGH-46 / v10 HIGH-71 / v8 CRIT-32 follow-up: STOA had no
durable record of "what tool calls did the agent attempt and were they
approved?". Approval decisions lived in process memory; on a crash or a
post-mortem the operator could not answer "did the user really approve
this `rm -rf ~/.ssh`?".

This module provides an append-only JSONL log at
``~/.stoa/audit/tool-calls.jsonl`` (one line per event), with rotation
when the file passes a configurable size cap.

Design constraints:

- **Append-only**: every entry is a single ``json.dumps(...) + "\n"``
  write. No re-writes, no in-place edits. Operators / IR analysts can
  ``tail -F`` it during an incident.
- **Hash-chained**: each entry carries the SHA-256 of the previous
  entry's JSON, so a malicious skill that truncates the file to remove
  evidence of its own actions also breaks every subsequent line's hash
  link. ``stoa audit verify`` (separate helper) walks the chain.
- **Local only**: no network. The log lives next to ``stoa_state.db``.
- **Best-effort**: any write failure logs a warning but never raises
  upstream — telemetry should not block the user's tool call. (The
  alternative would be to deny tool calls when the log is unwritable,
  which fails-closed but turns disk-full into an outage.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 16 MiB per file before rotation; keep last 8 files (~128 MiB max).
_MAX_BYTES = 16 * 1024 * 1024
_RETAIN = 8

_LOCK = threading.Lock()
_LAST_HASH: Optional[str] = None  # in-memory cache of the last entry's hash


def _log_path() -> Path:
    from stoa_constants import get_stoa_home
    return get_stoa_home() / "audit" / "tool-calls.jsonl"


def _ensure_chain_initialized() -> None:
    """Seed _LAST_HASH from the current tail of the log on first call.
    Subsequent calls within the same process use the in-memory cache."""
    global _LAST_HASH
    if _LAST_HASH is not None:
        return
    p = _log_path()
    if not p.exists():
        _LAST_HASH = "0" * 64  # genesis sentinel
        return
    try:
        # Read backwards in 8 KiB chunks to find the last newline.
        with p.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                _LAST_HASH = "0" * 64
                return
            block = min(8192, size)
            fh.seek(size - block)
            tail = fh.read(block).strip().splitlines()
        last = tail[-1].decode("utf-8") if tail else ""
        _LAST_HASH = hashlib.sha256(last.encode("utf-8")).hexdigest()
    except Exception as exc:
        logger.warning("audit log: failed to seed hash chain (%s); resetting to genesis", exc)
        _LAST_HASH = "0" * 64


def _rotate_if_needed(p: Path) -> None:
    """Roll <log>.jsonl → <log>.jsonl.1 → … → <log>.jsonl.N if oversize."""
    try:
        if not p.exists() or p.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    for i in range(_RETAIN, 0, -1):
        src = p.with_suffix(f".jsonl.{i - 1}" if i > 1 else ".jsonl")
        dst = p.with_suffix(f".jsonl.{i}")
        if src.exists():
            try:
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
            except OSError:
                continue


def record(
    *,
    kind: str,
    tool: str,
    args_preview: str = "",
    approved: Optional[bool] = None,
    actor: str = "agent",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Append one event to the audit log.

    Parameters
    ----------
    kind
        Short event class: ``"call"`` / ``"approve"`` / ``"deny"`` /
        ``"hardline-block"`` / ``"yolo-bypass"`` / etc.
    tool
        Tool name (``terminal`` / ``write_file`` / ``execute_code`` / …).
    args_preview
        First 200 chars of the args JSON, post-redaction. Never the
        full args — the log is human-skimmable, not a forensic body
        capture.
    approved
        ``True`` / ``False`` / ``None`` (event not an approval decision).
    actor
        Who initiated the decision: ``"agent"`` / ``"user"`` / ``"cron"``
        / ``"hardline"`` (auto block) / ``"yolo"`` (auto allow).
    extra
        Any small extra fields. Keep it shallow + JSON-serialisable.
    """
    global _LAST_HASH
    try:
        with _LOCK:
            _ensure_chain_initialized()
            p = _log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                p.parent.chmod(0o700)
            except OSError:
                pass
            _rotate_if_needed(p)
            preview = (args_preview or "")[:200]
            try:
                from agent.redact import redact_sensitive_text
                preview = redact_sensitive_text(preview, force=True)
            except Exception:
                pass
            entry = {
                "ts_ms": int(time.time() * 1000),
                "kind": kind,
                "tool": tool,
                "actor": actor,
                "approved": approved,
                "args_preview": preview,
                "prev_hash": _LAST_HASH,
            }
            if extra:
                entry["extra"] = extra
            line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            try:
                p.chmod(0o600)
            except OSError:
                pass
            _LAST_HASH = hashlib.sha256(line.encode("utf-8")).hexdigest()
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


def verify_chain() -> tuple[bool, Optional[str]]:
    """Walk the log and confirm every entry's prev_hash matches the
    sha256 of the previous line. Returns (ok, error_message_or_None).
    Tooling for ``stoa audit verify``."""
    p = _log_path()
    if not p.exists():
        return True, None
    expected = "0" * 64
    try:
        with p.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.rstrip("\n")
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError as je:
                    return False, f"line {lineno}: malformed JSON ({je})"
                if entry.get("prev_hash") != expected:
                    return False, (
                        f"line {lineno}: prev_hash mismatch "
                        f"(expected {expected[:12]}…, got {entry.get('prev_hash', '?')[:12]}…)"
                    )
                expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except OSError as oe:
        return False, f"read error: {oe}"
    return True, None


def _all_log_files() -> list[Path]:
    """Return current + rotated audit log files in chronological order
    (oldest first). Rotated files are ``tool-calls.jsonl.N`` where higher
    N is older (see ``_rotate_if_needed``)."""
    p = _log_path()
    files: list[Path] = []
    # Older rotations first → newest last (the live .jsonl)
    for i in range(_RETAIN, 0, -1):
        rotated = p.with_suffix(f".jsonl.{i}")
        if rotated.exists():
            files.append(rotated)
    if p.exists():
        files.append(p)
    return files


def export_log(
    since_ms: Optional[int] = None,
    user_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Collect audit log entries from the live log + rotated files.

    Used by ``stoa audit export`` to satisfy operator / compliance
    requests ("show me every tool call user X made since timestamp T").

    Parameters
    ----------
    since_ms
        Drop entries with ``ts_ms < since_ms``. Default: no time filter.
    user_filter
        Drop entries whose ``extra.user_id`` does not match this string.
        Entries without ``extra.user_id`` are also dropped when the
        filter is set.

    Returns a list of decoded entries (oldest first). Malformed lines
    are silently skipped — ``verify_chain()`` is the right tool for
    integrity checks; export is a best-effort dump.

    Note: callers must redact ``args_preview`` themselves via
    ``redact_sensitive_text(..., force=True)`` before persisting / sharing
    the dump. The CLI export command does this automatically.
    """
    out: list[dict[str, Any]] = []
    for path in _all_log_files():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.rstrip("\n")
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if since_ms is not None:
                        ts = entry.get("ts_ms")
                        if not isinstance(ts, (int, float)) or ts < since_ms:
                            continue
                    if user_filter is not None:
                        extra = entry.get("extra") or {}
                        if not isinstance(extra, dict):
                            continue
                        if str(extra.get("user_id", "")) != user_filter:
                            continue
                    out.append(entry)
        except OSError as exc:
            logger.warning("audit export: failed to read %s (%s)", path, exc)
            continue
    return out
