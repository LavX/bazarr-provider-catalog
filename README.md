# Bazarr Provider Catalog

Official catalog for Bazarr Provider Hub providers.

## Layout

- `catalog.json`: embedded Provider Hub V1 catalog manifests.
- `providers/smoke/`: deterministic no-network smoke provider.
- `sdk/`: standalone authoring tools and templates.
- `tests/`: catalog validation tests that do not import Bazarr internals.

## Common Commands

```bash
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
python3 -B -m sdk smoke-test
python3 -B -m unittest discover -s tests
```

Provider manifests declare pure Python `.py` files only. Dependencies, when needed, must be pinned wheel requirements with SHA256 hashes.
