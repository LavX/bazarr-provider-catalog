# GreekSubs Provider Notes

GreekSubs is a Greek-only movie and episode subtitle source.

## Behavior Preserved

- Looks up movie pages by movie IMDb id at `/en/view/<ttid>`.
- Looks up episode pages through the series IMDb page and the matching Season/Episode link.
- Extracts `secCode` and one-use subtitle ids from the page before building `/dll/<subtitle_id>/0/<secCode>` download URLs.
- Performs the required download-gate GET, reads hidden form fields, then POSTs those fields to fetch subtitle bytes.
- Preserves uploader, download count, release info, and movie/episode match signals in Provider Hub result payloads.

## Validation Targets

- Fixture tests cover movie parsing, episode page selection, language filtering, and the tokenized download form.
- Live smoke should use Greek language (`ell`) and a video with a current IMDb id on greeksubs.net.
