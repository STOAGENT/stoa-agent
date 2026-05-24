---
name: monad-mev-watchdog
description: "Use when a user wants passive on-chain monitoring of a specific Monad contract — watches for MEV patterns, oracle manipulation, sudden balance drains, and emits an alert with the council's red-team verdict."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos]
metadata:
  stoa:
    tags: [monitoring, mev, monad, on-chain, watchdog, alerts]
    related_skills: [council-verdict, erc8004-reputation, solidity-audit-pipeline]
---

# Monad MEV Watchdog

Passive monitor that watches a contract on Monad mainnet for MEV patterns,
unexpected balance drains, and oracle manipulation. When a suspicious
event fires, the watchdog calls the council with the on-chain context and
forwards the verdict as an alert.

## When to use

- A user owns or audited a contract and wants ongoing protection.
- A DeFi protocol team wants post-deploy monitoring without paying for
  a full audit subscription.
- An insurance pool needs an automated trigger for payouts.

## What it watches

| Pattern | Detection |
|---------|-----------|
| Sandwich attacks | Same wallet buy → user tx → sell within N blocks |
| Oracle manipulation | Spot price diverges from TWAP by &gt; threshold |
| Balance drain | Single tx removes &gt; X% of TVL |
| Privileged role abuse | `setOwner`, `upgradeTo`, `transferOwnership` from non-multisig EOA |
| Reentrancy in the wild | Repeated calls to same selector in one tx with state delta |
| Flash-loan attack | Borrow → manipulate → repay within one tx |

## Mechanics

1. Subscribe to Monad RPC event stream (logs filter on the watched
   address).
2. For each event, run a lightweight rule engine (no LLM call — keeps
   the watchdog cheap and fast).
3. On a rule hit, fetch tx context (transaction + receipt + 10 prior
   blocks of state) and pass to `council-verdict` skill with the
   prompt: "is this an attack?".
4. Council returns `consensus: attack` → emit alert via
   `~/.stoa/cli-config.yaml: alerts.{telegram,discord,email}`. Council
   returns `consensus: benign` → log and continue. Split → emit a
   "needs human review" alert.

## Output

A streaming alert log + a SQLite event log:

```
ts                 ISO timestamp
contract           watched address
tx_hash            offending tx
rule_hit           the deterministic rule that fired
council_agreement  consensus | split | no_consensus
council_verdict    "attack" | "benign" | "needs_review"
severity           HIGH | MEDIUM | LOW
attestation_hash   on-chain witness for the council verdict
```

## Cost model

The rule engine is free (just RPC subscribe + local logic). LLM calls
only happen when a rule fires; on a healthy contract this is rare. STOA
spend cap (`STOA_SPEND_CAP_USD`) still applies, so a runaway watchdog
cannot drive the LLM bill unbounded.

## Limitations

- Not a substitute for a full audit. Catches **active attacks**, not
  latent bugs.
- Detection depends on the rules; novel attack patterns may slip
  through until a new rule is shipped.
- Monad-only. Cross-chain MEV requires a separate watchdog per chain.
