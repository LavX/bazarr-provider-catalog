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

## Origin status: offline since August 2026

The site is down and the cause is not technical on our side. The operators announced on
2026-08-21 that their host suspended the site after repeated copyright complaints about
translations hosted there. Their follow-up says the complete site was backed up with no
translations lost, and that they are looking for new hosting less likely to hit the same
problem. No replacement address and no timeline have been announced.

Sources, both from the operators' own page:

- <https://www.facebook.com/prijevodi.online/posts/pfbid02pAELJ6mT71mmvbCp67CBZ1d1MPVgH7dgScqiXYnC7gzuE5FJX69pNjyi3ncivLA7l>
- <https://www.facebook.com/prijevodi.online/posts/pfbid02m5ZabJP4TJ9tFrTyTJKpmsnWTnVCsohvHJ8BkArQo86DcyPM3oKC3wzRho1LFArql>

Measured on 2026-08-31:

- `https://prijevodi-online.org/` returns `301` to `https://www.prijevodi-online.org/`.
- `https://www.prijevodi-online.org/` returns `401` with the origin's own page,
  "Proper authorization is required to access this resource!", to a plain client and to a
  real Chrome User-Agent alike.
- `https://www.prijevodi-online.org/serije/index/A`, the first URL any search reads,
  returns `500` with the origin's own page, "An internal server error has occured.",
  confirmed through a real Chrome via FlareSolverr.
- FlareSolverr reports `Challenge not detected!` and is issued zero clearance cookies, so
  no Cloudflare challenge is in play. Cloudflare is passing origin errors straight through.

Consequences for this plugin:

- The v0.2.0 Cloudflare challenge handling cannot be exercised against the live site until
  the site returns. Do not require live smoke or a production-channel e2e until then.
- Behavior against the dead origin was checked and is correct: `500` is retryable but
  bounded (3 attempts, 0.5s then 1.0s backoff), so a search fails honestly in about three
  seconds and never consumes the worker-deadline budget.
- `BASE_URL` is fixed to `https://www.prijevodi-online.org`. If the site returns on a new
  domain, the plugin needs a URL change on top of the challenge handling. If it returns on
  the same domain behind Cloudflare, v0.2.0 is what is needed and no change is due.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
