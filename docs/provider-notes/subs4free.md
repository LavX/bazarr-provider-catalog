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
- The site sits behind Anubis (v1.26.2 as of 2026-10-24), itself behind Cloudflare. Every page answers `200` with a proof-of-work challenge until the session holds a `*-anubis-auth` cookie. The worker solves it inline with the stdlib (`fast`/`slow` SHA-256, `preact`, `metarefresh`) and refetches. A failed solve on `www.subs4free.info` renews the browser identity and retries, up to three attempts. A gate on any other host (such as subs4series.com, which runs its own) gets one attempt and never resets the `www` clearance cookie that the `getSub.php` POST needs. One site call has a 60-second budget covering every attempt, solve and backoff, which keeps it well inside the host's 120-second worker timeout. Running out of the budget raises `ServiceUnavailable` once, with no renewal and no retry. Older Anubis builds that publish the challenge as a bare string (as subs4series parses) are accepted too. A `429` raises `RateLimited` carrying the site's `Retry-After` (seconds or HTTP-date) as `retry_after`.
- Search may return direct `.movie-details` result cards or a `Mov_sel` dropdown of matching movie pages.
- Result language is carried by `elgif` and `engif` sprite classes, plus language-specific URL paths.
- Download detail pages expose a hidden `id` field and an image submit button. The worker posts that id with generated `x` and `y` click coordinates to `/getSub.php`.
- The legacy "anti-block" sequence (`images.subs4free.info/favicon.ico` and two `subs4series.com/includes/anti-block...` pages) only detects ISP-level blocking for visitors in Greece: the script sets an `epoeBlock` cookie on subs4series.com and shows access instructions. `getSub.php` does not depend on it. Eleven live downloads across the end of October 2026 succeeded without parts of it, six of them with none of it, so the worker no longer makes these requests. The images host also now has its own Anubis `/login?redirectTo=` gate.
- Downloads can be direct subtitle bytes, ZIP archives, or RAR archives.
- `getSub.php` answers a granted download with a `302` to the file (`smp/tmp/<name>.zip`). It also enforces an anti-flood penalty on the client IP: a refused download is a `302` back to the subtitle's detail page with a short body such as `Refresh timorias [1]<BR>` or `Polla alla isxiei timoria...<BR>`. Followed, the redirect lands on the detail page, which still carries the download form. A second download a couple of seconds after the first was refused in live checks. The penalty is short: in a 12-download run spaced 5 to 120 seconds apart, the one refusal had cleared within five minutes. The worker raises `APIThrottled` for both shapes and does not retry, since a retry inside the penalty is refused again. The host counts `APIThrottled`: the provider sits out the current run, and five refusals within two minutes pause it for 10 minutes by default. `DownloadLimitExceeded` is left for account quotas, which reset daily.

## Live notes

Live verification on 2026-05-31 reached `https://www.subs4free.info/` through Cloudflare and returned HTTP `200`. Search for `Inception` returned current `movie-details` result cards with Greek subtitle rows, uploader names, download counts, and download detail links.

Live verification on 2026-10-24: without the Anubis gate, 0.1.5 returned no results, because every search page was the challenge. With 0.2.0, searches for five movies returned Greek and English rows. Twelve downloads were spaced 5 to 120 seconds apart and eleven were granted; the refused one cleared within five minutes.

## License notes

The implementation in this catalog is a clean-room MIT worker using only public request and markup behavior.
