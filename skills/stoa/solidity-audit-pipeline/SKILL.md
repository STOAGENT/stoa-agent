---
name: solidity-audit-pipeline
description: "Use when the user submits a Solidity contract for audit — runs Slither + Mythril + Echidna + Foundry invariant tests + manual review, aggregates findings by severity, then routes the high-severity set through the council for verdict."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos, windows]
metadata:
  stoa:
    tags: [audit, solidity, security, slither, mythril, echidna, foundry]
    related_skills: [council-verdict, monad-attestation]
---

# Solidity Audit Pipeline

The full STOA audit flow for a Solidity artifact. Combines deterministic
static analysis, fuzzing, and a council verdict over the findings.

## When to use

- A user submits one or more `.sol` files or a Foundry project path.
- The user says "audit", "review", "find bugs", "check for reentrancy".
- The chamber has accepted a commissioned audit task.

## When NOT to use

- Casual code reading or "explain this contract" — that is a single-agent
  `ask` task.
- The user submitted Vyper / Cairo / Move — different toolchain, different
  skill.

## Pipeline stages

1. **Setup**: detect Foundry (`foundry.toml`) or Hardhat or single-file.
   Install via `forge install` if needed, in the configured sandbox
   backend (local / docker / modal / vercel-sandbox).
2. **Static analysis — Slither**:
   ```sh
   slither <path> --json -
   ```
   Filters: `reentrancy-eth, uninitialized-state, locked-ether,
   arbitrary-send, controlled-delegatecall, unchecked-lowlevel,
   tx-origin, missing-zero-check`.
3. **Static analysis — Mythril** (deep, slow):
   ```sh
   myth analyze <path> --execution-timeout 300
   ```
4. **Fuzzing — Echidna**:
   ```sh
   echidna-test <contract> --config echidna.yaml
   ```
   Property + assertion mode.
5. **Invariant — Foundry**:
   ```sh
   forge test --match-path 'test/Invariant*' -vvv
   ```
6. **Symbolic — Halmos** (optional, opt-in):
   ```sh
   halmos --contract <name>
   ```
7. **Aggregate**: merge findings by `(file, line, detector)`, deduplicate,
   classify severity using STOA's calibrated rubric.
8. **Council verdict**: route the high-severity set + the contract source
   through `council-verdict` skill. Drax (red team) and Veritas (auditor)
   are weighted in the verdict prompt because this is their domain.
9. **Report**: produce `audit-report.md` + `audit-evidence.json` (raw
   tool outputs preserved). Compute `response_hash` over the report body
   for optional `monad-attestation`.

## Severity rubric

- **HIGH**: funds at direct risk, no on-chain mitigation possible
  post-deploy (reentrancy, signature replay, donation, oracle manip,
  controlled-delegatecall).
- **MEDIUM**: griefing, DoS, parameter misconfig that requires admin
  rotation to fix.
- **LOW**: gas optimization, style, missing event, missing zero-check
  with no path to loss.
- **INFO**: unused code, doc gap, naming.

## Output

`audit-report.md` matching STOA's standard template — Findings (grouped
by severity), Recommendations, Methodology, Tools, Limitations,
Attestation block (tx hash if on-chain), Signatures (5-of-6 council
ECDSA).

## Council emphasis

When `dispatch_council()` is called from this skill, the verdict prompt
appends:

> Drax, Veritas: this is your domain. Lead the finding triage. The other
> four read your triage and either co-sign or escalate. We need 5/6 for
> the report to ship.
