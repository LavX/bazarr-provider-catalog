# Podnapisi provider notes

Historical dead-origin notes for `podnapisi`. The upstream is dead. This branch intentionally ships no active Provider Hub plugin, catalog entry, provider code, tests, or fixtures.

## Public behavior

- Site: `https://www.podnapisi.net/subtitles`
- Search endpoint shape:
  `subtitles/search/advanced?keywords=<title>&language=<alpha2>&movie_type=<movie|tv-series>&year=<year>`
- Supported media: movies and episodes.
- Supported filters:
  - `only_foreign`
  - `also_foreign`
  - `verify_ssl`
- Search returns JSON result pages. Results expose subtitle id, language, flags, rating, download count, uploader, release names, movie metadata, season and episode data, and a subtitle page URL.
- Downloads use the subtitle download URL with ZIP container support.

## Compatibility quirks

- HTTP 429 responses must raise a rate-limit error.
- Foreign and hearing-impaired flags are part of result filtering.
- Movie and episode matching should preserve title, year, series, season, episode, and release-name match keys when the upstream result provides them.
- Live verification on 2026-05-31 is blocked because the upstream is dead:
  - `https://www.podnapisi.net/subtitles` does not resolve.
  - `https://podnapisi.net/subtitles` does not resolve.
  - Fresh recheck after the dead-origin report still returned `curl: (6) Could not resolve host` for both hosts.
  - RDAP still lists `PODNAPISI.NET`, with `last changed` at `2026-05-20T13:24:04Z`, but the public hosts do not resolve.
  - Do not require live smoke, Provider Hub compat proof, or core allow-list promotion unless the original site returns or a verified replacement origin is found.

## License notes

No implementation is promoted while the upstream remains unresolved. Behavior notes above describe public request and response contracts only.
