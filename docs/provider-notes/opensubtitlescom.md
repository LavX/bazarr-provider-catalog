# OpenSubtitles.com Provider Notes

## Upstream

- API docs: https://ai.opensubtitles.com/docs
- API base URL: `https://api.opensubtitles.com/api/v1`
- Login endpoint: `POST /login`
- Search endpoint: `GET /subtitles`
- Title lookup endpoint: `GET /features`
- Download endpoint: `POST /download`
- Authentication: `Api-Key` header plus login token. Search includes `Authorization: Bearer <token>` when the login response returns a VIP API host. Download always uses the bearer token.

## Legacy Parity

- Supports movie and episode searches.
- Keeps OpenSubtitles moviehash search with the legacy no-hash retry when a hash query returns no results.
- Preserves title feature fallback for searches without IMDb IDs.
- Preserves OpenSubtitles.com language aliases for Portuguese, Brazilian Portuguese, Chinese, Mexican Spanish, and Montenegrin.
- Preserves forced subtitle filtering by treating `foreign_parts_only` without hearing-impaired as the real forced result.
- Preserves hearing-impaired search, trusted uploader metadata, download counts, ratings, FPS, uploader, upload date, hash match, and page URL in result payloads.
- Excludes AI translated and machine translated subtitles by default unless the matching setting is enabled.
- Retries once after an expired search or download token by clearing the cached token and logging in again.
- Downloads the API-provided link and supports direct subtitle files or ZIP archives.

## Live Checks

- A no-key probe to `https://api.opensubtitles.com/api/v1/infos/languages` returned HTTP 200 with the current API language table.
- A fake-key probe to `https://api.opensubtitles.com/api/v1/subtitles` returned HTTP 403 with `You cannot consume this service`, confirming API key enforcement.
- Full search and download proof requires a real OpenSubtitles.com username, password, and API key.
