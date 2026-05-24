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


STOA_TOKEN_CONTRACT = os.getenv(
    "STOA_TOKEN_CONTRACT",
    "0xd645C10050551E93e40c4C06aF4b24F790067777",  # STOA on Monad mainnet
)
MONAD_RPC = os.getenv("STOA_MONAD_RPC", "https://rpc.monad.xyz")

# Minimum STOA holding (in wei-equivalent 18-decimal units) to unlock
# council mode + on-chain attestation. Default is a low non-zero floor —
# the point is "skin in the game", not "must be whale".
COUNCIL_MIN_HOLDING = int(os.getenv("STOA_COUNCIL_MIN_HOLDING_WEI", "1000000000000000000"))  # 1 STOA

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

def bind_wallet(address: str, signature: str, note: str = "") -> WalletBinding:
    """Persist a wallet binding to ``~/.stoa/wallet.json``.

    SIWE-style: the caller signed a canonical message with their wallet,
    proving custody. The signature is stored alongside the address so the
    operator can later prove they bound this wallet (useful for audit
    trails). This function does NOT verify the signature — that happens
    in M5+ when ``ecdsa`` recovery lands. For now it persists as
    declared and lets the operator move forward.
    """
    address = address.lower()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"not a valid EOA: {address}")

    binding = WalletBinding(
        address=address,
        bound_at=int(__import__("time").time() * 1000),
        signature=signature,
        note=note,
    )
    p = _wallet_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(binding.__dict__, indent=2), encoding="utf-8")
    logger.info("wallet bound: %s", address)
    return binding


def load_wallet() -> WalletBinding | None:
    p = _wallet_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return WalletBinding(**data)
    except Exception:
        return None


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

    The bypass exists for:
      - free tier (operator chooses to disable gating for their users)
      - local dev (no wallet, no RPC, but want to test council)
      - emergency unlock if the contract is being migrated
    """
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
