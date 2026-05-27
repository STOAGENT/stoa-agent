"""
STOA wallet + token gating.

Council mode and on-chain attestation are gated by holding STOA on Monad
mainnet. This module owns:

  - wallet binding flow (``stoa wallet bind 0x...`` with a SIWE-style sig)
  - balance read against the STOA token contract on Monad
  - council-mode gate check (`stoa /council` reads this; throws on insufficient)
  - signer for attestation tx submission (M3 transport calls in here)

This is a M5 SCAFFOLD. The web3 transport (httpx → JSON-RPC), the
``eth_call`` for ``balanceOf(address)``, and the ``eth_sendRawTransaction``
for attestation all land in the next iteration alongside ``eth-account``
or a minimal in-repo ECDSA signer.

The function shape here is fixed so the CLI ``/council`` command and the
``attestation_hook`` can already wire up the gate.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stoa_constants import get_stoa_home

logger = logging.getLogger(__name__)


# V-AGENT-006 — STOA token launch was dropped per the 2026-05-17 product
# decision. No STOA token is deployed on Monad mainnet. The contract
# address default is now empty so the gate auto-disables; council mode
# is free in v0.x. When/if a token launches, set STOA_TOKEN_CONTRACT in
# env + STOA_COUNCIL_MIN_HOLDING_WEI to enable real gating.
STOA_TOKEN_CONTRACT = os.getenv("STOA_TOKEN_CONTRACT", "")
MONAD_RPC = os.getenv("STOA_MONAD_RPC", "https://rpc.monad.xyz")

# Audit v2 P-O fix: chainId selection. Monad mainnet is 143, testnet
# is 10143. SIWE messages MUST bind the chainId the wallet was on when
# the user signed, otherwise a signature minted on testnet can unlock
# a contract on mainnet (or vice versa). STOA is positioned as a
# Monad-mainnet product (see feedback_stoa_chain_invariant.md memory +
# README), so the default is 143. Operators wiring against testnet
# during development set STOA_CHAIN_ID=10143 explicitly.
MONAD_CHAIN_ID = int(os.getenv("STOA_CHAIN_ID", "143"))

# Boot-time crosscheck: drift between the chainId the runtime defaults
# to and the chainId of any deployed contract reference is a class of
# subtle exploit (signature replay across chains). When STOA_CHAIN_ID
# is explicitly set to anything other than 143, log a one-shot warning
# so the operator sees the deviation in their first-run logs.
if MONAD_CHAIN_ID != 143 and os.getenv("STOA_CHAIN_ID") is not None:
    import logging
    logging.getLogger(__name__).warning(
        "STOA_CHAIN_ID=%d active (not Monad mainnet 143). SIWE messages "
        "and on-chain attestations will bind this chainId. Confirm you "
        "intend to operate on a non-mainnet chain.",
        MONAD_CHAIN_ID,
    )

# Audit v7 CRIT-25-1 fix: SIWE Issued At freshness. A signature is only
# valid for SIWE_FRESHNESS_WINDOW_MS milliseconds from its bound_at. Any
# replay outside that window is rejected at verify time.
SIWE_FRESHNESS_WINDOW_MS = int(os.getenv("STOA_SIWE_FRESHNESS_MS", str(10 * 60 * 1000)))  # 10 min

# Minimum STOA holding (in wei-equivalent 18-decimal units) to unlock
# council mode + on-chain attestation. v0.x default is 0 (no gate);
# set in env to enforce. The point when enforced is "skin in the game",
# not "must be whale".
COUNCIL_MIN_HOLDING = int(os.getenv("STOA_COUNCIL_MIN_HOLDING_WEI", "0"))

WALLET_FILE = "wallet.json"


@dataclass
class WalletBinding:
    address: str       # 0x...
    bound_at: int      # unix ts ms
    signature: str     # the SIWE-style signature that proved ownership
    note: str = ""


@dataclass
class GatingDecision:
    allowed: bool
    reason: str               # "ok" | "no_wallet" | "insufficient_balance" | "rpc_unreachable"
    balance_wei: int = 0
    min_required_wei: int = 0
    wallet: str | None = None


def _wallet_path() -> Path:
    return get_stoa_home() / WALLET_FILE


# ──────────────────────────────────────────────────────────────────────────
# Bind
# ──────────────────────────────────────────────────────────────────────────

# V-AGENT-018 fix: SIWE signature recovery + verification.
# Before this patch, `bind_wallet` accepted any (address, signature) pair
# and persisted them without proving custody — so anyone could bind a
# whale's address and pass the council gate without owning the key.
def _canonical_bind_message(address: str, bound_at_ms: int) -> str:
    """The exact UTF-8 string the caller must sign to bind ``address``.

    EIP-4361-style — domain + address + chain + monotonic timestamp so a
    signature captured from one session can't be replayed in another.
    """
    return (
        "stoa-agent wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        "Bind this address to STOA Agent for council mode.\n\n"
        f"URI: https://stoax.xyz\n"
        f"Version: 1\n"
        f"Chain ID: {MONAD_CHAIN_ID}\n"
        f"Issued At: {bound_at_ms}\n"
    )


def _verify_siwe_signature(address: str, signature: str, message: str) -> bool:
    """Recover the signer from ``signature`` over ``message`` and compare
    to ``address``. Returns True iff they match (case-insensitive).

    Uses ``eth-account`` if available; falls back to refusing the bind
    when the dep isn't installed (fail closed — pre-fix behavior was
    silently accepting everything, which is what we're fixing).
    """
    try:
        from eth_account import Account  # type: ignore
        from eth_account.messages import encode_defunct  # type: ignore
    except ImportError:
        logger.error(
            "eth-account not installed; cannot verify SIWE signature. "
            "Install with `pip install eth-account` or set "
            "STOA_GATING_BYPASS=1 if you're intentionally running ungated."
        )
        return False
    try:
        recovered = Account.recover_message(
            encode_defunct(text=message),
            signature=signature,
        )
        return recovered.lower() == address.lower()
    except Exception as e:
        logger.warning("SIWE recovery failed for %s: %s", address, e)
        return False


# Audit v7 CRIT-25-1: SIWE nonce store. Each (address, issued_at_ms)
# pair may only be consumed once. Persisted to ~/.stoa/wallet_nonces.json
# so the protection survives process restart.

def _siwe_nonce_path() -> Path:
    return get_stoa_home() / "wallet_nonces.json"


def _siwe_nonce_load() -> dict[str, list[int]]:
    p = _siwe_nonce_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _siwe_nonce_consumed(address: str, issued_at_ms: int) -> bool:
    store = _siwe_nonce_load()
    return issued_at_ms in store.get(address.lower(), [])


def _siwe_nonce_record(address: str, issued_at_ms: int) -> None:
    """Append a consumed nonce for ``address``. Prunes entries older than
    24h × freshness window to keep the file bounded."""
    store = _siwe_nonce_load()
    addr = address.lower()
    nonces = store.get(addr, [])
    # Prune: anything older than 24h × freshness can never be reused anyway.
    cutoff = int(__import__("time").time() * 1000) - 24 * SIWE_FRESHNESS_WINDOW_MS
    nonces = [n for n in nonces if n >= cutoff]
    nonces.append(issued_at_ms)
    store[addr] = nonces
    p = _siwe_nonce_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2), encoding="utf-8")


def bind_wallet(
    address: str,
    signature: str,
    note: str = "",
    issued_at_ms: int | None = None,
) -> WalletBinding:
    """Persist a wallet binding to ``~/.stoa/wallet.json``.

    V-AGENT-018 fix: the signature is verified before persistence.
    Audit v7 CRIT-25-1 fix: ``Issued At`` freshness check + nonce store.

    The caller must:
      1. Read the canonical bind message via ``_canonical_bind_message``
         using a fresh ``issued_at_ms`` (within ``SIWE_FRESHNESS_WINDOW_MS``)
      2. Sign that exact message with the private key for ``address``
      3. Pass ``signature`` + the same ``issued_at_ms`` here

    If ``issued_at_ms`` is ``None`` we generate one now (CLI fast-path
    where the user hands us a sig produced from "stoa wallet challenge"
    a moment ago). For external integrations always pass the explicit
    timestamp the user signed.

    Raises ``ValueError`` on:
      - malformed address (not 0x + 40 hex chars)
      - ``issued_at_ms`` outside ``[now - window, now + 30s clock skew]``
      - signature that doesn't recover to ``address``
      - eth-account not installed (fail closed)
      - nonce already consumed (replay attempt)
    """
    address = address.lower()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"not a valid EOA: {address}")
    # Lightweight hex check — eth-account would fail later but we want
    # a clean error before crypto.
    try:
        int(address, 16)
    except ValueError:
        raise ValueError(f"address contains non-hex characters: {address}")

    now_ms = int(__import__("time").time() * 1000)
    bound_at = issued_at_ms if issued_at_ms is not None else now_ms

    # Audit v7 CRIT-25-1: freshness window. A signature whose ``Issued At``
    # is older than SIWE_FRESHNESS_WINDOW_MS or claims to come from the
    # future (>30s of allowed clock skew) is rejected. Without this, an
    # attacker who once captured a victim's signature can replay it years
    # later to take over the binding.
    SKEW_MS = 30_000
    if bound_at > now_ms + SKEW_MS:
        raise ValueError(
            f"issued_at_ms {bound_at} is in the future (now={now_ms}); "
            "reject as either clock-skew attack or tampered timestamp."
        )
    if bound_at < now_ms - SIWE_FRESHNESS_WINDOW_MS:
        raise ValueError(
            f"signature is too old: issued_at={bound_at}, now={now_ms}, "
            f"window={SIWE_FRESHNESS_WINDOW_MS}ms. "
            "Run `stoa wallet challenge` to get a fresh message to sign, "
            "then `stoa wallet bind <addr> --signature 0x...` within "
            f"{SIWE_FRESHNESS_WINDOW_MS // 60_000} minutes."
        )

    canonical = _canonical_bind_message(address, bound_at)
    if not _verify_siwe_signature(address, signature, canonical):
        raise ValueError(
            f"signature does not recover to {address}. "
            "Re-sign the canonical bind message with the wallet's private "
            "key and pass the result as `--signature 0x...`. "
            "The exact message to sign is logged at DEBUG level."
        )

    # Audit v7 CRIT-25-1 nonce store: each (address, issued_at_ms) pair
    # may only be consumed once. Prevents the captured-signature replay
    # even within the freshness window.
    if _siwe_nonce_consumed(address, bound_at):
        raise ValueError(
            f"signature replay rejected: this (address, issued_at_ms) "
            f"pair was already used to bind. Run `stoa wallet challenge` "
            "to get a fresh message."
        )
    _siwe_nonce_record(address, bound_at)

    logger.debug("SIWE signature OK for %s (issued_at_ms=%d)", address, bound_at)

    binding = WalletBinding(
        address=address,
        bound_at=bound_at,
        signature=signature,
        note=note,
    )
    p = _wallet_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(binding.__dict__, indent=2), encoding="utf-8")
    logger.info("wallet bound: %s", address)
    return binding


def load_wallet() -> WalletBinding | None:
    """Read + re-verify the persisted wallet binding.

    Audit v4 V-03 fix: re-recover the signer from the persisted signature
    every time ``load_wallet`` is called. Without this, any process that
    can write ``~/.stoa/wallet.json`` (different malicious tool, backup
    restore, leaked dotfile) can hand-craft a whale address + dummy
    signature and pass the council gate without ever owning the key.

    The freshness window from ``SIWE_FRESHNESS_WINDOW_MS`` is intentionally
    NOT enforced here — once a user has bound their wallet, they shouldn't
    have to re-sign every 10 minutes. The window applies only at bind time
    (``bind_wallet``) to stop captured-before-bind replay. Long-lived
    bindings are accepted as long as the signature still recovers to the
    claimed address with the chainId stamped in the message.
    """
    p = _wallet_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        binding = WalletBinding(**data)
    except Exception as e:
        logger.warning("wallet.json unreadable: %s", e)
        return None

    # Re-recover signer to confirm on-disk binding actually proves custody.
    canonical = _canonical_bind_message(binding.address, binding.bound_at)
    if not _verify_siwe_signature(binding.address, binding.signature, canonical):
        logger.warning(
            "wallet.json signature does NOT recover to %s — refusing to load. "
            "Run `stoa wallet bind <addr> --signature 0x...` again. "
            "(Possible causes: someone tampered with ~/.stoa/wallet.json, "
            "STOA_CHAIN_ID changed since bind, or eth-account uninstalled.)",
            binding.address,
        )
        return None

    return binding


# ──────────────────────────────────────────────────────────────────────────
# Balance + gating
# ──────────────────────────────────────────────────────────────────────────

def read_stoa_balance(address: str) -> int | None:
    """Read STOA balance of ``address`` on Monad mainnet.

    M5 SCAFFOLD — real implementation:
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [
                {
                    "to": STOA_TOKEN_CONTRACT,
                    "data": "0x70a08231" + address.lower().replace("0x", "").rjust(64, "0"),
                },
                "latest",
            ],
            "id": 1,
        }
        r = httpx.post(MONAD_RPC, json=payload, timeout=10).json()
        return int(r["result"], 16)

    Returns ``None`` if RPC is unreachable so the caller can degrade
    gracefully (e.g. "council unavailable — try again when online").
    """
    # SCAFFOLD body — real RPC call lands in M5+ when httpx dep is wired.
    logger.debug("read_stoa_balance(%s) — scaffold returns 0", address)
    return 0


def gate_council() -> GatingDecision:
    """Decide whether the caller can invoke council mode + attestation."""
    wallet = load_wallet()
    if wallet is None:
        return GatingDecision(
            allowed=False,
            reason="no_wallet",
            min_required_wei=COUNCIL_MIN_HOLDING,
        )
    balance = read_stoa_balance(wallet.address)
    if balance is None:
        return GatingDecision(
            allowed=False,
            reason="rpc_unreachable",
            wallet=wallet.address,
            min_required_wei=COUNCIL_MIN_HOLDING,
        )
    if balance < COUNCIL_MIN_HOLDING:
        return GatingDecision(
            allowed=False,
            reason="insufficient_balance",
            balance_wei=balance,
            min_required_wei=COUNCIL_MIN_HOLDING,
            wallet=wallet.address,
        )
    return GatingDecision(
        allowed=True,
        reason="ok",
        balance_wei=balance,
        min_required_wei=COUNCIL_MIN_HOLDING,
        wallet=wallet.address,
    )


# ──────────────────────────────────────────────────────────────────────────
# Bypass for free tier + dev
# ──────────────────────────────────────────────────────────────────────────

def gate_council_with_fallback() -> tuple[GatingDecision, bool]:
    """Same as ``gate_council`` but honors ``STOA_GATING_BYPASS=1``.

    V-AGENT-006 — when no STOA token contract is configured (default for
    v0.x post-token-launch-cancellation), the gate auto-disables and the
    decision is always "allowed". Operators who wire a token contract +
    min holding take responsibility for enforcement.

    The explicit bypass via env still exists for:
      - free tier (operator chooses to disable gating for their users)
      - local dev (no wallet, no RPC, but want to test council)
      - emergency unlock if the contract is being migrated
    """
    if not STOA_TOKEN_CONTRACT:
        # No token deployed → council mode is free in v0.x.
        return (
            GatingDecision(allowed=True, reason="gate_disabled"),
            True,
        )
    decision = gate_council()
    bypass = os.getenv("STOA_GATING_BYPASS") == "1"
    return decision, bypass


def explain_decision(d: GatingDecision) -> str:
    """One-line CLI-friendly explanation."""
    if d.allowed:
        return f"council unlocked (wallet {d.wallet}, balance {d.balance_wei / 1e18:.4f} STOA)"
    if d.reason == "no_wallet":
        return "council requires a bound wallet. run: stoa wallet bind 0x..."
    if d.reason == "rpc_unreachable":
        return f"could not reach {MONAD_RPC} to check STOA balance. try again or set STOA_GATING_BYPASS=1"
    if d.reason == "insufficient_balance":
        held = d.balance_wei / 1e18
        need = d.min_required_wei / 1e18
        return f"council requires {need:.2f} STOA; wallet {d.wallet} holds {held:.4f}"
    return f"council gated ({d.reason})"


def _suppress_unused_warnings() -> dict[str, Any]:
    """Keep imported names linked so Pyright does not flag them as unused
    while we are still scaffolding the rest of M5."""
    return {"path": Path}
