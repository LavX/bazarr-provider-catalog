# Titlovi provider notes

Clean-room target for `titlovi`.

## Public behavior

- API base: `https://kodi.titlovi.com/api/subtitles`
- Login endpoint: `/gettoken` with `username`, `password`, and `json=true`.
- Search endpoint: `/search` with `query`, pipe-separated `lang`, `token`, `userid`, `json=true`, optional `season`, optional `imdbID`, and optional `pg`.
- Supported media: movies and episodes.
- Supported languages: Bosnian, English, Croatian, Macedonian, Slovenian, and Serbian.
- Titlovi language labels:
  - `bos` maps to `Bosanski`
  - `eng` maps to `English`
  - `hrv` maps to `Hrvatski`
  - `mkd` maps to `Makedonski`
  - `slv` maps to `Slovenski`
  - `srp` maps to `Srpski`
  - `srp` with script `Cyrl` maps to `Cirilica`
- Search follows up to three result pages.
- Episode searches send season only and filter the requested episode locally. API episode `0` is treated as a season pack.
- Downloads may be direct subtitle files, ZIP archives, or RAR archives.

## Compatibility quirks

- Credentials are required.
- HTTP `429` is treated as a provider rate limit.
- Non-rate-limit search errors return any already fetched page results, or no results if the first page fails.
- Titles with `AKA` expose an alternate title for matching.
- Episode packs select subtitle files by `SSxEE` or `SxxEyy` in archive member names.
- Serbian bundles select Latin by default and Cyrillic when the language payload asks for script `Cyrl`.
- RAR extraction uses bundled `py7zz` first, with `unar` or `7z` as local fallbacks.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API contracts only.
