"""Tests for ``stoa mcp allow`` / ``deny`` / ``allowlist`` commands.

Audit Lens 46 / M-11b coverage. These tests pin the contract:

* server-level toggle writes ``enabled: true|false``
* tool-level toggle writes ``tools.include`` / ``tools.exclude`` and
  keeps the two lists mutually consistent
* parse errors on malformed targets short-circuit before any save
* unknown server name does not silently create a new entry
"""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
import yaml

from stoa_cli import mcp_allowlist


@pytest.fixture
def _stoa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point STOA_HOME at a tmp directory and seed an empty config.yaml."""
    monkeypatch.setenv("STOA_HOME", str(tmp_path))
    # Make sure cached config loaders re-evaluate against the new env.
    from stoa_cli import config as _config
    monkeypatch.setattr(_config, "_LOAD_CONFIG_CACHE", {})
    return tmp_path


def _seed_config(tmp_path: Path, servers: dict) -> Path:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({"mcp_servers": servers}), encoding="utf-8")
    return cfg_path


def _read_servers(tmp_path: Path) -> dict:
    cfg_path = tmp_path / "config.yaml"
    if not cfg_path.exists():
        return {}
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return raw.get("mcp_servers", {})


# ── _split_target ─────────────────────────────────────────────────────


def test_split_target_server_only():
    assert mcp_allowlist._split_target("github") == ("github", None)


def test_split_target_server_and_tool():
    assert mcp_allowlist._split_target("github:create_pr") == ("github", "create_pr")


@pytest.mark.parametrize("bad", ["", ":", ":tool", "server:", ":", ":just_tool"])
def test_split_target_rejects_malformed(bad):
    with pytest.raises(ValueError):
        mcp_allowlist._split_target(bad)


# ── allow at server level ─────────────────────────────────────────────


def test_allow_server_marks_enabled_true(_stoa_home: Path):
    _seed_config(_stoa_home, {"github": {"url": "https://x", "enabled": False}})
    mcp_allowlist.cmd_mcp_allow(SimpleNamespace(target="github"))
    servers = _read_servers(_stoa_home)
    assert servers["github"]["enabled"] is True


def test_allow_unknown_server_does_not_create_entry(_stoa_home: Path, capsys):
    _seed_config(_stoa_home, {"github": {"url": "https://x"}})
    mcp_allowlist.cmd_mcp_allow(SimpleNamespace(target="ghost"))
    servers = _read_servers(_stoa_home)
    assert "ghost" not in servers, "deny must not silently create a server entry"
    captured = capsys.readouterr()
    assert "unknown server" in captured.out


# ── deny at server level ──────────────────────────────────────────────


def test_deny_server_marks_enabled_false(_stoa_home: Path):
    _seed_config(_stoa_home, {"github": {"url": "https://x", "enabled": True}})
    mcp_allowlist.cmd_mcp_deny(SimpleNamespace(target="github"))
    servers = _read_servers(_stoa_home)
    assert servers["github"]["enabled"] is False


# ── tool-level granularity ────────────────────────────────────────────


def test_allow_tool_appends_to_include(_stoa_home: Path):
    _seed_config(_stoa_home, {"github": {"url": "https://x"}})
    mcp_allowlist.cmd_mcp_allow(SimpleNamespace(target="github:create_pr"))
    servers = _read_servers(_stoa_home)
    assert servers["github"]["tools"]["include"] == ["create_pr"]


def test_deny_tool_appends_to_exclude(_stoa_home: Path):
    _seed_config(_stoa_home, {"github": {"url": "https://x"}})
    mcp_allowlist.cmd_mcp_deny(SimpleNamespace(target="github:delete_repo"))
    servers = _read_servers(_stoa_home)
    assert servers["github"]["tools"]["exclude"] == ["delete_repo"]


def test_allow_tool_removes_prior_exclude_entry(_stoa_home: Path):
    """The two lists must never disagree — allow wins over a prior deny."""
    _seed_config(_stoa_home, {
        "github": {"url": "https://x", "tools": {"exclude": ["create_pr", "merge_pr"]}}
    })
    mcp_allowlist.cmd_mcp_allow(SimpleNamespace(target="github:create_pr"))
    servers = _read_servers(_stoa_home)
    assert "create_pr" in servers["github"]["tools"].get("include", [])
    assert "create_pr" not in servers["github"]["tools"].get("exclude", [])
    # The other excluded tool must remain.
    assert "merge_pr" in servers["github"]["tools"]["exclude"]


def test_deny_tool_removes_prior_include_entry(_stoa_home: Path):
    _seed_config(_stoa_home, {
        "github": {"url": "https://x", "tools": {"include": ["create_pr", "list_pr"]}}
    })
    mcp_allowlist.cmd_mcp_deny(SimpleNamespace(target="github:create_pr"))
    servers = _read_servers(_stoa_home)
    assert "create_pr" in servers["github"]["tools"].get("exclude", [])
    assert "create_pr" not in servers["github"]["tools"].get("include", [])
    assert "list_pr" in servers["github"]["tools"]["include"]


def test_allow_tool_is_idempotent(_stoa_home: Path):
    _seed_config(_stoa_home, {"github": {"url": "https://x"}})
    cmd = SimpleNamespace(target="github:create_pr")
    mcp_allowlist.cmd_mcp_allow(cmd)
    mcp_allowlist.cmd_mcp_allow(cmd)
    servers = _read_servers(_stoa_home)
    # Must appear exactly once.
    assert servers["github"]["tools"]["include"].count("create_pr") == 1


# ── allowlist view ───────────────────────────────────────────────────


def test_allowlist_view_lists_all_servers(_stoa_home: Path, capsys):
    _seed_config(_stoa_home, {
        "alpha": {"url": "https://a", "enabled": True},
        "bravo": {"url": "https://b", "enabled": False,
                  "tools": {"include": ["safe_op"], "exclude": ["dangerous_op"]}},
    })
    mcp_allowlist.cmd_mcp_allowlist()
    captured = capsys.readouterr().out
    assert "alpha" in captured
    assert "bravo" in captured
    # Server-state markers visible (color codes also emitted but the symbol
    # itself is the load-bearing affordance).
    assert "allow" in captured or "✓" in captured
    assert "deny" in captured or "✗" in captured
    # Tool counts visible for bravo
    assert "include 1" in captured
    assert "exclude 1" in captured


def test_allowlist_view_handles_empty(_stoa_home: Path, capsys):
    mcp_allowlist.cmd_mcp_allowlist()
    captured = capsys.readouterr().out
    assert "no mcp servers configured" in captured.lower()


# ── parse-error short-circuit ────────────────────────────────────────


def test_allow_with_missing_target_does_not_crash(_stoa_home: Path, capsys):
    _seed_config(_stoa_home, {"github": {"url": "https://x"}})
    mcp_allowlist.cmd_mcp_allow(SimpleNamespace())  # no target attribute
    captured = capsys.readouterr().out
    assert "missing target" in captured.lower()
    # Config must not have been touched.
    servers = _read_servers(_stoa_home)
    assert servers["github"] == {"url": "https://x"}


def test_deny_with_malformed_target_does_not_crash(_stoa_home: Path, capsys):
    _seed_config(_stoa_home, {"github": {"url": "https://x"}})
    mcp_allowlist.cmd_mcp_deny(SimpleNamespace(target=":just-a-tool"))
    captured = capsys.readouterr().out
    assert "invalid target" in captured.lower()
    servers = _read_servers(_stoa_home)
    assert servers["github"] == {"url": "https://x"}
