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

- Cloudflare bypass order mirrors `opensubtitles_org`: use `ai-cloudscraper` first, solve inline Anubis `/.within.website/` challenges when present, and use optional FlareSolverr `/v1` only if the native cloudscraper session still receives a Cloudflare challenge. Matching browser cookies and User-Agent remain optional manual fallback inputs.
- The default User-Agent should stay browser-like. A caller-supplied User-Agent should remain paired with any supplied cookies.
- A HTTP 403 Cloudflare challenge should raise a clear access error instead of returning empty results when no working cookies or FlareSolverr fallback are configured.
- Official FlareSolverr 3.5.0 still timed out at `30000` ms on 2026-06-02, but solved Cloudflare challenges in about `29200` to `33400` ms with a `60000` ms request. The provider default and cap are therefore `60000` ms.
- Search results should preserve IMDb, season, episode, release group, hearing-impaired, uploader, and season-pack match behavior.
- Season-pack archives must not fall back to a wrong episode member.
- Live verification on 2026-06-02 from this environment reached the official FlareSolverr solved path, then Cloudflare returned a `522` origin timeout page for both the homepage retry and direct IMDb search probe.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
