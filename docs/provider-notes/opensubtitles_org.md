# OpenSubtitles.org provider notes

Catalog provider id: `opensubtitles`. A trusted catalog installation replaces the built-in provider under the same id, so existing `settings.opensubtitles` configuration and OpenSubtitles file-hash matching continue to work.

## Public behavior

- Searches movies and episodes on `www.opensubtitles.org`, then follows subtitle pages and download links on the site's own hosts.
- Creates one randomized desktop Chrome identity per provider session. The browser header and cookies stay together across requests. An explicit Anubis failure or HTTP 401/403 replaces the whole session and retries within the existing three-attempt budget.
- Solves Anubis challenges inline. `ai-cloudscraper` handles Cloudflare where possible; an optional FlareSolverr `/v1` endpoint is used only for a remaining Cloudflare browser challenge. A FlareSolverr user agent stays paired with its returned cookies until the session is replaced.
- Stops immediately on HTTP 429, including rate limits returned by Anubis endpoints. Repeated challenge failures become a visible service error after the bounded attempts.
- Shares a 110-second monotonic budget across every network step in one search or download. Direct provider probes have the same bound. Each request and optional FlareSolverr call receives a timeout limited by the remaining budget; new retries, challenge computation, and delays stop when it is exhausted, leaving time for the host worker to report the error.

## Compatibility notes

- The worker keeps the legacy OpenSubtitles file hash key `opensubtitles`.
- Forced subtitles follow `only_foreign` and `also_foreign`; hearing-impaired variants use the requested language payload.
- When `skip_wrong_fps` is enabled, subtitles with a different FPS remain candidates but lose match claims.

An origin outage, account quota, or a changed challenge protocol can still block a search. A successful anonymous fetch does not establish download acceptance. Verify search, download-link creation, and a non-empty subtitle stream on the target Bazarr instance before promoting a candidate.
