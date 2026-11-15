# Subs4Series Provider Notes

## Status

- Catalog provider id: `subs4series`
- Legacy source reviewed: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subs4series.py`
- Media type: episodes only
- Languages: Greek `ell`, English `eng`
- Origin checked on 2026-06-01: `https://www.subs4series.com`

## Preserved Behavior

- Searches the legacy `search_report.php?search=<title>&searchType=1` flow.
- Uses `select[name="Mov_sel"]` suggestions and accepts exact show title or show title plus year. A title that differs only by a trailing `(US)`-style country or `(2005)`-style year qualifier on either side counts too: Subs4Series names `Shameless (US)` where Sonarr may not, and Sonarr names `The Office (US)` where Subs4Series does not. Different countries never match, and a dated site title needs the video's year.
- A foreign show's site title carries its English title in brackets, `Vis a Vis (Locked Up)`, and matches either name exactly, since Sonarr may send `Locked Up` or `Vis a vis`.
- Subs4Series lists no IMDb/TVDB ids, so shows are told apart by the year in each episode page's header. With a video year:
  - A show matched only by ignoring a qualifier needs a readable page-header year within one year of the video's, even when it is the only one. Sonarr's `The Office (UK)` (2001) finds only the site's US (2005) and 2024 shows, so it gets no results rather than another country's subtitles.
  - Exact and qualified titles then compete on the year: the same year wins, then one year off. A site with both `Shameless` (2004) and `Shameless (US)` (2011) gives a 2011 video the US show.
  - With no show within a year, the exact-title shows are all kept.
- Without a video year, an exact title wins outright, and every show of that title is kept. A page whose year cannot be read is kept when nothing else can be checked.
- An episode page that answers 404 means that show lacks the episode; the search continues with the next show of the same title.
- Builds episode URLs as `/tv-series/<show>/season-<season>/episode-<episode>`.
- Parses `seeDark` and `seeMedium` subtitle rows.
- Maps `el.gif` to Greek and `en.gif` to English.
- Preserves uploader, download count, year, release info, and episode scoring signals.
- Runs the same anti-block endpoints before posting the download request.
- Uses ai-cloudscraper first, solves inline Anubis `/.within.website/` challenges, and keeps optional FlareSolverr fallback when Cloudflare blocks the worker egress IP.
- One site call, an Anubis solve included, has a 60-second budget, as in subs4free: each challenge request, the proof of work and any wait the challenge asks for get only what is left of it. A socket watchdog bounds TLS, headers, redirects and body reads even when the server keeps sending small amounts of data. Running out raises `ServiceUnavailable` once, with no retry. DNS lookup itself cannot be interrupted; the deadline is checked when it returns.
- Returns a direct subtitle body as content, and a ZIP or RAR archive as raw archive bytes that the host extracts. No extraction library is bundled.

## Cloudflare Handling

Subs4Series can return a Cloudflare `403` block to the Bazarr test-server egress even when a local residential live smoke passes. The plugin keeps ai-cloudscraper as the default request path, retries inline Anubis challenges before the original URL, and only calls FlareSolverr when a Cloudflare block is detected.

- `flaresolverr_url`: optional FlareSolverr `/v1` endpoint.
- `flaresolverr_timeout_ms`: maximum FlareSolverr solve/fetch time.

Successful FlareSolverr cookies and User-Agent are reused for later requests in the same provider instance.

## Captcha Handling

Legacy Bazarr solved Subs4Series reCAPTCHA through process-global anti-captcha settings. Provider Hub workers do not inherit that Bazarr process environment, so this plugin exposes explicit settings instead:

- `captcha_response`: a pre-solved response token.
- `captcha_solver_url`: optional helper endpoint that receives `site_key`, `site_url`, `url`, `provider`, and `invisible` (true only for an invisible reCAPTCHA; the live gate is a visible checkbox).
- `captcha_solver_token`: optional bearer token for that helper endpoint.
- `captcha_solver_timeout_ms`: timeout for the helper endpoint.

The reCAPTCHA gate on the getSub page used to guard every download. In November 2026 the getSub page answered with the archive directly in 10 of 11 live fetches, and the one gated subtitle downloaded without a captcha a few minutes later. The gate also follows the client IP: the next day, after a day of heavy testing, one IP got it on every download, still after an hour with no requests, while a phone browser on mobile data opened the same download without one. The gate is a visible reCAPTCHA v2 checkbox, and the solver is told whether the widget is invisible. So a solver is optional:

- With a solver or `captcha_response` set, a gated download is solved and posted at once.
- Without one, the worker waits 5 seconds, reloads the detail page for a fresh getSub token and tries once more.
- If the gate is still there, the worker raises `APIThrottled`, naming the page title. The host sits the provider out and retries later, instead of counting a broken download. For an IP the site has flagged, that can repeat for hours.
- A configured solver that returns no token fails with a clear captcha error, instead of silently returning empty content.
