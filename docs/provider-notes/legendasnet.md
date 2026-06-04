# Legendas.net provider notes

Clean-room target for `legendasnet`.

## Public behavior

- Site: `https://legendas.net/`
- API base: `https://legendas.net/api/v1/`
- Login endpoint: `POST /login` with JSON `email` and `password`.
- Search endpoints:
  - `GET /search/movie` with JSON body containing `name`, `page`, `per_page`, and `imdb_id`.
  - `GET /search/tv` with JSON body containing `name`, `page`, `per_page`, `tv_season`, `tv_episode`, and `imdb_id`.
- Supported media: movies and episodes.
- Supported language: `por-BR`.
- Requires username and password.
- Forced subtitles are detected when the item comment contains `forced` or `foreign`.
- Downloads use item `path`, joined to `https://legendas.net/`, and may be direct subtitle files or ZIP archives.

## Compatibility quirks

- API 429 means throttling or daily download limit, depending on the operation.
- API 401 and 403 mean invalid credentials or token.
- Search payloads can return either `success: false` or `status: false`; both mean no usable results.
- ZIP download handling follows the legacy provider behavior by using the first subtitle file in the archive.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API request and response contracts only.
