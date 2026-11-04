# UNIT3D Provider Notes

## Upstream

- Search: `GET /api/subtitles`, authenticated with `Authorization: Bearer <api key>`. The key needs the search permission.
- Download: `GET /api/subtitles/{id}/download`, with the same key. The key needs the download permission.
- Status: `GET /api/subtitles/status` reports the key's permissions. This provider does not call it.
- Parameters: `type` (`movie` or `episode`), `tmdb_id`, `imdb_id`, `tvdb_id` (episodes only), `title` with `year`, `season` and `episode` (episodes), `language` (up to 50 comma-separated alpha2 codes), `file_size` and `file_name` when file matching is on, `perPage` (50), and `page`.
- Matching: `meta.matched_by` names the strategy the tracker used (tmdb, tvdb, imdb, or title). Paging uses `meta.current_page` and `meta.last_page`.
- Records carry `release_match`, with `file_size` and `file_name` flags for the exact file. The API reports `hearing_impaired` and `forced` as `null`.
- Record fields the provider reads: `id`, `language`, `extension`, `filename`, `type`, `pack` (`null`, `season`, or `series`), `season`, `episode`, `release`, `release_match`, `uploader`, `downloads`, `torrent_id`, `imdb_id`, `tmdb_id`, and `tvdb_id`. The response `meta` carries `matched_by`, `current_page`, and `last_page`.
- Revision: the request parameters and record fields in this section follow the UNIT3D subtitle API contract. That contract may change, so check this section when the upstream API changes.
- Upstream rate limit: 30 requests per minute per user.
- A tracker may not serve this API. A 404 on the API path means the tracker does not provide the subtitle API.
- The provider is written from this documented contract. It does not copy upstream code.

## Endpoints And Keys

- `endpoints` is a whitespace-separated list of base URLs. `api_keys` holds one key per endpoint, in the same order. The two counts must match. Each key is printable ASCII without spaces.
- Each URL must use `http` or `https` and carry a host. Credentials, query strings, and fragments are refused. A trailing slash is removed and the base path is kept. The host and path must be ASCII.
- Duplicate normalized URLs are refused. The first 16 endpoints are used, and extra endpoints are logged and dropped.
- An endpoint's tag is the first 16 hex characters of the SHA-256 of its normalized URL. Candidate ids have the form `<tag>:<subtitle id>`, and download payloads carry the tag.
- `timeout_seconds` is the whole-search budget, from 10 to 60 seconds, with 30 as the default. Every endpoint searches in parallel inside that budget.
- `match_files` is on by default. It sends the video's file size and file name with each search. The tracker decides how to use them.

## Search And Download

- Languages go out in chunks of 50 base codes, in request order. The chunks of one endpoint run one after another. A regional request searches its base code: a Brazilian Portuguese request searches `pt`, and the candidate is labeled `por`. Forced requests are skipped, because the API does not report forced subtitles.
- Each request's timeout is the time left in the search budget. It bounds the connection and each read of the response headers. Reading each response body is bounded by the time left in the same budget. If the socket cannot be reached from the response, the budget is checked between reads only, so a read already waiting can run past it. A page that would start after the budget is spent is skipped and logged.
- A failed page or chunk keeps the candidates already received and marks the endpoint partial in its log line. An endpoint counts as failed only when none of its requests succeeded.
- A failed endpoint returns no results. Each endpoint with a failed request logs one line with the endpoint tag, the exception class, and the reason. A valid empty response is a success.
- A search raises for invalid settings, and when every endpoint failed. If they failed the same way, it raises that class: `APIThrottled` with the longest Retry-After, `AuthenticationError` for rejected keys, or `ConfigurationError` when the tracker has no subtitle API. Otherwise it raises `ServiceUnavailable`.
- In a search, a 422 response means the provider built the request wrongly. It is logged at error level and raised as `ConfigurationError`.
- A 429 response cools its endpoint down for the Retry-After interval. Without a usable Retry-After header, the cooldown is 60 seconds. The cooldown holds for later searches in the same worker, even when another endpoint succeeds. It applies to the endpoint and the API key it was earned with. The 429 also ends that endpoint's current search: its remaining chunks and pages are not requested, and the candidates already received are kept.
- A 429 that arrives while a longer cooldown is in force keeps that cooldown and reports the time left on it.
- A 401, 403 or 404 response also ends that endpoint's current search: its remaining chunks and pages are not requested, and the candidates already received are kept.
- Any other failed search in which the endpoint attempted requests and none succeeded cools its endpoint down for a provisional 60 seconds, so later searches in the same worker skip it. An endpoint that the search budget ends before its first request is not cooled down. The cooldown applies to the endpoint and the API key it was earned with, so a corrected or rotated key is queried in the next search. A search fails for an endpoint when none of its requests succeeded. A failure other than a 429, after a successful request, leaves the endpoint partial and starts no cooldown. When every endpoint is cooling, the search raises the class that the cooling failures share, or `ServiceUnavailable` when they differ.
- Redirects are refused on every authenticated request, so the key is never sent to another origin. A redirect counts as an endpoint failure in a search, and as a skipped candidate in a download.
- A candidate claims its hash only when the tracker flags both the file size and the file name of the exact file. That exact-release flag is reported as a hash match, with `hash_verifiable` true, and the host verifies the claim against series, season, episode and source. Its `release_info` is the torrent's release name.
- Hearing-impaired and forced flags are unknown for this API, so a candidate reports `hearing_impaired` as `null` rather than omitting it, and its language reports `hi` and `forced` as `null` rather than omitting them. `hearing_impaired_verifiable` is false.
- `page_link` is the configured base URL plus `/torrents/<torrent id>`. The response never supplies the link.
- A download goes to the endpoint named by its candidate's tag, and its body gets a 30-second budget. A 401 or 403 response raises `AuthenticationError`, and a 429 response raises `APIThrottled` and cools that endpoint down for later searches with the same API key. A download does not wait for a cooldown.
- Any other download failure skips the candidate. The provider returns an empty result and logs one line with the endpoint tag, the subtitle id, and a fixed reason, never a URL, key, or response body. Skips cover every other HTTP status (for example 404, 422, and 500), redirects, an unreachable endpoint, a failed TLS handshake, a TLS error while the body is read, a body over the 32 MiB limit or past its budget, a body shorter than its Content-Length, an empty body, a RAR archive, a JSON body, a web page, and an endpoint that is not configured.
- A ZIP archive is returned for the host to extract. Invalid endpoint and key settings still raise from a download.

## Known Gaps

- An HI-specific search does not reach the provider with its HI variant. The host intersects hearing-impaired requests with the manifest, and this manifest declares no hearing-impaired variants, so the gap depends on host support that this catalog does not contain. The matching test is an expected failure, and it stays in place until that support lands.
- A key that is valid but lacks the search or download permission is not diagnosed. A search reports it as an authentication failure, and a download raises an authentication error for 401 or 403. The status endpoint is not called.

## Live Checks

- Not run. A live tracker and a valid API key are required. Local mock-server tests cover the provider.
