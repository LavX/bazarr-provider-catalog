# Addic7ed provider notes

Clean-room target for `addic7ed`.

## Public behavior

- Site: `https://www.addic7ed.com/`
- Supported media: movies and episodes.
- Supported authentication:
  - valid session cookies
  - username and password when the login page does not require captcha solving
- Supported settings: `username`, `password`, `cookies`, `user_agent`, and `vip`.
- Episode search looks up a show id from `shows.php`, then queries `ajax_loadShow.php` for the requested season.
- Movie search resolves a movie id through `search.php?search=<title>`, then reads the `movie/<id>` subtitle page.
- Incomplete subtitles with percentage status are skipped.
- Downloads are plain subtitle files and use the result page as `Referer`.

## Compatibility quirks

- Addic7ed may show a captcha on login. Provider Hub does not expose the legacy in-process captcha solver, so users should configure cookies when captcha appears.
- Valid cookies are checked against `panel.php`; a redirect to `login.php` means the cookies expired.
- Daily download caps are tracked in worker memory: 40 downloads by default and 80 when `vip` is enabled.
- Live verification on 2026-05-31 found the homepage reachable, `shows.php` redirecting to login without cookies, and public title search redirecting to a show page.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
