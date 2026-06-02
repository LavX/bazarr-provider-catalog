# XSubs provider notes

Historical notes for `xsubs`. The upstream no longer serves the legacy XSubs subtitle service, so no active Provider Hub plugin is shipped from this branch.

## Public behavior

- Site: `http://xsubs.tv`
- Supported media: episodes only.
- Supported language: Greek, `ell` / `el`.
- Optional credentials:
  - `username`
  - `password`
- Login endpoint: `/xforum/account/signin/`
- Logout endpoint: `/xforum/account/signout/`
- Series index endpoint: `/series/all.xml`
- Series details endpoint shape: `/series/<show_id>/main.xml`
- Season subtitles endpoint shape: `/series/<show_id>/<season_id>.xml`
- Download endpoint shape: `/xthru/getsub/<release_id>`

## Compatibility quirks

- `username` and `password` are optional, but must be supplied together.
- Login uses a `csrftoken` cookie as `csrfmiddlewaretoken` and succeeds only on HTTP `302`.
- The series index grouped TV series under the Greek series category and exposed `series` elements with `srsid` ids.
- Series names stripped trailing non-year bracket tags before matching.
- Article names were matched both as `The Name` and `Name The`.
- Show lookup tried the sanitized title with the year, the year in brackets, then the title without a year.
- Episode listings used a season id from `series_group` rows and subtitle groups from `subg`.
- Multi-episode ranges such as `1-2` produced one result per episode in the range.
- Rows with empty `published_on` were unreleased and skipped.
- Download bodies were direct subtitle files encoded as Windows-1253 text.

## Live verification

Live verification on 2026-05-31 is blocked because the upstream host no longer serves the original subtitle site:

- `http://xsubs.tv/` returned HTTP `200`, but the response body was an unrelated Korean-language link page.
- `http://xsubs.tv/series/all.xml` returned HTTP `200`, but the response body was the same unrelated page instead of the legacy XML series index.
- `https://xsubs.tv/series/all.xml`, `http://www.xsubs.tv/series/all.xml`, and `https://www.xsubs.tv/series/all.xml` returned the same unrelated page.
- `http://xsubs.tv/xforum/account/signin/` returned the same unrelated page instead of the legacy login form.

Do not require live smoke, Provider Hub compat proof, or core allow-list promotion unless the original XSubs subtitle service returns or a verified replacement origin is found.

## License notes

No implementation is promoted while the upstream remains unrelated to the legacy service. Behavior notes above describe public request and response contracts only.
