"""Regression coverage for SIWE wallet binding (v13 HIGH-5).

The following audit fixes had no direct regression test before this file:

* chainId binding (CRIT-34-01) — signature scoped to STOA_CHAIN_ID so a
  testnet signature cannot unlock mainnet (and vice versa).
* Issued At freshness window (CRIT-25-1) — signatures older than
  SIWE_FRESHNESS_WINDOW_MS or "from the future" beyond clock-skew are
  rejected at bind time.
* Per-(address, issued_at_ms) nonce store (CRIT-25-1) — a captured
  signature cannot be replayed even within the freshness window.
* load_wallet re-verification (V-03) — a tampered ``wallet.json`` is
  rejected on read, not silently trusted.

The tests use ``eth_account`` to produce real signatures over the exact
canonical bind message (``_canonical_bind_message``), so they exercise
the same recover-and-compare path that runs in production. The hermetic
test fixture pins ``STOA_HOME`` to a tempdir, so the nonce store does
not leak across tests.
"""

from __future__ import annotations

import importlib
import time

import pytest

eth_account = pytest.importorskip("eth_account", reason="eth-account required for SIWE tests")
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_wallet_module(monkeypatch, tmp_path):
    """Reimport ``stoa_cli.wallet`` so module-level env reads pick up the
    test fixture's STOA_CHAIN_ID / STOA_SIWE_FRESHNESS_MS values.

    Also re-points the nonce store path at the per-test STOA_HOME so the
    replay-detection tests start from a clean ``wallet_nonces.json``.
    """
    import stoa_cli.wallet as wallet_mod
    return importlib.reload(wallet_mod)


def _sign(wallet_mod, privkey: str, address: str, issued_at_ms: int) -> str:
    """Sign the canonical bind message for ``address`` at ``issued_at_ms``."""
    msg = wallet_mod._canonical_bind_message(address, issued_at_ms)
    signed = Account.sign_message(encode_defunct(text=msg), private_key=privkey)
    return signed.signature.hex()


def _mk_account():
    """Generate a fresh local ECDSA keypair."""
    acct = Account.create()
    return acct.key.hex(), acct.address  # privkey, checksum address


# ── chainId binding ──────────────────────────────────────────────────────


def test_chain_id_default_is_10143(fresh_wallet_module):
    """Without STOA_CHAIN_ID set, the module pins chainId = 10143 (Monad
    testnet) per audit recommendation."""
    assert fresh_wallet_module.MONAD_CHAIN_ID == 10143


def test_chain_id_env_override_picks_up_mainnet(monkeypatch):
    """Setting STOA_CHAIN_ID=143 (Monad mainnet) before module load is
    honored — the canonical bind message embeds the env-driven value."""
    monkeypatch.setenv("STOA_CHAIN_ID", "143")
    import stoa_cli.wallet as wallet_mod
    reloaded = importlib.reload(wallet_mod)
    try:
        assert reloaded.MONAD_CHAIN_ID == 143
        msg = reloaded._canonical_bind_message("0x" + "a" * 40, 1)
        assert "Chain ID: 143\n" in msg
    finally:
        # Restore module-level state for the rest of the session.
        monkeypatch.delenv("STOA_CHAIN_ID", raising=False)
        importlib.reload(reloaded)


# ── Issued At freshness ──────────────────────────────────────────────────


def test_future_issued_at_beyond_skew_rejected(fresh_wallet_module):
    """A signature claiming Issued At more than 30s in the future is
    rejected as either clock-skew attack or tampered timestamp."""
    priv, addr = _mk_account()
    addr_l = addr.lower()
    now_ms = int(time.time() * 1000)
    future_ms = now_ms + 5 * 60 * 1000  # 5 minutes ahead
    sig = _sign(fresh_wallet_module, priv, addr_l, future_ms)
    with pytest.raises(ValueError, match="future"):
        fresh_wallet_module.bind_wallet(addr_l, sig, issued_at_ms=future_ms)


def test_old_issued_at_outside_window_rejected(fresh_wallet_module):
    """A signature whose Issued At is older than SIWE_FRESHNESS_WINDOW_MS
    is rejected, even if it cryptographically recovers."""
    priv, addr = _mk_account()
    addr_l = addr.lower()
    window = fresh_wallet_module.SIWE_FRESHNESS_WINDOW_MS
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - window - 60_000  # 1 min past the window
    sig = _sign(fresh_wallet_module, priv, addr_l, old_ms)
    with pytest.raises(ValueError, match="too old"):
        fresh_wallet_module.bind_wallet(addr_l, sig, issued_at_ms=old_ms)


# ── crypto validity ──────────────────────────────────────────────────────


def test_valid_signature_accepted(fresh_wallet_module):
    """A signature produced by the wallet's own private key over the
    canonical bind message is accepted and persisted."""
    priv, addr = _mk_account()
    addr_l = addr.lower()
    now_ms = int(time.time() * 1000)
    sig = _sign(fresh_wallet_module, priv, addr_l, now_ms)
    binding = fresh_wallet_module.bind_wallet(addr_l, sig, issued_at_ms=now_ms)
    assert binding.address == addr_l
    assert binding.bound_at == now_ms
    assert binding.signature == sig


def test_invalid_signature_rejected(fresh_wallet_module):
    """A signature that does NOT recover to the claimed address is
    rejected with a clear ``signature does not recover`` error."""
    priv_a, _ = _mk_account()
    _, addr_b = _mk_account()  # different account
    addr_b_l = addr_b.lower()
    now_ms = int(time.time() * 1000)
    # Sign for addr_b but with priv_a's key — recovers to A, not B.
    sig = _sign(fresh_wallet_module, priv_a, addr_b_l, now_ms)
    with pytest.raises(ValueError, match="signature does not recover"):
        fresh_wallet_module.bind_wallet(addr_b_l, sig, issued_at_ms=now_ms)


# ── replay protection ────────────────────────────────────────────────────


def test_replay_same_nonce_rejected(fresh_wallet_module):
    """Consuming the same (address, issued_at_ms) pair twice must be
    rejected on the second call — the nonce store remembers across calls."""
    priv, addr = _mk_account()
    addr_l = addr.lower()
    now_ms = int(time.time() * 1000)
    sig = _sign(fresh_wallet_module, priv, addr_l, now_ms)
    # First bind: succeeds and records the nonce.
    fresh_wallet_module.bind_wallet(addr_l, sig, issued_at_ms=now_ms)
    # Second bind with same (address, issued_at_ms): replay → reject.
    with pytest.raises(ValueError, match="replay rejected"):
        fresh_wallet_module.bind_wallet(addr_l, sig, issued_at_ms=now_ms)


# ── load_wallet re-verification ──────────────────────────────────────────


def test_load_wallet_rejects_tampered_signature(fresh_wallet_module):
    """If ``~/.stoa/wallet.json`` is tampered post-bind (e.g. someone
    rewrites the signature byte-for-byte), ``load_wallet`` must reject
    it on read instead of silently returning a poisoned binding."""
    priv, addr = _mk_account()
    addr_l = addr.lower()
    now_ms = int(time.time() * 1000)
    sig = _sign(fresh_wallet_module, priv, addr_l, now_ms)
    fresh_wallet_module.bind_wallet(addr_l, sig, issued_at_ms=now_ms)
    # Sanity check: a clean load round-trips.
    loaded = fresh_wallet_module.load_wallet()
    assert loaded is not None
    assert loaded.address == addr_l

    # Tamper: flip a byte in the persisted signature. The recover step
    # in load_wallet must reject the file.
    import json
    p = fresh_wallet_module._wallet_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    # Replace last hex char with a different one to keep length intact.
    bad_sig = data["signature"]
    bad_sig = bad_sig[:-1] + ("0" if bad_sig[-1] != "0" else "1")
    data["signature"] = bad_sig
    p.write_text(json.dumps(data), encoding="utf-8")

    tampered = fresh_wallet_module.load_wallet()
    assert tampered is None, "tampered wallet.json must NOT load"
