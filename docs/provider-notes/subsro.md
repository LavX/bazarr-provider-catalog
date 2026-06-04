# Subs.ro Provider Notes

## Upstream

- Site: https://subs.ro
- API docs: https://subs.ro/api
- API base URL: `https://api.subs.ro/v1.0`
- Authentication: `X-Subs-Api-Key` header

## Legacy Parity

- Supports movie and episode searches by IMDb ID.
- Supports Romanian and English subtitles, matching the legacy Bazarr provider language set.
- Accepts comma-separated API keys and rotates to the next key when the API returns HTTP 429 or JSON status 429.
- Uses the current documented IMDb form first, for example `tt1375666`, then falls back to the legacy numeric form, for example `1375666`, if the first search returns no items.
- Downloads direct subtitle files, ZIP archives, and RAR archives. RAR extraction prefers bundled `py7zz` and keeps system `unar` or `7z` as runtime fallbacks.

## Live Checks

- A no-key probe to `https://api.subs.ro/v1.0/search/imdbid/tt22202452` returns authentication failure, which confirms the v1.0 API host is live and requires a key.
- A fake-key probe returns an invalid-key response. Full search and download proof requires a real Subs.ro API key.
