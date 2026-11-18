# Bazarr+ Provider Hub SDK

Authoring tooling for building subtitle provider plugins distributed through the [Bazarr+](https://github.com/LavX/bazarr) Provider Hub catalog. Installed providers do not import this SDK at runtime: Bazarr+ runs each plugin in an isolated worker with only its declared dependencies.

> New here? Start at the repo [README](../README.md) and the [provider author guide](../docs/writing-a-scraper-provider.md). Looking for the Bazarr+ app itself? It lives at [LavX/bazarr](https://github.com/LavX/bazarr), docs at [lavx.github.io/bazarr](https://lavx.github.io/bazarr/).

## Commands

- `python3 -B -m sdk validate`: validate `catalog.json`, manifests, file hashes, bundle hashes, dependency locks and `revocations.json` (including that a branch which has signed a list still has the file). It also checks every catalog manifest with the released-host validators vendored in `sdk/released_hosts/`. Every `plugins/<id>/plugin.json` is validated against the schema 2 rules of the 2.8 host. Released hosts never read `plugins/`, so the released-host check covers providers only.
- `python3 -B -m sdk build-signed-feed --channel main|beta`: print the unsigned body of `signed/<channel>/catalog.json`: the providers of `catalog.json` plus the `plugins/` entries sorted by id, without the four fields a signing run stamps (`signed_version`, `not_before`, `expires`, `source_commit`). The same tree always gives the same bytes. A `revocations.json` at the root adds the `revocations` field (see Revocations). `--output PATH` writes the body to a file instead.
- `python3 -B -m sdk sign --channel main|beta`: build the unsigned body, stamp `signed_version` (the signing time in Unix seconds), `not_before`, `expires` (90 days later by default, at most 366 with `--validity-days`) and `source_commit` (the commit it was built from), sign the exact bytes with the CI signing key and write `signed/<channel>/catalog.json` and `catalog.json.sig`. The key is read from the environment variable named by `--key-env` (default `CATALOG_SIGNING_KEY`) as an unencrypted PKCS8 PEM, is never written anywhere and is removed from the process once read. Nothing is written unless the host's verifier accepts the result with the key file and the root it trusts. `--renew` keeps the current signed body, `source_commit` included, and only restamps the dates. It needs `sdk/requirements-signing.txt` (`pip install --require-hashes --only-binary=:all: -r sdk/requirements-signing.txt`). The publish workflow runs this in the `catalog-signing` environment, after the owner approves the run.
- `python3 -B -m sdk verify --channel main|beta`: check `signed/<channel>/` with the host's verifier, the root hosts trust and `keys/catalog-signing-keys.json`. `--ignore-expiry` checks the signature and chain only.
- `python3 -B -m sdk check-expiry`: read `expires` of each channel's signed body and say which channels are due for a renewal (45 days left or fewer) and whether anything is in the alarm window (14 days left or fewer, an expired or unreadable signed file, or, with `--git-ref-prefix`, no commit on the signed branches for 50 days once something is signed). `--git-ref-prefix origin/` reads each channel from its own branch of one checkout and fails when a branch is missing, `--runs FILE` takes the unfinished runs of the signing workflow as a JSON list or JSON lines (a channel with a run already waiting or running is not asked again; a missing or incomplete file starts nothing), `--outputs-file` appends `request=<channels>` and `alarm=<true|false>`, and `--fail-on-alarm` exits 1 in the alarm window. A channel with no signed file is skipped. The daily `signature-expiry.yml` workflow runs this and starts a renewal run for each requested channel. See `docs/signed-feed-calendar.md`.
- `python3 -B -m sdk hash providers/<id>`: print the deterministic bundle SHA256 for a provider folder.
- `python3 -B -m sdk build-catalog`: refresh provider and plugin file hashes and regenerate the embedded catalog. `catalog.json` carries providers only, so nothing under `plugins/` ever changes it.
- `python3 scripts/bake_plugin_wheels.py [plugin_id ...]`: download the wheels each `plugins/<id>/plugin.json` locks (checked against the locked hashes) into `plugins/<id>/wheels/`, so the plugin's bundle carries what its environment is built from. Unlike a provider bundle, a plugin bundle may carry these wheel files; each is listed in `files` and covered by `bundle_sha256`, and `validate` refuses a wheel the lock does not name. Run `build-catalog` afterwards.
- `python3 -B -m sdk runtime-matrix`: print the supported Bazarr+ Provider Hub Python runtimes and wheel tags.
- `python3 -B -m sdk smoke-test`: run a provider against the worker-shaped search/download contract.

Useful smoke-test inputs:

```bash
python3 -B -m sdk smoke-test
python3 -B -m sdk smoke-test --provider smokehub --config-json '{"profile_name":"dev","api_token":"secret"}'
SMOKE_TOKEN=secret python3 -B -m sdk smoke-test --secret api_token=SMOKE_TOKEN
python3 -B -m sdk smoke-test --provider myprovider --config-file fixtures/config.json --video-fixture fixtures/movie.json --language eng --skip-download
```

## Revocations

A branch can withdraw plugins or single releases from hosts that have already seen them. The list lives in `revocations.json` at the repo root of that branch, as a JSON array, and `python3 -B -m sdk build-signed-feed` copies it into the signed body as the top-level `revocations` field. `catalog.json` never carries it, so hosts that read only `catalog.json` are not affected.

```json
[
  {"id": "example", "version": "1.2.0", "reason": "It sends credentials to the wrong host.", "revoked_at": "2026-12-01T00:00:00Z"},
  {"id": "retired_plugin"}
]
```

- `id` (required): the plugin id, matching `^[a-z][a-z0-9_]{1,63}$`.
- `version` (optional): one release, 1 to 64 characters, with no blank, padding or control characters. Leave it out to revoke every release of the plugin. Never `null` and never `""`.
- `reason` (optional): text for people, at most 300 characters, with runs of spaces collapsed and no control characters (line breaks and tabs included). A host puts the reason into one sentence of at most 200 characters and shows roughly the first 80 characters of it, so put the point first.
- `revoked_at` (optional): `YYYY-MM-DDTHH:MM:SSZ`, in UTC.
- No other fields, no `null` values, no repeated `id` and `version` pair, at most 2000 entries. The builder sorts by `id`, then `version` (an entry without a version first), so a tree always gives the same bytes.

`sdk validate` and the build refuse any entry that breaks these rules, because a host refuses the whole catalog over one bad entry. The builder also runs the list through `sdk/host_revocation.py`, a vendored copy of the host's parser (`tests/test_host_revocation.py` checks that it has not drifted), and `sign` runs it over the stamped body, so nothing a host would refuse gets signed. `sdk validate` also fails when `revocations.json` is missing but `signed/main/catalog.json` or `signed/beta/catalog.json` in the tree carries a non-empty list. With no `revocations.json` the body has no `revocations` field. A file that holds `[]` publishes `"revocations": []`.

The list is replaced by each signing run of the branch and a renewal (`sign --renew`) keeps it. A branch that has signed a non-empty list will not sign a body without the field: if the file goes missing, the run fails. To lift every entry on purpose, set the file to `[]`. To lift one, delete its entry. Hosts drop an entry only when a newer signed catalog of that channel no longer lists it.

Two things to know when editing the list:

- Hosts enforce the union of the `main` and `beta` lists. Lifting an entry means removing it on every branch that carries it, and promoting `beta` to `main` by merge copies the file with it.
- A host matches versions by its own normalisation: `1.0`, `1.0.0` and `v1.0.0` are one release. The duplicate check here is exact, so do not list two spellings of one release.

## Provider Contract

A provider bundle is a folder with `provider.json` plus declared `.py` files. The worker instantiates `entry_class` from `entry_module` and calls:

```python
search(video: dict, languages: list[dict], config: dict) -> list[dict]
download(provider_payload: dict, language: dict, config: dict) -> dict | bytes | str | None
# optional: only needed when download() returns {"archive_b64": ..., "select_member": true}
select_archive_member(provider_payload: dict, language: dict, members: list[str], config: dict) -> dict
# optional: reports bounded, transient provider status after successful operations
drain_events() -> list[dict]
```

`select_archive_member` is how a worker language-pins a member of an archive it cannot list
itself (rar/7z are not stdlib-listable). When `download()` returns `select_member: true`
with the raw `archive_b64`, the host lists the members and calls `select_archive_member`
with their names; the worker runs its own language tagging and returns
`{"member": <name>|None, "decision": "pin"|"defer"|"reject"}`: **pin** the named member,
**defer** to the host's episode pick (safe for single-language archives), or **reject**
(host fails loud, e.g. a multilingual archive missing the requested language). This keeps
language selection identical for zip and rar/7z and avoids silent wrong-language downloads.

### AI provenance and runtime events

A search candidate may set the top-level field `ai_translated` to the literal boolean `true`
when the subtitle was produced by large-model translation. Keep it at the top level, not under
`display`. Missing values, `false`, strings, and numbers do not mark a candidate. This field is
distinct from `machine_translated`, which the host does not read from Provider Hub candidates.
Older hosts ignore the extra top-level field.

A provider that has transient account status to report may implement `drain_events()`. The worker
calls it after each successful `search` and `download`. Return a list of plain JSON objects. The
worker accepts at most 8 events per operation and at most 4 KiB serialised across the accepted
events. Events that cannot be serialised as strict JSON are dropped, including bytes, sets, non-finite
numbers, or circular structures. A host that does not support events never calls this method.

The `translation_quota` event has this shape:

```json
{
  "type": "translation_quota",
  "entitled": true,
  "exhausted": false,
  "remaining": 37,
  "limit": 50,
  "reset_at": "2026-10-01T00:00:00Z"
}
```

All five status fields are nullable. `entitled` and `exhausted` must otherwise be booleans.
`remaining` and `limit` must otherwise be integers from 0 through 10,000,000. `reset_at` must
otherwise be a string of at most 64 characters. The host drops invalid fields, unknown event
types, and an event whose five status fields are all null. Status is advisory and transient. It
must not contain credentials, account identifiers, or other secrets.

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

### Endpoint Lists

A setting that holds several endpoints is a whitespace-separated string. A secret field that pairs with it, such as one API key per endpoint, uses the same order, and its count must match. Compare the counts over the whole lists first. Raise `ConfigurationError` when they differ, and name both counts in the message, as they were given.

Then keep at most 16 endpoints, with their keys. Log a warning that names how many were dropped, and ignore the rest: entries past the sixteenth are never validated, so a bad one there cannot fail the search.

Validate each kept endpoint before the first request:

- Accept only `http` and `https` URLs with a host. Refuse user credentials, query strings, and fragments.
- Remove a trailing slash and keep the base path.
- Refuse duplicate normalized URLs.
- Derive each endpoint's tag from the first 16 hex characters of the SHA-256 of its normalized URL. Refuse tag collisions.

Use the tag in candidate ids and `provider_payload`, not the URL. Never log a key, a credential, or a response body.

A failed endpoint contributes no results and logs one bounded line with its tag, exception class, and reason. A valid empty response is a success. Raise ConfigurationError before any request when the settings are invalid. Otherwise raise only when every endpoint failed: raise the shared class when all of them failed the same way, and `ServiceUnavailable` otherwise. Raise with `from None` so that transport exceptions do not chain into worker output.

## Dependency Locks

Dependencies are installed into the provider venv, not into Bazarr. Each requirement must be exact and hash-locked.

Provider Hub targets the Bazarr+ runtime range `>=3.12,<3.15`, currently Python `3.12`, `3.13`, and `3.14`. Python `3.11` is not the catalog floor. Use `python3 -B -m sdk runtime-matrix` for the current ABI tags before reviewing dependency hashes.

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

List every direct and transitive dependency that pip needs under `--require-hashes`. A pure `py3-none-any` wheel usually needs one hash. A compiled or ABI-specific wheel needs hashes for all supported runtime tags, currently `cp312`, `cp313`, and `cp314`, or a compatible stable-ABI wheel such as `cp311-abi3`. ABI-specific and stable-ABI wheels still need hashes for every platform tag Bazarr+ installs on.

For ABI-specific packages, repeat wheel collection for each supported Python version and platform. Example shape:

```bash
python3 -m pip download cffi==1.17.1 --only-binary=:all: --no-deps \
  --python-version 3.12 --implementation cp --abi cp312 \
  --platform manylinux2014_x86_64 -d dist
python3 -m pip hash dist/*.whl
```

To do this across the whole catalog in one shot, run the maintained helper, which
pulls the matrix wheel hashes (cp312/cp313/cp314 + abi3, manylinux x86_64 and
aarch64, plus pure `py3-none-any`) straight from the PyPI JSON API and unions them
into every `provider.json` (existing hashes are never dropped):

```bash
python3 scripts/expand_wheel_hashes.py
python3 -B -m sdk build-catalog
python3 -B -m sdk validate
```

`sdk validate` enforces this: a package known to ship ABI-specific wheels
(`cffi`, `cryptography`, `aiohttp`, `charset-normalizer`, `yarl`, `multidict`,
`frozenlist`, `propcache`, `brotli`, `psutil`, `pycryptodome`, ...) that pins only
a single hash is rejected. Packages that publish no aarch64 wheel on PyPI (for
example `py7zz`, which has no Linux aarch64 build at any version) are a known
coverage gap, not a manifest error, and are exempted; providers that need archive
extraction on aarch64 workers require a different dependency.

## Troubleshooting

- `catalog.json is stale or invalid`: run `python3 -B -m sdk build-catalog`.
- `sha mismatch`: rebuild after editing provider Python files.
- `bundle_sha256 is stale`: rebuild after editing declared files.
- `undeclared python file`: run build-catalog or add the file to `provider.json`.
- `unexpected file`: provider bundles allow only `.py` and `provider.json`.
- `dependency must be pinned`: use exact versions, not ranges or VCS URLs.
- `dependency has invalid hash`: use `sha256:<hex>` wheel hashes.
- `ships ABI-specific wheels but pins only N hash`: run `python3 scripts/expand_wheel_hashes.py` to add the full cp312/cp313/cp314 manylinux x86_64+aarch64 (or abi3) wheel hashes.
- `missing provider class`: check `entry_module` and `entry_class`.
