# Google Translate plugin

A translator plugin (`bazarr.translator.v1`) with no key and no settings. It reads the answer out
of Google's public mobile translation page with the pinned `deep-translator` library. Subtitle
text is sent to Google, which may limit requests from a busy address.

## Declared origins

The manifest declares two origins:

- `https://translate.google.com:443`, the page the code reads today.
- `https://translate.googleapis.com:443`, reserved. No code uses it yet. It is declared now so
  that a later release that switches to Google's JSON endpoint, if the page route breaks, asks
  for no new consent: an origin outside the declared ones would ask again.

## Checkpoints

The plugin stores cumulative checkpoints and expects them back in `request["checkpoints"]` (a
list, oldest first) or `request["checkpoint"]` (the latest). The format and the rules are in the
module docstring of `google_translate.py`.
