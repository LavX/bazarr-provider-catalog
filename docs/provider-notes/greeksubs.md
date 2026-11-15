# GreekSubs.net Provider Notes

GreekSubs.net is a Greek-only movie and episode subtitle source. It is not the same site as greeksubs.pro. The provider id stays `greeksubs` because it replaces the Bazarr built-in provider of that name.

## Behavior Preserved

- Looks up movie pages by movie IMDb id at `/en/view/<ttid>`.
- Looks up episode pages through the series IMDb page and the matching Season/Episode link.
- Episode links carry the episode title in their slug, often non-ASCII and served garbled (`un-mal-dÃ­a`, `Ã‰pisode-1.1`). They are requested percent-encoded as UTF-8, as a browser does; urllib refuses a non-ASCII URL, which crashed the search for Vis a vis season 1, Élite and Babylon Berlin.
- Every result therefore matches the video's title or series, whatever the page calls it. The site can title a page in another language, such as French `Derrière les barreaux` for `Vis a vis`, which Sonarr calls `Locked Up`.
- GreekSubs.net answers `404` for a title or episode it does not carry. The search returns no results for it rather than failing, since the SDK counts a valid empty response as a success. Any other HTTP error still fails the search.
- Extracts `secCode` and one-use subtitle ids from the page before building `/dll/<subtitle_id>/0/<secCode>` download URLs.
- A `secCode` is single-use and bound to the `PHPSESSID` session that loaded the page. A valid one makes the `/dll/` GET serve the subtitle file itself (`Content-Disposition: attachment`); a used or foreign-session one gets a 302 to the home page. The gate GET does not follow redirects, so that refusal is recognized.
- The older gate page, whose hidden form fields had to be POSTed back for the bytes, is still handled if GreekSubs.net serves it. The POST does not follow redirects either: a redirect or an HTML page in reply is a refusal, which refreshes the token, rather than a subtitle.
- When the token is refused (a cached search result, an earlier download, another worker's session), reloads only the stored GreekSubs.net detail page in the current session, confirms the same subtitle id is still listed, then retries once with the new token.
- Reports non-UTF-8 subtitles as `windows-1253`, or `iso-8859-7` when `windows-1253` cannot decode them, not `latin-1`, so Greek text decodes correctly. In a live sample of 58 GreekSubs.net subtitles, 43 were `windows-1253` and 14 UTF-8.
- Converts a subtitle served as UTF-16 or UTF-32 with a BOM to UTF-8 before normalizing its line endings. The one UTF-16 file in that sample ("Gladiator (2000) EXTENDED") used to be reported as `latin-1`, with every line break doubled.
- Preserves uploader, download count, release info, and movie/episode match signals in Provider Hub result payloads.

## Validation Targets

- Fixture tests cover movie parsing, episode page selection, language filtering, titles and episodes the site lacks, UTF-16 subtitles, direct and legacy-form downloads, and bounded refresh of a refused token against the matching detail-page row.
- Live smoke should use Greek language (`ell`) and a video with a current IMDb id on greeksubs.net.
