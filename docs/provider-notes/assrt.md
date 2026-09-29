# Assrt provider notes

Clean-room target for `assrt`.

## Public behavior

- Site/API: `https://api.assrt.net/v1`
- Quota endpoint: `GET /user/quota?token=<token>`
- Search endpoint: `GET /sub/search?token=<token>&q=<query>&is_file=1`
- Detail endpoint: `GET /sub/detail?token=<token>&id=<subtitle_id>`
- Supported media: movies and episodes.
- Supported languages: English, Chinese, Simplified Chinese, and Traditional Chinese.
- Requires an Assrt API token before quota, search, detail, or download URL discovery.
- Search results can expose several Assrt language codes from one subtitle item.
- Season-pack detail responses include a `filelist`; the provider narrows episode packs to the requested episode before choosing a language-specific file.
- Episode tags can name several episodes. A chained tag (`S01E01E02`, `S01E01.E02`, `S01E01,E02`) gives each episode it lists. A range (`S01E01-E03`, `S01E01-03`, `S01E01~E03`, also with a full-width tilde, a wave dash, an en or em dash, or the Chinese `至` and `到`, as in `S01E01至E05`, and `S01E01-S01E03` when both ends name the same season) keeps a search result for every episode between its ends. A range of more than 100 episodes gives only its two ends. In a pack, a member made for the episode alone is chosen first, then a member listing it among others (`S01E01E02`, or either end of `S01E01-E03`), since a combined subtitle is timed for the joined video, and a member whose range spans the episode only when neither exists. If none of the members in one step has the requested language, the next step is tried. A member's own file name counts before the folder it sits in, so a folder tagged `S01E01-E10` does not name every episode for each file inside it, but it still gives the season to a member named only by its episode (`Show.S02E05/Show.E05.srt`). A resolution or bit depth after a hyphen (`S01E01-720p`, `S01E05-10-bit`) is never read as an episode, and a bare number after a spaced hyphen (`S01E05 - 10 Things`) is a title, not the end of a range.
- A tag in the SxxEyy shape that the provider does not read, such as one with a version suffix (`S01E01v2`), leaves a search result in place for every episode, but never scores it as a season pack. `1x02` tags are not read.
- Single-file detail responses can provide the final download URL directly on the subtitle entry.
- An empty download body or an HTML page (login, quota or expired-file pages can arrive as HTTP 200) is rejected instead of being returned as a subtitle.

## Live verification

- No-token, empty-token, and placeholder-token probes to `/user/quota` and `/sub/search` returned Assrt status `20001` with `invalid token`.
- Full SDK search and download proof requires a real Assrt API token.
- Community validation requested: a token holder should run live SDK smoke and Provider Hub compat search, download, and stream proof from a region that can resolve and fetch Assrt download URLs.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public API request and response contracts only.
