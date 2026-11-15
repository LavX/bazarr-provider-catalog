# Subs4Free provider notes

## Public behavior

- Site: `https://www.subs4free.info`
- Supported media: movies only.
- Supported languages:
  - Greek, `ell` / `el`
  - English, `eng` / `en`
- Search endpoint shape: `/search_report.php?search=<query>&searchType=1`
- Download endpoint shape: `/getSub.php`
- No credentials are exposed in the Bazarr UI.

## Compatibility quirks

- Bazarr labels this provider as broken for some users, so live checks must prove both search and download before promotion.
- Search may return direct `.movie-details` result cards or a `Mov_sel` dropdown of matching movie pages. A movie page lists all its subtitles on one page; the search page shows 25 per page. Every matching row is returned.
- Result language is carried by `elgif` and `engif` sprite classes, plus language-specific URL paths.
- Download detail pages expose a hidden `id` field and an image submit button. The worker posts that id with generated `x` and `y` click coordinates to `/getSub.php`.
- Downloads can be direct subtitle bytes, ZIP archives, or RAR archives.

## Anubis and time budgets

- The site sits behind Anubis (v1.26.2 as of 2026-10-24), itself behind Cloudflare. Pages answer `200` with a proof-of-work challenge until the session holds a `*-anubis-auth` cookie. The worker solves it inline with the stdlib (`fast`/`slow` SHA-256, `preact`, `metarefresh`, and the older bare-string challenge) and refetches.
- A failed solve on a `www.subs4free.info` page renews the browser identity and retries, up to three attempts. A gate on any other host, or on the `getSub.php` POST, gets one attempt and no renewal: `getSub.php` only serves a session that has opened the subtitle's page, so a POST from a new session bounces to the detail page and would read as the anti-flood penalty (checked live on 2026-11-12).
- Each site call has a 60-second budget. A whole search or download has `operation_timeout_seconds` (default 90), and the host gives the worker 30 seconds more. A socket watchdog interrupts trickling headers, response bodies and redirects at the deadline, including TLS traffic. It is stopped and joined when the call ends. DNS lookup itself cannot be interrupted; the deadline is checked as soon as it returns. Running out raises `ServiceUnavailable` once, with no retry. A search that already found results returns them.
- A `429` raises `RateLimited` carrying the site's `Retry-After`.

## Downloads

- `getSub.php` grants a download with a `302` to the file. Under its short anti-flood penalty on the client IP, it redirects back to the detail page instead (`Refresh timorias [1]`, `Polla alla isxiei timoria...`). The worker raises `APIThrottled` and does not retry. In a 12-download live run spaced 5 to 120 seconds apart, the one refusal cleared within five minutes. `DownloadLimitExceeded` is left for account quotas, which reset daily.
- The legacy "anti-block" requests (`images.subs4free.info/favicon.ico` and two `subs4series.com` pages) only detected ISP-level blocking for visitors in Greece, and `getSub.php` does not depend on them, so the worker no longer makes them.

## Live notes

Live verification on 2026-05-31 reached `https://www.subs4free.info/` through Cloudflare and returned HTTP `200`. Search for `Inception` returned current `movie-details` result cards with Greek subtitle rows, uploader names, download counts, and download detail links.

Live verification on 2026-10-24: without the Anubis gate, 0.1.5 returned no results, because every search page was the challenge. With 0.2.0, searches for five movies returned Greek and English rows. Twelve downloads were spaced 5 to 120 seconds apart and eleven were granted; the refused one cleared within five minutes.

## License notes

The implementation in this catalog is a clean-room MIT worker using only public request and markup behavior.
