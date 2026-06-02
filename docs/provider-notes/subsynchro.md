# SubSynchro provider notes

Clean-room target for `subsynchro`.

## Public behavior

- Site: `https://www.subsynchro.com/`
- Current search endpoint shape: `POST /tous-les-films.html`
- Current search parameter:
  - `q=<movie title>`
- Legacy fallback endpoint shape: `GET /include/ajax/subMarin.php?title=<movie title>&year=<movie year>`
- Supported media: movies only.
- Supported languages:
  - `fra`
- Current film pages expose release rows as `tr` elements with `id="release_<id>"`, `fichier_<count>`, and `data-format`.
- Current release pages expose subtitle files as `article id="fichier_<id>"` with a `telecharger` download link.
- Downloads are ZIP archives containing subtitle files plus optional NFO, image, and metadata files.

## Compatibility quirks

- The legacy AJAX endpoint is kept as a fallback because older Bazarr builds queried it directly.
- The provider ignores hidden archive members and non-subtitle files before selecting the subtitle payload.
- Line endings are normalized after extraction.
- The site exposes French subtitles only, so hearing-impaired and forced variants are not verifiable.
- Live verification on 2026-05-29 is blocked by upstream responsiveness:
  - `POST /tous-les-films.html` redirects to the expected film page, then times out with zero bytes received.
  - Direct homepage, film page, and download URL probes also timed out with zero bytes received.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
