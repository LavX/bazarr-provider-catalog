# SubSource provider notes

Clean-room target for `subsource`.

## Public behavior

- Site: `https://subsource.net/`
- API base URL: `https://api.subsource.net/api/v1`
- Required setting: `api_key`.
- Current API authentication: `X-API-Key` header. The live no-key response also says `api_key` query parameter is accepted.
- Supported media: movies and episodes.
- Title lookup endpoint: `GET /movies/search`.
- Subtitle list endpoint: `GET /subtitles`.
- Download endpoint: `GET /subtitles/{id}/download`.
- Downloads are ZIP archives.

## Compatibility quirks

- Title lookup prefers IMDb id when available and falls back to text search when IMDb search returns no data.
- Episode title lookup includes `season`.
- Subtitle search uses one request per requested language and passes `movieId`, `limit=100`, and episode season and episode numbers when present.
- Forced subtitles are only returned for forced language requests.
- Hearing-impaired language requests require hearing-impaired metadata or commentary markers.
- Episode results must parse the requested season from release names. A season result without a specific episode is treated as a pack.
- Pack downloads select the archive member matching the requested season and episode.
- Live no-key probe on 2026-05-29 returned HTTP 401 with `API key required`. Invalid-key probe returned HTTP 401 with `Invalid API key`.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API contracts only.
