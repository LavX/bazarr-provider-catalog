# SuperSubtitles provider notes

Clean-room target for `supersubtitles`.

## Public behavior

- Site: `https://feliratok.eu/`
- Movie search endpoint shape: `GET /index.php?search=<title>&soriSorszam=&nyelv=&tab=film`
- Series lookup endpoint shape: `GET /index.php?term=<series>&nyelv=0&action=autoname`
- Episode search endpoint shape: `GET /index.php?action=xbmc&sid=<series_id>&ev=<season>&rtol=<episode>`
- Detail endpoint shape: `GET /index.php?tipus=adatlap&azon=a_<subtitle_id>`
- Download endpoint shape: `GET /index.php?action=letolt&felirat=<subtitle_id>`
- Supported media: movies and episodes.
- Supported languages:
  - `hun`
  - `eng`
- Movie rows expose localized title, original title, language label, release list, uploader, forced markers, detail page, and download link.
- Episode rows are JSON and can contain multiple rows for the same subtitle id, one per compatible release. Rows are grouped into one candidate with all release names.
- Episode searches first request the exact episode. If no rows are returned, they retry the same season without the episode parameter to pick up season packs.
- Downloads can be direct subtitle files or ZIP/RAR archives.

## Compatibility quirks

- Forced movie subtitles are detected from `szinkronoshoz` or `forced` text. The episode JSON does not expose a forced flag, so forced-only episode requests return no results.
- Hearing-impaired variants are not advertised by the legacy provider and are skipped.
- Episode names such as `Series - 1x05 (WEB.2160p-GROUP)` and season-pack names such as `Series (Season 1) (WEB-DL)` are normalized to the series title plus release text.
- Detail pages are fetched to attach IMDb ids before final match filtering.
- Archive extraction prefers the requested episode and then release hints from the candidate.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
