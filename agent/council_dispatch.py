"""Real council dispatch — wires verdict_composer to actual LLM providers.

This is the M2 piece that turns the STOA chamber from prompt-level
role-play (single model pretending to be six) into a real parallel
6-LLM call. Each persona is bound to its own sovereign provider via
``persona_router`` and we hit them in parallel through asyncio.

Design constraints
------------------
* The sync REPL needs a sync entry point. We expose ``dispatch_chamber_sync``
  which wraps ``asyncio.run(...)`` around ``verdict_composer.dispatch_council``.
* Providers fail open. If the operator only has a DeepSeek key configured,
  five of six persona dispatches will return CouncilMessage(error="no client")
  and the verdict_composer will surface "Council failed to reach quorum"
  rather than crashing. We then offer a graceful fallback to single-mode.
* No streaming, no tool calls. The chamber is a deliberation, not an
  action — each persona returns a single text response to the task.
  Tool use stays in solo + persona-overlay modes where it belongs.
* Token + latency accounting per-persona so ``/verdict`` can show which
  provider drove the cost and which lagged behind.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agent.persona_router import PersonaRoute, resolve_council
from agent.verdict_composer import CouncilMessage, CouncilVerdict, dispatch_council

logger = logging.getLogger(__name__)


_CHAMBER_SYSTEM_PROMPT = """You are speaking as one persona in the STOA chamber — a council of six
sovereign LLMs that deliberate together on a single task. Your role is fixed:

  {role_uppercase} — {role_description}

Stay in this role's frame for the entire response. Do NOT:
  - Name yourself ('I am Drax' / 'as Sokrates'). The host writes the byline.
  - Hedge with 'as an AI'. You ARE the persona for this exchange.
  - Try to be diplomatic across all viewpoints — that's the dispatcher's
    job, not yours. Take the position your role demands.
  - Run tools, ask follow-ups, or stall. The council deliberates in one
    pass.

Output 2-4 sentences of dense, technical, opinionated response. The
dispatcher will synthesize your view with the other five into a single
verdict — be sharp, not vague.
"""


async def persona_dispatch_fn(
    route: PersonaRoute,
    task: str,
    extra: dict[str, Any] | None = None,
) -> CouncilMessage:
    """Dispatch a single persona's response to ``task`` via its bound provider.

    Returns a CouncilMessage. On any failure (no API key, provider down,
    parse error) returns a message with ``error`` set and ``text=""`` so
    ``verdict_composer.dispatch_council`` can treat it as a failed persona
    without raising.
    """
    extra = extra or {}
    is_verdict = bool(extra.get("is_verdict"))
    start = time.perf_counter()

    # Lazy import — auxiliary_client pulls in the full provider chain and
    # we don't want to pay that at module-import time. The chamber is only
    # touched on /council, which is rare relative to normal chat.
    try:
        from agent.auxiliary_client import resolve_provider_client
    except Exception as exc:
        logger.warning("council: cannot import auxiliary_client: %s", exc)
        return CouncilMessage(
            persona=route.persona,
            marketing_name=route.marketing_name,
            role=route.role,
            provider=route.provider,
            model=route.model,
            text="",
            latency_ms=0,
            error=f"auxiliary_client import failed: {exc}",
        )

    # Build the per-persona system steer. The dispatcher (route.persona == 'stoa')
    # gets the composite verdict prompt from verdict_composer; non-dispatcher
    # personas get the role steer below.
    if is_verdict:
        # verdict_composer already prepends STOA_VERDICT_PROMPT to ``task`` —
        # send as a single user message, no extra system prompt needed.
        messages = [{"role": "user", "content": task}]
    else:
        system_prompt = _CHAMBER_SYSTEM_PROMPT.format(
            role_uppercase=route.persona.upper(),
            role_description=route.role,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

    # Resolve client. resolve_provider_client returns (None, None) when the
    # provider has no credentials configured — treat that as a failed
    # persona, not a crash.
    try:
        client, resolved_model = await asyncio.to_thread(
            resolve_provider_client,
            route.provider,
            route.model,
            False,    # async_mode (we wrap sync calls in to_thread)
            False,    # raw_codex
            None,     # explicit_base_url
            None,     # explicit_api_key
            route.api_mode,
        )
    except Exception as exc:
        logger.warning(
            "council: resolve_provider_client(%s) failed: %s",
            route.provider, exc,
        )
        return CouncilMessage(
            persona=route.persona,
            marketing_name=route.marketing_name,
            role=route.role,
            provider=route.provider,
            model=route.model,
            text="",
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=f"client resolve failed: {exc}",
        )

    if client is None:
        return CouncilMessage(
            persona=route.persona,
            marketing_name=route.marketing_name,
            role=route.role,
            provider=route.provider,
            model=route.model,
            text="",
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=f"no credentials for provider '{route.provider}'",
        )

    # Issue the completion. We use chat.completions.create — the universal
    # surface every adapter in auxiliary_client exposes.
    try:
        kwargs: dict[str, Any] = {
            "model": resolved_model or route.model,
            "messages": messages,
            "max_tokens": 800,        # plenty for 2-4 sentences
            "temperature": 0.7,       # some diversity across personas
        }
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(**kwargs)
        )
    except Exception as exc:
        logger.warning("council: completion failed for %s: %s", route.persona, exc)
        return CouncilMessage(
            persona=route.persona,
            marketing_name=route.marketing_name,
            role=route.role,
            provider=route.provider,
            model=route.model,
            text="",
            latency_ms=int((time.perf_counter() - start) * 1000),
            error=f"completion failed: {exc}",
        )

    # Extract text + token counts defensively (every provider returns the
    # same shape but the exact field names sometimes drift).
    text = ""
    input_tokens = 0
    output_tokens = 0
    try:
        choice = response.choices[0]
        text = (choice.message.content or "").strip()
    except Exception as exc:
        logger.warning(
            "council: failed to extract text for %s: %s", route.persona, exc
        )
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    except Exception:
        pass

    latency_ms = int((time.perf_counter() - start) * 1000)
    return CouncilMessage(
        persona=route.persona,
        marketing_name=route.marketing_name,
        role=route.role,
        provider=route.provider,
        model=resolved_model or route.model,
        text=text,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=None if text else "empty response",
    )


# ──────────────────────────────────────────────────────────────────────────
# Sync entry point for the REPL
# ──────────────────────────────────────────────────────────────────────────

def dispatch_chamber_sync(task: str) -> CouncilVerdict:
    """Sync wrapper around ``verdict_composer.dispatch_council``.

    The CLI REPL is sync; the verdict composer is async. ``asyncio.run``
    is the cleanest bridge — fresh event loop per dispatch keeps the
    REPL's main loop untouched.
    """
    return asyncio.run(
        dispatch_council(task=task, dispatch_fn=persona_dispatch_fn)
    )


def configured_personas(council: list[PersonaRoute] | None = None) -> list[str]:
    """Return the persona slugs whose provider has credentials configured.

    Used by ``/council`` to give the operator an honest pre-dispatch
    status: 'You have 1 of 6 sovereigns configured. Add more via
    `stoa setup model`.' Lets us avoid burning a roundtrip on five
    guaranteed-fail providers when the operator is solo.
    """
    try:
        from agent.auxiliary_client import resolve_provider_client
    except Exception:
        return []

    council = council if council is not None else resolve_council()
    out: list[str] = []
    for r in council:
        try:
            client, _ = resolve_provider_client(
                r.provider, r.model, False, False, None, None, r.api_mode,
            )
            if client is not None:
                out.append(r.persona)
        except Exception:
            continue
    return out


def render_verdict(verdict: CouncilVerdict) -> str:
    """Render a CouncilVerdict as a single multi-line string for the CLI.

    Format:

        ⁂ Council verdict (5/6 consensus · 4.2s · sha256:abc123…)

        [SOKRATES] Claude Opus 4.7
        ...persona response...

        [MIRA] GPT-5
        ...persona response...

        [VERDICT]
        ...composed synthesis...

        Failed personas: drax (no credentials), lyra (timeout)

    Used by ``_handle_council_command`` after dispatch_chamber_sync returns.
    """
    lines: list[str] = []
    survived = sum(1 for m in verdict.agents if not m.error and m.text.strip())
    agreement = verdict.agreement.upper()
    short_hash = verdict.response_hash[:12] + "…" if verdict.response_hash else "—"
    duration_s = verdict.duration_ms / 1000.0
    lines.append(
        f"⁂ Council verdict ({survived}/6 · {agreement} · {duration_s:.1f}s · "
        f"sha256:{short_hash})"
    )
    lines.append("")
    for m in verdict.agents:
        header = f"[{m.persona.upper()}] {m.marketing_name}"
        if m.error:
            lines.append(f"  {header}")
            lines.append(f"    (failed: {m.error})")
        else:
            lines.append(f"  {header}")
            for body_line in m.text.splitlines():
                lines.append(f"    {body_line}")
        lines.append("")
    lines.append("[VERDICT — composed by Hermes the dispatcher]")
    for body_line in verdict.verdict_text.splitlines():
        lines.append(f"  {body_line}")
    if verdict.failed_personas:
        lines.append("")
        lines.append(f"Failed: {', '.join(verdict.failed_personas)}")
    return "\n".join(lines)
