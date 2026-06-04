# Ktuvit provider notes

Clean-room target for `ktuvit`.

## Public behavior

- Site: `https://www.ktuvit.me/`
- Supported media: movies and episodes.
- Supported language: Hebrew only.
- Login endpoint: `Services/MembershipService.svc/Login`.
- Search endpoint: `Services/ContentProvider.svc/SearchPage_search`.
- Movie subtitles are parsed from `MovieInfo.aspx?ID=<ktuvit-id>`.
- Episode subtitles are parsed from `Services/GetModuleAjax.ashx?moduleName=SubtitlesList&SeriesID=<id>&Season=<season>&Episode=<episode>`.
- Downloads first request a `DownloadIdentifier` from `Services/ContentProvider.svc/RequestSubtitleDownload`, then fetch `Services/DownloadFile.ashx?DownloadIdentifier=<id>`.

## Compatibility quirks

- Ktuvit expects the password value stored by Bazarr as `hashed_password`, not a plain text password.
- Ktuvit service JSON wraps the useful payload in a `d` field containing a JSON string.
- If the video does not have an IMDb id, the provider falls back to TMDB lookup to resolve the IMDb id before searching Ktuvit.
- Live verification on 2026-05-31 found the homepage reachable and the login service returning HTTP `405` for GET, confirming the login endpoint is POST-only.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and response contracts only.
