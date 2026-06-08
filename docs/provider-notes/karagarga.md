# Karagarga provider notes

Clean-room target for `karagarga`.

## Public behavior

- Site: `https://karagarga.in`
- Forum: `https://forum.karagarga.in`
- Supported media: movies only.
- Supported language: English only.
- Search flow:
  - log in to the tracker through `takelogin.php`
  - log in to the forum through `forum.karagarga.in/index.php`
  - search completed tracker subtitle entries through `pots.php?search=<title>&status=completed`
  - scan up to three approved forum links from matching English rows
  - parse forum attachments and choose the most downloaded subtitle
- Downloads fetch the forum attachment URL directly.

## Compatibility quirks

- Main tracker credentials are required. Forum username and password default to the main credentials when omitted.
- Main login is considered valid when the tracker sets a `pass` cookie.
- Forum login is considered valid when both `session_id` and `pass_hash` cookies are present.
- Search rows must match the requested movie year and English language, and must point to an approved forum item.
- Live verification on 2026-05-31 found the homepage reachable, `pots.php` redirecting to login without credentials, and the forum serving its sign-in page.

## License notes

Implementation is a clean-room Provider Hub plugin under this repository's MIT license. Behavior notes above describe public request and markup contracts only.
