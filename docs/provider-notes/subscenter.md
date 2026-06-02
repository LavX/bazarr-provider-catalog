# SubsCenter provider notes

Historical notes for `subscenter`. The upstream does not resolve, so no active Provider Hub plugin is shipped from this branch.

## Public behavior

- Site: `http://www.subscenter.info/he/`
- Optional login endpoint: `subscenter/accounts/login/`
- Search endpoint shape:
  `subtitle/search/?q=<title>`
- Movie data endpoint shape:
  `cst/data/movie/sb/<url-title>/`
- Episode data endpoint shape:
  `cst/data/series/sb/<url-title>/<season>/<episode>/`
- Supported media: movies and episodes.
- Supported language: Hebrew, `heb` / `he`.
- The JSON subtitle data is nested by language, quality, and release group. Duplicate subtitle ids represent alternate release names for the same download.
- Downloads use `subtitle/download/<alpha2>/<subtitle-id>/?v=<h-version>&key=<key>` and return ZIP files.

## Compatibility quirks

- `username` and `password` are optional but must be supplied together.
- Login uses a `csrftoken` cookie as `csrfmiddlewaretoken` and succeeds on HTTP `302`.
- Hearing-impaired flags are carried from search results.
- ZIP downloads should ignore `.txt` files and reject ambiguous archives with more than one subtitle member.
- Non-ZIP download bodies are treated as daily-limit or invalid-download responses.
- Live verification on 2026-05-31 is blocked because the upstream does not resolve:
  - `www.subscenter.info` and `subscenter.info` failed local and escalated curl DNS resolution.
  - Public DNS checks through `1.1.1.1` and `8.8.8.8` returned no address records for the legacy hosts.
  - Do not require live smoke, Provider Hub compat proof, or core allow-list promotion unless the original site returns or a verified replacement origin is found.

## License notes

No implementation is promoted while the upstream remains unresolved. Behavior notes above describe public request and response contracts only.
