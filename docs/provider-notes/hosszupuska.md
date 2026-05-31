# Hosszupuska provider notes

Historical dead-origin notes for `hosszupuska`. The upstream is dead. This branch intentionally ships no active Provider Hub plugin, catalog entry, provider code, tests, or fixtures.

## Public behavior

- Site: `http://hosszupuskasub.com/`
- Search endpoint shape:
  `sorozatok.php?cim=<series>&evad=<season>&resz=<episode>&nyelvtipus=%25&x=24&y=8`
- Supported media: episodes only.
- Supported languages:
  - `hun` from `nyelv/1.gif`
  - `eng` from `nyelv/2.gif`
- Result rows are table rows with the hover background marker `css/over2.jpg` and the info marker `css/infooldal.png`.
- Search rows expose `SxxEyy`, series title, optional year, release/version text, language flag, and a download link with an `id` query parameter.
- Downloads are direct subtitle files or ZIP/RAR archives. Multi-file archives should choose the subtitle matching the requested season and episode, then prefer release tags from the result row.

## Compatibility quirks

- Known title aliases:
  - `Stargate Origins` searches as `Stargate: Origins`.
  - `Marvel's Agents of S.H.I.E.L.D.` searches as `Marvels Agents of S.H.I.E.L.D`.
  - `Mayans M.C.` searches as `Mayans MC`.
- Live verification on 2026-05-31 is blocked because the upstream is dead:
  - `http://hosszupuskasub.com/` returns an empty reply from the server.
  - Browser-like HTTP and HTTPS requests previously returned a ParkLogic JavaScript router page, not the HosszuPuska subtitle site.
  - The legacy `sorozatok.php` search path returns the same ParkLogic router page.
  - Fresh recheck after the dead-origin report returned `curl: (52) Empty reply from server` for `http://hosszupuskasub.com/`.
  - RDAP shows nameservers `NS1.PARKLOGIC.COM` and `NS2.PARKLOGIC.COM`, with `last changed` at `2026-05-25T23:28:30Z`.
  - Do not require live smoke, Provider Hub compat proof, or core allow-list promotion unless the original site returns or a verified replacement origin is found.

## License notes

No implementation is promoted while the upstream remains parked. Behavior notes above describe public request and markup contracts only.
