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
- For an episode, a subtitle or torrent name with a season marker (`S01E01`, `S01.E01`, `1x01` or `Season 1 Episode 1`) must name the requested season as well as the episode. A bare `E01` marker carries no season. Multi-episode tags such as `S01E01E02` or `E01.E02` answer each episode they list, and a range such as `S01E01-03` or `1x01-03` answers both its ends. For the episodes between the ends, such a torrent is still searched and a `.zip` or `.rar` row is kept, since its archive member is picked at download. A single subtitle file does not answer them, so `S01E01-E03.srt` does not answer `E02`.
- The score starts at 70 and rises 5 points per release match (source, resolution, codec, release group and similar). The identity matches from the ID lookup are shared by every result, so they do not add to it.
- Subtitle filenames ending in `.ass`, `.srt`, `.ssa`, `.vtt`, `.zip`, or `.rar` are supported.
- Rows containing `extra`, `commentary`, or `lyrics` in the title or filename are ignored. Forced and hearing-impaired rows only answer requests for that variant.
- Credentials stay in provider config. Search results do not include the passkey in `provider_payload`.

## Review follow-up

Archive selection preserves the requested language country. Explicit Portuguese regional filename tags distinguish Brazilian and European Portuguese when an archive contains both variants.

Archive member selection reads the season and episode from the member's file name first. When the file name leaves them out, a folder such as `Season 2/` or `Show.S01E01/` supplies them, so a member from another season is not pinned for the requested episode. Inside a pack folder such as `Show.S01E01-E10/`, a number in the file name that falls among its episodes (`05.srt` or `05_English.srt`) picks the episode, and a member whose file name picks none is not pinned. A folder for one multi-episode video, such as `Show.S01E01E02/`, answers each of its episodes, and a leading track index there (`2_English.srt`) does not pick one. Track indexes are never zero-padded, so `02_English.srt` there still picks episode 2. Among members that match the episode and language equally, the one whose forced and hearing-impaired tags (`forced`, `sdh`, and `hi` except on Hindi files) fit the request wins. These tags only break ties, so an archive without them still answers every request.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and response contracts only.
