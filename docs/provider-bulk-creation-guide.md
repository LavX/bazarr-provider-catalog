# Bulk Provider Creation Guide

> **Audience:** Developers adding many Bazarr+ Provider Hub catalog providers.
> **Goal:** Keep provider work repeatable, testable, and compatible with the OpenSubtitles-style API surface that clients actually use.

Use the [scraper authoring guide](writing-a-scraper-provider.md) for the first end-to-end walkthrough. Use this guide as the operating checklist when you are creating providers in bulk.

## Definition of done

A provider is done only when all of these are true:

- Parser tests pass against captured fixtures.
- `search()` and `download()` pass the worker-shaped contract.
- `python3 -B -m sdk build-catalog` and `python3 -B -m sdk validate` pass.
- `python3 -B -m unittest discover -s tests` passes.
- A live smoke test returns at least one result for a real fixture.
- A test Bazarr+ install can install or update the provider, restart, and load it.
- The compat API returns the provider in `/api/v1/subtitles`.
- `/api/v1/download` returns a stream link for that provider.
- The stream link returns non-empty subtitle bytes.
- Logs show no provider worker errors for the search or download path.

Do not stop at "the provider appears in native search". The compat path is what Jellyfin-style clients exercise.

## Provider selection

Prefer a catalog plugin unless the provider must ship as a built-in Bazarr+ capability. Catalog plugins are faster to iterate, version independently, and run in isolated workers.

Use the official API first when one exists and covers the required search and download behavior. Treat scraping as the fallback, not the default. APIs are usually more stable, easier to test, less likely to trigger anti-bot systems, and clearer to operate under rate limits.

Use scraping only when:

- No official API exists.
- The official API misses required subtitle data.
- API access is blocked by unsuitable pricing, quota, approval, or account limits.
- The site exposes the needed flow only through public HTML.

If the official API needs credentials, expose them through `config_schema`, mark secrets in `secret_fields`, and keep those values out of results, logs, fixtures, and `provider_payload`.

Before writing code, check the target site:

- Media coverage: movie, episode, anime, Asian drama, or a narrow niche.
- Language coverage: exact languages and whether the site mixes source language with subtitle language.
- Official access: API docs, auth model, quota, terms, search endpoint, download endpoint, and whether the API covers the same data as the website.
- Search flow: query parameters, encoding, pagination, and whether results are usable without JavaScript.
- Detail flow: result links, detail pages, file rows, forms, tokens, and redirects.
- Download flow: direct subtitle, zip, rar, 7z, multi-file archive, or generated download.
- Access limits: login, CAPTCHA, Cloudflare-style challenge, throttling, or unusual User-Agent behavior.
- Stability: readable HTML markers, stable IDs, and clean detail URLs.

Avoid sites that require a browser challenge or account automation unless you already have a supported helper service. Provider Hub workers should be deterministic HTTP clients, not headless browsers.

When a site is hidden behind Cloudflare and no official API can replace the scrape, use a helper-service pattern like `/home/lavx/opensubtitles-scraper/`: try a normal session first, detect Cloudflare challenge responses, fall back to FlareSolverr, cache the returned cookies and User-Agent, and expose health or status so failures are visible. Keep this bypass explicit in config and tests. Do not silently add a browser dependency to a provider worker.

## Discovery checklist

Capture real fixtures before implementation:

```bash
mkdir -p tests/fixtures
curl -sL -A "Mozilla/5.0" "https://example.test/search?q=Interstellar+2014" \
  -o tests/fixtures/<provider>_search_interstellar.html
curl -sL -A "Mozilla/5.0" "https://example.test/detail/123" \
  -o tests/fixtures/<provider>_detail_interstellar.html
```

Then answer these questions in code or test names:

- What is the exact search URL for movie and episode queries?
- Which selectors or HTML markers identify one result?
- What stable ID can become `provider_payload["id"]`?
- Is the subtitle language visible on the search page, the detail page, or only in the filename?
- Does a result expose multiple subtitle files?
- Does download require a hidden token, form POST, cookie, referrer, or delay?
- What archive formats appear in real downloads?
- What does a bad search, deleted detail page, or empty archive look like?

## Implementation shape

Keep `provider.py` small and predictable:

- Put query building, HTML parsing, language mapping, scoring, archive extraction, and filename selection in pure functions.
- Let the provider class own HTTP calls and orchestration.
- Keep `search()` side-effect free except HTTP.
- Keep `download()` focused on fetch, extract, encode, and checksum.
- Put only stable, serializable data in `provider_payload`.
- Never put secrets, full HTML blobs, cookies, or raw subtitle bytes in `provider_payload`.

Recommended result payload rules:

- Include the provider's stable file or release ID.
- Include enough URL information to download without re-searching.
- Include archive member hints only when they came from the site or fixture.
- Include release metadata that helps display and scoring.
- Keep payloads small because Bazarr persists and passes them through worker boundaries.

## Archive extraction

Fansubs.ru exposed the most important archive lesson: do not rely on the host image having the right extraction tools.

Provider bundles include declared `.py` files. Provider runtime environments can install hash-locked PyPI wheels declared in `provider.json`. Bundles cannot include raw native binaries, vendored dependency folders, symlinks, sdists, or app-environment installs. If a provider needs extraction support, declare a wheel that ships what it needs.

For Fansubs, RAR extraction had to use bundled `py7zz` first. System `7z` failed on older RAR methods, while the bundled dependency succeeded inside the provider environment.

Use this ordering for archive work:

1. Detect archive type by bytes, not only by file extension.
2. Use bundled Python dependencies first.
3. Fall back to optional system tools only after the bundled path fails.
4. Pick the best subtitle member by requested language, extension, filename, and size.
5. Reject empty extraction results.
6. Test without system extractors in `PATH`.

Treat a `200` response with an empty stream as broken. The user sees no usable subtitle even though the HTTP status looks successful.

## Dependency policy

Dependencies are installed into the provider venv, not into Bazarr itself.

Provider Hub targets Bazarr+ Python `>=3.12,<3.15`, currently `3.12`, `3.13`, and `3.14`. Python `3.11` is not the catalog compatibility floor. Run `python3 -B -m sdk runtime-matrix` before reviewing dependency hashes.

Rules:

- Use stdlib unless a dependency removes real risk or complexity.
- Pin exact versions.
- Include SHA256 wheel hashes.
- Include every direct and transitive dependency needed under `--require-hashes`.
- Use wheels, not sdists. Pure `py3-none-any` wheels can share one hash, while ABI-specific wheels need coverage for `cp312`, `cp313`, and `cp314` on every Bazarr+ platform the provider should install on.
- Run `python3 -B -m sdk validate` after every manifest change.
- Bump the provider version for every runtime behavior change.

Provider Hub update detection is version-based. If you rebuild a bundle with the same provider version, a test server may not stage it as an update.

## Test matrix

Each real provider should have tests for:

- Query construction for movie and episode inputs.
- Search parser happy path and empty page.
- Detail parser happy path and missing fields.
- Language normalization.
- Scoring and match keys.
- Search integration with stubbed HTTP.
- Download integration with stubbed HTTP.
- Archive extraction, including empty archive and unknown archive cases.
- Clean dependency install when the provider declares wheels.

Useful commands:

```bash
python3 -B -m unittest discover -s tests
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
python3 -B -m sdk runtime-matrix
python3 -B -m sdk smoke-test --provider <provider> --language <alpha3> \
  --video-fixture tests/fixtures/<provider>_video_fixture.json \
  --expect-min-results 1
```

For archive providers, add a clean environment check where system extractors are not visible:

```bash
mkdir -p /tmp/no-system-tools
ln -sf "$(command -v python3)" /tmp/no-system-tools/python3
PATH=/tmp/no-system-tools python3 -B -m sdk smoke-test --provider <provider> \
  --language <alpha3> \
  --video-fixture tests/fixtures/<provider>_video_fixture.json \
  --expect-min-results 1
```

## Compat API verification

Use environment variables for secrets. Do not paste API keys into docs, scripts, commits, or fixtures.

```bash
BASE_URL="http://127.0.0.1:6767"
API_KEY="${BAZARR_API_KEY:?set BAZARR_API_KEY}"

curl -sS -H "X-API-KEY: $API_KEY" \
  "$BASE_URL/api/v1/subtitles?type=episode&language=rus&seriesid=<series>&season=1&episode=8"
```

Verify the response by provider, not only by total count:

- `total_count` is greater than zero.
- At least one item has `provider` equal to the provider under test.
- The item has a stable `file_id` or equivalent download identifier.

Then download and stream that exact candidate:

```bash
curl -sS -X POST -H "X-API-KEY: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"file_id":"<file_id>"}' \
  "$BASE_URL/api/v1/download"

curl -sS -H "X-API-KEY: $API_KEY" "$BASE_URL/download/stream/<token>" | head
```

Check the stream:

- HTTP status is `200`.
- Byte count is greater than zero.
- The first visible line looks like subtitle content, such as `1`, `[Script Info]`, or `WEBVTT`.
- Logs contain no provider worker exception for the request.

## Common failure patterns

- Provider appears in settings, but compat search does not return it: the worker may not load, the provider is disabled, the language does not match, or the test request shape does not match supported media.
- Native search works, but compat stream is empty: the download path is broken.
- Unit tests pass, but test server still runs old code: the provider version was not bumped or the server was not restarted.
- Archive tests pass locally, but server extraction fails: local system tools hid a missing bundled dependency.
- Search returns unrelated results: query generation is too broad or scoring accepts weak title matches.
- Episode searches miss results: the site may index anime by absolute episode, season episode, localized title, or year.
- Reusing an unrelated `moviehash` can force slower or different compat paths. Use client-shaped requests unless hash behavior is the target.

## Bulk workflow

For each provider:

1. Start from a clean branch or worktree if the checkout is dirty.
2. Capture search, detail, and download fixtures.
3. Write parser tests before implementation.
4. Implement the thin provider class and pure helpers.
5. Add or lock dependencies only when needed.
6. Run the local test matrix.
7. Bump the provider version.
8. Run `python3 -B -m sdk build-catalog`.
9. Stage the provider on a test Bazarr+ server and restart it.
10. Verify compat search, download, stream bytes, and logs.
11. Commit only provider files, fixtures, tests, catalog changes, and docs that belong to that provider.

This keeps each provider independently reviewable and prevents one weak scraper from blocking the whole batch.
