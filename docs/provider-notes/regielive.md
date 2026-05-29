# RegieLive Migration Notes

## Public Source

- Site or API base URL: `https://api.regielive.ro/bazarr/search.php`
- Subtitle download origin: `https://subtitrari.regielive.ro`
- Public docs URL, if any: none found in the repository or current public site.
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/regielive.py`

## Required Behavior

- Media: movies and episodes.
- Languages: Romanian only, `ron` / `ro`.
- Auth and settings: no user credentials. Requests must send the public RegieLive Bazarr API header value required by the endpoint.
- Search inputs: movie searches send title and year. Episode searches send series title, season number, episode number, and year when available.
- Download flow: call the RegieLive subtitle host first to obtain session cookies, then download the subtitle ZIP from the result URL.
- Archive handling: extract the first non-hidden subtitle file from a ZIP archive. Ignore `.txt` files because they may contain notes rather than subtitles.
- Hash or FPS behavior: no provider hash verification and no FPS filtering.
- Anti-bot or helper-service behavior: the live API can return 403 from some egress networks. Treat non-200 API responses as provider errors rather than silently returning fabricated results.

## Clean-Room Boundary

- GPL source was used only for behavior inventory.
- Provider implementation is a new MIT rewrite using public HTTP/API behavior and captured fixtures.
- No GPL parser code, regex structure, comments, or tests were copied.
