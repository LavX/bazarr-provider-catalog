# Google Translate plugin

A translator plugin (`bazarr.translator.v1`) with no key and no settings. It reads the answer out
of Google's public mobile translation page with the pinned `deep-translator` library. Subtitle
text is sent to Google, which may limit requests from a busy address. The code is in
`plugins/google_translate/`.

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

## The bundle and its wheels

The bundle carries everything its environment is built from. `plugin.json` locks the closure
(`beautifulsoup4`, `certifi`, `charset-normalizer`, `deep-translator`, `idna`, `requests`,
`soupsieve`, `typing-extensions`, `urllib3`) to exact versions with sha256 hashes, and
`plugins/google_translate/wheels/` holds the wheel each one resolves to. They are all
`py3-none-any` wheels, so the one set runs on every platform and Python 3 the host supports, and
`python_tags` says `py3`. An install builds the environment from these files and does not need PyPI.
`charset-normalizer` also lists its compiled wheels in the lock, so a `--require-hashes` install
that prefers a compiled build still matches; the pure wheel is the one baked.

The plugin SDK is not part of the closure. The host provides it to the worker.

To change a dependency, edit its version and hashes in `plugin.json`, then:

```
python3 scripts/expand_wheel_hashes.py
python3 scripts/bake_plugin_wheels.py
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
```

`build-catalog` refreshes the `files` list and `bundle_sha256` of the manifest. The tests install
the baked wheels offline against the locked hashes, so a closure that is missing a member, or a wheel
that does not match its lock, fails there.
