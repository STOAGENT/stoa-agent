# STOA Agent — Audit Residual Findings Map

**Bağlam:** `audit-fixes-2026-05-25` branch'inde ilgili audit'in (v4-v13,
10 cycle, 71 lens, 719 finding) CRIT (28/28) + ana HIGH cluster'larının
büyük çoğunluğu kapatıldı. Bu doküman geriye kalan **MED (~259) + LOW
(~258) + bazı kapsamlı HIGH refactor'lerini** kategori bazında
listeleyip her birinin önceliklendirilmiş hedefini, dosya konumunu ve
örnek başlangıç noktasını veriyor.

Audit'in kendi tahmini: kalan defansif iş ~1-2 hafta. Bu doküman, o
işin bir TODO-PR-listesi olarak parçalanmasını sağlar.

---

## Kalan HIGH refactor'leri (audit'in büyük subsistem işleri)

Bu beş alan tek-commit fix'le yapılamaz — yeni subsistem inşası gerek.
Her biri ayrı PR + tasarım kararı + stress test ister.

### H-R1 — Email gateway DKIM/SPF/DMARC + outbound signing (v11 HIGH-52, 5 finding) — **CLOSED 2026-05-25**

`gateway/platforms/email.py` + yeni `gateway/platforms/email_dkim.py`:
- **Outbound DKIM:** opt-in via `STOA_EMAIL_DKIM_KEY` /
  `STOA_EMAIL_DKIM_SELECTOR` / `STOA_EMAIL_DKIM_DOMAIN`. Default OFF —
  geriye uyumlu. `dkimpy` yokken loud warning + unsigned fallback.
  Üç SMTP path'i (`_send_email`, `_send_email_with_attachments`,
  `_send_email_with_attachment`) tek `_smtp_send()` helper'ında
  birleştirildi; DKIM açıkken `dkim.sign()` wire bytes'a uygulanıyor.
- **Display-name spoof reject:** decoded `From:` header `"x@y" <z@w>`
  şeklindeyse inbound mesaj sessizce drop ediliyor (warn log).
- **Agent-context fence:** subject + body artık
  `<email subject="..." from="...">…</email>` XML fence'inde sarılıyor;
  attribute & body injection ikisi de entity-escape edilmiş.
- **Reused subject sanitization:** zero-width + RTLO/LRO bidi chars
  outbound `Re:` header'a sızmadan strip ediliyor, 200 char cap.
- **Stress test:** `tests/gateway/platforms/test_email_audit_v11.py` —
  18/18 green (DKIM disabled noop, spoof reject, fence + escape,
  send_message used when DKIM off).

### H-R2 — Skill marketplace ed25519/cosign + revocation + author binding (v11 HIGH-61, 7 finding)

`tools/skills_hub.py:1078+, SKILL.md publish flow` — şu an:
- Bundle imzası yok; trust strict `TRUSTED_REPOS` string-match
- `author:` field frontmatter'da unvalidated, typosquat trivial
- Yayın-sonrası kill-switch yok (revocation manifest yok)
- Author push-update silent (önceki yayında olmayan dosyalar eklenebilir)

**Önerilen yaklaşım:**
1. `tools/skills_hub.py` install path'ine ed25519/cosign opsiyonel
   imza doğrulaması (`STOA_SKILL_REQUIRE_SIGNATURE=1` opt-in)
2. Yayın repo'sunda `revocations.json` (sha → revoked_at_ms + reason),
   her install'da fetch + check
3. `author:` field'ında `did:web:` veya `npm:`/`github:` prefix
   prefixed identifier zorunlu; typosquat-defense lookup
4. Push-update diff classification: dosya ekleme = security review,
   metadata-only update = silent OK

**Tahmini efor:** ~2-3 gün; marketplace tarafı (publish.py + revocation
manifest server) için ekstra ~1 gün.

### H-R3 — SQLCipher at-rest encryption (v12 HIGH-70, 3 finding)

`stoa_state.py:340, kanban_db.py:1185, holographic/store.py:115` — şu an:
- `state.db` + `kanban.db` + `memory_store.db` plaintext
- Laptop hırsızlığı = oturum geçmişi + memory leak
- `PRAGMA secure_delete=ON` da set değil (silinen sayfalar VACUUM'a
  kadar plaintext kalır)

**Önerilen yaklaşım:**
1. `sqlite3` yerine `pysqlcipher3` opt-in dep
2. Master key OS keychain (macOS Keychain / Windows DPAPI / Linux
   `secret-service`) üzerinden çekilir; STOA_DB_PASSPHRASE_ENV
   fallback'i CI/headless için
3. Migration script: existing plaintext → SQLCipher encrypted; opt-in
   `stoa db encrypt` komutu
4. Default OFF (geriye uyumluluk) + `STOA_DB_ENCRYPTION=1` ile aç

**Tahmini efor:** ~1-2 gün; migration script ve test fixture'ları
ekstra ~yarım gün.

### H-R4 — Audit log verify CLI + chain anchoring (v9 HIGH-46 follow-up)

`agent/audit_log.py` çekirdeği yazıldı; eksik olan:
- `stoa audit verify` CLI komutu (chain bütünlüğünü walk ed + raporlar)
- `stoa audit export` (operatör/compliance request için JSONL dump)
- Opsiyonel Monad mainnet Merkle anchor (her gün son entry'nin hash'i
  on-chain'e atılır — tampered geçmiş on-chain ile çelişir)

**Tahmini efor:** CLI komutu ~2-3 saat; on-chain anchor ~1 gün
(AuditAttestationV2 contract'a ek `auditLogAnchor` function).

### H-R5 — Test coverage genişletme (v13 HIGH-5, 79 lens)

Audit gözlemi: 80 test mevcut + güçlü ama `_verify_siwe_signature`
direkt regresyon testi yok. Ayrıca `file_safety.is_write_denied`
~%9 branch coverage.

**Önerilen yaklaşım:**
- `tests/stoa_cli/test_wallet_siwe.py` ekle (5-10 test, freshness +
  nonce + chainId + replay)
- `tests/agent/test_file_safety.py` 20+ denied path için tablo testi
- `tests/agent/test_audit_log.py` chain integrity + tamper detection +
  rotation
- `tests/agent/test_skill_integrity.py` pin/load/tamper edge cases

**Tahmini efor:** ~1 gün (toplu test ekle + CI gate).

---

## MED block — kategori bazlı (259 finding)

Audit'in MED bulgularının çoğu defansif tweak. Pattern bazlı, her
biri ~5-15 dk fix. Burada kategori başlıkları + örnek bulgular +
hedef dosyalar:

### M-1 — Webhook hardening detay (16 MED)

- WeCom AES-CBC deterministic IV (G-09) — protokol kısıtlaması, dokümante edildi
- MS Graph `clientState` fail-open when None (G-03) — refuse-start gate
- WeCom callback no MaxMsgId-only dedup (G-05 follow-up) — 24h ring buffer
- MSGraph `validationTokens` JWT array doğrulanmıyor şifreli flow'da (Lens 58)
- Generic webhook `delivery_id` fallback to `time.time()` (Lens 58)
- SMS twilio loopback INSECURE flag has no host check (G-06)

**Hedef:** `gateway/platforms/*` adapter dosyaları, her biri tek tek.

### M-2 — Container/sandbox hygiene (14 MED)

- Docker storage_opt probe pulls hello-world (v9 Lens 27)
- Singularity SIF signature verify (Lens 27)
- Vercel sandbox `read_bytes()` OOM (v9 HIGH-15 follow-up)
- Modal managed bearer in exception logs (Lens 27)
- AppArmor profile path (v9 Lens 43)
- userns-remap docs (Lens 43)
- gVisor / Kata runtime support (Lens 43)

**Hedef:** `tools/environments/docker.py`, `modal.py`, `singularity.py`,
`vercel_sandbox.py`. Çoğu config-only.

### M-3 — Cron + scheduler (8 MED)

- Schedule parser DoS ceiling (Lens 17)
- `cron.max_jobs` / `min_interval_seconds` cap (Lens 17)
- Saved output unredacted (Lens 17 + redact patch — kısmen kapsanmış)
- Job ID 48-bit (Lens 17)
- Cron killswitch admin gesture (Lens 17)
- `mark_job_run` rewrites all jobs per tick (Lens 17)
- `_run_job_script` extension-based interpreter dispatch (Lens 17)

**Hedef:** `cron/scheduler.py`, `cron/jobs.py`, `stoa_cli/web_server.py`
(cron endpoint'leri).

### M-4 — Memory provider hygiene (12 MED)

- Mem0 `auto_capture` default ON (Lens 33 — pattern Supermemory'deki gibi)
- Hindsight `bank_id_template.format` attribute walk (v7 HIGH-8)
- Memory provider description prompt-injection (Lens 33)
- Honcho session_id 32-bit suffix (Lens 41)
- Cross-thread inject_message gating (Lens 53)

**Hedef:** `plugins/memory/*/__init__.py`.

### M-5 — TUI / dashboard / web (18 MED)

- `_redact_tui_verbose_text` skips strip_ansi (Lens 20)
- WS PTY bridge accepts arbitrary control bytes (Lens 20)
- Plugin name interpolated into Rich markup (Lens 20)
- Plugin install `http://` + `file://` (audit v6 HIGH-22)
- `gh pr comment` payload `pr_number` int validation (G-07)

**Hedef:** `cli.py`, `gateway/run.py` ANSI strip, `ui-tui/*`,
`stoa_cli/plugins_cmd.py`.

### M-6 — Provider / OAuth secondary (14 MED)

- ChatGPT refresh no certifi pinning (P-08)
- Copilot api_token long-lived in memory (P-09)
- `STOA_COPILOT_ACP_COMMAND` arbitrary executable (P-10)
- Codex refresh state compare with `==` (P-11)
- Anthropic OAuth client_id duplicated literals (P-12)
- Z.AI cache key `==` compare (Y-06)

**Hedef:** `agent/anthropic_adapter.py`, `stoa_cli/auth.py`,
`stoa_cli/copilot_auth.py`.

### M-7 — Config / state hygiene (13 MED)

- Bitwarden `override_existing=true` covers PATH/LD_PRELOAD (Lens 18)
- `/api/config/defaults` + `/api/config/schema` unauth (Lens 18)
- Deep-merge unbounded recursion (Lens 18)
- `_LOAD_CONFIG_CACHE` mtime+size cache defeatable (Lens 18)
- `processes.json` SIGTERM gadget (S-07)
- LIKE wildcards in entity names (S-08)
- `state.db` not chmod 0o600 in container (S-06)

**Hedef:** `stoa_cli/config.py`, `stoa_state.py`, `tools/process_registry.py`.

### M-8 — Concurrency + locking (11 MED)

- process_registry partial lock inconsistency (Lens 19)
- `asyncio.gather` without `return_exceptions=True` leaks (Lens 19)
- Fan-out concurrency cap absent (qqbot, MS Graph batch)
- Kanban DB inconsistent `check_same_thread` (Lens 19)
- Cron jobs unlocked R-M-W (kısmen kapsanmış)
- nous_rate_guard unlocked R-M-W (Lens 19)

**Hedef:** `tools/process_registry.py`, `stoa_state.py`,
`stoa_cli/kanban_db.py`, `agent/nous_rate_guard.py`.

### M-9 — Pattern hardening (43 MED)

**Status (2026-05-25):** Secondary patterns largely closed —
form-urlencoded body redact already in place,
Discord/Slack mention redact done (Slack `<@U…>` / `<#C…|chan>` /
`<!subteam^S…>` + Discord role `<@&…>` shapes covered),
Stripe / SendGrid / HuggingFace prefix gaps closed (`rk_test_`,
`pk_live_`, `whsec_*`, two-segment SendGrid shape, `hf_oauth_*`,
AWS STS `ASIA*`), WhatsApp / Instagram CDN media URL strip added,
opt-in `STOA_REDACT_IP=1` for IPv4 / IPv6 source-IP scrubbing.
Bounded quantifiers throughout to narrow ReDoS surface (Lens 21).

Remaining for a follow-up pass: E.164 phone redact width tuning
(width currently fixed; some operators want full mask), memory-
context surrogate fence (`agent/message_sanitization.py` already
recovers surrogates; the fenced-view wrapper around memory context
is a separate refactor pending the holographic store interface
change).

**Hedef:** `agent/redact.py` + `agent/message_sanitization.py`.

### M-10 — Skills hub (~50 MED)

Çok sayıda küçük: hash collision narrow widening, marketplace
metadata schema strict, install-time scan threshold, plugin
shadowing detection, vb.

**Hedef:** `tools/skills_hub.py`, `tools/skills_guard.py`.

### M-11 — Calling docs (~30 MED)

- `cron list` no safety flags (Lens 46)
- MCP no auth/allowlist surface (Lens 46)
- Egress trace per-provider (Lens 46)
- Boot integrity hash (Lens 46)
- Network egress observability (Lens 46)

**Status (2026-05-25):** SECURITY.md §2.0.0 now references the full
`STOA_*` env inventory at
`website/docs/reference/environment-variables.md` (single source of
truth) and adds a "Network egress observability" paragraph naming
the secret shapes, source-IP toggle, and the always-blocked egress
floor (Tailscale ULA + IPv6 link-local + multicast). Per-provider
egress trace + MCP allowlist UI + boot integrity hash are still
TODO — those need code work, not docs.

**Hedef:** Çoğunlukla doc-only. `website/docs/` + `SECURITY.md`
extend.

### M-12 — Diğer (43 MED)

Locale catalog hardening (Lens 60), hash collision narrow widening
(Lens 41), test coverage geniş açılım (Lens 79), reproducibility
(Lens 76), shutdown ordering (Lens 77), kalan IPv6 + cron + plugin
küçük fix'ler.

**Status (2026-05-25):** URL safety floor now also blocks IPv6
link-local (`fe80::/10`), Tailscale ULA (`fd7a:115c:a1e0::/48`),
and IPv6 multicast (`ff00::/8`) — agent egress to a tailnet peer
or to a fe80 link-local interface is impossible regardless of the
`security.allow_private_urls` toggle (Lens 46). Other items
unchanged.

---

## LOW block (258 finding)

LOW'lar cosmetic + hygiene:

- Doc tipoları + grammar (~50)
- Yorum tutarsızlıkları (~40)
- Linter false-positive uyarıları (~30)
- Markdown indentation drift (~20)
- Backtick consistency (~20)
- Test fixture cleanup (~20)
- Deprecated function call site (~20)
- ID format inconsistency (~20)
- Diğer cosmetic (~38)

**Yaklaşım:** Tek bir grand-sweep PR olarak, audit'in bireysel
LOW listesini madde madde çek + tek commit'te düzelt. Tahmini
~1 mühendislik günü.

---

## Strateji

1. **Phase 0 (DONE):** 28 CRIT — production-ready
2. **Phase 1 (BÜYÜK ORANDA DONE):** ~50 HIGH — production-ready
3. **Phase 1b (TODO):** H-R1..H-R5 büyük HIGH refactor'leri — ~5-7 gün
4. **Phase 2 (TODO):** 259 MED kategori bazlı — ~5-7 gün
5. **Phase 3 (TODO):** 258 LOW grand-sweep PR — ~1 gün

**Bu branch'in (`audit-fixes-2026-05-25`) hedefi:** Phase 0 + Phase 1
production'a engel olan her şeyi kapatmak. Phase 1b–3 ayrı sprint.

**Bu dokümanın kendisi audit Lens 17/46 "no documentation map" eksiği
için bir fix:** önceki STOA Agent'ta kalan-iş listesi tek bir merkezi
yerde değildi.
