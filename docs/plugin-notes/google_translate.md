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

Each checkpoint is a delta: it holds only the cues finished since the previous one. Full
resumption therefore needs all the stored checkpoints, handed back in `request["checkpoints"]` (a
list, oldest first). The single `request["checkpoint"]` form carries one delta, so only the cues in
that one resume. The format and the rules are in the module docstring of `google_translate.py`.

## The bundle and its wheels

The bundle carries what its environment is built from. `plugin.json` locks the closure
(`beautifulsoup4`, `certifi`, `charset-normalizer`, `deep-translator`, `idna`, `requests`,
`soupsieve`, `typing-extensions`, `urllib3`) to exact versions with sha256 hashes, and
`plugins/google_translate/wheels/` holds the wheel each one resolves to. They are all
`py3-none-any` wheels, so the one set fits every platform and Python 3 the host supports, and
`python_tags` says `py3`. `charset-normalizer` also lists its compiled wheels in the lock, so a
`--require-hashes` install that prefers a compiled build still matches; the pure wheel is the one
baked.

Today the host builds a plugin's environment with `pip install --require-hashes` against PyPI and
does not read `wheels/`, so an install still needs PyPI. The wheels are in the bundle so that the
host can install from them (and a seeded image can carry them) once it learns to: that is host work
that has not landed. Until then they are verified and signed with the bundle but not used. The tests
prove that they would work: they install the baked wheels with no index against the locked hashes.

The plugin SDK is not part of the closure. The host provides it to the worker.

To change a dependency, edit its version and hashes in `plugin.json`, then:

```
python3 scripts/expand_wheel_hashes.py
python3 scripts/bake_plugin_wheels.py
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
```

`build-catalog` refreshes the `files` list and `bundle_sha256` of the manifest. `sdk validate`
refuses a bundle whose `wheels/` holds anything but a wheel of a locked dependency, at its locked
version, with a hash the lock lists, and a lock with a dependency that has no wheel. The tests also
install the baked wheels offline against the locked hashes, so a closure that is missing a member
fails there.

## Results and checkpoints

A cue Google gave no text for, or only some of its text, is reported in `missing` and left out of
`lines`, and the result status is `partial`. Bazarr+ keeps the source text for those cues and records
the job as partial. The status is `complete` only when every cue that has text to translate came back
translated.

Checkpoints are deltas: each holds only the cues finished since the previous one. Bazarr+ hands every
stored checkpoint back in the request of a retry, and a request frame is capped at 1 MiB, so the plugin
measures the request and stops storing checkpoints before a retry request would pass its budget.
