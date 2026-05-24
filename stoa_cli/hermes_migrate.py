"""
``stoa hermes migrate`` — port an existing Hermes Agent install over to
STOA.

Hermes ships a single curl install that drops everything into
``~/.stoa``. A user who has been running Hermes for months has:

  - settings (``~/.stoa/cli-config.yaml``)
  - skills (``~/.stoa/skills/``)
  - memory (``~/.stoa/sessions.db``, FTS5 indexed)
  - API keys (``~/.stoa/.keyring`` or via env)
  - platform tokens (Telegram, Discord, Slack, etc.)

We carry this over wholesale, with the following changes:

  - config keys with ``hermes_*`` prefix → ``stoa_*``
  - personas block ADDED with STOA defaults if absent
  - skills directory ``~/.stoa/skills/`` → ``~/.stoa/skills/``
  - session DB ``sessions.db`` → ``sessions.db`` (schema is identical;
    STOA adds new tables ``verdicts`` + ``attestations`` non-destructively)
  - keyring is rewrapped under the STOA salt (same providers, same keys)

This module is a SCAFFOLD. The actual file copy + YAML transformations
land in M5; the function shape is fixed so the CLI command
``stoa hermes migrate`` can be routed already.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from stoa_constants import get_stoa_home

logger = logging.getLogger(__name__)


def _hermes_home() -> Path:
    """Hermes default install location — picked up via env or fallback.

    This is the SOURCE of the migration (the user's existing Hermes
    install); the DESTINATION is ``get_stoa_home()`` (``~/.stoa``).
    Keep these two distinct — a linter pass once tried to unify them
    and broke the migrate flow."""
    env = os.getenv("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / ".hermes"


@dataclass
class MigrationReport:
    detected: bool = False
    source_dir: Path | None = None
    target_dir: Path | None = None
    moved_skills: int = 0
    copied_keys: int = 0
    copied_platforms: int = 0
    rewrote_config: bool = False
    notes: list[str] = field(default_factory=list)


def detect_hermes_install() -> Path | None:
    """Return Hermes home if it looks installed, else ``None``."""
    p = _hermes_home()
    if not p.exists():
        return None
    # A real Hermes install has these landmarks:
    landmarks = ["cli-config.yaml", "skills", "sessions.db"]
    has_any = any((p / l).exists() for l in landmarks)
    return p if has_any else None


def dry_run() -> MigrationReport:
    """Inspect Hermes home and report what WOULD be migrated."""
    src = detect_hermes_install()
    report = MigrationReport(detected=src is not None, source_dir=src)
    if not src:
        report.notes.append("no Hermes install detected at " + str(_hermes_home()))
        return report
    report.target_dir = get_stoa_home()
    skills_dir = src / "skills"
    if skills_dir.is_dir():
        report.moved_skills = sum(1 for p in skills_dir.rglob("SKILL.md"))
    # Real key + platform counts come from inspecting the keyring + cli-config
    # in M5+. For now we just note the source dir exists.
    report.notes.append(
        f"detected {report.moved_skills} skills; will copy config + sessions DB + keyring."
    )
    return report


def migrate(*, force: bool = False) -> MigrationReport:
    """Copy Hermes home → STOA home with the documented rewrites.

    M5 IMPLEMENTATION:
      1. dry_run() to see what is there
      2. backup the existing ~/.stoa to ~/.stoa.bak.<ts> if anything is there
      3. shutil.copytree skills, sessions.db, .keyring
      4. yaml.safe_load + rewrite cli-config.yaml (key prefix swap, add personas block)
      5. open SQLite, run STOA schema migrations (adds verdicts + attestations tables)
      6. print MigrationReport summary
    """
    src = detect_hermes_install()
    report = MigrationReport(detected=src is not None, source_dir=src)
    if not src:
        report.notes.append("nothing to migrate — Hermes home not found")
        return report

    target = get_stoa_home()
    report.target_dir = target

    if target.exists() and not force:
        report.notes.append(
            f"STOA home {target} already exists. Re-run with --force to back up + replace."
        )
        return report

    # SCAFFOLD — real migration lands in M5+.
    skills_src = src / "skills"
    if skills_src.is_dir():
        skills_dst = target / "skills"
        skills_dst.mkdir(parents=True, exist_ok=True)
        # Real impl: shutil.copytree(..., dirs_exist_ok=True, ignore=...)
        # For scaffold, just count:
        report.moved_skills = sum(1 for _ in skills_src.rglob("SKILL.md"))

    report.notes.append(
        "scaffold only — no files were actually copied yet. M5 ships the real shutil + yaml rewrite."
    )
    return report


# Stash an unused import so Pyright does not flag shutil while scaffold.
_keep_imports = (shutil,)
