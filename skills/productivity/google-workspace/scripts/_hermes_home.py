"""Resolve STOA_HOME for standalone skill scripts.

Skill scripts may run outside the Hermes process (e.g. system Python,
nix env, CI) where ``stoa_constants`` is not importable.  This module
provides the same ``get_stoa_home()`` and ``display_stoa_home()``
contracts as ``stoa_constants`` without requiring it on ``sys.path``.

When ``stoa_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``stoa_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``STOA_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from stoa_constants import display_stoa_home as display_stoa_home
    from stoa_constants import get_stoa_home as get_stoa_home
except (ModuleNotFoundError, ImportError):

    def get_stoa_home() -> Path:
        """Return the Hermes home directory (default: ~/.stoa).

        Mirrors ``stoa_constants.get_stoa_home()``."""
        val = os.environ.get("STOA_HOME", "").strip()
        return Path(val) if val else Path.home() / ".hermes"

    def display_stoa_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``stoa_constants.display_stoa_home()``."""
        home = get_stoa_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
