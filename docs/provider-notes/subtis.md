# Subtis Migration Notes

## Public Source

- Site or API base URL: `https://api.subt.is/v1`
- Public docs URL, if any: none found in the source inventory.
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subtis.py`

## Required Behavior

- Media: movie only.
- Languages: Spanish (`spa` / `es`).
- Auth and settings: none.
- Search inputs: cascade through file hash, byte size, filename, then alternative filename lookup.
- Download flow: direct subtitle URL returned by the API.
- Archive handling: none.
- Hash or FPS behavior: hash lookup uses the OpenSubtitles-style movie hash when available. Filename and alternative lookups are weaker matches.
- Anti-bot or helper-service behavior: none observed. The live API is behind Cloudflare but responded to normal HTTP client requests.

## Clean-Room Boundary

- GPL source was used only for behavior inventory.
- Provider implementation is a new MIT rewrite using public HTTP/API behavior and captured fixtures.
- No GPL parser code, regex structure, comments, or tests were copied.
