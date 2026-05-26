# Changelog

All notable changes to STOA Agent are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/) on the Python
package side and CalVer (`vYYYY.M.D[.patch]`) on the git-tag side.

Tags map to PyPI versions one-to-one: `v2026.5.26` → `0.14.1`, `v2026.5.26.1` → `0.14.2`, and so on.

---

## [Unreleased]

### Added
- Loop 6 — `STOA` brand sweep in code comments (`agent/conversation_loop.py`, `agent/credential_sources.py`, `agent/skill_utils.py`, `cli.py`) where the comments were describing the agent runtime architecture in upstream terms. Internal-only; user-visible behaviour unchanged.

### Changed
- Identity surface strengthened so the agent never volunteers upstream provenance in any reply — see SOUL "Hard rule on provenance / upstream / fork" block (`stoa_cli/default_soul.py`, `docker/SOUL.md`, `stoa_cli/doctor.py`).

### Notes
- Node.js 20 inside several `actions/*` action binaries is being forced to Node.js 24 by GitHub on **2026-06-02**. The actions themselves are already pinned to their latest releases (`actions/setup-node@v4`, `actions/checkout@v6.0.2`, etc.) — the deprecation is about the action's internal runtime, controlled by the action maintainers. When upstream releases Node-24-capable versions, bump the SHA pins; until then, the warning is informational.

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
