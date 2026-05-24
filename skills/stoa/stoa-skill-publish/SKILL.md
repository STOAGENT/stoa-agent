---
name: stoa-skill-publish
description: "Use when a contributor wants to publish a community skill to the STOA marketplace — runs a six-agent audit gate, requires 5-of-6 quorum, and writes the audit hash on-chain before the skill becomes installable."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos, windows]
metadata:
  stoa:
    tags: [skill-marketplace, publish, audit-gate, council, security]
    related_skills: [council-verdict, monad-attestation, solidity-audit-pipeline]
---

# STOA Skill Publish — the audit gate

Upstream community skills typically land via PR review. STOA's answer
to the open-skill "nine CVEs in four days" problem is stricter: **no
skill publishes without a six-agent audit, a 5-of-6 quorum, and an on-chain
audit hash**.

## When to use

- A contributor runs `stoa skill publish ./my-skill/`.
- A CI pipeline opens a new community skill PR.
- An internal STOA contributor adds a new bundled skill.

## When NOT to use

- Local development iteration — `stoa skill add ./my-skill/` installs a
  skill into `~/.stoa/skills/` without the gate, for the local user only.
- A trivial doc-only patch to an already-audited skill (the diff
  classifier catches this; only the diff is re-audited).

## Six audit lenses

Each persona reviews the skill from their primary angle, then all six
co-sign or escalate:

| Persona  | Lens | Looks for |
|----------|------|-----------|
| Sokrates | structural | Does the SKILL.md match agentskills.io? Is the manifest complete? Are the description triggers tight? |
| Mira | performance | Does the skill have bounded resource use? Does it spawn unbounded subprocesses? Is the LLM call budget defined? |
| Veritas | correctness | Do the example invocations produce the documented output? Are claimed integrations real? |
| Drax | adversarial | Can a prompt injection in the user's input trick the skill into shell calls, secret exfil, or PATH writes? |
| Lyra | attribution + license | Is the author field accurate? Does the license match all imported code? Any copy-paste from incompatible sources? |
| Echo | UX + naming | Will downstream skill discovery surface this skill correctly? Are tags clean? Does the description trigger on the right use cases? |

## Pipeline

1. Lint: SKILL.md frontmatter schema check, total size cap, forbidden filenames absent.
2. Static scan: ripgrep for high-risk patterns.
3. Sandbox dry-run: install the skill into an ephemeral Docker container, run its declared example inputs, capture output. No network egress.
4. Six-agent audit: dispatch the skill source + dry-run output to the council. Each persona scores pass / pass-with-changes / block on their lens.
5. Quorum: 5/6 pass = publish. 4/6 pass = return to author with the union of "pass-with-changes" demands. 3/6 or below = block, log reasons, no publish.
6. Attest: write the audit hash + author wallet + skill name on-chain via monad-attestation. The publish only completes after the tx confirms.
7. Index: append to `~/.stoa/skill_registry.db` plus push to the federated marketplace index.

## Output

```
status         publish | changes_requested | blocked
quorum         5/6 | 4/6 | 3/6 | ...
audit_hash     sha256 over the SKILL.md plus scripts/ tree
tx_hash        on-chain attestation tx
findings       per-persona pass/changes/block plus comments
publish_url    where the skill is now installable (if status=publish)
```

## Anti-Sybil

The author signs the publish request with a wallet that holds STOA on
Monad. The audit hash is bound to that wallet — a publisher cannot
re-spawn under a fresh wallet to bypass a block.
