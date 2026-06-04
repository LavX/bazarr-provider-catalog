# Napisy24 provider notes

Clean-room target for `napisy24`.

## Public behavior

- API endpoint: `http://napisy24.pl/run/CheckSubAgent.php`
- Supported media: movies and episodes.
- Supported language: `pol`.
- Required video data: file size, file basename, and `napisy24` hash.
- The `napisy24` hash is the same value Bazarr computes from the OpenSubtitles hash path.
- Config supports `username` and `password`. If either value is missing, the legacy default account is used.
- Search posts form data with `postAction=CheckSub`, credentials, `fs`, `fh`, `fn`, and `n24pref=1`.
- Response statuses:
  - `OK-0`: no subtitles.
  - `OK-1`: video info only, no subtitles.
  - `OK-2`: subtitle ZIP is attached after `||`.
  - `OK-3`: subtitle exists outside the Napisy24 database and is not downloaded.
  - `login error`: authentication failure.
- There is no separate upstream download request. The subtitle archive is returned by the search call and carried in the Provider Hub payload for `download()`.

## Compatibility quirks

- Search result matching is hash-first and can also match IMDb id from the response metadata.
- ZIP extraction follows the legacy provider behavior by choosing the first subtitle file in the archive.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API request and response contracts only.
