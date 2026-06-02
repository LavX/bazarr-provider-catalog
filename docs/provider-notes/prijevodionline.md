# PrijevodiOnline provider notes

Clean-room target for `prijevodionline`.

## Public behavior

- Site: `https://www.prijevodi-online.org/`
- Supported media: episodes only.
- Supported languages:
  - `hrv` from download URL suffix `-hr`
  - `srp` from download URL suffix `-sr`
  - `mne` from download URL suffix `-cg`
  - `hbs` as a broad Serbo-Croatian request mode
- Series lookup reads the alphabetical index page:
  `https://www.prijevodi-online.org/serije/index/<first-letter>`.
- Series rows have IDs like `serija-935` and links like `/serije/view/935/game-of-thrones`.
- Episode lookup reads the series page and extracts:
  - optional AJAX key from `epizode.key = '<32-char-hex>'`
  - season headings like `h3#sezona-1`
  - episode containers like `div#epizoda-33945`
  - episode number from `li.broj`
- Subtitle list request is an AJAX POST to `/prijevod/get/<episode-id>` with form field `key`.
- Subtitle rows have IDs like `prijevod-18050`, a download link under `td.naziv`, status text, and a following `prijevod-opis-<id>` row with release notes.
- Downloads are ZIP or RAR archives.

## Current live notes

- On 2026-05-29 the public site served normal HTML through Cloudflare.
- `Game of Thrones` is listed as `/serije/view/935/game-of-thrones`.
- `Game of Thrones` S01E01 maps to episode id `33945`.
- The live subtitle list returned Croatian and Serbian RAR download rows for S01E01.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
