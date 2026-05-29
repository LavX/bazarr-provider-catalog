# Titrari provider notes

Clean-room target for `titrari`.

## Public behavior

- Site: `https://www.titrari.ro/`
- Search discovers the advanced-search `page` parameter from the home page, with `numaicautamcaneiesepenas` as fallback.
- Search endpoint shape: `index.php?page=<advanced>&z7=<title>&z2=&z5=<imdb>&z3=-1&z4=-1&z8=<language>&z9=All&z11=<type>&z6=0`.
- Supported media: movies and episodes.
- Supported languages: Romanian and English. Forced and hearing-impaired variants are preserved in the Provider Hub language payload because legacy Bazarr advertised those variants.
- Search results expose subtitle id, title, year, season, episode hints, language, IMDb id, comments, translator, uploader, download count, page URL, and download URL.
- Downloads may be direct subtitle files, ZIP archives, or RAR archives.

## Compatibility quirks

- The site throttles by IP and user-agent, so the provider uses the legacy desktop Chrome-style user-agent.
- Episode searches prefer IMDb ids. When no IMDb id is available, the query falls back to the series title.
- Episode rows can describe a full season pack. Pack matching accepts explicit episode ranges and otherwise treats a season pack as covering the requested episode.
- Episode archives must contain the requested season and episode when structured `SxxEyy` or `SSxEE` names are present. Wrong-season or wrong-episode archive members are rejected instead of returning unrelated subtitle content.
- RAR extraction uses bundled `py7zz` first, with `unar` or `7z` as local fallbacks.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
