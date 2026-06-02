# Nekur provider notes

Clean-room target for `nekur`.

## Public behavior

- Site: `https://subtitri.nekur.net/`
- Search endpoint: `https://subtitri.nekur.net/modules/Subtitles.php`
- Search request shape: POST form with `ajax=1` and `sSearch=<movie title>`.
- Supported media: movies only.
- Supported language: Latvian, catalog alpha3 `lav`, alpha2 `lv`.
- Result rows are emitted under `tbody > tr` with:
  - `.title > a` containing the movie title, year span, and relative download URL.
  - `.fps` containing frame rate when known.
  - IMDb link in the fourth cell.
  - Notes in the final notes cell.
- Downloads are direct subtitle files or ZIP/RAR archives.

## Current live quirks

- On 2026-05-29, HTTP redirects to HTTPS.
- The HTTPS search endpoint returned HTTP `500`, but the body still contained a valid result table for `Dune`.
- The `Dune: Part One` download endpoint returned HTTP `200`, `Content-Disposition: filename=dune_part_one_2021.zip`, and a ZIP body.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
