"""Regression coverage for skill integrity pin/check/load
(``agent/skill_utils.py``, v13 HIGH-5 / audit v7 CRIT-30-01).

The integrity gate is the only thing standing between a tampered
``SKILL.md`` and prompt-injection delivery. It needs sharp coverage on:

* Clean pin → load yields the file.
* Tampered skill content after pin → load skips with logged error.
* No pin + strict mode (``STOA_REQUIRE_SKILL_INTEGRITY=1``) → load
  blocks unauthenticated skills.
* No pin + default mode → load yields with one-shot warning.
* ``reset_skill_integrity_cache()`` makes the next manifest read
  reflect a freshly edited manifest.
* ``unpin_skill_integrity()`` removes the entry so the next pin shows
  the skill back in "unmanaged" status.
"""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture()
def fake_skills_home(tmp_path, monkeypatch):
    """Build a STOA_HOME with a single ``my-skill/SKILL.md`` so
    ``iter_skill_index_files`` has something to iterate.

    Reloads ``agent.skill_utils`` so the cache module globals reset.
    """
    home = tmp_path / "stoa_home"
    skills = home / "skills"
    skills.mkdir(parents=True)
    skill_dir = skills / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: t\n---\nbody\n", encoding="utf-8"
    )
    monkeypatch.setenv("STOA_HOME", str(home))

    import agent.skill_utils as su
    reloaded = importlib.reload(su)
    reloaded.reset_skill_integrity_cache()
    reloaded._SKILL_INTEGRITY_WARNED.clear()
    return reloaded, home, skill_dir


def _list_skills(su, skills_dir):
    """Drive ``iter_skill_index_files`` and return a list of paths."""
    return list(su.iter_skill_index_files(skills_dir, "SKILL.md"))


# ── pin → clean check ────────────────────────────────────────────────────


def test_pin_then_clean_check_yields_skill(fake_skills_home):
    """Pin a skill, leave it untouched, and verify the loader yields it."""
    su, home, skill_dir = fake_skills_home
    h = su.pin_skill_integrity(skill_dir)
    assert h.startswith("sha256:")
    results = _list_skills(su, home / "skills")
    assert len(results) == 1
    assert results[0].parent == skill_dir


# ── pin → tamper → blocked ───────────────────────────────────────────────


def test_pin_then_tamper_blocks_load(fake_skills_home):
    """Pin → edit a file in the skill dir → loader must drop it."""
    su, home, skill_dir = fake_skills_home
    su.pin_skill_integrity(skill_dir)
    # Tamper: rewrite SKILL.md after the pin.
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: TAMPERED\n---\nbody\n",
        encoding="utf-8",
    )
    su.reset_skill_integrity_cache()
    results = _list_skills(su, home / "skills")
    assert results == [], "tampered skill MUST be dropped from the loader"


# ── no pin + strict mode → blocked ───────────────────────────────────────


def test_unpinned_strict_mode_blocks(fake_skills_home, monkeypatch):
    """With ``STOA_REQUIRE_SKILL_INTEGRITY=1`` and no manifest entry, the
    loader must refuse to yield the skill."""
    su, home, skill_dir = fake_skills_home
    monkeypatch.setenv("STOA_REQUIRE_SKILL_INTEGRITY", "1")
    # Note: no pin_skill_integrity call → manifest empty.
    results = _list_skills(su, home / "skills")
    assert results == [], "strict mode must block unpinned skills"


# ── no pin + default mode → yielded with warning ─────────────────────────


def test_unpinned_default_mode_yields_with_warning(fake_skills_home, caplog):
    """Without strict mode, unpinned skills are still yielded but a
    one-shot warning is logged."""
    su, home, skill_dir = fake_skills_home
    # Ensure clean warning state.
    su._SKILL_INTEGRITY_WARNED.clear()
    with caplog.at_level("INFO", logger="agent.skill_utils"):
        results = _list_skills(su, home / "skills")
    assert len(results) == 1, "default mode must yield unpinned skills"
    # The warning channel records the skill name once.
    assert any(
        "no manifest entry" in rec.getMessage() for rec in caplog.records
    ), "unpinned skill should produce a 'no manifest entry' log line"


# ── reset_cache reflects manifest change ─────────────────────────────────


def test_reset_cache_reflects_fresh_manifest(fake_skills_home):
    """Manually editing the manifest on disk while the cache holds an
    older view must NOT be visible until ``reset_skill_integrity_cache``
    is called — which is exactly the contract skills_hub relies on."""
    su, home, skill_dir = fake_skills_home
    # Initial load with an empty manifest.
    _ = su._load_skill_integrity_manifest()
    # Bypass pin_skill_integrity (which resets the cache itself) and
    # write the manifest directly so we can observe the cache lag.
    manifest_path = su._skill_integrity_manifest_path()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({str(skill_dir.resolve()): {"hash": "sha256:abc", "pinned_at_ms": 1}}),
        encoding="utf-8",
    )
    # Cache still holds the empty dict — fresh disk edit invisible.
    assert su._load_skill_integrity_manifest() == {}
    # Drop the cache; next read picks up the disk change.
    su.reset_skill_integrity_cache()
    manifest = su._load_skill_integrity_manifest()
    assert str(skill_dir.resolve()) in manifest
    assert manifest[str(skill_dir.resolve())]["hash"] == "sha256:abc"


# ── unpin removes entry ──────────────────────────────────────────────────


def test_unpin_removes_entry(fake_skills_home):
    """Pin a skill, then unpin it; the manifest should no longer mention
    the skill dir."""
    su, home, skill_dir = fake_skills_home
    su.pin_skill_integrity(skill_dir)
    manifest_after_pin = su._load_skill_integrity_manifest()
    assert str(skill_dir.resolve()) in manifest_after_pin

    removed = su.unpin_skill_integrity(skill_dir)
    assert removed is True
    # Cache reset is done inside unpin → fresh read.
    manifest_after_unpin = su._load_skill_integrity_manifest()
    assert str(skill_dir.resolve()) not in manifest_after_unpin

    # Idempotent: unpin again returns False (nothing to remove).
    assert su.unpin_skill_integrity(skill_dir) is False
