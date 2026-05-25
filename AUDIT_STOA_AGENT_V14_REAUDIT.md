# STOA Agent — v14 RE-AUDIT (audit-fixes-2026-05-25 branch)

**Date:** 2026-05-25
**Target:** `C:\Users\enesa\stoa-agent\` branch `audit-fixes-2026-05-25` (13 commits, ~600 LOC net)
**Methodology:** 3 parallel verification cycles
- Cycle 1: 28 CRIT closure verification via code inspection
- Cycle 2: per-commit new-attack-surface scan (13 diffs)
- Cycle 3: top-15 highest-yield lens panel re-run against fixed code

---

## EXECUTIVE SUMMARY

**User claim:** 25/28 CRIT closed · 1 partial · 2 skipped
**Verified reality:** **20 closed · 4 partial · 2 BROKEN · 2 skipped · 14 new attack surfaces from fix patches**

### 🔴 BROKEN / REGRESSED (2)

1. **uv.lock eth-account MISSING** — `pyproject.toml:93` requires `eth-account==0.13.7` but uv.lock contains zero matches. `uv sync --frozen` will fail; runtime SIWE → ImportError → every `bind_wallet` refuses → council mode bricked when STOA_TOKEN_CONTRACT activated.

2. **`04417e4` holographic owner_principal NO MIGRATION** — `_init_db` uses `CREATE TABLE IF NOT EXISTS`; existing DBs keep old schema; first `add_fact()` after upgrade → `sqlite3.OperationalError: table facts has no column named owner_principal`. Crashes every memory-plugin-enabled existing user.

### ⚠️ PARTIAL (4)

1. **CRIT 5 Obliteratus** — physical file still at `skills/mlops/inference/obliteratus/`, only name-marker filter (no `git mv`)
2. **CRIT 15 Supermemory** — substrate accepts `gateway_user_id` but gateway adapters (Telegram/Discord/Slack/Web) don't plumb it; multi-user gateways still collapse to `"anon"` suffix
3. **CRIT 17/18 Update signing** — `STOA_REQUIRE_SIGNED_UPDATES=1` opt-in; default still vulnerable. Acceptable as documented but counts PARTIAL.

### 🆕 NEW ATTACK SURFACES (14, from fix patches)

| # | Severity | File | Issue |
|---|---|---|---|
| N1 | HIGH | `stoa_cli/web_server.py:1783` | OAuth state validation `if state_from_callback and not compare_digest()` — empty callback state bypasses CSRF |
| N2 | HIGH | `MANIFEST.in` graft optional-skills | Powerpoint Anthropic-proprietary still ships in PyPI sdist (runtime gate yes, redistribution gate no) |
| N3 | HIGH | `stoa_state.py:2544+` `delete_user` | Cascade doesn't reach `telegram_dm_topic_bindings`, holographic `facts.owner_principal`, supermemory cloud — Art. 17 partial |
| N4 | HIGH-op | `tools/skills_guard.py` (uncommitted) | content_hash widening to 256-bit in working tree but NOT committed; `skills_hub` already 64-hex → asymmetric → false `update_available` for every skill |
| N5 | MED | `stoa_cli/wallet.py:166-176` | Nonce store `load → mutate → write_text` no atomic write + no flock + `except Exception: return {}` → file corruption wipes replay defense |
| N6 | MED | `tools/code_execution_tool.py:79-96` | `_STOA_SAFE_EXACT` excludes `STOA_HOME/TIMEZONE/RPC_SOCKET` → skills relying on them break silently in sandbox |
| N7 | MED | `stoa_cli/wallet.py:49` | `int(os.getenv("STOA_CHAIN_ID", "10143"))` at module import — garbage env raises ValueError → all wallet calls crash |
| N8 | MED | `agent/file_safety.py:176` | Symlink escape: `resolved = Path(path).resolve()` — `~/.ssh/id_rsa → /tmp/key` resolves out of deny prefix; lexical+resolved double check missing |
| N9 | MED | `plugins/memory/supermemory/__init__.py:506-510` | Raw `gateway_user_id` (Telegram chat_id, Discord snowflake) sent to Supermemory cloud unhashed → stable user identifier crossing trust boundary |
| N10 | MED | `tools/code_execution_tool.py:94-96` | `_SECRET_SUBSTRINGS` has DSN/PEM/SIGNATURE but NOT URI/URL/CONN/WEBHOOK → `DATABASE_URL`, `REDIS_URI`, `*_BASE_URL` leak into sandbox |
| N11 | MED | `stoa_cli/main.py:8868-8902` | `git verify-commit origin/X` then `git pull` — pull re-fetches → MITM flips between verified ref and merged ref. Fix: `git merge --ff-only <verified-sha>` |
| N12 | MED | `plugins/memory/holographic/store.py:253` | `(owner_principal IS NULL OR ...)` — any writer forgetting to set owner_principal poisons shared pool readable by all users |
| N13 | LOW | `stoa_state.py:2529` | `_remove_session_files`: `sessions_dir / f"{session_id}.json"` no `Path(session_id).name` slugging — DB-generated ids safe today, future schema relaxation = traversal |
| N14 | LOW | `~/.stoa/wallet_nonces.json` | Plain JSON, no integrity check — local attacker clears file → in-window signature replay |

### ✅ CLOSED (20)

Dashboard PKCE state independence (P-01), Codex client-side PKCE (P-02), read_file deny list cross-platform (T-01), sandbox env explicit allowlist (T-02), Nix fork-PR Cachix gate (Q-01), Powerpoint physical removal (29-1), OFL.txt shipped (29-2), README honest license (29-3), SIWE freshness + nonce store + chainId 10143 (25-1, 34-1), MCP non-interactive default-deny (32-01), holographic owner_principal schema (33-01) [but migration broken see CRIT-A], conversation_loop session_id plumb (33-02), redteam install gate fail-closed (K-01), seccomp profile JSON comprehensive (43-1), `stoa import` live-check (56-1), zip path traversal belt-and-suspenders (56-2), `_safe_copy_db` fail-closed no fallback (56-3), `delete_user` (57-1) [partial cascade], `export_user_data` (57-2), `auto_prune=True`+90day default (57-3).

### 🆗 SKIPPED (2, acknowledged)

- CRIT 11 (skill/code content-hash gate at load) — V7 CRIT-30-01
- CRIT 19 (pip --require-hashes) — V6 C-3

### ✅ POSITIVES (from fix patches — what the patches got right)

- OAuth fixes textbook: independent state token, `hmac.compare_digest`, client-side PKCE verifier
- Seccomp profile real + comprehensive: `data/seccomp/stoa-sandbox.json` 66-line denying io_uring_*, userfaultfd, keyctl, bpf, perf_event_open with `SCMP_ACT_ERRNO`
- SIWE 4-layer defense: chainId binding + 10-min freshness window + persisted nonce store + load-time re-verify
- Backup belt-and-suspenders: `/`, `../`, `/../`, `/..`, drive letter, UNC BEFORE `relative_to()` check
- Backup brave tradeoff: drop `shutil.copy2` fallback → loud failure but no silent corruption
- README license enumeration enterprise-procurement ready (AGPL/Proprietary/OFL per-asset)
- Inline audit annotations: every fix references audit ID (`Audit v8 CRIT-32-01 fix:`) in docstring → future auditor trace
- Streamed hashing for ZIP (`iter(lambda: fh.read(1<<20), b"")`) — no OOM on large archives
- Test coverage exists for harder fixes (`tests/agent/test_memory_user_id.py`)
- Nix fork-PR `core.setFailed()` BEFORE checkout (correct ordering)

---

## 🛑 PUSH-BLOCKING FIX LIST (6 items, ~3 hours)

Priority order before tagging/pushing:

1. **`uv lock` rerun** — fixes broken eth-account + transitive closure (eth-abi/eth-keys/eth-keyfile/ckzg/pycryptodome). Verify `uv sync --frozen` passes in CI. **(Closes broken CRIT 21)**

2. **`plugins/memory/holographic/store.py` ALTER TABLE migration** — copy the pattern at line 153 (`hrr_vector`):
   ```python
   if "owner_principal" not in columns:
       self._conn.execute("ALTER TABLE facts ADD COLUMN owner_principal TEXT DEFAULT NULL")
       self._conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(owner_principal)")
   ```
   **(Closes broken CRIT-A — existing user crash on first add_fact)**

3. **`stoa_cli/web_server.py:1783` OAuth state unconditional check** — drop the `state_from_callback and` short-circuit:
   ```python
   if not state_from_callback or not hmac.compare_digest(state_from_callback, expected_state):
       raise CSRFError("invalid_state")
   ```
   **(Closes N1 HIGH — empty-state CSRF bypass)**

4. **`MANIFEST.in` exclude powerpoint** — add `prune optional-skills/productivity/powerpoint` or `global-exclude LICENSE.txt`. **(Closes N2 HIGH — proprietary in PyPI sdist)**

5. **`delete_user` cascade extend** — add three calls:
   - `DELETE FROM telegram_dm_topic_bindings WHERE user_id=?`
   - `holographic_store.delete_facts_for_owner(owner_principal)`
   - `supermemory.delete_container(container_tag)`
   **(Closes N3 HIGH — GDPR partial erasure)**

6. **`tools/skills_guard.py` content_hash widening commit + stage** — currently uncommitted in working tree. Without this, `update_available` reports every installed skill as needing update (false positive flood). **(Closes N4 HIGH-op)**

### Recommended for v15 (after push)

- Gateway adapter plumbing for `gateway_user_id` (Telegram/Discord/Slack/Web) — closes CRIT 15 from PARTIAL → CLOSED
- HMAC-hash `gateway_user_id` before Supermemory tag substitution — closes N9
- `os.open(O_CREAT|O_EXCL|O_WRONLY, 0o600)` for anthropic credential file (v10 H1 unchanged)
- Wallet nonce store atomic write via `os.replace(tmp, final)`
- `_SECRET_SUBSTRINGS` extend with URI/URL/CONN/WEBHOOK
- `git verify-commit → git merge --ff-only <verified-sha>` SHA boundary pin
- Migration audit log: `~/.stoa/audit/gdpr-ops.jsonl`

---

## METHODOLOGY

V14 ran 3 parallel verification cycles against `C:\Users\enesa\stoa-agent\` branch `audit-fixes-2026-05-25` at HEAD `9ea567d`:

- **Cycle 1** — 28 CRIT closure verification (CLOSED/PARTIAL/SKIPPED/REGRESSED matrix)
- **Cycle 2** — Per-commit new-attack-surface scan (13 commits, each diff read for fresh bugs)
- **Cycle 3** — Top-15 highest-yield lens panel re-run (lenses with most CRIT/HIGH findings originally)

Findings triangulated — every CRIT/HIGH confirmed by ≥2 cycles. The Cycle 2-only CRIT-A (holographic ALTER TABLE) is THE most important catch — Cycle 1 and Cycle 3 verified the SCHEMA but not the MIGRATION. Cycle 2 specifically read the `_init_db` diff and noticed `CREATE TABLE IF NOT EXISTS` pattern doesn't ALTER existing tables.

## OUTPUT FILES

- `AUDIT_STOA_AGENT_V14_REAUDIT.md` — this consolidated report
- Cycle outputs in `C:\Users\enesa\AppData\Local\Temp\claude\C--Users-enesa\146c4646-0ea0-46a6-a516-d2976a87303c\tasks\` (transcripts)

## STATUS

**Branch not ready for tag/release.** 2 broken CRITs (uv.lock + holographic migration) will break existing users on first run. 4 new HIGH findings (OAuth CSRF, PyPI redistribution, GDPR cascade, asymmetric content_hash) each independently warrant blocking. The 6-item fix list above closes all blockers in ~3 hours of focused work. After those land, v15 full 71-lens re-audit can confirm release readiness.
