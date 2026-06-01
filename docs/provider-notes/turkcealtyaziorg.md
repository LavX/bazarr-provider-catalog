# TurkceAltyazi.org provider notes

Clean-room target for `turkcealtyaziorg`.

## Public behavior

- Site: `https://turkcealtyazi.org`
- Search endpoint shape: `find.php?cat=sub&find=<numeric-imdb-id>`.
- Supported media: movies and episodes.
- Supported languages:
  - `tur` from `flagtr`
  - `eng` from `flagen`
- Movie searches use the movie IMDb id. Episode searches use the series IMDb id.
- Episode rows expose season and episode fields. Non-numeric episode values are treated as season packs for the requested episode.
- Downloads require the subtitle page hidden form fields `idid`, `altid`, and `sidid`, then post to `/ind`.
- Downloads can return direct subtitle bytes, ZIP archives, or RAR archives.

## Compatibility quirks

- Cloudflare can block the site without matching browser cookies and User-Agent. The provider now uses `ai-cloudscraper` by default, solves inline Anubis `/.within.website/` challenges, keeps `cookies` optional and secret for manual `cf_clearance`, and can use an optional FlareSolverr `/v1` URL if the native session still receives a Cloudflare challenge.
- The default User-Agent should stay browser-like. A caller-supplied User-Agent should remain paired with any supplied cookies.
- A HTTP 403 Cloudflare challenge should raise a clear access error instead of returning empty results when no working cookies or FlareSolverr fallback are configured.
- FlareSolverr timeouts are capped at `25000` ms to stay inside the Provider Hub worker deadline.
- Search results should preserve IMDb, season, episode, release group, hearing-impaired, uploader, and season-pack match behavior.
- Season-pack archives must not fall back to a wrong episode member.
- Live verification on 2026-05-31 from this environment returned a Cloudflare challenge for the homepage and IMDb search without cookies.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
