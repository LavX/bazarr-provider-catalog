# Bazarr Provider Catalog Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate every subtitle provider from `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/` into this MIT-licensed Provider Hub catalog without losing provider behavior.

**Architecture:** Treat Bazarr's GPL provider source as behavior evidence, not as code to copy. Each provider gets one isolated branch and one isolated worktree, with fixtures, tests, manifest, catalog rebuild, local SDK validation, test-server install, and compat search/download/stream proof before merge. Platform gaps that affect many providers are handled first in their own branches so provider branches stay narrow.

**Tech Stack:** Python 3.11+, Provider Hub V1 worker contract, `sdk build-catalog`, `sdk validate`, `unittest`, per-provider fixtures, Bazarr test server compat endpoint.

---

## Evidence Snapshot

- Source inventory: 60 provider modules with provider classes were found under `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/`.
- Excluded helper modules: `__init__.py`, `_agent_list.py`, `avistaz_network.py`, `mixins.py`, `opensubtitles_scraper.py`, `utils.py`.
- Main-branch catalog inventory: 12 bundles currently ship from `main`.
- Branch inventory: all 60 legacy provider-class modules have matching `catalog-*` branches.
- Current checkout audit on 2026-06-01: `git worktree list --porcelain` shows all 60 provider-class modules linked under `/tmp/bazarr_catalog_provider_worktrees`, plus the planning worktree. The missing-provider check against `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/` returned no missing worktrees.
- Helper coverage: `opensubtitles_scraper.py` is not a provider-class module. Its behavior is covered inside the `catalog-opensubtitles` / `opensubtitles_org` branch, but the current implementation no longer defaults to a sidecar helper.
- OpenSubtitles.org current branch evidence: `catalog-opensubtitles` at `c8f013c` uses `ai-cloudscraper==3.8.4`, inline Anubis solving, request throttling, and optional FlareSolverr fallback for Cloudflare challenges.
- Core replacement-policy evidence: Bazarr core branch `worktree-provider-hub-builtin-replacements` in `/tmp/bazarr_provider_hub_builtin_replacements`, current head `f245ae096`, contains a trusted replacement policy for 55 active migrated built-ins, the compat AniDB ID bridge needed by anime providers, and the compat NapiProjekt hash bridge. It excludes dead-origin providers `hosszupuska`, `podnapisi`, `subscenter`, and `xsubs`, and excludes legacy `opensubtitles` because the catalog rewrite ships as `opensubtitles_org`.
- Test-server core evidence: `bazarr-ui-test` was updated on 2026-05-31 to image version `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`, and returned healthy. The earlier test image based on old head `456071d10` failed because the image did not contain database migration `6c9f1b8d2e3a`; rebasing the core branch onto current `origin/development` fixed that mismatch.
- License boundary: `/home/lavx/bazarr/LICENSE` is GPL-3.0. This catalog is MIT. Provider implementations in this repo must be clean-room MIT rewrites, not copied or mechanically translated GPL provider files.
- Provider Hub V1 contract currently calls `search(video: dict, languages: list[dict], config: dict)` and `download(provider_payload: dict, language: dict, config: dict)`.

## Current Execution State

- Planning branch: `provider-migration-inventory`
- Planning worktree: `/tmp/bazarr_catalog-provider-migration-inventory`
- Provider worktree root: `/tmp/bazarr_catalog_provider_worktrees`
- Source provider-class modules: 60 after excluding `__init__.py`, `_agent_list.py`, `avistaz_network.py`, `mixins.py`, `opensubtitles_scraper.py`, and `utils.py`.
- Current linked provider worktrees: all 60 provider-class modules have dedicated worktrees under `/tmp/bazarr_catalog_provider_worktrees/<provider>`.
- Current catalog checkout inventory: 12 Provider Hub bundles are present in this planning worktree: `bollynook`, `fansubs`, `isubtitles`, `kitsunekko`, `moviesubtitles`, `my_subs`, `smoke`, `sub_scene`, `subcentral`, `subhd`, `subtitlecat`, and `subtitlestar`.
- Core migration prerequisite branch: `worktree-provider-hub-builtin-replacements` in `/tmp/bazarr_provider_hub_builtin_replacements`, current head `f245ae096`
- Bazarr test server: `bazarr-ui-test` is healthy on image version `ui-test-20260531-provider-hub-replacements-f245ae096`; runtime policy includes `bsplayer`, `gestdown`, `tvsubtitles`, `subtitulamostv`, `greeksubs`, `animekalesi`, `animesubinfo`, `animetosho`, `napiprojekt`, `subf2m`, `greeksubtitles`, `nekur`, `prijevodionline`, `soustitreseu`, `subclub`, `subssabbz`, `subsunacs`, `subsynchro`, `subtitrarinoi`, `subtitriid`, `supersubtitles`, `titrari`, `yavkanet`, `yifysubtitles`, and `subs4free`, excludes `hosszupuska` and `podnapisi`, and has 55 trusted migrated built-in ids.
- Dead-origin providers: `hosszupuska`, `podnapisi`, `subscenter`, `xsubs`. Confirmed dead for Provider Hub migration on 2026-05-31 and re-confirmed on 2026-06-01. Their branch artifacts are historical notes only. Do not ship active catalog entries, promote them to the core replacement policy, open merge-ready provider PRs, or require Provider Hub compat proof unless a verified upstream origin returns.
- OpenSubtitles.org current state: branch `catalog-opensubtitles` is clean at `c8f013c` and PR `#16` is open. Local validation, direct native search, and direct native download passed, but Bazarr compat proof is still incomplete because the compat key cannot stage or enable Provider Hub bundles and the current compat search results do not include `opensubtitles_org`.
- Before editing any provider, run `git worktree list --porcelain` and `git status --short --branch` for that provider's exact worktree.
- Existing provider worktrees:
  - `addic7ed`: branch `catalog-addic7ed`, worktree `/tmp/bazarr_catalog_provider_worktrees/addic7ed`, current head `9464c2a`
  - `animekalesi`: branch `catalog-animekalesi`, worktree `/tmp/bazarr_catalog_provider_worktrees/animekalesi`, current head `b5db085`
  - `animesubinfo`: branch `catalog-animesubinfo`, worktree `/tmp/bazarr_catalog_provider_worktrees/animesubinfo`, current head `b2be823`
  - `animetosho`: branch `catalog-animetosho`, worktree `/tmp/bazarr_catalog_provider_worktrees/animetosho`, current head `4a282a3`
  - `assrt`: branch `catalog-assrt`, worktree `/tmp/bazarr_catalog_provider_worktrees/assrt`, current head `c4e2440`
  - `avistaz`: branch `catalog-avistaz`, worktree `/tmp/bazarr_catalog_provider_worktrees/avistaz`, current head `1b339e2`
  - `betaseries`: branch `catalog-betaseries`, worktree `/tmp/bazarr_catalog_provider_worktrees/betaseries`, current head `461ab52`
  - `bsplayer`: branch `catalog-bsplayer`, worktree `/tmp/bazarr_catalog_provider_worktrees/bsplayer`, current head `c04f374`
  - `cinemaz`: branch `catalog-cinemaz`, worktree `/tmp/bazarr_catalog_provider_worktrees/cinemaz`, current head `df1b3bd`
  - `embeddedsubtitles`: branch `catalog-embeddedsubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/embeddedsubtitles`, current head `a8257a4`, local/generated provider
  - `gestdown`: branch `catalog-gestdown`, worktree `/tmp/bazarr_catalog_provider_worktrees/gestdown`, current head `c74a706`
  - `greeksubs`: branch `catalog-greeksubs`, worktree `/tmp/bazarr_catalog_provider_worktrees/greeksubs`, current head `1ec84fa`
  - `greeksubtitles`: branch `catalog-greeksubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/greeksubtitles`, current head `da99eee`
  - `hdbits`: branch `catalog-hdbits`, worktree `/tmp/bazarr_catalog_provider_worktrees/hdbits`, current head `42d7f02`
  - `hosszupuska`: branch `catalog-hosszupuska`, worktree `/tmp/bazarr_catalog_provider_worktrees/hosszupuska`, current head `5ccb3a7`, dead origin
  - `jimaku`: branch `catalog-jimaku`, worktree `/tmp/bazarr_catalog_provider_worktrees/jimaku`, current head `112d345`
  - `karagarga`: branch `catalog-karagarga`, worktree `/tmp/bazarr_catalog_provider_worktrees/karagarga`, current head `0167ba8`
  - `ktuvit`: branch `catalog-ktuvit`, worktree `/tmp/bazarr_catalog_provider_worktrees/ktuvit`, current head `9d3162e`
  - `legendasdivx`: branch `catalog-legendasdivx`, worktree `/tmp/bazarr_catalog_provider_worktrees/legendasdivx`, current head `02bbb60`
  - `legendasnet`: branch `catalog-legendasnet`, worktree `/tmp/bazarr_catalog_provider_worktrees/legendasnet`, current head `983878f`
  - `napiprojekt`: branch `catalog-napiprojekt`, worktree `/tmp/bazarr_catalog_provider_worktrees/napiprojekt`, current head `5d9fb63`
  - `napisy24`: branch `catalog-napisy24`, worktree `/tmp/bazarr_catalog_provider_worktrees/napisy24`, current head `34a9720`
  - `nekur`: branch `catalog-nekur`, worktree `/tmp/bazarr_catalog_provider_worktrees/nekur`, current head `41e428e`
  - `opensubtitles`: branch `catalog-opensubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/opensubtitles`, current head `c8f013c`
  - `opensubtitlescom`: branch `catalog-opensubtitlescom`, worktree `/tmp/bazarr_catalog_provider_worktrees/opensubtitlescom`, current head `885985f`
  - `pipocas`: branch `catalog-pipocas`, worktree `/tmp/bazarr_catalog_provider_worktrees/pipocas`, current head `4fe281b`
  - `podnapisi`: branch `catalog-podnapisi`, worktree `/tmp/bazarr_catalog_provider_worktrees/podnapisi`, current head `8b3d09f`, dead origin
  - `prijevodionline`: branch `catalog-prijevodionline`, worktree `/tmp/bazarr_catalog_provider_worktrees/prijevodionline`, current head `9c1d795`
  - `regielive`: branch `catalog-regielive`, worktree `/tmp/bazarr_catalog_provider_worktrees/regielive`, current head `fb5a2ae`
  - `shooter`: branch `catalog-shooter`, worktree `/tmp/bazarr_catalog_provider_worktrees/shooter`, current head `3d794c2`
  - `soustitreseu`: branch `catalog-soustitreseu`, worktree `/tmp/bazarr_catalog_provider_worktrees/soustitreseu`, current head `ed93af8`
  - `subclub`: branch `catalog-subclub`, worktree `/tmp/bazarr_catalog_provider_worktrees/subclub`, current head `0849eae`
  - `subdl`: branch `catalog-subdl`, worktree `/tmp/bazarr_catalog_provider_worktrees/subdl`, current head `7ff94cd`
  - `subf2m`: branch `catalog-subf2m`, worktree `/tmp/bazarr_catalog_provider_worktrees/subf2m`, current head `35bb9c4`
  - `subs4free`: branch `catalog-subs4free`, worktree `/tmp/bazarr_catalog_provider_worktrees/subs4free`, current head `f7ca9ac`
  - `subs4series`: branch `catalog-subs4series`, worktree `/tmp/bazarr_catalog_provider_worktrees/subs4series`, current head `4dbe046`
  - `subsarr`: branch `catalog-subsarr`, worktree `/tmp/bazarr_catalog_provider_worktrees/subsarr`, current head `e154cee`
  - `subscenter`: branch `catalog-subscenter`, worktree `/tmp/bazarr_catalog_provider_worktrees/subscenter`, current head `57de626`, dead origin
  - `subsource`: branch `catalog-subsource`, worktree `/tmp/bazarr_catalog_provider_worktrees/subsource`, current head `d50b08f`
  - `subsro`: branch `catalog-subsro`, worktree `/tmp/bazarr_catalog_provider_worktrees/subsro`, current head `4e63940`
  - `subssabbz`: branch `catalog-subssabbz`, worktree `/tmp/bazarr_catalog_provider_worktrees/subssabbz`, current head `3aa8f02`
  - `subsunacs`: branch `catalog-subsunacs`, worktree `/tmp/bazarr_catalog_provider_worktrees/subsunacs`, current head `6394dff`
  - `subsynchro`: branch `catalog-subsynchro`, worktree `/tmp/bazarr_catalog_provider_worktrees/subsynchro`, current head `20b207d`
  - `subtis`: branch `catalog-subtis`, worktree `/tmp/bazarr_catalog_provider_worktrees/subtis`, current head `10a7aa3`
  - `subtitrarinoi`: branch `catalog-subtitrarinoi`, worktree `/tmp/bazarr_catalog_provider_worktrees/subtitrarinoi`, current head `8fc7785`
  - `subtitriid`: branch `catalog-subtitriid`, worktree `/tmp/bazarr_catalog_provider_worktrees/subtitriid`, current head `ba6e108`
  - `subtitulamostv`: branch `catalog-subtitulamostv`, worktree `/tmp/bazarr_catalog_provider_worktrees/subtitulamostv`, current head `f75eafd`
  - `subx`: branch `catalog-subx`, worktree `/tmp/bazarr_catalog_provider_worktrees/subx`, current head `1f649cf`
  - `supersubtitles`: branch `catalog-supersubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/supersubtitles`, current head `3f96078`
  - `titlovi`: branch `catalog-titlovi`, worktree `/tmp/bazarr_catalog_provider_worktrees/titlovi`, current head `933fb47`
  - `titrari`: branch `catalog-titrari`, worktree `/tmp/bazarr_catalog_provider_worktrees/titrari`, current head `0781631`
  - `titulky`: branch `catalog-titulky`, worktree `/tmp/bazarr_catalog_provider_worktrees/titulky`, current head `8394a28`
  - `turkcealtyaziorg`: branch `catalog-turkcealtyaziorg`, worktree `/tmp/bazarr_catalog_provider_worktrees/turkcealtyaziorg`, current head `6ec4f09`
  - `tvsubtitles`: branch `catalog-tvsubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/tvsubtitles`, current head `875df4e`
  - `whisperai`: branch `catalog-whisperai`, worktree `/tmp/bazarr_catalog_provider_worktrees/whisperai`, current head `0eeb964`, local/generated provider
  - `wizdom`: branch `catalog-wizdom`, worktree `/tmp/bazarr_catalog_provider_worktrees/wizdom`, current head `2d5425c`
  - `xsubs`: branch `catalog-xsubs`, worktree `/tmp/bazarr_catalog_provider_worktrees/xsubs`, current head `5a17922`, dead origin
  - `yavkanet`: branch `catalog-yavkanet`, worktree `/tmp/bazarr_catalog_provider_worktrees/yavkanet`, current head `000921d`
  - `yifysubtitles`: branch `catalog-yifysubtitles`, worktree `/tmp/bazarr_catalog_provider_worktrees/yifysubtitles`, current head `6d4ecff`
  - `zimuku`: branch `catalog-zimuku`, worktree `/tmp/bazarr_catalog_provider_worktrees/zimuku`, current head `f9a9eff`
- The current checkout `/home/lavx/Documents/bazarr_catalog` is not a provider migration workspace. Do not implement providers there.
- Before implementing the next provider, verify whether its worktree already exists with `git worktree list --porcelain`; reuse it if it exists.

## Provider Progress Ledger

### `bsplayer`

- Branch: `catalog-bsplayer`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/bsplayer`
- Current checkpoint: `c04f374 Add BSPlayer provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/17` opened on 2026-06-01, marked ready for review, head `catalog-bsplayer`, base `main`, merge state `CLEAN`.
- Provider type: active API provider.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed the old Bazarr module had a SOAP implementation, but `list_subtitles()` was intentionally disabled and returned no results.
  - Live BSPlayer SOAP login probes returned HTTP `200` across checked API subdomains.
  - New catalog behavior restores the hash and size based SOAP search path with no title scraping fallback.
  - `python3 -B -m unittest discover -s tests -p test_bsplayer.py`: `9` tests passed.
  - `python3 -B -m py_compile providers/bsplayer/provider.py`: passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `git diff --check`: clean.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m sdk smoke-test --provider bsplayer --language eng --video-fixture tests/fixtures/bsplayer_video_hash_movie.json`: `bsplayer ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - Provider Hub state has `bsplayer` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - `general.enabled_providers` on the test server was updated to include `bsplayer`; a backup was saved as `/config/config/config.yaml.pre-bsplayer-enable-20260531`.
  - Compat search with `moviehash=0000000000000000`, `moviebytesize=123456789`, `moviehash_match=only`, `imdb_id=1234567`, and language `en` returned HTTP `200`, `100` results, all from `bsplayer`, all with `moviehash_match: true`.
  - Compat login returned HTTP `200`; compat download for the first BSPlayer `file_id` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `53917` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - Branch diff against `origin/main` only touches `README.md`, `catalog.json`, `docs/provider-notes/bsplayer.md`, `providers/bsplayer/`, `tests/fixtures/bsplayer_video_hash_movie.json`, and `tests/test_bsplayer.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_bsplayer.py'`: `9` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/bsplayer/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Attribution and em-dash scan over touched BSPlayer files found no matches.
  - `python3 -B -m sdk smoke-test --provider bsplayer --language eng --video-fixture tests/fixtures/bsplayer_video_hash_movie.json`: `bsplayer ok`.
- Remaining gates: none for the current BSPlayer migration proof.

### `embeddedsubtitles`

- Branch: `catalog-embeddedsubtitles`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/embeddedsubtitles`
- Current checkpoint: `a8257a4 Add EmbeddedSubtitles provider`
- Provider type: local/generated provider.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed this is not a web source. It extracts embedded text subtitle streams from local media files.
  - Catalog behavior uses configured `ffprobe` for stream discovery and configured `ffmpeg` for stream extraction.
  - Supported codecs are `ass`, `subrip`, `webvtt`, and `mov_text`.
  - `python3 -B -m unittest discover -s tests -p test_embeddedsubtitles.py`: `8` tests passed.
  - `python3 -B -m py_compile providers/embeddedsubtitles/provider.py`: passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `git diff --check`: clean.
  - `python3 -B -m unittest discover -s tests`: `336` tests passed, `6` skipped.
  - Generated-media smoke with real local `ffprobe` and `ffmpeg`: `embeddedsubtitles ok`.
- Remaining gates:
  - Confirm Bazarr Provider Hub workers receive readable media paths and executable `ffprobe` and `ffmpeg` paths on the test server.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `embeddedsubtitles` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with a local media fixture containing an embedded text subtitle stream.

### `whisperai`

- Branch: `catalog-whisperai`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/whisperai`
- Current checkpoint: `0eeb964 Add WhisperAI provider`
- Provider type: local/generated provider backed by a user-supplied Whisper web service.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed required endpoint, response timeout, transcription timeout, `ffmpeg_path`, and `pass_video_name` settings.
  - Catalog behavior extracts local audio with configured `ffmpeg`, detects language through `/detect-language` when no audio tags exist, and calls `/asr` for generated SRT content.
  - Translation remains limited to English when source audio language differs from the requested subtitle language.
  - `python3 -B -m unittest discover -s tests -p test_whisperai.py`: `8` tests passed.
  - `python3 -B -m py_compile providers/whisperai/provider.py`: passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `git diff --check`: clean.
  - `python3 -B -m unittest discover -s tests`: `336` tests passed, `6` skipped.
  - Local fake Whisper service smoke with generated media fixture: `whisperai ok`.
- Remaining gates:
  - Run against a real Whisper web service endpoint with a real media fixture.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `whisperai` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `gestdown`

- Branch: `catalog-gestdown`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/gestdown`
- Current checkpoint: `c74a706 Fix Gestdown language parity`
- Local evidence: provider tests, catalog validation, full tests, live smoke, and core manifest parse check passed on 2026-05-29.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - Official Provider Hub catalog source was refreshed from `catalog-gestdown`; the Gestdown manifest resolved to commit `c74a706eeead349507bdb936133ae625dc3c2cc1`.
  - Provider Hub state has `gestdown` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - Provider Hub worker health returned `ok: True`, `status: ready`.
  - Bazarr `general.enabled_providers` includes `gestdown`.
  - Live target selected from the test-server library: Fallout S01E01, IMDb `tt12637874`, TVDB `416744`; direct Gestdown API probes returned English subtitles for that episode.
  - Compat search for `imdb_id=12637874`, `type=episode`, `season_number=1`, `episode_number=1`, and `languages=en` returned HTTP `200`, `40` total results, including `3` Gestdown results with correct Fallout S01E01 feature details.
  - Compat login returned HTTP `200`; compat download for Gestdown `file_id=2` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `45751` bytes of SRT content.
- Remaining gates: none for the current Gestdown migration proof.

### `addic7ed`

- Branch: `catalog-addic7ed`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/addic7ed`
- Current checkpoint: `9464c2a Add Addic7ed provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed username/password or cookie auth, optional User-Agent, VIP download cap, show-id lookup, movie-id lookup, episode and movie parsing, incomplete-subtitle filtering, hearing-impaired flags, rate-limit detection, and plain subtitle downloads.
  - Bazarr UI/config inspection confirmed settings `username`, `password`, `cookies`, `user_agent`, and `vip`, with `username`, `password`, and `cookies` classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_addic7ed.py'`: failed because `providers/addic7ed/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_addic7ed.py'`: `5` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/addic7ed/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `333` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Addic7ed UI language registry: `44` entries, with hearing-impaired represented per result.
  - Attribution and em-dash scan over touched Addic7ed files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://www.addic7ed.com/`: returned HTTP `200`.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://www.addic7ed.com/shows.php`: returned HTTP `302` to `/login.php` without cookies.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' 'https://www.addic7ed.com/search.php?search=Dexter'`: returned HTTP `302` to a show page.
  - Real search and download require valid Addic7ed credentials or session cookies. If Addic7ed presents captcha during username/password login, the Provider Hub plugin requires cookies.
- Remaining gates:
  - Run SDK live smoke search and download with valid Addic7ed cookies or credentials.
  - Decide whether captcha-solver integration belongs in a separate Plugin Hub helper before treating username/password login as fully equivalent to the legacy in-process captcha path.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `addic7ed` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Addic7ed credentials or cookies.

### `karagarga`

- Branch: `catalog-karagarga`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/karagarga`
- Current checkpoint: `0167ba8 Add Karagarga provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie-only English support, tracker login, separate forum login, `pots.php` completed-search flow, approved forum link filtering, up to three forum scans, most-downloaded attachment selection, and direct attachment downloads.
  - Bazarr UI/config inspection confirmed settings `username`, `password`, `f_username`, and `f_password`, with `username`, `password`, and `f_password` classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_karagarga.py'`: failed because `providers/karagarga/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_karagarga.py'`: `4` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/karagarga/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `332` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Karagarga UI language registry: `1` entry.
  - Attribution and em-dash scan over touched Karagarga files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://karagarga.in/`: returned HTTP `200` with the logged-out tracker page.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' 'https://karagarga.in/pots.php?search=Dune&status=completed'`: returned HTTP `302` to `/login.php`.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://forum.karagarga.in/`: returned HTTP `200` with the forum sign-in page.
  - Real search and download require valid Karagarga tracker and forum credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid Karagarga tracker and forum credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `karagarga` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Karagarga credentials.

### `ktuvit`

- Branch: `catalog-ktuvit`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/ktuvit`
- Current checkpoint: `9d3162e Add Ktuvit provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed Hebrew-only movie and episode support, required `email` and `hashed_password`, login through `MembershipService.svc/Login`, service responses wrapped in `d`, Ktuvit search service requests, movie page parsing, episode AJAX subtitle list parsing, TMDB fallback when no IMDb id is present, and the download identifier flow.
  - Bazarr UI/config inspection confirmed settings `email` and `hashed_password`, with both classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_ktuvit.py'`: failed because `providers/ktuvit/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_ktuvit.py`: `5` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/ktuvit/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `333` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Ktuvit UI language registry: `1` entry.
  - Attribution and em-dash scan over touched Ktuvit files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://www.ktuvit.me/`: returned HTTP `200`.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://www.ktuvit.me/Services/MembershipService.svc/Login`: returned HTTP `405` with POST allowed.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://www.ktuvit.me/MovieInfo.aspx?ID=1`: returned HTTP `200` with an invalid-request page.
  - Real search and download require valid Ktuvit email and hashed password credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid Ktuvit credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `ktuvit` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Ktuvit credentials.

### `legendasdivx`

- Branch: `catalog-legendasdivx`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/legendasdivx`
- Current checkpoint: `02bbb60 Add LegendasDivx provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, Portugal Portuguese and Brazilian Portuguese languages, required `username` and `password`, legacy `skip_wrong_fps`, login through `/forum/ucp.php?mode=login`, `modules.php` search flow, series IMDb episode search, daily search limit counter, uploader/hits/frame-rate parsing, and direct ZIP/RAR download extraction.
  - Bazarr UI/config inspection confirmed settings `username`, `password`, and `skip_wrong_fps`, with `username` and `password` classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_legendasdivx.py`: failed because `providers/legendasdivx/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_legendasdivx.py`: `6` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/legendasdivx/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr LegendasDivx UI language registry: `2` entries.
  - Attribution and em-dash scan over touched LegendasDivx files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://www.legendasdivx.pt/`: returned HTTP `200` from Cloudflare with LegendasDivx session cookies.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 'https://www.legendasdivx.pt/forum/ucp.php?mode=login'`: returned HTTP `200`.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 'https://www.legendasdivx.pt/modules.php?name=Downloads&file=jz&d_op=search&op=_jz00&query=tt1160419&temporada=&episodio=&imdb='`: returned HTTP `302` to `/modules.php?name=Your_Account` without credentials.
  - Real search and download require valid LegendasDivx credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid LegendasDivx credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `legendasdivx` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured LegendasDivx credentials.

### `legendasnet`

- Branch: `catalog-legendasnet`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/legendasnet`
- Current checkpoint: `983878f Add Legendas.net provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, Brazilian Portuguese only, required `username` and `password`, login through `POST /api/v1/login`, bearer token auth, movie and TV JSON search payloads, forced/foreign comment detection, unsuccessful payload handling, direct downloads, ZIP download extraction, and daily download limit detection.
  - Bazarr UI/config inspection confirmed settings `username` and `password`, with both classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_legendasnet.py`: failed because `providers/legendasnet/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_legendasnet.py`: `6` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/legendasnet/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Legendas.net UI language registry: `1` entry.
  - Attribution and em-dash scan over touched Legendas.net files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://legendas.net/`: returned HTTP `200`.
  - `curl -sS -i --max-time 20 -A BazarrProviderHub/1.0 https://legendas.net/api/v1/login`: returned HTTP `405` with POST allowed.
  - `curl -sS -i --max-time 20 -A BazarrProviderHub/1.0 https://legendas.net/api/v1/search/movie`: returned HTTP `401` JSON `Missing Authorization Header`.
  - Real search and download require valid Legendas.net credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid Legendas.net credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `legendasnet` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Legendas.net credentials.

### `napisy24`

- Branch: `catalog-napisy24`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/napisy24`
- Current checkpoint: `34a9720 Add Napisy24 provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, Polish only, optional `username` and `password`, default API account fallback when either credential is missing, `napisy24` hash requirement, form POST API lookup, response statuses `OK-0`, `OK-1`, `OK-2`, `OK-3`, login error handling, embedded ZIP payload handling, and no separate upstream download step.
  - Bazarr UI/config inspection confirmed settings `username` and `password`, with both classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_napisy24.py`: failed because `providers/napisy24/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_napisy24.py`: `6` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/napisy24/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Napisy24 UI language registry: `1` entry.
  - Attribution and em-dash scan over touched Napisy24 files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 http://napisy24.pl/`: returned HTTP `301` to `https://napisy24.pl/`.
  - `curl -sS -i --max-time 20 -A BazarrProviderHub/1.0 -d 'postAction=CheckSub&ua=subliminal&ap=lanimilbus&fs=1&fh=0000000000000000&fn=probe.mkv&n24pref=1' http://napisy24.pl/run/CheckSubAgent.php`: returned HTTP `200` with `OK-0||` for a dummy hash.
- Remaining gates:
  - Run SDK live smoke search and download with a real video fixture that has a valid Napisy24/OpenSubtitles hash.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `napisy24` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with a library-backed video whose hash can be computed.

### `pipocas`

- Branch: `catalog-pipocas`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/pipocas`
- Current checkpoint: `4fe281b Add Pipocas.tv provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, required `username` and `password`, CSRF login, language mapping for Portuguese, Brazilian Portuguese, English, and Spanish, release search through `/legendas`, detail-page metadata parsing, direct subtitle downloads, and ZIP/RAR archive downloads.
  - Bazarr UI/config inspection confirmed settings `username` and `password`, with both classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_pipocas.py`: failed because `providers/pipocas/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_pipocas.py`: `6` tests passed.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/pipocas/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched Pipocas files found no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `pipocas.tv`, but the escalated network probe reached the site.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://pipocas.tv/`: returned HTTP `302` to `https://pipocas.tv/login`.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://pipocas.tv/login`: returned HTTP `200` from Cloudflare.
  - Real search and download require valid Pipocas.tv credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid Pipocas.tv credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `pipocas` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Pipocas credentials.

### `subscenter`

- Branch: `catalog-subscenter`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subscenter`
- Current checkpoint: `57de626 Mark SubsCenter upstream dead`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, Hebrew only, optional `username` and `password`, CSRF cookie login, title suggestion lookup, nested subtitle JSON parsing, duplicate release merge by subtitle id, hearing-impaired flags, ZIP downloads, and daily-limit handling for non-ZIP responses.
  - Bazarr UI/config inspection confirmed `subscenter` currently exposes no UI inputs, while the legacy provider still accepts optional credentials.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_subscenter.py`: failed because `providers/subscenter/provider.py` did not exist.
  - Temporary clean-room implementation passed `python3 -B -m unittest discover -s tests -p test_subscenter.py`: `6` tests passed, then was removed because the upstream does not resolve.
  - Final notes-only branch `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - Final notes-only branch `python3 -B -m sdk validate`: `catalog ok`.
  - Final notes-only branch `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - `rg -n "subscenter|SubsCenter" README.md catalog.json providers tests -S`: no matches.
  - `git diff --check` and `git diff --cached --check`: clean.
- Live evidence on 2026-05-31:
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 http://www.subscenter.info/he/`: failed with `curl: (6) Could not resolve host`.
  - Escalated curl checks for `http://www.subscenter.info/he/`, `http://subscenter.info/he/`, `https://www.subscenter.info/he/`, and `https://subscenter.info/he/` all failed DNS resolution.
  - Public DNS checks through `dig +short @1.1.1.1` and `dig +short @8.8.8.8` returned no address records for `www.subscenter.info` and `subscenter.info`.
  - `python3 -B -m sdk smoke-test --provider subscenter --language heb --video-fixture tests/fixtures/subscenter_video_dune_2021.json --expect-min-results 1 --skip-download` failed with sandbox DNS error.
  - The same SDK smoke test with escalated network failed with `No address associated with hostname`.
- Remaining gates:
  - Treat SubsCenter as blocked/dead unless the original site returns or a verified replacement origin is found.
  - Do not add `subscenter` to the core replacement policy while the origin is dead.
  - Do not open or merge SubsCenter as an active catalog provider while the domain does not resolve.
  - Do not require Provider Hub compat search, download, or stream proof while the origin is dead.

### `titlovi`

- Branch: `catalog-titlovi`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/titlovi`
- Current checkpoint: `933fb47 Add Titlovi provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, required `username` and `password`, token login, six UI languages, Titlovi language-label conversion, duplicate Serbian handling, paginated API search up to three pages, season-only episode search with local episode filtering, episode-zero packs, inconsistent title-name fixes, direct/ZIP/RAR downloads, Serbian Latin/Cyrillic bundled archive selection, and HTTP `429` rate-limit handling.
  - Bazarr UI/config inspection confirmed settings `username` and `password`, with both classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_titlovi.py`: failed because `providers/titlovi/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_titlovi.py`: `6` tests passed.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/titlovi/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Titlovi UI language registry: `6` entries.
  - Attribution and em-dash scan over touched Titlovi files found no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `kodi.titlovi.com`, but escalated network probes reached the API.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://kodi.titlovi.com/api/subtitles/gettoken`: returned HTTP `405` with `allow: POST`.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://kodi.titlovi.com/api/subtitles/search`: returned HTTP `405` with `allow: GET`.
  - Real search and download require valid Titlovi credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid Titlovi credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `titlovi` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Titlovi credentials.

### `titulky`

- Branch: `catalog-titulky`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/titulky`
- Current checkpoint: `8394a28 Add Titulky provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, required VIP `username` and `password`, `approved_only`, `skip_wrong_fps`, session login through the premium site root, serial browse by IMDb id, movie-as-season-zero behavior, episode row parsing, Czech/Slovak flag language parsing, approved/unapproved row classes, detail-page FPS parsing, direct/ZIP/RAR downloads, daily download-limit detection, HTTP `429` rate-limit handling, and Europe/Prague daily reset semantics in Bazarr core.
  - Bazarr UI/config inspection confirmed settings `username`, `password`, `approved_only`, and `skip_wrong_fps`, with `username` and `password` classified as secrets.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_titulky.py`: failed because `providers/titulky/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_titulky.py`: `6` tests passed.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/titulky/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the Bazarr Titulky UI language registry: `2` entries.
  - Attribution and em-dash scan over touched Titulky files found no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `premium.titulky.com`, but escalated network probes reached the site.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://premium.titulky.com/`: returned HTTP `200`.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 'https://premium.titulky.com/?action=serial&step=0&id=1160419'`: returned HTTP `200`.
  - Real search and download require valid Titulky VIP credentials.
- Remaining gates:
  - Run SDK live smoke search and download with valid Titulky VIP credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `titulky` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Titulky credentials.

### `xsubs`

- Branch: `catalog-xsubs`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/xsubs`
- Current checkpoint: `5a17922 Mark XSubs upstream dead`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed episode-only support, optional `username` and `password`, CSRF login, logout, Greek-only language support, XML series index, series id lookup with article and year fallbacks, season id lookup, episode range expansion, unreleased subtitle filtering, direct subtitle downloads, and Windows-1253 subtitle encoding.
  - Bazarr UI/config inspection confirmed settings `username` and `password`, with both classified as secrets.
  - No active provider implementation, manifest, test fixtures, README entry, or catalog entry was promoted because the upstream no longer serves the legacy service.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - `rg -n "xsubs|XSubs" README.md catalog.json providers tests -S`: no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `xsubs.tv`, but escalated network probes reached the host.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 http://xsubs.tv/`: returned HTTP `200`.
  - `http://xsubs.tv/series/all.xml` returned HTTP `200`, but the body was an unrelated Korean-language link page instead of the legacy XML series index.
  - `https://xsubs.tv/series/all.xml`, `http://www.xsubs.tv/series/all.xml`, and `https://www.xsubs.tv/series/all.xml` returned the same unrelated page.
  - `http://xsubs.tv/xforum/account/signin/` returned the same unrelated page instead of the legacy login form.
- Remaining gates:
  - Treat XSubs as blocked/dead unless the original subtitle service returns or a verified replacement origin is found.
  - Do not add `xsubs` to the core replacement policy while the origin is dead.
  - Do not open or merge XSubs as an active catalog provider while the host serves unrelated content.

### `subs4free`

- Branch: `catalog-subs4free`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subs4free`
- Current checkpoint: `f7ca9ac Add Subs4Free provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie-only support, Greek and English languages, no credentials, search through `/search_report.php?search=<query>&searchType=1`, direct result cards, older `Mov_sel` suggestion pages, uploader and download-count metadata, hidden download id form parsing, anti-block GET sequence, `/getSub.php` POST with click coordinates, direct subtitle downloads, ZIP downloads, and RAR downloads.
  - Bazarr UI/config inspection confirmed no provider settings and UI languages `ell` and `eng`.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_subs4free.py`: failed because `providers/subs4free/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_subs4free.py`: `7` tests passed.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subs4free/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched Subs4Free files found no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `www.subs4free.info`, but escalated network probes reached the site.
  - `curl -sS -I --max-time 20 -A BazarrProviderHub/1.0 https://www.subs4free.info/`: returned HTTP `200`.
  - `curl -sS -L --max-time 20 -A 'Mozilla/5.0 BazarrProviderHub' 'https://www.subs4free.info/search_report.php?search=Inception&searchType=1'`: returned current `movie-details` rows for Inception.
  - `python3 -B -m sdk smoke-test --provider subs4free --language ell --video-fixture tests/fixtures/subs4free_video_inception_2010.json --expect-min-results 1 --skip-download`: passed outside the sandbox network restriction.
  - `python3 -B -m sdk smoke-test --provider subs4free --language ell --video-fixture tests/fixtures/subs4free_video_inception_2010.json --expect-min-results 1`: passed outside the sandbox network restriction, including download.
- Fresh local and live evidence on 2026-06-01:
  - Branch `catalog-subs4free` was pushed at `f7ca9ac`.
  - `python3 -B -m unittest discover -s tests -p 'test_subs4free.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subs4free/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subs4free`, `tests/test_subs4free.py`, `README.md`, `catalog.json`, and `docs/provider-notes/subs4free.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `python3 -B -m sdk smoke-test --provider subs4free --language ell --video-fixture tests/fixtures/subs4free_video_inception_2010.json --expect-min-results 1`: `subs4free ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-subs4free`; refresh returned `13` entries and resolved Subs4Free `0.1.0` at commit `f7ca9ac599bf2ced93596e9b014bf42d70e2e00f`.
  - Provider Hub staged Subs4Free `0.1.0`, installed dependencies successfully, and saved config `request_delay_ms=0`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `f7ca9ac599bf2ced93596e9b014bf42d70e2e00f`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subs4free`, and excludes `hosszupuska` and `podnapisi`.
  - A direct active-bundle search against the Inception Greek fixture returned `14` Subs4Free candidates.
  - Initial compat searches for `Inception.2010.1080p.BluRay.x264.mkv` returned no Subs4Free rows because higher-ranked cached and non-Subs4Free rows filled the pages; a fresh release-specific key was used for proof.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1375666&query=Inception.2010.1080p.BRRip.x265.HazMatt.mkv&type=movie&languages=el&per_page=100` returned HTTP `200`, `140` total results, and `14` Subs4Free rows.
  - First Subs4Free result: `file_id=354`, release `Inception 2010 1080p BluRay H264 AAC-RARBG [SubRip]`, subtitle id `subs4free:subs4free-s3591aab93d-ell`.
  - Compat download `POST /api/v1/download` for `file_id=354` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `150646` bytes. The payload starts with SRT cue `0`, timestamp `00:00:00,000 --> 00:00:02,500`, and the source marker line from the downloaded subtitle.
- Status:
  - Subs4Free is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for the Greek movie path.

### `subs4series`

- Branch: `catalog-subs4series`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subs4series`
- Current checkpoint: `b91856e Add Subs4Series Cloudflare fallback`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed episode-only support, Greek and English languages, no per-provider UI settings, `search_report.php?search=<title>&searchType=1` suggestions, `/tv-series/<show>/season-<season>/episode-<episode>` episode pages, `seeDark` and `seeMedium` row parsing, language image mapping, uploader and download-count metadata, anti-block GET sequence, reCAPTCHA-gated download pages, direct subtitle bodies, ZIP downloads, RAR downloads, and Windows-1253 subtitle encoding.
  - Provider Hub worker inspection showed legacy global anti-captcha environment is not inherited by plugin workers, so the clean-room plugin exposes explicit `captcha_response`, `captcha_solver_url`, `captcha_solver_token`, and `captcha_solver_timeout_ms` settings.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_subs4series.py`: failed because `providers/subs4series/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_subs4series.py`: `8` tests passed.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `336` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched Subs4Series files found no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `www.subs4series.com`, but escalated network probes reached the site.
  - `https://www.subs4series.com/search_report.php?search=Game%20of%20Thrones&searchType=1` returned current `Mov_sel` suggestions and subtitle rows.
  - `https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1` returned current Game of Thrones S01E01 Greek and English subtitle rows.
  - Live detail-page probes found the current `a.style55ws` download target shape.
  - `python3 -B -m sdk smoke-test --provider subs4series --language eng --video-fixture tests/fixtures/subs4series_video_game_of_thrones_s01e01.json --expect-min-results 1 --skip-download`: passed outside the sandbox network restriction.
  - `python3 -B -m sdk smoke-test --provider subs4series --language eng --video-fixture tests/fixtures/subs4series_video_game_of_thrones_s01e01.json --expect-min-results 1`: passed outside the sandbox network restriction, including download. One earlier full live run timed out on origin read, but an immediate curl decomposition and final full smoke both succeeded.
- Local evidence on 2026-06-01:
  - Test-server direct active-bundle search initially failed with Cloudflare `403` `Attention Required!` on `https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1`.
  - Test-server FlareSolverr at `http://127.0.0.1:8191/v1` fetched the same search URL in `3.86` seconds, returned HTTP `200`, real suggestion HTML, `3` cookies, and a solved User-Agent.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subs4series.py'`: failed with `TypeError: Subs4SeriesProvider._http_get() got an unexpected keyword argument 'config'` for the new FlareSolverr fallback test.
  - `b91856e` adds cloudscraper-first, optional FlareSolverr fallback for Cloudflare-blocked GET requests, stores returned cookies/User-Agent, exposes `flaresolverr_url` and `flaresolverr_timeout_ms`, and bumps Subs4Series to `0.1.1`.
  - `python3 -B -m unittest discover -s tests -p 'test_subs4series.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subs4series/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over touched Subs4Series files found no matches.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m sdk smoke-test --provider subs4series --language eng --video-fixture tests/fixtures/subs4series_video_game_of_thrones_s01e01.json --expect-min-results 1`: `subs4series ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-subs4series`; refresh returned `13` entries and resolved Subs4Series `0.1.1` at commit `b91856ed2b105c80b4843c846aeff421b067a9ff`.
  - Provider Hub staged Subs4Series `0.1.1`, found no broken requirements, and saved config `request_delay_ms=0`, `flaresolverr_url=http://127.0.0.1:8191/v1`, and `flaresolverr_timeout_ms=60000`.
  - Provider state after restart: active version `0.1.1`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `b91856ed2b105c80b4843c846aeff421b067a9ff`.
  - Replacement policy contained `55` trusted ids, included `subs4series`, and excluded `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt0944947&query=Game.of.Thrones.S01E01.HDTV.XviD-FEVER.avi&type=episode&season_number=1&episode_number=1&languages=en&per_page=100` returned HTTP `200`, `119` total results over `2` pages, and `1` Subs4Series row.
  - First Subs4Series row: page `2`, `file_id=118`, release `Game of Thrones - 01x01 - Winter is Coming [HDTV XviD-FEVER]`, subtitle id `subs4series:69f3c23c83b7591e25b4802e12dcaa0acd09f12f`.
  - Compat login returned HTTP `200`; compat download `POST /api/v1/download` for `file_id=118` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` but `0` bytes, so Subs4Series is not complete.
  - Direct active-bundle download tracing showed the generated `getSub-...html` target returns Cloudflare `403` `Attention Required!` on both GET and POST from the test server.
  - FlareSolverr browser-session probes could load detail and anti-block pages, but GET on the download target returned the detail or anti-block page and POST on the target returned the site homepage HTML, not a subtitle archive.
- Remaining gates:
  - Prove Provider Hub compat download and non-empty stream bytes on `bazarr-ui-test`.
  - Resolve the test-server Cloudflare block on `getSub-...html` download targets. Search is now proven through compat, but download target retrieval is still blocked by the origin from the test-server egress.
  - If live Subs4Series presents reCAPTCHA during compat proof, configure `captcha_response` or a `captcha_solver_url` helper and rerun download proof.

### `zimuku`

- Branch: `catalog-zimuku`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/zimuku`
- Current checkpoint: `f9a9eff Fix Zimuku dependency lock`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed movie and episode support, English and Chinese language variants, search through `https://srtku.com/search?q=<query>`, episode `.Sxx` query suffixes, non-shooter `div.item` result parsing, Chinese season marker filtering, season-year adjustment, `tbody tr` subtitle row parsing, detail-page `a#down1` download flow, final `a[rel=nofollow]` download flow, Yunsuo image verification, direct subtitle downloads, ZIP downloads, RAR downloads, and archive-file preference for Simplified, Traditional, and bilingual Chinese names.
  - Bazarr UI/config inspection confirmed no provider-specific settings in the legacy UI, but the provider description says anti-captcha is required. Provider Hub worker inspection showed legacy global anti-captcha environment is not inherited by plugin workers, so the clean-room plugin exposes explicit `captcha_response`, `captcha_solver_url`, `captcha_solver_token`, and `captcha_solver_timeout_ms` settings.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p test_zimuku.py`: failed because `providers/zimuku/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p test_zimuku.py`: `6` tests passed.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched Zimuku files found no matches.
- Live evidence on 2026-05-31:
  - Sandbox DNS could not resolve `srtku.com`, but escalated network probes reached the host.
  - `curl -sS -I --max-time 20 -A 'Mozilla/5.0 BazarrProviderHub' https://srtku.com/`: returned HTTP `404` with `security_session_verify` cookie, matching the Yunsuo wall behavior.
  - `https://srtku.com/search?q=Dune%202021` returned the current Yunsuo image verification page with `data:image/bmp;base64,...` and a `security_verify_img` redirect.
  - `python3 -B -m sdk smoke-test --provider zimuku --language zho --video-fixture tests/fixtures/zimuku_video_game_of_thrones_s01e01.json --expect-min-results 1 --skip-download`: failed outside the sandbox network restriction with `zimuku search failed: zimuku yunsuo captcha response required`.
- Local evidence on 2026-06-01:
  - `python3 -B -m unittest discover -s tests -p 'test_zimuku.py'`: `6` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/zimuku/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over touched Zimuku files found no matches.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped before the dependency-lock fix.
  - Live smoke `python3 -B -m sdk smoke-test --provider zimuku --language zho --video-fixture tests/fixtures/zimuku_video_game_of_thrones_s01e01.json --expect-min-results 1 --skip-download`: failed with `zimuku yunsuo captcha response required`.
  - Provider Hub staging of Zimuku `0.1.0` failed before runtime because `py7zz` pulled unpinned `requests>=2.32.4` while pip was running with `--require-hashes`.
  - Red catalog regression `python3 -B -m unittest discover -s tests -p 'test_catalog.py' -k py7zz`: failed because `providers/zimuku/provider.json` was missing `certifi`, `charset-normalizer`, `idna`, `requests`, and `urllib3` pins.
  - `f9a9eff` adds the missing transitive pins, adds the catalog regression, and bumps Zimuku to `0.1.1`.
  - `python3 -B -m unittest discover -s tests -p 'test_catalog.py' -k py7zz`: `1` test passed.
  - `python3 -B -m unittest discover -s tests -p 'test_zimuku.py'`: `6` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/zimuku/provider.py`: passed.
  - `git diff --check`: clean.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - Live smoke after the dependency-lock fix still failed with `zimuku yunsuo captcha response required`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-zimuku`; refresh returned `13` entries and resolved Zimuku `0.1.1` at commit `f9a9eff1f8d0b76f9172381d879db31b68d57408`.
  - Provider Hub staged Zimuku `0.1.1`, installed hash-locked requirements successfully, found no broken requirements, and saved config `request_delay_ms=0`.
  - Provider state after restart: active version `0.1.1`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `f9a9eff1f8d0b76f9172381d879db31b68d57408`.
  - Replacement policy contained `55` trusted ids, included `zimuku`, and excluded `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt0944947&query=Game.of.Thrones.S01E01.HDTV.XviD-FEVER.mkv&type=episode&season_number=1&episode_number=1&languages=zh&per_page=100` returned HTTP `200`, `11` total results, and `0` Zimuku rows.
  - Direct active-bundle search failed with `ValueError: zimuku yunsuo captcha response required`.
  - Direct first search response from `https://srtku.com/search?q=Game+of+Thrones.S01` returned HTTP `404`, parsed as a Yunsuo challenge with `image_mime=image/bmp` and `10872` base64 characters.
- Remaining gates:
  - Configure a working `captcha_solver_url` or one-use `captcha_response`, then rerun live Zimuku search and download smoke.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with Yunsuo verification solved.

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
- Fresh local evidence on 2026-06-01:
  - `python3 -B -m unittest discover -s tests -p 'test_regielive.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/regielive/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/regielive`, `tests/test_regielive.py`, `README.md`, `catalog.json`, and `docs/provider-notes/regielive.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
- Live evidence on 2026-06-01:
  - `python3 -B -m sdk smoke-test --provider regielive --language ron --video-fixture tests/fixtures/regielive_video_dune.json --expect-min-results 1 --skip-download`: failed with `regielive search failed: regielive rejected the request`.
  - Local direct probe `GET https://api.regielive.ro/bazarr/search.php?nume=Dune&an=2021` with `RL-API: API-BAZARR-YTZ-SL` returned HTTP `403` JSON `{"eroare":"Cerere invalida","cod":403}` before later probes hit the access-restriction page.
  - Browser-style User-Agent, GET-with-body, POST form, cookie-retention, and episode query probes did not produce a successful response; the endpoint returned the RegieLive access-restriction page or HTTP `429` after repeated attempts.
  - `bazarr-ui-test` runtime egress reached `https://api.regielive.ro/` with HTTP `200` and body `API<BR>Hi :)`, but the same runtime egress got HTTP `403` from the search endpoint and from `https://subtitrari.regielive.ro`.
- Remaining gates:
  - Treat current RegieLive proof as blocked by the live API/search-host access restriction, not by parser behavior or catalog validation.
  - Determine whether RegieLive currently requires a different public request shape, allows only specific egress IPs, or has retired the Bazarr API key.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `regielive` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.

### `shooter`

- Branch: `catalog-shooter`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/shooter`
- Current checkpoint: `3d794c2 Add Shooter provider`
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_shooter.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live Shooter API evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider shooter --language eng --video-fixture /tmp/shooter_live_video_man_of_steel.json --expect-min-results 1`: `shooter ok`.
  - Direct worker-shaped live byte check returned `3` results and downloaded `110646` subtitle bytes.
- Fresh local evidence on 2026-06-01:
  - Recreated isolated worktree `/tmp/bazarr_catalog_provider_worktrees/shooter` from branch `catalog-shooter`.
  - `python3 -B -m unittest discover -s tests -p 'test_shooter.py'`: `8` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/shooter/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/shooter`, `tests/test_shooter.py`, `README.md`, `catalog.json`, and `docs/provider-notes/shooter.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `336` tests passed, `6` skipped.
- Live evidence on 2026-06-01:
  - Direct provider search with the synthetic fixture hash reached the Shooter API and returned `0` results, proving the route still returns the expected no-results shape without disclosing real media metadata.
  - `catalog-shooter` was pushed to GitHub at commit `3d794c2e1adeb6e08df841e71b8c87249b577c94`.
  - Official Provider Hub catalog source was refreshed from `catalog-shooter`; the Shooter manifest resolved to version `0.1.0` at commit `3d794c2e1adeb6e08df841e71b8c87249b577c94`.
  - Provider Hub staged Shooter with no broken requirements, then `bazarr-ui-test` restarted healthy.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `3d794c2e1adeb6e08df841e71b8c87249b577c94`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `shooter`, and excludes `hosszupuska` and `podnapisi`.
  - Real-media Shooter compat proof was not run because it would send a test-server media path and derived Shooter hash to the public Shooter API; run only after explicit approval for that disclosure or with a user-provided non-sensitive fixture.
- Remaining gates:
  - Prove Provider Hub compat search, download, and stream using a library-backed video that lets Bazarr compute `video.hashes.shooter`, because Shooter does not support title-only searches.
  - Get explicit approval before using real test-server media paths and derived Shooter hashes against the public Shooter API, or use a non-sensitive fixture supplied for this purpose.

### `subtis`

- Branch: `catalog-subtis`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subtis`
- Current checkpoint: `4e7200d Fix Subtis fixture trailing blanks`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/18` opened on 2026-06-01, head `catalog-subtis`, base `main`, merge state `CLEAN`.
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
- Fresh local and live evidence on 2026-06-01:
  - Recreated isolated worktree `/tmp/bazarr_catalog_provider_worktrees/subtis` from branch `catalog-subtis`.
  - `python3 -B -m unittest discover -s tests -p 'test_subtis.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subtis/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subtis`, `tests/test_subtis.py`, `README.md`, `catalog.json`, and `docs/provider-notes/subtis.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m sdk smoke-test --provider subtis --language spa --video-fixture tests/fixtures/subtis_video_man_of_steel.json --expect-min-results 1`: `subtis ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Branch `catalog-subtis` was pushed at `10a7aa31a7353d5570d8f636c241690a56ed33f3`.
  - Official catalog source dev ref was set to `catalog-subtis`; refresh returned `13` entries and resolved Subtis `0.1.0` at commit `10a7aa31a7353d5570d8f636c241690a56ed33f3`.
  - Provider Hub staged Subtis `0.1.0` with no broken requirements, then `bazarr-ui-test` restarted healthy.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `10a7aa31a7353d5570d8f636c241690a56ed33f3`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subtis`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?moviehash=5b8f8f4e41ccb21e&moviebytesize=7033732714&imdb_id=tt0770828&query=man.of.steel.2013.720p.bluray.x264-felony.mkv&type=movie&languages=es&per_page=100` returned HTTP `200`, `68` total results, and `1` Subtis row.
  - Subtis result: `file_id=61`, release `Man of Steel [fuzzy match]`, subtitle id `subtis:subtis-f38417eceeb7f088`.
  - Compat download `POST /api/v1/download` for `file_id=61` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `94820` bytes. The payload starts with SRT cue `1` and timestamp `00:00:00,000 --> 00:00:24,000`.
- Fresh PR evidence on 2026-06-01:
  - Branch diff against `origin/main` only touches `README.md`, `catalog.json`, `docs/provider-notes/subtis.md`, `providers/subtis/`, `tests/fixtures/subtis_search_response.json`, `tests/fixtures/subtis_video_man_of_steel.json`, and `tests/test_subtis.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_subtis.py'`: `9` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subtis/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean after commit `4e7200d`.
  - Attribution and em-dash scan over touched Subtis files found no matches.
  - Direct Subtis endpoint probes showed hash, byte, and filename lookups now return `404`, while the alternative endpoint still returns `200` with a subtitle candidate for the fixture.
  - `python3 -B -m sdk smoke-test --provider subtis --language spa --video-fixture tests/fixtures/subtis_video_man_of_steel.json --expect-min-results 1`: `subtis ok`.
- Remaining gates: none for the current Subtis migration proof.

### `wizdom`

- Branch: `catalog-wizdom`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/wizdom`
- Current checkpoint: `2d5425c Add Wizdom provider`
- Local evidence on 2026-05-29:
  - `python3 -B -m unittest discover -s tests -p 'test_wizdom.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider wizdom --language heb --video-fixture tests/fixtures/wizdom_video_inception.json --expect-min-results 1` failed with `wizdom search failed: The read operation timed out`.
  - Direct `curl` probes to `https://wizdom.xyz/`, `https://wizdom.xyz/api/releases/tt1375666`, and `http://wizdom.xyz/api/releases/tt1375666` returned Cloudflare HTTP `522` after about `19` seconds from both the local workstation and `bazarr-ui-test`.
  - TMDB lookup for the same Inception fixture returned HTTP `200`, so the current live blocker is Wizdom origin availability, not TMDB.
- Fresh local evidence on 2026-06-01:
  - Recreated isolated worktree `/tmp/bazarr_catalog_provider_worktrees/wizdom` from branch `catalog-wizdom`.
  - `python3 -B -m unittest discover -s tests -p 'test_wizdom.py'`: `12` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/wizdom/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/wizdom`, `tests/test_wizdom.py`, `README.md`, `catalog.json`, and `docs/provider-notes/wizdom.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
- Live evidence on 2026-06-01:
  - `python3 -B -m sdk smoke-test --provider wizdom --language heb --video-fixture tests/fixtures/wizdom_video_inception.json --expect-min-results 1 --skip-download`: failed with `wizdom search failed: The read operation timed out`.
  - Local direct probe `GET https://api.tmdb.org/3/search/movie?...query=Inception...year=2010` returned HTTP `200` with current TMDB search results.
  - Local direct probe `GET https://wizdom.xyz/` returned Cloudflare HTTP `522`; local direct probe `GET https://wizdom.xyz/api/releases/tt1375666` timed out after `25` seconds with `0` bytes received.
  - `bazarr-ui-test` direct probe `GET https://wizdom.xyz/` returned Cloudflare HTTP `522`; `GET https://wizdom.xyz/api/releases/tt1375666` timed out after `25` seconds with `0` bytes received.
- Provider Hub test-server evidence on 2026-06-01:
  - Branch `catalog-wizdom` was pushed at `2d5425ce8f2e35ef897dc51eac59a30be7ef4366`.
  - Official catalog source dev ref was set to `catalog-wizdom`; refresh returned `13` entries and resolved Wizdom `0.1.0` at commit `2d5425ce8f2e35ef897dc51eac59a30be7ef4366`.
  - Provider Hub staged Wizdom `0.1.0` with no broken requirements, then `bazarr-ui-test` restarted healthy.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `2d5425ce8f2e35ef897dc51eac59a30be7ef4366`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `wizdom`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1375666&query=Inception.2010.1080p.BluRay.x264.mkv&type=movie&languages=he&per_page=100` returned HTTP `200`, `34` total rows, and `0` Wizdom rows.
- Remaining gates:
  - Treat current Wizdom proof as blocked by `wizdom.xyz` origin timeout or Cloudflare `522`, not by TMDB, local parser behavior, branch deployment, or Provider Hub loading.
  - Re-run live Wizdom smoke when `wizdom.xyz` stops timing out or returning Cloudflare `522`.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `tvsubtitles`

- Branch: `catalog-tvsubtitles`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/tvsubtitles`
- Current checkpoint: `875df4e Add TVsubtitles provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/19` opened on 2026-06-01, head `catalog-tvsubtitles`, base `main`, merge state `CLEAN`.
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
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_tvsubtitles.py'`: `12` tests passed.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - `catalog-tvsubtitles` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-tvsubtitles`; the TVSubtitles manifest resolved to commit `875df4e08e51088b460ef802c86b8a9f8d37fcaf`.
  - Provider Hub state has `tvsubtitles` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - Provider Hub worker health returned `ok: True`, `status: ready`.
  - Bazarr `general.enabled_providers` includes `tvsubtitles`.
  - Compat query-only episode search for `query=The.Office.S01E02.Diversity.Day.HDTV.XviD.avi`, `type=episode`, `season_number=1`, `episode_number=2`, and `languages=en` returned HTTP `200`, `63` total results, including `2` TVSubtitles results.
  - Compat login returned HTTP `200`; compat download for TVSubtitles `file_id=1` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `24861` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - Branch diff against `origin/main` only touches `README.md`, `catalog.json`, `providers/tvsubtitles/`, `tests/fixtures/tvsubtitles_video_the_office_s01e02.json`, and `tests/test_tvsubtitles.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_tvsubtitles.py'`: `12` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/tvsubtitles/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Attribution and em-dash scan over touched TVSubtitles files found no matches.
  - `python3 -B -m sdk smoke-test --provider tvsubtitles --language eng --video-fixture tests/fixtures/tvsubtitles_video_the_office_s01e02.json --expect-min-results 1`: `tvsubtitles ok`.
- Remaining gates: none for the current TVSubtitles migration proof.

### `subtitulamostv`

- Branch: `catalog-subtitulamostv`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subtitulamostv`
- Current checkpoint: `9ac6f4d Fix SubtitulamosTV fixture trailing blank`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/20` opened on 2026-06-01, head `catalog-subtitulamostv`, base `main`, merge state `CLEAN`.
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
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subtitulamostv.py'`: `16` tests passed.
  - `python3 -B -m sdk smoke-test --provider subtitulamostv --language eng --video-fixture tests/fixtures/subtitulamostv_video_the_last_ship_s05e10.json --expect-min-results 1 --skip-download`: `subtitulamostv ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - `catalog-subtitulamostv` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-subtitulamostv`; the SubtitulamosTV manifest resolved to commit `f75eafde2378fe1a9e0e9aa16e4d8f56180ec224`.
  - Provider Hub state has `subtitulamostv` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - Provider Hub worker health returned `ok: True`, `status: ready`.
  - Bazarr `general.enabled_providers` includes `subtitulamostv`.
  - Compat query-only episode search for `query=The.Last.Ship.S05E10.1080p.WEBRip.x264-TBS.mkv`, `type=episode`, `season_number=5`, `episode_number=10`, and `languages=en` returned HTTP `200`, `48` total results, including `1` SubtitulamosTV result for release `MeGusta`.
  - Compat login returned HTTP `200`; compat download for SubtitulamosTV `file_id=3` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `25611` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - Branch diff against `origin/main` only touches `README.md`, `catalog.json`, `docs/provider-notes/subtitulamostv.md`, `providers/subtitulamostv/`, SubtitulamosTV fixtures, and `tests/test_subtitulamostv.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_subtitulamostv.py'`: `16` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subtitulamostv/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean after commit `9ac6f4d`.
  - Attribution and em-dash scan over touched SubtitulamosTV files found no matches.
  - `python3 -B -m sdk smoke-test --provider subtitulamostv --language eng --video-fixture tests/fixtures/subtitulamostv_video_the_last_ship_s05e10.json --expect-min-results 1`: `subtitulamostv ok`.
- Remaining gates: none for the current SubtitulamosTV migration proof.

### `greeksubs`

- Branch: `catalog-greeksubs`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/greeksubs`
- Current checkpoint: `1ec84fa Add GreekSubs provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/21` opened on 2026-06-01, head `catalog-greeksubs`, base `main`, merge state `CLEAN`.
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
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_greeksubs.py'`: `8` tests passed.
  - `python3 -B -m sdk smoke-test --provider greeksubs --language ell --video-fixture tests/fixtures/greeksubs_video_dune.json --expect-min-results 1`: `greeksubs ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - `catalog-greeksubs` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-greeksubs`; the GreekSubs manifest resolved to commit `1ec84faebba0de7ad331520a18d99732fa0c22c5`.
  - Provider Hub state has `greeksubs` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - Provider Hub worker health returned `ok: True`, `status: ready`.
  - Bazarr `general.enabled_providers` includes `greeksubs`; a backup was saved as `/config/config/config.yaml.pre-greeksubs-enable-20260531222901`.
  - Compat movie search for `query=Dune.2021.1080p.WEBRip.mkv`, `type=movie`, `imdb_id=1160419`, and `languages=el` returned HTTP `200`, `17` total results, including `1` GreekSubs result for release `DUNE (2021)`.
  - Compat login returned HTTP `200`; compat download for GreekSubs `file_id=15` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `107129` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - Branch diff against `origin/main` only touches `README.md`, `catalog.json`, `docs/provider-notes/greeksubs.md`, `providers/greeksubs/`, GreekSubs fixtures, and `tests/test_greeksubs.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_greeksubs.py'`: `8` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/greeksubs/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Attribution and em-dash scan over touched GreekSubs files found no matches.
  - `python3 -B -m sdk smoke-test --provider greeksubs --language ell --video-fixture tests/fixtures/greeksubs_video_dune.json --expect-min-results 1`: `greeksubs ok`.
- Remaining gates: none for the current GreekSubs migration proof.

### `animekalesi`

- Branch: `catalog-animekalesi`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/animekalesi`
- Current checkpoint: `b5db085 Add AnimeKalesi provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/22` opened on 2026-06-01, head `catalog-animekalesi`, base `main`, merge state `CLEAN`.
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
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_animekalesi.py'`: `9` tests passed.
  - `python3 -B -m sdk smoke-test --provider animekalesi --language tur --video-fixture tests/fixtures/animekalesi_video_jujutsu_kaisen_2_e01.json --expect-min-results 1`: `animekalesi ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - `catalog-animekalesi` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-animekalesi`; the AnimeKalesi manifest resolved to commit `b5db0859ab73ea98cd1bef5df17a1dcd13b8b6a9`.
  - Provider Hub state has `animekalesi` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - Provider Hub worker health returned `ok: True`, `status: ready`.
  - Bazarr `general.enabled_providers` includes `animekalesi`; a backup was saved as `/config/config/config.yaml.pre-animekalesi-enable-20260531223618`.
  - Compat query-only episode search for `query=Jujutsu.Kaisen.2.S01E01.Hidden.Inventory.mkv`, `type=episode`, `season_number=1`, `episode_number=1`, and `languages=tr` returned HTTP `200`, `12` total results, including `2` AnimeKalesi results.
  - Compat login returned HTTP `200`; compat download for AnimeKalesi `file_id=2` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `27626` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - Branch diff against `origin/main` only touches `README.md`, `catalog.json`, `providers/animekalesi/`, AnimeKalesi fixtures, and `tests/test_animekalesi.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_animekalesi.py'`: `9` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/animekalesi/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Attribution and em-dash scan over touched AnimeKalesi files found no matches.
  - `python3 -B -m sdk smoke-test --provider animekalesi --language tur --video-fixture tests/fixtures/animekalesi_video_jujutsu_kaisen_2_e01.json --expect-min-results 1`: `animekalesi ok`.
- Remaining gates: none for the current AnimeKalesi migration proof.

### `animesubinfo`

- Branch: `catalog-animesubinfo`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/animesubinfo`
- Current checkpoint: `b2be823 Add AnimeSub.info provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/23` opened on 2026-06-01, head `catalog-animesubinfo`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_animesubinfo.py'`: failed because `providers/animesubinfo/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_animesubinfo.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `git diff --cached --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider animesubinfo --language pol --video-fixture tests/fixtures/animesubinfo_video_kimetsu_ep01.json --expect-min-results 1`: `animesubinfo ok`.
  - `python3 -B -m sdk smoke-test --provider animesubinfo --language pol --video-fixture tests/fixtures/animesubinfo_video_akira.json --expect-min-results 1`: `animesubinfo ok`.
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_animesubinfo.py'`: `9` tests passed.
  - `python3 -B -m sdk smoke-test --provider animesubinfo --language pol --video-fixture tests/fixtures/animesubinfo_video_kimetsu_ep01.json --expect-min-results 1`: `animesubinfo ok`.
  - `python3 -B -m sdk smoke-test --provider animesubinfo --language pol --video-fixture tests/fixtures/animesubinfo_video_akira.json --expect-min-results 1`: `animesubinfo ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` is deployed to `bazarr-ui-test`.
  - `catalog-animesubinfo` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-animesubinfo`; the AnimeSub.info manifest resolved to commit `b2be823313fc457e27faa3e497a8d1a2e402375c`.
  - Provider Hub state has `animesubinfo` active at version `0.1.0`, `pending_restart` false, `trusted` true, and `last_error` null.
  - Provider Hub worker health returned `ok: True`, `status: ready`.
  - Bazarr `general.enabled_providers` includes `animesubinfo`.
  - Compat query-only episode search for `query=Kimetsu.no.Yaiba.S01E01.HorribleSubs.mkv`, `type=episode`, `season_number=1`, `episode_number=1`, and `languages=pl` returned HTTP `200`, `7` total results, including `3` AnimeSub.info results.
  - Compat login returned HTTP `200`; compat download for AnimeSub.info `file_id=1` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `28644` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-animesubinfo --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_animesubinfo.py'`: `9` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/animesubinfo/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider animesubinfo --language pol --video-fixture tests/fixtures/animesubinfo_video_kimetsu_ep01.json --expect-min-results 1`: `animesubinfo ok`.
  - `python3 -B -m sdk smoke-test --provider animesubinfo --language pol --video-fixture tests/fixtures/animesubinfo_video_akira.json --expect-min-results 1`: `animesubinfo ok`.
  - `gh pr view 23 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#23` is open, non-draft, head `catalog-animesubinfo`, base `main`, merge state `CLEAN`.
- Remaining gates: none for the current AnimeSub.info migration proof.

### `animetosho`

- Branch: `catalog-animetosho`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/animetosho`
- Current checkpoint: `4a282a3 Add AnimeTosho provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/24` opened on 2026-06-01, head `catalog-animetosho`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_animetosho.py'`: failed because `providers/animetosho/provider.py` did not exist.
  - Live fixture capture from `https://feed.animetosho.org/json?eid=277518`: HTTP `200`, JSON feed for AnimeTosho AniDB episode `277518`.
  - Live fixture capture from `https://feed.animetosho.org/json?show=torrent&id=616869`: HTTP `200`, JSON torrent detail with subtitle attachments.
  - Live attachment probe `https://animetosho.org/storage/attach/001e3505/1979653.xz`: HTTP `200`, `application/x-xz`, decompressed to an ASS subtitle.
  - `python3 -B -m unittest discover -s tests -p 'test_animetosho.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/animetosho/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider animetosho --language eng --video-fixture tests/fixtures/animetosho_video_solo_leveling_s01e12.json --config-json '{"search_threshold":1,"request_delay_ms":0}' --expect-min-results 1 --skip-download`: `animetosho ok`.
  - `python3 -B -m sdk smoke-test --provider animetosho --language eng --video-fixture tests/fixtures/animetosho_video_solo_leveling_s01e12.json --config-json '{"search_threshold":1,"request_delay_ms":0}' --expect-min-results 1`: `animetosho ok`.
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_animetosho.py'`: `11` tests passed.
  - `python3 -B -m sdk smoke-test --provider animetosho --language eng --video-fixture tests/fixtures/animetosho_video_solo_leveling_s01e12.json --config-json '{"search_threshold":1,"request_delay_ms":0}' --expect-min-results 1`: `animetosho ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `658433568` is deployed to `bazarr-ui-test` as image version `ui-test-20260531-provider-hub-replacements-658433568`.
  - Core commit `658433568` passes AniDB IDs through compat search and Provider Hub worker payloads.
  - `catalog-animetosho` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-animetosho`; the AnimeTosho manifest resolved to commit `4a282a3c17f0847c9b84d7b68ef06895814d8dad`.
  - Provider Hub state has `animetosho` active at version `0.1.0`, `pending_restart` false, `trusted` true, `enabled` true, and `last_error` null.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `animetosho`, and excludes `hosszupuska` and `podnapisi`.
  - Compat episode search for `query=Solo.Leveling.S01E12.2160p.WEB-ToonsHub.mkv`, `type=episode`, `season_number=1`, `episode_number=12`, `languages=en`, `series_anidb_id=17495`, and `series_anidb_episode_id=277518` returned HTTP `200`, `13` total results, including `5` AnimeTosho results.
  - Compat login returned HTTP `200`; compat download for AnimeTosho `file_id=9` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `17895` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-animetosho --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_animetosho.py'`: `11` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/animetosho/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider animetosho --language eng --video-fixture tests/fixtures/animetosho_video_solo_leveling_s01e12.json --config-json '{"search_threshold":1,"request_delay_ms":0}' --expect-min-results 1`: `animetosho ok`.
  - `gh pr view 24 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#24` is open, non-draft, head `catalog-animetosho`, base `main`, merge state `CLEAN`.
- Remaining gates: none for the current AnimeTosho migration proof.

### `napiprojekt`

- Branch: `catalog-napiprojekt`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/napiprojekt`
- Current checkpoint: `5d9fb63 Add NapiProjekt provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/25` opened on 2026-06-01, head `catalog-napiprojekt`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_napiprojekt.py'`: failed because `providers/napiprojekt/provider.py` did not exist.
  - Live hash probe `https://napiprojekt.pl/unit_napisy/dl.php?...f=444563eef63f83d47cabb888f7a45113&t=a6f09`: HTTP `200`, returned raw Polish subtitle bytes.
  - Live catalog POST to `https://www.napiprojekt.pl/ajax/search_catalog.php`: HTTP `403`, Cloudflare managed challenge with `cf-mitigated: challenge`.
  - Local cloudscraper probe against the same catalog POST also returned HTTP `403`, so catalog scraping requires configured FlareSolverr fallback when challenged.
  - `python3 -B -m unittest discover -s tests -p 'test_napiprojekt.py'`: `13` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/napiprojekt/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `341` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider napiprojekt --language pol --video-fixture tests/fixtures/napiprojekt_video_shrek.json --config-json '{"only_authors":false,"only_real_names":false}' --expect-min-results 1 --skip-download`: `napiprojekt ok`.
  - Initial full live smoke hit HTTP `429` because search and download queried the hash endpoint twice quickly.
  - After caching hash-response bytes inside the provider worker, `python3 -B -m sdk smoke-test --provider napiprojekt --language pol --video-fixture tests/fixtures/napiprojekt_video_shrek.json --config-json '{"only_authors":false,"only_real_names":false}' --expect-min-results 1`: `napiprojekt ok`.
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_napiprojekt.py'`: `13` tests passed.
  - `python3 -B -m sdk smoke-test --provider napiprojekt --language pol --video-fixture tests/fixtures/napiprojekt_video_shrek.json --config-json '{"only_authors":false,"only_real_names":false}' --expect-min-results 1`: `napiprojekt ok`.
- Test-server evidence on 2026-05-31:
  - Core branch `worktree-provider-hub-builtin-replacements` at `f245ae096` is deployed to `bazarr-ui-test` as image version `ui-test-20260531-provider-hub-replacements-f245ae096`.
  - Core commit `f245ae096` passes compat `moviehash` into `video.hashes["napiprojekt"]`, which is required for NapiProjekt hash searches through `/api/v1/subtitles`.
  - `catalog-napiprojekt` was pushed to GitHub so Provider Hub could stage it from the official source.
  - Official Provider Hub catalog source was refreshed from `catalog-napiprojekt`; the NapiProjekt manifest resolved to commit `5d9fb634c62221b34f96dc9b92df7e0882c4eab4`.
  - Provider Hub state has `napiprojekt` active at version `0.1.0`, `pending_restart` false, `trusted` true, `enabled` true, and `last_error` null.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `napiprojekt`, and excludes `hosszupuska` and `podnapisi`.
  - Compat movie search for `imdb_id=tt0126029`, `query=Shrek.2001.1080p.BluRay.x264.mkv`, `type=movie`, `languages=pl`, `moviehash=444563eef63f83d47cabb888f7a45113`, and `moviehash_match=include` returned HTTP `200`, `25` total results, including `1` NapiProjekt result with `moviehash_match` true.
  - Compat login returned HTTP `200`; compat download for NapiProjekt `file_id=13` returned HTTP `200` and a stream link.
  - Fetching the stream link returned HTTP `200` with `67901` bytes of SRT content.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-napiprojekt --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_napiprojekt.py'`: `13` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/napiprojekt/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider napiprojekt --language pol --video-fixture tests/fixtures/napiprojekt_video_shrek.json --config-json '{"only_authors":false,"only_real_names":false}' --expect-min-results 1`: `napiprojekt ok`.
  - `gh pr view 25 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#25` is open, non-draft, head `catalog-napiprojekt`, base `main`, merge state `CLEAN`.
- Remaining gates: none for the current NapiProjekt migration proof. Author-filtered catalog scraping still depends on a reachable FlareSolverr `/v1` endpoint when NapiProjekt serves a Cloudflare challenge.

### `podnapisi`

- Branch: `catalog-podnapisi`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/podnapisi`
- Current checkpoint: `d04b05b Record Podnapisi dead-origin recheck`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_podnapisi.py'`: failed because `providers/podnapisi/provider.py` did not exist.
  - Live Podnapisi JSON endpoint probes from this environment failed DNS for `www.podnapisi.net`; direct host-IP TLS attempts timed out.
  - `python3 -B -m unittest discover -s tests -p 'test_podnapisi.py'`: `12` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/podnapisi/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution, em-dash, and non-ASCII scan over touched files found no matches.
- Cleanup evidence on 2026-05-31:
  - Removed active `providers/podnapisi` manifest and implementation, Podnapisi-specific tests and fixtures, README entry, and catalog entry.
  - Kept `docs/provider-notes/podnapisi.md` as a historical dead-origin record only.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - `rg -n "podnapisi|Podnapisi" README.md catalog.json providers tests -S`: no matches.
  - `git diff --check` and `git diff --cached --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider podnapisi --language eng --video-fixture tests/fixtures/podnapisi_video_dune_2021.json --config-json '{"verify_ssl":true}' --expect-min-results 1 --skip-download`: failed with `podnapisi search failed: <urlopen error [Errno -2] Name or service not known>`.
  - Recheck on 2026-05-29: `https://www.podnapisi.net/subtitles` and `https://podnapisi.net/subtitles` failed DNS with `curl: (6) Could not resolve host`.
  - RDAP recheck on 2026-05-29: `PODNAPISI.NET` still exists, but `podnapisi.net` and `www.podnapisi.net` do not resolve from the test network; `last changed` is `2026-05-20T13:24:04Z`.
  - Recheck on 2026-05-31 from this migration network: `https://podnapisi.net/subtitles` and `https://www.podnapisi.net/subtitles/search/advanced` still fail DNS with `curl: (6) Could not resolve host`.
  - Fresh recheck on 2026-05-31 after the dead-origin report: `https://www.podnapisi.net/subtitles` and `https://podnapisi.net/subtitles` still fail DNS with `curl: (6) Could not resolve host`.
  - Bazarr test host recheck on 2026-05-31: `ssh lavx@192.168.100.6 curl -I --max-time 15 https://www.podnapisi.net/` and `https://podnapisi.net/` both fail DNS with `curl: (6) Could not resolve host`.
  - Fresh recheck on 2026-05-31 after final dead-origin decision: `curl -I --max-time 15 https://www.podnapisi.net/` and `https://podnapisi.net/` both fail DNS with `curl: (6) Could not resolve host`.
  - Fresh recheck on 2026-06-01 from this migration network: `curl -I --max-time 15 https://www.podnapisi.net/subtitles` fails DNS with `curl: (6) Could not resolve host`.
  - Bazarr test host recheck on 2026-06-01: `ssh lavx@192.168.100.6 curl -I --max-time 15 https://www.podnapisi.net/subtitles` fails DNS with `curl: (6) Could not resolve host`.
- Dead-origin decision:
  - Treat Podnapisi as dead for Provider Hub migration unless `www.podnapisi.net` resolves and serves the original subtitle API again, or a verified replacement origin is found.
  - Do not add `podnapisi` to the core replacement policy while the origin is dead.
  - Do not open or merge Podnapisi as an active catalog provider while the origin is dead.
  - Do not require Provider Hub compat search, download, or stream proof while the origin is dead.

### `subf2m`

- Branch: `catalog-subf2m`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subf2m`
- Current checkpoint: `35bb9c4 Add SubF2M provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/26` opened on 2026-06-01, head `catalog-subf2m`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subf2m.py'`: failed because `providers/subf2m/provider.py` did not exist.
  - Live `https://subf2m.co/` probe returned HTTP `200` behind Cloudflare.
  - Live `searchbytitle` probes for `dune` and `chernobyl` returned current search-result HTML with `/subtitles/<slug>` links.
  - Live detail pages for `Dune: Part One` and `Chernobyl - First Season` exposed language pages and `download icon-download` detail links.
  - Live detail download button for `/subtitles/dune-2021/english/3331049` redirected to `isubcdn.com` and returned `application/zip`.
  - `python3 -B -m unittest discover -s tests -p 'test_subf2m.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subf2m/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution, em-dash, and non-ASCII scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider subf2m --language eng --video-fixture tests/fixtures/subf2m_video_dune_2021.json --config-json '{"request_delay_ms":0}' --expect-min-results 1 --skip-download`: `subf2m ok`.
  - `python3 -B -m sdk smoke-test --provider subf2m --language eng --video-fixture tests/fixtures/subf2m_video_chernobyl_s01e01.json --config-json '{"request_delay_ms":0}' --expect-min-results 1 --skip-download`: `subf2m ok`.
  - `python3 -B -m sdk smoke-test --provider subf2m --language eng --video-fixture tests/fixtures/subf2m_video_dune_2021.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `subf2m ok`.
  - `python3 -B -m sdk smoke-test --provider subf2m --language eng --video-fixture tests/fixtures/subf2m_video_chernobyl_s01e01.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `subf2m ok`.
- Fresh local evidence on 2026-05-31:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subf2m.py'`: `11` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subf2m`, `tests/test_subf2m.py`, `README.md`, and `catalog.json` found no matches.
  - `python3 -B -m sdk smoke-test --provider subf2m --language eng --video-fixture tests/fixtures/subf2m_video_dune_2021.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `subf2m ok`.
- Provider Hub test-server evidence on 2026-05-31:
  - Branch `catalog-subf2m` was pushed at `35bb9c42ebbc33eeb84db5282eb56f908d7db08d`.
  - Official catalog source dev ref was set to `catalog-subf2m`; refresh returned `13` entries and resolved SubF2M `0.1.0` at commit `35bb9c42ebbc33eeb84db5282eb56f908d7db08d`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `35bb9c42ebbc33eeb84db5282eb56f908d7db08d`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subf2m`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1160419&query=Dune.Part.One.2021.1080p.BluRay.x264.mkv&type=movie&languages=en&per_page=100` returned HTTP `200`, `81` total results, and `30` SubF2M results.
  - First SubF2M result: `file_id=45`, release `Dune.Part.One.2021.2160p.Ai-Enhanced.DV.HDR10Plus.H265.TrueHD.Atmos.7.1.MULTI-RIFE.4.15v2-60fps-DirtyHippie`, subtitle id `subf2m:subf2m-3331049-eng`.
  - Compat download `POST /api/v1/download` for `file_id=45` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `126519` bytes. The payload starts with an SRT BOM, cue `1`, and timestamp `00:00:04,588 --> 00:00:05,839`.
- Status:
  - SubF2M is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-subf2m --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_subf2m.py'`: `11` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subf2m/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider subf2m --language eng --video-fixture tests/fixtures/subf2m_video_dune_2021.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `subf2m ok`.
  - `gh pr view 26 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#26` is open, non-draft, head `catalog-subf2m`, base `main`, merge state `CLEAN`.

### `subsarr`

- Branch: `catalog-subsarr`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subsarr`
- Current checkpoint: `e154cee Add Subsarr provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/27` opened as draft on 2026-06-01, head `catalog-subsarr`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subsarr.py'`: failed because `providers/subsarr/provider.py` did not exist.
  - Legacy inspection confirmed the API contract: required `base_url`, `/api/v1/subtitles/search`, IMDb-first search, title fallback, HI filtering, and raw `download_url` subtitle bytes.
  - `python3 -B -m unittest discover -s tests -p 'test_subsarr.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsarr/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution, em-dash, and non-ASCII scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public live smoke is not applicable without a configured self-hosted Subsarr `base_url`.
- Fresh local and test-server evidence on 2026-05-31:
  - Branch `catalog-subsarr` was pushed at `e154cee`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subsarr.py'`: `7` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subsarr`, `tests/test_subsarr.py`, `README.md`, and `catalog.json` found no matches.
  - Bazarr test host has no Subsarr container, no Provider Hub `subsarr` installation, and saved Bazarr config has `subsarr.base_url: ''`.
- Fresh local evidence on 2026-06-01:
  - Worktree recheck confirmed `/tmp/bazarr_catalog_provider_worktrees/subsarr` is a linked git worktree on branch `catalog-subsarr`, clean, and tracking `origin/catalog-subsarr`.
  - `python3 -B -m unittest discover -s tests -p 'test_subsarr.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsarr/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subsarr`, `tests/test_subsarr.py`, `README.md`, and `catalog.json` found no matches.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-subsarr --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_subsarr.py'`: `7` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsarr/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - `gh pr view 27 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#27` is open, draft, head `catalog-subsarr`, base `main`, merge state `CLEAN`.
- Remaining gates:
  - Run SDK smoke search and download against a reachable self-hosted Subsarr service when a test `base_url` is available.
  - Core branch `worktree-provider-hub-builtin-replacements` at `f245ae096` is deployed on `bazarr-ui-test`; `subsarr` remains unproved only because the test service URL is missing.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with a real `base_url`.

### `assrt`

- Branch: `catalog-assrt`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/assrt`
- Current checkpoint: `c4e2440 Add Assrt provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/28` opened as draft on 2026-06-01, head `catalog-assrt`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_assrt.py'`: failed because `providers/assrt/provider.py` did not exist.
  - Legacy inspection confirmed the tokened API contract: quota lookup, `/sub/search`, `/sub/detail`, language keys `chs`, `cht`, `eng`, native-name fallback, and season-pack file selection.
  - `python3 -B -m unittest discover -s tests -p 'test_assrt.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/assrt/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution, em-dash, and non-ASCII scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - No-token `https://api.assrt.net/v1/user/quota` probe returned `{"status":20001,"errmsg":"invalid token"}`.
  - No-token `https://api.assrt.net/v1/sub/search?...` probe returned `{"status":20001,"errmsg":"invalid token"}`.
  - Real search and download smoke require a valid Assrt API token.
- Fresh local and test-server evidence on 2026-05-31:
  - Branch `catalog-assrt` was pushed at `c4e2440`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_assrt.py'`: `7` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/assrt`, `tests/test_assrt.py`, `README.md`, and `catalog.json` found no matches.
  - Provider Hub staged and activated Assrt `0.1.0` from commit `c4e2440fceaca97e1d5d4fe4397713a7825614ee` on `bazarr-ui-test`.
  - Bazarr settings inspection through the settings object showed `assrt.token` is empty, so Assrt was disabled again after activation to avoid no-token compat fanout noise.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/assrt` as a linked git worktree on branch `catalog-assrt`, clean, and tracking `origin/catalog-assrt`.
  - `python3 -B -m unittest discover -s tests -p 'test_assrt.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/assrt/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/assrt`, `tests/test_assrt.py`, `README.md`, and `catalog.json` found no matches.
  - No-token `https://api.assrt.net/v1/user/quota?token=` probe returned `{"errmsg":"invalid token","status":20001}`.
  - Test-server config check read only `assrt.token` presence and length from `/home/lavx/bazarr-data/config/config.yaml`; the token is empty.
  - Test-server Provider Hub state file `/home/lavx/bazarr-data/provider_hub/state.json` currently has no active Assrt installation.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-assrt --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_assrt.py'`: `7` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/assrt/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - No-token `https://api.assrt.net/v1/user/quota?token=` probe returned `{"errmsg":"invalid token","status":20001}`.
  - `gh pr view 28 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#28` is open, draft, head `catalog-assrt`, base `main`, merge state `CLEAN`.
- Remaining gates:
  - Run SDK smoke search and download when a test Assrt token is available.
  - Core branch `worktree-provider-hub-builtin-replacements` at `f245ae096` is deployed on `bazarr-ui-test`; Assrt remains unproved only because the test token is missing.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with a real token.

### `betaseries`

- Branch: `catalog-betaseries`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/betaseries`
- Current checkpoint: `461ab52 Add BetaSeries provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/29` opened as draft on 2026-06-01, head `catalog-betaseries`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_betaseries.py'`: failed because `providers/betaseries/provider.py` did not exist.
  - Legacy inspection confirmed episode-only behavior, token config, `episodes/display` and `shows/episodes` endpoint selection, `vo` and `vf` language mapping, `seriessub` source filtering, and ZIP/RAR/raw subtitle downloads.
  - `python3 -B -m unittest discover -s tests -p 'test_betaseries.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/betaseries/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution, em-dash, and non-ASCII scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - No-key `https://api.betaseries.com/episodes/display?...` probe returned API error code `1001`, `Please set an API key.`
  - Real search and download smoke require a valid BetaSeries API key.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/betaseries` as a linked git worktree on branch `catalog-betaseries`, clean, and tracking `origin/catalog-betaseries` after push.
  - Branch `catalog-betaseries` was pushed at `461ab52`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `providers/betaseries`, `tests/test_betaseries.py`, and BetaSeries fixtures.
  - `python3 -B -m unittest discover -s tests -p 'test_betaseries.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/betaseries/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/betaseries`, `tests/test_betaseries.py`, `README.md`, and `catalog.json` found no matches.
  - No-key `https://api.betaseries.com/episodes/display?id=1` probe returned API error code `1001`, `Please set an API key.`
  - Test-server config check read only token presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `betaseries.token` is empty.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-betaseries --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_betaseries.py'`: `7` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/betaseries/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, provider code, tests, and fixtures: no matches.
  - No-key `https://api.betaseries.com/episodes/display?id=1` probe returned API error code `1001`, `Please set an API key.`
  - `gh pr view 29 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#29` is open, draft, head `catalog-betaseries`, base `main`, merge state `CLEAN`.
- Remaining gates:
  - Run SDK smoke search and download when a test BetaSeries API key is available.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `betaseries` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with a real API key.

### `greeksubtitles`

- Branch: `catalog-greeksubtitles`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/greeksubtitles`
- Current checkpoint: `da99eee Add GreekSubtitles provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/30` opened on 2026-06-01, head `catalog-greeksubtitles`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_greeksubtitles.py'`: failed because the provider did not yet follow the live `href = "..."` pagination shape.
  - `python3 -B -m unittest discover -s tests -p 'test_greeksubtitles.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider greeksubtitles --language ell --video-fixture tests/fixtures/greeksubtitles_video_game_of_thrones_s01e01.json --expect-min-results 1`: `greeksubtitles ok`.
  - `python3 -B -m sdk smoke-test --provider greeksubtitles --language ell --video-fixture tests/fixtures/greeksubtitles_video_dune.json --expect-min-results 1`: `greeksubtitles ok`.
- Fresh local evidence on 2026-05-31:
  - Branch `catalog-greeksubtitles` was pushed at `da99eee`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_greeksubtitles.py'`: `9` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/greeksubtitles`, `tests/test_greeksubtitles.py`, `README.md`, and `catalog.json` found no matches.
  - `python3 -B -m sdk smoke-test --provider greeksubtitles --language ell --video-fixture tests/fixtures/greeksubtitles_video_dune.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `greeksubtitles ok`.
- Provider Hub test-server evidence on 2026-05-31:
  - Official catalog source dev ref was set to `catalog-greeksubtitles`; refresh returned `13` entries and resolved GreekSubtitles `0.1.0` at commit `da99eee59e5f135130284da247822b332165b646`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `da99eee59e5f135130284da247822b332165b646`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `greeksubtitles`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search with `imdb_id=tt1160419` returned HTTP `200` but no GreekSubtitles rows because the compat refiner title `Dune: Part One` does not match GreekSubtitles' site search shape.
  - Direct active-provider diagnosis on the test container confirmed `Dune: Part One` returns `0` rows while `Dune` returns `22` rows from the same GreekSubtitles provider.
  - Query-only compat search `GET /api/v1/subtitles?query=Dune.2021.1080p.BluRay.x264.mkv&type=movie&languages=el&per_page=100` returned HTTP `200`, `45` total results, and `22` GreekSubtitles results.
  - First GreekSubtitles result: `file_id=29`, release `Dune 2021 1080p HDRip X264 AC3 EVO greek srt`, subtitle id `greeksubtitles:greeksubtitles-2793203-ell`.
  - Compat download `POST /api/v1/download` for `file_id=29` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `107398` bytes. The payload starts with an SRT BOM, cue `1`, and timestamp `00:00:04,120 --> 00:00:08,690`.
- Status:
  - GreekSubtitles is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-greeksubtitles --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_greeksubtitles.py'`: `9` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/greeksubtitles/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, docs, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider greeksubtitles --language ell --video-fixture tests/fixtures/greeksubtitles_video_dune.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `greeksubtitles ok`.
  - `gh pr view 30 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#30` is open, non-draft, head `catalog-greeksubtitles`, base `main`, merge state `CLEAN`.

### `hosszupuska`

- Branch: `catalog-hosszupuska`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/hosszupuska`
- Current checkpoint: `2b36db2 Record Hosszupuska dead-origin recheck`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_hosszupuska.py'`: failed because `providers/hosszupuska/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_hosszupuska.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/hosszupuska/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
- Cleanup evidence on 2026-05-31:
  - Removed active `providers/hosszupuska` manifest and implementation, Hosszupuska-specific tests and fixtures, README entry, and catalog entry.
  - Kept `docs/provider-notes/hosszupuska.md` as a historical dead-origin record only.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - `rg -n "hosszupuska|Hosszupuska" README.md catalog.json providers tests -S`: no matches.
  - `git diff --check` and `git diff --cached --check`: clean.
- Live smoke evidence on 2026-05-29:
  - `curl -I --max-time 15 http://hosszupuskasub.com/`: `curl: (52) Empty reply from server`.
  - `curl -I --max-time 15 https://hosszupuskasub.com/`: `curl: (7) Failed to connect to hosszupuskasub.com port 443`.
  - `curl -L --http1.1 --max-time 20 -A "Mozilla/5.0" http://hosszupuskasub.com/`: HTTP `200` from `openresty`, but body is a ParkLogic redirect/parking script, not the HosszuPuska subtitle site.
  - `curl -L --http1.1 --max-time 20 -A "Mozilla/5.0" "http://hosszupuskasub.com/sorozatok.php?cim=American+Horror+Story&evad=10&resz=01&nyelvtipus=%25&x=24&y=8"`: returned the same ParkLogic redirect/parking script for the legacy search path.
  - `curl -L --http1.1 --max-time 20 -A "Mozilla/5.0" "http://hosszupuskasub.com/download.php?file=0124336.zip"`: returned the same ParkLogic redirect/parking script for a known legacy download path.
  - `curl -I -L --http1.1 --max-time 20 -A "Mozilla/5.0" http://85.255.9.174/`: timed out, so the older indexed origin IP is not reachable from this network.
  - `python3 -B -m sdk smoke-test --provider hosszupuska --language hun --video-fixture tests/fixtures/hosszupuska_video_game_of_thrones_s01e01.json --expect-min-results 1 --skip-download`: failed with `hosszupuska search failed: Remote end closed connection without response`.
  - Recheck on 2026-05-29: `https://www.hosszupuskasub.com/` returned HTTP `200`, but the body is a ParkLogic JavaScript router, not the HosszuPuska subtitle site.
  - Recheck on 2026-05-29: `http://hosszupuskasub.com/sorozatok.php?sid=17617&evad=1&resz=1&nyelv=1` returned HTTP `200`, but the body is the same ParkLogic router for the legacy endpoint.
  - RDAP recheck on 2026-05-29: nameservers are `NS1.PARKLOGIC.COM` and `NS2.PARKLOGIC.COM`; `last changed` is `2026-05-25T23:28:30Z`.
  - Recheck on 2026-05-31: `http://hosszupuskasub.com/` returns HTTP `200` from `openresty`, but the response is a ParkLogic JavaScript router.
  - Recheck on 2026-05-31: `http://hosszupuskasub.com/sorozatok.php?sid=17617` returns the same ParkLogic router for the legacy search path.
  - Fresh recheck on 2026-05-31 after the dead-origin report: `curl -I --max-time 15 http://hosszupuskasub.com/` returns `curl: (52) Empty reply from server`.
  - Bazarr test host recheck on 2026-05-31: `ssh lavx@192.168.100.6 curl -I --max-time 15 http://hosszupuskasub.com/` returns `curl: (52) Empty reply from server`.
  - Fresh recheck on 2026-05-31 after final dead-origin decision: `curl -I --max-time 15 http://hosszupuskasub.com/` returns `curl: (52) Empty reply from server`.
  - Fresh recheck on 2026-06-01 from this migration network: `curl -I --max-time 15 http://hosszupuskasub.com/` fails with `curl: (7) Failed to connect to hosszupuskasub.com port 80`.
  - Bazarr test host recheck on 2026-06-01: `ssh lavx@192.168.100.6 curl -I --max-time 15 http://hosszupuskasub.com/` returns HTTP `308` to `https://hosszupuskasub.com/`, but `curl -I --max-time 15 https://hosszupuskasub.com/` fails with `curl: (35) TLS connect error`, and the legacy `sorozatok.php?sid=17617` path fails the same way after redirect.
- Dead-origin decision:
  - Treat Hosszupuska as dead for Provider Hub migration unless the original site returns or a verified replacement origin is found.
  - Re-run live Hosszupuska smoke only after the domain stops serving ParkLogic parking responses.
  - Do not add `hosszupuska` to the core replacement policy while the origin is dead.
  - Do not open or merge Hosszupuska as an active catalog provider while the origin is dead.
  - Do not require Provider Hub compat search, download, or stream proof while the origin is dead.

### `nekur`

- Branch: `catalog-nekur`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/nekur`
- Current checkpoint: `41e428e Add Nekur provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/31` opened on 2026-06-01, head `catalog-nekur`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_nekur.py'`: failed because `providers/nekur/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_nekur.py'`: `6` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `334` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/nekur/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
- Live smoke evidence on 2026-05-29:
  - Direct live search probe to `https://subtitri.nekur.net/modules/Subtitles.php` returned HTTP `500`, but with a valid result table for `Dune`.
  - Direct live download HEAD for `Dune: Part One` returned HTTP `200`, `Content-Disposition: filename=dune_part_one_2021.zip`, and `Content-Length: 33183`.
  - `python3 -B -m sdk smoke-test --provider nekur --language lav --video-fixture tests/fixtures/nekur_video_dune.json --expect-min-results 1`: `nekur ok`.
  - `/usr/bin/env PATH=/tmp/no-system-tools python3 -B -m sdk smoke-test --provider nekur --language lav --video-fixture tests/fixtures/nekur_video_dune.json --expect-min-results 1`: `nekur ok`.
- Fresh local evidence on 2026-06-01:
  - Branch `catalog-nekur` was pushed at `41e428e`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_nekur.py'`: `6` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/nekur`, `tests/test_nekur.py`, `README.md`, and `catalog.json` found no matches.
  - `python3 -B -m sdk smoke-test --provider nekur --language lav --video-fixture tests/fixtures/nekur_video_dune.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `nekur ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-nekur`; refresh returned `13` entries and resolved Nekur `0.1.0` at commit `41e428e0e98e19cda3f4eae121f2f96f2ca7ddee`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `41e428e0e98e19cda3f4eae121f2f96f2ca7ddee`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `nekur`, and excludes `hosszupuska` and `podnapisi`.
  - Query-only compat search `GET /api/v1/subtitles?query=Dune.2021.1080p.BluRay.x264.mkv&type=movie&languages=lv&per_page=100` returned HTTP `200`, `9` total results, and `1` Nekur result.
  - Nekur result: `file_id=4`, release `DVD, BD`, subtitle id `nekur:nekur-51fcaecad656f7e9894c70d0bab7a3dc`.
  - Compat download `POST /api/v1/download` for `file_id=4` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `82459` bytes. The payload starts with an SRT BOM, cue `1`, and timestamp `00:00:05,339 --> 00:00:10,177`.
- Status:
  - Nekur is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-nekur --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_nekur.py'`: `6` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/nekur/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, docs, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider nekur --language lav --video-fixture tests/fixtures/nekur_video_dune.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `nekur ok`.
  - `gh pr view 31 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#31` is open, non-draft, head `catalog-nekur`, base `main`, merge state `CLEAN`.

### `prijevodionline`

- Branch: `catalog-prijevodionline`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/prijevodionline`
- Current checkpoint: `9c1d795 Add PrijevodiOnline provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/32` opened on 2026-06-01, head `catalog-prijevodionline`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_prijevodionline.py'`: failed because `providers/prijevodionline/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_prijevodionline.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/prijevodionline/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public index, series page, and AJAX subtitle-list probes returned live `Game of Thrones` S01E01 data, including episode id `33945` and key `ca7a167e13db896fe2324b2cbf10311f`.
  - `python3 -B -m sdk smoke-test --provider prijevodionline --language hrv --video-fixture tests/fixtures/prijevodionline_video_game_of_thrones_s01e01.json --expect-min-results 1`: `prijevodionline ok`.
  - `/usr/bin/env PYTHONPATH=/tmp/prijevodionline-deps PATH=/tmp/no-system-tools python3 -B -m sdk smoke-test --provider prijevodionline --language hrv --video-fixture tests/fixtures/prijevodionline_video_game_of_thrones_s01e01.json --expect-min-results 1`: `prijevodionline ok`.
- Fresh local evidence on 2026-06-01:
  - Branch `catalog-prijevodionline` was pushed at `9c1d795`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_prijevodionline.py'`: `7` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/prijevodionline`, `tests/test_prijevodionline.py`, `README.md`, and `catalog.json` found no matches.
  - `python3 -B -m sdk smoke-test --provider prijevodionline --language hrv --video-fixture tests/fixtures/prijevodionline_video_game_of_thrones_s01e01.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `prijevodionline ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-prijevodionline`; refresh returned `13` entries and resolved PrijevodiOnline `0.1.0` at commit `9c1d795c2d058ac4f5acafb042f162fc5367e0d9`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `9c1d795c2d058ac4f5acafb042f162fc5367e0d9`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `prijevodionline`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt0944947&query=Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.mkv&type=episode&season_number=1&episode_number=1&languages=hr&per_page=100` returned HTTP `200`, `43` total results, and `7` PrijevodiOnline results.
  - First PrijevodiOnline result: `file_id=36`, release `HDTV.XviD-FEVER, 720p.HDTV.X264-CTU`, subtitle id `prijevodionline:prijevodionline-18050-hrv`.
  - Compat download `POST /api/v1/download` for `file_id=36` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `36527` bytes. The payload starts with SRT cue `1` and timestamp `00:01:56,627 --> 00:01:58,962`.
- Status:
  - PrijevodiOnline is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-prijevodionline --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_prijevodionline.py'`: `7` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/prijevodionline/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, docs, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider prijevodionline --language hrv --video-fixture tests/fixtures/prijevodionline_video_game_of_thrones_s01e01.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `prijevodionline ok`.
  - `gh pr view 32 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#32` is open, non-draft, head `catalog-prijevodionline`, base `main`, merge state `CLEAN`.

### `soustitreseu`

- Branch: `catalog-soustitreseu`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/soustitreseu`
- Current checkpoint: `ed93af8 Add Soustitres.eu provider`
- PR: `https://github.com/LavX/bazarr-provider-catalog/pull/33` opened on 2026-06-01, head `catalog-soustitreseu`, base `main`, merge state `CLEAN`.
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_soustitreseu.py'`: failed because `providers/soustitreseu/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_soustitreseu.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/soustitreseu/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public search and detail probes returned `Game Of Thrones` S01E01 and `Dune: Part One (2021)` rows.
  - Direct live S01E01 ZIP probe showed French `VF` and English/original `VO` subtitle files.
  - `python3 -B -m sdk smoke-test --provider soustitreseu --language eng --video-fixture tests/fixtures/soustitreseu_video_game_of_thrones_s01e01.json --expect-min-results 1`: `soustitreseu ok`.
  - `python3 -B -m sdk smoke-test --provider soustitreseu --language fra --video-fixture tests/fixtures/soustitreseu_video_dune.json --expect-min-results 1`: `soustitreseu ok`.
  - `/usr/bin/env PYTHONPATH=/tmp/prijevodionline-deps PATH=/tmp/no-system-tools python3 -B -m sdk smoke-test --provider soustitreseu --language eng --video-fixture tests/fixtures/soustitreseu_video_game_of_thrones_s01e01.json --expect-min-results 1`: `soustitreseu ok`.
- Fresh local evidence on 2026-06-01:
  - Branch `catalog-soustitreseu` was pushed at `ed93af8`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_soustitreseu.py'`: `7` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/soustitreseu`, `tests/test_soustitreseu.py`, `README.md`, and `catalog.json` found no matches.
  - `python3 -B -m sdk smoke-test --provider soustitreseu --language eng --video-fixture tests/fixtures/soustitreseu_video_game_of_thrones_s01e01.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `soustitreseu ok`.
  - `python3 -B -m sdk smoke-test --provider soustitreseu --language fra --video-fixture tests/fixtures/soustitreseu_video_dune.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `soustitreseu ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-soustitreseu`; refresh returned `13` entries and resolved Soustitres.eu `0.1.0` at commit `ed93af8f43be7c87a1d0f8b55946504a3038c051`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `ed93af8f43be7c87a1d0f8b55946504a3038c051`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `soustitreseu`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt0944947&query=Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.mkv&type=episode&season_number=1&episode_number=1&languages=en&per_page=100` returned HTTP `200`, `124` total results, and `3` Soustitres.eu results in the first page.
  - First Soustitres.eu result: `file_id=80`, release `Game.Of.Thrones.1x01.ENFR.FBK.zip La Fabrique 1x01`, subtitle id `soustitreseu:soustitreseu-3907850abfd356f1`.
  - Compat download `POST /api/v1/download` for `file_id=80` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `37074` bytes. The payload starts with SRT cue `1` and timestamp `00:01:56,237 --> 00:01:57,432`.
- Status:
  - Soustitres.eu is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.
- Fresh PR evidence on 2026-06-01:
  - `gh pr list --repo LavX/bazarr-provider-catalog --head catalog-soustitreseu --json number,state,isDraft,reviewDecision,mergeStateStatus,url,title`: no existing PR before creation.
  - `python3 -B -m unittest discover -s tests -p 'test_soustitreseu.py'`: `7` tests passed.
  - `python3 -m unittest discover -s tests -p 'test_catalog.py'`: `12` tests passed, `6` skipped.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/soustitreseu/provider.py`: passed.
  - `git diff --check origin/main...HEAD`: clean.
  - Prohibited punctuation and attribution scan across README, catalog, docs, provider code, tests, and fixtures: no matches.
  - `python3 -B -m sdk smoke-test --provider soustitreseu --language eng --video-fixture tests/fixtures/soustitreseu_video_game_of_thrones_s01e01.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `soustitreseu ok`.
  - `python3 -B -m sdk smoke-test --provider soustitreseu --language fra --video-fixture tests/fixtures/soustitreseu_video_dune.json --config-json '{"request_delay_ms":0}' --expect-min-results 1`: `soustitreseu ok`.
  - `gh pr view 33 --repo LavX/bazarr-provider-catalog --json number,url,state,isDraft,headRefName,baseRefName,mergeStateStatus,reviewDecision,statusCheckRollup,title`: PR `#33` is open, non-draft, head `catalog-soustitreseu`, base `main`, merge state `CLEAN`.

### `subclub`

- Branch: `catalog-subclub`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subclub`
- Current checkpoint: `0849eae Add Subclub provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subclub.py'`: failed because `providers/subclub/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_subclub.py'`: `8` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `336` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/subclub/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public search probes returned `Inception` archive id `10100` and `Game of Thrones` S01E01 archive id `11232`.
  - Public archive-list probes returned direct `.srt` download links for both archive ids.
  - `python3 -B -m sdk smoke-test --provider subclub --language est --video-fixture tests/fixtures/subclub_video_inception.json --expect-min-results 1`: `subclub ok`.
  - `python3 -B -m sdk smoke-test --provider subclub --language est --video-fixture tests/fixtures/subclub_video_game_of_thrones_s01e01.json --expect-min-results 1`: `subclub ok`.
- Fresh local evidence on 2026-06-01:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subclub.py'`: `8` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over Subclub provider, tests, README, catalog, and notes found no matches.
  - `python3 -B -m sdk smoke-test --provider subclub --language est --video-fixture tests/fixtures/subclub_video_inception.json --expect-min-results 1`: `subclub ok`.
  - `python3 -B -m sdk smoke-test --provider subclub --language est --video-fixture tests/fixtures/subclub_video_game_of_thrones_s01e01.json --expect-min-results 1`: `subclub ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - `catalog-subclub` was pushed to origin and now tracks `origin/catalog-subclub` at `0849eaee2abea0090c5c6da8d45acd16e074104e`.
  - Official catalog source dev ref was set to `catalog-subclub`; refresh returned `13` entries and resolved Subclub `0.1.0` at commit `0849eaee2abea0090c5c6da8d45acd16e074104e`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `0849eaee2abea0090c5c6da8d45acd16e074104e`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subclub`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1375666&query=Inception.2010.720p.BluRay.x264-CROSSBOW.mkv&type=movie&languages=et&per_page=100` returned HTTP `200`, `18` total results, and `7` Subclub results.
  - Matching Subclub result: `file_id=13`, release `Inception.720p.BluRay.x264-CROSSBOW.srt`, subtitle id `subclub:subclub-10100-e6f277f38e88`.
  - Compat download `POST /api/v1/download` for `file_id=13` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `108649` bytes. The payload starts with SRT cue `1` and timestamp `00:01:25,168 --> 00:01:26,690`.
- Status:
  - Subclub is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `subssabbz`

- Branch: `catalog-subssabbz`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subssabbz`
- Current checkpoint: `3aa8f02 Add SubsSabBz provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subssabbz.py'`: failed because `providers/subssabbz/provider.py` did not exist.
  - Red 403 retry gate `python3 -B -m unittest discover -s tests -p 'test_subssabbz.py'`: failed because `_http_post` propagated the first HTTP `403`.
  - `python3 -B -m unittest discover -s tests -p 'test_subssabbz.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/subssabbz/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public probes confirmed `select-language=2` returns Bulgarian rows and `select-language=1` returns English rows.
  - Live Bulgarian Inception archive `attach_id=51764` was a ZIP with an `.srt` subtitle.
  - Live English Inception archive `attach_id=51168` was a RAR archive.
  - `python3 -B -m sdk smoke-test --provider subssabbz --language bul --video-fixture tests/fixtures/subssabbz_video_inception.json --expect-min-results 1`: `subssabbz ok`.
  - `python3 -B -m sdk smoke-test --provider subssabbz --language eng --video-fixture tests/fixtures/subssabbz_video_inception.json --expect-min-results 1`: `subssabbz ok`.
  - `python3 -B -m sdk smoke-test --provider subssabbz --language bul --video-fixture tests/fixtures/subssabbz_video_game_of_thrones_s01e01.json --expect-min-results 1`: `subssabbz ok`.
  - `PYTHONPATH=/tmp/prijevodionline-deps PATH=/tmp/no-system-tools /usr/bin/python3 -B -m sdk smoke-test --provider subssabbz --language eng --video-fixture tests/fixtures/subssabbz_video_inception.json --expect-min-results 1`: `subssabbz ok`.
- Fresh local evidence on 2026-06-01:
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subssabbz.py'`: `9` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over SubsSabBz provider, tests, README, catalog, and notes found no matches.
  - `python3 -B -m sdk smoke-test --provider subssabbz --language bul --video-fixture tests/fixtures/subssabbz_video_inception.json --expect-min-results 1`: `subssabbz ok`.
  - `python3 -B -m sdk smoke-test --provider subssabbz --language eng --video-fixture tests/fixtures/subssabbz_video_inception.json --expect-min-results 1`: `subssabbz ok`.
  - Fresh episode smoke `python3 -B -m sdk smoke-test --provider subssabbz --language bul --video-fixture tests/fixtures/subssabbz_video_game_of_thrones_s01e01.json --expect-min-results 1` exceeded a bounded wait and was terminated; the 2026-05-29 episode smoke remains the latest successful episode proof.
- Provider Hub test-server evidence on 2026-06-01:
  - `catalog-subssabbz` was pushed to origin and now tracks `origin/catalog-subssabbz` at `3aa8f02b8c5b344f937cb665243967d0a31fcdff`.
  - Official catalog source dev ref was set to `catalog-subssabbz`; refresh returned `13` entries and resolved SubsSabBz `0.1.0` at commit `3aa8f02b8c5b344f937cb665243967d0a31fcdff`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `3aa8f02b8c5b344f937cb665243967d0a31fcdff`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subssabbz`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1375666&query=Inception.2010.DVDRip.XviD-ARROW.mkv&type=movie&languages=en&per_page=100` returned HTTP `200`, `377` total results, and `5` SubsSabBz results in the first page.
  - Matching SubsSabBz result: `file_id=2`, release `arrow-inception.cd1.srt`, subtitle id `subssabbz:subssabbz-ef44ff32bc070795`.
  - Compat download `POST /api/v1/download` for `file_id=2` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `70434` bytes. The payload starts with SRT cue `1` and timestamp `00:01:24,597 --> 00:01:26,119`.
- Status:
  - SubsSabBz is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for the English movie path.

### `subsunacs`

- Branch: `catalog-subsunacs`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subsunacs`
- Current checkpoint: `6394dff Add SubsUnacs provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subsunacs.py'`: failed because `providers/subsunacs/provider.py` did not exist.
  - Red archive permission gate `python3 -B -m unittest discover -s tests -p 'test_subsunacs.py'`: failed because `py7zz` extraction could leave unreadable temp files.
  - `python3 -B -m unittest discover -s tests -p 'test_subsunacs.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/subsunacs/provider.py`: passed.
  - `PYTHONPATH=/tmp/prijevodionline-deps PATH=/tmp/no-system-tools /usr/bin/python3 ...`: bundled `py7zz` 7Z extraction passed without system archive tools.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public probes confirmed `l=0` returns Bulgarian rows and `l=1` returns English rows.
  - Current public detail pages expose direct `/getentry.php?id=<id>&ei=<index>` subtitle files.
  - `python3 -B -m sdk smoke-test --provider subsunacs --language eng --video-fixture tests/fixtures/subsunacs_video_dune.json --expect-min-results 1`: `subsunacs ok`.
  - `python3 -B -m sdk smoke-test --provider subsunacs --language bul --video-fixture tests/fixtures/subsunacs_video_game_of_thrones_s01e01.json --expect-min-results 1`: `subsunacs ok`.
- Fresh local evidence on 2026-06-01:
  - Recreated the isolated provider worktree at `/tmp/bazarr_catalog_provider_worktrees/subsunacs` from branch `catalog-subsunacs`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subsunacs.py'`: `9` tests passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over SubsUnacs provider, tests, README, catalog, and notes found no matches.
  - `python3 -B -m sdk smoke-test --provider subsunacs --language eng --video-fixture tests/fixtures/subsunacs_video_dune.json --expect-min-results 1`: `subsunacs ok`.
  - `python3 -B -m sdk smoke-test --provider subsunacs --language bul --video-fixture tests/fixtures/subsunacs_video_game_of_thrones_s01e01.json --expect-min-results 1`: `subsunacs ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - `catalog-subsunacs` was pushed to origin and now tracks `origin/catalog-subsunacs` at `6394dffff34e63c0dd1d0ec752321926ba1873a2`.
  - Official catalog source dev ref was set to `catalog-subsunacs`; refresh returned `13` entries and resolved SubsUnacs `0.1.0` at commit `6394dffff34e63c0dd1d0ec752321926ba1873a2`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `6394dffff34e63c0dd1d0ec752321926ba1873a2`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subsunacs`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt0944947&query=Game.of.Thrones.S01E01.HDTV.XviD-FEVER.avi&type=episode&season_number=1&episode_number=1&languages=bg&per_page=100` returned HTTP `200`, `30` total results, and `1` SubsUnacs result.
  - Matching SubsUnacs result: `file_id=135`, release `game.of.thrones.s01e01.hdtv.xvid-fever.srt`, subtitle id `subsunacs:subsunacs-36cfef1da3529b21`.
  - Compat download `POST /api/v1/download` for `file_id=135` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `51676` bytes. The payload starts with SRT cue `1` and timestamp `00:01:55,418 --> 00:01:58,420`.
- Status:
  - SubsUnacs is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for the Bulgarian episode path.

### `subsynchro`

- Branch: `catalog-subsynchro`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subsynchro`
- Current checkpoint: `20b207d Fix SubSynchro malformed release anchors`
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subsynchro.py'`: failed because `providers/subsynchro/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_subsynchro.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/subsynchro/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider subsynchro --language fra --video-fixture tests/fixtures/subsynchro_video_the_plastic_detox.json --expect-min-results 1`: failed with `subsynchro search failed: The read operation timed out`.
  - `POST /tous-les-films.html` for `The Plastic Detox` returned HTTP `302` to `/2025/33547-the-plastic-detox.html`, then timed out after `25` seconds with zero bytes received.
  - Direct homepage, film page, and download URL probes timed out with zero bytes received.
- Fresh local and live evidence on 2026-06-01:
  - Root cause traced to current live markup containing malformed group-anchor HTML, `EDITH</<a></li>`, before the real release anchor. The previous anchor parser skipped the release link in that row.
  - Added regression coverage for malformed group anchors and changed release-link extraction to scan raw anchor start tags.
  - `python3 -B -m sdk build-catalog`: `wrote catalog.json`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subsynchro.py'`: `10` tests passed.
  - `python3 -B -m py_compile providers/subsynchro/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subsynchro`, `tests/test_subsynchro.py`, `README.md`, `catalog.json`, and `docs/provider-notes/subsynchro.md` found no matches.
  - `python3 -B -m sdk smoke-test --provider subsynchro --language fra --video-fixture tests/fixtures/subsynchro_video_the_plastic_detox.json --expect-min-results 1`: `subsynchro ok`.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
- Provider Hub test-server evidence on 2026-06-01:
  - Branch `catalog-subsynchro` was pushed at `20b207d`.
  - Official catalog source dev ref was set to `catalog-subsynchro`; refresh returned `13` entries and resolved SubSynchro `0.1.1` at commit `20b207dc8750b419825fc2478322be79d77f8659`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.1`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `20b207dc8750b419825fc2478322be79d77f8659`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subsynchro`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?query=The.Plastic.Detox.2025.1080p.WEB.H264-EDITH.mkv&type=movie&languages=fr&per_page=100` returned HTTP `200`, `13` total results, and `1` SubSynchro result.
  - SubSynchro result: `file_id=1`, release `The.Plastic.Detox.2026.1080p.WEB.H264-EDITH`, subtitle id `subsynchro:subsynchro-986`.
  - Compat download `POST /api/v1/download` for `file_id=1` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `116017` bytes. The payload starts with SRT cue `1` and timestamp `00:00:06,006 --> 00:00:11,469`.
- Status:
  - SubSynchro is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for the French movie path.

### `subtitrarinoi`

- Branch: `catalog-subtitrarinoi`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subtitrarinoi`
- Current checkpoint: `8fc7785 Add Subtitrari Noi provider`
- Local evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subtitrarinoi.py'`: failed because `providers/subtitrarinoi/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_subtitrarinoi.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/subtitrarinoi/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - Public probes confirmed `POST /paginare_filme.php` returns current `div id="round"` rows for IMDb and title searches.
  - The live Breaking Bad row exposes a working ZIP archive at the legacy relative download URL shape.
  - `python3 -B -m sdk smoke-test --provider subtitrarinoi --language ron --video-fixture tests/fixtures/subtitrarinoi_video_breaking_bad_s01e01.json --expect-min-results 1`: `subtitrarinoi ok`.
- Fresh local and live evidence on 2026-06-01:
  - Branch `catalog-subtitrarinoi` was pushed at `8fc7785`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subtitrarinoi.py'`: `11` tests passed.
  - `python3 -B -m py_compile providers/subtitrarinoi/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subtitrarinoi`, `tests/test_subtitrarinoi.py`, `README.md`, `catalog.json`, and `docs/provider-notes/subtitrarinoi.md` found no matches.
  - `python3 -B -m sdk smoke-test --provider subtitrarinoi --language ron --video-fixture tests/fixtures/subtitrarinoi_video_breaking_bad_s01e01.json --expect-min-results 1`: `subtitrarinoi ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-subtitrarinoi`; refresh returned `13` entries and resolved Subtitrari Noi `0.1.0` at commit `8fc7785dd05cb37b48c74485fe982dd7ebf45348`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `8fc7785dd05cb37b48c74485fe982dd7ebf45348`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subtitrarinoi`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt0903747&query=Breaking.Bad.S01E01.2160p.WEB-DL-CRFW.mkv&type=episode&season_number=1&episode_number=1&languages=ro&per_page=100` returned HTTP `200`, `21` total results, and `1` Subtitrari Noi result.
  - Subtitrari Noi result: `file_id=20`, release `Sezoanele 1-5 complete`, subtitle id `subtitrarinoi:subtitrarinoi-74168-ro`.
  - Compat download `POST /api/v1/download` for `file_id=20` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `39467` bytes. The payload starts with SRT cue `1` and timestamp `00:01:15,408 --> 00:01:17,619`.
- Status:
  - Subtitrari Noi is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for the Romanian episode path.

### `subtitriid`

- Branch: `catalog-subtitriid`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subtitriid`
- Current checkpoint: `ba6e108 Add Subtitri.id provider`
- Local evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subtitriid.py'`: failed because `providers/subtitriid/provider.py` did not exist.
  - Parser edge red gate for download URL entry id: failed with `AssertionError: '20' != '406'`, then passed after `_entry_id_from_url` learned the uCoz `/load/0-0-0-406-20` shape.
  - `python3 -B -m unittest discover -s tests -p 'test_subtitriid.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/subtitriid/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `curl -I -L --http1.1 --max-time 20 -A "Mozilla/5.0" https://subtitri.do.am/`: HTTP `200`.
  - `curl -L --http1.1 --max-time 20 -A "Mozilla/5.0" "https://subtitri.do.am/search/?q=Inception"` returned a live `eBlock` result for `Inception`.
  - The live `Inception` detail page exposes `main-header`, `film-page-year`, IMDb `tt1375666`, and download URL `/load/0-0-0-406-20`.
  - `curl -L --http1.1 --max-time 20 -A "Mozilla/5.0" -e "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406" -o /tmp/subtitriid_inception_download.bin "https://subtitri.do.am/load/0-0-0-406-20"` returned a ZIP archive with one SRT file.
  - `python3 -B -m sdk smoke-test --provider subtitriid --language lav --video-fixture tests/fixtures/subtitriid_video_inception.json --expect-min-results 1`: `subtitriid ok`.
- Fresh local and live evidence on 2026-06-01:
  - Branch `catalog-subtitriid` was pushed at `ba6e108`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_subtitriid.py'`: `12` tests passed.
  - `python3 -B -m py_compile providers/subtitriid/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subtitriid`, `tests/test_subtitriid.py`, `README.md`, `catalog.json`, and `docs/provider-notes/subtitriid.md` found no matches.
  - `python3 -B -m sdk smoke-test --provider subtitriid --language lav --video-fixture tests/fixtures/subtitriid_video_inception.json --expect-min-results 1`: `subtitriid ok`.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-subtitriid`; refresh returned `13` entries and resolved Subtitri.id `0.1.0` at commit `ba6e1080efac534185f237c11ddd0c5a6a804bec`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `ba6e1080efac534185f237c11ddd0c5a6a804bec`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `subtitriid`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1375666&query=Inception.2010.1080p.BluRay.x264.mkv&type=movie&languages=lv&per_page=100` returned HTTP `200`, `4` total results, and `1` Subtitri.id result.
  - Subtitri.id result: `file_id=2`, release `Inception (2010)`, subtitle id `subtitriid:subtitriid-406-lv`.
  - Compat download `POST /api/v1/download` for `file_id=2` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `110497` bytes. The payload starts with SRT cue `1` and timestamp `00:01:29,720 --> 00:01:34,669`.
- Status:
  - Subtitri.id is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for the Latvian movie path.

### `supersubtitles`

- Branch: `catalog-supersubtitles`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/supersubtitles`
- Current checkpoint: `3f96078 Add SuperSubtitles provider`
- Local evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_supersubtitles.py'`: failed because `providers/supersubtitles/provider.py` did not exist.
  - Live smoke red gate before HTTP hardening: `HTTP Error 403: Forbidden` from feliratok.eu search endpoints.
  - Root cause probes showed feliratok.eu search requires a root `Referer` and rejects the extra `BazarrProviderHub` User-Agent token, while a plain Chrome-style User-Agent succeeds.
  - Download red gate: movie download failed because feliratok.eu returned raw spaces in the `fnev` query value; `_request_url` now encodes the request URL while preserving query separators.
  - `python3 -B -m unittest discover -s tests -p 'test_supersubtitles.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `python3 -B -m py_compile providers/supersubtitles/provider.py`: passed.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider supersubtitles --language hun --video-fixture tests/fixtures/supersubtitles_video_dune_2021.json --expect-min-results 1 --skip-download`: `supersubtitles ok`.
  - `python3 -B -m sdk smoke-test --provider supersubtitles --language eng --video-fixture tests/fixtures/supersubtitles_video_la_brea_s02e13.json --expect-min-results 1 --skip-download`: `supersubtitles ok`.
  - `python3 -B -m sdk smoke-test --provider supersubtitles --language hun --video-fixture tests/fixtures/supersubtitles_video_dune_2021.json --expect-min-results 1`: `supersubtitles ok`.
  - `python3 -B -m sdk smoke-test --provider supersubtitles --language eng --video-fixture tests/fixtures/supersubtitles_video_la_brea_s02e13.json --expect-min-results 1`: `supersubtitles ok`.
- Fresh local and live evidence on 2026-06-01:
  - Branch `catalog-supersubtitles` was pushed at `3f96078`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_supersubtitles.py'`: `13` tests passed.
  - `python3 -B -m py_compile providers/supersubtitles/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/supersubtitles`, `tests/test_supersubtitles.py`, `README.md`, `catalog.json`, and `docs/provider-notes/supersubtitles.md` found no matches.
  - `python3 -B -m sdk smoke-test --provider supersubtitles --language hun --video-fixture tests/fixtures/supersubtitles_video_dune_2021.json --expect-min-results 1`: `supersubtitles ok`.
  - `python3 -B -m sdk smoke-test --provider supersubtitles --language eng --video-fixture tests/fixtures/supersubtitles_video_la_brea_s02e13.json --expect-min-results 1`: `supersubtitles ok`.
  - `python3 -B -m unittest discover -s tests`: `341` tests passed, `6` skipped.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-supersubtitles`; refresh returned `13` entries and resolved SuperSubtitles `0.1.0` at commit `3f960782f5641af2e23f85db31a90c1873827070`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `3f960782f5641af2e23f85db31a90c1873827070`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `supersubtitles`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?query=Dune.2021.1080p.WEB-DL.HONE.mkv&type=movie&languages=hu&per_page=100` returned HTTP `200`, `24` total results, and `6` SuperSubtitles results.
  - First SuperSubtitles movie result: `file_id=67`, release `Dune (2021) (WEBRip.1080p-HiDt, MA.WEB-DL.1080p-HONE, MA.WEB-DL.2160p-FLUX)`, subtitle id `supersubtitles:supersubtitles-1735404922-hun`.
  - Compat download `POST /api/v1/download` for movie `file_id=67` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat movie stream returned HTTP `200` and `69129` bytes. The payload starts with SRT cue `1` and timestamp `00:00:05,364 --> 00:00:10,497`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt11640018&query=La.Brea.S02E13.720p.WEB.H264-CAKES.mkv&type=episode&season_number=2&episode_number=13&languages=en&per_page=100` returned HTTP `200`, `29` total results, and `1` SuperSubtitles result.
  - SuperSubtitles episode result: `file_id=31`, release `La Brea (AMZN.WEB-DL.1080p-NTb, AMZN.WEB-DL.720p-NTb, WEB.1080p-CAKES, WEB.1080p-GLHF, WEB.1080p-GOSSIP, WEB.1080p-KOGi, WEB.1080p-PLZPROPER, WEB.720p-CAKES, WEB.720p-GLHF, WEB.720p-GOSSIP, WEB.720p-KOGi, WEB.720p-PLZPROPER, WEBRip-ION10, WEBRip-ION265, WEBRip.1080p-RARBG)`, subtitle id `supersubtitles:supersubtitles-1691315119-eng`.
  - Compat download `POST /api/v1/download` for episode `file_id=31` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat episode stream returned HTTP `200` and `41393` bytes. The payload starts with SRT cue `1` and timestamp `00:00:00,000 --> 00:00:04,448`.
- Status:
  - SuperSubtitles is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for Hungarian movie and English episode paths.

### `titrari`

- Branch: `catalog-titrari`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/titrari`
- Current checkpoint: `0781631 Add Titrari provider`
- Local evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_titrari.py'`: failed because `providers/titrari/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_titrari.py'`: `10` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/titrari/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `python3 -B -m sdk smoke-test --provider titrari --language ron --video-fixture tests/fixtures/titrari_video_dune_2021.json --expect-min-results 1 --skip-download`: `titrari ok`.
  - `python3 -B -m sdk smoke-test --provider titrari --language ron --video-fixture tests/fixtures/titrari_video_chernobyl_s01e01.json --expect-min-results 1 --skip-download`: `titrari ok`.
  - `python3 -B -m sdk smoke-test --provider titrari --language ron --video-fixture tests/fixtures/titrari_video_dune_2021.json --expect-min-results 1`: `titrari ok`.
  - `python3 -B -m sdk smoke-test --provider titrari --language ron --video-fixture tests/fixtures/titrari_video_chernobyl_s01e01.json --expect-min-results 1`: `titrari ok`.
- Fresh local and live evidence on 2026-06-01:
  - Branch `catalog-titrari` was pushed at `0781631`.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m unittest discover -s tests -p 'test_titrari.py'`: `12` tests passed.
  - `python3 -B -m py_compile providers/titrari/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/titrari`, `tests/test_titrari.py`, `README.md`, `catalog.json`, and `docs/provider-notes/titrari.md` found no matches.
  - `python3 -B -m sdk smoke-test --provider titrari --language ron --video-fixture tests/fixtures/titrari_video_dune_2021.json --expect-min-results 1`: `titrari ok`.
  - `python3 -B -m sdk smoke-test --provider titrari --language ron --video-fixture tests/fixtures/titrari_video_chernobyl_s01e01.json --expect-min-results 1`: `titrari ok`.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-titrari`; refresh returned `13` entries and resolved Titrari `0.1.0` at commit `078163157f5e0613830dbba631565af735532d91`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`, revision `f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `078163157f5e0613830dbba631565af735532d91`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `titrari`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1160419&query=Dune.2021.1080p.WEB.FLUX.mkv&type=movie&languages=ro&per_page=100` returned HTTP `200`, `50` total results, and `4` Titrari results.
  - First Titrari movie result: `file_id=11`, release `Dune.2021.720p.BRRip.XviD.AC3-XVID Dune.2021.BRRip.XviD.MP3-XVID Dune 2021 1080p EUR BluRay AVC Atmos TrueHD 7 1-EVO Dune 2021 1080p Bluray Atmos TrueHD 7 1 x264-EVO Dune.2021.1080p.BluRay.REMUX.AVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT Dune.2021.1080p.BluRay.AVC.TrueHD.7.1.Atmos-CYBER Dune.2021.1080p.HMAX.WEB-DL.DDP5.1.Atmos.x264-EVO Dune.2021.1080p.HMAX.WEBRip.DDP5.1.Atmos.x264-CM Dune.2021.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA Dune.2021.720p.HMAX.WEBRip.HQ.x265.10bit-GalaxyRG Dune.2021.1080p.HMAX.WEBRip.AAC5.1.10bits.x265-Rapta Dune.2021.1080p.HMAX.WEB-DL.DDP5.1.Atmos.HDR.H.265-FLUX`, subtitle id `titrari:titrari-124410-ron`.
  - Compat download `POST /api/v1/download` for movie `file_id=11` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat movie stream returned HTTP `200` and `93523` bytes. The payload starts with SRT cue `1` and timestamp `00:00:06,568 --> 00:00:11,398`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt7366338&query=Chernobyl.S01E01.1080p.BluRay.mkv&type=episode&season_number=1&episode_number=1&languages=ro&per_page=100` returned HTTP `200`, `28` total results, and `2` Titrari results.
  - First Titrari episode result: `file_id=54`, release `Sezonul 1 complet, 5 episoade , pentru 720p, 1080p, WEB-DL & BluRay`, subtitle id `titrari:titrari-116927-ron`.
  - Compat download `POST /api/v1/download` for episode `file_id=54` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat episode stream returned HTTP `200` and `34586` bytes. The payload starts with SRT cue `1` and timestamp `00:00:39,832 --> 00:00:41,667`.
- Status:
  - Titrari is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test` for Romanian movie and episode paths.

### `yavkanet`

- Branch: `catalog-yavkanet`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/yavkanet`
- Current checkpoint: `000921d Add YavkaNet provider`
- Local evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_yavkanet.py'`: failed because `providers/yavkanet/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_yavkanet.py'`: `10` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/yavkanet/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live smoke evidence on 2026-05-29:
  - `curl -L --http1.1 --max-time 25 -A "Mozilla/5.0" https://yavka.net/`: HTTP `403`, Cloudflare managed challenge page.
  - `curl -L --http1.1 --max-time 25 -A "Mozilla/5.0" -e "https://yavka.net/" https://yavka.net/imdb/tt1160419`: HTTP `403`, Cloudflare managed challenge page.
  - `python3 -B -m sdk smoke-test --provider yavkanet --language bul --video-fixture tests/fixtures/yavkanet_video_dune_2021.json --expect-min-results 1 --skip-download`: failed with `yavkanet hit a Cloudflare challenge and no FlareSolverr URL is configured`.
- Fresh local evidence on 2026-06-01:
  - Branch `catalog-yavkanet` was pushed at `000921d`.
  - `python3 -B -m unittest discover -s tests -p 'test_yavkanet.py'`: `12` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/yavkanet/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/yavkanet`, `tests/test_yavkanet.py`, `README.md`, `catalog.json`, and `docs/provider-notes/yavkanet.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-yavkanet`; refresh returned `13` entries and resolved YavkaNet `0.1.0` at commit `000921de80907ce8d4028f64b8488543c8026350`.
  - Provider Hub staged YavkaNet `0.1.0`, installed dependencies successfully, and saved config `flaresolverr_url=http://127.0.0.1:8191/v1`, `flaresolverr_timeout_ms=60000`, and `request_delay_ms=0`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `000921de80907ce8d4028f64b8488543c8026350`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `yavkanet`, and excludes `hosszupuska` and `podnapisi`.
  - FlareSolverr endpoint `http://127.0.0.1:8191/v1` is reachable from inside `bazarr-ui-test`; a probe against `https://example.com` returned status `ok`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1160419&query=Dune.2021.1080p.WEB-DL.FLUX.mkv&type=movie&languages=bg&per_page=100` returned HTTP `200`, `14` total results, and `0` YavkaNet results.
  - Direct active-bundle search with the same Dune fixture and stored FlareSolverr config failed with `CloudflareBlockedError: yavkanet FlareSolverr request failed: timed out`.
  - Direct FlareSolverr probe for `https://yavka.net/imdb/tt1160419` with `maxTimeout=180000` failed after `186.52` seconds with HTTP `500`.
  - FlareSolverr logs for that probe show `Challenge detected. Title found: Just a moment...` followed by `Error solving the challenge. Timeout after 180.0 seconds.`
- Remaining gates:
  - Treat current YavkaNet proof as blocked by the origin Cloudflare challenge, not by Provider Hub config or branch deployment.
  - Re-run live smoke with a solver, cookie, or FlareSolverr environment that can actually solve `https://yavka.net/imdb/tt1160419`.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `yifysubtitles`

- Branch: `catalog-yifysubtitles`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/yifysubtitles`
- Current checkpoint: `6d4ecff Add YIFYSubtitles provider`
- Local evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_yifysubtitles.py'`: failed because `providers/yifysubtitles/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_yifysubtitles.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/yifysubtitles/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Attribution and em-dash scan over touched files found no matches.
- Live evidence on 2026-05-29:
  - `curl -L --http1.1 --max-time 25 -A "Mozilla/5.0" -e "https://yifysubtitles.ch" https://yifysubtitles.ch/movie-imdb/tt1160419`: HTTP `200`, HTML table with `other-subs` rows.
  - `curl -L --http1.1 --max-time 25 -A "Mozilla/5.0" -e "https://yifysubtitles.ch/movie-imdb/tt1160419" https://yifysubtitles.ch/subtitles/dune-part-one-2021-english-yify-364913`: HTTP `200`, detail page exposed `/subtitle/dune-2021-english-yify-364913.zip`.
  - `curl -L --http1.1 --max-time 25 -A "Mozilla/5.0" -e "https://yifysubtitles.ch/subtitles/dune-part-one-2021-english-yify-364913" https://yifysubtitles.ch/subtitle/dune-2021-english-yify-364913.zip`: HTTP `200`, `application/zip`, one SRT member.
  - `python3 -B -m sdk smoke-test --provider yifysubtitles --language eng --video-fixture tests/fixtures/yifysubtitles_video_dune_2021.json --expect-min-results 1 --skip-download`: could not be run with network because escalation approval review timed out; without escalation it failed DNS.
- Fresh local and live evidence on 2026-06-01:
  - Branch `catalog-yifysubtitles` was pushed at `6d4ecff`.
  - `python3 -B -m unittest discover -s tests -p 'test_yifysubtitles.py'`: `9` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/yifysubtitles/provider.py`: passed.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/yifysubtitles`, `tests/test_yifysubtitles.py`, `README.md`, `catalog.json`, and `docs/provider-notes/yifysubtitles.md` found no matches.
  - `python3 -B -m unittest discover -s tests`: `337` tests passed, `6` skipped.
  - `python3 -B -m sdk smoke-test --provider yifysubtitles --language eng --video-fixture tests/fixtures/yifysubtitles_video_dune_2021.json --expect-min-results 1`: `yifysubtitles ok`.
- Provider Hub test-server evidence on 2026-06-01:
  - Official catalog source dev ref was set to `catalog-yifysubtitles`; refresh returned `13` entries and resolved YIFYSubtitles `0.1.0` at commit `6d4ecffd73a0f47c30940ec7278c47dcc7f374fc`.
  - Provider Hub staged YIFYSubtitles `0.1.0`, found no broken requirements, and saved config `request_delay_ms=0`.
  - `bazarr-ui-test` restarted healthy on image `ui-test-20260531-provider-hub-replacements-f245ae096`.
  - Provider state after restart: active version `0.1.0`, `pending_restart=false`, `trusted=true`, `enabled=true`, `last_error=None`, manifest commit `6d4ecffd73a0f47c30940ec7278c47dcc7f374fc`.
  - Runtime replacement policy contains `55` trusted migrated built-in ids, includes `yifysubtitles`, and excludes `hosszupuska` and `podnapisi`.
  - Compat search `GET /api/v1/subtitles?imdb_id=tt1160419&query=Dune.2021.1080p.HMAX.WEBRip.DDP5.1.Atmos.x264-CM.mkv&type=movie&languages=en&per_page=100` returned HTTP `200`, `115` total results, `100` page items, and `35` YIFYSubtitles rows.
  - First YIFYSubtitles result: `file_id=17`, release `Dune.2021.1080p.HDRip.X264.AC3-EVO Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX Dune.2021.1080p.WEBRip.x264-RARBG Dune.2021.1080p.WEBRip.DD5.1.x264-KOGI Dune (2021) [1080p] [WEBRip] [YTS.MX] Dune (2021) [720p] [WEBRip] [YTS.MX] Dune.2021.1080p.WEBRip.x264.AAC5.1-[YTS.MX] Dune.2021.1080p.HDRip.1600MB.DD5.1.x264-GalaxyRG Dune.2021.720p.HDRip.900MB.x264-GalaxyRG`, subtitle id `yifysubtitles:yifysubtitles-364092-eng`.
  - Compat download `POST /api/v1/download` for `file_id=17` returned HTTP `200`, a stream link, `remaining=999`, and `remaining_downloads=999`.
  - Compat stream returned HTTP `200` and `72273` bytes. The payload starts with an SRT BOM, cue `1`, and timestamp `00:00:04,120 --> 00:00:08,690`.
- Status:
  - YIFYSubtitles is locally validated and proved through Provider Hub compat search, download, and stream on `bazarr-ui-test`.

### `hdbits`

- Branch: `catalog-hdbits`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/hdbits`
- Current checkpoint: `42d7f02 Add HDBits provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_hdbits.py'`: failed because `providers/hdbits/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_hdbits.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/hdbits/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - JSON parse check for `provider.json`, `catalog.json`, and HDBits fixtures: passed.
  - Attribution, em-dash, and non-ASCII scan over touched HDBits files found no matches.
- Live evidence on 2026-05-29:
  - `curl -L --max-time 20 -H 'Content-Type: application/json' --data '{}' https://hdbits.org/api/torrents`: returned HDBits JSON `{"status":3,"message":"Json missing or malformed"}`.
  - `curl -L -I --max-time 20 https://hdbits.org/`: redirected to `/login?returnto=%2F`; the login page returned a Cloudflare challenge. The JSON API endpoint still responded to the no-credential probe.
  - Real live search and download require an HDBits `username` and `passkey`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/hdbits` as a linked git worktree on branch `catalog-hdbits`, clean, and tracking `origin/catalog-hdbits` after push.
  - Branch `catalog-hdbits` was pushed at `42d7f02`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/hdbits.md`, `providers/hdbits`, `tests/test_hdbits.py`, and HDBits fixtures.
  - `python3 -B -m unittest discover -s tests -p 'test_hdbits.py'`: `11` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/hdbits/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `339` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/hdbits`, `tests/test_hdbits.py`, `docs/provider-notes/hdbits.md`, `README.md`, and `catalog.json` found no matches.
  - No-credential `https://hdbits.org/api/torrents` probe returned `{"status":3,"message":"Json missing or malformed"}`.
  - Test-server config check read only credential presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `hdbits.username` and `hdbits.passkey` are empty.
- Remaining gates:
  - Run SDK live smoke search and download with real HDBits credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `hdbits` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured HDBits credentials.

### `jimaku`

- Branch: `catalog-jimaku`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/jimaku`
- Current checkpoint: `112d345 Add Jimaku provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_jimaku.py'`: failed because `providers/jimaku/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_jimaku.py'`: `15` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/jimaku/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `343` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - JSON parse check for `provider.json`, `catalog.json`, and Jimaku fixtures: passed.
  - Attribution, em-dash, and non-ASCII scan over touched Jimaku files found no matches.
- Live evidence on 2026-05-29:
  - `curl -L --max-time 20 https://jimaku.cc/api/openapi.json`: returned OpenAPI `3.0.3` with API-key auth, `/api/entries/search`, `/api/entries/{id}/files`, AniList/TMDB entry backing, and rate-limit headers.
  - `curl -L --max-time 20 'https://jimaku.cc/api/entries/search?query=one%20piece'`: returned Jimaku JSON `{"error":"unauthorized","code":7}` without an API key.
  - Real live search and download require a Jimaku `api_key`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/jimaku` as a linked git worktree on branch `catalog-jimaku`, clean, and tracking `origin/catalog-jimaku` after push.
  - Branch `catalog-jimaku` was pushed at `112d345`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/jimaku.md`, `providers/jimaku`, `tests/test_jimaku.py`, and Jimaku fixtures.
  - `python3 -B -m unittest discover -s tests -p 'test_jimaku.py'`: `15` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/jimaku/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `343` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/jimaku`, `tests/test_jimaku.py`, `docs/provider-notes/jimaku.md`, `README.md`, and `catalog.json` found no matches.
  - Live OpenAPI probe returned `3.0.3` and confirmed `/api/entries/search` plus file endpoints are present.
  - No-key `https://jimaku.cc/api/entries/search?query=one%20piece` probe returned `{"error":"unauthorized","code":7}`.
  - Test-server config check read only API-key presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `jimaku.api_key` is empty.
- Remaining gates:
  - Run SDK live smoke search and download with a real Jimaku API key.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `jimaku` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Jimaku API key.

### `subdl`

- Branch: `catalog-subdl`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subdl`
- Current checkpoint: `7ff94cd Add SubDL provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Official API docs confirmed `https://api.subdl.com/api/v1/subtitles`, required `api_key`, movie and TV search, IMDb/TMDB/SubDL ids, language filters, comments, releases, hearing-impaired metadata, full-season filters, and `unpack=1` for saved files inside packs.
  - Official language list confirmed the SubDL language code surface used by the provider, including `BR_PT`, `ZH`, and `ZH_BG`.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subdl.py'`: failed because `providers/subdl/provider.py` did not exist.
  - Edge-case red gate after initial implementation: direct unpacked format and missing pack member tests failed before the provider patch.
  - `python3 -B -m unittest discover -s tests -p 'test_subdl.py'`: `13` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subdl/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `341` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - JSON parse check for `provider.json` and `catalog.json`: passed.
  - Attribution, em-dash, and non-ASCII scans over touched SubDL implementation, notes, and tests found no matches.
- Live evidence on 2026-05-29:
  - `curl -sS -i --max-time 20 "https://api.subdl.com/api/v1/subtitles?film_name=Inception&type=movie&languages=EN"` returned HTTP `422` with `undefined is not an object (evaluating 'error2.schema')`, confirming real search proof requires a SubDL `api_key`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/subdl` as a linked git worktree on branch `catalog-subdl`, clean, and tracking `origin/catalog-subdl` after push.
  - Branch `catalog-subdl` was pushed at `7ff94cd`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/subdl.md`, `providers/subdl`, and `tests/test_subdl.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_subdl.py'`: `13` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subdl/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `341` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subdl`, `tests/test_subdl.py`, `docs/provider-notes/subdl.md`, `README.md`, and `catalog.json` found no matches.
  - No-key API probe to `https://api.subdl.com/api/v1/subtitles?film_name=Inception&type=movie&languages=EN` returned HTTP `422`.
  - Test-server config check read only API-key presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `subdl.api_key` is empty.
- Remaining gates:
  - Run SDK live smoke search and download with a real SubDL API key.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `subdl` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured SubDL API key.

### `subsource`

- Branch: `catalog-subsource`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subsource`
- Current checkpoint: `d50b08f Add SubSource provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Current API docs confirmed base URL `https://api.subsource.net/api/v1`, `X-API-Key` auth, `GET /movies/search`, `GET /subtitles`, `GET /subtitles/{id}`, and `GET /subtitles/{id}/download`.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subsource.py'`: failed because `providers/subsource/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_subsource.py'`: `10` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsource/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - JSON parse check for `provider.json` and `catalog.json`: passed.
  - Attribution, em-dash, and non-ASCII scans over touched SubSource implementation, notes, and tests found no matches.
- Live evidence on 2026-05-29:
  - `curl -sS -i --max-time 20 https://api.subsource.net/api/v1/movies/search`: returned HTTP `401` with `API key required` and noted `X-API-Key` header or `api_key` query parameter.
  - `curl -sS -i --max-time 20 -H 'X-API-Key: invalid-test-key' 'https://api.subsource.net/api/v1/movies/search?searchType=text&q=inception'`: returned HTTP `401` with `Invalid API key`.
  - Fetching `https://subsource.net/api-docs` directly from this environment returned a Cloudflare challenge page, while the API endpoint itself returned JSON auth errors.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/subsource` as a linked git worktree on branch `catalog-subsource`, clean, and tracking `origin/catalog-subsource` after push.
  - Branch `catalog-subsource` was pushed at `d50b08f`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/subsource.md`, `providers/subsource`, and `tests/test_subsource.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_subsource.py'`: `10` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsource/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subsource`, `tests/test_subsource.py`, `docs/provider-notes/subsource.md`, `README.md`, and `catalog.json` found no matches.
  - No-key `https://api.subsource.net/api/v1/movies/search` probe returned HTTP `401` with `API key required`.
  - Invalid-key probe returned HTTP `401` with `Invalid API key`.
  - Test-server config check read only API-key presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `subsource.apikey` is empty.
- Remaining gates:
  - Run SDK live smoke search and download with a real SubSource API key.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `subsource` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured SubSource API key.

### `subsro`

- Branch: `catalog-subsro`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subsro`
- Current checkpoint: `4e63940 Add Subs.ro provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Current API docs confirmed base URL `https://api.subs.ro/v1.0`, `X-Subs-Api-Key` auth, `GET /search/imdbid/{value}`, `GET /subtitle/{id}`, `GET /subtitle/{id}/download`, Romanian and English filters, quota checks, and binary archive downloads.
  - Legacy inspection confirmed required API key, comma-separated key rotation on HTTP 429, movie and episode IMDb search, Romanian and English language support, season parsing, and direct, ZIP, and RAR download handling.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subsro.py'`: failed because `providers/subsro/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_subsro.py'`: `15` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsro/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `343` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - JSON parse check for `providers/subsro/provider.json`: passed.
  - Attribution and em-dash scan over touched Subs.ro files found no matches.
- Live evidence on 2026-05-29:
  - `curl -sS -i --max-time 20 https://api.subs.ro/v1.0/search/imdbid/tt22202452`: returned HTTP `401` with `Missing API key` and `x-subs-api-version: 1.0`.
  - `curl -sS -i --max-time 20 -H 'X-Subs-Api-Key: invalid-test-key' https://api.subs.ro/v1.0/search/imdbid/tt22202452`: returned HTTP `403` with `Invalid API key` and `x-subs-api-version: 1.0`.
  - Real live search and download require a Subs.ro `api_key`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/subsro` as a linked git worktree on branch `catalog-subsro`, clean, and tracking `origin/catalog-subsro` after push.
  - Branch `catalog-subsro` was pushed at `4e63940`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/subsro.md`, `providers/subsro`, and `tests/test_subsro.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_subsro.py'`: `15` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subsro/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `343` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subsro`, `tests/test_subsro.py`, `docs/provider-notes/subsro.md`, `README.md`, and `catalog.json` found no matches.
  - No-key `https://api.subs.ro/v1.0/search/imdbid/tt22202452` probe returned HTTP `401` with `Missing API key`.
  - Invalid-key probe returned HTTP `403` with `Invalid API key`.
  - Test-server config check read only API-key presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `subsro.api_key` is empty.
- Remaining gates:
  - Run SDK live smoke search and download with a real Subs.ro API key.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `subsro` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured Subs.ro API key.

### `subx`

- Branch: `catalog-subx`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/subx`
- Current checkpoint: `1f649cf Add SubX provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Public API docs confirmed base URL `https://subx-api.duckdns.org`, `Authorization: Bearer <api_key>` auth, `GET /api/health`, `GET /api/subtitles/search`, title and IMDb search, `GET /api/subtitles/{id}/download`, and documented `400`, `401`, `404`, `429`, and `500` responses.
  - Legacy inspection confirmed required API key, Spanish Spain and Latin American variants, IMDb-first search, title fallback, episode exact matching, season-pack fallback, rate-limit retry handling, and archive downloads.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_subx.py'`: failed because `providers/subx/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_subx.py'`: `12` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subx/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - JSON parse check for `providers/subx/provider.json`: passed.
  - Attribution and em-dash scan over touched SubX files found no matches.
- Live evidence on 2026-05-29:
  - `curl -sS -i --max-time 20 https://subx-api.duckdns.org/api/health`: returned HTTP `200` with JSON health status, version `7b60d84`, and `built_at` `2026-05-22T12:27:43Z`.
  - `curl -sS -i --max-time 20 'https://subx-api.duckdns.org/api/subtitles/search?title=Dexter&limit=1'`: returned HTTP `401` with `Missing bearer token`.
  - `curl -sS -i --max-time 20 -H 'Authorization: Bearer invalid-test-key' 'https://subx-api.duckdns.org/api/subtitles/search?title=Dexter&limit=1'`: returned HTTP `401` with `Invalid or expired token`.
  - Real live search and download require a SubX `api_key`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/subx` as a linked git worktree on branch `catalog-subx`, clean, and tracking `origin/catalog-subx` after push.
  - Branch `catalog-subx` was pushed at `1f649cf`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/subx.md`, `providers/subx`, and `tests/test_subx.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_subx.py'`: `15` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/subx/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `343` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/subx`, `tests/test_subx.py`, `docs/provider-notes/subx.md`, `README.md`, and `catalog.json` found no matches.
  - `https://subx-api.duckdns.org/api/health` returned HTTP `200` with version `7b60d84` and built timestamp `2026-05-22T12:27:43Z`.
  - No-key subtitle search probe returned HTTP `401` with `Missing bearer token`.
  - Invalid bearer-token probe returned HTTP `401` with `Invalid or expired token`.
  - Test-server config check read only API-key presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `subx.api_key` is empty.
- Remaining gates:
  - Run SDK live smoke search and download with a real SubX API key.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `subx` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured SubX API key.

### `opensubtitlescom`

- Branch: `catalog-opensubtitlescom`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/opensubtitlescom`
- Current checkpoint: `885985f Add OpenSubtitles.com provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-29:
  - Official API docs confirmed base URL `https://api.opensubtitles.com/api/v1`, `Api-Key` authentication, bearer-token login, `/infos/languages`, `/login`, and `/download`.
  - Legacy inspection confirmed required username, password, and API key, 12-hour token cache, returned `base_url` handling, VIP bearer search, hash search with no-hash retry, title feature fallback, forced and hearing-impaired filtering, AI and machine translation filters, and bearer-token download flow.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_opensubtitlescom.py'`: failed because `providers/opensubtitlescom/provider.py` did not exist.
  - Mapper red gate after live language-table inspection failed for official API codes such as `abk -> ab`, `aze -> az-az`, `tet -> tm-td`, and `srp-ME -> me`, then passed after the mapping fix.
  - `python3 -B -m unittest discover -s tests -p 'test_opensubtitlescom.py'`: `12` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/opensubtitlescom/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the legacy Bazarr provider language registry: `422` entries.
  - Attribution and em-dash scan over touched OpenSubtitles.com files found no matches.
- Live evidence on 2026-05-29:
  - `curl -sS -i --max-time 20 -H 'User-Agent: BazarrProviderHub/1.0' https://api.opensubtitles.com/api/v1/infos/languages`: returned HTTP `200` with the current API language table.
  - `curl -sS -i --max-time 20 -H 'Api-Key: invalid-test-key' -H 'User-Agent: BazarrProviderHub/1.0' https://api.opensubtitles.com/api/v1/subtitles`: returned HTTP `403` with `You cannot consume this service`.
  - Real search and download require a valid OpenSubtitles.com username, password, and API key.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/opensubtitlescom` as a linked git worktree on branch `catalog-opensubtitlescom`, clean, and tracking `origin/catalog-opensubtitlescom` after push.
  - Branch `catalog-opensubtitlescom` was pushed at `885985f`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/opensubtitlescom.md`, `providers/opensubtitlescom`, and `tests/test_opensubtitlescom.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_opensubtitlescom.py'`: `12` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/opensubtitlescom/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `340` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/opensubtitlescom`, `tests/test_opensubtitlescom.py`, `docs/provider-notes/opensubtitlescom.md`, `README.md`, and `catalog.json` found no matches.
  - Manifest and catalog language counts both remain `422`.
  - Live `https://api.opensubtitles.com/api/v1/infos/languages` probe returned HTTP `200`.
  - Invalid API key subtitle probe returned HTTP `403` with `You cannot consume this service`.
  - Test-server config check read only credential presence and length from `/home/lavx/bazarr-data/config/config.yaml`; `opensubtitlescom.username`, `opensubtitlescom.password`, and `opensubtitlescom.api_key` are empty or absent.
- Remaining gates:
  - Run SDK live smoke search and download with real OpenSubtitles.com credentials.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `opensubtitlescom` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured OpenSubtitles.com credentials.

### `avistaz`

- Branch: `catalog-avistaz`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/avistaz`
- Current checkpoint: `1b339e2 Add AvistaZ provider`
- Baseline evidence on 2026-05-29:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed AvistaZ is a release-page provider using `video.info_url`, validates session cookies against `/rules`, parses the nested `Subtitles` table, treats release-page subtitles as hash-quality matches, and downloads direct, ZIP, and RAR subtitle payloads.
  - Bazarr UI/config inspection confirmed settings `cookies` and `user_agent`, with `cookies` classified as a secret.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_avistaz.py'`: failed because `providers/avistaz/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_avistaz.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/avistaz/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the legacy Bazarr AvistaZ provider language registry: `173` entries.
  - Attribution and em-dash scan over touched AvistaZ files found no matches.
- Live evidence on 2026-05-29:
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://avistaz.to/`: returned HTTP `200` with the public AvistaZ landing page.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://avistaz.to/rules`: returned HTTP `302` to `https://avistaz.to/auth/login`.
  - Real release-page search and download require valid AvistaZ session cookies and a video refined with an AvistaZ `info_url`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/avistaz` as a linked git worktree on branch `catalog-avistaz`, clean, and tracking `origin/catalog-avistaz` after push.
  - Branch `catalog-avistaz` was pushed at `1b339e2`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/avistaz.md`, `providers/avistaz`, and `tests/test_avistaz.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_avistaz.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/avistaz/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/avistaz`, `tests/test_avistaz.py`, `docs/provider-notes/avistaz.md`, `README.md`, and `catalog.json` found no matches.
  - Manifest and catalog language counts both remain `173`.
  - Public `https://avistaz.to/` probe returned HTTP `200`.
  - Unauthenticated `https://avistaz.to/rules` probe returned HTTP `302` to `https://avistaz.to/auth/login`.
  - Test-server config check read only cookie and user-agent presence plus length from `/home/lavx/bazarr-data/config/config.yaml`; `avistaz.cookies` and `avistaz.user_agent` are empty.
- Remaining gates:
  - Run SDK live smoke search and download with valid AvistaZ cookies and a known AvistaZ release-page fixture or test-server media item.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `avistaz` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured AvistaZ cookies.

### `cinemaz`

- Branch: `catalog-cinemaz`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/cinemaz`
- Current checkpoint: `df1b3bd Add CinemaZ provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed CinemaZ subclasses the shared AvistaZ-network behavior with `server_url = https://cinemaz.to/` and provider id `cinemaz`.
  - Bazarr UI/config inspection confirmed settings `cookies` and `user_agent`, with `cookies` classified as a secret.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_cinemaz.py'`: failed because `providers/cinemaz/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_cinemaz.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/cinemaz/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the legacy Bazarr CinemaZ provider language registry: `173` entries.
  - Attribution and em-dash scan over touched CinemaZ files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://cinemaz.to/`: returned HTTP `200` with the public CinemaZ landing page.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://cinemaz.to/rules`: returned HTTP `302` to `https://cinemaz.to/auth/login`.
  - Real release-page search and download require valid CinemaZ session cookies and a video refined with a CinemaZ `info_url`.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/cinemaz` as a linked git worktree on branch `catalog-cinemaz`, clean, and tracking `origin/catalog-cinemaz` after push.
  - Branch `catalog-cinemaz` was pushed at `df1b3bd`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/cinemaz.md`, `providers/cinemaz`, and `tests/test_cinemaz.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_cinemaz.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/cinemaz/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/cinemaz`, `tests/test_cinemaz.py`, `docs/provider-notes/cinemaz.md`, `README.md`, and `catalog.json` found no matches.
  - Manifest and catalog language counts both remain `173`.
  - Public `https://cinemaz.to/` probe returned HTTP `200`.
  - Unauthenticated `https://cinemaz.to/rules` probe returned HTTP `302` to `https://cinemaz.to/auth/login`.
  - Test-server config check read only cookie and user-agent presence plus length from `/home/lavx/bazarr-data/config/config.yaml`; `cinemaz.cookies` and `cinemaz.user_agent` are empty.
- Remaining gates:
  - Run SDK live smoke search and download with valid CinemaZ cookies and a known CinemaZ release-page fixture or test-server media item.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `cinemaz` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured CinemaZ cookies.

### `turkcealtyaziorg`

- Branch: `catalog-turkcealtyaziorg`
- Worktree: `/tmp/bazarr_catalog_provider_worktrees/turkcealtyaziorg`
- Current checkpoint: `6ec4f09 Add TurkceAltyazi.org provider`
- Baseline evidence on 2026-05-31:
  - Fresh worktree baseline `python3 -B -m sdk validate`: `catalog ok`.
  - Fresh worktree baseline `python3 -B -m unittest discover -s tests`: `328` tests passed, `6` skipped.
- Local evidence on 2026-05-31:
  - Legacy inspection confirmed IMDb-only movie search, series IMDb episode search, Turkish and English language support, optional cookies and User-Agent, Cloudflare access check, season-pack handling, hearing-impaired detection, hidden-form `/ind` downloads, and archive extraction.
  - Bazarr UI/config inspection confirmed settings `cookies` and `user_agent`, with `cookies` classified as a secret and language registry `["eng", "tur"]`.
  - Red TDD gate `python3 -B -m unittest discover -s tests -p 'test_turkcealtyaziorg.py'`: failed because `providers/turkcealtyaziorg/provider.py` did not exist.
  - `python3 -B -m unittest discover -s tests -p 'test_turkcealtyaziorg.py'`: `7` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/turkcealtyaziorg/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `335` tests passed, `6` skipped.
  - `git diff --check` and `git diff --cached --check`: clean.
  - Manifest language count matches the legacy Bazarr TurkceAltyazi.org provider language registry: `2` entries.
  - Attribution and em-dash scan over touched TurkceAltyazi.org files found no matches.
- Live evidence on 2026-05-31:
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' https://turkcealtyazi.org/`: returned HTTP `403` with `cf-mitigated: challenge`.
  - `curl -sS -i --max-time 20 -A 'BazarrProviderHub/1.0' 'https://turkcealtyazi.org/find.php?cat=sub&find=1375666'`: returned HTTP `403` with `cf-mitigated: challenge`.
  - Real search and download require valid TurkceAltyazi.org Cloudflare cookies paired with a browser User-Agent.
- Fresh local and credential evidence on 2026-06-01:
  - Recreated `/tmp/bazarr_catalog_provider_worktrees/turkcealtyaziorg` as a linked git worktree on branch `catalog-turkcealtyaziorg`, clean, and tracking `origin/catalog-turkcealtyaziorg` after push.
  - Branch `catalog-turkcealtyaziorg` was pushed at `6ec4f09`.
  - Branch scope remains limited to `README.md`, `catalog.json`, `docs/provider-notes/turkcealtyaziorg.md`, `providers/turkcealtyaziorg`, and `tests/test_turkcealtyaziorg.py`.
  - `python3 -B -m unittest discover -s tests -p 'test_turkcealtyaziorg.py'`: `10` tests passed.
  - `python3 -B -m sdk validate`: `catalog ok`.
  - `python3 -B -m py_compile providers/turkcealtyaziorg/provider.py`: passed.
  - `python3 -B -m unittest discover -s tests`: `338` tests passed, `6` skipped.
  - `git diff --check`: clean.
  - Attribution and em-dash scan over `providers/turkcealtyaziorg`, `tests/test_turkcealtyaziorg.py`, `docs/provider-notes/turkcealtyaziorg.md`, `README.md`, and `catalog.json` found no matches.
  - Manifest and catalog language counts both remain `2`.
  - Public `https://turkcealtyazi.org/` probe returned HTTP `403` with `cf-mitigated: challenge`.
  - Subtitle search probe for IMDb `tt1375666` returned HTTP `403` with `cf-mitigated: challenge`.
  - Test-server config check read only cookie and user-agent presence plus length from `/home/lavx/bazarr-data/config/config.yaml`; `turkcealtyaziorg.cookies` and `turkcealtyaziorg.user_agent` are empty.
- Remaining gates:
  - Run SDK live smoke search and download with valid TurkceAltyazi.org cookies and User-Agent.
  - Core branch `worktree-provider-hub-builtin-replacements` at `fe1afaeaf` already includes `turkcealtyaziorg` in the trusted replacement policy; deploy that core branch before Provider Hub compat proof.
  - Prove Provider Hub compat search, download, and stream on `bazarr-ui-test` with configured TurkceAltyazi.org cookies.

## Why OpenSubtitles.org Is Tricky

- `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles.py` contains a self-contained legacy `.org` XML-RPC implementation, but the current Bazarr behavior also mixes in `OpenSubtitlesScraperMixin`.
- `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles_scraper.py` and `/home/lavx/bazarr/opensubtitles-scraper/` remain behavior references, but the catalog branch now implements the live path natively instead of requiring a sidecar by default.
- The current migration target is `opensubtitles_org`, not legacy `opensubtitles`, so it avoids shadowing the built-in id while still preserving `.org` search and download behavior.
- The native path has three explicit upstream defenses: `ai-cloudscraper` as the default HTTP client, inline Anubis proof solving for `/.within.website/` challenges, and optional FlareSolverr fallback when Cloudflare still returns a browser challenge. Request delay and HTTP `429` handling stay visible as rate-limit controls.
- Keep legacy XML-RPC login fields out of the schema unless a current live XML-RPC search and download path is proven usable. Otherwise the provider would expose dead settings and still fail users.
- Do not claim completion until `opensubtitles_org` is installed and enabled on `bazarr-ui-test`, `/api/v1/subtitles` includes that provider, `/api/v1/download` returns a link for that candidate, and the stream URL returns non-empty subtitle bytes.

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
- Verified implementation: `/tmp/bazarr_provider_hub_builtin_replacements`
- Verified branch: `worktree-provider-hub-builtin-replacements`
- Verified head: `fe1afaeaf`

- [x] **Step 1: Create or reuse the Bazarr core worktree**

```bash
git -C /home/lavx/bazarr worktree add -b worktree-provider-hub-builtin-replacements /tmp/bazarr_provider_hub_builtin_replacements worktree-provider-hub-apply-all
```

- [x] **Step 2: Add an explicit trusted migration mode**

Current Bazarr core rejects Provider Hub manifests whose `provider_id` shadows a built-in provider and skips any active installation with that id during registry registration. This blocks migrated built-in providers from being proven through the real Provider Hub path until the provider id is explicitly migrated.

The verified branch implements a narrow migration mode that allows a trusted catalog entry to replace a built-in provider only when the provider id is present in `bazarr/provider_hub/policy.py`. The current policy contains 55 active migrated built-in ids and intentionally excludes dead-origin `hosszupuska`, `podnapisi`, `subscenter`, and `xsubs`. It also excludes legacy `opensubtitles`; that rewrite uses catalog id `opensubtitles_org` instead of shadowing the built-in `opensubtitles` id.

- [x] **Step 3: Preserve default safety**

Untrusted catalogs must still be blocked from shadowing built-ins. The default behavior for non-migrated built-ins must stay unchanged.

- [x] **Step 4: Add regression tests**

The tests prove:

- Untrusted built-in replacements are rejected while the built-in provider exists.
- Trusted official catalog replacements are accepted when the provider id is in the replacement policy.
- Registry registers a trusted Provider Hub built-in replacement instead of silently skipping it.
- Dead-origin providers are not in the replacement policy.
- Existing non-shadow plugin providers still register normally.

- [x] **Step 5: Verify the core branch**

```bash
cd /tmp/bazarr_provider_hub_builtin_replacements
python3 -B -m pytest tests/bazarr/test_provider_hub.py -q -k 'dead_origin_providers or explicit_built_in_replacement_policy or trusted_provider_hub_builtin_replacement_registers_proxy or trusted_official_catalog_to_replace_migrated_builtin or rejects_untrusted_catalog_builtin_replacement'
python3 -B -m pytest tests/bazarr/test_provider_hub.py -q
python3 -B -m py_compile bazarr/provider_hub/manifest.py bazarr/provider_hub/policy.py bazarr/provider_hub/registry.py bazarr/provider_hub/service.py bazarr/provider_hub/state.py
git diff --check
```

Observed on 2026-05-31:

- `5 passed, 90 deselected, 1 warning` for the targeted replacement-policy slice.
- `95 passed, 75 warnings` for the full Provider Hub test file after rebasing onto current `origin/development`.
- `18 passed` for the compat `moviebytesize` route, cache, and video-building tests.
- `py_compile`: passed for touched Provider Hub modules.
- `git diff --check`: clean.

- [x] **Step 6: Deploy to the Bazarr test server before provider compat proof**

Observed on 2026-05-31:

- The test-server image `ui-test-20260531-provider-hub-replacements-fe1afaeaf` is healthy on `bazarr-ui-test`.
- Runtime policy includes `bsplayer`, excludes `hosszupuska` and `podnapisi`, and contains 55 trusted migrated built-in ids.
- The old failed test image was based on a stale Bazarr revision and missed migration `6c9f1b8d2e3a`; rebasing the core branch onto current `origin/development` resolved the startup failure.

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

### Task P5: OpenSubtitles.org Native Antibot Boundary

**Files:**
- Provider branch: `catalog-opensubtitles`
- Provider worktree: `/tmp/bazarr_catalog_provider_worktrees/opensubtitles`
- Source behavior: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles.py`
- Historical helper behavior: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles_scraper.py`
- Current native provider: `/tmp/bazarr_catalog_provider_worktrees/opensubtitles/providers/opensubtitles_org/provider.py`

- [x] **Step 1: Treat `.org` as native scraper first**

Bazarr settings forced `use_web_scraper` because OpenSubtitles.org login is no longer available. The catalog provider now defaults to native `ai-cloudscraper` with no XML-RPC login controls and no required sidecar URL.

- [x] **Step 2: Preserve legacy feature shape without making dead login the default**

Expose `use_tag_search`, `skip_wrong_fps`, `only_foreign`, `also_foreign`, `request_delay_ms`, `flaresolverr_url`, and `flaresolverr_timeout_ms`. Keep username, password, VIP, SSL, `use_web_scraper`, `scraper_service_url`, and XML-RPC timeout out of the schema unless a current live `.org` XML-RPC path is proven usable.

- [x] **Step 3: Keep all antibot failures explicit**

Search must fail with a clear provider error when Anubis solving fails, FlareSolverr is needed but not configured, FlareSolverr returns invalid JSON, Cloudflare remains after fallback, or OpenSubtitles.org returns HTTP `429`.

- [ ] **Step 4: Finish live Provider Hub proof**

PR `#16` and commit `c8f013c` have local validation and direct native search/download proof. The remaining gate is Bazarr compat proof after an admin-enabled Provider Hub install or update on `bazarr-ui-test`.

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
| 12 | `hosszupuska` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/hosszupuska.py` | `catalog-hosszupuska` | `/tmp/bazarr_catalog_provider_worktrees/hosszupuska` | episode | none | dead origin, historical only |
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
| 28 | `podnapisi` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/podnapisi.py` | `catalog-podnapisi` | `/tmp/bazarr_catalog_provider_worktrees/podnapisi` | movie, episode | `only_foreign`, `also_foreign`, `verify_ssl` | dead origin, historical only |
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
| 50 | `subscenter` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subscenter.py` | `catalog-subscenter` | `/tmp/bazarr_catalog_provider_worktrees/subscenter` | movie, episode | `username`, `password` | dead origin, auth, archive |
| 51 | `titlovi` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/titlovi.py` | `catalog-titlovi` | `/tmp/bazarr_catalog_provider_worktrees/titlovi` | movie, episode | `username`, `password` | auth, archive |
| 52 | `titulky` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/titulky.py` | `catalog-titulky` | `/tmp/bazarr_catalog_provider_worktrees/titulky` | movie, episode | `username`, `password`, `approved_only`, `skip_wrong_fps` | auth, archive, FPS |
| 53 | `xsubs` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/xsubs.py` | `catalog-xsubs` | `/tmp/bazarr_catalog_provider_worktrees/xsubs` | episode | `username`, `password` | dead origin, auth |
| 54 | `subs4free` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subs4free.py` | `catalog-subs4free` | `/tmp/bazarr_catalog_provider_worktrees/subs4free` | movie | none | archive, anti-bot |
| 55 | `subs4series` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subs4series.py` | `catalog-subs4series` | `/tmp/bazarr_catalog_provider_worktrees/subs4series` | episode | `captcha_response`, `captcha_solver_url`, `captcha_solver_token`, `captcha_solver_timeout_ms`, `request_delay_ms` | archive, anti-bot, captcha helper |
| 56 | `zimuku` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/zimuku.py` | `catalog-zimuku` | `/tmp/bazarr_catalog_provider_worktrees/zimuku` | movie, episode | `captcha_response`, `captcha_solver_url`, `captcha_solver_token`, `captcha_solver_timeout_ms`, `request_delay_ms` | archive, Yunsuo captcha helper |
| 57 | `opensubtitles` | `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/opensubtitles.py` | `catalog-opensubtitles` | `/tmp/bazarr_catalog_provider_worktrees/opensubtitles` | movie, episode | `use_tag_search`, `skip_wrong_fps`, `only_foreign`, `also_foreign`, `request_delay_ms`, `flaresolverr_url`, `flaresolverr_timeout_ms` | OpenSubtitles.org native scraper, ai-cloudscraper, Anubis, Cloudflare fallback |
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
- Feature coverage: auth, API, archive, anti-bot, hash, FPS, helper-service history, native scraper behavior, and local-file behavior are identified before implementation begins.
- OpenSubtitles.org coverage: the plan explains why `.org` is tricky and treats native `ai-cloudscraper`, inline Anubis solving, request throttling, and optional FlareSolverr fallback as the current target.
