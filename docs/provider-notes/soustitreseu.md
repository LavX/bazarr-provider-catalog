# Soustitreseu provider notes

Clean-room target for `soustitreseu`.

## Public behavior

- Site: `https://www.sous-titres.eu/`
- Supported media: movies and episodes.
- Supported languages:
  - `fra` from French archive rows and `VF` subtitle filenames
  - `eng` from `EN`, `ENFR`, and `VO` archive or subtitle filenames
- Search reads `https://www.sous-titres.eu/search.html?q=<title>`.
- Search result rows use `li.film` and `li.serie`, with detail links under `h3 > a`.
- Detail pages expose archive links as `a.subList`.
- Episode archive rows use labels such as `1×01` or `S1`.
- Archive links are relative to the detail page, for example `series/download/...` and `films/download/...`.
- Downloads are ZIP or RAR archives. ZIP is common in live probes.

## Current live notes

- On 2026-05-29 the public site served normal HTML through Cloudflare.
- `Game Of Thrones` is listed as `/series/game_of_thrones.html`.
- `Game Of Thrones` S01E01 has archive `Game.Of.Thrones.1x01.ENFR.FBK.zip`.
- That live S01E01 ZIP contains French `VF` and English/original `VO` subtitle files.
- `Dune: Part One (2021)` is listed as `/films/dune_part_one.html`.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
