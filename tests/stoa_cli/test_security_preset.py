"""Tests for the central security preset selector (audit P-C)."""

from __future__ import annotations

import pytest

from stoa_cli.security_preset import (
    active_preset,
    describe_state,
    is_gate_enabled,
)


class TestActivePreset:
    def test_default_is_normal_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("STOA_SECURITY_PRESET", raising=False)
        assert active_preset() == "normal"

    def test_unknown_value_falls_back_to_normal(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "paranoid")  # not real
        assert active_preset() == "normal"

    def test_off_preset_honored(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "off")
        assert active_preset() == "off"

    def test_strict_preset_honored(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "strict")
        assert active_preset() == "strict"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "NoRmAl")
        assert active_preset() == "normal"

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "  strict  ")
        assert active_preset() == "strict"


class TestIsGateEnabledPresetDefaults:
    """Audit P-C: the new `normal` default flips hardening features ON."""

    def test_normal_default_redact_pii_on(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.delenv("STOA_REDACT_PII", raising=False)
        assert is_gate_enabled("REDACT_PII") is True

    def test_normal_default_redact_ip_on(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.delenv("STOA_REDACT_IP", raising=False)
        assert is_gate_enabled("REDACT_IP") is True

    def test_normal_default_skill_integrity_on(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.delenv("STOA_REQUIRE_SKILL_INTEGRITY", raising=False)
        assert is_gate_enabled("REQUIRE_SKILL_INTEGRITY") is True

    def test_off_preset_keeps_legacy_pii_off(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "off")
        monkeypatch.delenv("STOA_REDACT_PII", raising=False)
        assert is_gate_enabled("REDACT_PII") is False

    def test_strict_preset_flips_download_hash_on(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "strict")
        monkeypatch.delenv("STOA_REQUIRE_DOWNLOAD_HASH", raising=False)
        assert is_gate_enabled("REQUIRE_DOWNLOAD_HASH") is True


class TestExplicitEnvOverridesPreset:
    """Operators retain per-gate overrides regardless of preset."""

    def test_explicit_false_beats_normal_default(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.setenv("STOA_REDACT_PII", "0")
        assert is_gate_enabled("REDACT_PII") is False

    def test_explicit_true_beats_off_default(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "off")
        monkeypatch.setenv("STOA_REDACT_PII", "1")
        assert is_gate_enabled("REDACT_PII") is True

    def test_explicit_yes_is_truthy(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "off")
        monkeypatch.setenv("STOA_REDACT_PII", "yes")
        assert is_gate_enabled("REDACT_PII") is True

    def test_explicit_no_is_falsy(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.setenv("STOA_REDACT_PII", "no")
        assert is_gate_enabled("REDACT_PII") is False

    def test_explicit_empty_falls_through_to_preset(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.setenv("STOA_REDACT_PII", "")
        # Empty value = "not set" — fall through to preset.
        assert is_gate_enabled("REDACT_PII") is True

    def test_unrecognized_value_falls_through(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        monkeypatch.setenv("STOA_REDACT_PII", "maybe")
        # Garbled value = not parseable → fall through to preset.
        assert is_gate_enabled("REDACT_PII") is True


class TestDescribeState:
    def test_snapshot_contains_preset_and_all_gates(self, monkeypatch):
        monkeypatch.setenv("STOA_SECURITY_PRESET", "normal")
        state = describe_state()
        assert state["preset"] == "normal"
        # Every gate name from the off preset's keyset must be present.
        for gate in ("REDACT_PII", "REDACT_IP", "REQUIRE_SKILL_INTEGRITY",
                     "REQUIRE_DOWNLOAD_HASH", "REQUIRE_PINNED_DEPS",
                     "REQUIRE_SIGNED_UPDATES", "ALLOW_PRIVATE_URLS",
                     "NPM_ALLOW_SCRIPTS"):
            assert gate in state
            assert isinstance(state[gate], bool)
