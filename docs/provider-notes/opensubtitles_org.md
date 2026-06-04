# OpenSubtitles.org provider notes

Historical provider id: `opensubtitles`.

Catalog provider id: `opensubtitles`. The plugin reuses the built-in id; a trusted catalog install on the Provider Hub migration allow-list overwrites the built-in in place, so there is no duplicate provider and the existing `settings.opensubtitles` config and compat moviehash routing keep working.

## Public behavior

- Supported media: movies and episodes.
- Default mode: external scraper helper at `http://localhost:8000`.
- Helper health endpoint: `GET /health`.
- Helper search endpoints:
  - `POST /api/v1/search/tv`
  - `POST /api/v1/search/movies`
- Helper subtitle endpoint: `POST /api/v1/subtitles`.
- Helper download endpoint: `POST /api/v1/download/subtitle`.
- Legacy XML-RPC mode remains available through `api.opensubtitles.org/xml-rpc` or `vip-api.opensubtitles.org/xml-rpc`.

## Compatibility notes

- The worker keeps the legacy OpenSubtitles file hash key `opensubtitles`.
- XML-RPC mode preserves hash, tag, and IMDB criteria construction.
- Forced subtitles follow the legacy `only_foreign` and `also_foreign` behavior.
- Hearing-impaired variants must be requested as hearing-impaired language payloads.
- Wrong FPS subtitles keep the candidate but drop matches when `skip_wrong_fps` is enabled.

## Core promotion gate

`opensubtitles` is on the Provider Hub built-in migration allow-list (`MIGRATED_BUILT_IN_PROVIDER_IDS`), so a trusted catalog plugin that reuses the id replaces the built-in directly via the same-id registry overwrite. No separate legacy alias is required.

## Live smoke status

Local verification on 2026-05-31 did not run against the helper because `localhost:8000` and `127.0.0.1:8000` were not listening. Unit fixtures cover the helper API contract, but live compat proof still requires a running OpenSubtitles scraper helper.
