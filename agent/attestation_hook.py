"""
STOA on-chain attestation hook — opt-in writer to the AuditAttestationV2
contract on Monad mainnet.

A council verdict's ``response_hash`` (32 bytes) can be stamped on-chain
so that months later anyone can prove the STOA agent ran exactly the
action it claims — without trusting the operator's local log file.

Design constraints
------------------

1.  **Opt-in, not on by default.** Solo dispatches and verbose tools (every
    file read, every shell tick) never write — the on-chain footprint
    would be noise. Only ``--attest`` flagged calls and council verdicts
    with explicit consent reach the chain.

2.  **Non-blocking on failure.** If the RPC is down or the wallet has no
    gas, the attestation queue persists the request to local SQLite
    (``attestations`` table) and retries on the next successful boot.
    The agent never blocks on the chain.

3.  **Nonce-safe.** The local signer reads its current nonce from the RPC
    once at boot, then increments locally so back-to-back attestations
    don't collide. A periodic resync handles wallet collisions from other
    sources.

4.  **Stateless contract call.** AuditAttestationV2 takes:
        attest(bytes32 responseHash, bytes32 sessionHash, uint64 timestamp, string memory persona)
    and emits an Attestation event. No reads, no state checks — keeps gas
    bounded and the call idempotent under retries.

This module is a SCAFFOLD. The actual web3 transport + signer lives in
``stoa_cli/wallet.py`` (to be written in M3); this file describes the
public contract: ``attest_response_hash(verdict)``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Config — read from env / cli-config.yaml at boot
# ──────────────────────────────────────────────────────────────────────────

MONAD_MAINNET_RPC = os.getenv("STOA_MONAD_RPC", "https://rpc.monad.xyz")
ATTESTATION_CONTRACT = os.getenv(
    "STOA_ATTESTATION_CONTRACT",
    "0xee0fe34c1d9544fa66968b7e4dada14f591fbdd6",  # AuditAttestationV2 on Monad mainnet
)
ATTESTATION_ENABLED = os.getenv("STOA_ATTESTATION_ENABLED", "0") == "1"


@dataclass
class AttestationReceipt:
    """Returned by ``attest_response_hash`` on success."""

    tx_hash: str
    block_number: int | None
    response_hash: str
    contract: str
    rpc: str
    queued: bool  # True if the tx was queued offline (no RPC), False if confirmed


@dataclass
class AttestationFailure:
    """Returned on a non-fatal failure. Always carries a reason so the
    caller can surface it cleanly without a stack trace."""

    response_hash: str
    reason: str  # rpc_unreachable | insufficient_gas | revert | unauthorized | other
    queued: bool  # True if persisted to local retry queue


async def attest_response_hash(
    verdict: Any,  # CouncilVerdict from verdict_composer; Any avoids import cycle
    *,
    persona: str = "council",
    session_hash: bytes | None = None,
) -> AttestationReceipt | AttestationFailure:
    """Stamp ``verdict.response_hash`` on AuditAttestationV2.

    Returns a receipt with the tx hash on success, or a failure record on
    any expected error (RPC down, gas low, revert). Never raises.

    M3 IMPLEMENTATION:
      - Resolve signer wallet from ``~/.stoa/wallet.key`` (or env override).
      - Build ``attest(bytes32, bytes32, uint64, string)`` calldata.
      - eth_sendRawTransaction via httpx to STOA_MONAD_RPC.
      - On failure, append to ``attestations`` SQLite queue and return queued=True.
      - Emit a structured log line so the operator's gateway dashboard sees
        every attestation that hits the chain.

    For M2 this is a SCAFFOLD — the function shape is fixed so the verdict
    composer and the CLI ``/attest`` command can already wire it up.
    """
    if not ATTESTATION_ENABLED:
        logger.debug("attestation disabled (STOA_ATTESTATION_ENABLED=0); skipping")
        return AttestationFailure(
            response_hash=verdict.response_hash,
            reason="attestation_disabled",
            queued=False,
        )

    # SCAFFOLD body — real implementation lands in M3 alongside ``wallet.py``.
    logger.info(
        "would attest hash=%s on contract=%s rpc=%s persona=%s",
        verdict.response_hash[:16] + "…",
        ATTESTATION_CONTRACT,
        MONAD_MAINNET_RPC,
        persona,
    )
    return AttestationFailure(
        response_hash=verdict.response_hash,
        reason="not_yet_implemented",
        queued=False,
    )


# ──────────────────────────────────────────────────────────────────────────
# Local SQLite mirror — every verdict, attested or not, stays in
# ``~/.stoa/attestations.db`` so the operator can replay queued ones after
# an RPC outage. Schema lives in stoa_state.py.
# ──────────────────────────────────────────────────────────────────────────

ATTESTATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS attestations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    response_hash   TEXT NOT NULL,
    persona         TEXT NOT NULL,
    session_id      TEXT,
    contract_addr   TEXT NOT NULL,
    rpc_url         TEXT NOT NULL,
    tx_hash         TEXT,
    block_number    INTEGER,
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued | confirmed | failed
    failure_reason  TEXT,
    queued_at       INTEGER NOT NULL,
    confirmed_at    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_attestations_status_queued_at
    ON attestations(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_attestations_response_hash
    ON attestations(response_hash);
"""
