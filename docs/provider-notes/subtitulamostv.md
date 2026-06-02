# SubtitulamosTV Migration Notes

## Public Source

- Site or API base URL: `https://www.subtitulamos.tv`
- Public docs URL, if any: none found.
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/subtitulamostv.py`
- Upstream public behavior inspected from the installed `subliminal.providers.subtitulamos` package to identify endpoints and supported labels.

## Required Behavior

- Media: episode only.
- Languages: Catalan, English, Galician, Portuguese, Brazilian Portuguese, Spanish, and Spanish Latin American country variants supported by the site label model.
- Auth and settings: no account, token, or provider-specific settings.
- Search inputs: series title, optional year, season number, episode number, and requested languages.
- Search flow: query `/search/query?q=<series (year)>`, fall back to `/search/query?q=<series>` when the year query has no exact show hit, fetch `/shows/<show_id>`, select the requested season and episode page, then parse available download rows.
- Result filtering: exact normalized show-name match only, with trailing ` (YYYY)` stripped from the search title.
- Download flow: direct subtitle file download from the row link using the episode page as referer.
- Archive handling: none, SubtitulamosTV download links return subtitle content directly.
- Hash or FPS behavior: no hash verification and no FPS-specific behavior.
- Anti-bot or helper-service behavior: none identified.

## Clean-Room Boundary

- GPL source was used only for behavior inventory.
- Provider implementation is a new MIT rewrite using public HTTP/API behavior and captured fixtures.
- No GPL parser code, regex structure, comments, or tests were copied.
