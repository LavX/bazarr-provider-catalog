# OpenSubtitles.org provider notes

Historical provider id: `opensubtitles`.

Catalog provider id: `opensubtitles_org`. Current Bazarr+ Provider Hub rejects plugins whose `provider_id` shadows a built-in provider, so the catalog plugin uses an installable id and records the legacy id in result payloads.

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

To replace the built-in `opensubtitles` provider directly, Bazarr+ core needs a trust-gated built-in shadowing or migration rule. Until that exists, install this catalog provider as `opensubtitles_org`.

## Live smoke status

Local verification on 2026-05-31 did not run against the helper because `localhost:8000` and `127.0.0.1:8000` were not listening. Unit fixtures cover the helper API contract, but live compat proof still requires a running OpenSubtitles scraper helper.
