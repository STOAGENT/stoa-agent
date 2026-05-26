"""``stoa mcp allow`` / ``stoa mcp deny`` / ``stoa mcp allowlist`` commands.

Audit Lens 46 / M-11b — the historic UX gap was that operators had no
single-command way to flip an MCP server on or off, or to pin a single
tool inside a server. The only path was ``stoa mcp configure <name>``
into the interactive picker — fine for a person at a terminal, useless
in a script or one-liner.

These commands operate on the same ``mcp_servers`` block in
``~/.stoa/config.yaml`` that ``cmd_mcp_add`` / ``cmd_mcp_remove`` /
``cmd_mcp_list`` already manage, so the schema stays single-source.

Target schema (per existing list command):

    mcp_servers:
      myserver:
        enabled: true | false             # server-level allow
        url / command / args / ...        # transport
        tools:
          include: ["tool_a", "tool_b"]   # whitelist (mutually exclusive
          exclude: ["tool_c"]             # with include — newest wins)

The CLI accepts either ``<server>`` for the server-level switch or
``<server>:<tool>`` for tool-level granularity:

    stoa mcp allow github                  # enable the github server
    stoa mcp deny  github                  # disable it
    stoa mcp allow github:create_pr        # pin tool create_pr (include list)
    stoa mcp deny  github:delete_repo      # block tool delete_repo (exclude list)
    stoa mcp allowlist                     # show the current state, all servers
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from stoa_cli.colors import Colors, color
from stoa_cli.config import load_config, save_config


def _split_target(target: str) -> Tuple[str, Optional[str]]:
    """Parse ``server`` or ``server:tool`` into a pair.

    Returns ``(server_name, tool_name_or_None)``. A leading ':' or
    trailing ':' is treated as a parse error (both raise ValueError).
    """
    if not target or target.startswith(":") or target.endswith(":"):
        raise ValueError(
            f"invalid target {target!r} — expected '<server>' or '<server>:<tool>'"
        )
    if ":" not in target:
        return target, None
    server, _, tool = target.partition(":")
    if not server or not tool:
        raise ValueError(
            f"invalid target {target!r} — expected '<server>' or '<server>:<tool>'"
        )
    return server, tool


def _ensure_server(servers: Dict[str, dict], name: str) -> dict:
    server = servers.get(name)
    if server is None:
        raise KeyError(name)
    return server


def _tools_block(server: dict) -> dict:
    """Return the server's ``tools`` sub-dict, materialising if absent."""
    tools = server.get("tools")
    if not isinstance(tools, dict):
        tools = {}
        server["tools"] = tools
    return tools


def _add_to_list(block: dict, key: str, value: str) -> bool:
    """Idempotently append ``value`` to ``block[key]``. Returns True iff added."""
    cur = block.get(key)
    if not isinstance(cur, list):
        cur = []
    if value in cur:
        return False
    cur.append(value)
    block[key] = cur
    return True


def _remove_from_list(block: dict, key: str, value: str) -> bool:
    """Idempotently remove ``value`` from ``block[key]``. Returns True iff removed."""
    cur = block.get(key)
    if not isinstance(cur, list) or value not in cur:
        return False
    new = [v for v in cur if v != value]
    if new:
        block[key] = new
    else:
        block.pop(key, None)
    return True


def _print_err(text: str) -> None:
    print(color(f"  ✗ {text}", Colors.RED))


def _print_ok(text: str) -> None:
    print(color(f"  ✓ {text}", Colors.GREEN))


def _print_info(text: str) -> None:
    print(color(f"  {text}", Colors.DIM))


def cmd_mcp_allow(args) -> None:
    """``stoa mcp allow <server>`` or ``<server>:<tool>``.

    Server form: marks the server ``enabled: true``.
    Tool form:  adds the tool to ``tools.include`` (and removes from
    ``tools.exclude`` if present, so the two lists never disagree).
    """
    target = getattr(args, "target", None)
    if not target:
        _print_err("missing target — expected '<server>' or '<server>:<tool>'")
        return
    try:
        server_name, tool_name = _split_target(target)
    except ValueError as exc:
        _print_err(str(exc))
        return

    config = load_config()
    servers = config.get("mcp_servers") or {}
    try:
        server = _ensure_server(servers, server_name)
    except KeyError:
        _print_err(f"unknown server {server_name!r}")
        if servers:
            _print_info(f"available: {', '.join(sorted(servers))}")
        return

    if tool_name is None:
        server["enabled"] = True
        config["mcp_servers"] = servers
        save_config(config)
        _print_ok(f"enabled MCP server '{server_name}'")
        return

    tools = _tools_block(server)
    added = _add_to_list(tools, "include", tool_name)
    excluded_dropped = _remove_from_list(tools, "exclude", tool_name)
    config["mcp_servers"] = servers
    save_config(config)
    if added:
        _print_ok(f"added '{tool_name}' to {server_name}.tools.include")
    else:
        _print_info(f"'{tool_name}' already in {server_name}.tools.include")
    if excluded_dropped:
        _print_info(f"removed '{tool_name}' from {server_name}.tools.exclude")


def cmd_mcp_deny(args) -> None:
    """``stoa mcp deny <server>`` or ``<server>:<tool>``.

    Server form: marks the server ``enabled: false``.
    Tool form:  adds the tool to ``tools.exclude`` (and removes from
    ``tools.include`` if present).
    """
    target = getattr(args, "target", None)
    if not target:
        _print_err("missing target — expected '<server>' or '<server>:<tool>'")
        return
    try:
        server_name, tool_name = _split_target(target)
    except ValueError as exc:
        _print_err(str(exc))
        return

    config = load_config()
    servers = config.get("mcp_servers") or {}
    try:
        server = _ensure_server(servers, server_name)
    except KeyError:
        _print_err(f"unknown server {server_name!r}")
        if servers:
            _print_info(f"available: {', '.join(sorted(servers))}")
        return

    if tool_name is None:
        server["enabled"] = False
        config["mcp_servers"] = servers
        save_config(config)
        _print_ok(f"disabled MCP server '{server_name}'")
        return

    tools = _tools_block(server)
    added = _add_to_list(tools, "exclude", tool_name)
    included_dropped = _remove_from_list(tools, "include", tool_name)
    config["mcp_servers"] = servers
    save_config(config)
    if added:
        _print_ok(f"added '{tool_name}' to {server_name}.tools.exclude")
    else:
        _print_info(f"'{tool_name}' already in {server_name}.tools.exclude")
    if included_dropped:
        _print_info(f"removed '{tool_name}' from {server_name}.tools.include")


def _format_tool_state(tools: dict) -> str:
    if not isinstance(tools, dict) or not tools:
        return "all tools"
    include = tools.get("include")
    exclude = tools.get("exclude")
    parts: List[str] = []
    if isinstance(include, list) and include:
        parts.append(f"include {len(include)} ({', '.join(include[:3])}{'…' if len(include) > 3 else ''})")
    if isinstance(exclude, list) and exclude:
        parts.append(f"exclude {len(exclude)} ({', '.join(exclude[:3])}{'…' if len(exclude) > 3 else ''})")
    return "; ".join(parts) if parts else "all tools"


def cmd_mcp_allowlist(args=None) -> None:
    """``stoa mcp allowlist`` — print the current server + tool allow/deny state.

    Sorted by server name. Distinct from ``stoa mcp list`` which is a
    transport-oriented overview; this view is focused on the allow/deny
    surface specifically (Lens 46 readability).
    """
    del args  # unused — accepts the argparse object only for dispatcher symmetry.
    config = load_config()
    servers = config.get("mcp_servers") or {}
    if not servers:
        _print_info("no MCP servers configured.")
        return

    print()
    print(color("  MCP allowlist:", Colors.CYAN + Colors.BOLD))
    print()
    print(f"  {'Server':<20} {'Server state':<14} {'Tool policy':<48}")
    print(f"  {'─' * 20} {'─' * 14} {'─' * 48}")
    for name in sorted(servers.keys()):
        cfg = servers.get(name) or {}
        enabled = cfg.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.lower() in {"1", "true", "yes"}
        state_label = (
            color("✓ allow", Colors.GREEN) if enabled else color("✗ deny", Colors.RED)
        )
        tool_label = _format_tool_state(cfg.get("tools") or {})
        # Plain left-aligned to keep the table tidy; colour codes count
        # as visible width to ljust so we hand-pad after stripping ANSI.
        print(f"  {name:<20} {state_label:<23} {tool_label}")
    print()
