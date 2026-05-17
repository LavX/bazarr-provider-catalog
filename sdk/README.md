# Provider Hub SDK

This SDK is authoring tooling only. Installed providers do not import it at runtime.

## Commands

- `python3 -B -m sdk validate`: validate `catalog.json`, manifests, file hashes, bundle hashes, and dependency locks.
- `python3 -B -m sdk hash providers/<id>`: print the deterministic bundle SHA256 for a provider folder.
- `python3 -B -m sdk build-catalog`: refresh provider file hashes and regenerate the embedded catalog.
- `python3 -B -m sdk smoke-test`: run a provider against the worker-shaped search/download contract.

Useful smoke-test inputs:

```bash
python3 -B -m sdk smoke-test
python3 -B -m sdk smoke-test --provider smokehub --config-json '{"profile_name":"dev","api_token":"secret"}'
SMOKE_TOKEN=secret python3 -B -m sdk smoke-test --secret api_token=SMOKE_TOKEN
python3 -B -m sdk smoke-test --provider myprovider --config-file fixtures/config.json --video-fixture fixtures/movie.json --language eng --skip-download
```

## Provider Contract

A provider bundle is a folder with `provider.json` plus declared `.py` files. The worker instantiates `entry_class` from `entry_module` and calls:

```python
search(video: dict, languages: list[dict], config: dict) -> list[dict]
download(provider_payload: dict, language: dict, config: dict) -> dict | bytes | str | None
```

No wheels, sdists, native files, symlinks, vendored dependencies, or app-environment `pip install` are part of the bundle. Optional dependencies must be declared in `provider.json` under `dependencies.requirements` with exact versions and SHA256 wheel hashes.

## Manifest Fields

- `provider_id`: lowercase id, no built-in provider shadowing.
- `name` and `version`: user-facing name and provider bundle version.
- `api_version`: must be `bazarr.provider-hub.v1`.
- `entry_module` and `entry_class`: the provider class Bazarr imports inside the worker.
- `config_schema`: JSON-schema subset for UI-rendered settings. Use object properties with `string`, `boolean`, `number`, or `integer`.
- `secret_fields`: config keys that contain credentials. These fields are passed to workers but should never be echoed in results.
- `supported_media`: `movie`, `episode`, or both.
- `languages`: language codes the provider supports.
- `files`: every Python file in the bundle with SHA256.
- `bundle_sha256`: deterministic hash over declared Python files.
- `source`: GitHub repo, ref, commit, catalog path, and trust metadata.
- `dependencies.requirements`: optional hash-locked PyPI wheels.

## Auth And Settings

Declare public and secret settings in `config_schema`. Field names become keys in the worker `config` dict.

```json
"config_schema": {
  "type": "object",
  "additionalProperties": false,
  "required": ["base_url", "username", "api_token"],
  "properties": {
    "base_url": {"type": "string", "title": "Base URL"},
    "username": {"type": "string", "title": "Username"},
    "api_token": {"type": "string", "title": "API token", "secret": true},
    "use_hash": {"type": "boolean", "title": "Use hash matching", "default": true},
    "search_mode": {
      "type": "string",
      "title": "Search mode",
      "enum": ["strict", "balanced", "broad"],
      "default": "balanced"
    }
  }
},
"secret_fields": ["api_token"]
```

Provider code receives the same config in search and download:

```python
class ExampleProvider:
    def search(self, video, languages, config):
        api_token = config["api_token"]
        return []

    def download(self, provider_payload, language, config):
        api_token = config["api_token"]
        return None
```

Never include secret values in `release_info`, `display`, `provider_payload`, errors, or subtitle content.

## Dependency Locks

Dependencies are installed into the provider venv, not into Bazarr. Each requirement must be exact and hash-locked.

```json
"dependencies": {
  "requirements": [
    {
      "name": "humanfriendly",
      "version": "10.0",
      "hashes": [
        "sha256:1697e1a8a8f550fd43c2865cd84542fc175a61dcb779b6fee18cf6b6ccba1477"
      ]
    }
  ]
}
```

Generate hashes with:

```bash
python3 -m pip download humanfriendly==10.0 --only-binary=:all: --no-deps -d dist
python3 -m pip hash dist/humanfriendly-10.0-py2.py3-none-any.whl
```

List every direct and transitive dependency that pip needs under `--require-hashes`.

## Troubleshooting

- `catalog.json is stale or invalid`: run `python3 -B -m sdk build-catalog`.
- `sha mismatch`: rebuild after editing provider Python files.
- `bundle_sha256 is stale`: rebuild after editing declared files.
- `undeclared python file`: run build-catalog or add the file to `provider.json`.
- `unexpected file`: provider bundles allow only `.py` and `provider.json`.
- `dependency must be pinned`: use exact versions, not ranges or VCS URLs.
- `dependency has invalid hash`: use `sha256:<hex>` wheel hashes.
- `missing provider class`: check `entry_module` and `entry_class`.
