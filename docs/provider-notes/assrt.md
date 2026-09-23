# Assrt provider notes

Clean-room target for `assrt`.

## Public behavior

- Site/API: `https://api.assrt.net/v1`
- Quota endpoint: `GET /user/quota?token=<token>`
- Search endpoint: `GET /sub/search?token=<token>&q=<query>&is_file=1`
- Detail endpoint: `GET /sub/detail?token=<token>&id=<subtitle_id>`
- Supported media: movies and episodes.
- Supported languages: English, Chinese, Simplified Chinese, and Traditional Chinese.
- Requires an Assrt API token before quota, search, detail, or download URL discovery.
- Search results can expose several Assrt language codes from one subtitle item.
- Season-pack detail responses include a `filelist`; the provider narrows episode packs to the requested episode before choosing a language-specific file.
- Single-file detail responses can provide the final download URL directly on the subtitle entry.

## Live verification

- No-token, empty-token, and placeholder-token probes to `/user/quota` and `/sub/search` returned Assrt status `20001` with `invalid token`.
- Full SDK search and download proof requires a real Assrt API token.
- Community validation requested: a token holder should run live SDK smoke and Provider Hub compat search, download, and stream proof from a region that can resolve and fetch Assrt download URLs.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API request and response contracts only.
