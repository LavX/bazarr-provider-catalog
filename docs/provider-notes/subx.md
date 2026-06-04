# SubX Provider Notes

## Upstream

- API docs: https://subx-api.duckdns.org/docs/getting-started/quickstart/
- API base URL: `https://subx-api.duckdns.org`
- Search endpoint: `GET /api/subtitles/search`
- Download endpoint: `GET /api/subtitles/{id}/download`
- Authentication: `Authorization: Bearer <api_key>`

## Legacy Parity

- Supports movie and episode searches.
- Prefers IMDb ID when available, then falls back to title search.
- Keeps exact episode filtering and season-pack fallback for episode searches.
- Episode searches use the series title plus alternative series titles, each with exact episode, season, and series-only fallbacks.
- Supports Spanish subtitles and preserves the legacy Spain versus Latin American Spanish distinction by detecting release descriptions.
- Retries documented rate-limit and server-error responses. Search-side runtime failures exhaust retries and then return no results, matching the legacy provider's graceful fallback.
- Downloads direct subtitle files, ZIP archives, and RAR archives. RAR extraction prefers bundled `py7zz` and keeps system `unar` or `7z` as runtime fallbacks.

## Live Checks

- The public docs list `/api/health` as the unauthenticated health endpoint.
- Search and download proof require a real SubX API key.
