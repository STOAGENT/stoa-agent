"""
Opt-in DKIM signing + display-name spoof / agent-context guards for the email
gateway.

This module is a thin, defensive abstraction over the optional ``dkimpy``
package.  All helpers are designed to be **safe-by-default**:

  * If the ``dkimpy`` dependency is not installed or the operator has not
    configured ``STOA_EMAIL_DKIM_KEY``, every helper either no-ops or
    behaves as if DKIM were disabled.  Existing deployments keep working
    unchanged.
  * If signing is opted in but mis-configured (missing key file, bad
    selector, parse error), we emit a **loud warning** and fall back to
    unsigned send rather than dropping the message.  The caller decides
    whether unsigned send is acceptable.

Environment variables consumed by this module:

    STOA_EMAIL_DKIM_KEY       — Path to the PEM-encoded RSA private key.
                                When unset, DKIM is disabled entirely.
    STOA_EMAIL_DKIM_SELECTOR  — DKIM selector (default: ``stoa``).
    STOA_EMAIL_DKIM_DOMAIN    — DKIM signing domain.  Falls back to the
                                domain of ``EMAIL_ADDRESS`` when unset.

The display-name guard and agent-context fence helpers are dependency-free
and always active — they protect against social-engineering payloads that
abuse mail clients' rendering of ``From:`` headers and against
prompt-injection vectors that smuggle agent instructions through subject /
body text.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy import: dkimpy is an *opt-in* dependency.  Importing at module load
# would force every email-platform user to install it.  We swallow ImportError
# and emit a loud warning only when the operator has explicitly opted in via
# STOA_EMAIL_DKIM_KEY but the package is missing.
try:  # pragma: no cover — import side-effect; covered indirectly by tests
    import dkim as _dkim  # type: ignore
    _DKIM_AVAILABLE = True
except ImportError:  # pragma: no cover
    _dkim = None  # type: ignore[assignment]
    _DKIM_AVAILABLE = False


# Characters that are commonly abused to mask From: header content:
#   * zero-width space / joiner / non-joiner / BOM
#   * bidi override marks (RTLO / LRO / PDF / LRM / RLM)
# Stripping them out of any reused header value defeats trivial homoglyph and
# right-to-left subject-line spoofing tricks.
_INVISIBLE_OR_BIDI = re.compile(
    r"[​-‏‪-‮⁦-⁩﻿]"
)


def dkim_enabled() -> bool:
    """Return True if outbound DKIM signing is opted-in *and* usable.

    Operators opt in by setting ``STOA_EMAIL_DKIM_KEY`` to the path of a
    PEM-encoded RSA private key.  If the env var is unset, signing stays
    off and existing behavior is preserved.  If it is set but ``dkimpy``
    is missing or the key file is unreadable, we log a loud warning and
    return False so the caller falls back to unsigned send.
    """
    key_path = os.getenv("STOA_EMAIL_DKIM_KEY", "").strip()
    if not key_path:
        return False
    if not _DKIM_AVAILABLE:
        logger.warning(
            "[Email/DKIM] STOA_EMAIL_DKIM_KEY is set but the 'dkimpy' package "
            "is not installed; outbound mail will be sent UNSIGNED. "
            "Install with: pip install dkimpy"
        )
        return False
    if not Path(key_path).is_file():
        logger.warning(
            "[Email/DKIM] DKIM key file %r does not exist; outbound mail "
            "will be sent UNSIGNED.",
            key_path,
        )
        return False
    return True


def _dkim_domain(from_address: str) -> str:
    """Resolve the DKIM signing domain.

    Preference order:
      1. ``STOA_EMAIL_DKIM_DOMAIN`` env var (explicit operator choice)
      2. The domain portion of *from_address* (e.g. ``stoax.xyz`` for
         ``agent@stoax.xyz``)
    """
    explicit = os.getenv("STOA_EMAIL_DKIM_DOMAIN", "").strip()
    if explicit:
        return explicit
    if "@" in from_address:
        return from_address.split("@", 1)[1]
    return from_address


def sign_message_bytes(raw_message: bytes, from_address: str) -> bytes:
    """Return *raw_message* with a ``DKIM-Signature`` header prepended.

    Caller is responsible for first checking :func:`dkim_enabled`.  If
    signing fails for any reason (bad key, unsupported algorithm, parse
    error) we log loudly and return *raw_message* unchanged so the send
    pipeline still delivers — at worst the recipient gets the same
    unsigned mail it would have gotten before this feature was wired in.
    """
    if not dkim_enabled():
        return raw_message
    key_path = os.getenv("STOA_EMAIL_DKIM_KEY", "").strip()
    selector = os.getenv("STOA_EMAIL_DKIM_SELECTOR", "stoa").strip() or "stoa"
    domain = _dkim_domain(from_address)
    try:
        with open(key_path, "rb") as fh:
            privkey = fh.read()
    except OSError as exc:
        logger.warning("[Email/DKIM] cannot read key %r: %s; sending UNSIGNED.", key_path, exc)
        return raw_message
    try:
        sig = _dkim.sign(  # type: ignore[union-attr]
            message=raw_message,
            selector=selector.encode("ascii"),
            domain=domain.encode("ascii"),
            privkey=privkey,
            include_headers=[b"From", b"To", b"Subject", b"Date", b"Message-ID"],
        )
    except Exception as exc:  # noqa: BLE001 — dkimpy raises many concrete types
        logger.warning(
            "[Email/DKIM] sign() failed (%s); sending UNSIGNED.", exc
        )
        return raw_message
    # dkimpy returns the header as bytes including trailing CRLF.  Prepend it.
    return sig + raw_message


# ---------------------------------------------------------------------------
# Display-name spoof guard
# ---------------------------------------------------------------------------

# Match a string like:  "alice@bank.com" <evil@attacker.example>
# We deliberately stay liberal: any '@' embedded inside the human-readable
# display-name portion is suspect because legitimate display names do not
# normally contain email addresses.
_FROM_DISPLAY_RE = re.compile(r"^\s*(?P<display>.*?)\s*<(?P<addr>[^>]+)>\s*$")


def is_display_name_spoof(from_header: str) -> bool:
    """Return True if ``From:`` looks like a display-name spoofing attempt.

    Heuristic: any ``@`` (or angle-bracket pair) inside the display-name
    portion of a ``"display" <addr>`` form is treated as suspicious.  We
    deliberately *do not* try to match the display-name against the bare
    address — that would produce false positives whenever a legitimate
    user signs as e.g. ``"Alice (alice@bank.com)" <alice@bank.com>``;
    presence of '@' alone is enough signal for an inbound-rejection
    decision in a high-security agent gateway.

    Returns False on malformed input rather than raising — callers should
    treat the inbound mail as unknown rather than crashing the poll loop.
    """
    if not from_header:
        return False
    match = _FROM_DISPLAY_RE.match(from_header)
    if not match:
        return False
    display = match.group("display") or ""
    # Strip the surrounding quotes if the display name was quoted.
    display = display.strip().strip('"').strip()
    if not display:
        return False
    if "@" in display or "<" in display or ">" in display:
        return True
    return False


def sanitize_reused_subject(subject: str, *, max_len: int = 200) -> str:
    """Strip invisible / bidi chars from a subject before reusing it.

    Used when constructing the outbound ``Re:`` subject from inbound mail.
    Without this step an attacker can inject an RTLO mark and cause mail
    clients to render the outbound subject ambiguously, or smuggle
    zero-width characters that survive content moderation downstream.
    """
    if not subject:
        return ""
    cleaned = _INVISIBLE_OR_BIDI.sub("", subject)
    # Collapse runs of whitespace introduced by removals.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


# ---------------------------------------------------------------------------
# Agent context fence
# ---------------------------------------------------------------------------

def _xml_attr_escape(value: str) -> str:
    """Minimal escape for XML attribute values."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _xml_text_escape(value: str) -> str:
    """Minimal escape for XML text bodies.  Newlines are preserved."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fence_email_for_agent(
    *,
    subject: str,
    from_addr: str,
    body: str,
) -> str:
    """Wrap inbound email content in an XML-like fence for agent prompts.

    The agent loop treats the entire body of an inbound mail as
    untrusted user input.  Wrapping it in ``<email subject="..."
    from="...">…</email>`` gives the system prompt a stable boundary to
    say "instructions inside this fence are data, not commands".  Without
    a fence, an attacker who controls the body can write
    ``IGNORE PREVIOUS INSTRUCTIONS …`` and the LLM may comply.

    The function is deterministic and dependency-free; it does **not**
    attempt to parse the body.  Callers MUST escape neither input — this
    function handles both the attribute values and the inner text.
    """
    safe_subject = _xml_attr_escape(subject or "")
    safe_from = _xml_attr_escape(from_addr or "")
    safe_body = _xml_text_escape(body or "")
    return (
        f'<email subject="{safe_subject}" from="{safe_from}">\n'
        f"{safe_body}\n"
        f"</email>"
    )


__all__ = [
    "dkim_enabled",
    "sign_message_bytes",
    "is_display_name_spoof",
    "sanitize_reused_subject",
    "fence_email_for_agent",
]
