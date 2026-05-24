"""Regression tests for _apply_profile_override STOA_HOME guard (issue #22502).

When STOA_HOME is set to the stoa root (e.g. systemd hardcodes
STOA_HOME=/root/.stoa), _apply_profile_override must still read
active_profile and update STOA_HOME to the profile directory.

When STOA_HOME is already a profile directory (.../profiles/<name>),
_apply_profile_override must trust it and return without re-reading
active_profile (child-process inheritance contract).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _run_apply_profile_override(
    tmp_path, monkeypatch, *, stoa_home: str | None, active_profile: str | None,
    argv: list[str] | None = None,
):
    """Run _apply_profile_override in isolation.

    Returns the value of os.environ["STOA_HOME"] after the call,
    or None if unset.
    """
    stoa_root = tmp_path / ".stoa"
    stoa_root.mkdir(parents=True, exist_ok=True)

    if active_profile is not None:
        (stoa_root / "active_profile").write_text(active_profile)

    if active_profile and active_profile != "default":
        (stoa_root / "profiles" / active_profile).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if stoa_home is not None:
        monkeypatch.setenv("STOA_HOME", stoa_home)
    else:
        monkeypatch.delenv("STOA_HOME", raising=False)

    monkeypatch.setattr(sys, "argv", argv or ["stoa", "gateway", "start"])

    from stoa_cli.main import _apply_profile_override
    _apply_profile_override()

    return os.environ.get("STOA_HOME")


class TestApplyProfileOverrideSTOAHomeGuard:
    """Regression guard for issue #22502.

    Verifies that STOA_HOME pointing to the stoa root does NOT suppress
    the active_profile check, while STOA_HOME already pointing to a
    profile directory IS trusted as-is.
    """

    def test_stoa_home_at_root_with_active_profile_is_redirected(
        self, tmp_path, monkeypatch
    ):
        """STOA_HOME=/root/.stoa + active_profile=coder must redirect
        STOA_HOME to .../profiles/coder.

        Bug scenario from #22502: systemd sets STOA_HOME to the stoa root
        and the user switches to a profile via `stoa profile use`.
        Before the fix, the guard returned early and active_profile was ignored.
        """
        stoa_root = tmp_path / ".stoa"
        stoa_root.mkdir(parents=True, exist_ok=True)

        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            stoa_home=str(stoa_root),
            active_profile="coder",
        )

        assert result is not None, "STOA_HOME must be set after profile redirect"
        assert "profiles" in result, (
            f"Expected STOA_HOME to point into profiles/ dir, got: {result!r}"
        )
        assert result.endswith("coder"), (
            f"Expected STOA_HOME to end with 'coder', got: {result!r}"
        )

    def test_stoa_home_already_profile_dir_is_trusted(self, tmp_path, monkeypatch):
        """STOA_HOME=.../profiles/coder must not be overridden even when
        active_profile says something different.

        Preserves the child-process inheritance contract: a subprocess spawned
        with STOA_HOME already set to a specific profile must stay in that
        profile.
        """
        stoa_root = tmp_path / ".stoa"
        profile_dir = stoa_root / "profiles" / "coder"
        profile_dir.mkdir(parents=True, exist_ok=True)

        (stoa_root / "active_profile").write_text("other")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("STOA_HOME", str(profile_dir))
        monkeypatch.setattr(sys, "argv", ["stoa", "gateway", "start"])

        from stoa_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("STOA_HOME") == str(profile_dir), (
            "STOA_HOME must remain unchanged when already pointing to a profile dir"
        )

    def test_stoa_home_unset_reads_active_profile(self, tmp_path, monkeypatch):
        """Classic case: STOA_HOME unset + active_profile=coder must set
        STOA_HOME to the profile directory (existing behaviour must not regress).
        """
        result = _run_apply_profile_override(
            tmp_path,
            monkeypatch,
            stoa_home=None,
            active_profile="coder",
        )

        assert result is not None
        assert "coder" in result

    def test_stoa_home_unset_default_profile_no_redirect(self, tmp_path, monkeypatch):
        """active_profile=default must not redirect STOA_HOME."""
        stoa_root = tmp_path / ".stoa"
        stoa_root.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.delenv("STOA_HOME", raising=False)
        monkeypatch.setattr(sys, "argv", ["stoa", "gateway", "start"])
        (stoa_root / "active_profile").write_text("default")

        from stoa_cli.main import _apply_profile_override
        _apply_profile_override()

        assert os.environ.get("STOA_HOME") is None
