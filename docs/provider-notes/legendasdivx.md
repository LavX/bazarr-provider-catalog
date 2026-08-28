# LegendasDivx provider notes

Port of the built-in `legendasdivx` provider onto the Provider Hub catalog.

## Public behavior

- Site: `https://www.legendasdivx.pt/`
- Login endpoint: `/forum/ucp.php?mode=login` (phpBB)
- Search endpoint shape:
  `/modules.php?name=Downloads&file=jz&d_op=<search|jz_00>&op=<op>&query=<query>&temporada=<season>&episodio=<episode>&imdb=<series_imdb>`
- Supported media: movies and episodes.
- Supported languages: `por` (Portugal) and `por` plus `country_alpha2: BR` (Brazil). The manifest
  declares these as `por` and `por-BR`; the runtime payload never invents a `por-BR` alpha3.
- Requires username and password.
- Supports the legacy `skip_wrong_fps` setting.
- Search responses expose subtitle boxes with a header (bold title, `(year)`, uploader anchor), a
  language flag image, hits, frame rate, a free-text description, and a `sub_download` link carrying
  `lid`.
- Downloads are direct subtitle files or ZIP/RAR archives.

## How this plugin differs from the built-in

- **HTTP.** The site is Cloudflare-fronted, so the plugin uses ai-cloudscraper (the catalog's
  convention for gated providers) and keeps one scraper for the worker's lifetime, because the
  phpBB session cookie is what makes search and download work. FlareSolverr is an optional fallback
  when cloudscraper still receives a challenge, and it replays the challenged request with its
  method and body intact: replaying a challenged login POST as a GET would throw the credentials
  away, so FlareSolverr could never recover a challenge on the login submission. Its browser
  follows redirects, so a solved page that lands on the login endpoint is turned back into a
  redirect for the caller, which is the signal re-authentication keys off. The cookies it solves
  are written into the stdlib cookie jar as well as the scraper's, because the fallback HTTP path
  sends cookies from that jar and nowhere else.
- **What counts as a challenge.** A `Server: cloudflare` header only says the response came through
  the proxy; the site's own 403, 429 and 503 carry it too. A response is treated as a challenge only
  when it says so, through `cf-mitigated: challenge` or a challenge marker in the body. Otherwise an
  IP block, a bad password or an outage would be swallowed as a Cloudflare problem, and a pointless
  solve request would be sent.
- **No manual decompression.** cloudscraper advertises brotli and gzip, and urllib3 has already
  decoded the body by the time `response.content` is read. The plugin never decompresses a body.
  This is the failure that was fixed in the built-in's session, and
  `test_a_body_that_reports_an_encoding_is_not_decoded_twice` pins it.
- **Archives.** The worker declares no archive library and never shells out. A zip or rar is
  returned as `archive_b64` with `select_member: true`; the host lists the members and calls back
  into `select_archive_member`, which is how a season pack inside a rar still resolves to the right
  episode from a stdlib-only worker.
- **No wrong-episode fallback.** When no member matches the wanted episode the selector returns
  `reject`. The built-in used to hand back an arbitrary member, which wrote a subtitle for a
  different episode into the library and marked the episode done with no error anywhere.
- **No subprocess extraction fallback.** Host-side extraction replaces it, and a sandboxed worker
  must not shell out. The built-in's CLI-extraction budget (time, size, member count) has no
  counterpart here because there is no CLI to bound.

## Compatibility quirks

- The site tracks a daily search counter in `<!--pesquisas: N-->`; the plugin raises
  `SearchLimitReached` at 145 of the real 150 so a search never trips the block that follows.
- Episode searches use the series IMDb id path when `series_imdb_id` is available. That filter is
  applied by the backend, which is why `derive_matches` claims series/season/episode on that path
  without re-proving them from the description.
- If no series IMDb id is available, the plugin searches the series plus `SxxEyy`, then the season
  pack shape.
- Movie searches send `imdb=`, so nothing on the backend guarantees the film. An `imdb_id` match is
  claimed only when the id actually appears in the result.
- A result whose description cell is missing is still a result: the header already states the title
  and year, and the release name falls back to them.
- A direct (non-archive) download reports the format its bytes actually are, from
  `Content-Disposition`, the final URL, or a sniff of the body. The candidate's filename is
  synthetic and always says `.zip`, so trusting it would label an ASS or VTT file as SubRip.
- `bloqueado` is ordinary Portuguese and turns up in uploader descriptions, so the IP-block guard
  matches the notice (the word near `IP`) rather than the bare word.
- Pagination uses ceiling division. Flooring and adding one costs a request for a page that does not
  exist whenever the result count is an exact multiple of the page size, against a site with a
  strict daily search limit.
- The login POST answers 200 whether or not the credentials were accepted. Success is decided from
  the session cookies: a session id on its own proves nothing, because phpBB hands one to anonymous
  visitors too, so a cookie naming a real (non-anonymous) user id is required as well. Both cookies
  are matched by shape (`phpbb3_*_u`, `phpbb3_*_sid` or `PHPSESSID`) rather than by the board
  prefix, so a prefix change cannot quietly turn "no user cookie found" into "accept anything".
- A session that expires under a reused worker shows up as a redirect to the login page. The plugin
  drops the phpBB cookies, logs in again and retries once. The Cloudflare clearance cookie is kept:
  it is unrelated to the phpBB session and expensive to re-obtain. A redirect that survives the
  re-login raises rather than being parsed: a 302 body is an empty result page, so search and
  pagination would otherwise report "no subtitles" for what is an authentication failure.

## Deliberate divergences from the built-in

Recorded in `member_matches_episode_intentional_divergences` in the parity fixture and asserted by
the suite, so neither side can drift unnoticed.

- **A folder and a file name that disagree about the season.** For `Pack S02/Show.S01E07.srt`
  guessit resolves the contradiction by trusting the folder, so the built-in serves that member for
  S02E07. The plugin refuses it for either season: one of the two names is wrong, nothing in the
  path says which, and delivering a subtitle on a coin flip is the failure this selector exists to
  prevent. A folder that agrees with the file name still matches normally.

## Known-unverified areas

Shipping without a live run against the site was a deliberate decision (there is no account for
this work). Recording what that means, so a field failure is diagnosable:

- **Verified by the built-in's author against the live site**, and ported here character for
  character, with parity asserted in `tests/test_legendasdivx.py` against
  `tests/fixtures/legendasdivx_builtin_parity.json`: release-name extraction
  (`clean_release_line`, `extract_release_info`) and archive member episode matching. The member
  matching is checked over roughly 900 name-and-episode combinations generated by running the
  built-in itself, not by hand, so the two cannot drift apart quietly.
- **Not covered by that verification, and the most likely thing to fail in the field:**
  1. *The cloudscraper conversion.* No offline fixture can prove a Cloudflare challenge is actually
     cleared. If search returns nothing and the log shows `CloudflareBlockedError`, the fix is a
     FlareSolverr URL in the provider settings.
  2. *Host-side archive extraction.* New code with no live-verified equivalent. If a download fails
     with "no usable subtitle member" or "select_archive_member rejected the archive", the member
     names are the thing to look at.
  3. *The login form's captcha path.* The plugin detects a reCAPTCHA or hCaptcha sitekey on the
     login page and posts the solver token under that captcha's own field, but no captcha has been
     observed on this form. If login fails with the captcha message and the form turns out to use
     phpBB's own image or Q&A captcha instead, that path needs rewriting, not configuring.
  4. *Result markup details.* The HTML fixtures are modelled on the markup in the built-in's own
     test suite, not on a fresh capture. Title, year and uploader are read from the result header;
     if the site nests them differently, `_parse_sub_header` is where that shows up.
  5. *The bare-number reading of archive member names.* Without guessit the plugin reads a bare
     number three ways (episode, absolute number, compact season-plus-episode) after stripping
     release tags and any season token, and a season stated by a path segment binds those readings
     so `Season 1/02.srt` cannot answer a request for season two. Member names keep their
     separators for this, because collapsing them would make the range in `S01E01-03` (which covers
     episode two) indistinguishable from the stray number in `S01E01.2160p` (which does not). The
     compact season-plus-episode reading applies only to the number a file name opens with, which
     is this site's pack shape (`105 - Pilot.srt`); a number further along is an absolute one
     (`[Group] Anime - 105 [720p].srt`) and does not also answer for season one episode five. It
     agrees with the built-in on every recorded case bar the one divergence above, but an unusual
     member name could still be read differently.

Asking the built-in's contributor to try a plugin build is the cheapest way to close items 1 and 2.

## License notes

`clean_release_line` and `extract_release_info` are copied from the Bazarr+ built-in provider,
which is part of the same project and under the same terms. The rest is a clean-room Provider Hub
plugin under this repository's MIT license. The behavior notes above describe public request and
markup contracts only.
