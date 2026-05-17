# Bazarr Provider Catalog

Official catalog for Bazarr Provider Hub providers.

## Layout

- `catalog.json`: embedded Provider Hub V1 catalog manifests.
- `providers/smoke/`: deterministic no-network smoke provider for install and worker checks.
- `providers/supersubtitles_demo/`: deterministic SuperSubtitles-style provider that exercises auth, settings, and venv wheel installs.
- `sdk/`: standalone authoring tools and templates.
- `tests/`: catalog validation tests that do not import Bazarr internals.

## Provider Author Quickstart

```bash
cp -R sdk/templates providers/myprovider
$EDITOR providers/myprovider/provider.json providers/myprovider/provider.py
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
python3 -B -m sdk smoke-test --provider myprovider --config-json '{"api_token":"dev-token"}' --skip-download
python3 -B -m unittest discover -s tests
```

Use `providers/supersubtitles_demo/` as the richer example when your provider needs settings, secrets, or PyPI dependencies.

## Common Commands

```bash
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
python3 -B -m sdk smoke-test
python3 -B -m unittest discover -s tests
```

Provider manifests declare pure Python `.py` files only. Dependencies, when needed, must be pinned wheel requirements with SHA256 hashes.
