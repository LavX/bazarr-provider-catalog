# Pipocas.tv provider notes

Clean-room target for `pipocas`.

## Public behavior

- Site: `https://pipocas.tv`
- Login endpoint: `/login` with `username`, `password`, and a CSRF `_token` from the login page meta tag.
- Search endpoint shape:
  `/legendas?t=rel&l=<site-language>&page=1&s=<query>`
- Supported media: movies and episodes.
- Supported languages:
  - `por` maps to `portugues`
  - `por-BR` maps to `brasileiro`
  - `eng` maps to `ingles`
  - `spa` maps to `espanhol`
- Search rows link to `/legendas/info/<id>` through anchors with `text-dark no-decoration`.
- Detail pages expose release text, download id, hit count, uploader, and rating.
- Downloads may be direct subtitle files, ZIP archives, or RAR archives. Episode archives should prefer the requested `SxxEyy` member.

## Compatibility quirks

- Credentials are required. HTML account pages containing `Cria uma conta` trigger one login refresh and one retry per request. HTTP 429 never triggers a retry.
- Requests and redirects must remain on `https://pipocas.tv` at port 443. Off-origin search links are skipped.
- Direct subtitles without filename extensions use a recognized content type or subtitle signature. Subtitle bytes are preserved for host decoding.
- Movie searches use the title. Episode searches use `Series SxxEyy` when season and episode are present.
- Result scoring combines release matches, site rating, and hit count.
- ZIP and RAR downloads are passed to the Bazarr+ host for safe extraction and episode selection.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
