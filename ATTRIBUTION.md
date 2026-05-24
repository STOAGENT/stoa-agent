# Attribution

STOA Agent is a fork of **NousResearch / hermes-agent** v0.14.0 (MIT License, Copyright © 2025 Nous Research) — at the time of the fork the fastest-growing autonomous-AI-agent framework on GitHub.

## What we kept

Everything that made Hermes Agent great:

- The **agent runtime** (`agent/`, `run_agent.py`) — context engine, prompt builder, memory manager, secret sources, transports, LSP integration.
- The **CLI shell** (`stoa_cli/`, `cli.py`) — slash-command registry, prompt_toolkit REPL, multi-line editor, autocomplete.
- The **gateway** (`gateway/`) — 21-platform messaging adapters (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost, Email, SMS, Home Assistant, DingTalk, Feishu, WeCom, WeiXin, Yuanbao, QQ, BlueBubbles, MSGraph, Webhook, API server).
- The **tool registry** (`tools/`) — self-registering tool layer, 7 execution backends (`local`, `docker`, `ssh`, `modal`, `daytona`, `singularity`, `vercel_sandbox`).
- The **memory store** (`stoa_state.py` — formerly `stoa_state.py`) — SQLite WAL + FTS5 unicode61 + trigram CJK, episodic + semantic + procedural layers.
- The **skill format** (`SKILL.md` with YAML frontmatter, agentskills.io standard) — 25+ shipped skill categories.
- The **provider plugin system** (`plugins/model-providers/`) — 200+ models across 18+ providers, three API modes (chat_completions, codex_responses, anthropic).
- The **install script** (`scripts/install.sh`) — uv-based, single-command bootstrap for Linux/macOS/WSL2/Termux.

## What we changed

Everything that makes a **6-LLM Council** different from a single brain:

| Hermes Agent (upstream) | STOA Agent (this fork) |
|---|---|
| Single LLM per session | 6 sovereign LLMs in parallel (`/council` mode), 5-of-6 quorum verdict |
| Agent action is local-ephemeral | Every tool call carries a response hash; opt-in on-chain attestation on Monad mainnet |
| No reputation graph | ERC-8004 reputation events written per command, queryable cross-agent |
| Community skills land via PR review | New skill publication runs through a 6-agent audit (security / performance / prompt-injection / license / structural / attribution) — 5-of-6 quorum required before publish |
| One brand | Brand = "the council of six": Sokrates / Mira / Veritas / Drax / Lyra / Echo + STOA the dispatcher |
| Free | Free tier keeps solo mode; council mode + on-chain attestation are gated by STOA token balance on Monad |

## License

The original Hermes Agent ships under the MIT License (LICENSE file unchanged in this fork). STOA Agent continues under the same MIT License.

This file (`ATTRIBUTION.md`) preserves the chain of derivation as a courtesy and a binding fact of provenance. If you fork STOA Agent in turn, please add yourself below.

## Fork chain

- **NousResearch / hermes-agent** v0.14.0 — MIT © 2025 Nous Research
- **STOAGENT / stoa-agent** v0.1.0 — MIT © 2026 STOA — *this fork*
