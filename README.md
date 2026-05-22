# Bazarr+ Provider Catalog

> Official community catalog of subtitle provider plugins for the **[Bazarr+](https://github.com/LavX/bazarr) Provider Hub** — load extra subtitle sources into your Bazarr+ install without waiting on a new release.

[![Bazarr+](https://img.shields.io/badge/Built%20for-Bazarr%2B-2ea44f?logo=github)](https://github.com/LavX/bazarr)
[![Bazarr+ Docs](https://img.shields.io/badge/Docs-lavx.github.io%2Fbazarr-blue)](https://lavx.github.io/bazarr/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## What is this?

[Bazarr+](https://github.com/LavX/bazarr) is an enhanced fork of Bazarr that adds a **Provider Hub** — a plugin system that loads subtitle providers from an external catalog and runs each one in an isolated worker. This repo is that catalog.

If you run **Bazarr+ alongside Sonarr / Radarr / Plex / Jellyfin / Emby**, install providers from here via the Bazarr+ Marketplace to expand your subtitle sources beyond the built-in providers — no rebuild, no restart of Sonarr/Radarr.

## Providers in this catalog

| Provider | Version | Notes |
| --- | --- | --- |
| [`subtitlecat`](providers/subtitlecat/) | 0.1.5 | Scrapes [subtitlecat.com](https://www.subtitlecat.com) (no login, no API key). Worked example for the [scraper authoring guide](docs/writing-a-scraper-provider.md). |
| [`smoke`](providers/smoke/) | latest | Deterministic no-network fixture for install / worker sanity checks. Not a real subtitle source. |

More coming. [Contribute](#contributing) your own — every provider here ships independently of Bazarr+ releases.

## Install a provider into Bazarr+

1. Open **Bazarr+** → Settings → Providers → Marketplace.
2. Point the catalog source at this repo's `catalog.json`:
   ```
   https://raw.githubusercontent.com/LavX/bazarr-provider-catalog/main/catalog.json
   ```
3. Browse the Marketplace, click **Install** on the provider you want, and restart Bazarr+ when prompted.
4. Configure the provider from the regular Providers settings page — each plugin advertises its own config schema.

> See the [Bazarr+ documentation](https://lavx.github.io/bazarr/) for the broader install / config flow.

## Layout

- `catalog.json` — embedded Provider Hub V1 catalog manifest. This is the file Bazarr+ fetches.
- `providers/smoke/` — deterministic no-network smoke provider for install and worker checks.
- `providers/subtitlecat/` — first production community provider; scrapes subtitlecat.com using stdlib only. Worked example referenced by [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md).
- `sdk/` — standalone authoring tools and templates ([SDK reference](sdk/README.md)).
- `tests/` — catalog validation tests that do not import Bazarr internals.

## Writing your own provider

Want to add another subtitle source? Start with the full walkthrough:

**→ [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md)**

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

Provider manifests declare pure Python `.py` files only. Dependencies, when needed, must be pinned wheel requirements with SHA256 hashes — see the [SDK reference](sdk/README.md).

## Contributing

Pull requests welcome. A good provider PR:

- Adds `providers/<id>/provider.py` and `providers/<id>/provider.json`.
- Adds `tests/test_<id>.py` exercising query building, parsing, scoring, and the search/download flow against captured HTML fixtures in `tests/fixtures/`.
- Bumps and regenerates `catalog.json` (`python3 -B -m sdk build-catalog`).
- Passes `python3 -B -m sdk validate` and `python3 -B -m unittest discover -s tests`.
- Follows the patterns in [docs/writing-a-scraper-provider.md](docs/writing-a-scraper-provider.md).

## Related

- **[Bazarr+](https://github.com/LavX/bazarr)** — the enhanced Bazarr fork that consumes this catalog. Adds AI translation via OpenRouter, OpenSubtitles.org scraper, provider priority, API key encryption, batch translation, mass subtitle sync, advanced filters, and security hardening.
- [Bazarr+ Documentation](https://lavx.github.io/bazarr/)
- [Bazarr (upstream)](https://github.com/morpheus65535/bazarr)

---

<sub>Keywords: bazarr, bazarr+, bazarr plus, subtitle provider, subtitle plugin, subtitle scraper, subtitlecat, subtitles, jellyfin, plex, emby, radarr, sonarr, opensubtitles, provider hub, python plugin, subtitle automation.</sub>
