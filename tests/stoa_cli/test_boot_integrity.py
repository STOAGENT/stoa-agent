"""Tests for the boot-integrity hash (audit Lens 46 / M-11c).

The module's design contract is "never crash startup, write one line per
invocation, opt out via env var." These tests guard each of those.
"""

from __future__ import annotations

import os
from pathlib import Path

from stoa_cli.boot_integrity import (
    compute_boot_digest,
    record_boot,
)


def test_compute_boot_digest_returns_hex_string() -> None:
    digest = compute_boot_digest()
    assert isinstance(digest, str)
    assert len(digest) == 64
    int(digest, 16)  # raises if not valid hex


def test_compute_boot_digest_is_stable_across_calls() -> None:
    a = compute_boot_digest()
    b = compute_boot_digest()
    assert a == b


def test_compute_boot_digest_changes_on_missing_file(tmp_path: Path) -> None:
    """An empty root (no hashable files at all) must still produce a
    valid 64-char hex digest — the absence of every file is folded in
    as the MISSING sentinel."""
    digest = compute_boot_digest(root=tmp_path)
    assert len(digest) == 64
    # Distinct from the real-root digest (which has actual file content)
    assert digest != compute_boot_digest()


def test_record_boot_writes_one_line(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STOA_HOME", str(tmp_path))
    monkeypatch.delenv("STOA_BOOT_INTEGRITY", raising=False)

    digest = record_boot()
    assert digest is not None and len(digest) == 64

    log_path = tmp_path / "boot-integrity.log"
    assert log_path.exists(), "boot-integrity.log must be created under STOA_HOME"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    # Format: "<iso-ts> <digest> v<version> <hostname>"
    parts = lines[0].split(" ")
    assert len(parts) >= 4
    assert parts[1] == digest, "digest in log line must match returned digest"
    assert parts[2].startswith("v"), "version field must be v-prefixed"


def test_record_boot_appends_on_repeated_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STOA_HOME", str(tmp_path))
    monkeypatch.delenv("STOA_BOOT_INTEGRITY", raising=False)

    record_boot()
    record_boot()
    record_boot()

    lines = (tmp_path / "boot-integrity.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # Same install → same digest in every line.
    digests = {ln.split(" ")[1] for ln in lines}
    assert len(digests) == 1


def test_record_boot_opt_out_via_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STOA_HOME", str(tmp_path))
    monkeypatch.setenv("STOA_BOOT_INTEGRITY", "0")

    result = record_boot()
    assert result is None, "opt-out must return None"
    assert not (tmp_path / "boot-integrity.log").exists(), (
        "opt-out must skip the log write entirely"
    )


def test_record_boot_force_overrides_opt_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STOA_HOME", str(tmp_path))
    monkeypatch.setenv("STOA_BOOT_INTEGRITY", "0")

    digest = record_boot(force=True)
    assert digest is not None
    assert (tmp_path / "boot-integrity.log").exists()


def test_record_boot_swallows_unreadable_target(monkeypatch) -> None:
    """If the log dir is genuinely unwritable, record_boot must still
    return the digest (best-effort) and never raise."""
    monkeypatch.setenv("STOA_HOME", os.path.join(os.devnull, "definitely-not-a-dir"))
    # On Windows this path is unwritable; on POSIX likewise. Must not raise.
    try:
        digest = record_boot()
    except Exception as exc:
        raise AssertionError(f"record_boot raised on unwritable target: {exc}")
    # Digest may be None (skipped) or hex (computed but not written).
    if digest is not None:
        assert len(digest) == 64
