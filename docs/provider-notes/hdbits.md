# HDBits provider notes

Clean-room target for `hdbits`.

## Public behavior

- Site: `https://hdbits.org`
- Search endpoints:
  - `POST https://hdbits.org/api/torrents`
  - `POST https://hdbits.org/api/subtitles`
- Download endpoint:
  - `GET https://hdbits.org/getdox.php?id=<subtitle_id>&passkey=<passkey>`
- Required settings:
  - `username`
  - `passkey`
- Supported media:
  - Movies use IMDb id lookup, with the `tt` prefix stripped.
  - Episodes use TVDB series id plus season lookup, then filter explicit subtitle episode tags locally.
- Supported downloads:
  - Direct subtitle files.
  - ZIP archives.
  - RAR archives through bundled `py7zz`, with `unar` or `7z` fallback.

## Compatibility quirks

- HDBits language codes are mostly alpha2, with legacy special cases:
  - `uk` means English.
  - `br` means Brazilian Portuguese.
  - `gr` means Greek.
- Subtitle filenames ending in `.ass`, `.srt`, `.ssa`, `.vtt`, `.zip`, or `.rar` are supported.
- Rows containing `extra`, `commentary`, `lyrics`, or `forced` in the title or filename are ignored.
- Credentials stay in provider config. Search results do not include the passkey in `provider_payload`.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and response contracts only.
