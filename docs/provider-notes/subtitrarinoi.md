# Subtitrari Noi provider notes

Clean-room target for `subtitrarinoi`.

## Public behavior

- Site: `https://www.subtitrari-noi.ro/`
- Search endpoint shape: `POST /paginare_filme.php`
- Search parameters:
  - `search_q=1`
  - `tip=2`
  - `an=Toti anii`
  - `gen=Toate`
  - `cautare=<IMDb id without tt prefix or title>`
  - `query_q=<same value as cautare>`
- Supported media: movies and episodes.
- Supported language:
  - `ron`
- Requested hearing-impaired and forced variants are preserved in Provider Hub results, even though the site does not expose separate flags.
- Result rows use `div id="round"` with `content-main`, `content-right`, a `buton` download link, an IMDb link, and a following bold italic comments block.
- Downloads can be direct subtitle files or ZIP/RAR archives.

## Compatibility quirks

- Known title aliases:
  - `DC's Legends of Tomorrow` searches as `Legends of Tomorrow`.
  - `Marvel's Jessica Jones` searches as `Jessica Jones`.
- Episode matching keeps the legacy IMDb plus season behavior and also understands current plural season comments such as `Sezoanele 1-5 complete`.
- Archive selection prefers the requested season and episode, then release-info hints such as resolution, source, and release group.
- Current live rows may advertise archive links that return an unavailable plain-text message. The provider rejects those bodies instead of returning them as subtitles.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
