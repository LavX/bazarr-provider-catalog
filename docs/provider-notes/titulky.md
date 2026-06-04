# Titulky.com provider notes

Clean-room target for `titulky`.

## Public behavior

- Site: `https://premium.titulky.com`
- Login endpoint: site root with form fields `LoginName` and `LoginPassword`.
- Browse endpoint shape:
  `/?action=serial&step=<season>&id=<imdb-id-without-tt>`
- Detail endpoint shape:
  `/?action=detail&id=<subtitle-id>`
- Download endpoint shape:
  `/download.php?id=<subtitle-id>`
- Supported media: movies and episodes.
- Supported languages:
  - `ces` from Czech flag rows
  - `slk` from Slovak flag rows
- Movie search uses season step `0` and episode `0`.
- Episode search uses the series IMDb id, season step, and requested episode row.
- Downloads may be direct subtitle files, ZIP archives, or RAR archives.

## Compatibility quirks

- Credentials are required and must be a VIP-capable account.
- `approved_only` filters out `pbl0` rows when enabled.
- `skip_wrong_fps` fetches the detail page and drops matches for FPS-mismatched subtitles, preserving legacy low-score behavior.
- FPS values `23.976`, `23.98`, and `24.0` are treated as equivalent.
- HTTP `429` is treated as a provider rate limit.
- A single-file archive with no subtitle member indicates that the daily download limit was exceeded.
- RAR extraction uses bundled `py7zz` first, with `unar` or `7z` as local fallbacks.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
