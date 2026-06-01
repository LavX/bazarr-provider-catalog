# Subs4Series Provider Notes

## Status

- Catalog provider id: `subs4series`
- Legacy source reviewed: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subs4series.py`
- Media type: episodes only
- Languages: Greek `ell`, English `eng`
- Origin checked on 2026-06-01: `https://www.subs4series.com`

## Preserved Behavior

- Searches the legacy `search_report.php?search=<title>&searchType=1` flow.
- Uses `select[name="Mov_sel"]` suggestions and accepts exact show title or show title plus year.
- Builds episode URLs as `/tv-series/<show>/season-<season>/episode-<episode>`.
- Parses `seeDark` and `seeMedium` subtitle rows.
- Maps `el.gif` to Greek and `en.gif` to English.
- Preserves uploader, download count, year, release info, and episode scoring signals.
- Runs the same anti-block endpoints before posting the download request.
- Uses ai-cloudscraper first and optional FlareSolverr fallback when Cloudflare blocks the worker egress IP.
- Supports direct subtitle bodies, ZIP archives, and RAR archives through the bundled `py7zz` dependency.

## Cloudflare Handling

Subs4Series can return a Cloudflare `403` block to the Bazarr test-server egress even when a local residential live smoke passes. The plugin keeps ai-cloudscraper as the default request path and only calls FlareSolverr when a Cloudflare block is detected.

- `flaresolverr_url`: optional FlareSolverr `/v1` endpoint.
- `flaresolverr_timeout_ms`: maximum FlareSolverr solve/fetch time.

Successful FlareSolverr cookies and User-Agent are reused for later requests in the same provider instance.

## Captcha Handling

Legacy Bazarr solved Subs4Series reCAPTCHA through process-global anti-captcha settings. Provider Hub workers do not inherit that Bazarr process environment, so this plugin exposes explicit settings instead:

- `captcha_response`: a pre-solved response token.
- `captcha_solver_url`: optional helper endpoint that receives `site_key`, `site_url`, `url`, `provider`, and `invisible`.
- `captcha_solver_token`: optional bearer token for that helper endpoint.
- `captcha_solver_timeout_ms`: timeout for the helper endpoint.

If Subs4Series asks for reCAPTCHA and no response is available, downloads fail with a clear captcha error instead of silently returning empty content.
