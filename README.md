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
| [`addic7ed`](providers/addic7ed/) | 0.1.3 | Uses [Addic7ed](https://www.addic7ed.com) for movie and episode subtitles. Requires username and password or valid cookies. |
| [`animekalesi`](providers/animekalesi/) | 0.1.4 | Scrapes [AnimeKalesi](https://www.animekalesi.com) for Turkish anime episode subtitles. Direct subtitle files and ZIP packages are supported. |
| [`animesubinfo`](providers/animesubinfo/) | 0.1.3 | Scrapes [AnimeSub.info](http://animesub.info) for Polish anime movie and episode subtitles. Direct subtitle files and ZIP packages are supported. |
| [`animetosho`](providers/animetosho/) | 0.1.4 | Archive-only provider using [AnimeTosho](https://animetosho.org) JSON feeds to find embedded subtitle attachments for older anime episodes by AniDB episode id. New torrent ingestion stopped on May 9, 2026. No login or API key. |
| [`betaseries`](providers/betaseries/) | 0.1.6 | Searches the [BetaSeries](https://www.betaseries.com) API for French and English episode subtitles. Requires an API key. |
| [`bollynook`](providers/bollynook/) | 0.1.3 | Scrapes [BollyNook](https://www.bollynook.com) for movie subtitles across Indian and international languages. No login or API key. |
| [`bsplayer`](providers/bsplayer/) | 0.1.1 | Uses the BSPlayer subtitles SOAP API for hash and size based movie and episode subtitle lookup. No login or API key. |
| [`embeddedsubtitles`](providers/embeddedsubtitles/) | 0.1.3 | Extracts subtitle streams embedded in local movie and episode media files with ffprobe and ffmpeg. No login or API key. |
| [`fansubs`](providers/fansubs/) | 0.1.6 | Scrapes [fansubs.ru](http://fansubs.ru) for Russian anime and Asian cinema subtitles. No login or API key. |
| [`gestdown`](providers/gestdown/) | 0.1.3 | Uses the public Gestdown API for episode subtitles by TVDB show id, season, episode, and language. No login or API key. |
| [`greeksubs`](providers/greeksubs/) | 0.1.1 | Scrapes [GreekSubs](https://greeksubs.net) for Greek movie and episode subtitles by IMDb id. No login or API key. |
| [`greeksubtitles`](providers/greeksubtitles/) | 0.1.3 | Scrapes [GreekSubtitles](https://gr.greek-subtitles.com) for Greek and English movie and episode subtitles. ZIP and RAR downloads are supported. |
| [`isubtitles`](providers/isubtitles/) | 0.1.4 | Scrapes [iSubtitles.org](https://isubtitles.org) for movie and episode subtitles in broad multilingual coverage. No login or API key. |
| [`jimaku`](providers/jimaku/) | 0.1.4 | Uses the [Jimaku](https://jimaku.cc) API for Japanese movie and episode subtitles. Requires API key. |
| [`kitsunekko`](providers/kitsunekko/) | 0.1.2 | Scrapes [Kitsunekko](https://kitsunekko.net) for anime subtitle directories and ZIP packs. No login or API key. |
| [`ktuvit`](providers/ktuvit/) | 0.1.3 | Uses [Ktuvit.me](https://www.ktuvit.me) services for Hebrew movie and episode subtitles. Requires email and hashed password. |
| [`legendasdivx`](providers/legendasdivx/) | 0.2.2 | Scrapes [LegendasDivx](https://www.legendasdivx.pt) for European and Brazilian Portuguese movie and episode subtitles. Requires username and password. Uses ai-cloudscraper, with optional FlareSolverr and captcha solver fallbacks. |
| [`legendasnet`](providers/legendasnet/) | 0.1.3 | Uses the [Legendas.net](https://legendas.net) API for Brazilian Portuguese movie and episode subtitles. Requires username and password. |
| [`moviesubtitles`](providers/moviesubtitles/) | 0.1.2 | Scrapes [Moviesubtitles.org](https://www.moviesubtitles.org) for movie subtitles, including multipart archives. No login or API key. |
| [`napisy24`](providers/napisy24/) | 0.1.3 | Uses the [Napisy24](https://napisy24.pl) hash API for Polish movie and episode subtitles. Optional username and password. |
| [`my_subs`](providers/my_subs/) | 0.1.3 | Scrapes [My-Subs.co](https://my-subs.co) for movie and episode subtitles in many languages. No login or API key. |
| [`napiprojekt`](providers/napiprojekt/) | 0.1.6 | Searches [NapiProjekt](https://www.napiprojekt.pl) for Polish subtitles using hash lookup and catalog scraping with optional author filters. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback. |
| [`nekur`](providers/nekur/) | 0.1.4 | Scrapes [Nekur](https://subtitri.nekur.net) for Latvian movie subtitles. No login or API key. |
| [`opensubtitles`](providers/opensubtitles_org/) | 0.1.9 | Scrapes OpenSubtitles.org natively with ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback for Cloudflare challenges. |
| [`opensubtitlescom`](providers/opensubtitlescom/) | 0.1.9 | Uses the official [OpenSubtitles.com](https://www.opensubtitles.com) API for movie and episode subtitles. Requires username, password, and API key. |
| [`prijevodionline`](providers/prijevodionline/) | 0.2.1 | Scrapes [Prijevodi-Online](https://www.prijevodi-online.org) for Croatian, Serbian, Montenegrin, and Serbo-Croatian episode subtitles. **The site has been offline since August 2026** after its host suspended it; the operators are moving to new hosting, so searches fail until it returns ([notes](docs/provider-notes/prijevodionline.md)). Needs a FlareSolverr URL when it is up. No login or API key. |
| [`regielive`](providers/regielive/) | 0.1.3 | Uses the RegieLive Bazarr API for Romanian movie and episode subtitles, with public HTML search fallback when the API rejects a request. No login or user API key. |
| [`shooter`](providers/shooter/) | 0.1.3 | Queries [Shooter.cn](https://www.shooter.cn) hash-based subtitle API for English and Chinese movie and episode subtitles. No login or API key. |
| [`soustitreseu`](providers/soustitreseu/) | 0.1.10 | Scrapes [Sous-Titres.eu](https://www.sous-titres.eu) for French and English movie and episode subtitles. No login or API key. |
| [`subcentral`](providers/subcentral/) | 0.1.1 | Scrapes [SubCentral.de](https://www.subcentral.de) forum subtitle threads for German and English episode releases. No login or API key. |
| [`subclub`](providers/subclub/) | 0.1.4 | Scrapes [Subclub.eu](https://www.subclub.eu) for Estonian movie and episode subtitles. No login or API key. |
| [`subdl`](providers/subdl/) | 0.1.4 | Uses the official [SubDL](https://subdl.com) API for movie and episode subtitles, including optional anime pack handling. API key required. |
| [`subf2m`](providers/subf2m/) | 0.1.3 | Scrapes [SubF2M](https://subf2m.co) for movie and episode subtitle ZIP files in 30+ languages. Configurable User-Agent and SSL verification. |
| [`subs4free`](providers/subs4free/) | 0.1.4 | Scrapes [Subs4Free](https://www.subs4free.info) for Greek and English movie subtitles. No login or API key. |
| [`subs4series`](providers/subs4series/) | 0.1.7 | Scrapes [Subs4Series](https://www.subs4series.com) for Greek and English episode subtitles. Uses ai-cloudscraper with inline Anubis solving, optional FlareSolverr fallback, and optional captcha helper settings for download gates. |
| [`subhd`](providers/subhd/) | 0.1.4 | Scrapes [SubHD.tv](https://subhd.tv) for Chinese-first movie and episode subtitles with multilingual releases. No login or API key. |
| [`subsarr`](providers/subsarr/) | 0.1.2 | Connects to a self-hosted [Subsarr](https://github.com/slimcdk/subsarr) API for Subscene-style movie and episode subtitles. Requires a Base URL. |
| [`subsource`](providers/subsource/) | 0.1.4 | Uses the official [SubSource](https://subsource.net) API for movie and episode subtitles. API key required. |
| [`subsynchro`](providers/subsynchro/) | 0.1.4 | Scrapes [SubSynchro](https://www.subsynchro.com) for French movie release subtitle ZIP files. No login or API key. |
| [`subtis`](providers/subtis/) | 0.1.0 | Queries [api.subt.is](https://api.subt.is) for Spanish movie subtitles using hash, size, filename, and alternative lookups. No login or API key. |
| [`subtitrarinoi`](providers/subtitrarinoi/) | 0.1.5 | Scrapes [subtitrari-noi.ro](https://www.subtitrari-noi.ro) for Romanian movie and episode subtitles. No login or API key. |
| [`subtitlestar`](providers/subtitlestar/) | 0.1.12 | Scrapes [subtitlestar.com](https://subtitlestar.com) for Persian/Farsi movie and episode subtitles. No login or API key. |
| [`subtitriid`](providers/subtitriid/) | 0.1.5 | Scrapes [subtitri.do.am](https://subtitri.do.am) for Latvian movie subtitles. No login or API key. |
| [`subtitulamostv`](providers/subtitulamostv/) | 0.1.2 | Scrapes [subtitulamos.tv](https://www.subtitulamos.tv) for episode subtitles in Spanish variants, English, Catalan, Galician, and Portuguese. No login or API key. |
| [`sub_scene`](providers/sub_scene/) | 0.1.17 | Scrapes [sub-scene.com](https://sub-scene.com) (Subscene clone) for movie and episode subtitles in 35+ languages including Vietnamese, Arabic, Bengali, Danish, Dutch. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback. |
| [`subsro`](providers/subsro/) | 0.1.4 | Uses the [Subs.ro](https://subs.ro/api) API for Romanian and English movie and episode subtitles. Requires an API key. ZIP and RAR downloads are supported. |
| [`subssabbz`](providers/subssabbz/) | 0.1.5 | Scrapes [subs.sab.bz](http://subs.sab.bz) for Bulgarian and English movie and episode subtitles. ZIP and RAR downloads are supported. |
| [`subsunacs`](providers/subsunacs/) | 0.1.5 | Scrapes [subsunacs.net](https://subsunacs.net) for Bulgarian and English movie and episode subtitles. Direct entry pages plus ZIP, RAR, and 7Z downloads are supported. |
| [`subx`](providers/subx/) | 0.1.2 | Uses the [SubX](https://subx-api.duckdns.org/docs/getting-started/quickstart/) API for Spanish movie and episode subtitles. Requires an API key. ZIP and RAR downloads are supported. |
| [`subtitlecat`](providers/subtitlecat/) | 0.1.5 | Scrapes [subtitlecat.com](https://www.subtitlecat.com) (no login, no API key). Worked example for the [scraper authoring guide](docs/writing-a-scraper-provider.md). |
| [`yavkanet`](providers/yavkanet/) | 0.1.10 | Scrapes [Yavka.net](https://yavka.net) for Bulgarian, English, Russian, Spanish, and Italian subtitles by IMDb id. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback. |
| [`supersubtitles`](providers/supersubtitles/) | 0.1.5 | Scrapes [feliratok.eu](https://feliratok.eu) for Hungarian and English movie and episode subtitles. No login or API key. |
| [`titrari`](providers/titrari/) | 0.1.5 | Scrapes [Titrari.ro](https://www.titrari.ro) for Romanian and English movie and episode subtitles. No login or API key. |
| [`titlovi`](providers/titlovi/) | 0.1.8 | Uses the [Titlovi](https://kodi.titlovi.com/api/subtitles) Kodi API for movie and episode subtitles. Login required. |
| [`turkcealtyaziorg`](providers/turkcealtyaziorg/) | 0.1.7 | Scrapes [TurkceAltyazi.org](https://turkcealtyazi.org) by IMDb id for Turkish and English movie and episode subtitles. Uses ai-cloudscraper by default with inline Anubis solving and optional FlareSolverr fallback for Cloudflare challenges. |
| [`tvsubtitles`](providers/tvsubtitles/) | 0.1.3 | Scrapes [tvsubtitles.net](https://www.tvsubtitles.net) for episode subtitles in broad multilingual coverage. No login or API key. |
| [`wizdom`](providers/wizdom/) | 0.1.4 | Uses [wizdom.xyz](https://wizdom.xyz) for Hebrew movie and episode subtitles, with TMDB lookup when an IMDb id is not supplied. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback for Cloudflare browser challenges. |
| [`whisperai`](providers/whisperai/) | 0.1.2 | Generates subtitles through a user-supplied Whisper web service by extracting local audio with ffmpeg. Requires endpoint configuration. |
| [`yifysubtitles`](providers/yifysubtitles/) | 0.1.3 | Scrapes [YIFYSubtitles](https://yifysubtitles.ch) for movie subtitles in broad multilingual coverage. No login or API key. |
| [`zimuku`](providers/zimuku/) | 0.1.9 | Scrapes [Zimuku / srtku.com](https://srtku.com) for Chinese and English movie and episode subtitles. Includes native Yunsuo image verification with optional helper fallback. |
| [`smoke`](providers/smoke/) | 0.2.0 | Deterministic no-network fixture for install / worker sanity checks. Not a real subtitle source. |
| [`titulky`](providers/titulky/) | 0.1.6 | Scrapes [Titulky.com](https://premium.titulky.com) for Czech and Slovak movie and episode subtitles. VIP login required. |

Every provider here ships independently of Bazarr+ releases. [Contribute](#contributing) your own.

## Install a provider into Bazarr+

1. Open **Bazarr+** → **Settings** → **Providers** → Provider Hub → Marketplace.
2. Point the catalog source at this repo's `catalog.json`:
   ```
   https://raw.githubusercontent.com/LavX/bazarr-provider-catalog/main/catalog.json
   ```
3. Browse the Marketplace, click **Install** on the provider you want, and restart Bazarr+ when prompted.
4. Configure the provider from the regular Providers settings page. Each plugin advertises its own config schema.

> See the [Bazarr+ documentation](https://lavx.github.io/bazarr/) for the broader install / config flow.

## Layout

- `catalog.json`: embedded Provider Hub V1 catalog manifest. This is the file Bazarr+ fetches.
- `providers/addic7ed/`: uses Addic7ed movie and episode subtitle listings with login or cookie authentication.
- `providers/animekalesi/`: scrapes AnimeKalesi Turkish anime episode subtitle pages.
- `providers/animesubinfo/`: scrapes AnimeSub.info Polish anime movie and episode subtitles.
- `providers/animetosho/`: archive-only AnimeTosho JSON feed provider for embedded subtitle attachments on older anime episodes by AniDB episode id.
- `providers/betaseries/`: searches the BetaSeries API for token-authenticated French and English episode subtitles.
- `providers/bollynook/`: scrapes BollyNook movie subtitle pages and downloads.
- `providers/bsplayer/`: uses the BSPlayer subtitles SOAP API for hash and size based movie and episode subtitle lookup.
- `providers/embeddedsubtitles/`: extracts subtitle streams embedded in local movie and episode media files with ffprobe and ffmpeg.
- `providers/fansubs/`: production community provider, scrapes fansubs.ru for Russian anime and Asian cinema subtitle releases.
- `providers/gestdown/`: uses the public Gestdown API for episode subtitles by TVDB id, season, episode, and language.
- `providers/greeksubs/`: scrapes GreekSubs movie and episode subtitle listings by IMDb id.
- `providers/greeksubtitles/`: scrapes GreekSubtitles movie and episode search results.
- `providers/isubtitles/`: scrapes iSubtitles movie and episode subtitle pages.
- `providers/jimaku/`: uses the Jimaku API for Japanese movie and episode subtitles.
- `providers/kitsunekko/`: scrapes Kitsunekko anime subtitle directories and ZIP packs.
- `providers/ktuvit/`: uses Ktuvit.me services for Hebrew movie and episode subtitles.
- `providers/legendasdivx/`: scrapes LegendasDivx for European and Brazilian Portuguese movie and episode subtitles.
- `providers/legendasnet/`: uses the Legendas.net API for Brazilian Portuguese movie and episode subtitles.
- `providers/moviesubtitles/`: scrapes Moviesubtitles.org movie subtitle listings and multipart downloads.
- `providers/napisy24/`: uses the Napisy24 hash API for Polish movie and episode subtitles.
- `providers/my_subs/`: scrapes My-Subs.co movie and episode subtitle listings.
- `providers/napiprojekt/`: searches NapiProjekt Polish subtitles by hash and catalog pages, with ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback for catalog Cloudflare challenges.
- `providers/nekur/`: scrapes Nekur Latvian movie subtitle listings and archive downloads.
- `providers/opensubtitles_org/`: scrapes OpenSubtitles.org natively with ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback.
- `providers/opensubtitlescom/`: uses the official OpenSubtitles.com API for movie and episode subtitles.
- `providers/prijevodionline/`: scrapes Prijevodi-Online episode subtitle listings and archive downloads.
- `providers/regielive/`: uses the RegieLive Bazarr API for Romanian movie and episode subtitles, with public HTML search fallback when the API rejects a request.
- `providers/shooter/`: queries Shooter.cn's hash-based API for English and Chinese movie and episode subtitles.
- `providers/smoke/`: deterministic no-network smoke provider for install and worker checks.
- `providers/soustitreseu/`: scrapes Sous-Titres.eu movie and episode subtitle listings and archive downloads.
- `providers/subcentral/`: scrapes SubCentral.de forum threads for German and English episode subtitles.
- `providers/subclub/`: scrapes Subclub.eu movie and episode subtitle listings and downloads.
- `providers/subdl/`: uses the official SubDL API for movie and episode subtitles with optional anime pack handling.
- `providers/subf2m/`: scrapes SubF2M movie and episode subtitle pages and ZIP downloads.
- `providers/subs4free/`: scrapes Subs4Free movie subtitle listings and anti-block downloads.
- `providers/subs4series/`: scrapes Subs4Series episode subtitles with ai-cloudscraper, inline Anubis solving, optional FlareSolverr fallback, anti-block requests, optional captcha helper settings, and archive extraction.
- `providers/subhd/`: scrapes SubHD.tv Chinese-first movie and episode subtitle pages.
- `providers/subsarr/`: connects to a configured self-hosted Subsarr API.
- `providers/subsource/`: uses the official SubSource API for movie and episode subtitles.
- `providers/subsynchro/`: scrapes SubSynchro French movie release subtitle ZIP files.
- `providers/subtis/`: queries api.subt.is for Spanish movie subtitles using hash, size, filename, and alternative lookups.
- `providers/subtitrarinoi/`: scrapes subtitrari-noi.ro Romanian movie and episode subtitle downloads.
- `providers/subtitlestar/`: scrapes subtitlestar.com for Persian/Farsi movie and episode subtitles.
- `providers/subtitriid/`: scrapes subtitri.do.am for Latvian movie subtitles.
- `providers/subtitulamostv/`: scrapes subtitulamos.tv for episode subtitle pages and direct downloads.
- `providers/sub_scene/`: scrapes sub-scene.com (Subscene clone) for movie and episode subtitles in 35+ languages including Vietnamese, Arabic, Bengali, Danish, Dutch, using ai-cloudscraper, inline Anubis solving, and optional FlareSolverr fallback.
- `providers/subsro/`: uses the Subs.ro API for Romanian and English movie and episode subtitles with API-key authentication.
- `providers/subssabbz/`: scrapes subs.sab.bz for Bulgarian and English movie and episode subtitles with ZIP and RAR downloads.
- `providers/subsunacs/`: scrapes subsunacs.net for Bulgarian and English movie and episode subtitles with direct entry pages plus ZIP, RAR, and 7Z downloads.
- `providers/subx/`: uses the SubX API for Spanish movie and episode subtitles with API-key authentication.
- `providers/subtitlecat/`: first production community provider, scrapes subtitlecat.com using stdlib only. Worked example referenced by [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md).
- `providers/yavkanet/`: scrapes Yavka.net by IMDb id, including ai-cloudscraper, inline Anubis solving, Cloudflare fallback, and archive downloads.
- `providers/supersubtitles/`: scrapes feliratok.eu Hungarian and English movie and episode subtitle listings.
- `providers/titrari/`: scrapes Titrari.ro movie and episode subtitle listings, including ZIP and RAR downloads.
- `providers/titlovi/`: uses the Titlovi Kodi API for authenticated movie and episode subtitle search and downloads.
- `providers/turkcealtyaziorg/`: scrapes TurkceAltyazi.org movie and episode subtitle listings by IMDb id.
- `providers/tvsubtitles/`: scrapes tvsubtitles.net episode subtitle listings and ZIP downloads.
- `providers/wizdom/`: uses wizdom.xyz for Hebrew movie and episode subtitles, with TMDB lookup when an IMDb id is missing. Uses ai-cloudscraper with inline Anubis solving and optional FlareSolverr fallback for Cloudflare browser challenges.
- `providers/whisperai/`: generates subtitles through a configured Whisper web service after extracting local audio with ffmpeg.
- `providers/yifysubtitles/`: scrapes YIFYSubtitles movie subtitle pages and ZIP downloads.
- `providers/zimuku/`: scrapes Zimuku / srtku.com for Chinese and English movie and episode subtitles, with native Yunsuo image verification and optional helper settings.
- `sdk/`: standalone authoring tools and templates, see the [SDK reference](sdk/README.md).
- `providers/titulky/`: scrapes Titulky.com Czech and Slovak movie and episode subtitle listings and archive downloads.
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
