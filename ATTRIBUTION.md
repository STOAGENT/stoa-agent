# Attribution

STOA Agent is a fork of **NousResearch / hermes-agent** v0.14.0 (MIT License,
Copyright © 2025 Nous Research) — at the time of the fork the
fastest-growing autonomous-AI-agent framework on GitHub.

This file preserves the chain of derivation as a courtesy and as a binding
fact of provenance. The vast majority of the runtime, gateway, sandbox,
memory, skill, and provider-plugin code paths in STOA Agent are inherited
from upstream and continue to work the same way.

## What we kept (inherited verbatim or near-verbatim from upstream)

- The **agent runtime** (`agent/`, `run_agent.py`) — context engine,
  prompt builder, memory manager, secret sources, transports,
  LSP integration.
- The **CLI shell** (`stoa_cli/`, `cli.py`) — slash-command registry,
  prompt_toolkit REPL, multi-line editor, autocomplete.
- The **gateway** (`gateway/`) — 21-platform messaging adapters
  (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost,
  Email, SMS, Home Assistant, DingTalk, Feishu, WeCom, WeiXin, Yuanbao,
  QQ, BlueBubbles, MSGraph, Webhook, API server).
- The **tool registry** (`tools/`) — self-registering tool layer,
  7 execution backends (`local`, `docker`, `ssh`, `modal`, `daytona`,
  `singularity`, `vercel_sandbox`).
- The **memory store** (`stoa_state.py` — renamed from `hermes_state.py`
  but functionally the same) — SQLite WAL + FTS5 unicode61 +
  trigram CJK, episodic + semantic + procedural layers.
- The **skill format** (`SKILL.md` with YAML frontmatter,
  agentskills.io standard) — 25+ shipped skill categories.
- The **provider plugin system** (`plugins/model-providers/`) — 200+
  models across 18+ providers, three API modes (chat_completions,
  codex_responses, anthropic).
- The **install script** (`scripts/install.sh`) — uv-based,
  single-command bootstrap for Linux/macOS/WSL2/Termux.

## What we added on top

STOA Agent adds three layers above the upstream runtime:

| Upstream (hermes-agent) | STOA addition |
|---|---|
| Single agent per session | **Persona chamber** — six named personas (Sokrates / Mira / Veritas / Drax / Lyra / Echo) run in parallel on the same task; a seventh dispatcher (Hermes-the-character) composes a verdict that surfaces agreement and dissent rather than collapsing to a single voice. The personas share an underlying provider by default (DeepSeek, free at point of use); operators may bind one provider per seat in `~/.stoa/cli-config.yaml`. |
| Tool calls are local-ephemeral | **Optional on-chain attestation (preview)** — `stoa --attest` computes a response hash for every council verdict and queues it for submission to `AuditAttestationV2` on Monad mainnet. The hashing and local persistence ship today; the `eth_sendRawTransaction` transport and verifier client are under hardening for the next release. The flag is opt-in; STOA works fully offline without it. |
| Community skills land via PR review | **Council-audited skill publication** — `stoa skill publish` runs the proposed skill through a 6-persona audit (security / performance / prompt-injection / license / structural / attribution) before it is published; 5-of-6 quorum required, audit hash persisted. |

## Provider-and-cost positioning

Out of the box, STOA Agent routes all seven seats (six personas + dispatcher)
to **DeepSeek** (`deepseek-chat` / `deepseek-reasoner`). No personal API key
is required for the basic council loop — the chamber is free at the point
of use.

Operators who prefer a one-provider-per-persona binding (e.g. Sokrates →
Anthropic, Veritas → Google, Drax → xAI) supply their own keys per seat
in `~/.stoa/cli-config.yaml`. STOA does not redistribute provider keys; any
key bound is and stays the operator's.

## License

The upstream hermes-agent MIT License text is retained verbatim in
[LICENSE](LICENSE) under its original copyright (© 2025 Nous Research).
STOA Agent's additions ship under the same MIT License (© 2025 STOA Agent
contributors). The combined notice is in the project root `LICENSE` file.

If you fork STOA Agent in turn, please add yourself to the fork chain below.

## Fork chain

- **NousResearch / hermes-agent** v0.14.0 — MIT © 2025 Nous Research
- **STOAGENT / stoa-agent** v0.14.x — MIT © 2025 STOA Agent contributors — *this fork*
