"""CLI handlers for the ``stoa proxy`` subcommand."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

from stoa_cli.proxy.adapters import ADAPTERS, get_adapter
from stoa_cli.proxy.server import (
    AIOHTTP_AVAILABLE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    run_server,
)

logger = logging.getLogger(__name__)


def _print_aiohttp_missing() -> None:
    print(
        "stoa proxy requires aiohttp. Install one of:\n"
        "  pip install 'stoa-agent[messaging]'\n"
        "  pip install aiohttp",
        file=sys.stderr,
    )


def cmd_proxy_start(args: Any) -> int:
    """Run the proxy server in the foreground.

    Returns process exit code (0 on clean shutdown).
    """
    if not AIOHTTP_AVAILABLE:
        _print_aiohttp_missing()
        return 1

    provider = getattr(args, "provider", None) or "nous"
    try:
        adapter = get_adapter(provider)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not adapter.is_authenticated():
        auth_hint = getattr(adapter, "auth_hint", f"stoa login {adapter.name}")
        print(
            f"Not logged into {adapter.display_name}. "
            f"Run `{auth_hint}` first.",
            file=sys.stderr,
        )
        return 2

    host = getattr(args, "host", None) or DEFAULT_HOST
    port = getattr(args, "port", None) or DEFAULT_PORT

    # Audit Phase-1A (stoa proxy bind): the proxy strips inbound
    # Authorization and re-attaches the operator's real provider
    # credential. If a non-loopback host is configured AND no opt-in
    # env var is set, refuse to start — exposing the proxy on a LAN-
    # reachable interface lets any local-network client trade arbitrary
    # bearers for the operator's paid provider key.
    _safe_loopback = {"127.0.0.1", "localhost", "::1"}
    if host not in _safe_loopback and os.environ.get("STOA_PROXY_ALLOW_REMOTE_BIND", "").lower() not in ("1", "true", "yes"):
        print(
            f"proxy: refusing to bind {host}:{port} — non-loopback host\n"
            "  The proxy re-attaches your provider credential to every\n"
            "  forwarded request. Binding on a LAN-reachable interface\n"
            "  lets any client on the local network impersonate you to\n"
            "  the upstream provider.\n"
            "  If you ARE intentionally exposing the proxy (e.g. on a\n"
            "  trusted private subnet), set STOA_PROXY_ALLOW_REMOTE_BIND=1\n"
            "  and re-run.",
            file=sys.stderr,
        )
        return 2

    print(
        f"Starting STOA proxy for {adapter.display_name}\n"
        f"  Listening on:  http://{host}:{port}/v1\n"
        f"  Forwarding to: (resolved per-request from your subscription)\n"
        f"  Use any bearer token in the client — the proxy attaches your real credential.\n"
        f"\n"
        f"Press Ctrl+C to stop.",
        file=sys.stderr,
    )

    try:
        asyncio.run(run_server(adapter, host=host, port=port))
    except KeyboardInterrupt:
        print("\nproxy: stopped", file=sys.stderr)
    except OSError as exc:
        print(f"proxy: failed to bind {host}:{port}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_proxy_status(args: Any) -> int:
    """Print the status of each configured upstream adapter."""
    print("STOA proxy upstream adapters\n")
    for name in sorted(ADAPTERS):
        adapter = get_adapter(name)
        if not adapter.is_authenticated():
            print(f"  [{name:8s}] {adapter.display_name} — not logged in")
            continue
        try:
            cred = adapter.get_credential()
        except Exception as exc:
            print(
                f"  [{name:8s}] {adapter.display_name} — credentials need attention "
                f"({exc})"
            )
            continue
        expires = f" (bearer expires {cred.expires_at})" if cred.expires_at else ""
        print(f"  [{name:8s}] {adapter.display_name} — ready{expires}")
    print(
        "\nStart the proxy with: stoa proxy start [--provider <name>]"
    )
    return 0


def cmd_proxy_list_providers(args: Any) -> int:
    """List available proxy upstream providers."""
    print("Available proxy upstream providers:")
    for name in sorted(ADAPTERS):
        adapter = get_adapter(name)
        print(f"  {name}  — {adapter.display_name}")
    return 0


def cmd_proxy(args: Any) -> int:
    """Dispatch ``stoa proxy <subcommand>``."""
    sub = getattr(args, "proxy_command", None)
    if sub == "start":
        return cmd_proxy_start(args)
    if sub == "status":
        return cmd_proxy_status(args)
    if sub in {"providers", "list"}:
        return cmd_proxy_list_providers(args)
    # No subcommand → print short help.
    print(
        "stoa proxy — local OpenAI-compatible proxy that attaches your\n"
        "OAuth-authenticated provider credentials to outbound requests.\n"
        "\n"
        "Subcommands:\n"
        "  stoa proxy start [--provider nous|xai] [--host 127.0.0.1] [--port 8645]\n"
        "      Run the proxy in the foreground.\n"
        "  stoa proxy status\n"
        "      Show which upstream adapters are ready.\n"
        "  stoa proxy providers\n"
        "      List available upstream providers.\n",
        file=sys.stderr,
    )
    return 0


__all__ = [
    "cmd_proxy",
    "cmd_proxy_start",
    "cmd_proxy_status",
    "cmd_proxy_list_providers",
]
