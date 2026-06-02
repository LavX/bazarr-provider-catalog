# Zimuku Provider Notes

## Status

- Catalog provider id: `zimuku`
- Legacy source reviewed: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/zimuku.py`
- Media type: movies and episodes
- Languages: English `eng`, Chinese `zho`, Simplified Chinese `zho-CN`, Traditional Chinese `zho-TW`
- Origin checked on 2026-05-31: `https://srtku.com`

## Preserved Behavior

- Searches `https://srtku.com/search?q=<query>`.
- Uses episode season query suffixes such as `.S01`.
- Parses non-shooter result pages from `div.item` rows.
- Filters episode search results by Chinese season markers such as `第一季`.
- Preserves Zimuku's season-year behavior, where an episode result year can be the season year rather than the show year.
- Parses subtitle rows from `tbody tr` blocks.
- Expands rows with Simplified and Traditional Chinese flags into separate candidates.
- Supports English candidates when the row or filename advertises English.
- Follows detail pages through `a#down1` and final `a[rel=nofollow]` download links.
- Supports direct subtitle bodies, ZIP archives, and RAR archives through the bundled `py7zz` dependency.
- Preserves archive-file preference for Simplified, Traditional, and bilingual Chinese subtitle names.

## Yunsuo Verification

The live origin currently presents a Yunsuo verification page before search. The plugin first tries a native bundled-template solver for the embedded BMP image challenge. If the challenge has no embedded image, it preserves the legacy behavior and submits browser-like click coordinates. A helper endpoint remains available for image samples the native solver cannot decode.

- `captcha_response`: one-use manual verification text for the next challenged request.
- `captcha_solver_url`: optional helper endpoint that receives `image_b64`, `image_mime`, `provider`, and `type`.
- `captcha_solver_token`: optional bearer token for that helper endpoint.
- `captcha_solver_timeout_ms`: timeout for the helper endpoint.
