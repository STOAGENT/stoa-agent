---
name: solana-anchor-audit
description: "Use when the user submits a Solana Anchor program for audit — runs anchor-lang invariant tests, signer-check linter, account-discriminator review, and routes findings through the council with Drax + Veritas weighted for the verdict."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos]
metadata:
  stoa:
    tags: [audit, solana, anchor, security, sealevel]
    related_skills: [council-verdict, solidity-audit-pipeline]
---

# Solana Anchor Audit

The STOA audit flow for Anchor programs on Solana. Mirrors the Solidity
pipeline but adapted to Sealevel runtime quirks.

## When to use

- User submits a path to an Anchor workspace (`Anchor.toml` present).
- User says "audit my Solana program", "review this Anchor account".
- User pastes Rust code with `#[derive(Accounts)]` or `#[program]`.

## When NOT to use

- Generic Rust without Anchor — different skill (rust-audit, not yet
  shipped).
- Native Solana programs (no Anchor) — runs the lower-level skill
  `solana-native-audit` instead (also not yet shipped).
- Solana SDK call from a client — that is a client-side review, not a
  program audit.

## Pipeline

1. **Setup**: detect Anchor version, install matching CLI in sandbox.
2. **Build**: `anchor build` — catches compile errors and lint warnings.
3. **Test**: `anchor test --skip-deploy` — verifies declared
   invariants.
4. **Account discriminator check**: every account must derive a
   discriminator (Anchor adds this by default but custom layouts can
   bypass it).
5. **Signer-check linter**: scan all `#[derive(Accounts)]` structs for
   missing `signer` constraints on accounts that are mutated.
6. **PDA seed audit**: verify all `Pubkey::find_program_address`
   invocations and `seeds = [...]` declarations match between client
   and program.
7. **CPI authority check**: every cross-program invocation must declare
   its signer seeds explicitly; bare `Cpi` calls without authority are
   flagged.
8. **Realloc safety**: any `account.realloc(...)` must zero-init the
   new space; otherwise stale-data leak.
9. **Rent exemption**: every initialized account must be rent-exempt.
10. **Council verdict**: route findings + program source to the council.
    Drax (red team) and Veritas (auditor) weighted. Council prompt
    includes a primer on Sealevel runtime semantics so providers that
    are EVM-default don't reach for Solidity assumptions.

## Severity rubric (Solana-specific)

- **HIGH**: drain via missing signer check, missing discriminator,
  insufficient PDA seed binding, missing CPI authority.
- **MEDIUM**: rent-exemption miss (account becomes purgeable), realloc
  without zero-init.
- **LOW**: missing `#[account(close = ...)]` on temp accounts, gas/CU
  inefficiency.
- **INFO**: lint warnings, style.

## Output

Same shape as `solidity-audit-pipeline` — `audit-report.md` plus raw
tool outputs plus optional on-chain attestation via
`monad-attestation` (the attestation contract is on Monad regardless of
which chain the audited program lives on; STOA's attestation infra is
Monad-anchored).

## Cross-chain note

This is a STOA scope expansion. The audited artifact is on Solana; the
audit attestation is on Monad. Two chains, one verdict — the attestation
contract acts as a neutral witness rather than a chain-specific gate.
