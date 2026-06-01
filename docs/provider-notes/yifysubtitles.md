# YIFYSubtitles provider notes

Clean-room target for `yifysubtitles`.

## Public behavior

- Site: `https://yifysubtitles.ch`
- Search endpoint shape: `/movie-imdb/<tt-imdb-id>`.
- Detail pages expose a `.download-subtitle` link to the ZIP download.
- Supported media: movies only.
- Supported languages match the public YIFY language names, including hearing-impaired variants when the row has `hi-subtitle`.
- Search parses the `other-subs` table, preserving subtitle id, language, release text, detail URL, rating, hearing-impaired flag, and uploader.
- Downloads fetch the detail page, follow the download link, and extract the best subtitle member from the ZIP archive.

## Compatibility quirks

- Forced subtitles are not advertised by the legacy provider. Forced-only requests return no results.
- HTTP `404` from the IMDb page means the movie has no YIFY subtitle page and returns no results.
- Rows are sorted by Provider Hub score, then YIFY rating.
- Archive member selection prefers release title, resolution, source, and release group matches.
- An early 2026-05-31 probe reached Cloudflare and returned HTTP `504`, but later live and Provider Hub compat proof succeeded.
- Current 2026-06-01 live smoke succeeds with the browser-style `urllib` request path. Do not add `ai-cloudscraper` or FlareSolverr unless the site starts serving a real Cloudflare challenge instead of normal HTML.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
