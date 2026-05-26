"""Tests for the per-provider egress trace (audit Lens 46 / M-11c)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.egress_trace import (
    get_log_path,
    read_recent,
    record_egress,
)


@pytest.fixture
def _stoa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STOA_HOME", str(tmp_path))
    monkeypatch.delenv("STOA_EGRESS_TRACE", raising=False)
    return tmp_path


# ── basic recording ─────────────────────────────────────────────────


def test_record_returns_dict_on_success(_stoa_home: Path):
    rec = record_egress(
        provider="anthropic",
        url="https://api.anthropic.com/v1/messages",
        model="claude-opus-4-7",
        method="POST",
        status=200,
        latency_ms=812,
        tokens_in=450,
        tokens_out=89,
    )
    assert rec is not None
    assert rec["provider"] == "anthropic"
    assert rec["endpoint"] == "https://api.anthropic.com"
    assert rec["host"] == "api.anthropic.com"
    assert rec["model"] == "claude-opus-4-7"
    assert rec["status"] == 200
    assert rec["tokens_in"] == 450


def test_log_file_is_jsonl_under_stoa_home(_stoa_home: Path):
    record_egress("openai", "https://api.openai.com/v1/chat/completions", status=200)
    log_path = get_log_path()
    assert log_path == _stoa_home / "egress" / "per-provider.jsonl"
    assert log_path.is_file()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["host"] == "api.openai.com"


def test_record_appends_one_line_per_call(_stoa_home: Path):
    record_egress("provider_a", "https://a.example/v1", status=200)
    record_egress("provider_b", "https://b.example/v1", status=200)
    record_egress("provider_c", "https://c.example/v1", status=200)
    lines = (get_log_path()).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    providers = [json.loads(ln)["provider"] for ln in lines]
    assert providers == ["provider_a", "provider_b", "provider_c"]


# ── URL reduction is path/query-safe ────────────────────────────────


def test_path_and_query_are_dropped_from_endpoint(_stoa_home: Path):
    """A URL accidentally carrying a prompt or API key in its path or
    query string must NOT have those bits land in the log."""
    rec = record_egress(
        provider="openrouter",
        url="https://openrouter.ai/api/v1/chat?key=sk-leaked-1234&prompt=secret",
    )
    assert rec is not None
    # endpoint must NOT contain ?, /api, key=, or prompt=
    assert rec["endpoint"] == "https://openrouter.ai"
    assert "?" not in rec["endpoint"]
    assert "key=" not in rec["endpoint"]
    assert "prompt=" not in rec["endpoint"]
    # host similarly clean
    assert rec["host"] == "openrouter.ai"


def test_garbage_url_records_provider_without_endpoint(_stoa_home: Path):
    """A junk URL must NOT crash the recorder; provider still gets logged."""
    rec = record_egress(provider="weird", url="not a url")
    assert rec is not None
    assert rec["provider"] == "weird"
    assert "endpoint" not in rec
    assert "host" not in rec


def test_url_with_port_preserves_port(_stoa_home: Path):
    rec = record_egress(provider="local", url="http://127.0.0.1:11434/api/generate")
    assert rec is not None
    assert rec["endpoint"] == "http://127.0.0.1:11434"
    assert rec["host"] == "127.0.0.1"


# ── opt-out + force ────────────────────────────────────────────────


def test_record_returns_none_when_env_opt_out(_stoa_home: Path, monkeypatch):
    monkeypatch.setenv("STOA_EGRESS_TRACE", "0")
    rec = record_egress(provider="anthropic", url="https://api.anthropic.com")
    assert rec is None
    assert not (_stoa_home / "egress" / "per-provider.jsonl").exists()


def test_record_opt_out_when_env_is_explicitly_zero(_stoa_home: Path, monkeypatch):
    monkeypatch.setenv("STOA_EGRESS_TRACE", "0")
    assert record_egress("foo", "https://x") is None
    # Other values are treated as "on" (default behaviour) — no false negative
    monkeypatch.setenv("STOA_EGRESS_TRACE", "1")
    assert record_egress("foo", "https://x") is not None
    monkeypatch.setenv("STOA_EGRESS_TRACE", "true")
    assert record_egress("foo", "https://x") is not None


# ── safety: input scrubbing ────────────────────────────────────────


def test_negative_status_is_dropped(_stoa_home: Path):
    rec = record_egress(provider="x", url="https://a", status=-1)
    assert rec is not None
    assert "status" not in rec


def test_oversized_model_string_is_truncated(_stoa_home: Path):
    very_long = "x" * 500
    rec = record_egress(provider="x", url="https://a", model=very_long)
    assert rec is not None
    assert len(rec["model"]) <= 128
    assert rec["model"].endswith("…")


def test_blank_provider_falls_back_to_unknown(_stoa_home: Path):
    rec = record_egress(provider="", url="https://a")
    assert rec is not None
    assert rec["provider"] == "unknown"


def test_non_int_token_count_is_dropped(_stoa_home: Path):
    rec = record_egress(provider="x", url="https://a",
                        tokens_in="not_a_number", tokens_out=None)  # type: ignore[arg-type]
    assert rec is not None
    assert "tokens_in" not in rec
    assert "tokens_out" not in rec


# ── never raises ────────────────────────────────────────────────────


def test_record_never_raises_on_unwritable_target(monkeypatch):
    """Recorder MUST swallow IO errors and return None (or the record),
    never raise — a tampered ~/.stoa MUST NOT prevent the wrapped LLM
    call from going through."""
    monkeypatch.setenv(
        "STOA_HOME", os.path.join(os.devnull, "definitely-not-a-real-dir")
    )
    try:
        rec = record_egress(provider="x", url="https://a")
    except Exception as exc:
        raise AssertionError(f"record_egress raised: {exc}")
    # On a broken path the recorder either skipped silently or returned
    # the in-memory record without writing. Both are valid; neither
    # raised.
    assert rec is None or isinstance(rec, dict)


# ── reader: tail behaviour ─────────────────────────────────────────


def test_read_recent_returns_empty_when_no_log(_stoa_home: Path):
    assert read_recent(50) == []


def test_read_recent_returns_chronological(_stoa_home: Path):
    record_egress("first", "https://1")
    record_egress("second", "https://2")
    record_egress("third", "https://3")
    rows = read_recent(50)
    assert [r["provider"] for r in rows] == ["first", "second", "third"]


def test_read_recent_respects_limit(_stoa_home: Path):
    for i in range(10):
        record_egress(f"p{i}", f"https://example.com/{i}")
    rows = read_recent(3)
    assert len(rows) == 3
    assert [r["provider"] for r in rows] == ["p7", "p8", "p9"]


def test_read_recent_skips_malformed_lines(_stoa_home: Path):
    record_egress("ok1", "https://x")
    # Inject a malformed line into the log.
    log = get_log_path()
    with open(log, "a", encoding="utf-8") as f:
        f.write("not valid json\n")
    record_egress("ok2", "https://y")
    rows = read_recent(10)
    # Both valid records survive; the junk line is silently skipped.
    providers = [r["provider"] for r in rows]
    assert "ok1" in providers
    assert "ok2" in providers
    assert len(rows) == 2


def test_read_recent_handles_zero_limit(_stoa_home: Path):
    record_egress("x", "https://x")
    assert read_recent(0) == []


# ── no prompt content reaches the recorder by construction ─────────


def test_recorder_signature_does_not_accept_body(_stoa_home: Path):
    """Compile-time guarantee: the public signature has no parameter
    that would let a caller smuggle prompt bytes into the log. If you
    add such a parameter in the future you MUST also update this test
    (and reconsider the threat model)."""
    import inspect

    sig = inspect.signature(record_egress)
    banned = {
        "body", "request_body", "response_body",
        "prompt", "completion", "messages",
        "api_key", "auth", "authorization",
        "headers",
    }
    bad = banned & set(sig.parameters.keys())
    assert not bad, (
        f"egress_trace.record_egress grew unsafe param(s) {bad} — "
        "the recorder MUST NOT accept prompt/response/auth fields"
    )
