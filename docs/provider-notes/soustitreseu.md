# Soustitreseu provider notes

Clean-room target for `soustitreseu`.

## Public behavior

- Site: `https://www.sous-titres.eu/`
- Supported media: movies and episodes.
- Supported languages:
  - `fra` from `FR`, `VF` and `ENFR` archive or subtitle filenames, and the French flag on an archive row
  - `eng` from `EN`, `VO` and `ENFR` archive or subtitle filenames, and the English flag on an archive row
- Archive rows and archive members read language tags with the same rules. `VO`, `VF`, `ENG`,
  `FRE` and `ENFR` count in any case and outrank the two-letter tags. A bare `EN` or `FR` counts
  in capitals anywhere in the name, or in any case as the last token before the extension,
  because lowercase `en` and `fr` are ordinary French words (`Asterix.en.Bretagne`).
- Member choice pins a member tagged with the requested language. When members are tagged but
  none with the requested language, it rejects the archive. It leaves the choice to Bazarr's
  episode picker only when no member is tagged or every member carries the requested language.
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
- On 2026-09-29 `The.Walking.Dead.1x01.ENFR.STAYIN.zip` held 18 members, nine tagged `.EN.` and
  nine `.FR.`, for example `The.Walking.Dead.101.CTU.EN.TAG.srt`. The other season 1 rows are
  French-only `.FR.` archives. Film archive names carry no language tag
  (`Inception.(2010).Z1.REFiNED.zip`), so their flags decide, and one film member ended in
  `-WiHD.Fr.srt`.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
