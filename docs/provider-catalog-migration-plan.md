# Bazarr Provider Catalog Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate every subtitle provider from `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/` into this MIT-licensed Provider Hub catalog without losing provider behavior.

**Architecture:** Treat Bazarr's GPL provider source as behavior evidence, not as code to copy. Each provider gets one isolated branch and one isolated worktree, with fixtures, tests, manifest, catalog rebuild, local SDK validation, test-server install, and compat search/download/stream proof before merge. Platform gaps that affect many providers are handled first in their own branches so provider branches stay narrow.

**Tech Stack:** Python 3.11+, Provider Hub V1 worker contract, `sdk build-catalog`, `sdk validate`, `unittest`, per-provider fixtures, Bazarr test server compat endpoint.

---

## Evidence Snapshot

- Source inventory: 60 provider modules with provider classes were found under `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/`.
- Excluded helper modules: `__init__.py`, `_agent_list.py`, `avistaz_network.py`, `mixins.py`, `opensubtitles_scraper.py`, `utils.py`.
- Current catalog inventory: 12 bundles exist, but none use the same provider ids as the 60 built-in Bazarr providers in this migration list.
- License boundary: `/home/lavx/bazarr/LICENSE` is GPL-3.0. This catalog is MIT. Provider implementations in this repo must be clean-room MIT rewrites, not copied or mechanically translated GPL provider files.
- Provider Hub V1 contract currently calls `search(video: dict, languages: list[dict], config: dict)` and `download(provider_payload: dict, language: dict, config: dict)`.

## Current Execution State

- Planning branch: `provider-migration-inventory`
- Planning worktree: `/tmp/bazarr_catalog-provider-migration-inventory`
- Provider worktree root: `/tmp/bazarr_catalog_provider_worktrees`
- Core migration prerequisite branch: `provider-hub-builtin-migration` in `/tmp/bazarr_provider_hub_builtin_migration`
- Existing provider worktrees:
  - `gestdown`: branch `catalog-gestdown`, worktree `/tmp/bazarr_catalog_provider_worktrees/gestdown`, current head `c74a706 Fix Gestdown language parity`
  - `addic7ed`: branch `catalog-addic7ed`, worktree `/tmp/bazarr_catalog_provider_worktrees/addic7ed`, currently clean at `origin/main`
  - `regielive`: branch `catalog-regielive`, worktree `/tmp/bazarr_catalog_provider_worktrees/regielive`, current head `fb5a2ae Add RegieLive provider`
  - `shooter`: branch `catalog-shooter`, worktree `/tmp/bazarr_catalog_provider_worktrees/shooter`, current head `776fdb2 Add Shooter provider`
  - `subtis`: branch `catalog-subtis`, worktree `/tmp/bazarr_catalog_provider_worktrees/subtis`, current head `5f2b268 Add Subtis provider`
  - `wizdom`: branch `catalog-wizdom`, worktree `/tmp/bazarr_catalog_provider_worktrees/wizdom`, current head `e25f7af Add Wizdom provider`
  - `tvsubtitles`: branch `catalog-tvsubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/tvsubtitles`, current head `f63bcb7 Add TVsubtitles provider`
  - `subtitulamostv`: branch `catalog-subtitulamostv`, worktree `/tmp/bazarr_catalog_provider_worktrees/subtitulamostv`, current head `f75eafd Add SubtitulamosTV provider`
  - `greeksubs`: branch `catalog-greeksubs`, worktree `/tmp/bazarr_catalog_provider_worktrees/greeksubs`, current head `1ec84fa Add GreekSubs provider`
  - `animekalesi`: branch `catalog-animekalesi`, worktree `/tmp/bazarr_catalog_provider_worktrees/animekalesi`, current head `b5db085 Add AnimeKalesi provider`
- The current checkout `/home/lavx/Documents/bazarr_catalog` is not a provider migration workspace. Do not implement providers there.
- Before implementing the next provider, verify whether its worktree already exists with `git worktree list --porcelain`; reuse it if it exists.

## Provider Progress Ledger

### `gestdown`

- Branch: `catalog-gestdown`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/gestdown`
- Current checkpoint: `c74a706 Fix Gestdown language parity`
- Local evidence: provider tests, catalog validation, full tests, live smoke, and core manifest parse check passed on 2026-05-29.
- Remaining gate: live Provider Hub compat proof after the trusted built-in migration core branch is deployable on the Bazarr test server.

### `regielive`

- Branch: `catalog-regielive`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/regielive`
- Current checkpoint: `fb5a2ae Add RegieLive provider`
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_regielive.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check`: clean before commit.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider regielive --language ron --video-fixture tests/fixtures/regielive_video_dune.json --expect-min-results 1 --skip-download` failed with `regielive search failed: regielive rejected the request`.
  - Direct API probes from local network and `bazarr-ui-test` returned HTTP `403`.
- Remaining gates:
  - Determine whether RegieLive currently requires a different public request shape, allows only specific egress IPs, or has retired the Bazarr API key.
  - Add `regielive` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.

### `shooter`

- Branch: `catalog-shooter`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/shooter`
- Current checkpoint: `776fdb2 Add Shooter provider`
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_shooter.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live Shooter API evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider shooter --language eng --video-fixture /tmp/shooter_live_video_man_of_steel.json --expect-min-results 1`: `shooter ok`.
  - Direct worker-shaped live byte check returned `3` results and downloaded `110646` subtitle bytes.
- Remaining gates:
  - Add `shooter` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.
  - Deploy a current test-server-compatible core branch to `bazarr-ui-test`; the earlier core branch deployment attempt was based on an old Bazarr revision and hit a database migration mismatch.
  - Prove Provider Hub compat using a library-backed video that lets Bazarr compute `video.hashes.shooter`, because Shooter does not support title-only searches.

### `subtis`

- Branch: `catalog-subtis`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subtis`
- Current checkpoint: `5f2b268 Add Subtis provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_subtis.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live Subtis API evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider subtis --language spa --video-fixture tests/fixtures/subtis_video_man_of_steel.json --expect-min-results 1`: `subtis ok`.
  - Direct worker-shaped live byte check returned `1` result via `alternative` fallback and downloaded `100666` subtitle bytes.
- Remaining gates:
  - Add `subtis` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.
  - Deploy a current test-server-compatible core branch to `bazarr-ui-test`.
  - Prove Provider Hub compat search, download, and stream for the exact Subtis candidate.

### `wizdom`

- Branch: `catalog-wizdom`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/wizdom`
- Current checkpoint: `e25f7af Add Wizdom provider`
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_wizdom.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider wizdom --language heb --video-fixture tests/fixtures/wizdom_video_inception.json --expect-min-results 1` failed with `wizdom search failed: The read operation timed out`.
  - Direct `curl` probes to `https://wizdom.xyz/`, `https://wizdom.xyz/api/releases/tt1375666`, and `http://wizdom.xyz/api/releases/tt1375666` returned Cloudflare HTTP `522` after about `19` seconds from both the local workstation and `bazarr-ui-test`.
  - TMDB lookup for the same Inception fixture returned HTTP `200`, so the current live blocker is Wizdom origin availability, not TMDB.
- Remaining gates:
  - Re-run live Wizdom smoke when `wizdom.xyz` stops returning Cloudflare `522`.
  - Add `wizdom` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `tvsubtitles`

- Branch: `catalog-tvsubtitles`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/tvsubtitles`
- Current checkpoint: `f63bcb7 Add TVsubtitles provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_tvsubtitles.py'`: `10` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider tvsubtitles --language eng --video-fixture tests/fixtures/tvsubtitles_video_the_office_s01e02.json --expect-min-results 1`: `tvsubtitles ok`.
  - First live smoke found a script redirect path containing a space; regression coverage now quotes that path before fetching the ZIP.
- Remaining gates:
  - Add `tvsubtitles` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `subtitulamostv`

- Branch: `catalog-subtitulamostv`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subtitulamostv`
- Current checkpoint: `f75eafd Add SubtitulamosTV provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_subtitulamostv.py'`: `16` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `344` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider subtitulamostv --language eng --video-fixture tests/fixtures/subtitulamostv_video_the_last_ship_s05e10.json --expect-min-results 1`: `subtitulamostv ok`.
  - The original The Last of Us fixture remains covered by parser fixtures, but the live site search did not return that show, so the live gate uses The Last Ship S05E10.
- Remaining gates:
  - Add `subtitulamostv` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `greeksubs`

- Branch: `catalog-greeksubs`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/greeksubs`
- Current checkpoint: `1ec84fa Add GreekSubs provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_greeksubs.py'`: `8` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `336` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider greeksubs --language ell --video-fixture tests/fixtures/greeksubs_video_dune.json --expect-min-results 1`: `greeksubs ok`.
- Remaining gates:
  - Add `greeksubs` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.

### `animekalesi`

- Branch: `catalog-animekalesi`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/animekalesi`
- Current checkpoint: `b5db085 Add AnimeKalesi provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_animekalesi.py'`: failed because `providers/animekalesi/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_animekalesi.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `git diff --cached --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider animekalesi --language tur --video-fixture tests/fixtures/animekalesi_video_jujutsu_kaisen_2_e01.json --expect-min-results 1`: `animekalesi ok`.
- Remaining gates:
  - Add `animekalesi` to the trusted built-in migration allow-list in the Bazarr core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

## Why OpenSubtitles.org Is Tricky

- `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles.py` contains a self-contained legacy `.org` XML-RPC implementation, but the current Bazarr behavior also mixes in `OpenSubtitlesScraperMixin`.
- `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles_scraper.py` calls a separate scraper service. That helper service exists at `/home/lavx/bazarr/opensubtitles-scraper/` and is MIT licensed.
- The migration target must preserve current behavior by treating the scraper service as the default `.org` path. Do not rebuild browser scraping inside the Provider Hub worker.
- Keep legacy XML-RPC login fields out of the default schema unless a current live XML-RPC search and download path is proven usable. Otherwise the provider would expose dead settings and still fail users.

## Non-Negotiable Rules

- Never mix two providers in one branch, one commit, one PR, or one worktree.
- Never copy provider source, comments, constants that are not public protocol values, regexes, parser structure, or tests from the GPL Bazarr tree into this MIT catalog.
- Use Bazarr source only to extract behavior requirements: supported media, settings, auth shape, match behavior, archive behavior, hash behavior, and edge cases.
- Public API field names, documented endpoint URLs, public language codes, and public protocol values may be used because they are external interface facts.
- Each provider branch must touch only that provider's folder, provider fixtures, provider tests, generated `catalog.json`, README/catalog docs for that provider, and narrowly scoped shared helpers that were planned for that provider.
- If a shared helper is needed by more than one provider, land it first in a separate shared branch, then rebase provider branches onto it.
- Every provider must prove local parser behavior and live compat behavior before it is called done.
- Store secrets only in local environment variables or Bazarr test-server settings. Do not write API keys, passwords, cookies, tokens, or scraper credentials to docs, fixtures, commits, or PR messages.

## Platform Prerequisites

### Task P0: Built-In Provider Migration Switch

**Files:**
- Bazarr core branch, not a provider catalog branch.
- Inspect: `/home/lavx/bazarr/bazarr/provider_hub/manifest.py`
- Inspect: `/home/lavx/bazarr/bazarr/provider_hub/registry.py`
- Inspect: `/home/lavx/bazarr/bazarr/provider_hub/service.py`
- Verified implementation: `/tmp/bazarr_provider_hub_builtin_migration`
- Verified branch: `provider-hub-builtin-migration`

- [x] **Step 1: Create or reuse the Bazarr core worktree**

```bash
git worktree add /tmp/bazarr_provider_hub_builtin_migration -b provider-hub-builtin-migration origin/master
```

- [x] **Step 2: Add an explicit trusted migration mode**

Current Bazarr core rejects Provider Hub manifests whose `provider_id` shadows a built-in provider and skips any active installation with that id during registry registration. This blocks every provider in this migration queue from being proven through the real Provider Hub path until the provider id is explicitly migrated.

The verified branch implements a narrow migration mode that allows a trusted catalog entry to replace a built-in provider only when the provider id is present in `bazarr/provider_hub/migration.py`:

```python
MIGRATED_BUILT_IN_PROVIDER_IDS = frozenset({
    "gestdown",
})
```

- [x] **Step 3: Preserve default safety**

Untrusted catalogs must still be blocked from shadowing built-ins. The default behavior for non-migrated built-ins must stay unchanged.

- [x] **Step 4: Add regression tests**

The tests must prove:

- Untrusted `gestdown` manifest is rejected while built-in `gestdown` exists.
- Trusted `gestdown` manifest is accepted when `gestdown` is in the migration allow-list.
- Registry registers the trusted Provider Hub `gestdown` class instead of silently skipping it.
- Existing non-shadow plugin providers still register normally.

- [x] **Step 5: Verify the core branch**

```bash
cd /tmp/bazarr_provider_hub_builtin_migration
python3 -m pytest tests/bazarr/test_provider_hub.py -k 'migrated or shadow or trusted'
python3 -m pytest tests/bazarr/test_provider_hub.py
```

Observed on 2026-05-29:

- `6 passed, 86 deselected` for the targeted trusted/shadow/migrated slice.
- `92 passed` for the full Provider Hub test file.

- [ ] **Step 6: Deploy to the Bazarr test server before provider compat proof**

Provider branches whose ids match built-ins cannot complete the live compat gate until this core branch is deployed to `bazarr-ui-test`.

### Task P1: Provider Worktree Discipline

**Files:**
- Read: `.gitignore`
- No tracked file changes unless a project-local `.worktrees/` directory is adopted.

- [ ] **Step 1: Create a provider worktree root outside the repo**

```bash
mkdir -p /tmp/bazarr_catalog_provider_worktrees
```

- [ ] **Step 2: For each provider, create exactly one worktree**

```bash
git worktree add /tmp/bazarr_catalog_provider_worktrees/<provider_id> -b catalog-<provider_id> origin/main
```

- [ ] **Step 3: Verify branch isolation before editing**

```bash
git -C /tmp/bazarr_catalog_provider_worktrees/<provider_id> status --short --branch
```

Expected: branch is `catalog-<provider_id>` and the worktree is clean.

### Task P2: Clean-Room Rewrite Protocol

**Files:**
- Create per provider: `docs/provider-notes/<provider_id>.md`
- Create per provider: `tests/test_<provider_id>.py`
- Create per provider: `tests/fixtures/<provider_id>_*`
- Create per provider: `providers/<provider_id>/provider.py`
- Create per provider: `providers/<provider_id>/provider.json`

- [ ] **Step 1: Write behavior notes before provider code**

Each notes file must contain these sections:

```markdown
# <Provider Name> Migration Notes

## Public Source

- Site or API base URL:
- Public docs URL, if any:
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/<provider_id>.py`

## Required Behavior

- Media:
- Languages:
- Auth and settings:
- Search inputs:
- Download flow:
- Archive handling:
- Hash or FPS behavior:
- Anti-bot or helper-service behavior:

## Clean-Room Boundary

- GPL source was used only for behavior inventory.
- Provider implementation is a new MIT rewrite using public HTTP/API behavior and captured fixtures.
- No GPL parser code, regex structure, comments, or tests were copied.
```

- [ ] **Step 2: Write failing parser and worker-contract tests**

Each provider gets tests that import only `providers/<provider_id>/provider.py` and use local fixtures. Tests must cover query building, search parsing, empty search, download path, language mapping, and any provider-specific setting.

- [ ] **Step 3: Implement the provider from fixtures and public behavior**

Keep helpers pure where possible. Keep `provider_payload` serializable and free of secrets, cookies, full HTML, or subtitle bytes.

- [ ] **Step 4: Run local gates**

```bash
python3 -B -m unittest tests/test_<provider_id>.py
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
python3 -B -m unittest discover -s tests
```

- [ ] **Step 5: Run live Provider Hub and compat gates**

Install or update the provider on the Bazarr test server, restart if prompted, then prove:

```text
/api/v1/subtitles returns the provider entry
/api/v1/download returns a stream link for that exact file_id
/api/v1/download/stream/<token> returns HTTP 200 and non-empty subtitle bytes
recent Bazarr logs show no provider worker exception
```

### Task P3: Worker Contract Extension For Local-File Providers

**Files:**
- Bazarr core branch, not this catalog branch.
- Provider ids affected: `embeddedsubtitles`, `whisperai`.

- [ ] **Step 1: Create a Bazarr core worktree**

```bash
git worktree add /tmp/bazarr_provider_hub_local_contract -b feat/provider-hub-local-video-contract origin/main
```

- [ ] **Step 2: Extend Provider Hub worker input**

The worker `video` dict must expose the fields required by local-file providers:

```json
{
  "kind": "movie",
  "path": "/absolute/path/to/video.mkv",
  "size": 123456789,
  "hashes": {"opensubtitles": "optional-hash"},
  "fps": 23.976
}
```

- [ ] **Step 3: Add trust and path controls**

Only installed trusted provider bundles may receive local file paths. The worker must reject path traversal and paths outside configured media roots.

- [ ] **Step 4: Verify with a smoke local provider**

Add a temporary smoke provider that reads only metadata, not file content, then remove it before merge if a real local provider branch is ready.

### Task P4: Shared Archive Extraction Foundation

**Files:**
- Create if needed: `providers/_shared_archive.py`
- Tests: `tests/test_shared_archive.py`

- [ ] **Step 1: Add shared archive helpers only when the first archive provider needs them**

The helper must detect ZIP, RAR, 7z, GZIP, and raw subtitle bytes by content bytes, not filename alone.

- [ ] **Step 2: Prefer bundled wheel dependencies**

Use hash-locked wheels in each provider manifest. Do not rely on system `7z`, `unrar`, `ffmpeg`, or app-environment packages unless the Provider Hub contract explicitly provides them.

### Task P5: OpenSubtitles.org Helper-Service Boundary

**Files:**
- Provider branch: `catalog-opensubtitles`
- Provider worktree: `/tmp/bazarr_catalog_provider_worktrees/opensubtitles`
- Source behavior: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles.py`
- Helper behavior: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles_scraper.py`
- Helper project license: `/home/lavx/bazarr/opensubtitles-scraper/LICENSE`

- [ ] **Step 1: Treat `.org` as helper-service first**

Bazarr settings force `use_web_scraper` because OpenSubtitles.org login is no longer available. The catalog provider should default to `scraper_service_url`, not XML-RPC login.

- [ ] **Step 2: Preserve legacy feature shape without making dead login the default**

Expose `use_tag_search`, `skip_wrong_fps`, `only_foreign`, and `also_foreign` settings if the helper API supports them. Keep username, password, VIP, SSL, and XML-RPC timeout out of the default schema unless a current live `.org` XML-RPC path is proven usable.

- [ ] **Step 3: Keep helper-service failures explicit**

Search must fail with a clear provider error when the scraper service is unavailable, throttled, or returns malformed JSON. It must not silently fall back to browser automation inside the worker.

## Provider Execution Queue

Each row is one branch, one worktree, one provider PR, and one independent validation pass.

| Order | Provider | Source file | Branch | Worktree | Media | Config surface | Risk/features |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `gestdown` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/gestdown.py` | `catalog-gestdown` | `/tmp/bazarr_catalog_provider_worktrees/gestdown` | episode | none | plain HTTP API |
| 2 | `regielive` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/regielive.py` | `catalog-regielive` | `/tmp/bazarr_catalog_provider_worktrees/regielive` | movie, episode | none | plain HTTP API, archive download |
| 3 | `shooter` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/shooter.py` | `catalog-shooter` | `/tmp/bazarr_catalog_provider_worktrees/shooter` | movie, episode | none | hash-oriented API |
| 4 | `subtis` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subtis.py` | `catalog-subtis` | `/tmp/bazarr_catalog_provider_worktrees/subtis` | movie | none | plain HTTP API |
| 5 | `wizdom` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/wizdom.py` | `catalog-wizdom` | `/tmp/bazarr_catalog_provider_worktrees/wizdom` | movie, episode | none | API, TMDB lookup, archive download |
| 6 | `tvsubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/tvsubtitles.py` | `catalog-tvsubtitles` | `/tmp/bazarr_catalog_provider_worktrees/tvsubtitles` | episode | none | upstream wrapper rewrite |
| 7 | `subtitulamostv` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subtitulamostv.py` | `catalog-subtitulamostv` | `/tmp/bazarr_catalog_provider_worktrees/subtitulamostv` | episode | none | upstream wrapper rewrite |
| 8 | `greeksubs` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/greeksubs.py` | `catalog-greeksubs` | `/tmp/bazarr_catalog_provider_worktrees/greeksubs` | movie, episode | none | HTML scrape |
| 9 | `animekalesi` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/animekalesi.py` | `catalog-animekalesi` | `/tmp/bazarr_catalog_provider_worktrees/animekalesi` | episode | none | archive |
| 10 | `animesubinfo` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/animesubinfo.py` | `catalog-animesubinfo` | `/tmp/bazarr_catalog_provider_worktrees/animesubinfo` | movie, episode | none | archive |
| 11 | `greeksubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/greeksubtitles.py` | `catalog-greeksubtitles` | `/tmp/bazarr_catalog_provider_worktrees/greeksubtitles` | movie, episode | none | archive |
| 12 | `hosszupuska` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/hosszupuska.py` | `catalog-hosszupuska` | `/tmp/bazarr_catalog_provider_worktrees/hosszupuska` | episode | none | archive |
| 13 | `nekur` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/nekur.py` | `catalog-nekur` | `/tmp/bazarr_catalog_provider_worktrees/nekur` | movie | none | archive |
| 14 | `prijevodionline` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/prijevodionline.py` | `catalog-prijevodionline` | `/tmp/bazarr_catalog_provider_worktrees/prijevodionline` | episode | none | archive |
| 15 | `soustitreseu` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/soustitreseu.py` | `catalog-soustitreseu` | `/tmp/bazarr_catalog_provider_worktrees/soustitreseu` | movie, episode | none | archive |
| 16 | `subclub` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subclub.py` | `catalog-subclub` | `/tmp/bazarr_catalog_provider_worktrees/subclub` | movie, episode | none | archive |
| 17 | `subssabbz` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subssabbz.py` | `catalog-subssabbz` | `/tmp/bazarr_catalog_provider_worktrees/subssabbz` | movie, episode | none | archive |
| 18 | `subsunacs` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subsunacs.py` | `catalog-subsunacs` | `/tmp/bazarr_catalog_provider_worktrees/subsunacs` | movie, episode | none | archive |
| 19 | `subsynchro` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subsynchro.py` | `catalog-subsynchro` | `/tmp/bazarr_catalog_provider_worktrees/subsynchro` | movie | none | archive |
| 20 | `subtitrarinoi` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subtitrarinoi.py` | `catalog-subtitrarinoi` | `/tmp/bazarr_catalog_provider_worktrees/subtitrarinoi` | movie, episode | none | archive |
| 21 | `subtitriid` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subtitriid.py` | `catalog-subtitriid` | `/tmp/bazarr_catalog_provider_worktrees/subtitriid` | movie | none | archive |
| 22 | `supersubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/supersubtitles.py` | `catalog-supersubtitles` | `/tmp/bazarr_catalog_provider_worktrees/supersubtitles` | movie, episode | none | archive |
| 23 | `titrari` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/titrari.py` | `catalog-titrari` | `/tmp/bazarr_catalog_provider_worktrees/titrari` | movie, episode | none | archive |
| 24 | `yavkanet` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/yavkanet.py` | `catalog-yavkanet` | `/tmp/bazarr_catalog_provider_worktrees/yavkanet` | movie, episode | none | archive |
| 25 | `yifysubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/yifysubtitles.py` | `catalog-yifysubtitles` | `/tmp/bazarr_catalog_provider_worktrees/yifysubtitles` | movie | none | archive |
| 26 | `animetosho` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/animetosho.py` | `catalog-animetosho` | `/tmp/bazarr_catalog_provider_worktrees/animetosho` | episode | `search_threshold` | API, archive |
| 27 | `napiprojekt` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/napiprojekt.py` | `catalog-napiprojekt` | `/tmp/bazarr_catalog_provider_worktrees/napiprojekt` | movie, episode | `only_authors`, `only_real_names` | upstream rewrite, author filters |
| 28 | `podnapisi` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/podnapisi.py` | `catalog-podnapisi` | `/tmp/bazarr_catalog_provider_worktrees/podnapisi` | movie, episode | `only_foreign`, `also_foreign`, `verify_ssl` | API, foreign subtitle filters |
| 29 | `subf2m` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subf2m.py` | `catalog-subf2m` | `/tmp/bazarr_catalog_provider_worktrees/subf2m` | movie, episode | `verify_ssl`, `user_agent` | archive, custom session |
| 30 | `subsarr` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subsarr.py` | `catalog-subsarr` | `/tmp/bazarr_catalog_provider_worktrees/subsarr` | movie, episode | `base_url` | self-hosted API |
| 31 | `assrt` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/assrt.py` | `catalog-assrt` | `/tmp/bazarr_catalog_provider_worktrees/assrt` | movie, episode | `token` | authenticated API |
| 32 | `betaseries` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/betaseries.py` | `catalog-betaseries` | `/tmp/bazarr_catalog_provider_worktrees/betaseries` | episode | `token` | authenticated API, archive |
| 33 | `hdbits` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/hdbits.py` | `catalog-hdbits` | `/tmp/bazarr_catalog_provider_worktrees/hdbits` | movie, episode | `username`, `passkey` | authenticated API, archive |
| 34 | `jimaku` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/jimaku.py` | `catalog-jimaku` | `/tmp/bazarr_catalog_provider_worktrees/jimaku` | movie, episode | `api_key`, `enable_name_search_fallback`, `enable_archives_download`, `enable_ai_subs` | API, archive, AI flag |
| 35 | `subdl` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subdl.py` | `catalog-subdl` | `/tmp/bazarr_catalog_provider_worktrees/subdl` | movie, episode | `api_key`, `anime_mode` | API, archive, anime mode |
| 36 | `subsource` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subsource.py` | `catalog-subsource` | `/tmp/bazarr_catalog_provider_worktrees/subsource` | movie, episode | `api_key` | API, archive |
| 37 | `subsro` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subsro.py` | `catalog-subsro` | `/tmp/bazarr_catalog_provider_worktrees/subsro` | movie, episode | `api_key` | API, archive |
| 38 | `subx` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subx.py` | `catalog-subx` | `/tmp/bazarr_catalog_provider_worktrees/subx` | movie, episode | `api_key` | API, archive |
| 39 | `opensubtitlescom` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitlescom.py` | `catalog-opensubtitlescom` | `/tmp/bazarr_catalog_provider_worktrees/opensubtitlescom` | movie, episode | `username`, `password`, `use_hash`, `include_ai_translated`, `include_machine_translated`, `api_key` | official API, hash, quota, AI flags |
| 40 | `avistaz` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/avistaz.py` | `catalog-avistaz` | `/tmp/bazarr_catalog_provider_worktrees/avistaz` | inspect base | `cookies`, `user_agent` | shared AvistaZ network behavior |
| 41 | `cinemaz` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/cinemaz.py` | `catalog-cinemaz` | `/tmp/bazarr_catalog_provider_worktrees/cinemaz` | inspect base | `cookies`, `user_agent` | shared AvistaZ network behavior |
| 42 | `turkcealtyaziorg` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/turkcealtyaziorg.py` | `catalog-turkcealtyaziorg` | `/tmp/bazarr_catalog_provider_worktrees/turkcealtyaziorg` | movie, episode | `cookies`, `user_agent` | cookie auth, archive |
| 43 | `addic7ed` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/addic7ed.py` | `catalog-addic7ed` | `/tmp/bazarr_catalog_provider_worktrees/addic7ed` | movie, episode | `username`, `password`, `cookies`, `user_agent`, `is_vip` | auth, anti-bot |
| 44 | `karagarga` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/karagarga.py` | `catalog-karagarga` | `/tmp/bazarr_catalog_provider_worktrees/karagarga` | movie | `username`, `password`, `f_username`, `f_password` | dual login |
| 45 | `ktuvit` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/ktuvit.py` | `catalog-ktuvit` | `/tmp/bazarr_catalog_provider_worktrees/ktuvit` | movie, episode | `email`, `hashed_password` | authenticated service |
| 46 | `legendasdivx` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/legendasdivx.py` | `catalog-legendasdivx` | `/tmp/bazarr_catalog_provider_worktrees/legendasdivx` | movie, episode | `username`, `password`, `skip_wrong_fps` | auth, archive, FPS |
| 47 | `legendasnet` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/legendasnet.py` | `catalog-legendasnet` | `/tmp/bazarr_catalog_provider_worktrees/legendasnet` | movie, episode | `username`, `password` | auth, archive |
| 48 | `napisy24` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/napisy24.py` | `catalog-napisy24` | `/tmp/bazarr_catalog_provider_worktrees/napisy24` | movie, episode | `username`, `password` | auth, archive |
| 49 | `pipocas` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/pipocas.py` | `catalog-pipocas` | `/tmp/bazarr_catalog_provider_worktrees/pipocas` | movie, episode | `username`, `password` | auth, archive |
| 50 | `subscenter` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subscenter.py` | `catalog-subscenter` | `/tmp/bazarr_catalog_provider_worktrees/subscenter` | movie, episode | `username`, `password` | auth, archive |
| 51 | `titlovi` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/titlovi.py` | `catalog-titlovi` | `/tmp/bazarr_catalog_provider_worktrees/titlovi` | movie, episode | `username`, `password` | auth, archive |
| 52 | `titulky` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/titulky.py` | `catalog-titulky` | `/tmp/bazarr_catalog_provider_worktrees/titulky` | movie, episode | `username`, `password`, `approved_only`, `skip_wrong_fps` | auth, archive, FPS |
| 53 | `xsubs` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/xsubs.py` | `catalog-xsubs` | `/tmp/bazarr_catalog_provider_worktrees/xsubs` | episode | `username`, `password` | auth |
| 54 | `subs4free` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subs4free.py` | `catalog-subs4free` | `/tmp/bazarr_catalog_provider_worktrees/subs4free` | movie | none | archive, anti-bot |
| 55 | `subs4series` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subs4series.py` | `catalog-subs4series` | `/tmp/bazarr_catalog_provider_worktrees/subs4series` | episode | none | archive, anti-bot |
| 56 | `zimuku` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/zimuku.py` | `catalog-zimuku` | `/tmp/bazarr_catalog_provider_worktrees/zimuku` | movie, episode | none | archive, anti-bot |
| 57 | `opensubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles.py` | `catalog-opensubtitles` | `/tmp/bazarr_catalog_provider_worktrees/opensubtitles` | movie, episode | `scraper_service_url`, `use_tag_search`, `skip_wrong_fps`, `only_foreign`, `also_foreign` | OpenSubtitles.org helper service |
| 58 | `embeddedsubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/embeddedsubtitles.py` | `catalog-embeddedsubtitles` | `/tmp/bazarr_catalog_provider_worktrees/embeddedsubtitles` | movie, episode | `included_codecs`, `hi_fallback`, `timeout`, `unknown_as_fallback`, `fallback_lang` | local video path, ffprobe, ffmpeg |
| 59 | `whisperai` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/whisperai.py` | `catalog-whisperai` | `/tmp/bazarr_catalog_provider_worktrees/whisperai` | movie, episode | `endpoint`, `response`, `timeout`, `loglevel`, `pass_video_name` | local video path, external AI service, ffmpeg |
| 60 | `bsplayer` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/bsplayer.py` | `catalog-bsplayer` | `/tmp/bazarr_catalog_provider_worktrees/bsplayer` | movie, episode | none | SOAP API, hash behavior |

## Per-Provider Completion Gate

A provider is complete only when the provider branch has all of this evidence:

- `providers/<provider_id>/provider.json` has accurate `provider_id`, `name`, `version`, `supported_media`, `languages`, `config_schema`, `secret_fields`, dependency locks, file hashes, and bundle hash.
- `providers/<provider_id>/provider.py` is a clean-room MIT implementation.
- `tests/test_<provider_id>.py` covers parser behavior and worker-shaped `search()` and `download()`.
- Captured fixtures exist under `tests/fixtures/` and contain no secrets.
- `python3 -B -m unittest tests/test_<provider_id>.py` passes.
- `python3 -B -m sdk build-catalog` produces the committed `catalog.json`.
- `python3 -B -m sdk validate` passes.
- `python3 -B -m unittest discover -s tests` passes or any unrelated existing failures are recorded with exact evidence.
- Live smoke returns at least one real result.
- Test Bazarr installs or updates the provider and restarts when required.
- Compat `/api/v1/subtitles`, `/api/v1/download`, and `/api/v1/download/stream/<token>` pass for that provider.
- The branch contains only that provider's files and planned shared helper changes.

## Self-Review

- Spec coverage: every provider module with a provider class in `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/` is present in the execution queue.
- Worktree coverage: every provider row has a unique branch and unique `/tmp/bazarr_catalog_provider_worktrees/<provider_id>` path.
- License coverage: the plan blocks GPL source copying and requires clean-room MIT provider code.
- Feature coverage: auth, API, archive, anti-bot, hash, FPS, helper service, and local-file behavior are identified before implementation begins.
- OpenSubtitles.org coverage: the plan explains why `.org` is tricky and treats the current scraper-service mode as the default behavior.
