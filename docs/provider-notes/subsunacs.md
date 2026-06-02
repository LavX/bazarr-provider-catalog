# SubsUnacs provider notes

Clean-room target for `subsunacs`.

## Public behavior

- Site: `https://subsunacs.net/`
- Search endpoint shape: `POST /search.php`
- Search parameters:
  - `m=<title>`
  - `l=0` for Bulgarian, `1` for English
  - `y=<movie year>` for movies
  - `imdbcheck=1`
  - remaining public search fields are posted empty
- Supported media: movies and episodes.
- Supported languages:
  - `bul`
  - `eng`
- Episode searches use `<series> <season:02d> <episode:02d>`.
- Search rows use `td.tdMovie` and subtitle detail links under `/subtitles/.../`.
- Current public detail pages expose direct subtitle entries through `/getentry.php?id=<id>&ei=<index>`.
- ZIP, RAR, and 7Z archives are still supported for compatibility with legacy download responses.

## Compatibility quirks

- Search title normalization strips diacritics and apostrophes.
- Legacy TV aliases are preserved for Marvel, DC, Doctor Who, Star Trek, and Superman and Lois titles.
- Legacy movie aliases are preserved for Back to the Future Part II, Back to the Future Part III, and Bill & Ted Face the Music.
- Site readme `.txt` files are filtered while real `.txt` subtitle files remain supported.
- Archive limits match the legacy safety bounds: `256` files, `64` subtitle candidates, and `100 MiB` per extracted file.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
