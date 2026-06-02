# Subtitri.id provider notes

Clean-room target for `subtitriid`.

## Public behavior

- Site: `https://subtitri.do.am/`
- Search endpoint shape: `GET /search/?q=<title>`
- Supported media: movies only.
- Supported language:
  - `lav` / `lv`
  - The legacy provider also accepted `lva` with `LV`; this catalog provider accepts `lva` requests and normalizes returned payloads to `lav`.
- Search uses the movie title plus every alternative title.
- Search rows use `table.eBlock` with `.eTitle > a` links to detail pages.
- Detail pages expose the localized and original titles in `.main-header`, the year in `#film-page-year`, IMDb links, download count metadata, and an `.hvr` download link.
- Downloads can be direct subtitle files or ZIP/RAR archives.

## Compatibility quirks

- The site uses old uCoz pages and includes large unrelated sidebars and scripts. The parser anchors on the search result and detail metadata rather than page-wide text.
- The legacy provider queried all movie titles, not only the first title that produced results. The catalog provider preserves that behavior and de-duplicates by entry id and requested variant.
- Requested hearing-impaired and forced variants are preserved in Provider Hub results, even though the site does not expose separate flags.
- Direct subtitle downloads are accepted only when the filename extension or content looks like a subtitle. ZIP and RAR archives are unpacked and the first supported subtitle file is returned.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
