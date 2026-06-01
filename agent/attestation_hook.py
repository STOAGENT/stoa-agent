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

    # Residual-closure 2026-06-01 (F-C13): REAL on-chain transport. Honest by
    # construction — if no signing key is configured we return a clear failure,
    # never a fake success. Real attestation requires an explicitly configured
    # key (STOA_ATTEST_KEY env or ~/.stoa/wallet.key); when present we build,
    # sign and broadcast the attest() tx via eth_sendRawTransaction.
    signer_key = _load_attest_signer_key()
    if not signer_key:
        logger.info(
            "attestation: no signer key configured (set STOA_ATTEST_KEY or "
            "~/.stoa/wallet.key) — not stamping on-chain"
        )
        return AttestationFailure(
            response_hash=verdict.response_hash,
            reason="no_signer_key",
            queued=False,
        )

    try:
        import time as _time

        import httpx
        from eth_abi import encode as _abi_encode
        from eth_account import Account
        from eth_utils import keccak, to_checksum_address, to_hex

        acct = Account.from_key(signer_key)
        response_b32 = _to_bytes32(verdict.response_hash)
        session_b32 = (
            bytes(session_hash)
            if isinstance(session_hash, (bytes, bytearray)) and len(session_hash) == 32
            else b"\x00" * 32
        )
        ts = int(_time.time())
        # attest(bytes32 responseHash, bytes32 sessionHash, uint64 timestamp, string persona)
        selector = keccak(text="attest(bytes32,bytes32,uint64,string)")[:4]
        calldata = to_hex(
            selector
            + _abi_encode(
                ["bytes32", "bytes32", "uint64", "string"],
                [response_b32, session_b32, ts, persona],
            )
        )
        contract = to_checksum_address(ATTESTATION_CONTRACT)

        async def _rpc(method: str, params: list):
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    MONAD_MAINNET_RPC,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                data = resp.json()
                if data.get("error"):
                    raise RuntimeError(f"{method}: {data['error']}")
                return data["result"]

        chain_id = int(await _rpc("eth_chainId", []), 16)
        nonce = int(await _rpc("eth_getTransactionCount", [acct.address, "pending"]), 16)
        gas_price = int(await _rpc("eth_gasPrice", []), 16)
        tx = {
            "to": contract,
            "value": 0,
            "data": calldata,
            "nonce": nonce,
            "chainId": chain_id,
            "maxFeePerGas": gas_price * 2,
            "maxPriorityFeePerGas": gas_price,
            "gas": 250_000,
        }
        try:
            est = int(
                await _rpc("eth_estimateGas", [{"from": acct.address, "to": contract, "data": calldata}]),
                16,
            )
            tx["gas"] = int(est * 1.25)
        except Exception:
            pass  # keep the conservative default gas

        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            raw = getattr(signed, "rawTransaction")  # older eth-account
        tx_hash = await _eth_send_raw_transaction(_rpc, to_hex(raw))
        logger.info(
            "attested response_hash=%s on %s tx=%s",
            verdict.response_hash[:16] + "…", contract, tx_hash,
        )
        return AttestationReceipt(
            tx_hash=tx_hash,
            block_number=None,
            response_hash=verdict.response_hash,
            contract=contract,
            rpc=MONAD_MAINNET_RPC,
            queued=False,
        )
    except Exception as exc:  # noqa: BLE001 — never raise; queue + report
        logger.warning("attestation: on-chain send failed (%s); queuing for retry", exc)
        _queue_attestation_failure(verdict.response_hash, persona, str(exc))
        return AttestationFailure(
            response_hash=verdict.response_hash,
            reason="rpc_or_sign_error",
            queued=True,
        )


async def _eth_send_raw_transaction(rpc_post: Any, raw_tx_hex: str) -> str:
    """Broadcast a signed transaction via the eth_sendRawTransaction JSON-RPC
    method and return the resulting tx hash. The transport itself — this is
    where the attestation actually hits Monad."""
    return await rpc_post("eth_sendRawTransaction", [raw_tx_hex])


def _to_bytes32(h: Any) -> bytes:
    """Coerce a response/session hash (hex str with/without 0x) to 32 bytes."""
    s = h[2:] if isinstance(h, str) and h.startswith("0x") else (h or "")
    try:
        b = bytes.fromhex(s) if isinstance(s, str) else bytes(s)
    except (ValueError, TypeError):
        from eth_utils import keccak
        b = keccak(text=str(h))  # last-resort: deterministic 32-byte digest
    return b.rjust(32, b"\x00") if len(b) < 32 else b[:32]


def _load_attest_signer_key() -> str | None:
    """Resolve the attestation signer key. Returns None if none configured
    (the honest 'no on-chain stamp' path — never fabricates a key)."""
    key = os.getenv("STOA_ATTEST_KEY", "").strip()
    if key:
        return key
    try:
        from stoa_constants import get_stoa_home
        kp = get_stoa_home() / "wallet.key"
        if kp.is_file():
            return (kp.read_text(encoding="utf-8").strip() or None)
    except Exception:
        pass
    return None


def _queue_attestation_failure(response_hash: str, persona: str, reason: str = "") -> None:
    """Best-effort persist a failed attestation to the local retry queue."""
    try:
        import sqlite3
        import time as _time

        from stoa_constants import get_stoa_home

        db = get_stoa_home() / "attestations.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.executescript(ATTESTATIONS_TABLE_SQL)
            conn.execute(
                "INSERT INTO attestations "
                "(response_hash, persona, contract_addr, rpc_url, status, failure_reason, queued_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (response_hash, persona, ATTESTATION_CONTRACT, MONAD_MAINNET_RPC,
                 reason[:200], int(_time.time())),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("attestation queue write failed: %s", exc)


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
