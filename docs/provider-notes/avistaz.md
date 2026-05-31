# AvistaZ Provider Notes

## Upstream

- Site: https://avistaz.to
- Cookie validation path: `GET https://avistaz.to/rules`
- Search source: AvistaZ release page URL from Sonarr or Radarr history, exposed as `video.info_url`.
- Authentication: AvistaZ browser session cookies, optionally paired with the same browser User-Agent.

## Legacy Parity

- Supports movie and episode release pages when `video.info_url` points at `https://avistaz.to/`.
- Returns no results when the video was not grabbed from AvistaZ, matching the legacy provider's release-page-only behavior.
- Validates cookies against `/rules` without following redirects before parsing a release page.
- Parses the release title and nested subtitles table.
- Filters results by requested language and preserves the broad AvistaZ language list used by Bazarr.
- Treats each release-page subtitle as a hash-quality match because the subtitle is attached to the exact grabbed release page.
- Downloads direct subtitle files and ZIP archives.
- Supports RAR downloads through bundled `py7zz`, with `unar` or `7z` as runtime fallbacks.

## Live Checks

- A no-cookie probe to `https://avistaz.to/` returned HTTP 200 with the public AvistaZ landing page.
- A no-cookie probe to `https://avistaz.to/rules` returned HTTP 302 to `/auth/login`, confirming the cookie validation path.
- Full release-page search and download proof requires valid AvistaZ session cookies from the test server.
