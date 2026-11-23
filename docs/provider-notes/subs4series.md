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
- Also offers the season packs of the requested episode's season. An episode page lists only the subtitles filed under that episode, and the site files a pack under episode 0, under no episode, or under whichever episode the uploader picked (all three seen on one show's season 1 in November 2026). So a search also reads the show's season listing (`/season-<season>`, 25 subtitles a page, the second page at `/p-1`), up to 5 pages with the last one always read, since the episode-0 packs sort to the end.
  - A pack is a release name with a season and no episode (`Showname S01 1080p BluRay x264-GRP`, `Showname Season 1 Complete`, `Showname S01E HDTV`), a range of seasons (`Showname S01-S03`, `S01-03`, `Season 1-3`, `Seasons 1 - 3`), episode 0 (`Showname S01E0 - - 1080p-GRP`, `S01E0Preview`), or a range of three or more episodes (`Showname S01E01-E10`, `Season 1 Episodes 13-22`), which holds only its own episodes. That holds even for `S04E60-81 - Complete Season`: such a pack numbers its files from the show's start, so it cannot give the season's episode 5. `Season 2 10 Episodes` counts the season's episodes and is a pack of the whole season. A miniseries pack without a season (`Episodes 1-10`, `(All Episodes)`) is read as a pack of the season it is filed under. A misfiled subtitle of another episode or season (`Showname 3x17` on the season 1 listing) is not a pack, nor is a name that gives an episode another way (`Showname S01 Episode 5`, `Showname Season 1 - 05`).
  - A pack matches the series, season and episode, and is scored like the episode's own subtitles. A pack filed under the episode itself is scored the same way but downloaded like any subtitle of the episode, since a name like `Showname S01 720p` there is as often that episode's own subtitle.
  - A listing pack's payload carries `season_pack: true` and the requested season and episode. Its download must be an archive; a single subtitle fails. The archive is returned with `select_member: true`, so the host lists it, zip and rar alike, and calls `select_archive_member`. That pins the member of the episode, the one closest to the release name when several hold it, and rejects a pack that names other episodes but not this one, or holds one subtitle naming none. Members naming no episode at all are left to the host's pick, except in a pack of several seasons, which is rejected, since an episode number alone could take another season's subtitle.
  - A member's episode is read from its file name, or from its folder when the file name names none (a bare number such as `H.264` does not count). A file named only `Episode 3` takes its season from the folder, and otherwise counts in a pack of one season or a miniseries pack.
  - When the episode has no page (a 404: nobody filed a subtitle under it), the season listing is still read, for its packs and for the show's year. When another show of the same title has the episode, a show without it joins only within a year of the video's year, and without a video year not at all, since nothing else tells the shows apart.
  - A season listing that fails to load is logged and leaves the episode's own subtitles in place. A Cloudflare block, a spent time budget or a 429 is raised when the search has found nothing; otherwise it is logged, the remaining listings are skipped, and the subtitles found are returned.
- Reads the season and episode from release and zip member names in every form seen across the site's listings (over 4,000 names from 47 shows, November 2026): `S01E01`, `S02-Ep01`, `S01 EP01` (zip members of a season pack), `S01 Episode 5`, `S01E01The One…` (a word run into the number), `S0405`, `3x17`, `s1x05`, `1×02`, `S2xEP01`, `Season 1 Episode 01` and `Season1-Episode12` (older uploads), and the episode before the season, `Showname-13-(SEASON 2)` or `Showname 11-Season2`, which needs the `-` or `(` so that `Showname 24 Season 1` stays a pack. A double episode, `S01E24-25`, `Episode24,25`, `4x13&14`, `S04E01E02` or `S04E0102`, counts for both episodes, and a range or chain, `S01E01-E10`, `S01E01-S01E10`, `1x01-1x10`, `S1x12-22`, `Season 4 ep 1-11` or `S01E05,06,07`, for every episode in it. A last number without its own marker must follow a bare `-`, `,` or `&` and end the word, so `S01E05-720p` and `S01E05 - 2 Parts` stay one episode, and the marker before the first number must start a word or follow the season, so `S02E01 The 1-2 Punch` stays episode 1. A miniseries name without a season, `E05`, `Part5`, `Episodes 1-10` or `1-5 episodes`, matches the episode alone and scores 95. Names without any number, such as specials, match the series alone.
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
