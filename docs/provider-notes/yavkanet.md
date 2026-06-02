# Yavka.net provider notes

Clean-room target for `yavkanet`.

## Public behavior

- Site: `https://yavka.net/`
- Search endpoint shape: `/imdb/<tt-imdb-id>`.
- Detail pages may expose either a legacy POST form with hidden fields or a current direct `/download?q=...` link.
- Supported media: movies and episodes.
- Supported languages: Bulgarian, English, Russian, Spanish, and Italian.
- Search scans the latest IMDb rows exposed by the site, keeps the last 50 rows, then fetches each detail page to capture either the download form or direct download link.
- Downloads either post the captured form back to the detail action URL or fetch the direct download link, and may return ZIP, RAR, or direct subtitle bytes.

## Compatibility quirks

- The provider uses `ai-cloudscraper` by default with the same native interpreter session shape used by the OpenSubtitles.org plugin. It solves inline Anubis `/.within.website/` challenges before retrying the original URL. `flaresolverr_url` is an optional fallback used only when ai-cloudscraper still receives a Cloudflare challenge.
- FlareSolverr solutions can supply a User-Agent and cookies, which must be reused for later requests in the same provider instance.
- Current Cloudflare solves can take longer than 10 seconds, so the optional FlareSolverr timeout is capped at 30000 ms while the HTTP client wait stays within the Provider Hub worker budget.
- Current direct download links can still return Yavka.net's own HTTP 403 page for unavailable titles even after Cloudflare clearance.
- A Yavka.net banner dated June 2 says some titles are no longer available for downloads and recommends using `bultor.net` or `nanoset.biz` instead.
- IMDb-specific movie pages are trusted even when the displayed row title is localized or does not contain the requested title.
- Archive selection uses the requested video metadata. Episode archives with explicit episode markers must not return a wrong episode member.
- Unsupported languages return no results without touching the network.
- RAR extraction uses bundled `py7zz` first, with `unar` or `7z` as local fallbacks.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
