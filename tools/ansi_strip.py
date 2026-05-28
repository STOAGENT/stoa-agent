"""Strip ANSI escape sequences from subprocess output + canonical Unicode
sanitisation for untrusted text (audit P-A).

ANSI strip
----------
Used by terminal_tool, code_execution_tool, and process_registry to clean
command output before returning it to the model.  This prevents ANSI codes
from entering the model's context — which is the root cause of models
copying escape sequences into file writes.

Covers the full ECMA-48 spec: CSI (including private-mode ``?`` prefix,
colon-separated params, intermediate bytes), OSC (BEL and ST terminators),
DCS/SOS/PM/APC string sequences, nF multi-byte escapes, Fp/Fe/Fs
single-byte escapes, and 8-bit C1 control characters.

Canonical sanitisation (audit P-A)
----------------------------------
``sanitize_untrusted_text`` is the one primitive every "external content
enters the agent" seam should call (memory provider prefetches, MCP tool
responses, browser-tool page text, gateway message bodies, etc.). It:

  1. Strips ANSI / C1 control sequences (terminal hijack class).
  2. Strips bidi-override codepoints (RLO/LRO/RLE/LRE/PDF/RLI/LRI/FSI/PDI)
     — the Trojan-Source class. A "DELETE" command rendered RTL can read
     as "ETELED" but execute as "DELETE".
  3. Strips zero-width characters (ZWSP, ZWNJ, ZWJ, ZWNBSP/BOM, word-
     joiner, mongolian vowel separator) — the prompt-injection
     "hidden text" class.
  4. NFKC-normalises so confusables like 'Ⓐ' and 'A' collapse to the
     same form the model would otherwise see two distinct tokens for.

Every call site that previously did some subset of this work locally
should be migrated to this one function. Keeping the policy in one
place is what makes the gate auditable.
"""

import re
import unicodedata

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b"
    r"(?:"
        r"\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"     # CSI sequence
        r"|\][\s\S]*?(?:\x07|\x1b\\)"                  # OSC (BEL or ST terminator)
        r"|[PX^_][\s\S]*?(?:\x1b\\)"                   # DCS/SOS/PM/APC strings
        r"|[\x20-\x2f]+[\x30-\x7e]"                    # nF escape sequences
        r"|[\x30-\x7e]"                                 # Fp/Fe/Fs single-byte
    r")"
    r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"       # 8-bit CSI
    r"|\x9d[\s\S]*?(?:\x07|\x9c)"                       # 8-bit OSC
    r"|[\x80-\x9f]",                                    # Other 8-bit C1 controls
    re.DOTALL,
)

# Fast-path check — skip full regex when no escape-like bytes are present.
_HAS_ESCAPE = re.compile(r"[\x1b\x80-\x9f]")

# Audit Phase-1A (PROBE-HIGH-028 / F-L32-001): the prior strip_ansi
# left C0 control bytes (0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F)
# intact. These can hijack terminals (BS=0x08 erases preceding char),
# disrupt audit logs, or smuggle prompt-injection content that
# renders as harmless prose. We strip them after the main ANSI pass.
# Preserves \t (0x09), \n (0x0A), \r (0x0D) — the legitimate
# whitespace controls.
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences AND C0 control bytes from text.

    Audit Phase-1A (PROBE-HIGH-028): the C0 control bytes (0x00-0x08,
    0x0B, 0x0C, 0x0E-0x1F, 0x7F) are stripped after the main ANSI
    pass. \\t (0x09), \\n (0x0A), and \\r (0x0D) are preserved.

    Returns the input unchanged (fast path) when no ESC, C1, or C0
    bytes are present.  Safe to call on any string — clean text
    passes through with negligible overhead.
    """
    if not text:
        return text
    if _HAS_ESCAPE.search(text):
        text = _ANSI_ESCAPE_RE.sub("", text)
    if _C0_CONTROL_RE.search(text):
        text = _C0_CONTROL_RE.sub("", text)
    return text


# ── canonical Unicode sanitisation (audit P-A) ──────────────────────────

# Bidi formatting characters that flip glyph order. Trojan-Source attacks
# (CVE-2021-42574) use these to make code reviewers see a different
# control flow than the compiler executes. Strip them outright; if the
# caller actually wants RTL text they should pass a separate untrusted
# flag rather than expecting these specific codepoints to survive.
_BIDI_OVERRIDE_RE = re.compile(
    "["
    "‪"   # LRE — left-to-right embedding
    "‫"   # RLE — right-to-left embedding
    "‬"   # PDF — pop directional formatting
    "‭"   # LRO — left-to-right override
    "‮"   # RLO — right-to-left override
    "⁦"   # LRI — left-to-right isolate
    "⁧"   # RLI — right-to-left isolate
    "⁨"   # FSI — first-strong isolate
    "⁩"   # PDI — pop directional isolate
    "]"
)

# Zero-width and invisible-by-default characters. These are the
# "hidden text" channel used in prompt-injection payloads ("ignore
# previous​instructions") and to smuggle JS/HTML through naive
# string filters.
_ZERO_WIDTH_RE = re.compile(
    "["
    "​"   # ZWSP — zero-width space
    "‌"   # ZWNJ — zero-width non-joiner
    "‍"   # ZWJ  — zero-width joiner
    "﻿"   # ZWNBSP / BOM
    "⁠"   # WORD JOINER
    "᠎"   # MONGOLIAN VOWEL SEPARATOR
    "­"   # SOFT HYPHEN
    "]"
)

# Tag-plane characters (U+E0000–U+E007F). The "tag space" U+E0020 is
# invisible in most renderers but accepted by some token parsers as a
# word break — used to obfuscate ``rm <TAG-SPACE>-rf /`` past
# string-equality command filters. Strip them outright.
_TAG_PLANE_RE = re.compile(r"[\U000E0000-\U000E007F]")


def sanitize_untrusted_text(text: str) -> str:
    """Canonicalise text that came from outside the trust boundary.

    Order matters: ANSI first (drops the SS3/CSI families), then Unicode
    normal-form NFKC (so subsequent codepoint filters work on
    canonical characters), then bidi-override strip, then zero-width
    strip.

    Returns ``""`` for falsy input. Safe to call on any string —
    well-formed plain text passes through unchanged (modulo NFKC,
    which collapses compatibility characters by design).
    """
    if not text:
        return text
    text = strip_ansi(text)
    text = unicodedata.normalize("NFKC", text)
    if _BIDI_OVERRIDE_RE.search(text):
        text = _BIDI_OVERRIDE_RE.sub("", text)
    if _ZERO_WIDTH_RE.search(text):
        text = _ZERO_WIDTH_RE.sub("", text)
    if _TAG_PLANE_RE.search(text):
        text = _TAG_PLANE_RE.sub("", text)
    return text
