# Subclub provider notes

Clean-room target for `subclub`.

## Public behavior

- Site: `https://www.subclub.eu/`
- Supported media: movies and episodes.
- Supported language: `est`.
- Search reads `https://www.subclub.eu/jutud.php?otsing=<title>`.
- Search result rows link to `down.php?id=<archive-id>` and can include IMDb links, FPS, ratings, and uploader text.
- Episode rows include labels in the title link such as `Game of Thrones (2011) [1x1]`.
- Direct file listing reads `https://www.subclub.eu/subtitles_archivecontent.php?id=<archive-id>`.
- Listing rows link to `down.php?id=<archive-id>&filename=<base64-name>`.
- If the listing endpoint is empty, the whole archive can be downloaded from `down.php?id=<archive-id>` and extracted.

## Current live notes

- On 2026-05-29 the public site served normal HTML.
- `Inception` returned archive id `10100`, IMDb id `tt1375666`, FPS `23.976`, and direct `.srt` file links.
- `Game of Thrones` S01E01 returned archive id `11232`, IMDb id `tt0944947`, and direct `.srt` file links including `CTU`.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
