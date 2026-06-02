# Subs4Free provider notes

## Public behavior

- Site: `https://www.subs4free.info`
- Supported media: movies only.
- Supported languages:
  - Greek, `ell` / `el`
  - English, `eng` / `en`
- Search endpoint shape: `/search_report.php?search=<query>&searchType=1`
- Download endpoint shape: `/getSub.php`
- No credentials are exposed in the Bazarr UI.

## Compatibility quirks

- Bazarr labels this provider as broken for some users, so live checks must prove both search and download before promotion.
- Search may return direct `.movie-details` result cards or a `Mov_sel` dropdown of matching movie pages.
- Result language is carried by `elgif` and `engif` sprite classes, plus language-specific URL paths.
- Download detail pages expose a hidden `id` field and an image submit button. The worker posts that id with generated `x` and `y` click coordinates to `/getSub.php`.
- The legacy anti-block sequence touches `images.subs4free.info/favicon.ico` and the two `subs4series.com/includes/anti-block...` URLs before posting the download form.
- Downloads can be direct subtitle bytes, ZIP archives, or RAR archives.

## Live notes

Live verification on 2026-05-31 reached `https://www.subs4free.info/` through Cloudflare and returned HTTP `200`. Search for `Inception` returned current `movie-details` result cards with Greek subtitle rows, uploader names, download counts, and download detail links.

## License notes

The implementation in this catalog is a clean-room MIT worker using only public request and markup behavior.
