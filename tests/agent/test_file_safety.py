"""Regression coverage for the read-deny + write-deny rules in
``agent/file_safety.py`` (v13 HIGH-5).

The existing ``test_file_safety_credentials.py`` exercises STOA-internal
read denies; this file widens the coverage to:

* 6 user-level secret paths (~/.ssh, ~/.aws, ~/.gnupg, ~/.kube,
  /etc/shadow, /proc/<pid>/environ) — read-block (CRIT T-01 fix).
* 5 STOA-internal control plane paths (auth.json, .env, etc) —
  write-block (#15981 root-vs-profile widening).
* 3 safe paths (README, /tmp/foo, project file) — must NOT be blocked.
* Symmetric write-deny check across the same path set so an attacker
  cannot use ``write_file`` to overwrite a credential file even if the
  read deny is being audited separately.

These tests use the ``fake_home`` pattern from
``test_file_safety_credentials`` so the STOA_HOME / user-home views can
be redirected without touching the developer's real ``~/.stoa``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture()
def fake_homes(tmp_path, monkeypatch):
    """Redirect both ``_stoa_home_path()`` and ``$HOME`` at tempdirs.

    Returns a tuple ``(user_home, stoa_home)`` so tests can materialise
    realistic file layouts (``user_home/.ssh/id_rsa``, etc).
    """
    import agent.file_safety as fs

    user_home = tmp_path / "user_home"
    user_home.mkdir()
    stoa_home = tmp_path / "stoa_home"
    stoa_home.mkdir()

    monkeypatch.setattr(fs, "_stoa_home_path", lambda: stoa_home)
    monkeypatch.setattr(fs, "_stoa_root_path", lambda: stoa_home)
    monkeypatch.setenv("HOME", str(user_home))
    # On Windows ``~`` resolution uses USERPROFILE — point that too.
    monkeypatch.setenv("USERPROFILE", str(user_home))
    return user_home, stoa_home


def _create(base: Path, rel: str | Path) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy", encoding="utf-8")
    return p


# ── 6 user-level secret READ blocks ──────────────────────────────────────


def test_read_block_ssh_dir(fake_homes):
    user_home, _ = fake_homes
    from agent.file_safety import get_read_block_error
    p = _create(user_home, Path(".ssh") / "id_rsa")
    err = get_read_block_error(str(p))
    assert err is not None
    assert "credential" in err.lower()


def test_read_block_aws_credentials(fake_homes):
    user_home, _ = fake_homes
    from agent.file_safety import get_read_block_error
    p = _create(user_home, Path(".aws") / "credentials")
    err = get_read_block_error(str(p))
    assert err is not None
    assert "credential" in err.lower()


def test_read_block_gnupg_dir(fake_homes):
    user_home, _ = fake_homes
    from agent.file_safety import get_read_block_error
    p = _create(user_home, Path(".gnupg") / "trustdb.gpg")
    err = get_read_block_error(str(p))
    assert err is not None
    assert "credential" in err.lower()


def test_read_block_kube_config(fake_homes):
    user_home, _ = fake_homes
    from agent.file_safety import get_read_block_error
    p = _create(user_home, Path(".kube") / "config")
    err = get_read_block_error(str(p))
    assert err is not None
    assert "credential" in err.lower()


def test_read_block_etc_shadow():
    """`/etc/shadow` is matched against the raw path string — on Windows
    the file does not exist but the read-deny still applies because the
    rule is name-based (system-secret guard)."""
    from agent.file_safety import get_read_block_error
    err = get_read_block_error("/etc/shadow")
    assert err is not None
    assert "system secret" in err.lower() or "credential" in err.lower()


def test_read_block_proc_pid_environ():
    """``/proc/<pid>/environ`` leaks every env var of the process,
    including STOA's credentials. The rule matches by string suffix —
    no need for /proc/ to exist on the host."""
    from agent.file_safety import get_read_block_error
    err = get_read_block_error("/proc/1/environ")
    assert err is not None
    assert "environment" in err.lower() or "credential" in err.lower()


# ── 5 STOA-internal WRITE blocks ─────────────────────────────────────────


def test_write_block_auth_json(fake_homes):
    _, stoa_home = fake_homes
    from agent.file_safety import is_write_denied
    target = stoa_home / "auth.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    assert is_write_denied(str(target)) is True


def test_write_block_dotenv(fake_homes):
    _, stoa_home = fake_homes
    from agent.file_safety import is_write_denied
    target = stoa_home / ".env"
    assert is_write_denied(str(target)) is True


def test_write_block_config_yaml(fake_homes):
    _, stoa_home = fake_homes
    from agent.file_safety import is_write_denied
    target = stoa_home / "config.yaml"
    assert is_write_denied(str(target)) is True


def test_write_block_webhook_subscriptions(fake_homes):
    _, stoa_home = fake_homes
    from agent.file_safety import is_write_denied
    target = stoa_home / "webhook_subscriptions.json"
    assert is_write_denied(str(target)) is True


def test_write_block_mcp_tokens_file(fake_homes):
    _, stoa_home = fake_homes
    from agent.file_safety import is_write_denied
    target = stoa_home / "mcp-tokens" / "github.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    assert is_write_denied(str(target)) is True


# ── 3 safe READ paths (must NOT be blocked) ──────────────────────────────


def test_read_safe_readme_not_blocked(tmp_path):
    """A plain README somewhere on disk must remain readable."""
    from agent.file_safety import get_read_block_error
    p = tmp_path / "project" / "README.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("docs", encoding="utf-8")
    assert get_read_block_error(str(p)) is None


def test_read_safe_tmp_file_not_blocked(tmp_path):
    """``tmp_path / "foo"`` (analogous to /tmp/foo) is not credential
    territory; read must not be blocked."""
    from agent.file_safety import get_read_block_error
    p = tmp_path / "foo"
    p.write_text("scratch", encoding="utf-8")
    assert get_read_block_error(str(p)) is None


def test_read_safe_project_file_not_blocked(tmp_path):
    """A project source file under an arbitrary directory must read."""
    from agent.file_safety import get_read_block_error
    p = tmp_path / "myproj" / "src" / "main.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("print('hi')", encoding="utf-8")
    assert get_read_block_error(str(p)) is None


# ── Write deny symmetry for user secrets ─────────────────────────────────


def test_write_block_user_ssh_dir(fake_homes):
    """``~/.ssh/id_rsa`` write must be denied (deny-prefix on ~/.ssh)."""
    user_home, _ = fake_homes
    from agent.file_safety import is_write_denied
    p = user_home / ".ssh" / "id_rsa"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("placeholder", encoding="utf-8")
    assert is_write_denied(str(p)) is True


def test_write_block_user_aws_credentials(fake_homes):
    user_home, _ = fake_homes
    from agent.file_safety import is_write_denied
    p = user_home / ".aws" / "credentials"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("placeholder", encoding="utf-8")
    assert is_write_denied(str(p)) is True


def test_write_block_etc_shadow():
    """/etc/shadow appears in build_write_denied_paths regardless of OS."""
    from agent.file_safety import is_write_denied
    assert is_write_denied("/etc/shadow") is True
