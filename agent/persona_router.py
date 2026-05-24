"""
STOA persona router — maps the six council personas (plus STOA the seventh)
to concrete (provider, model, api_mode) tuples.

The mapping lives in ``~/.stoa/cli-config.yaml`` under the ``personas:`` key,
so the operator can rotate any persona to a different sovereign LLM provider
without code changes.

The router does NOT instantiate clients — it only resolves the routing tuple.
``CouncilProvider`` (see ``verdict_composer.py``) calls ``resolve_persona()``
to look up which provider profile each persona uses, then dispatches in
parallel through the standard ``ProviderProfile`` transport layer.

Why this matters (STOA's "beyond STOA" feature):
    STOA routes a session to one LLM. STOA routes a single task to six LLMs
    in parallel — one per sovereign provider — and a quorum decides. The
    router is the seam where "single brain" becomes "council".

Why externalized config: the six marketing-public model names (Claude Opus
4.7 / GPT-5 / Gemini 2.5 Pro / Grok 4 / Llama 3.3 405B / Mistral Large 3)
must never leak the real backend in any user-facing surface. The config file
is the only place real provider names live. UI strings read from
``persona_display_name()`` which returns marketing names regardless of the
underlying provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from stoa_constants import get_stoa_home

PersonaName = Literal["sokrates", "mira", "veritas", "drax", "lyra", "echo", "stoa"]

CHAMBER_PERSONAS: tuple[PersonaName, ...] = (
    "sokrates", "mira", "veritas", "drax", "lyra", "echo",
)
"""The six council agents. STOA is the seventh (dispatcher) and is NOT
included in council dispatches — STOA composes the verdict over the six."""


DEFAULT_PERSONAS: dict[str, dict[str, str]] = {
    "sokrates": {"provider": "anthropic",  "model": "claude-opus-4-7",     "api_mode": "anthropic"},
    "mira":     {"provider": "openrouter", "model": "openai/gpt-5",         "api_mode": "chat_completions"},
    "veritas":  {"provider": "openrouter", "model": "google/gemini-2.5-pro", "api_mode": "chat_completions"},
    "drax":     {"provider": "openrouter", "model": "xai/grok-4",            "api_mode": "chat_completions"},
    "lyra":     {"provider": "openrouter", "model": "meta-llama/llama-3.3-405b", "api_mode": "chat_completions"},
    "echo":     {"provider": "openrouter", "model": "mistralai/mistral-large-3", "api_mode": "chat_completions"},
    "stoa":   {"provider": "deepseek",   "model": "deepseek-chat",        "api_mode": "chat_completions"},
}


PERSONA_MARKETING_NAME: dict[PersonaName, str] = {
    "sokrates": "Claude Opus 4.7",
    "mira":     "GPT-5",
    "veritas":  "Gemini 2.5 Pro",
    "drax":     "Grok 4",
    "lyra":     "Llama 3.3 405B",
    "echo":     "Mistral Large 3",
    "stoa":   "STOA (dispatcher)",
}
"""Marketing-public model names. NEVER leak the real backend (DeepSeek,
OpenRouter, etc.) in user-facing surfaces — pricing, copy, social shares.
This dict is the single source of truth for display names."""


PERSONA_ROLE: dict[PersonaName, str] = {
    "sokrates": "the question-maker",
    "mira":     "the builder",
    "veritas":  "the auditor",
    "drax":     "the red team",
    "lyra":     "the designer",
    "echo":     "the operator",
    "stoa":   "the dispatcher (the seventh)",
}


@dataclass(frozen=True)
class PersonaRoute:
    persona: PersonaName
    provider: str
    model: str
    api_mode: str
    role: str
    marketing_name: str


_CONFIG_CACHE: dict[str, dict[str, str]] | None = None


def _config_path() -> Path:
    return get_stoa_home() / "cli-config.yaml"


def _load_personas_config() -> dict[str, dict[str, str]]:
    """Read ``personas:`` block from ``~/.stoa/cli-config.yaml``.

    Falls back to ``DEFAULT_PERSONAS`` if the file or block is missing. The
    fallback is intentional — a fresh install should be able to dispatch
    council without forcing the user to write YAML first."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cfg_path = _config_path()
    merged = {**DEFAULT_PERSONAS}
    try:
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            personas = raw.get("personas")
            if isinstance(personas, dict):
                for name, body in personas.items():
                    if isinstance(body, dict) and name in DEFAULT_PERSONAS:
                        merged[name] = {**DEFAULT_PERSONAS[name], **body}
    except Exception:
        # Config errors fall back silently to defaults rather than blocking
        # the agent — the operator can run ``stoa doctor`` to see the parse
        # error explicitly.
        pass
    _CONFIG_CACHE = merged
    return merged


def reset_persona_cache() -> None:
    """Invalidate the in-process cache; pick up YAML edits on next call.

    Used by ``stoa /persona <name>`` after a config rewrite, and by tests."""
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def resolve_persona(persona: PersonaName) -> PersonaRoute:
    """Return the full routing tuple for a persona.

    Raises ``KeyError`` if the persona is not one of the seven known names —
    we deliberately do not allow arbitrary persona names, because every
    persona has a fixed role + marketing name + color in the rest of the
    codebase."""
    cfg = _load_personas_config()
    body = cfg[persona]  # KeyError if unknown
    return PersonaRoute(
        persona=persona,
        provider=body["provider"],
        model=body["model"],
        api_mode=body.get("api_mode", "chat_completions"),
        role=PERSONA_ROLE[persona],
        marketing_name=PERSONA_MARKETING_NAME[persona],
    )


def resolve_council() -> list[PersonaRoute]:
    """The six personas that form the chamber, in canonical order.

    Order is fixed because it shows up everywhere — splash dashboard, color
    rotation, verdict citation. Sokrates first by tradition; Echo last as
    "the chorus that closes the room"."""
    return [resolve_persona(p) for p in CHAMBER_PERSONAS]


def resolve_dispatcher() -> PersonaRoute:
    """STOA — the seventh agent, composes verdicts over the six."""
    return resolve_persona("stoa")


def persona_display_name(persona: PersonaName) -> str:
    """Marketing-public name. NEVER returns the real backend name."""
    return PERSONA_MARKETING_NAME[persona]
