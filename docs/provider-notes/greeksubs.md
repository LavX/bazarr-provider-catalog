# GreekSubs Provider Notes

GreekSubs is a Greek-only movie and episode subtitle source.

## Behavior Preserved

- Looks up movie pages by movie IMDb id at `/en/view/<ttid>`.
- Looks up episode pages through the series IMDb page and the matching Season/Episode link.
- Extracts `secCode` and one-use subtitle ids from the page before building `/dll/<subtitle_id>/0/<secCode>` download URLs.
- A `secCode` is single-use and bound to the `PHPSESSID` session that loaded the page. A valid one makes the `/dll/` GET serve the subtitle file itself (`Content-Disposition: attachment`); a used or foreign-session one gets a 302 to the home page. The gate GET does not follow redirects, so that refusal is recognized.
- The older gate page, whose hidden form fields had to be POSTed back for the bytes, is still handled if GreekSubs serves it.
- When the token is refused (a cached search result, an earlier download, another worker's session), reloads only the stored GreekSubs detail page in the current session, confirms the same subtitle id is still listed, then retries once with the new token.
- Reports non-UTF-8 subtitles as `windows-1253` (or `iso-8859-7`), not `latin-1`, so Greek text decodes correctly.
- Preserves uploader, download count, release info, and movie/episode match signals in Provider Hub result payloads.

## Validation Targets

- Fixture tests cover movie parsing, episode page selection, language filtering, direct and legacy-form downloads, and bounded refresh of a refused token against the matching detail-page row.
- Live smoke should use Greek language (`ell`) and a video with a current IMDb id on greeksubs.net.
