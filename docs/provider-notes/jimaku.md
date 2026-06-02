# Jimaku provider notes

Clean-room target for `jimaku`.

## Public behavior

- Site: `https://jimaku.cc`
- API documentation: `https://jimaku.cc/api/docs`
- OpenAPI document: `https://jimaku.cc/api/openapi.json`
- API base: `https://jimaku.cc/api`
- Authentication: API key in the `Authorization` header.
- Supported media: movies and episodes.
- Supported language: Japanese (`jpn`).
- Entry search endpoint:
  - `GET /api/entries/search`
  - Prefer `anilist_id` when present.
  - Use `tmdb_id` in Jimaku's documented `movie:<id>` or `tv:<id>` form when present.
  - Fall back to fuzzy `query` search when enabled. For episode seasons above 1, append the season number to the query.
  - Retry name searches with `anime=false` when the default anime search returns no entries.
- File endpoint:
  - `GET /api/entries/{id}/files`
  - For series entries, pass the requested episode number when possible.
  - If AniDB season episode offset metadata is present, try the adjusted episode number first.
  - If a series episode search returns no files, retry without `episode` and keep archive files only.
- Download flow:
  - Direct subtitle files are returned as-is.
  - ZIP/RAR archives are extracted, with episode-aware member selection.

## Compatibility quirks

- The provider rejects files smaller than 500 bytes as likely corrupt.
- `.7z` files are ignored.
- ZIP/RAR archives are skipped when direct subtitle files are available unless `enable_archives_download` is enabled.
- Archives are still used when archives are the only available files.
- Filenames that look like Whisper or WhisperAI output are skipped unless `enable_ai_subs` is enabled.
- Likely multilingual subtitles are skipped. Filenames without a reliable language marker are treated as Japanese.
- Credentials stay in provider config. Search results do not include the API key in `provider_payload`.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and response contracts only.
