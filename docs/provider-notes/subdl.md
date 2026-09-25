# SubDL provider notes

Clean-room target for `subdl`.

## Public behavior

- Site: `https://subdl.com/`
- Search endpoint: `https://api.subdl.com/api/v1/subtitles`
- Translation endpoints: `https://api.subdl.com/api/v1/pro/translate/subtitles` and `https://api.subdl.com/api/v1/pro/translate/jobs/{request_id}`.
- Download host: `https://dl.subdl.com`
- Supported media: movies and episodes.
- Required setting: `api_key`.
- Optional settings: `anime_mode`, `ai_translate`, `include_ai_translated`, and `ai_translate_timeout_seconds`.
- API search supports text title, file name, SubDL id, IMDb id, TMDB id, season number, episode number, media type, release year, language codes, comments, releases, hearing-impaired metadata, full-season filters, and `unpack=1`.
- Downloads are zip archives or direct raw subtitle files when `unpack=1` returns a saved unpacked file URL.
- AI translation is off by default and requires a SubDL Plus or Pro account. SubDL translates one of its existing subtitles, consumes the account's translation quota when the job is not reused, and publishes the result as a regular subtitle on subdl.com.
- Search requests keep SubDL's Bazarr policy flag and also send the public API's documented `client=bazarr` integration identifier.

## AI translation behavior

- With `ai_translate` enabled, the provider reads SubDL's translation block from the primary search, or from the movie TMDB fallback when the empty primary search caused that fallback to run.
- It offers one virtual candidate for each requested language that SubDL reports missing. Forced targets are skipped. Hearing-impaired and non-hearing-impaired candidates use sources from the same class.
- Source selection uses release metadata inside each hearing-impaired class and preserves SubDL's order when sources match equally. A matching subtitle from the same episode suppresses the virtual candidate. A row from another episode does not.
- Already-published rows marked `ai_translated` by SubDL are hidden unless `include_ai_translated` or `ai_translate` is enabled. Returned virtual and published AI rows carry the top-level `ai_translated: true` flag.
- The translation request body contains only the SubDL subtitle id, target language, and episode coordinates. The API key is sent separately as the endpoint's authentication query parameter. The worker never sends subtitle content to the translation endpoint.
- A translation download submits once, polls a remembered request id, and returns an empty result on failure or timeout. Submitted jobs are remembered in memory for 24 hours so a later attempt polls instead of submitting again.
- An uncertain submit response suppresses another submit for the same job for 24 hours. This covers a timeout or dropped connection after send, a server error, and a successful response without a readable request id. DNS failures, refused connections, and recognised API error tokens are treated as definite failures.
- Job and uncertainty memory is bounded and process-local. A worker restart clears it. Finished translations remain discoverable as normal published rows.
- `ai_translate_timeout_seconds` is a select from 60 through 600 seconds, with 240 seconds as the default. Every request and retry stays inside that whole-operation budget.
- The provider reports a bounded `translation_quota` runtime event when SubDL supplies account status. Numeric `remaining` and `limit` values are reported only when the service actually returns them.
- When the search API advertises AI translation but omits its per-title translation block, the provider starts at most one `/api/v1/me` account-status probe, waits no more than 10 seconds, and caches the result for 15 minutes. A slow probe cannot block the worker or start another probe while it remains in flight. A strictly validated eligibility and quota response can still drive the account status event, but cannot create a virtual candidate without SubDL's per-title source ids and missing-language list.
- Account-status failures and unknown response shapes fail closed without logging response bodies, credentials, or account identifiers. They do not affect regular search results.
- Missing entitlement, exhausted quota, failed jobs, malformed responses, and other AI-path failures return no subtitle and do not throttle ordinary SubDL search or download traffic. A missing API key still fails as it does for every SubDL operation.

## Compatibility quirks

- Movie search prefers IMDb id when available. If the API reports an empty result and TMDB id exists, the provider retries with TMDB id only.
- Episode search prefers series IMDb id when available, otherwise series title.
- Anime mode adds absolute-episode search, season-only search, and title-only fallback.
- Matching season packs are considered for all episode searches. Pack season and episode ranges are checked before results are returned.
- Pack downloads choose the subtitle member matching the requested season and episode, then absolute episode.
- The bounded `bazarr_policy` fields returned by the API can cap primary search pagination at two pages and control season/title fallbacks, unpack requests, and AI translation candidates.
- Hearing-impaired and forced flags are inferred from API fields, comments, archive names, and release names, including stylized SDH markers.

## API verification notes

- SubDL's public API documentation, checked on 2026-09-24, documents HTTP 402 with `translation_not_entitled` and HTTP 429 with `translation_quota_exhausted` for translation submission.
- The same documentation says a completed identical translation can return `reused: true` without consuming quota.
- The public documentation does not define numeric remaining-quota fields or the cost of repeating a still-running request. Authenticated live acceptance is still required before recording either behavior as verified.
- A redacted live check on 2026-09-24 confirmed that `/api/v1/me` reports translation eligibility and quota under its `pro` object. The public documentation describes the endpoint's purpose but not this response shape, so parsing remains strict and fail-closed.
- A live no-key probe on 2026-05-29 returned HTTP 422 with a schema error body, confirming that real search proof requires a user SubDL API key.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API contracts only.
