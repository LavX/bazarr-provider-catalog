# Gestdown Migration Notes

## Public Source

- Site or API base URL: `https://api.gestdown.info`
- Public docs URL, if any: none found during this migration slice.
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/gestdown.py`

## Required Behavior

- Media: episodes only.
- Languages: broad Addic7ed-style language set, including Portuguese and Serbian variants.
- Auth and settings: no login, token, cookie, or API key.
- Search inputs: TVDB series id, season number, episode number, requested subtitle language.
- Download flow: search returns a relative `downloadUri`; provider downloads it from the API host and returns subtitle bytes.
- Archive handling: none observed. The download endpoint returns subtitle text directly.
- Hash or FPS behavior: no hash matching. Hearing-impaired metadata is exposed by the API and must be preserved.
- Anti-bot or helper-service behavior: none observed. API may return HTTP 423, which the source provider retries before giving up.

## Clean-Room Boundary

- GPL source was used only for behavior inventory.
- Provider implementation is a new MIT rewrite using public HTTP/API behavior and captured fixtures.
- No GPL parser code, regex structure, comments, or tests were copied.

## Captured Fixtures

- `tests/fixtures/gestdown_show_breaking_bad.json`: `GET /shows/external/tvdb/81189`
- `tests/fixtures/gestdown_subtitles_breaking_bad_s01e01_english.json`: `GET /subtitles/get/<show_id>/1/1/English`
- `tests/fixtures/gestdown_download_breaking_bad_s01e01_english.srt`: `GET /subtitles/download/<subtitle_id>`
- `tests/fixtures/gestdown_video_breaking_bad_s01e01.json`: worker-shaped episode fixture for smoke tests.
