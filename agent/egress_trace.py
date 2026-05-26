"""Per-provider egress trace (audit Lens 46 / M-11c).

Records one append-only JSONL line per outbound LLM HTTP request at
``~/.stoa/egress/per-provider.jsonl``. Each line captures *who* made
the call (provider name) and *where it went* (URL host + port) plus a
small set of safe metadata fields — token counts, latency, status,
optional model name.

The deliberate non-goal is: **no request body, no response body, no
prompt content, no API keys, no auth tokens, no PII**. The redaction
strategy is "drop, don't filter" — the recorder simply never receives
those fields. Callers should not pass them in.

Design constraints
------------------

* **Must not block the request path.** All errors are swallowed; a
  recorder crash MUST NOT prevent the LLM call. Latency added to
  every call is bounded to a single sync file append.
* **Must not enlarge the cold-start budget.** Module-level side
  effects are zero; the log dir is created lazily on first append.
* **Opt-out is one env var.** ``STOA_EGRESS_TRACE=0`` no-ops every
  ``record_egress()`` call. Default is ON because the value is
  visibility — operators need a paper trail by default, not
  opt-in.
* **Hostname only.** URLs are reduced to ``scheme://host[:port]``.
  Path/query/fragment are dropped before logging so that prompt
  arguments accidentally encoded in a URL (some providers do this
  for chunked sessions) don't leak.

Schema
------

Each line is one JSON object with these keys (all optional except
``ts`` and ``provider``):

::

    {
      "ts":        "2026-05-26T01:23:45+00:00",
      "provider":  "anthropic",                       # adapter-supplied label
      "endpoint":  "https://api.anthropic.com",       # scheme://host[:port], NO path
      "host":      "api.anthropic.com",               # convenience field
      "model":     "claude-opus-4-7",                 # optional, redacted to None if empty
      "method":    "POST",
      "status":    200,                               # HTTP status if known
      "latency_ms": 4123,                             # wall-clock
      "tokens_in":  450,                              # prompt tokens, if reported
      "tokens_out": 89,
      "session":   "8c1f...",                         # opaque session id, never user-visible
      "agent_v":   "0.14.2",
    }

Operators can ``jq`` the file directly, or use the companion
``stoa egress`` CLI (separate file) which provides ``show`` / ``export``.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


def _is_enabled() -> bool:
    return os.environ.get("STOA_EGRESS_TRACE", "1") != "0"


def _resolve_log_dir() -> Path:
    home = os.environ.get("STOA_HOME")
    base = Path(home) if home else Path.home() / ".stoa"
    return base / "egress"


def _resolve_log_path() -> Path:
    return _resolve_log_dir() / "per-provider.jsonl"


def _reduce_url(url: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(endpoint, host)`` from *url* with path/query/fragment dropped.

    A URL like ``https://api.openai.com/v1/chat/completions?key=ABC`` becomes
    ``("https://api.openai.com", "api.openai.com")``. Garbage in returns
    ``(None, None)`` rather than raising — recorder must not crash on
    callers that hand it a junk URL.
    """
    if not url:
        return None, None
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None, None
        endpoint = f"{parsed.scheme}://{parsed.netloc}"
        host = parsed.hostname or parsed.netloc
        return endpoint, host
    except Exception:
        return None, None


def _read_agent_version() -> str:
    try:
        from stoa_cli import __version__  # type: ignore
        return str(__version__)
    except Exception:
        return "unknown"


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _safe_string(value: Any, max_len: int = 200) -> Optional[str]:
    if value is None:
        return None
    try:
        s = str(value)
    except Exception:
        return None
    s = s.strip()
    if not s:
        return None
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


# Public API ----------------------------------------------------------


def record_egress(
    provider: str,
    url: Optional[str] = None,
    *,
    model: Optional[str] = None,
    method: Optional[str] = None,
    status: Optional[int] = None,
    latency_ms: Optional[int] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    session: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Append one egress record to ``~/.stoa/egress/per-provider.jsonl``.

    Returns the record on success or ``None`` if recording was skipped
    (opt-out, or unrecoverable IO error). Never raises — a failing
    recorder MUST NOT prevent the wrapping LLM call from proceeding.

    The caller does NOT pass body / headers / API keys / prompt text.
    The recorder's safety property is that those fields never enter
    its signature in the first place — see the module docstring.
    """
    if not _is_enabled():
        return None

    try:
        endpoint, host = _reduce_url(url)
        record: Dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "provider": _safe_string(provider, max_len=64) or "unknown",
            "agent_v": _read_agent_version(),
        }
        if endpoint:
            record["endpoint"] = endpoint
        if host:
            record["host"] = host
        if model:
            record["model"] = _safe_string(model, max_len=128)
        if method:
            record["method"] = _safe_string(method, max_len=8)
        if status is not None:
            s = _safe_int(status)
            if s is not None:
                record["status"] = s
        if latency_ms is not None:
            lm = _safe_int(latency_ms)
            if lm is not None:
                record["latency_ms"] = lm
        if tokens_in is not None:
            ti = _safe_int(tokens_in)
            if ti is not None:
                record["tokens_in"] = ti
        if tokens_out is not None:
            to = _safe_int(tokens_out)
            if to is not None:
                record["tokens_out"] = to
        if session:
            record["session"] = _safe_string(session, max_len=128)
    except Exception:
        return None

    try:
        log_path = _resolve_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialise once, then a single append-write. JSONL = one
        # record per line, ASCII-safe so the file roundtrips through
        # legacy log shippers.
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
        try:
            os.chmod(log_path, 0o600)
        except OSError:
            pass
    except OSError:
        # Disk full, permission denied, broken filesystem — return the
        # record anyway so the caller (rare: a wrapping tracer) can
        # still emit it elsewhere.
        return record
    return record


def read_recent(n: int = 50) -> list[Dict[str, Any]]:
    """Return the last *n* records from the log, or [] if missing.

    Convenience reader for the companion CLI. Returns a list of dicts
    in chronological order (oldest first). Malformed JSON lines are
    skipped silently; the file is expected to be append-only so the
    only realistic source of corruption is a partial write — which
    will simply be ignored on the next read.
    """
    log_path = _resolve_log_path()
    if not log_path.exists():
        return []
    if n <= 0:
        return []
    try:
        # Cheap "tail -N" without holding the full file in memory.
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # Read trailing 64 KiB or the whole file, whichever is smaller.
            window = min(size, 64 * 1024)
            f.seek(size - window)
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    out: list[Dict[str, Any]] = []
    for raw_line in raw.splitlines()[-n:]:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            obj = json.loads(raw_line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def get_log_path() -> Path:
    """Public accessor for the log path (so callers don't reconstruct it)."""
    return _resolve_log_path()


__all__ = ["record_egress", "read_recent", "get_log_path"]


# Hostname is captured once at import so a recorder under chroot or
# inside a sandbox still gets a stable identifier — but is NOT
# emitted per-record by default because most operators want the file
# to be portable across machines without leaking which laptop they
# audited it from. If you need per-host attribution, wrap
# `record_egress` and add a `host_machine` field yourself.
_THIS_HOST = socket.gethostname()  # noqa: F841 — reserved for future opt-in field
