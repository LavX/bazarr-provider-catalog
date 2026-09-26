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
  - ZIP and RAR archives through the host archive API, preserving language and episode selection.

## Compatibility quirks

- HDBits language codes are mostly alpha2, with legacy special cases:
  - `uk` means English.
  - `br` means Brazilian Portuguese.
  - `gr` means Greek.
- Because `uk` is English, no known HDBits code means Ukrainian, so `ukr` is not advertised.
- The manifest declares base codes only, so a plain Portuguese request also returns `br` rows, labelled `por` with `country_alpha2: BR`. A Brazilian request still skips plain `pt` rows.
- For an episode, a subtitle or torrent name with an `SxxEyy` marker must name the requested season as well as the episode. A bare `E01` marker carries no season.
- The score starts at 70 and rises 5 points per release match (source, resolution, codec, release group and similar). The identity matches from the ID lookup are shared by every result, so they do not add to it.
- Subtitle filenames ending in `.ass`, `.srt`, `.ssa`, `.vtt`, `.zip`, or `.rar` are supported.
- Rows containing `extra`, `commentary`, `lyrics`, or `forced` in the title or filename are ignored.
- Credentials stay in provider config. Search results do not include the passkey in `provider_payload`.

## Review follow-up

Archive selection preserves the requested language country. Explicit Portuguese regional filename tags distinguish Brazilian and European Portuguese when an archive contains both variants.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and response contracts only.
