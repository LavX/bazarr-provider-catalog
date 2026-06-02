# GreekSubtitles Provider Notes

GreekSubtitles is a Greek and English movie and episode subtitle source.

## Public Source

- Site base URL: `https://gr.greek-subtitles.com`
- Download host: `https://www.greeksubtitles.info`
- Source behavior inspected from: `/home/lavx/bazarr/custom_libs/subliminal_patch/providers/greeksubtitles.py`

## Behavior Preserved

- Searches movies by title plus year, including alternative titles.
- Searches episodes by series plus `SxxEyy`, including alternative series names.
- Follows `Next >>` pagination links in search results.
- Preserves Greek and English result filtering from flag images.
- Builds download requests from subtitle page ids through `/getp.php?id=<id>`.
- Sends the subtitle page as the download referer.
- Supports direct subtitle bytes, ZIP archives, and RAR archives.
- Selects the first visible supported subtitle file and normalizes line endings.
- Uses `windows-1253` as the non-UTF-8 encoding fallback.

## Validation Targets

- Fixture tests cover movie rows, episode pagination, language filtering, ZIP extraction, RAR extraction, and raw subtitle downloads.
- Live smoke should use Greek language (`ell`) against both a movie and an episode.
- Provider Hub compat proof still depends on the built-in provider migration switch allowing `greeksubtitles` to shadow the shipped provider id.
