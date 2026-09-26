# PrijevodiOnline provider notes

Clean-room target for `prijevodionline`.

## Public behavior (0.3.0)

- Site: `https://www.prijevodi-online.org/`. The provider only talks to its JSON API at
  `https://www.prijevodi-online.org/api/v1/` and never follows an absolute URL from a response.
- Supported media: episodes and movies.
- Redirects are refused. A redirect, an HTML answer (the app shell) or a `NOT_FOUND "Route not
  found"` answer on a known route raises `ServiceUnavailable` saying the API changed, instead of
  returning an empty result. So does a translation list in which no row carries a readable `price`
  (owned rows aside).
- Movie subtitles need an account: visitors hold no movie download permission, so a visitor's
  movie search finds nothing to download.

Routes used:

| Route | Purpose |
|---|---|
| `GET /auth/me` | Who the request runs as: visitor or member, permissions, token balance |
| `GET /auth/captcha-config` | Only to classify an unclear sign-in failure |
| `POST /auth/login` | Username and password sign-in, one attempt, no captcha token |
| `GET /search/results?q=&type=series\|movies&perPage=20` | Series and movie lookup by title |
| `GET /series/<id>/seasons` | Season number to season id |
| `GET /translations/series?seasonId=<id>&perPage=1000` | Every translation of a season, all languages, up to 3 pages |
| `GET /movies/by-slug/<slug>` | IMDb check of a movie hit |
| `GET /movies?tmdbId=<id>&perPage=5` | Fallback only when the title search finds nothing |
| `GET /translations/movies?movieId=<id>&perPage=100` | Every translation of a movie, up to 3 pages |
| `GET /translations/series\|movies/<id>/download` | The file |
| `POST /purchases/intent` | Price quote for one translation. It spends nothing |
| `POST /purchases/confirm` | Buys one translation with the quote's token |

Routes never used: the translation detail route (`GET /translations/series/<id>`, which answered
`price: null` for an item the list prices at 1), `/translations/movies/by-movie/<id>` (no price
and no language code), `/series/<id>/episodes`, the pack routes (`/purchases/packs/*`,
`/seasons/<id>/pack*`), `POST /auth/logout` (it would end the user's browser session), and the
magic-link, Google OAuth and forum sign-in routes.

### Modes

| Mode | Settings | Search | Downloads |
|---|---|---|---|
| Visitor (default) | none | series and movies | Free items (price 0), and list-priced series items the site grants visitors through `series.translations.downloadFree`. Never movies: visitors hold no movie download permission |
| Account, spending off | `account_name` and `account_password`, or `session_cookie` | Same, as the member sees it | Free, owned, and anything the site's price quote says needs no purchase |
| Account, spending on | also `allow_paid_downloads` and `max_tokens_per_download` | Also priced items within the cap and the known balance | Also buys priced items within the cap, once each |

A pasted session cookie is tried first, then the username and password, then visitor mode. A
failed sign-in never fails a search: the search runs as a visitor and the failure class is
logged as a warning. The failure is remembered per set of credentials (a captcha answer for 12
hours, rejected credentials for an hour, an unreachable site for 5 minutes), and changing the
credentials retries at once. Only a download that needs the account reports the failure.

The account settings are named `account_name` and `account_password` on purpose. The Provider
Hub labels a provider "Account" when a setting is named `username`, `password` or `email`, and
an account is optional here, so the card shows no account badge. No setting is required. The
only spending limit is the per-download cap; there is no daily or rolling limit.

### Access classes and what search shows

Each translation row is classified for the identity the search runs as:

- `skip`: no file, not published, or not approved. Never shown.
- `owned`: a member bought it (`isPurchased`, not `isRevoked`). Shown, tagged `owned`, whatever
  its price, because an owned row is never quoted or bought.
- `free`: price 0. Shown. Only an explicit 0 is free. A missing, `null` or unreadable price is
  unknown, so the row is skipped, never taken as free, because a free member row is downloaded
  without a price quote (the site's detail route answers `null` for rows its lists price at 1).
- `granted`: priced, but the identity holds `<kind>.translations.downloadFree`, which the site's
  own client treats as free. Shown. With spending on, a member's granted row within the cap is
  tagged `may cost N tokens`, because its quote may still ask for them.
- `priced`: priced, and the member holds `<kind>.translations.download`. Shown only when spending
  is on, the price is at or below the cap, and the known balance covers it. Tagged
  `costs N tokens`.
- `account_required`: the identity holds neither permission. Shown, tagged `needs an account` or
  `needs a member permission`, only when free; hidden when priced.

Candidates the current settings cannot download or pay for are hidden rather than listed and
refused, because a refused download pauses the provider. Each search logs one line with the
hidden counts by reason (`spending_off`, `over_cap`, `low_balance`, `needs_account`).

If a visitor download of a `granted` item is refused with 401, 402 or 403, or with an `Auth/` or
`Tokens/` error, visitor grants are distrusted for 6 hours. Other answers teach nothing: the app
shell where the file belongs raises `ServiceUnavailable` (API changed), and any other error names
its code. If a member's quote for a `granted` item asks for tokens, that account's grants count as
priced for 12 hours.

A series the site answers `404` for on `/series/<id>/seasons` (removed or merged since the lookup
was cached) finds nothing for 30 minutes and its lookup is dropped, instead of failing and pausing
the provider on every search for the show.

### Spending rules

Tokens are spent only when all of these hold: an account is signed in, `allow_paid_downloads` is
on, and the live quoted price is at or below both the per-download cap and the price shown at
search time.

- An `owned` item is never quoted or bought, whatever the worker remembers (after a restart, in
  another worker or once a cache expired). The claim comes from a search that may have run as
  another account or before a refund, so the signed-in account's own translation list (cached
  for 10 minutes) must still show the row as bought; otherwise the download is refused before
  any request for the file. A session that ended since the search reads the list as a visitor
  sees it, so the session is checked once and signed in again before refusing. Then the file is
  fetched directly. If the site still asks for tokens
  (402 or a `Tokens/` error), the download is refused. That nothing is spent then rests on the
  site answering this way rather than charging for the download, which has not been verified;
  it can only happen if the item was refunded between the list read and the download.
- A member's `free` download also skips the quote, so it needs the price 0 listed at search
  time; a download result without it is refused.
- Every member download of a `granted` or `priced` item asks for a quote first. A quote whose
  `translationId` or `translationType` names a different subtitle is refused. A quote that answers
  `download` fetches the file directly.
- Any other `purchase` answer is checked before anything is bought, in this order: no purchase
  of it already sent by this worker (a confirmed one says it was already bought, so no later
  refusal claims that nothing was spent), spending on, a valid cap, no overcharge in the last
  24 hours, a readable token price of at least 1, the cap,
  the price at search time, the balance, `canAfford`, and a purchase token. The settings the user
  controls come first so a refusal names them. A subtitle that was free at search time, or whose
  price at search time is not readable, and now wants tokens is refused.
- The confirmation is sent once, outside the retry loop, never through FlareSolverr and never
  replayed, and only with at least 12 seconds and 5 requests of rate budget left. The one
  exception: when the site answers a confirmation with an authentication error (it checks the
  session before the purchase, so nothing was bought), the provider signs in again once and
  sends one fresh quote and at most one more confirmation. A second authentication error ends
  the download.
- An unknown outcome (a timeout after sending, a 5xx, an unreadable success) blocks buying the
  same subtitle again for up to 24 hours. A later quote that answers `download` clears the block.
- A purchase that the provider already confirmed is never paid again.
- The purchase record is kept per site member id, so a freshly pasted cookie or a switch between
  the cookie and the password for the same account keeps it. It lives in the worker process.
- A charge above the quote is logged as an error and stops purchases on that account for up to
  24 hours; search hides the priced rows meanwhile, and a refusal says why. These blocks live in
  the worker process, so a worker restart ends them early.
- Every refusal before a purchase says that nothing was spent. Any download failure after a
  successful purchase says the purchase went through, and keeps its error class.
- The cap applies to each download. When a downloaded subtitle is rejected (for example an
  archive without the wanted episode), Bazarr moves on to the next candidate, and a second priced
  candidate may be bought in the same run, again within the cap. There is no daily limit.

### Languages (mapped by the site's language code, never its language id)

| Site code (id) | Candidate | Manifest code | Notes |
|---|---|---|---|
| `hr` (1) | `hrv`, alpha2 `hr` | `hrv` | |
| `sr` (2) | `srp`, alpha2 `sr` | `srp` | Latin |
| `sr-cyr` (6) | `srp` with `script: "Cyrl"` | `sr-Cyrl` | Only for a Cyrillic request |
| `bs` (3) | `bos`, alpha2 `bs` | `bos` | |
| `cnr` (4) | `cnr`, alpha2 `me` | `cnr` | `mne` is not a manifest code |
| `mk` (5) | `mkd`, alpha2 `mk` | `mkd` | |
| `mix` (7), `??` (8) | none | none | Ambiguous, never emitted |
| `en` (21), `sl` (58) | none | none | Only in response includes, not advertised |

`hbs` is a broad Serbo-Croatian request: it takes `hr`, `sr` and `cnr` rows that were not asked
for under their own code. It does not take `bs` or `sr-cyr`.

### Downloads

The stored file name always ends in `.zip`, so the answer is sniffed. ZIP and RAR archives go
back to the host in archive mode; a ZIP member is pinned by `SxxExx` and release overlap, and a
movie archive's member only when it holds exactly one subtitle. A bare subtitle goes back in
content mode without an encoding guess. An empty body, HTML, an unexpected JSON answer or a
damaged ZIP raise an error.

## Current live notes (2026-09-26)

- The site relaunched as a browser app. The old HTML routes such as `/serije/index/<letter>`
  answer `301` to the app, a 1.7 KB shell with no listings.
- The JSON API answers every request with `x-ratelimit-limit: 200`, `x-ratelimit-remaining` and
  `x-ratelimit-reset`, a window of about 60 seconds. The provider keeps 20 requests in reserve
  for the user's own browsing, waits up to 5 seconds for the window to reset, and otherwise
  pauses with `APIThrottled`.
- Cloudflare sits in front of the site, but no challenge was seen. FlareSolverr stays an
  optional fallback and only ever visits `/api/v1/auth/me`.
- The sign-in page carries a Cloudflare Turnstile captcha (`/auth/captcha-config` reports it
  enabled). Whether the API enforces it on `POST /auth/login` is not known.
- Downloads are priced in tokens. No item in the sample was free: 0 of 5,501 series
  translations and 0 of 200 movie translations had a price of 0 or empty.
- Visitors hold `series.translations.download` and `series.translations.downloadFree`, and no
  movie download permission.
- The price ladder from `/api/v1/pricing/info`: a series translation costs 5 tokens for 7 days,
  2 until day 14, then 1. A movie translation costs 5 for 7 days, 3 until day 21, 2 until day
  60, then 1.

Live verification covered anonymous use only. No Prijevodi-Online account was available, so sign-in with a username and password, the session cookie mode, token balance reads, purchase quotes and purchase confirmation were never exercised against the live site. Those paths are covered by unit tests with mocked responses shaped after the site's own web client, and their behaviour against the real service is unverified.

Anonymous downloads were not exercised live either; they are unit-tested only.

## History

Versions 0.2.x scraped the old server-rendered HTML site: the alphabetical series index, the
series page with its episode list and AJAX key, and a `POST /prijevod/get/<episode>` subtitle
list. Only episodes were supported.

The site went down in August 2026. The operators announced on 2026-08-21 that their host
suspended the site after repeated copyright complaints about translations hosted there. Their
follow-up said the complete site was backed up with no translations lost, and that they were
looking for new hosting less likely to hit the same problem.

Sources, both from the operators' own page:

- <https://www.facebook.com/prijevodi.online/posts/pfbid02pAELJ6mT71mmvbCp67CBZ1d1MPVgH7dgScqiXYnC7gzuE5FJX69pNjyi3ncivLA7l>
- <https://www.facebook.com/prijevodi.online/posts/pfbid02m5ZabJP4TJ9tFrTyTJKpmsnWTnVCsohvHJ8BkArQo86DcyPM3oKC3wzRho1LFArql>

The site came back in September 2026 as the browser app described above. The relaunch replaced
the HTML routes, so 0.2.1 found no series on the redirected index and returned no results
without an error (reported in <https://github.com/LavX/bazarr/issues/510>). 0.3.0 moved to the
JSON API, and treats an HTML or redirected API answer as an error.

## Open questions about site behaviour

Each has a fallback in the provider:

- Does the server stream a list-priced series translation to a visitor who holds
  `downloadFree`? The provider assumes it does, and hides those items for 6 hours after a
  refusal.
- Is the Turnstile captcha enforced on the API sign-in? The provider tries once without a
  captcha token and points to the session cookie field when the site asks for one.
- Does a forum sign-in give a cookie the API accepts? Not used.
- Does a pasted session cookie work from another address or User-Agent, and is `PHPSESSID`
  needed? Unknown cookies are kept, and an optional User-Agent setting can match the browser.
- What does a member download of a priced, unowned translation do? Not sent on purpose:
  owned items are checked against the account's own list first, and everything else is quoted
  first. Only a refund between that list read and the download could reach it.
- Which error statuses can a confirmation return, how long does a quote token live, and is the
  token empty when the account cannot afford the price? One confirmation is sent right after the
  quote; a 4xx error envelope counts as a refusal, anything else as unknown for 24 hours.
- Is the download always a ZIP, and can it hold several files or a RAR? The answer is sniffed.
- Is the list price the same for every viewer? The member's own list is used, and the quote
  decides.
- Is a download after a purchase free, and what exactly does `isRevoked` mean? An owned
  translation is downloaded without a quote, and a quote right after a purchase is expected to
  answer `download`; `isRevoked` counts as not owned.
- Can members download movies? Read from the member's permissions.
- Is the rate window per address or per session, what does a 429 look like, and could
  Cloudflare escalate? The provider keeps a reserve and pauses with the reset time.
- Are language ids stable? Not relied on: rows are mapped by language code.
- Which app page routes could serve as a subtitle link? None is set.
- How are episodes numbered inside the "Season 99" specials bucket? A special needs its episode
  title to match as well.
- Does the server check `Origin` on POST requests? The provider sends `Origin` and `Referer`
  as a browser does.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
