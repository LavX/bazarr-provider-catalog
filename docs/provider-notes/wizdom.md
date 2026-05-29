# Wizdom provider notes

Historical provider id: `wizdom`.

## Public behavior

- Site: `https://wizdom.xyz`.
- API release endpoint: `GET /api/releases/<imdb_id>`.
- Download endpoint: `GET /api/files/sub/<subtitle_id>`.
- Supported media: movies and episodes.
- Supported language: Hebrew, `heb`.
- TMDB is used to resolve missing IMDb ids with the legacy public API key.

## Compatibility notes

- Wizdom only supports plain Hebrew. Requests for forced or hearing-impaired Hebrew should not return results.
- Episode results support both season dictionary and season list response shapes.
- HTTP 500 from the Wizdom release endpoint means no subtitles.
- Downloads are ZIP payloads containing `.srt` or `.sub`; when multiple candidates exist, choose the first text-valid subtitle member.

## Live smoke status

Local verification on 2026-05-31 did not complete against `wizdom.xyz`: both the home page and `https://wizdom.xyz/api/releases/tt1375666` timed out after 20 seconds with no bytes received. Unit tests cover the API contract, but live compat proof still requires the origin to respond.
