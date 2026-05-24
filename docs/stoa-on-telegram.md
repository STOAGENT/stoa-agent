# STOA Agent on Telegram (and 20 other platforms)

The STOA Agent fork inherits Hermes's full 21-platform gateway —
Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Mattermost, Email,
SMS, BlueBubbles, MSGraph (Outlook), HomeAssistant, DingTalk, Feishu,
WeCom, QQ, Webhook, plus a generic API server. The council follows you
into every messaging app you already use.

## Set up Telegram

```sh
# 1. Talk to @BotFather on Telegram, create a bot, grab the token.
# 2. Add the token to ~/.stoa/cli-config.yaml:

cat >> ~/.stoa/cli-config.yaml <<'YAML'
gateway:
  enabled: true

platforms:
  telegram:
    enabled: true
    bot_token: "123456789:AAA..."   # from @BotFather
    allowed_user_ids: [12345678]    # your Telegram user id, or [] to allow anyone (NOT recommended)
    allowed_chat_ids: []            # leave empty to allow DMs only
YAML

# 3. Run the gateway daemon:
stoa gateway

# 4. Open Telegram, DM your bot:
#    hi
#    /persona drax
#    /council should I deploy on a Friday?
#    /attest
#    /verdict
```

## STOA-specific slash commands (work on every platform)

| Command | What it does |
|---|---|
| `/council "<task>"` | Six personas in parallel, 5-of-6 quorum verdict, response hash returned. |
| `/persona <name>` | Switch the solo-mode persona (`sokrates`, `mira`, `veritas`, `drax`, `lyra`, `echo`). |
| `/attest` | Stamp the most recent response_hash on-chain (Monad AuditAttestationV2). |
| `/verdict` | Re-display the most recent council verdict (no LLM call, free). |

The plain Hermes commands (`/skills`, `/model`, `/help`, `/clear`, etc.)
all continue to work unchanged — STOA is additive.

## Token gating on Telegram

`/council` and `/attest` require your wallet to hold STOA on Monad.
The check runs once per chat session and is cached, so the chain RPC
is not hit on every command. If your wallet is unbound or below the
threshold, the bot replies:

> council requires a STOA holding. run `stoa wallet bind 0x...` from
> your local install first, then try again.

To bypass gating for free-tier deployments, set
`STOA_GATING_BYPASS=1` on the gateway host. Useful for operators who
want to host a public bot without forcing every user through a wallet
binding.

## Rendering per platform

Each platform's `render_council_verdict` helper formats the output
appropriately:

| Platform | Render |
|---|---|
| Telegram | MarkdownV2, per-agent quote blocks, verdict in bold |
| Discord | Embeds, one per persona, verdict as final embed |
| Slack | Block kit, agents as fields, verdict as section |
| WhatsApp | Plain text, verdict prefix `VERDICT:` |
| Signal | Plain text |
| Email | multipart/HTML |
| SMS | Verdict-only (length budget) |

## Multi-instance deployments

If you run the gateway on a VPS (Hermes ships a `$5 VPS` flow that we
inherit), each platform adapter shares the same `~/.stoa/sessions.db`
SQLite file. The council remembers your conversations across Telegram,
Discord, and the local CLI — `stoa /verdict` on your laptop will pull
the verdict you ran on your phone an hour earlier.
