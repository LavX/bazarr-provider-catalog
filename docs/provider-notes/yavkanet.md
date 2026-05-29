# Yavka.net provider notes

Clean-room target for `yavkanet`.

## Public behavior

- Site: `https://yavka.net/`
- Search endpoint shape: `/imdb/<tt-imdb-id>`.
- Detail pages expose a POST form with hidden fields used for download.
- Supported media: movies and episodes.
- Supported languages: Bulgarian, English, Russian, Spanish, and Italian.
- Search scans the latest IMDb rows exposed by the site, keeps the last 50 rows, then fetches each detail page to capture the download form.
- Downloads post the captured form back to the detail action URL and may return ZIP, RAR, or direct subtitle bytes.

## Compatibility quirks

- The provider uses `cloudscraper` by default. `flaresolverr_url` is an optional fallback used only when cloudscraper still receives a Cloudflare challenge.
- FlareSolverr solutions can supply a User-Agent and cookies, which must be reused for later requests in the same provider instance.
- IMDb-specific movie pages are trusted even when the displayed row title is localized or does not contain the requested title.
- Archive selection uses the requested video metadata. Episode archives with explicit episode markers must not return a wrong episode member.
- Unsupported languages return no results without touching the network.
- RAR extraction uses bundled `py7zz` first, with `unar` or `7z` as local fallbacks.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
