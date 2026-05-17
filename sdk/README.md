# Provider Hub SDK

This SDK is authoring tooling only. Installed providers do not import it at runtime.

## Commands

- `python3 -B -m sdk validate`: validate `catalog.json`, manifests, file hashes, bundle hashes, and dependency locks.
- `python3 -B -m sdk hash providers/<id>`: print the deterministic bundle SHA256 for a provider folder.
- `python3 -B -m sdk build-catalog`: refresh provider file hashes and regenerate the embedded catalog.
- `python3 -B -m sdk smoke-test`: run the smoke provider against the worker-shaped search/download contract.

## Provider Contract

A provider bundle is a folder with `provider.json` plus declared `.py` files. The worker instantiates `entry_class` from `entry_module` and calls:

```python
search(video: dict, languages: list[dict], config: dict) -> list[dict]
download(provider_payload: dict, language: dict, config: dict) -> dict | bytes | str | None
```

No wheels, sdists, native files, symlinks, vendored dependencies, or app-environment `pip install` are part of the bundle. Optional dependencies must be declared in `provider.json` under `dependencies.requirements` with exact versions and SHA256 wheel hashes.
