# SubsSabBz provider notes

Clean-room target for `subssabbz`.

## Public behavior

- Site: `http://subs.sab.bz/`
- Search endpoint shape: `POST /index.php?`
- Search parameters:
  - `act=search`
  - `movie=<title>`
  - `select-language=1` for English, `2` for Bulgarian
  - `yr=<movie year>` for movies
  - `upldr=` and `release=` empty
- Supported media: movies and episodes.
- Supported languages:
  - `bul` from Bulgarian rows
  - `eng` from English rows
- Result rows use `tr.subs-row` with a download link containing `act=download` and `attach_id`.
- Search rows expose title, optional year, subtitle language, disc count, FPS, uploader, IMDb ID, and notes from the row tooltip.
- Downloads are ZIP or RAR archives. The plugin expands `.srt` and `.sub` files and keeps one Provider Hub result per subtitle file.

## Compatibility quirks

- Search title normalization strips diacritics and apostrophes.
- TV aliases preserve the legacy search behavior for Marvel, DC, Doctor Who, Star Trek, and Superman and Lois titles.
- The legacy Back to the Future movie alias is preserved.
- Temporary 403 responses are retried before the provider gives up.
- Requested hearing-impaired and forced language variants are preserved in returned results, even though the site does not expose separate filters for them.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
