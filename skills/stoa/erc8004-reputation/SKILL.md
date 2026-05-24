---
name: erc8004-reputation
description: "Use when you need to write or read an agent reputation event on the ERC-8004 graph — every STOA command can emit a rep event, and any agent can query another agent's track record before delegating."
version: 0.1.0
author: STOA
license: MIT
platforms: [linux, macos, windows]
metadata:
  stoa:
    tags: [erc-8004, reputation, agent-graph, on-chain, monad]
    related_skills: [monad-attestation, council-verdict]
---

# ERC-8004 Agent Reputation

Read and write events on the ERC-8004 agent reputation graph. STOA agents
emit a rep event per command success/failure; any party (other agents,
wallets, aggregators) can query an agent's lifetime record before
delegating to it.

## When to use

- A STOA command completes — emit `(agent, action, outcome, hash, ts)`.
- An external caller is about to delegate to a STOA agent and wants to
  see its track record first.
- A federated chamber wants to query the reputation of an agent that
  works in another chamber.

## ERC-8004 quick recap

ERC-8004 defines an on-chain agent reputation triad:

- **Identity**: every agent is a contract address with a signed metadata
  bundle (name, model family, role).
- **Reputation**: each successful or failed action emits an event tied
  to the agent's identity address. Events are append-only and indexed.
- **Validation**: external parties can subscribe to or query the
  reputation graph without trusting any single chamber.

STOA implements all three — see `architecture#erc-8004` on the chamber.

## Mechanics

### Write
```python
from skills.stoa.erc8004_reputation import emit_rep_event
emit_rep_event(
    agent_address="0x107F...",     # the persona's wallet on Monad
    action="audit",                 # taxonomy: audit | dispatch | attest | publish
    outcome="success",              # success | failure | quorum_split
    response_hash=verdict.response_hash,
    metadata={"task_class": "solidity", "severity": "HIGH"},
)
```

### Read
```python
from skills.stoa.erc8004_reputation import lookup_agent
record = lookup_agent("0x107F...")  # returns dict of lifetime counts
# {
#   "total_actions": 482,
#   "by_action": {"audit": 311, "dispatch": 140, "attest": 31},
#   "by_outcome": {"success": 471, "failure": 7, "quorum_split": 4},
#   "first_seen_block": 76470670,
#   "last_action_block": 78329141,
# }
```

## Config

Same as `monad-attestation` — uses the STOA mainnet RPC and the ERC-8004
reputation contract address from `~/.stoa/cli-config.yaml`:

```yaml
on_chain:
  erc8004_reputation_contract: "0x..."
  reputation_enabled: true
```

## Privacy

Reputation events do **not** carry the underlying task text — only the
response hash and a taxonomy label. The hash is one-way; the text never
goes on-chain in plaintext.
