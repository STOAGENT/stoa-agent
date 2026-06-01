/**
 * Display-string sanitizer for untrusted agent/tool/web content rendered in
 * the dashboard (GAP-09-06).
 *
 * Tool args/results, summaries and errors can carry attacker-controlled bytes
 * (from web pages, MCP/ACP peers, or skill output). Even though the browser
 * does not execute these in a text node, control bytes, ANSI escape
 * sequences, and Unicode bidi / zero-width / tag-plane characters let an
 * attacker SPOOF what the operator sees — reverse a displayed command, hide
 * approval text, or smuggle invisible content. Strip them before rendering.
 *
 * What is removed:
 *  - C0 control bytes (U+0000–U+001F) except \t \n \r, and DEL (U+007F)
 *  - C1 control bytes (U+0080–U+009F)
 *  - ANSI/VT escape sequences (CSI/OSC/etc. introduced by ESC, U+001B)
 *  - Unicode bidirectional overrides/embeddings/isolates
 *    (U+202A–U+202E, U+2066–U+2069) and the deprecated marks U+200E/U+200F
 *  - Zero-width characters (U+200B–U+200D, U+FEFF)
 *  - Tag-plane characters (U+E0000–U+E007F)
 */

// ESC-introduced escape sequences (ANSI/VT). Covers CSI (ESC [ … final),
// OSC (ESC ] … BEL/ST), and the generic two-byte / Fe forms.
// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;

// C0 controls except \t (\x09) \n (\x0A) \r (\x0D); plus DEL; plus C1.
// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x80-\x9F]/g;

// Bidi overrides/embeddings/isolates + directional marks.
const BIDI_RE = /[‪-‮⁦-⁩‎‏]/g;

// Zero-width characters + BOM.
const ZERO_WIDTH_RE = /[​-‍﻿]/g;

// Unicode tag-plane (U+E0000–U+E007F).
const TAG_PLANE_RE = /[\u{E0000}-\u{E007F}]/gu;

/**
 * Strip control bytes, ANSI escapes, and bidi/zero-width/tag-plane
 * characters from an untrusted display string. Newlines/tabs are preserved.
 */
export function sanitizeDisplay(input: string | undefined | null): string {
  if (!input) return "";
  return input
    .replace(ANSI_RE, "")
    .replace(CONTROL_RE, "")
    .replace(BIDI_RE, "")
    .replace(ZERO_WIDTH_RE, "")
    .replace(TAG_PLANE_RE, "");
}
