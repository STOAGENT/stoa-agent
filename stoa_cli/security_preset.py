"""Central SECURITY_PRESET selector (audit P-C).

STOA grew a long tail of individual ``STOA_*`` env-var gates as new
hardening features landed (skill integrity, download SHA verify,
pinned-deps mode, PII redact, IP redact, lazy-installs, private-URL
allow, …). Fresh installs left almost all of them OFF, which is the
audit's biggest single source of "looks secure on paper, ships
insecure by default" findings.

This module collapses the gates behind one selector: ``STOA_SECURITY_PRESET``.

Values
------
``off``
    Every gate stays at its legacy default (mostly OFF). Backwards-
    compatible escape hatch for operators who explicitly want the
    old behaviour. Use only when you understand what you're turning off.

``normal`` (default for new installs)
    The audit-recommended baseline. Flips on:
      - skill ed25519 integrity check
      - installer SHA-256 verification
      - PII redaction in agent logs
      - IP-address redaction
      - skill-hub lazy-install confirmation prompts
      - private-URL block (so MCP/SSE/web tools can't reach
        169.254.169.254 / link-local / Tailscale-CGNAT)
      - npm lifecycle scripts disabled in the installer
    These are reasonable for an operator who is not running their own
    threat model. They cost some log noise + add ~200 ms to first
    skill load.

``strict``
    Everything ``normal`` does plus:
      - pinned-deps mode (refuse to install when dep tree shifted
        without a lockfile bump)
      - signed-updates required (refuse to `stoa update` to a tag
        without a verifiable signature)
      - require-download-hash (refuse to install when a download
        has no pinned SHA-256)

How call sites use it
---------------------
Each gate that historically read ``os.getenv("STOA_FOO", "0")`` should
now do::

    from stoa_cli.security_preset import is_gate_enabled
    if is_gate_enabled("FOO"):
        ...

``is_gate_enabled`` honours the legacy env-var override first (so
operators can keep flipping individual gates without changing preset),
then falls back to the preset's effective default.
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


# Effective defaults per preset. Each gate name is the env-var suffix
# (e.g. "REDACT_PII" → STOA_REDACT_PII). The value is the bool the
# preset *would* set if no explicit env override is in place.
_PRESET_DEFAULTS: Final[dict[str, dict[str, bool]]] = {
    "off": {
        # Legacy defaults (the historical "STOA_FOO unset" behaviour).
        # All gates OFF. Keep here so call sites have a single source.
        "REDACT_PII": False,
        "REDACT_IP": False,
        "REQUIRE_SKILL_INTEGRITY": False,
        "REQUIRE_DOWNLOAD_HASH": False,
        "REQUIRE_PINNED_DEPS": False,
        "REQUIRE_SIGNED_UPDATES": False,
        "ALLOW_PRIVATE_URLS": True,   # legacy: private URLs unblocked
        "NPM_ALLOW_SCRIPTS": True,    # legacy: npm postinstall ran
    },
    "normal": {
        # New default. Flip the user-facing hardening features ON.
        # Operators who explicitly want them off can either set the
        # individual env-var or downgrade to preset=off.
        "REDACT_PII": True,
        "REDACT_IP": True,
        "REQUIRE_SKILL_INTEGRITY": True,
        "REQUIRE_DOWNLOAD_HASH": False,    # placeholders not yet pinned
        "REQUIRE_PINNED_DEPS": False,      # would block lazy_deps.py
        "REQUIRE_SIGNED_UPDATES": False,   # waits on signing infra
        "ALLOW_PRIVATE_URLS": False,
        "NPM_ALLOW_SCRIPTS": False,
    },
    "strict": {
        # Lock-down. Refuse to install / update without verification.
        "REDACT_PII": True,
        "REDACT_IP": True,
        "REQUIRE_SKILL_INTEGRITY": True,
        "REQUIRE_DOWNLOAD_HASH": True,
        "REQUIRE_PINNED_DEPS": True,
        "REQUIRE_SIGNED_UPDATES": True,
        "ALLOW_PRIVATE_URLS": False,
        "NPM_ALLOW_SCRIPTS": False,
    },
}

# Default preset for fresh installs. Operators bump this via
# STOA_SECURITY_PRESET env or the security.preset config key.
_DEFAULT_PRESET: Final[str] = "normal"


def _resolve_preset() -> str:
    raw = os.environ.get("STOA_SECURITY_PRESET", "").strip().lower()
    if raw in _PRESET_DEFAULTS:
        return raw
    if raw:
        logger.warning(
            "STOA_SECURITY_PRESET=%r is not recognized (expected one of "
            "%s); falling back to %r",
            raw, sorted(_PRESET_DEFAULTS), _DEFAULT_PRESET,
        )
    return _DEFAULT_PRESET


def active_preset() -> str:
    """Return the resolved preset name (``off`` / ``normal`` / ``strict``)."""
    return _resolve_preset()


_TRUE_TOKENS: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_TOKENS: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", "n", "f"})


def _coerce_env(value: str | None) -> bool | None:
    if value is None:
        return None
    token = value.strip().lower()
    if not token:
        return None
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def is_gate_enabled(gate_name: str) -> bool:
    """Resolve a single security gate.

    Precedence (high → low):
      1. Explicit ``STOA_<GATE_NAME>`` env var (parsed permissively —
         "1"/"true"/"yes"/"on" → True; "0"/"false"/"no"/"off" → False).
      2. Effective default of the active preset.
      3. Legacy default of the ``off`` preset.
    """
    explicit = _coerce_env(os.environ.get(f"STOA_{gate_name.upper()}"))
    if explicit is not None:
        return explicit
    preset_defaults = _PRESET_DEFAULTS.get(_resolve_preset(), _PRESET_DEFAULTS["off"])
    return preset_defaults.get(gate_name.upper(), _PRESET_DEFAULTS["off"].get(gate_name.upper(), False))


def describe_state() -> dict[str, bool | str]:
    """Snapshot the active preset + every gate's effective state.

    Used by `stoa doctor` and the first-run wizard to surface what's
    actually enabled so the operator sees the security posture
    without having to read 8 env vars.
    """
    out: dict[str, bool | str] = {"preset": active_preset()}
    for gate in _PRESET_DEFAULTS["off"]:
        out[gate] = is_gate_enabled(gate)
    return out
