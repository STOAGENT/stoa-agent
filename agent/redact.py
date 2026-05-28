"""Regex-based secret redaction for logs and tool output.

Applies pattern matching to mask API keys, tokens, and credentials
before they reach log files, verbose output, or gateway logs.

Short tokens (< 18 chars) are fully masked. Longer tokens preserve
the first 6 and last 4 characters for debuggability.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Sensitive query-string parameter names (case-insensitive exact match).
# Ported from nearai/ironclaw#2529 — catches tokens whose values don't match
# any known vendor prefix regex (e.g. opaque tokens, short OAuth codes).
_SENSITIVE_QUERY_PARAMS = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "password",
    "auth",
    "jwt",
    "session",
    "secret",
    "key",
    "code",           # OAuth authorization codes
    "signature",      # pre-signed URL signatures
    "x-amz-signature",
})

# Sensitive form-urlencoded / JSON body key names (case-insensitive exact match).
# Exact match, NOT substring — "token_count" and "session_id" must NOT match.
# Ported from nearai/ironclaw#2529.
_SENSITIVE_BODY_KEYS = frozenset({
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "password",
    "auth",
    "jwt",
    "secret",
    "private_key",
    "authorization",
    "key",
})

# Snapshot at import time so runtime env mutations (e.g. LLM-generated
# `export STOA_REDACT_SECRETS=false`) cannot disable redaction
# mid-session.  ON by default — secure default per issue #17691. Users who
# need raw credential values in tool output (e.g. working on the redactor
# itself) can opt out via `security.redact_secrets: false` in config.yaml
# (bridged to this env var in stoa_cli/main.py, gateway/run.py, and
# cli.py) or `STOA_REDACT_SECRETS=false` in ~/.stoa/.env. An opt-out
# warning is logged at gateway and CLI startup so operators see the
# downgrade — see `_log_redaction_status()` in gateway/run.py and cli.py.
_REDACT_ENABLED = os.getenv("STOA_REDACT_SECRETS", "true").lower() in {"1", "true", "yes", "on"}


# Audit v10 HIGH-55 + v9 HIGH-46 fix: ``_REDACT_ENABLED`` was a module-
# load-time snapshot. An operator who started a session with redact:false
# (e.g. while debugging) and then flipped redact:true via config edit
# would NOT pick up the change without a process restart — the previous
# defense ("snapshot defeats mid-session env mutation by LLM-generated
# command") only addressed the dangerous direction. To make
# "operator wants MORE redaction" instant, we expose a programmatic
# re-enable helper that the config-reload path calls. The snapshot still
# defeats `_REDACT_ENABLED=false` injection via os.environ poisoning
# because the value comes from a function-scoped read, not the env.
def force_enable_redaction() -> None:
    """Flip ``_REDACT_ENABLED`` back ON regardless of current state.

    Called by gateway/CLI when the operator changes
    ``security.redact_secrets`` from false → true mid-session.
    Idempotent. Never disables — to turn off, operators must restart
    (deliberately friction-laden so a hostile prompt can't toggle it).
    """
    global _REDACT_ENABLED
    if not _REDACT_ENABLED:
        logger.warning(
            "redact: forced ON via force_enable_redaction() (was OFF). "
            "All sensitive-pattern matches will scrub from this point on."
        )
    _REDACT_ENABLED = True

# Audit v11 HIGH-57-2 fix: PII regexes for GDPR-aware redaction.
# Phone numbers, email addresses, IBAN, US SSN, credit-card BIN+last4.
# These fire on .info-level log records that aren't sensitive enough
# for the credential regexes but still trip a privacy review when
# they leak into an export / debug dump / Sentry capture.
_PII_PATTERNS = [
    # Order matters: most-specific patterns first, otherwise the
    # phone-number "any 10+ digit run" pattern eats IBAN / card prefix
    # bytes. Email is unambiguous so it can stay at the top.
    (r"\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,24})\b",
     lambda m: f"<email:{m.group(2)}>"),
    # US SSN — fixed 3-2-4 digit shape, very low false-positive rate.
    (r"\b\d{3}-\d{2}-\d{4}\b",
     lambda m: "<ssn>"),
    # IBAN: country + 2 check digits + 11-30 alphanumeric. Must come
    # BEFORE the phone pattern so a digit-heavy IBAN tail doesn't get
    # caught as a phone first.
    (r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
     lambda m: "<iban>"),
    # Credit card (Luhn-loose). 13–19 contiguous digits with optional
    # separators. Also has to come before phone — a 16-digit card with
    # no separators otherwise gets eaten by the phone pattern.
    (r"\b(?:\d[ -]?){13,19}\b",
     lambda m: "<card>"),
    # E.164-ish phone (10+ digits with optional +/spaces/dashes). Catches
    # +905551234567, (415) 555-0100, etc. Last because everything above
    # is more specific.
    (r"\+?\d[\d\s().-]{8,16}\d",
     lambda m: "<phone>"),
]


# Known API key prefixes -- match the prefix + contiguous token chars
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",           # OpenAI / OpenRouter / Anthropic (sk-ant-*)
    r"ghp_[A-Za-z0-9]{10,}",            # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",    # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",            # GitHub OAuth access token
    r"ghu_[A-Za-z0-9]{10,}",            # GitHub user-to-server token
    r"ghs_[A-Za-z0-9]{10,}",            # GitHub server-to-server token
    r"ghr_[A-Za-z0-9]{10,}",            # GitHub refresh token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack tokens
    r"AIza[A-Za-z0-9_-]{30,}",          # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",           # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",          # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",             # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",      # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",        # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",                # AWS Access Key ID
    r"ASIA[A-Z0-9]{16}",                # AWS STS temporary access key ID (M-12 audit follow-up)
    r"sk_live_[A-Za-z0-9]{10,}",        # Stripe secret key (live)
    r"sk_test_[A-Za-z0-9]{10,}",        # Stripe secret key (test)
    r"rk_live_[A-Za-z0-9]{10,}",        # Stripe restricted key
    r"rk_test_[A-Za-z0-9]{10,}",        # Stripe restricted key (test) — M-9 prefix-coverage gap
    r"pk_live_[A-Za-z0-9]{10,}",        # Stripe publishable live — usually safe but treated as secret-adjacent
    r"whsec_[A-Za-z0-9]{10,}",          # Stripe / Svix / generic webhook signing secret (M-9)
    r"SG\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # SendGrid API key (M-9 — two-segment shape; old single-seg also caught by next line for compat)
    r"SG\.[A-Za-z0-9_-]{20,}",          # SendGrid API key (legacy single-segment shape)
    r"hf_[A-Za-z0-9]{10,}",             # HuggingFace token (read/write)
    r"hf_oauth_[A-Za-z0-9_-]{10,}",     # HuggingFace OAuth token (M-9 — separate prefix variant)
    r"r8_[A-Za-z0-9]{10,}",             # Replicate API token
    r"npm_[A-Za-z0-9]{10,}",            # npm access token
    r"pypi-[A-Za-z0-9_-]{10,}",         # PyPI API token
    r"dop_v1_[A-Za-z0-9]{10,}",         # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",         # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",           # AgentMail API key
    r"sk_[A-Za-z0-9_]{10,}",            # ElevenLabs TTS key (sk_ underscore, not sk- dash)
    r"tvly-[A-Za-z0-9]{10,}",           # Tavily search API key
    r"exa_[A-Za-z0-9]{10,}",            # Exa search API key
    r"gsk_[A-Za-z0-9]{10,}",            # Groq Cloud API key
    r"syt_[A-Za-z0-9]{10,}",            # Matrix access token
    r"retaindb_[A-Za-z0-9]{10,}",       # RetainDB API key
    r"hsk-[A-Za-z0-9]{10,}",            # Hindsight API key
    r"mem0_[A-Za-z0-9]{10,}",           # Mem0 Platform API key
    r"brv_[A-Za-z0-9]{10,}",            # ByteRover API key
    r"xai-[A-Za-z0-9]{30,}",            # xAI (Grok) API key
]

# ENV assignment patterns: KEY=value where KEY contains a secret-like name
_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
_ENV_ASSIGN_RE = re.compile(
    rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2",
)

# JSON field patterns: "apiKey": "value", "token": "value", etc.
_JSON_KEY_NAMES = r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|auth_token|bearer|secret_value|raw_secret|secret_input|key_material)"
_JSON_FIELD_RE = re.compile(
    rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

# Authorization headers
_AUTH_HEADER_RE = re.compile(
    r"(Authorization:\s*Bearer\s+)(\S+)",
    re.IGNORECASE,
)

# Telegram bot tokens: bot<digits>:<token> or <digits>:<token>,
# where token part is restricted to [-A-Za-z0-9_] and length >= 30
_TELEGRAM_RE = re.compile(
    r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})",
)

# Private key blocks: -----BEGIN RSA PRIVATE KEY----- ... -----END RSA PRIVATE KEY-----
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

# Database connection strings: protocol://user:PASSWORD@host
# Catches postgres, mysql, mongodb, redis, amqp URLs and redacts the password
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)

# JWT tokens: header.payload[.signature] — always start with "eyJ" (base64 for "{")
# Matches 1-part (header only), 2-part (header.payload), and full 3-part JWTs.
_JWT_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}"           # Header (always starts with eyJ)
    r"(?:\.[A-Za-z0-9_=-]{4,}){0,2}"   # Optional payload and/or signature
)

# Discord user/role mentions: <@123456789012345678> or <@!123456789012345678>
# Snowflake IDs are 17-20 digit integers that resolve to specific Discord accounts.
# Audit M-9 (Lens 12): also masks <@&roleid> role mentions and the leading `!`/`&`
# token shape so the result reads `<@***>` / `<@!***>` / `<@&***>` consistently.
_DISCORD_MENTION_RE = re.compile(r"<@([!&]?)(\d{17,20})>")

# Slack user/channel/group mentions: <@U01ABCDEF>, <#C01ABCDEF|channel>,
# <!subteam^S01ABCDEF>. Slack IDs are uppercase-letter + alphanumeric, length
# 9-13. Bounded quantifier per audit M-9 (Lens 21 ReDoS narrowing).
_SLACK_MENTION_RE = re.compile(
    r"<(@|#|!subteam\^)([UWCGS][A-Z0-9]{8,12})(\|[^>]{0,80})?>"
)

# WhatsApp media URLs — `https://mmg.whatsapp.net/...` and
# `https://media-*.cdninstagram.com/...` (sibling Meta CDN used for WA media).
# These URLs carry a session-bound token that's effectively a credential for
# the duration of the media's lifetime; redact the entire query string when
# we see one. Bounded so a pathological 10 MB log doesn't trigger ReDoS.
_WHATSAPP_MEDIA_RE = re.compile(
    r"https://(?:mmg\.whatsapp\.net|media-[a-z0-9-]{1,40}\.cdninstagram\.com)/[^?\s]{0,1000}\?[^\s]{0,2000}"
)

# IPv4 + IPv6 address patterns (opt-in via STOA_REDACT_IP=1). Bounded
# quantifiers (each octet capped at 3 digits; IPv6 group capped at 4 hex).
# Audit M-9 Lens 12 — operators in regulated industries (healthcare,
# financial) need to scrub source IPs from audit dumps even though our
# default posture keeps them for debugging.
_IPV4_RE = re.compile(
    r"\b(?:(?:\d{1,3}\.){3}\d{1,3})\b"
)
_IPV6_RE = re.compile(
    # Conservative match — full 8-group form OR `::`-compressed; no
    # unbounded character classes. The pre-check (`":"` in text) gates
    # cheaply.
    r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b|\b::1\b|\b::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4}\b"
)

# E.164 phone numbers: +<country><number>, 7-15 digits
# Negative lookahead prevents matching hex strings or identifiers
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# URLs containing query strings — matches `scheme://...?...[# or end]`.
# Used to scan text for URLs whose query params may contain secrets.
# Ported from nearai/ironclaw#2529.
_URL_WITH_QUERY_RE = re.compile(
    r"(https?|wss?|ftp)://"          # scheme
    r"([^\s/?#]+)"                    # authority (may include userinfo)
    r"([^\s?#]*)"                     # path
    r"\?([^\s#]+)"                    # query (required)
    r"(#\S*)?",                       # optional fragment
)

# URLs containing userinfo — `scheme://user:password@host` for ANY scheme
# (not just DB protocols already covered by _DB_CONNSTR_RE above).
# Catches things like `https://user:token@api.example.com/v1/foo`.
_URL_USERINFO_RE = re.compile(
    r"(https?|wss?|ftp)://([^/\s:@]+):([^/\s@]+)@",
)

# Form-urlencoded body detection: conservative — only applies when the entire
# text looks like a query string (k=v&k=v pattern with no newlines).
_FORM_BODY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*(?:&[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*)+$"
)

# Compile known prefix patterns into one alternation
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])"
)


def mask_secret(
    value: str,
    *,
    head: int = 4,
    tail: int = 4,
    floor: int = 12,
    placeholder: str = "***",
    empty: str = "",
) -> str:
    """Mask a secret for display, preserving ``head`` and ``tail`` characters.

    Canonical helper for display-time redaction across STOA — used by
    ``stoa config``, ``stoa status``, ``stoa dump``, and anywhere
    a secret needs to be shown truncated for debuggability while still
    keeping the bulk hidden.

    Args:
        value:       The secret to mask. ``None``/empty returns ``empty``.
        head:        Leading characters to preserve. Default 4.
        tail:        Trailing characters to preserve. Default 4.
        floor:       Values shorter than ``head + tail + floor_margin`` are
                     fully masked (returns ``placeholder``). Default 12 —
                     matches the existing config/status/dump convention.
        placeholder: Value returned for too-short inputs. Default ``"***"``.
        empty:       Value returned when ``value`` is falsy (None, ""). The
                     caller can override this to e.g. ``color("(not set)",
                     Colors.DIM)`` for user-facing display.

    Examples:
        >>> mask_secret("sk-proj-abcdef1234567890")
        'sk-p...7890'
        >>> mask_secret("short")                         # fully masked
        '***'
        >>> mask_secret("")                              # empty default
        ''
        >>> mask_secret("", empty="(not set)")           # empty override
        '(not set)'
        >>> mask_secret("long-token", head=6, tail=4, floor=18)
        '***'
    """
    if not value:
        return empty
    if len(value) < floor:
        return placeholder
    return f"{value[:head]}...{value[-tail:]}"


def _mask_token(token: str) -> str:
    """Mask a log token — conservative 18-char floor, preserves 6 prefix / 4 suffix."""
    # Empty input: historically this returned "***" rather than "". Preserve.
    if not token:
        return "***"
    return mask_secret(token, head=6, tail=4, floor=18)


def _redact_query_string(query: str) -> str:
    """Redact sensitive parameter values in a URL query string.

    Handles `k=v&k=v` format. Sensitive keys (case-insensitive) have values
    replaced with `***`. Non-sensitive keys pass through unchanged.
    Empty or malformed pairs are preserved as-is.
    """
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        if key.lower() in _SENSITIVE_QUERY_PARAMS:
            parts.append(f"{key}=***")
        else:
            parts.append(pair)
    return "&".join(parts)


def _redact_url_query_params(text: str) -> str:
    """Scan text for URLs with query strings and redact sensitive params.

    Catches opaque tokens that don't match vendor prefix regexes, e.g.
    `https://example.com/cb?code=ABC123&state=xyz` → `...?code=***&state=xyz`.
    """
    def _sub(m: re.Match) -> str:
        scheme = m.group(1)
        authority = m.group(2)
        path = m.group(3)
        query = _redact_query_string(m.group(4))
        fragment = m.group(5) or ""
        return f"{scheme}://{authority}{path}?{query}{fragment}"
    return _URL_WITH_QUERY_RE.sub(_sub, text)


def _redact_url_userinfo(text: str) -> str:
    """Strip `user:password@` from HTTP/WS/FTP URLs.

    DB protocols (postgres, mysql, mongodb, redis, amqp) are handled
    separately by `_DB_CONNSTR_RE`.
    """
    return _URL_USERINFO_RE.sub(
        lambda m: f"{m.group(1)}://{m.group(2)}:***@",
        text,
    )


def _redact_form_body(text: str) -> str:
    """Redact sensitive values in a form-urlencoded body.

    Only applies when the entire input looks like a pure form body
    (k=v&k=v with no newlines, no other text). Single-line non-form
    text passes through unchanged. This is a conservative pass — the
    `_redact_url_query_params` function handles embedded query strings.
    """
    if not text or "\n" in text or "&" not in text:
        return text
    # The body-body form check is strict: only trigger on clean k=v&k=v.
    if not _FORM_BODY_RE.match(text.strip()):
        return text
    return _redact_query_string(text.strip())


def redact_sensitive_text(text: str, *, force: bool = False, code_file: bool = False) -> str:
    """Apply all redaction patterns to a block of text.

    Safe to call on any string -- non-matching text passes through unchanged.
    Disabled by default — enable via security.redact_secrets: true in config.yaml.
    Set force=True for safety boundaries that must never return raw secrets
    regardless of the user's global logging redaction preference.

    Set code_file=True to skip the ENV-assignment and JSON-field regex
    patterns when the text is known to be source code (e.g. MAX_TOKENS=***
    constants, "apiKey": "test" fixtures). Prefix patterns, auth headers,
    private keys, DB connstrings, JWTs, and URL secrets are still redacted.

    Performance: each regex pattern is gated behind a cheap substring
    pre-check (e.g. ``"=" in text`` for ENV assignments, ``"://" in text``
    for URLs, ``"eyJ" in text`` for JWTs). On a typical stoa log line
    (no secrets) this drops the 13-pattern scan from ~5.6us to ~1.8us per
    record (-68%). The pre-checks are conservative — false positives
    still run the full regex, which then doesn't match. False negatives
    are impossible because every regex requires the gated substring to
    match.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not (force or _REDACT_ENABLED):
        return text

    # Known prefixes (sk-, ghp_, etc.) — gate on substring presence
    if _has_known_prefix_substring(text):
        text = _PREFIX_RE.sub(lambda m: _mask_token(m.group(1)), text)

    # ENV assignments: OPENAI_API_KEY=***  (skip for code files — false positives)
    if not code_file:
        if "=" in text:
            def _redact_env(m):
                name, quote, value = m.group(1), m.group(2), m.group(3)
                return f"{name}={quote}{_mask_token(value)}{quote}"
            text = _ENV_ASSIGN_RE.sub(_redact_env, text)

        # JSON fields: "apiKey": "***"  (skip for code files — false positives)
        if ":" in text and '"' in text:
            def _redact_json(m):
                key, value = m.group(1), m.group(2)
                return f'{key}: "{_mask_token(value)}"'
            text = _JSON_FIELD_RE.sub(_redact_json, text)

    # Authorization headers — _AUTH_HEADER_RE is "Authorization: Bearer ..."
    # case-insensitive, so "uthorization" is the cheapest substring gate that
    # covers both "Authorization" and "authorization" without a casefold().
    if "uthorization" in text or "UTHORIZATION" in text:
        text = _AUTH_HEADER_RE.sub(
            lambda m: m.group(1) + _mask_token(m.group(2)),
            text,
        )

    # Telegram bot tokens — pattern requires ":<token>" with digits prefix
    if ":" in text:
        def _redact_telegram(m):
            prefix = m.group(1) or ""
            digits = m.group(2)
            return f"{prefix}{digits}:***"
        text = _TELEGRAM_RE.sub(_redact_telegram, text)

    # Private key blocks
    if "BEGIN" in text and "-----" in text:
        text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)

    # Database connection string passwords
    if "://" in text:
        text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)

    # JWT tokens (eyJ... — base64-encoded JSON headers)
    if "eyJ" in text:
        text = _JWT_RE.sub(lambda m: _mask_token(m.group(0)), text)

    # URL userinfo (http(s)://user:pass@host) — redact for non-DB schemes.
    # DB schemes are handled above by _DB_CONNSTR_RE.
    if "://" in text:
        text = _redact_url_userinfo(text)

        # URL query params containing opaque tokens (?access_token=…&code=…)
        if "?" in text:
            text = _redact_url_query_params(text)

    # Form-urlencoded bodies (only triggers on clean k=v&k=v inputs).
    if "&" in text and "=" in text:
        text = _redact_form_body(text)

    # Discord user/role mentions (<@snowflake_id>, <@!snowflake_id>, <@&roleid>)
    if "<@" in text:
        text = _DISCORD_MENTION_RE.sub(
            lambda m: f"<@{m.group(1)}***>", text
        )

    # Slack user/channel/group mentions
    if "<@" in text or "<#" in text or "<!subteam" in text:
        def _redact_slack(m: re.Match) -> str:
            prefix = m.group(1)
            label = m.group(3) or ""
            # Preserve human-readable label (e.g. `|channel`) but mask the ID.
            return f"<{prefix}***{label}>"
        text = _SLACK_MENTION_RE.sub(_redact_slack, text)

    # WhatsApp / Meta-CDN media URLs — strip query string entirely
    if "whatsapp.net" in text or "cdninstagram.com" in text:
        text = _WHATSAPP_MEDIA_RE.sub(
            lambda m: m.group(0).split("?", 1)[0] + "?***",
            text,
        )

    # E.164 phone numbers (Signal, WhatsApp)
    if "+" in text:
        def _redact_phone(m):
            phone = m.group(1)
            if len(phone) <= 8:
                return phone[:2] + "****" + phone[-2:]
            return phone[:4] + "****" + phone[-4:]
        text = _SIGNAL_PHONE_RE.sub(_redact_phone, text)

    # Audit P-N (PII/IP default-ON): the original implementation gated
    # these patterns behind STOA_REDACT_PII / STOA_REDACT_IP and
    # defaulted them OFF, which let an EU operator running on the
    # "default" install ship PII into provider logs by accident.
    # Resolution now goes through the central security preset
    # (stoa_cli.security_preset.is_gate_enabled) — `normal` (the new
    # default) flips both ON; `off` preserves the legacy posture for
    # operators who explicitly want maximum log readability.
    try:
        from stoa_cli.security_preset import is_gate_enabled as _gate
    except Exception:
        # security_preset is a CLI-package import; if redact is loaded
        # by a non-CLI runtime (rare — embedded test, alt entry point)
        # fall back to the explicit env-only behaviour for safety.
        def _gate(name: str) -> bool:
            return os.getenv(f"STOA_{name}", "0") == "1"

    if _gate("REDACT_PII"):
        for pat, repl in _PII_PATTERNS:
            try:
                text = re.sub(pat, repl, text)
            except Exception:
                continue

    if _gate("REDACT_IP"):
        # IPv4 — pre-check with `.` is cheap and avoids the regex on
        # text that obviously has no dotted-quad shape.
        if "." in text:
            try:
                text = _IPV4_RE.sub("<ip>", text)
            except Exception:
                pass
        # IPv6 — pre-check `:` plus at least one hex digit nearby
        if ":" in text:
            try:
                text = _IPV6_RE.sub("<ip>", text)
            except Exception:
                pass

    return text


# Substrings used to gate ``_PREFIX_RE`` execution. If none of these appear in
# the input string, the prefix regex cannot match anything, so we skip it.
# False positives are fine (they just run the regex, which then matches
# nothing) — the bound is "no false negatives" and that holds because every
# pattern in ``_PREFIX_PATTERNS`` has at least one of these as a literal
# substring of its leading characters.
#
# Derived automatically from ``_PREFIX_PATTERNS`` at module load time so a
# future PR that adds a new prefix to the regex list can't silently break
# the screen.

def _extract_literal_prefix(pattern: str) -> str:
    """Return the leading literal characters of a regex pattern.

    Stops at the first regex metacharacter (``[``, ``(``, ``\\``, ``.``,
    ``?``, ``*``, ``+``, ``|``, ``{``, ``^``, ``$``).  Returns the literal
    that any match of the pattern MUST contain as a substring, so the
    pre-screen never produces false negatives.
    """
    meta = "[(\\.?*+|{^$"
    for i, ch in enumerate(pattern):
        if ch in meta:
            return pattern[:i]
    return pattern


_PREFIX_SUBSTRINGS = tuple(
    _extract_literal_prefix(p) for p in _PREFIX_PATTERNS
)


def _has_known_prefix_substring(text: str) -> bool:
    """Return True if ``text`` contains any known credential prefix substring.

    Used as a cheap pre-check before invoking the expensive ``_PREFIX_RE``.
    """
    return any(p in text for p in _PREFIX_SUBSTRINGS)


class RedactingFormatter(logging.Formatter):
    """Log formatter that redacts secrets from all log messages."""

    def __init__(self, fmt=None, datefmt=None, style='%', **kwargs):
        super().__init__(fmt, datefmt, style, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_text(original)
