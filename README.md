# Bazarr+ Provider Catalog

> Official community catalog of subtitle provider plugins for the **[Bazarr+](https://github.com/LavX/bazarr) Provider Hub**. Load extra subtitle sources into your Bazarr+ install without waiting on a new release.

[![Bazarr+](https://img.shields.io/badge/Built%20for-Bazarr%2B-2ea44f?logo=github)](https://github.com/LavX/bazarr)
[![Bazarr+ Docs](https://img.shields.io/badge/Docs-lavx.github.io%2Fbazarr-blue)](https://lavx.github.io/bazarr/)
[![Python](https://img.shields.io/badge/Python-3.12%20to%203.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## What is this?

[Bazarr+](https://github.com/LavX/bazarr) is an enhanced fork of Bazarr that adds a **Provider Hub**: a plugin system that loads subtitle providers from an external catalog and runs each one in an isolated worker. This repo is that catalog.

If you run **Bazarr+ alongside Sonarr / Radarr / Plex / Jellyfin / Emby**, install providers from here via the Bazarr+ Marketplace to expand your subtitle sources beyond the built-in providers, with no rebuild and no restart of Sonarr/Radarr.

## Providers in this catalog

| Provider | Version | Notes |
| --- | --- | --- |
| [`animekalesi`](providers/animekalesi/) | 0.1.1 | Scrapes [AnimeKalesi](https://www.animekalesi.com) for Turkish anime episode subtitles. Direct subtitle files and ZIP packages are supported. |
| [`animesubinfo`](providers/animesubinfo/) | 0.1.1 | Scrapes [AnimeSub.info](http://animesub.info) for Polish anime movie and episode subtitles. Direct subtitle files and ZIP packages are supported. |
| [`animetosho`](providers/animetosho/) | 0.1.3 | Archive-only provider using [AnimeTosho](https://animetosho.org) JSON feeds to find embedded subtitle attachments for older anime episodes by AniDB episode id. New torrent ingestion stopped on May 9, 2026. No login or API key. |
| [`bollynook`](providers/bollynook/) | 0.1.1 | Scrapes [BollyNook](https://www.bollynook.com) for movie subtitles across Indian and international languages. No login or API key. |
| [`bsplayer`](providers/bsplayer/) | 0.1.0 | Uses the BSPlayer subtitles SOAP API for hash and size based movie and episode subtitle lookup. No login or API key. |
| [`embedded_subtitles`](providers/embeddedsubtitles/) | 0.1.2 | Extracts subtitle streams embedded in local movie and episode media files with ffprobe and ffmpeg. No login or API key. |
| [`fansubs`](providers/fansubs/) | 0.1.3 | Scrapes [fansubs.ru](http://fansubs.ru) for Russian anime and Asian cinema subtitles. No login or API key. |
| [`gestdown`](providers/gestdown/) | 0.1.0 | Uses the public Gestdown API for episode subtitles by TVDB show id, season, episode, and language. No login or API key. |
| [`greeksubs`](providers/greeksubs/) | 0.1.0 | Scrapes [GreekSubs](https://greeksubs.net) for Greek movie and episode subtitles by IMDb id. No login or API key. |
| [`greeksubtitles`](providers/greeksubtitles/) | 0.1.2 | Scrapes [GreekSubtitles](https://gr.greek-subtitles.com) for Greek and English movie and episode subtitles. ZIP and RAR downloads are supported. |
| [`isubtitles`](providers/isubtitles/) | 0.1.1 | Scrapes [iSubtitles.org](https://isubtitles.org) for movie and episode subtitles in broad multilingual coverage. No login or API key. |
| [`kitsunekko`](providers/kitsunekko/) | 0.1.0 | Scrapes [Kitsunekko](https://kitsunekko.net) for anime subtitle directories and ZIP packs. No login or API key. |
| [`moviesubtitles`](providers/moviesubtitles/) | 0.1.0 | Scrapes [Moviesubtitles.org](https://www.moviesubtitles.org) for movie subtitles, including multipart archives. No login or API key. |
| [`my_subs`](providers/my_subs/) | 0.1.1 | Scrapes [My-Subs.co](https://my-subs.co) for movie and episode subtitles in many languages. No login or API key. |
| [`napiprojekt`](providers/napiprojekt/) | 0.1.3 | Searches [NapiProjekt](https://www.napiprojekt.pl) for Polish subtitles using hash lookup and catalog scraping with optional author filters. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback. |
| [`nekur`](providers/nekur/) | 0.1.1 | Scrapes [Nekur](https://subtitri.nekur.net) for Latvian movie subtitles. No login or API key. |
| [`opensubtitles_org`](providers/opensubtitles_org/) | 0.1.3 | Scrapes OpenSubtitles.org natively with ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback for Cloudflare challenges. |
| [`prijevodionline`](providers/prijevodionline/) | 0.1.1 | Scrapes [Prijevodi-Online](https://www.prijevodi-online.org) for Croatian, Serbian, Montenegrin, and Serbo-Croatian episode subtitles. No login or API key. |
| [`soustitreseu`](providers/soustitreseu/) | 0.1.2 | Scrapes [Sous-Titres.eu](https://www.sous-titres.eu) for French and English movie and episode subtitles. No login or API key. |
| [`subcentral`](providers/subcentral/) | 0.1.0 | Scrapes [SubCentral.de](https://www.subcentral.de) forum subtitle threads for German and English episode releases. No login or API key. |
| [`subclub`](providers/subclub/) | 0.1.0 | Scrapes [Subclub.eu](https://www.subclub.eu) for Estonian movie and episode subtitles. No login or API key. |
| [`subf2m`](providers/subf2m/) | 0.1.1 | Scrapes [SubF2M](https://subf2m.co) for movie and episode subtitle ZIP files in 30+ languages. Configurable User-Agent and SSL verification. |
| [`subs4free`](providers/subs4free/) | 0.1.2 | Scrapes [Subs4Free](https://www.subs4free.info) for Greek and English movie subtitles. No login or API key. |
| [`subs4series`](providers/subs4series/) | 0.1.3 | Scrapes [Subs4Series](https://www.subs4series.com) for Greek and English episode subtitles. Uses ai-cloudscraper with inline Anubis solving, optional FlareSolverr fallback, and optional captcha helper settings for download gates. |
| [`subhd`](providers/subhd/) | 0.1.3 | Scrapes [SubHD.tv](https://subhd.tv) for Chinese-first movie and episode subtitles with multilingual releases. No login or API key. |
| [`subsynchro`](providers/subsynchro/) | 0.1.2 | Scrapes [SubSynchro](https://www.subsynchro.com) for French movie release subtitle ZIP files. No login or API key. |
| [`subtis`](providers/subtis/) | 0.1.0 | Queries [api.subt.is](https://api.subt.is) for Spanish movie subtitles using hash, size, filename, and alternative lookups. No login or API key. |
| [`subtitrarinoi`](providers/subtitrarinoi/) | 0.1.1 | Scrapes [subtitrari-noi.ro](https://www.subtitrari-noi.ro) for Romanian movie and episode subtitles. No login or API key. |
| [`subtitlestar`](providers/subtitlestar/) | 0.1.9 | Scrapes [subtitlestar.com](https://subtitlestar.com) for Persian/Farsi movie and episode subtitles. No login or API key. |
| [`subtitriid`](providers/subtitriid/) | 0.1.1 | Scrapes [subtitri.do.am](https://subtitri.do.am) for Latvian movie subtitles. No login or API key. |
| [`subtitulamostv`](providers/subtitulamostv/) | 0.1.1 | Scrapes [subtitulamos.tv](https://www.subtitulamos.tv) for episode subtitles in Spanish variants, English, Catalan, Galician, and Portuguese. No login or API key. |
| [`sub_scene`](providers/sub_scene/) | 0.1.14 | Scrapes [sub-scene.com](https://sub-scene.com) (Subscene clone) for movie and episode subtitles in 35+ languages including Vietnamese, Arabic, Bengali, Danish, Dutch. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback. |
| [`subssabbz`](providers/subssabbz/) | 0.1.1 | Scrapes [subs.sab.bz](http://subs.sab.bz) for Bulgarian and English movie and episode subtitles. ZIP and RAR downloads are supported. |
| [`subsunacs`](providers/subsunacs/) | 0.1.1 | Scrapes [subsunacs.net](https://subsunacs.net) for Bulgarian and English movie and episode subtitles. Direct entry pages plus ZIP, RAR, and 7Z downloads are supported. |
| [`subtitlecat`](providers/subtitlecat/) | 0.1.5 | Scrapes [subtitlecat.com](https://www.subtitlecat.com) (no login, no API key). Worked example for the [scraper authoring guide](docs/writing-a-scraper-provider.md). |
| [`supersubtitles`](providers/supersubtitles/) | 0.1.1 | Scrapes [feliratok.eu](https://feliratok.eu) for Hungarian and English movie and episode subtitles. No login or API key. |
| [`titrari`](providers/titrari/) | 0.1.1 | Scrapes [Titrari.ro](https://www.titrari.ro) for Romanian and English movie and episode subtitles. No login or API key. |
| [`tvsubtitles`](providers/tvsubtitles/) | 0.1.1 | Scrapes [tvsubtitles.net](https://www.tvsubtitles.net) for episode subtitles in broad multilingual coverage. No login or API key. |
| [`yifysubtitles`](providers/yifysubtitles/) | 0.1.1 | Scrapes [YIFYSubtitles](https://yifysubtitles.ch) for movie subtitles in broad multilingual coverage. No login or API key. |
| [`zimuku`](providers/zimuku/) | 0.1.3 | Scrapes [Zimuku / srtku.com](https://srtku.com) for Chinese and English movie and episode subtitles. Includes native Yunsuo image verification with optional helper fallback. |
| [`smoke`](providers/smoke/) | 0.2.0 | Deterministic no-network fixture for install / worker sanity checks. Not a real subtitle source. |

Every provider here ships independently of Bazarr+ releases. [Contribute](#contributing) your own.

## Install a provider into Bazarr+

1. Open **Bazarr+** → Subtitle Hub → Marketplace.
2. Point the catalog source at this repo's `catalog.json`:
   ```
   https://raw.githubusercontent.com/LavX/bazarr-provider-catalog/main/catalog.json
   ```
3. Browse the Marketplace, click **Install** on the provider you want, and restart Bazarr+ when prompted.
4. Configure the provider from the regular Providers settings page. Each plugin advertises its own config schema.

> See the [Bazarr+ documentation](https://lavx.github.io/bazarr/) for the broader install / config flow.

## Layout

- `catalog.json`: embedded Provider Hub V1 catalog manifest. This is the file Bazarr+ fetches.
- `providers/animekalesi/`: scrapes AnimeKalesi Turkish anime episode subtitle pages.
- `providers/animesubinfo/`: scrapes AnimeSub.info Polish anime movie and episode subtitles.
- `providers/animetosho/`: archive-only AnimeTosho JSON feed provider for embedded subtitle attachments on older anime episodes by AniDB episode id.
- `providers/bollynook/`: scrapes BollyNook movie subtitle pages and downloads.
- `providers/bsplayer/`: uses the BSPlayer subtitles SOAP API for hash and size based movie and episode subtitle lookup.
- `providers/embeddedsubtitles/`: extracts subtitle streams embedded in local movie and episode media files with ffprobe and ffmpeg.
- `providers/fansubs/`: production community provider, scrapes fansubs.ru for Russian anime and Asian cinema subtitle releases.
- `providers/gestdown/`: uses the public Gestdown API for episode subtitles by TVDB id, season, episode, and language.
- `providers/greeksubs/`: scrapes GreekSubs movie and episode subtitle listings by IMDb id.
- `providers/greeksubtitles/`: scrapes GreekSubtitles movie and episode search results.
- `providers/isubtitles/`: scrapes iSubtitles movie and episode subtitle pages.
- `providers/kitsunekko/`: scrapes Kitsunekko anime subtitle directories and ZIP packs.
- `providers/moviesubtitles/`: scrapes Moviesubtitles.org movie subtitle listings and multipart downloads.
- `providers/my_subs/`: scrapes My-Subs.co movie and episode subtitle listings.
- `providers/napiprojekt/`: searches NapiProjekt Polish subtitles by hash and catalog pages, with ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback for catalog Cloudflare challenges.
- `providers/nekur/`: scrapes Nekur Latvian movie subtitle listings and archive downloads.
- `providers/opensubtitles_org/`: scrapes OpenSubtitles.org natively with ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback.
- `providers/prijevodionline/`: scrapes Prijevodi-Online episode subtitle listings and archive downloads.
- `providers/smoke/`: deterministic no-network smoke provider for install and worker checks.
- `providers/soustitreseu/`: scrapes Sous-Titres.eu movie and episode subtitle listings and archive downloads.
- `providers/subcentral/`: scrapes SubCentral.de forum threads for German and English episode subtitles.
- `providers/subclub/`: scrapes Subclub.eu movie and episode subtitle listings and downloads.
- `providers/subf2m/`: scrapes SubF2M movie and episode subtitle pages and ZIP downloads.
- `providers/subs4free/`: scrapes Subs4Free movie subtitle listings and anti-block downloads.
- `providers/subs4series/`: scrapes Subs4Series episode subtitles with ai-cloudscraper, inline Anubis solving, optional FlareSolverr fallback, anti-block requests, optional captcha helper settings, and archive extraction.
- `providers/subhd/`: scrapes SubHD.tv Chinese-first movie and episode subtitle pages.
- `providers/subsynchro/`: scrapes SubSynchro French movie release subtitle ZIP files.
- `providers/subtis/`: queries api.subt.is for Spanish movie subtitles using hash, size, filename, and alternative lookups.
- `providers/subtitrarinoi/`: scrapes subtitrari-noi.ro Romanian movie and episode subtitle downloads.
- `providers/subtitlestar/`: scrapes subtitlestar.com for Persian/Farsi movie and episode subtitles.
- `providers/subtitriid/`: scrapes subtitri.do.am for Latvian movie subtitles.
- `providers/subtitulamostv/`: scrapes subtitulamos.tv for episode subtitle pages and direct downloads.
- `providers/sub_scene/`: scrapes sub-scene.com (Subscene clone) for movie and episode subtitles in 35+ languages including Vietnamese, Arabic, Bengali, Danish, Dutch, using ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback.
- `providers/subssabbz/`: scrapes subs.sab.bz for Bulgarian and English movie and episode subtitles with ZIP and RAR downloads.
- `providers/subsunacs/`: scrapes subsunacs.net for Bulgarian and English movie and episode subtitles with direct entry pages plus ZIP, RAR, and 7Z downloads.
- `providers/subtitlecat/`: first production community provider, scrapes subtitlecat.com using stdlib only. Worked example referenced by [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md).
- `providers/supersubtitles/`: scrapes feliratok.eu Hungarian and English movie and episode subtitle listings.
- `providers/titrari/`: scrapes Titrari.ro movie and episode subtitle listings, including ZIP and RAR downloads.
- `providers/tvsubtitles/`: scrapes tvsubtitles.net episode subtitle listings and ZIP downloads.
- `providers/yifysubtitles/`: scrapes YIFYSubtitles movie subtitle pages and ZIP downloads.
- `providers/zimuku/`: scrapes Zimuku / srtku.com for Chinese and English movie and episode subtitles, with native Yunsuo image verification and optional helper settings.
- `sdk/`: standalone authoring tools and templates, see the [SDK reference](sdk/README.md).
- `tests/`: catalog validation tests that do not import Bazarr internals.

## Writing your own provider

Want to add another subtitle source? Start with the full walkthrough:

**→ [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md)**

Adding providers in batches? Use the practical checklist:

**→ [docs/provider-bulk-creation-guide.md](docs/provider-bulk-creation-guide.md)**

The 5-minute version:

```bash
cp -R sdk/templates providers/myprovider
$EDITOR providers/myprovider/provider.json providers/myprovider/provider.py
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
python3 -B -m sdk smoke-test --provider myprovider --config-json '{"api_token":"dev-token"}' --skip-download
python3 -B -m unittest discover -s tests
```

Use [`providers/smoke/`](providers/smoke/) for the manifest reference and [`providers/subtitlecat/`](providers/subtitlecat/) for a real scraping pattern.

## Common Commands

```bash
python3 -B -m sdk build-catalog   # regenerate catalog.json from all provider manifests
python3 -B -m sdk validate        # validate manifests, hashes, schema
python3 -B -m sdk runtime-matrix  # print supported Bazarr+ Python runtimes
python3 -B -m sdk smoke-test      # exercise a provider against the worker contract
python3 -B -m unittest discover -s tests
```

## Runtime Matrix

Provider Hub workers target the Bazarr+ Python runtime range `>=3.12,<3.15`: Python `3.12`, `3.13`, and `3.14`. Python `3.11` is not the compatibility floor for this catalog.

Pure wheels such as `py3-none-any` can be covered by one hash. ABI-specific wheels need hashes for every supported ABI tag, currently `cp312`, `cp313`, and `cp314`, or a compatible stable-ABI wheel such as `cp311-abi3`, on every Bazarr+ platform the provider is expected to install on. Check the current policy with:

```bash
python3 -B -m sdk runtime-matrix
```

Provider manifests declare pure Python `.py` files only. Dependencies, when needed, must be pinned wheel requirements with SHA256 hashes. See the [SDK reference](sdk/README.md).

## Contributing

Pull requests welcome. A good provider PR:

- Adds `providers/<id>/provider.py` and `providers/<id>/provider.json`.
- Adds `tests/test_<id>.py` exercising query building, parsing, scoring, and the search/download flow against captured HTML fixtures in `tests/fixtures/`.
- Bumps and regenerates `catalog.json` (`python3 -B -m sdk build-catalog`).
- Passes `python3 -B -m sdk validate` and `python3 -B -m unittest discover -s tests`.
- Follows the patterns in [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md) and the bulk checklist in [docs/provider-bulk-creation-guide.md](docs/provider-bulk-creation-guide.md).

## Related

- **[Bazarr+](https://github.com/LavX/bazarr)**: the enhanced Bazarr fork that consumes this catalog. Adds AI translation via OpenRouter, OpenSubtitles.org scraper, provider priority, API key encryption, batch translation, mass subtitle sync, advanced filters, and security hardening.
- [Bazarr+ Documentation](https://lavx.github.io/bazarr/)
- [Bazarr (upstream)](https://github.com/morpheus65535/bazarr)

---

<sub>Keywords: bazarr, bazarr+, bazarr plus, subtitle provider, subtitle plugin, subtitle scraper, subtitlecat, subtitles, jellyfin, plex, emby, radarr, sonarr, opensubtitles, provider hub, python plugin, subtitle automation.</sub>
