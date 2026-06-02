# SubDL provider notes

Clean-room target for `subdl`.

## Public behavior

- Site: `https://subdl.com/`
- API endpoint: `https://api.subdl.com/api/v1/subtitles`
- Download host: `https://dl.subdl.com`
- Supported media: movies and episodes.
- Required setting: `api_key`.
- Optional setting: `anime_mode`.
- API search supports text title, file name, SubDL id, IMDb id, TMDB id, season number, episode number, media type, release year, language codes, comments, releases, hearing-impaired metadata, full-season filters, and `unpack=1`.
- Downloads are zip archives or direct raw subtitle files when `unpack=1` returns a saved unpacked file URL.

## Compatibility quirks

- Movie search prefers IMDb id when available. If the API reports an empty result and TMDB id exists, the provider retries with TMDB id only.
- Episode search prefers series IMDb id when available, otherwise series title.
- Anime mode adds absolute-episode search, season-only search, title-only fallback, and pack-range matching.
- Non-anime mode preserves legacy behavior by skipping multi-episode packs.
- Pack downloads choose the subtitle member matching the requested season and episode, then absolute episode.
- Hearing-impaired and forced flags are inferred from API fields, comments, archive names, and release names.
- Live no-key probe on 2026-05-29 returned HTTP 422 with a schema error body, confirming that real search proof requires a user SubDL API key.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API contracts only.
