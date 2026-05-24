"""
STOA council hook for the gateway — routes ``/council``, ``/persona``,
``/attest``, ``/verdict`` slash commands from any of the 21 messaging
platforms to the council dispatcher.

STOA inherits 21 platform adapters (Telegram, Discord, Slack, WhatsApp,
Signal, Matrix, Mattermost, Email, SMS, BlueBubbles, MSGraph, Webhook,
HomeAssistant, DingTalk, Feishu, WeCom, QQ, api_server, +). All of them
already understand slash commands via the ``COMMAND_REGISTRY`` in
``stoa_cli/commands.py``.

This hook adds four STOA-specific commands so the chamber follows the
operator into every messaging app:

    /council "<task>"   Dispatch the full six-agent council and reply with
                        the verdict (and optionally the six per-persona
                        responses, depending on platform message limits).

    /persona <name>     Set the active solo-mode persona for this chat
                        (sokrates|mira|veritas|drax|lyra|echo).

    /attest             Stamp the most recent response_hash on-chain
                        via the Monad AuditAttestationV2 contract.

    /verdict            Re-display the last council verdict produced in
                        this chat (no LLM call — pulls from session).

Platform-specific rendering
---------------------------

Each gateway adapter provides a ``render_council_verdict(verdict)`` helper
that formats the response in the platform's preferred markup:

    Telegram   — MarkdownV2, per-agent quote blocks, verdict in bold
    Discord    — embeds, one per persona, verdict as final embed
    Slack      — block kit, agents as fields, verdict as section
    WhatsApp   — plain text (no markup), verdict prefix "VERDICT:"
    Signal     — plain text
    Email      — multipart/HTML
    SMS        — verdict only (no per-agent, length budget)

Token gating
------------

Council mode is wallet-gated (see ``stoa_cli/wallet.py``). The gate
runs once per session and the result is cached for the session lifetime
so the user does not pay the RPC roundtrip on every ``/council`` call.
If the wallet is unbound or the balance is below the threshold, the
hook replies with the canonical "council requires STOA" message and
suggests ``stoa wallet bind 0x...``.

This file is a SCAFFOLD. The real wiring into ``GatewayRunner._handle_message``
+ the per-platform ``render_council_verdict`` helpers lands in M2.5
(post-M2, before M3 attestation integration).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Command parser
# ──────────────────────────────────────────────────────────────────────────

STOA_SLASH_COMMANDS = ("/council", "/persona", "/attest", "/verdict")


@dataclass
class ParsedSlash:
    command: str            # /council | /persona | /attest | /verdict
    argument: str           # remainder of the message after the command
    is_stoa_command: bool   # True if recognized as one of ours


def parse_slash(message_text: str) -> ParsedSlash:
    """Detect if a message starts with a STOA slash command.

    Edge cases handled:
      * leading whitespace
      * command followed by colon, dash, or just whitespace
      * command-only message (no argument): returns argument=""
      * non-command: returns is_stoa_command=False
    """
    text = (message_text or "").lstrip()
    for cmd in STOA_SLASH_COMMANDS:
        if text == cmd or text.startswith(cmd + " ") or text.startswith(cmd + ":"):
            arg = text[len(cmd):].lstrip(": ").strip()
            return ParsedSlash(command=cmd, argument=arg, is_stoa_command=True)
    return ParsedSlash(command="", argument="", is_stoa_command=False)


# ──────────────────────────────────────────────────────────────────────────
# Dispatch contract
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class CouncilHookContext:
    """Everything the council hook needs from the gateway side."""

    platform: str               # "telegram" | "discord" | "slack" | ...
    user_id: str                # platform-specific stable id
    chat_id: str                # platform-specific stable id
    message_text: str           # raw incoming message text
    wallet_address: str | None  # bound wallet if any (from session)
    # Reply function provided by the gateway. The hook calls this with a
    # ready-to-send message; the gateway handles markup translation per
    # platform.
    reply: Callable[[str], Awaitable[None]]


@dataclass
class HookResult:
    """Outcome of a slash-command dispatch — for telemetry + tests."""

    handled: bool
    command: str
    status: str  # ok | unauthorized | rate_limited | rpc_unreachable | error
    detail: str = ""


# ──────────────────────────────────────────────────────────────────────────
# Hook entry point
# ──────────────────────────────────────────────────────────────────────────

async def handle_stoa_slash(ctx: CouncilHookContext) -> HookResult:
    """Inspect a message and dispatch it through the STOA council if it
    starts with a STOA slash command.

    Returns ``HookResult(handled=False)`` if the message is not one of
    ours (gateway then continues with the normal single-agent flow).

    M2.5 IMPLEMENTATION:
      * parse_slash(ctx.message_text)
      * if not is_stoa_command → return handled=False
      * if command == /council:
            - wallet gate check (cached per session)
            - dispatch_council(task=arg) via host-supplied dispatch_fn
            - reply with platform-appropriate render of the verdict
            - persist response_hash on the session for later /attest /verdict
      * if /persona → update session persona
      * if /attest → call attestation_hook.attest_response_hash on last verdict
      * if /verdict → fetch last verdict from session, render
    """
    parsed = parse_slash(ctx.message_text)
    if not parsed.is_stoa_command:
        return HookResult(handled=False, command="", status="ok")

    logger.info(
        "stoa slash %s on %s (chat=%s user=%s)",
        parsed.command, ctx.platform, ctx.chat_id, ctx.user_id,
    )

    # SCAFFOLD — real handlers wire to verdict_composer + wallet + attestation_hook.
    msg = (
        f"⁂ STOA council\n"
        f"command: {parsed.command}\n"
        f"argument: {parsed.argument[:200] if parsed.argument else '(none)'}\n"
        f"platform: {ctx.platform}\n"
        f"\n"
        f"(scaffold — real handler ships in v0.2)"
    )
    try:
        await ctx.reply(msg)
        return HookResult(handled=True, command=parsed.command, status="ok")
    except Exception as exc:
        logger.warning("stoa slash reply failed: %s", exc)
        return HookResult(
            handled=True, command=parsed.command, status="error", detail=str(exc),
        )


def _suppress_unused(*_args: Any) -> None:
    """Keep ``Any`` from showing up as unused while scaffolding."""
    return None
