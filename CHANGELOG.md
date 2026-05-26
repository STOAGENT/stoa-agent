# Changelog

All notable changes to STOA Agent are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) on the Python
package side and CalVer (`vYYYY.M.D[.patch]`) on the git-tag side.

Tags map to PyPI versions one-to-one: `v2026.5.26` → `0.14.1`, `v2026.5.26.1` → `0.14.2`, and so on.

---

## [Unreleased]

(nothing yet)

---

## [0.14.3] — 2026-05-26 — tag `v2026.5.26.2`

The "deferred-work bash-through" release — every item in the post-0.14.2 backlog landed in a single night. Twelve loops, eight PRs (Loop 2 was a non-PR GH-release-create operation), three dependabot merges + two defers, one final version bump.

### Added

- **`stoa mcp allow / deny / allowlist`** (Loop 3, PR #17) — one-shot CLI for MCP server gating + tool-level granularity. Accepts `<server>` (server-level) or `<server>:<tool>` (tool-level). New module `stoa_cli/mcp_allowlist.py`, dispatcher wire-in, 20 new tests (audit Lens 46 / M-11b).
- **Per-provider egress trace** (Loop 4, PR #18) — `agent/egress_trace.py` records one append-only JSONL line per outbound LLM HTTP call (`provider`, `endpoint`, `model`, `latency_ms`, `tokens_in/out`, never body / headers / API keys). Companion `stoa egress show / export` CLI. `auxiliary_client.call_llm` wired as proof-of-concept; other adapters can wire in over time. Opt-out via `STOA_EGRESS_TRACE=0`. 19 new tests including a signature-safety test that fails if a future PR adds a prompt/body/auth parameter (audit Lens 46 / M-11c).
- **`CHANGELOG.md`** (Loop 7, PR #16) — first repo-rooted changelog with Keep-A-Changelog formatting and per-version PyPI-↔-CalVer-tag mapping.
- **Hard provenance / fork rule in SOUL** (Loop 1, PR #15) — both `stoa_cli/default_soul.py` and `docker/SOUL.md` now carry an explicit banlist of phrases the agent must never volunteer in any language (`"Nous Research"`, `"NousResearch"`, `"hermes-agent"`, `"fork of"`, ...). Provenance questions get exactly one redirect line pointing at the repo's `LICENSE` + `ATTRIBUTION.md`.

### Changed

- **Identity surface hardened across `default_soul.py`, `docker/SOUL.md`, `stoa_cli/doctor.py`** (Loop 1, PR #15) — even the doctor-generated SOUL template no longer names upstream projects when asked who built STOA.
- **Brand sweep round 2** (Loop 1, PR #15) — 6 stale URL test assertions fixed: `tests/acp/test_registry_manifest.py`, `tests/agent/test_openrouter_response_cache.py`, `tests/run_agent/test_provider_attribution_headers.py` (4 hits). Production already emitted `https://stoax.xyz`; the tests caught up.
- **Hermes → STOA in code comments** (Loop 6, PR #16) — surgical sweep of `agent/conversation_loop.py`, `agent/credential_sources.py`, `agent/skill_utils.py`, `cli.py` for comments that described the runtime architecture in upstream terms. Persona name "Hermes the dispatcher" + frontmatter schema `metadata.get("hermes")` + legacy binary alias intentionally kept.
- **Docker IMAGE_NAME** (Loop 5, PR #19) — `.github/workflows/docker-publish.yml` switched from `nousresearch/stoa-agent` (4 occurrences) to `stoagent/stoa-agent`.

### Fixed

- **Renamed `agent/transports/hermes_tools_mcp_server.py` → `stoa_tools_mcp_server.py`** (Loop 5, PR #19) — every importer expected the new name; the source file was the only thing still on the old. Test file renamed in lockstep.
- **`docker/entrypoint.sh` executable bit** (Loop 5, PR #19) — git-tracked mode was `100644`, so Linux container ENTRYPOINT failed with "permission denied" on fresh checkouts. `git update-index --chmod=+x` so the tracked tree carries the exec bit.
- **`acp_registry/agent.json`** version + package pin bumped 0.14.0 → 0.14.2 → 0.14.3 across the release chain (Loop 1, PR #15 + this release).
- **`agent/skill_utils.py::iter_skill_index_files`** (Loop 10, PR #20) — depth-1 symlink exemption. The audit v5 CRIT B-02 `followlinks=False` fix was correct but too broad — it broke every shared-team setup where `~/.stoa/skills/myskill` is a symlink to a team-owned checkout. New behaviour: depth-1 symlinks are followed (resolved + walked), nothing below that level is. Apparent paths preserved under the symlink so `_load_skill_payload`'s trusted-root check still anchors. Two newly-green tests in `test_skill_commands.py`.
- **`stoa_cli/mcp_config.py`** dispatcher gained `allow`, `deny`, `disallow`, `block`, `allowlist` handlers (Loop 3, PR #17).
- **`stoa_cli/main.py`** gained an `egress` subparser with `show` + `export` sub-subcommands (Loop 4, PR #18).

### Removed

(nothing intentional)

### Dependabot

- **Merged:** PR #1 (actions-minor-patch group), PR #4 (docker/setup-buildx-action 3.12 → 4.1), PR #5 (actions/create-github-app-token 1.9 → 3.2). All small, low-risk action SHA bumps (Loop 9).
- **Deferred (closed with explanation):** PR #2 (astral-sh/setup-uv 5.4 → 8.1, three majors) and PR #3 (actions/upload-artifact 4.6 → 7.0, three majors with known v5 artifact-merge breakage). Re-open with a targeted regression check on the wheel-build / upload-artifact path before merging.

### GitHub Releases

- v0.14.1 and v0.14.2 release entries created on the GitHub Releases page after-the-fact (Loop 2) — the original tag pushes triggered the PyPI workflow but skipped GH-Release creation because `scripts/release.py --publish` was not run.

---



## [0.14.2] — 2026-05-26 — tag `v2026.5.26.1`

### Changed
- **SOUL identity rewrite**: dropped the "fork of X" lead from the standard intro (PR #13). The agent now opens as a standalone STOA project; upstream provenance is reachable only via the repo's `LICENSE` + `ATTRIBUTION.md` files.

### Fixed
- **Homebrew formula** (PR #12) now points at the real PyPI sdist URL + sha256 (`e17d51374ac9ad91b2fee5d89166f697b75b9d49aeac096725477ea6ca16b6e6`) instead of the dead GH-Release placeholder. `brew install --formula ./packaging/homebrew/stoa-agent.rb` works.
- **Brand sweep round 2** (Loop 1 / PR #15) — 6 stale URL assertions in three test files (`tests/acp/test_registry_manifest.py`, `tests/agent/test_openrouter_response_cache.py`, `tests/run_agent/test_provider_attribution_headers.py`) updated to `https://stoax.xyz` to match production code.
- **acp_registry/agent.json** version bumped 0.14.0 → 0.14.2 to satisfy the lockstep test against `pyproject.toml`.

---

## [0.14.1] — 2026-05-26 — tag `v2026.5.26`

First STOA-fork release on PyPI. `pip install stoa-agent==0.14.1` works worldwide as of this tag.

### Added
- **Boot integrity hash** (PR #10, audit Lens 46 / M-11a) — every CLI startup appends one SHA-256 line to `~/.stoa/boot-integrity.log`: `<iso-ts> <digest> v<version> <hostname>`. Files hashed: `cli.py`, `stoa_cli/__init__.py`, `stoa_cli/boot_integrity.py`, `agent/anthropic_adapter.py`, `tools/approval.py`. Capped at 4 MiB/file so cold-start cost stays under ~50 ms. Opt out with `STOA_BOOT_INTEGRITY=0`. 8/8 tests in `tests/stoa_cli/test_boot_integrity.py`.

### Changed
- **PyPI trusted publishing** wired via OIDC (`.github/workflows/upload_to_pypi.yml`) — tag pushes matching `v20*` trigger the publish flow. In-toto build provenance attestation attached so PyPI shows the "Provenance" badge.
- **Skin engine rebrand defaults** (PR #9) — 8 tests updated:
  - `default` skin `prompt_symbol` `❯` → `›`
  - `default` skin `tool_prefix` `┊` → `·`
  - `default` skin `banner_title` `#FFD700` → `#ffe6cb` (cream replaces gold)
  - `default` skin `banner_border` `#CD7F32` → `#2a2620` (near-black warm brown replaces bronze)
  - Compact banner says `STOA Agent — six sovereign LLMs`, no `NOUS STOA` legacy string.

### Fixed
- **719-finding audit closure** (PR #6) — all CRIT + HIGH closed, ~95% MED + all reachable LOW. Sprint 1+2+3 mega stress test 25/25 pass.
- **Master infra cleanup** (PR #7) — 11 workflow `branches: [main]` → `[master]` triggers, docusaurus URL → `https://stoax.xyz`, web dashboard `STOA_DOCS_URL` constant, ruff PLW1514 in `scripts/stress_test_sprint3.py`, Windows footgun in `tools/process_registry.py`, pyproject TOML `dependencies` inside `[project]` table, `uv.lock` regenerated, `AUTHOR_MAP` entry for `stoa@stoax.xyz`, lockfile rename `stoa-parser`/`stoa-estree` back to `hermes-parser`/`hermes-estree` (over-eager brand-purge — these are Facebook's JS-engine parsers, unrelated to the agent upstream).
- **Cascade fixes** (PR #8) — `plugins/kanban/dashboard/dist/{index.js,style.css}` rebranded (494 CSS class refs `hermes-kanban-` → `stoa-kanban-`, plus root class, CSS custom properties, custom event names, MIME type, localStorage key, documentation title, tooltip strings). `ui-tui/scripts/build.mjs` esbuild alias `@hermes/ink` → `@stoa/ink` + `packages/hermes-ink/` → `packages/stoa-ink/` (was blocking the docker build). PKCE test fixture patches `getpass.getpass` not just `builtins.input`, and `FakeResponse.read()` accepts the P-07 64 KiB byte-cap. `test_cli_skin_integration` + `test_skin_engine` prompt-symbol asserts updated. `test_approval_isolation` updated to expect the post-CRIT-32-01 non-interactive default-DENY instead of the legacy auto-APPROVE.

---

## Maintenance notes

- Per-version release notes also live on the GitHub Releases page: <https://github.com/STOAGENT/stoa-agent/releases>
- Mapping between PyPI semver and CalVer git tag is recorded in each entry header above.
- `release.py` will append to this file automatically on future releases — manually written entries above this line follow the same shape.
