# GreekSubs Provider Notes

GreekSubs is a Greek-only movie and episode subtitle source.

## Behavior Preserved

- Looks up movie pages by movie IMDb id at `/en/view/<ttid>`.
- Looks up episode pages through the series IMDb page and the matching Season/Episode link.
- Extracts `secCode` and one-use subtitle ids from the page before building `/dll/<subtitle_id>/0/<secCode>` download URLs.
- Performs the required download-gate GET, reads hidden form fields, then POSTs those fields to fetch subtitle bytes.
- If a saved download URL has an expired `secCode`, reloads only its stored GreekSubs detail page, confirms the same subtitle id is still listed, then retries the gate once with the new token.
- Preserves uploader, download count, release info, and movie/episode match signals in Provider Hub result payloads.

## Validation Targets

- Fixture tests cover movie parsing, episode page selection, language filtering, the tokenized download form, and bounded expired-token refresh against the matching detail-page row.
- Live smoke should use Greek language (`ell`) and a video with a current IMDb id on greeksubs.net.
