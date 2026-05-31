# Bazarr+ Provider Catalog

> Official community catalog of subtitle provider plugins for the **[Bazarr+](https://github.com/LavX/bazarr) Provider Hub**. Load extra subtitle sources into your Bazarr+ install without waiting on a new release.

[![Bazarr+](https://img.shields.io/badge/Built%20for-Bazarr%2B-2ea44f?logo=github)](https://github.com/LavX/bazarr)
[![Bazarr+ Docs](https://img.shields.io/badge/Docs-lavx.github.io%2Fbazarr-blue)](https://lavx.github.io/bazarr/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## What is this?

[Bazarr+](https://github.com/LavX/bazarr) is an enhanced fork of Bazarr that adds a **Provider Hub**: a plugin system that loads subtitle providers from an external catalog and runs each one in an isolated worker. This repo is that catalog.

If you run **Bazarr+ alongside Sonarr / Radarr / Plex / Jellyfin / Emby**, install providers from here via the Bazarr+ Marketplace to expand your subtitle sources beyond the built-in providers, with no rebuild and no restart of Sonarr/Radarr.

## Providers in this catalog

| Provider | Version | Notes |
| --- | --- | --- |
| [`bollynook`](providers/bollynook/) | 0.1.1 | Scrapes [BollyNook](https://www.bollynook.com) for movie subtitles across Indian and international languages. No login or API key. |
| [`fansubs`](providers/fansubs/) | 0.1.3 | Scrapes [fansubs.ru](http://fansubs.ru) for Russian anime and Asian cinema subtitles. No login or API key. |
| [`isubtitles`](providers/isubtitles/) | 0.1.1 | Scrapes [iSubtitles.org](https://isubtitles.org) for movie and episode subtitles in broad multilingual coverage. No login or API key. |
| [`kitsunekko`](providers/kitsunekko/) | 0.1.0 | Scrapes [Kitsunekko](https://kitsunekko.net) for anime subtitle directories and ZIP packs. No login or API key. |
| [`moviesubtitles`](providers/moviesubtitles/) | 0.1.0 | Scrapes [Moviesubtitles.org](https://www.moviesubtitles.org) for movie subtitles, including multipart archives. No login or API key. |
| [`my_subs`](providers/my_subs/) | 0.1.1 | Scrapes [My-Subs.co](https://my-subs.co) for movie and episode subtitles in many languages. No login or API key. |
| [`pipocas`](providers/pipocas/) | 0.1.0 | Scrapes [Pipocas.tv](https://pipocas.tv) for movie and episode subtitles in Portuguese, Brazilian Portuguese, English, and Spanish. Login required. |
| [`subcentral`](providers/subcentral/) | 0.1.0 | Scrapes [SubCentral.de](https://www.subcentral.de) forum subtitle threads for German and English episode releases. No login or API key. |
| [`subhd`](providers/subhd/) | 0.1.3 | Scrapes [SubHD.tv](https://subhd.tv) for Chinese-first movie and episode subtitles with multilingual releases. No login or API key. |
| [`subtitlestar`](providers/subtitlestar/) | 0.1.8 | Scrapes [subtitlestar.com](https://subtitlestar.com) for Persian/Farsi movie and episode subtitles. No login or API key. |
| [`sub_scene`](providers/sub_scene/) | 0.1.9 | Scrapes [sub-scene.com](https://sub-scene.com) (Subscene clone) for movie and episode subtitles in 35+ languages including Vietnamese, Arabic, Bengali, Danish, Dutch. Uses cloudscraper with optional FlareSolverr fallback. |
| [`subtitlecat`](providers/subtitlecat/) | 0.1.5 | Scrapes [subtitlecat.com](https://www.subtitlecat.com) (no login, no API key). Worked example for the [scraper authoring guide](docs/writing-a-scraper-provider.md). |
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
- `providers/bollynook/`: scrapes BollyNook movie subtitle pages and downloads.
- `providers/fansubs/`: production community provider, scrapes fansubs.ru for Russian anime and Asian cinema subtitle releases.
- `providers/isubtitles/`: scrapes iSubtitles movie and episode subtitle pages.
- `providers/kitsunekko/`: scrapes Kitsunekko anime subtitle directories and ZIP packs.
- `providers/moviesubtitles/`: scrapes Moviesubtitles.org movie subtitle listings and multipart downloads.
- `providers/my_subs/`: scrapes My-Subs.co movie and episode subtitle listings.
- `providers/pipocas/`: scrapes Pipocas.tv movie and episode subtitle listings with login-backed downloads.
- `providers/smoke/`: deterministic no-network smoke provider for install and worker checks.
- `providers/subcentral/`: scrapes SubCentral.de forum threads for German and English episode subtitles.
- `providers/subhd/`: scrapes SubHD.tv Chinese-first movie and episode subtitle pages.
- `providers/subtitlestar/`: scrapes subtitlestar.com for Persian/Farsi movie and episode subtitles.
- `providers/sub_scene/`: scrapes sub-scene.com (Subscene clone) for movie and episode subtitles in 35+ languages including Vietnamese, Arabic, Bengali, Danish, Dutch.
- `providers/subtitlecat/`: first production community provider, scrapes subtitlecat.com using stdlib only. Worked example referenced by [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md).
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
python3 -B -m sdk smoke-test      # exercise a provider against the worker contract
python3 -B -m unittest discover -s tests
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
