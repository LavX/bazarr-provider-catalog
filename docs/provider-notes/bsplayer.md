# BSPlayer Provider Notes

- Legacy source: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/bsplayer.py`.
- Legacy behavior: the SOAP implementation exists, but `list_subtitles()` is disabled and always returns an empty list.
- Current upstream status checked on 2026-05-31: BSPlayer SOAP login endpoints on `s1` through `s109` responded with HTTP 200.
- Catalog behavior: restores the usable SOAP API path as a hash-only provider. A video hash and file size are required.
- Download flow: BSPlayer returns gzip-compressed subtitle content from `subDownloadLink`.
