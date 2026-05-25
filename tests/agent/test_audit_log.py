"""Regression coverage for ``agent/audit_log.py`` (v13 HIGH-5).

The audit-log primitive ships three security-relevant invariants that
had no direct regression test before this file:

* Hash-chained append — every entry carries the SHA-256 of the previous
  line's full JSON, so tampering with any historical line breaks every
  subsequent ``prev_hash``.
* ``verify_chain()`` walks the file and returns ``(False, "line N: ...")``
  on the FIRST mismatched line, so an incident responder can pinpoint
  which entry was edited.
* Rotation at ``_MAX_BYTES`` (16 MiB) → ``tool-calls.jsonl.1`` …
  ``.jsonl.N``, keeping the active file bounded.
* ``args_preview`` is redacted via ``agent.redact.redact_sensitive_text``
  so a tool call that captures an ``OPENAI_API_KEY=sk-...`` argument
  cannot exfiltrate the key to the on-disk audit log.

The tests use the hermetic ``STOA_HOME`` tempdir from the project-wide
conftest, so the audit log lives at ``<tempdir>/audit/tool-calls.jsonl``
and never touches the developer's real ``~/.stoa``.
"""

from __future__ import annotations

import hashlib
import importlib
import json

import pytest


@pytest.fixture()
def audit_module(monkeypatch):
    """Reload ``agent.audit_log`` so the module-level ``_LAST_HASH`` cache
    starts at None for every test (no leak across tests)."""
    import agent.audit_log as al
    reloaded = importlib.reload(al)
    # Defense-in-depth: force the cache to None even if reload no-op'd.
    reloaded._LAST_HASH = None
    return reloaded


def _read_lines(p) -> list[str]:
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── record + verify_chain ────────────────────────────────────────────────


def test_record_then_verify_chain_ok(audit_module):
    """Two records in succession write a 2-line file whose chain verifies."""
    audit_module.record(kind="call", tool="terminal", args_preview="ls -la")
    audit_module.record(kind="approve", tool="terminal", approved=True)
    p = audit_module._log_path()
    assert p.exists()
    lines = _read_lines(p)
    assert len(lines) == 2
    ok, err = audit_module.verify_chain()
    assert ok is True, f"verify_chain reported: {err}"
    assert err is None


def test_chain_genesis_prev_hash_is_all_zero(audit_module):
    """The very first entry's prev_hash MUST be the genesis sentinel
    (64 zeros). Otherwise a chain seeded mid-file could be silently
    swapped out without verify_chain noticing."""
    audit_module.record(kind="call", tool="write_file", args_preview="hi")
    p = audit_module._log_path()
    first = json.loads(_read_lines(p)[0])
    assert first["prev_hash"] == "0" * 64


# ── tamper detection ─────────────────────────────────────────────────────


def test_tamper_middle_entry_breaks_chain(audit_module):
    """Editing any field of a middle entry must cause verify_chain to
    return (False, msg) and the message must name the affected line."""
    audit_module.record(kind="call", tool="terminal", args_preview="step1")
    audit_module.record(kind="call", tool="terminal", args_preview="step2")
    audit_module.record(kind="call", tool="terminal", args_preview="step3")
    p = audit_module._log_path()
    lines = _read_lines(p)
    assert len(lines) == 3

    # Tamper the middle line — change the tool field.
    middle = json.loads(lines[1])
    middle["tool"] = "TAMPERED"
    lines[1] = json.dumps(middle, ensure_ascii=False, sort_keys=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, err = audit_module.verify_chain()
    assert ok is False
    # The mismatch is detected at line 3 (whose prev_hash points at the
    # untampered line 2, but the actual on-disk line 2 hashes differently
    # now). verify_chain reports the first line whose prev_hash check
    # fails.
    assert err is not None
    assert "line 3" in err or "line 2" in err
    assert "mismatch" in err.lower()


# ── rotation ─────────────────────────────────────────────────────────────


def test_rotation_at_max_bytes(audit_module, monkeypatch):
    """When the active log passes ``_MAX_BYTES`` (we shrink it to 1KB
    for the test), the next record() must roll the file to ``.jsonl.1``
    and start a fresh empty primary."""
    # Shrink the rotation threshold so we don't have to write 16 MiB.
    monkeypatch.setattr(audit_module, "_MAX_BYTES", 1024)

    # Fill the primary file past the new threshold.
    for i in range(40):
        audit_module.record(
            kind="call",
            tool="terminal",
            args_preview=f"payload-{i:04d}-" + "x" * 50,
        )

    p = audit_module._log_path()
    rotated = p.with_suffix(".jsonl.1")
    assert rotated.exists(), "rotation should have produced tool-calls.jsonl.1"
    # The rotated file holds the OLD contents; the new primary will be
    # rewritten on the next record() call after the rotation step.
    audit_module.record(kind="call", tool="terminal", args_preview="post-rotation")
    assert p.exists()


# ── redaction ────────────────────────────────────────────────────────────


def test_args_preview_redacts_api_key_assignments(audit_module):
    """An args_preview that looks like ``OPENAI_API_KEY=sk-abc...`` must
    be redacted before reaching disk — even if the operator has opted
    out of global redaction (``force=True`` inside record())."""
    leaky = 'OPENAI_API_KEY=sk-supersecret123456789abcdefghijk'
    audit_module.record(kind="call", tool="terminal", args_preview=leaky)
    p = audit_module._log_path()
    written = p.read_text(encoding="utf-8")
    # The full secret must not survive.
    assert "supersecret123456789abcdefghijk" not in written
    # The env-var name should still be there so audit readers can see
    # WHICH key was attempted; only the value is masked.
    assert "OPENAI_API_KEY" in written


# ── seed from existing file ──────────────────────────────────────────────


def test_chain_seed_from_existing_file_continues_chain(audit_module):
    """When a process restarts and finds an existing log, the next
    record() must seed ``_LAST_HASH`` from the last line so the new
    entry's prev_hash links into the existing chain (verify_chain still
    passes end-to-end)."""
    audit_module.record(kind="call", tool="terminal", args_preview="pre-restart")
    p = audit_module._log_path()
    pre_lines = _read_lines(p)
    assert len(pre_lines) == 1
    expected_last_hash = hashlib.sha256(pre_lines[0].encode("utf-8")).hexdigest()

    # Simulate process restart by dropping the in-memory cache.
    audit_module._LAST_HASH = None

    # Next record must compute prev_hash from the on-disk tail.
    audit_module.record(kind="call", tool="terminal", args_preview="post-restart")
    post_lines = _read_lines(p)
    assert len(post_lines) == 2
    new_entry = json.loads(post_lines[1])
    assert new_entry["prev_hash"] == expected_last_hash

    ok, err = audit_module.verify_chain()
    assert ok is True, f"chain broken after seed: {err}"


# ── reset (cross-test isolation primitive) ───────────────────────────────


def test_in_memory_cache_reset_does_not_corrupt_chain(audit_module):
    """Manually resetting ``_LAST_HASH`` between writes must NOT corrupt
    the chain — the next record() reseeds from disk and the chain stays
    verifiable. (Functions as our equivalent of
    reset_skill_integrity_cache for the audit log.)"""
    audit_module.record(kind="call", tool="terminal", args_preview="a")
    audit_module._LAST_HASH = None  # simulate explicit cache reset
    audit_module.record(kind="call", tool="terminal", args_preview="b")
    audit_module._LAST_HASH = None
    audit_module.record(kind="call", tool="terminal", args_preview="c")

    ok, err = audit_module.verify_chain()
    assert ok is True, f"chain broken under repeated cache reset: {err}"
