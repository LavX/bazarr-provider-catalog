# Shooter Migration Notes

## Public Source

- Site or API base URL: `https://www.shooter.cn/api/subapi.php`
- Public docs URL, if any: none found in the source inventory.
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/shooter.py`
- Related language behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/converters/shooter.py`
- Related hash behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/hashes.py`

## Required Behavior

- Media: movie and episode.
- Languages: English (`eng`) and Chinese (`zho`), sent to Shooter as `eng` and `chn`.
- Auth and settings: none.
- Search inputs: Shooter file hash from `video.hashes.shooter`, video filename/path as `pathinfo`, requested language, and JSON response format.
- Download flow: direct subtitle download URL returned by Shooter API.
- Archive handling: none in the current behavior. The legacy provider downloads the returned link directly.
- Hash or FPS behavior: only exact Shooter hash matches are meaningful. The provider should not query without a Shooter hash.
- Anti-bot or helper-service behavior: none.

## Clean-Room Boundary

- GPL source was used only for behavior inventory.
- Provider implementation is a new MIT rewrite using public HTTP/API behavior and captured fixtures.
- No GPL parser code, regex structure, comments, or tests were copied.

## Live Smoke Status

- Local verification on 2026-05-31 reached `https://www.shooter.cn/api/subapi.php` with a synthetic Shooter hash.
- The API returned the expected single-byte `0xff` no-results response, which verifies route reachability and the no-results shape. Full result and download smoke still requires a real Shooter hash with live subtitles.
