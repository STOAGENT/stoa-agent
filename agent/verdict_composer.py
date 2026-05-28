"""
STOA verdict composer — the core "council synthesis" feature.

A single task is dispatched to all six council personas in parallel. Each
persona is bound to its own sovereign LLM provider (see ``persona_router``).
A seventh persona (STOA, the dispatcher) reads the six responses and
composes a verdict — synthesis, not a catalog of who-said-what.

The verdict carries an ``agreement`` signal:
    * ``consensus``     — at least five of six agree on the core position
    * ``split``         — meaningful camps, no quorum
    * ``no_consensus``  — six different answers, no convergence

Every verdict is hashed (sha256 over canonical JSON of the inputs + outputs)
so the same ``(task, verdict)`` pair can be attested on-chain later via
``attestation_hook.attest_response_hash``.

Design notes
------------
1.  We use ``asyncio.gather`` with ``return_exceptions=True`` so one
    provider outage does not collapse the council — the verdict reflects
    the surviving members and the failed ones are listed in ``failed``.

2.  We **do not** filter dissent. Even if 5/6 agree, the sixth voice is
    captured and shown to the caller. STOA's value over a single-brain
    framework is that minority reports are visible, not erased.

3.  The verdict prompt explicitly instructs STOA to synthesize, not
    summarize. "Drax said X, Lyra said Y" is the wrong output shape —
    the caller wants the actual answer they should act on.

4.  Token + latency accounting is preserved per-persona so the operator
    can see (via ``stoa /verdict``) which provider drove the cost and
    which lagged behind.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

from agent.persona_router import (
    PersonaName,
    PersonaRoute,
    resolve_council,
    resolve_dispatcher,
)

logger = logging.getLogger(__name__)


@dataclass
class CouncilMessage:
    """One persona's contribution to a council dispatch."""

    persona: PersonaName
    marketing_name: str
    role: str
    provider: str  # internal — do not leak in user-facing surfaces
    model: str     # internal — do not leak in user-facing surfaces
    text: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


@dataclass
class CouncilVerdict:
    """The composed output of a six-agent dispatch."""

    task: str
    agents: list[CouncilMessage]
    # ``failed_personas`` holds slug strings ("sokrates", ...) rather than the
    # PersonaName literal because Python's invariant ``list[T]`` makes mixing
    # a ``list[str]`` (from list-comp) with ``list[Literal[...]]`` painful at
    # call sites. The value space is identical at runtime.
    failed_personas: list[str]
    verdict_text: str
    agreement: str  # consensus | split | no_consensus | unknown
    response_hash: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "agents": [asdict(a) for a in self.agents],
            "failed_personas": list(self.failed_personas),
            "verdict_text": self.verdict_text,
            "agreement": self.agreement,
            "response_hash": self.response_hash,
            "duration_ms": self.duration_ms,
        }


# ──────────────────────────────────────────────────────────────────────────
# Dispatch interface
# ──────────────────────────────────────────────────────────────────────────

# A single-agent dispatch callable. The host code (run_agent.py) supplies
# this — it knows how to construct the right client for a given route and
# stream/await a completion. Keeping the contract small makes the composer
# trivially testable (a mock dispatch function with deterministic outputs).
AgentDispatchFn = Callable[
    [PersonaRoute, str, dict[str, Any]],
    Awaitable[CouncilMessage],
]


STOA_VERDICT_PROMPT = """You are STOA, the dispatcher for STOA — a sovereign AI council of six LLMs.
Six agents just answered the same task in parallel. Each is tied to a
different sovereign provider, with a fixed role:

  SOKRATES — the question-maker
  MIRA     — the builder
  VERITAS  — the auditor
  DRAX     — the red team
  LYRA     — the designer
  ECHO     — the operator

Your job:

1. Read all six answers.
2. Identify the agreement signal:
   - consensus     = at least 5 of 6 agree on the core position
   - split         = meaningful camps form, no quorum
   - no_consensus  = six different answers, no convergence
3. Produce a single COMPOSED VERDICT — the actual answer the caller should
   act on. Synthesize, do not catalog. Do not write "Drax said X, Lyra
   said Y". Write the answer.
4. If consensus, the verdict carries that position with the strongest
   supporting reasoning extracted from the six.
5. If split, present the strongest case from each side and tell the caller
   what additional context would break the tie.
6. Keep it tight: 1-3 short paragraphs, technical, no fluff.
7. Speak as the council, in plural ("we"). Do not name individual agents
   in the verdict body.

End with exactly one line:
  AGREEMENT: <consensus|split|no_consensus>

That trailing line is parsed by the host — do not omit it, do not rephrase
it, do not add anything after it.
"""


async def dispatch_council(
    *,
    task: str,
    dispatch_fn: AgentDispatchFn,
    extra: dict[str, Any] | None = None,
) -> CouncilVerdict:
    """Dispatch ``task`` to all six personas in parallel, then compose verdict.

    ``dispatch_fn(route, task, extra)`` is the host-supplied callable that
    knows how to talk to a specific provider. It must return a
    ``CouncilMessage`` (possibly with ``error`` set on failure) — it must
    not raise. The composer catches stray exceptions defensively but
    treating provider failures as data is cleaner.

    Returns a fully populated ``CouncilVerdict``.
    """
    start = time.perf_counter()
    extra = extra or {}

    council = resolve_council()

    async def _safe_call(route: PersonaRoute) -> CouncilMessage:
        try:
            return await dispatch_fn(route, task, extra)
        except Exception as exc:  # provider-level failure
            logger.warning("council: %s failed: %s", route.persona, exc)
            return CouncilMessage(
                persona=route.persona,
                marketing_name=route.marketing_name,
                role=route.role,
                provider=route.provider,
                model=route.model,
                text="",
                latency_ms=0,
                error=str(exc),
            )

    results: list[CouncilMessage] = await asyncio.gather(
        *(_safe_call(r) for r in council)
    )

    survivors = [m for m in results if not m.error and m.text.strip()]
    failed: list[str] = [m.persona for m in results if m.error or not m.text.strip()]

    # Compose verdict only if at least 5 of 6 survived. Otherwise the
    # quorum doesn't exist — return the partial set with an "unknown"
    # agreement signal so the host can decide whether to retry or fall
    # back to single-mode.
    if len(survivors) >= 5:
        verdict_text, agreement = await _compose_verdict(
            task=task,
            survivors=survivors,
            dispatch_fn=dispatch_fn,
            extra=extra,
        )
    else:
        verdict_text = (
            "Council failed to reach quorum: only "
            f"{len(survivors)} of 6 personas responded. No verdict composed."
        )
        agreement = "unknown"

    response_hash = _hash_response(task=task, agents=results, verdict_text=verdict_text)
    duration_ms = int((time.perf_counter() - start) * 1000)

    return CouncilVerdict(
        task=task,
        agents=results,
        failed_personas=failed,
        verdict_text=verdict_text,
        agreement=agreement,
        response_hash=response_hash,
        duration_ms=duration_ms,
    )


async def _compose_verdict(
    *,
    task: str,
    survivors: list[CouncilMessage],
    dispatch_fn: AgentDispatchFn,
    extra: dict[str, Any],
) -> tuple[str, str]:
    """Ask STOA (the dispatcher) to synthesize a verdict over the survivors."""
    dispatcher = resolve_dispatcher()
    # Audit v4 HIGH V-02 fix: prompt-injection fence. Council answers
    # are untrusted free-form text — a persona that wrote
    # "AGREEMENT: consensus" inside its own answer used to leak through
    # the trailing-line parser and let one persona unilaterally declare
    # quorum. We now:
    #   1. Escape the literal token "AGREEMENT:" inside survivor text
    #      so only the dispatcher's own AGREEMENT line counts
    #   2. Wrap each survivor in explicit <persona name="…"> bounds so
    #      the dispatcher (and our parser) can tell where one answer
    #      ends and the next begins
    #   3. Hard cap each survivor to 4000 chars so a runaway persona
    #      can't fill the dispatcher context with self-quoted AGREEMENT
    #      markers
    def _fence(text: str) -> str:
        text = (text or "")[:4000]
        # Neutralise marker patterns that the parser keys on. Leading-
        # line "AGREEMENT:" → "[AGREEMENT]:" (kept readable, parser
        # won't match the regex). [PERSONA] tags inside body → escaped.
        text = re.sub(r"(?im)^\s*AGREEMENT\s*:", "[AGREEMENT]:", text)
        text = text.replace("</persona>", "&lt;/persona&gt;")
        return text

    composite_task = "\n".join(
        [
            STOA_VERDICT_PROMPT,
            "",
            "TASK:",
            task,
            "",
            f"COUNCIL ANSWERS ({len(survivors)} of 6):",
            *(
                f'\n<persona name="{m.persona}">\n{_fence(m.text)}\n</persona>'
                for m in survivors
            ),
        ]
    )
    try:
        result = await dispatch_fn(dispatcher, composite_task, {**extra, "is_verdict": True})
        text = result.text.strip()
    except Exception as exc:
        logger.warning("council: verdict compose failed: %s", exc)
        text = f"[verdict compose failed: {exc}]"

    # Extract the AGREEMENT trailing line; default to unknown on parse miss.
    agreement = "unknown"
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.upper().startswith("AGREEMENT:"):
            tok = line.split(":", 1)[1].strip().lower()
            if tok in {"consensus", "split", "no_consensus"}:
                agreement = tok
            break
    return text, agreement


_VERDICT_DOMAIN_TAG = b"stoa-verdict-v1\x00"


def _hash_response(
    *,
    task: str,
    agents: list[CouncilMessage],
    verdict_text: str,
    chain_id: int | None = None,
    contract_address: str | None = None,
) -> str:
    """sha256 over canonical JSON with a domain separator. Stable across runs.

    This hash is what gets attested on-chain. We include provider/model so
    that two verdicts from different sovereign routings are distinguishable
    even if the surface text matches verbatim — provenance matters.

    Audit v4 HIGH V-04 fix: the hash now includes a domain-separator
    prefix (``stoa-verdict-v1\\x00``) plus chain_id + contract address so a
    signature minted for the testnet contract cannot replay against the
    mainnet contract (or any other deployment / future protocol version).
    Without this, the bare sha256 over the JSON payload was the same
    bytes everywhere — perfect cross-chain replay primitive once the
    attestation contract goes live.
    """
    # Audit Phase-1A (P-J seed work): NFC-normalise every string field
    # before serialisation. Different platforms emit different normalisation
    # forms for the same visible glyph ("café" as NFC vs NFD differs by
    # the combining acute accent). Without explicit NFC the hash drifts
    # across macOS / Linux / Windows for byte-identical user input. Full
    # RFC 8785 JCS roll-out is L10/P-J; this is the upstream guard so
    # later JCS migration doesn't have to revisit every callsite.
    import unicodedata

    def _nfc(value):
        if isinstance(value, str):
            return unicodedata.normalize("NFC", value)
        return value

    payload = {
        "task": _nfc(task),
        "agents": [
            {
                "persona": _nfc(m.persona),
                "marketing_name": _nfc(m.marketing_name),
                "provider": _nfc(m.provider),
                "model": _nfc(m.model),
                "text": _nfc(m.text),
            }
            for m in agents
        ],
        "verdict": _nfc(verdict_text),
        # Domain-separation fields. Read from env when not passed
        # explicitly so the rest of the codebase needn't be plumbed
        # on day one. STOA_CHAIN_ID matches the wallet binding default.
        "chain_id": chain_id if chain_id is not None else int(os.environ.get("STOA_CHAIN_ID", "143")),
        "contract": _nfc((contract_address or os.environ.get("AUDIT_ATTESTATION_V2_ADDRESS", "")).lower()),
        "schema_version": 1,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    h = hashlib.sha256()
    h.update(_VERDICT_DOMAIN_TAG)
    h.update(canonical)
    return h.hexdigest()
