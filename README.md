<p align="center">
  <img src="assets/banner.svg" alt="STOA Agent — six personas, one chamber, on your machine" width="100%">
</p>

# STOA Agent ⁂

**Six personas in one chamber, running on your machine.**
Council-mode debate · Bring-your-own-key · No subscription, no lock-in.

> STOA is a Socratic chamber: six named personas — each with its own role,
> system prompt, and reasoning style — debate every non-trivial task in
> parallel, then a seventh dispatcher composes a verdict. STOA itself is
> free and open-source forever; you bind your own LLM API key (we
> recommend DeepSeek's free tier — 2-minute signup, no card) and STOA
> never sees, brokers, or proxies your key. Want one provider per persona
> (e.g. Sokrates → Anthropic, Veritas → Google, Drax → xAI)? Bind a
> different key per seat in `~/.stoa/cli-config.yaml`.

This is a fork of [NousResearch / hermes-agent](https://github.com/NousResearch/hermes-agent) v0.14.0 (MIT). The runtime, gateway, sandboxes, memory store, skill format, and provider plugin layer are inherited from upstream — see [ATTRIBUTION.md](ATTRIBUTION.md) for the full lineage. What STOA adds on top is the chamber: persona orchestration, an optional on-chain attestation preview, and a 6-agent skill-publication audit gate.

---

## Install

**macOS · Linux · WSL2 · Termux**
```sh
curl -fsSL https://stoax.xyz/install.sh | sh
```

**Windows · PowerShell**
```powershell
iex (irm https://stoax.xyz/install.ps1)
```

**PyPI**
```sh
pip install stoa-agent
stoa setup
```

**Homebrew**
```sh
brew tap stoagent/stoa
brew install stoa-agent
```

**Direct from source**
```sh
git clone https://github.com/STOAGENT/stoa-agent
cd stoa-agent
uv venv && uv pip install -e .
stoa setup
```

---

## What STOA adds

### 1. Council mode — six personas, one chamber

Each persona has its own system prompt, reasoning style, and tool affinity. They run in parallel against the same task, and a seventh dispatcher composes a verdict by surfacing agreement and dissent rather than collapsing the answer to a single voice.

```sh
stoa /council "audit this contract: $(cat MyToken.sol)"
# → 6 personas in parallel
# → Sokrates / Mira / Veritas / Drax / Lyra / Echo each respond
# → Verdict + per-persona dissent + response hash (local)
```

| Persona | Role |
|---|---|
| **Sokrates** | the question-maker — surfaces hidden assumptions |
| **Mira** | the builder — produces concrete artifacts |
| **Veritas** | the auditor — looks for incorrectness |
| **Drax** | the red team — looks for failure modes |
| **Lyra** | the designer — looks for clarity and form |
| **Echo** | the operator — looks for ops + lifecycle risk |
| **Hermes** | the dispatcher (the seventh) — composes the verdict |

### 2. Provider model — bring-your-own-key

STOA never bundles, brokers, or proxies anyone else's API key. There is no STOA cloud and no "free credits from us." Instead the first-run wizard walks you through the cheapest viable setup:

1. Go to https://platform.deepseek.com/api_keys (free signup, no card).
2. Create a key. DeepSeek's free tier covers typical solo use; a heavy session is a few cents.
3. Paste the key. STOA writes it to `~/.stoa/cli-config.yaml` on your machine and never sends it anywhere else.

Result: the chamber works end-to-end on your own DeepSeek free tier, with you in full control of the spend. Want one provider per persona (e.g. Sokrates → Anthropic, Veritas → Google, Drax → xAI)? Bind a different key per seat:

```yaml
# ~/.stoa/cli-config.yaml — produced by `stoa setup`, fully editable
personas:
  sokrates: { provider: deepseek, model: deepseek-reasoner }   # default
  mira:     { provider: deepseek, model: deepseek-chat }       # default
  # …or override per seat with a different key + provider:
  veritas:  { provider: anthropic, model: claude-opus-4-7, api_mode: anthropic }
```

> The persona names (Sokrates / Mira / Veritas / Drax / Lyra / Echo / Hermes) are role identifiers and are decoupled from any single model vendor — see `stoa /persona list` for the live binding on your machine.

### 3. Council-audited skill publication

The hardest problem in agent skill ecosystems is supply-chain trust. STOA's answer: **no skill publishes without a 6-persona audit + 5-of-6 quorum + a local audit hash**. Security, performance, prompt-injection, license, structure, attribution — six different lenses on every new skill.

```sh
stoa skill publish ./my-skill
# → 6 personas audit it independently
# → 5-of-6 quorum required
# → audit hash written locally; on-chain stamp behind --attest (preview)
```

### 4. On-chain attestation — preview

`stoa --attest` is currently a **preview feature** behind a flag.

When enabled, every council verdict optionally writes its response hash to **AuditAttestationV2** on Monad mainnet, so months later anyone can verify a STOA agent ran exactly the action it claims it ran. The hashing + persistence are wired; the `eth_sendRawTransaction` submission and verifier client are under hardening for the next release. Until then, expect `--attest` to compute the hash, queue the request, and log `attestation_preview: pending_submit`.

If you have no need for on-chain verifiability, you can ignore `--attest` entirely — the chamber, the verdict, and the skill audit gate all work locally without it.

---

## Commands

| Command | What it does |
|---|---|
| `stoa` | Splash dashboard + interactive REPL |
| `stoa chat` | Direct chat mode |
| `stoa setup` | First-run wizard (writes `~/.stoa/cli-config.yaml`) |
| `stoa gateway` | Run the multi-platform daemon (Telegram, Discord, Slack, etc.) |
| `stoa /council "<task>"` | Six personas in parallel + verdict |
| `stoa /persona <name>` | Switch single-persona mode |
| `stoa /persona list` | Show the live persona ↔ provider binding |
| `stoa /verdict` | Show the last council verdict |
| `stoa /attest` | **preview** — stamp the last verdict on-chain |
| `stoa skill publish` | Runs the 6-persona audit gate before publishing |
| `stoa migrate xai` | Rewrite config to replace retired xAI models with current ones |

---

## Skills shipped under `skills/stoa/`

- `council-verdict` — orchestrate a 6-persona call from inside a skill
- `monad-attestation` — write a hash to AuditAttestationV2 (preview)
- `solidity-audit-pipeline` — Slither + Mythril + Echidna + manual review
- `erc8004-reputation` — read or write agent reputation events
- `stoa-skill-publish` — the publication audit gate itself
- `monad-mev-watchdog` — passive on-chain monitor
- `solana-anchor-audit` — Anchor-program review

---

## Security posture

STOA inherits the same primitive set as its upstream lineage: shell execution, browser automation, plugin marketplace, optional wallet binding. These are powerful tools and require operator literacy — STOA targets the same operator profile as Cursor, Claude Code, and Aider.

- **Default-OFF gates** (DB encryption, PII/IP redaction, skill ed25519 signature, mandatory sandbox) are being flipped to default-ON via a `STOA_SECURITY_PRESET` selector in the next release.
- **Bug bounty + coordinated disclosure**: see [SECURITY.md](SECURITY.md). Do **not** open public issues for security reports.
- **Audit reports** are not published to the master tree — coordinated disclosure first. We ship fixes, then summary write-ups.

---

## License

MIT for the STOA Agent codebase. See [LICENSE](LICENSE).
The upstream MIT license is preserved verbatim; this fork adds the attribution recorded in [ATTRIBUTION.md](ATTRIBUTION.md).

**Bundled assets carry their own licenses:**

- `web/public/fonts-terminal/JetBrainsMono-*.woff2` — SIL Open Font
  License 1.1, see [`web/public/fonts-terminal/OFL.txt`](web/public/fonts-terminal/OFL.txt).
- `optional-skills/productivity/powerpoint/` — **Proprietary, Anthropic**.
  Opt-in only (set `STOA_ENABLE_OPTIONAL_SKILLS=1` to discover it).
  Use is governed by your separate agreement with Anthropic; the file
  `optional-skills/productivity/powerpoint/LICENSE.txt` ships the full
  terms. NOT covered by MIT.
- `optional-skills/mlops/inference/obliteratus/` — AGPL-3.0. Opt-in via
  `STOA_ENABLE_REDTEAM=1`. AGPL §13 obligations apply if you ship a
  network-accessible service that incorporates this skill.

## Links

- Docs · https://stoax.xyz/cli
- Chamber · https://stoax.xyz
- PyPI · https://pypi.org/project/stoa-agent/
- Source · https://github.com/STOAGENT/stoa-agent
- Upstream lineage · [ATTRIBUTION.md](ATTRIBUTION.md)
