---
name: monad-attestation
description: "Use when a council verdict or agent action needs a permanent, third-party-verifiable on-chain witness — stamps a response hash to AuditAttestationV2 on Monad mainnet and returns the tx hash + IPFS evidence bundle."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos, windows]
metadata:
  stoa:
    tags: [on-chain, monad, attestation, audit, verifiable]
    related_skills: [council-verdict, erc8004-reputation]
---

# Monad Attestation

Write a 32-byte response hash to the `AuditAttestationV2` contract on
Monad mainnet so any future verifier can prove a STOA agent ran exactly
the action it claims — without trusting the operator's local log file.

## When to use

- After a council verdict the operator wants to publish as proof.
- After a tool call the caller explicitly flagged with `--attest`.
- During a commissioned audit where the deliverable must be on-chain.

## When NOT to use

- Every single tool call — the chain footprint becomes noise and gas
  adds up. Attestation is opt-in by design.
- Solo `stoa ask` dispatches without `--attest` — the user did not ask
  for permanence.

## Mechanics

1. Resolve the signing wallet from `~/.stoa/wallet.key` (or env override).
2. Pin an IPFS evidence bundle: `(task, agents[], verdict_text)` as JSON.
3. Build calldata for:
   ```solidity
   attest(
       bytes32 responseHash,
       bytes32 sessionHash,
       uint64  timestamp,
       string  persona,
       string  evidenceCID
   )
   ```
4. `eth_sendRawTransaction` via `httpx` to `STOA_MONAD_RPC`
   (default `https://rpc.monad.xyz`).
5. On failure: persist the request to `~/.stoa/attestations.db` (queued)
   and return `{queued: true}` without blocking the agent.
6. A background daemon retries queued attestations on the next successful
   boot.

## Config

```yaml
# ~/.stoa/cli-config.yaml
on_chain:
  monad_rpc: https://rpc.monad.xyz
  attestation_contract: "0xee0fe34c1d9544fa66968b7e4dada14f591fbdd6"
  attestation_enabled: true
  ipfs_pin_endpoint: ""  # optional, leave empty to pin via local node
```

## Output

```
tx_hash         str       — Monad explorer link
block_number   int|null  — null if still queued
response_hash   str       — the input, echoed for caller convenience
contract        str       — AuditAttestationV2 address
ipfs_cid        str       — evidence bundle CID
queued          bool      — true if the RPC was unreachable
```

## Security

- The wallet must hold gas (~0.001 MON per call) and STOA tokens if the
  contract is gated (M4+).
- The hash is **not reversible** — the agent's verdict text never goes
  on-chain in plaintext. The IPFS CID is the bridge.
- Nonce safety: a local nonce counter avoids back-to-back collisions.
