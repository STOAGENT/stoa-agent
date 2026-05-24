---
name: council-verdict
description: "Use when the user asks a high-stakes question (audit, security review, deploy decision, contract review) — dispatch all six sovereign LLMs in parallel and compose a 5-of-6 quorum verdict instead of relying on one brain."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos, windows]
metadata:
  stoa:
    tags: [council, verdict, multi-llm, quorum, on-chain]
    related_skills: [monad-attestation, stoa-skill-publish]
---

# Council Verdict

When the cost of being wrong is high, route the task to all six council
personas in parallel and compose a quorum verdict instead of trusting any
single LLM.

## When to use

- Security/audit questions: "is this Solidity safe?", "is this approve race
  a real bug?"
- Deploy decisions: "should I ship this on a Friday?", "what's the risk of
  this migration?"
- Disputed claims: any task where the user explicitly asks for a "second
  opinion" or "panel" or "council".
- High-value contract reviews: any time the artifact under review handles
  money, identity, or governance.

## When NOT to use

- Casual greetings, short factual lookups, code formatting — single-agent
  mode is faster and cheaper.
- Tasks under 200 characters that have one obvious answer.

## Mechanics

1. The skill calls `agent.verdict_composer.dispatch_council(task=...)`.
2. Six personas (Sokrates / Mira / Veritas / Drax / Lyra / Echo) each
   answer in parallel — every persona is bound to a different sovereign
   provider via `~/.stoa/cli-config.yaml`.
3. Hermes (the seventh) reads all six and composes a verdict. Five of six
   must agree on the core position for the verdict to claim `consensus`.
4. The verdict carries a sha256 `response_hash` over canonical JSON of
   the inputs + outputs.

## Output shape

```
verdict_text     str   — what to actually do
agreement        str   — consensus | split | no_consensus
agents           list  — six per-persona responses + latencies + tokens
failed_personas  list  — providers that timed out / refused
response_hash    str   — sha256, attestable on-chain via monad-attestation
duration_ms      int   — wall-clock for the parallel dispatch
```

## Pairing with on-chain attestation

If the caller passes `--attest`, run the `monad-attestation` skill with
the returned `response_hash` to stamp the verdict on Monad mainnet's
`AuditAttestationV2` contract. The chain becomes the witness.
