import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
CATALOG_NAME = "Bazarr Provider Catalog"
API_VERSION = "bazarr.provider-hub.v1"
EXPECTED_SMOKE_PROVIDER_ID = "smokehub"
DEFAULT_SMOKE_CONFIG = {
    "profile_name": "smoke-profile",
    "api_token": "smoke-secret-token",
}
DEFAULT_SMOKE_VIDEOS = (
    {"kind": "movie", "title": "Smoke Movie"},
    {"kind": "episode", "series": "Smoke Show", "season": 1, "episode": 2},
)
DEFAULT_SMOKE_LANGUAGES = ({"alpha3": "eng", "hi": False, "forced": False},)
EXPECTED_SMOKE_SUBTITLE = """1
00:00:01,000 --> 00:00:02,500
SmokeHub deterministic subtitle.
"""
_HEX_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_HEX_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_CONFIG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENTRY_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ALLOWED_MEDIA = {"movie", "episode"}
SUPPORTED_CONFIG_TYPES = {"string", "boolean", "number", "integer"}
DISALLOWED_SCHEMA_KEYS = {"$ref", "oneOf", "anyOf", "allOf", "not", "items"}
SUPPORTED_PYTHON_REQUIRES = ">=3.12,<3.15"
SUPPORTED_PYTHON_VERSIONS = ("3.12", "3.13", "3.14")
SUPPORTED_PYTHON_ABI_TAGS = ("cp312", "cp313", "cp314", "abi3")
SUPPORTED_WHEEL_PLATFORM_TAGS = ("manylinux2014_x86_64", "manylinux2014_aarch64")
WHEEL_COVERAGE_POLICY = (
    "Use a py3-none-any wheel when available. ABI-specific wheels must include "
    "hashes for cp312, cp313, cp314, or a compatible stable-ABI wheel such as "
    "cp311-abi3, on every Bazarr+ platform you support."
)
# Packages that ship ABI/platform-specific wheels and therefore must carry hashes
# for more than one wheel (cp312/cp313/cp314 across manylinux x86_64 and aarch64,
# or a compatible abi3 wheel). A single hash means a pip --require-hashes install
# fails on any worker that resolves a different wheel than the one pinned. Use
# scripts/expand_wheel_hashes.py to regenerate the full set from PyPI.
KNOWN_MULTIWHEEL_PACKAGES = frozenset({
    "aiohttp", "brotli", "Brotli", "cffi", "charset-normalizer", "charset_normalizer",
    "cryptography", "frozenlist", "multidict", "propcache", "psutil",
    "pycryptodome", "pycryptodomex", "yarl",
})
# Archive libraries must not be bundled: the Bazarr+ host extracts zip/rar/7z members
# itself (see docs/provider-bulk-creation-guide.md "Archive extraction"), so a worker
# never carries py7zz (which has no aarch64 wheel) or another archive backend.
BUNDLED_ARCHIVE_PACKAGES = frozenset({"py7zz", "py7zr", "rarfile"})


class CatalogError(Exception):
    pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc


def dump_json(data):
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_sha256(manifest, provider_dir):
    digest = hashlib.sha256()
    for relative_path in sorted(manifest["files"]):
        data = (provider_dir / relative_path).read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def safe_rel_path(path, allow_python=True):
    if not isinstance(path, str) or not path:
        raise CatalogError("paths must be non-empty strings")
    if path.startswith("/") or "\\" in path:
        raise CatalogError(f"unsafe path: {path}")
    parsed = PurePosixPath(path)
    if any(part in ("", ".", "..") for part in parsed.parts):
        raise CatalogError(f"unsafe path: {path}")
    if allow_python and parsed.suffix != ".py":
        raise CatalogError(f"only .py files are allowed: {path}")
    return parsed.as_posix()


def discover_provider_dirs(root):
    providers_root = root / "providers"
    if not providers_root.is_dir():
        raise CatalogError(f"missing providers directory: {providers_root}")
    return sorted(path for path in providers_root.iterdir() if (path / "provider.json").is_file())


def validate_dependency_lock(manifest_path, dependencies):
    if not isinstance(dependencies, dict):
        raise CatalogError(f"{manifest_path} dependencies must be an object")
    requirements = dependencies.get("requirements", [])
    if not isinstance(requirements, list):
        raise CatalogError(f"{manifest_path} dependencies.requirements must be a list")
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise CatalogError(f"{manifest_path} dependency must be an object")
        name = requirement.get("name")
        version = requirement.get("version")
        hashes = requirement.get("hashes")
        if not isinstance(name, str) or not name or any(token in name for token in ("/", "\\", ";")):
            raise CatalogError(f"{manifest_path} dependency has unsafe name")
        if not isinstance(version, str) or not version or any(token in version for token in ("<", ">", "=", "*")):
            raise CatalogError(f"{manifest_path} dependency {name} must be pinned")
        if not isinstance(hashes, list) or not hashes:
            raise CatalogError(f"{manifest_path} dependency {name} needs hashes")
        for digest in hashes:
            if not isinstance(digest, str) or not digest.startswith("sha256:") or not _HEX_SHA256_RE.match(digest[7:]):
                raise CatalogError(f"{manifest_path} dependency {name} has invalid hash")
        if re.sub(r"[-_.]+", "-", name).lower() in BUNDLED_ARCHIVE_PACKAGES:
            # Compare PEP 503 normalized names so a different casing/separator (Py7zz,
            # RarFile, py_7zz) cannot smuggle a banned archive library past the gate.
            raise CatalogError(
                f"{manifest_path} must not bundle archive library {name}; the Bazarr+ host "
                f"extracts zip/rar/7z members (return an archive_b64 download payload)"
            )
        if name in KNOWN_MULTIWHEEL_PACKAGES and len(hashes) < 2:
            raise CatalogError(
                f"{manifest_path} dependency {name} ships ABI-specific wheels but pins only "
                f"{len(hashes)} hash; include the cp312/cp313/cp314 manylinux x86_64+aarch64 "
                f"(or abi3) wheel hashes (run scripts/expand_wheel_hashes.py)"
            )


def validate_config_schema(manifest_path, config_schema, secret_fields):
    if config_schema.get("type") != "object":
        raise CatalogError(f"{manifest_path} config_schema.type must be object")
    if any(key in config_schema for key in DISALLOWED_SCHEMA_KEYS):
        raise CatalogError(f"{manifest_path} config_schema contains unsupported composition keys")

    properties = config_schema.get("properties", {})
    if not isinstance(properties, dict):
        raise CatalogError(f"{manifest_path} config_schema.properties must be an object")

    required = config_schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise CatalogError(f"{manifest_path} config_schema.required must be a string list")

    for key, field in properties.items():
        if not isinstance(key, str) or not _CONFIG_KEY_RE.match(key):
            raise CatalogError(f"{manifest_path} config key is invalid: {key}")
        if not isinstance(field, dict):
            raise CatalogError(f"{manifest_path} config field {key} must be an object")
        if any(item in field for item in DISALLOWED_SCHEMA_KEYS | {"properties"}):
            raise CatalogError(f"{manifest_path} config field {key} contains unsupported nested schema")
        field_type = field.get("type")
        if field_type not in SUPPORTED_CONFIG_TYPES:
            raise CatalogError(f"{manifest_path} config field {key} has unsupported type")
        enum = field.get("enum")
        if enum is not None:
            if not isinstance(enum, list) or not enum:
                raise CatalogError(f"{manifest_path} config field {key} enum must be a non-empty list")
            if any(not isinstance(item, (str, int, float, bool)) for item in enum):
                raise CatalogError(f"{manifest_path} config field {key} enum must use scalar values")

    for key in required:
        if key not in properties:
            raise CatalogError(f"{manifest_path} required config field is not declared: {key}")

    for key in secret_fields:
        field = properties.get(key)
        if field is None:
            raise CatalogError(f"{manifest_path} secret field is not declared in config_schema: {key}")
        if field.get("type") != "string":
            raise CatalogError(f"{manifest_path} secret field must be a string: {key}")


def validate_manifest(root, provider_dir, seen_ids):
    manifest_path = provider_dir / "provider.json"
    manifest = read_json(manifest_path)

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise CatalogError(f"{manifest_path} schema_version must be {SCHEMA_VERSION}")
    provider_id = manifest.get("provider_id")
    if not isinstance(provider_id, str) or not _PROVIDER_ID_RE.match(provider_id):
        raise CatalogError(f"{manifest_path} provider_id is invalid")
    if provider_id in seen_ids:
        raise CatalogError(f"duplicate provider id: {provider_id}")
    seen_ids.add(provider_id)

    for field in ("name", "version", "entry_module", "entry_class"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise CatalogError(f"{manifest_path} {field} must be a non-empty string")
    if not _ENTRY_MODULE_RE.match(manifest["entry_module"]):
        raise CatalogError(f"{manifest_path} entry_module must be a simple Python module name")
    if manifest.get("api_version") != API_VERSION:
        raise CatalogError(f"{manifest_path} api_version must be {API_VERSION}")
    if not isinstance(manifest.get("config_schema"), dict):
        raise CatalogError(f"{manifest_path} config_schema must be an object")
    if not isinstance(manifest.get("secret_fields"), list) or any(not isinstance(item, str) for item in manifest["secret_fields"]):
        raise CatalogError(f"{manifest_path} secret_fields must be a string list")
    validate_config_schema(manifest_path, manifest["config_schema"], manifest["secret_fields"])
    if not isinstance(manifest.get("supported_media"), list) or not manifest["supported_media"] or any(item not in ALLOWED_MEDIA for item in manifest["supported_media"]):
        raise CatalogError(f"{manifest_path} supported_media is invalid")
    if not isinstance(manifest.get("languages"), list) or not manifest["languages"] or any(not isinstance(item, str) or not item for item in manifest["languages"]):
        raise CatalogError(f"{manifest_path} languages must be a non-empty string list")

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise CatalogError(f"{manifest_path} files must be a non-empty object")
    declared_files = set()
    for relative_path, expected in files.items():
        safe_path = safe_rel_path(relative_path)
        declared_files.add(safe_path)
        file_path = provider_dir / safe_path
        if file_path.is_symlink():
            raise CatalogError(f"symlink not allowed: {file_path}")
        if not file_path.is_file():
            raise CatalogError(f"missing declared file: {file_path}")
        if expected != file_sha256(file_path):
            raise CatalogError(f"sha mismatch for {safe_path}")
    for path in provider_dir.rglob("*"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "provider.json":
            continue
        if path.is_symlink():
            raise CatalogError(f"symlink not allowed: {path}")
        if path.is_file() and path.suffix == ".py":
            rel = path.relative_to(provider_dir).as_posix()
            if rel not in declared_files:
                raise CatalogError(f"undeclared python file: {rel}")
        if path.is_file() and path.suffix not in {".py", ".json"}:
            raise CatalogError(f"unexpected file: {path}")
    entry_file = safe_rel_path(f"{manifest['entry_module']}.py")
    if entry_file not in declared_files:
        raise CatalogError(f"{manifest_path} entry_module must resolve to a declared file: {entry_file}")

    if manifest.get("bundle_sha256") != bundle_sha256(manifest, provider_dir):
        raise CatalogError(f"{manifest_path} bundle_sha256 is stale")

    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("type") != "github":
        raise CatalogError(f"{manifest_path} source.type must be github")
    if source.get("repo") != "LavX/bazarr-provider-catalog":
        raise CatalogError(f"{manifest_path} source.repo must be LavX/bazarr-provider-catalog")
    if source.get("ref") != "main":
        raise CatalogError(f"{manifest_path} source.ref must be main")
    if not isinstance(source.get("commit"), str) or not _HEX_COMMIT_RE.match(source["commit"]):
        raise CatalogError(f"{manifest_path} source.commit must be a 40 char SHA placeholder or commit")
    expected_path = provider_dir.relative_to(root).as_posix()
    if safe_rel_path(source.get("path"), allow_python=False) != expected_path:
        raise CatalogError(f"{manifest_path} source.path must be {expected_path}")

    validate_dependency_lock(manifest_path, manifest.get("dependencies"))
    return manifest


def refresh_manifest_hashes(provider_dir):
    manifest = read_json(provider_dir / "provider.json")
    files = {}
    for path in sorted(provider_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(provider_dir).as_posix()
        files[rel] = file_sha256(path)
    manifest["files"] = files
    manifest["bundle_sha256"] = bundle_sha256(manifest, provider_dir)
    return manifest


def build_catalog(root, write_manifests=False):
    root = Path(root).resolve()
    seen_ids = set()
    manifests = []
    for provider_dir in discover_provider_dirs(root):
        manifest = refresh_manifest_hashes(provider_dir)
        if write_manifests:
            (provider_dir / "provider.json").write_text(dump_json(manifest), encoding="utf-8")
        manifests.append(validate_manifest(root, provider_dir, seen_ids))
    manifests.sort(key=lambda item: item["provider_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "name": CATALOG_NAME,
        "providers": [{"manifest": manifest} for manifest in manifests],
    }


def validate_catalog(root):
    root = Path(root).resolve()
    catalog_path = root / "catalog.json"
    catalog = read_json(catalog_path)
    expected = build_catalog(root)
    if catalog != expected:
        raise CatalogError("catalog.json is stale or invalid, run: python -m sdk build-catalog")
    return catalog


def load_provider_class(provider_dir, manifest):
    path = provider_dir / f"{manifest['entry_module']}.py"
    spec = importlib.util.spec_from_file_location("provider_under_test", path)
    if spec is None or spec.loader is None:
        raise CatalogError(f"cannot import provider module: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CatalogError(f"cannot import provider module {path}: {exc}") from exc
    cls = getattr(module, manifest["entry_class"], None)
    if cls is None:
        raise CatalogError(f"missing provider class: {manifest['entry_class']}")
    return cls


def _load_config(config_file=None, config_json=None, secret_specs=None, defaults=None):
    config = dict(defaults or {})
    if config_file:
        file_config = read_json(Path(config_file))
        if not isinstance(file_config, dict):
            raise CatalogError("--config-file must contain a JSON object")
        config.update(file_config)
    if config_json:
        try:
            inline_config = json.loads(config_json)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"--config-json must be a JSON object: {exc}") from exc
        if not isinstance(inline_config, dict):
            raise CatalogError("--config-json must be a JSON object")
        config.update(inline_config)
    for spec in secret_specs or []:
        if "=" not in spec:
            raise CatalogError("--secret must use FIELD=ENV_VAR")
        field, env_var = spec.split("=", 1)
        if not _CONFIG_KEY_RE.match(field):
            raise CatalogError(f"--secret field is invalid: {field}")
        if not env_var:
            raise CatalogError("--secret requires an environment variable name")
        if env_var not in os.environ:
            raise CatalogError(f"environment variable is not set: {env_var}")
        config[field] = os.environ[env_var]
    return config


def _load_video_fixtures(paths):
    if not paths:
        return [dict(item) for item in DEFAULT_SMOKE_VIDEOS]
    videos = []
    for path in paths:
        payload = read_json(Path(path))
        if isinstance(payload, dict):
            videos.append(payload)
        elif isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            videos.extend(payload)
        else:
            raise CatalogError(f"video fixture must be an object or object list: {path}")
    if not videos:
        raise CatalogError("at least one video fixture is required")
    return videos


def _language_payloads(language_codes):
    codes = language_codes or ["eng"]
    return [{"alpha3": code, "hi": False, "forced": False} for code in codes]


def _language_code_parts(language):
    alpha3 = str(language.get("alpha3") or "")
    country = str(language.get("country_alpha2") or language.get("country") or "").upper()
    if "-" in alpha3 and not country:
        alpha3, country = alpha3.split("-", 1)
        country = country.upper()
    return alpha3, country


def _language_matches_requested(candidate_language, requested_languages):
    if not isinstance(candidate_language, dict):
        return False
    candidate_alpha3 = candidate_language.get("alpha3")
    for requested_language in requested_languages:
        if isinstance(requested_language, dict) and candidate_alpha3 == requested_language.get("alpha3"):
            return True

    candidate_alpha3, candidate_country = _language_code_parts(candidate_language)
    for requested_language in requested_languages:
        if not isinstance(requested_language, dict):
            continue
        requested_alpha3, requested_country = _language_code_parts(requested_language)
        if candidate_alpha3 != requested_alpha3:
            continue
        if not requested_country:
            return True
        if candidate_country == requested_country:
            return True
    return False


def _assert_secret_not_leaked(config, secret_fields, payload, context):
    serialized = json.dumps(payload, sort_keys=True)
    for field in secret_fields:
        value = config.get(field)
        if isinstance(value, str) and value and value in serialized:
            raise CatalogError(f"secret field {field} leaked through {context}")


def smoke_test(
    root,
    provider_id=EXPECTED_SMOKE_PROVIDER_ID,
    config=None,
    videos=None,
    languages=None,
    expect_min_results=1,
    skip_download=False,
):
    root = Path(root).resolve()
    catalog = validate_catalog(root)
    manifest = next((item["manifest"] for item in catalog["providers"] if item["manifest"]["provider_id"] == provider_id), None)
    if manifest is None:
        raise CatalogError(f"provider not found in catalog: {provider_id}")

    provider_dir = root / manifest["source"]["path"]
    provider = load_provider_class(provider_dir, manifest)()
    is_official_smoke = provider_id == EXPECTED_SMOKE_PROVIDER_ID
    secret_fields = manifest.get("secret_fields") or []
    config = dict(config or {})
    videos = videos or [dict(item) for item in DEFAULT_SMOKE_VIDEOS]
    languages = languages or [dict(item) for item in DEFAULT_SMOKE_LANGUAGES]
    last_candidate = None
    for video in videos:
        try:
            results = provider.search(video=video, languages=languages, config=config)
        except Exception as exc:
            raise CatalogError(f"{provider_id} search failed: {exc}") from exc
        if len(results) < expect_min_results:
            raise CatalogError(f"{provider_id} returned {len(results)} candidates, expected at least {expect_min_results}")
        if is_official_smoke and len(results) != 1:
            raise CatalogError(f"{provider_id} returned {len(results)} candidates, expected 1")
        candidate = results[0]
        if candidate.get("provider") != provider_id:
            raise CatalogError(f"{provider_id} returned wrong provider id")
        if not _language_matches_requested(candidate.get("language"), languages):
            raise CatalogError(f"{provider_id} returned invalid language payload")
        if not isinstance(candidate.get("provider_payload"), dict):
            raise CatalogError(f"{provider_id} returned no provider_payload")
        _assert_secret_not_leaked(config, secret_fields, candidate, f"{provider_id} search")
        last_candidate = candidate

    if skip_download:
        return provider_id

    try:
        content = provider.download(last_candidate["provider_payload"], {"alpha3": "eng"}, config)
    except Exception as exc:
        raise CatalogError(f"{provider_id} download failed: {exc}") from exc
    if not isinstance(content, dict):
        raise CatalogError(f"{provider_id} download must return a content or archive payload")
    if "archive_b64" in content:
        # Archive payloads do not carry `empty`; the host extracts the member.
        names = _validate_archive_download(provider_id, content)
        if content.get("select_member"):
            _smoke_select_archive_member(provider_id, provider, last_candidate, names, config)
    elif content.get("empty") is False:
        data = base64.b64decode(content["content_b64"].encode("ascii"), validate=True)
        if hashlib.sha256(data).hexdigest() != content.get("content_sha256"):
            raise CatalogError(f"{provider_id} download hash mismatch")
        if is_official_smoke and data.decode("utf-8") != EXPECTED_SMOKE_SUBTITLE:
            raise CatalogError(f"{provider_id} download content does not match fixed SRT")
    else:
        raise CatalogError(f"{provider_id} download must return a content or archive payload")
    _assert_secret_not_leaked(config, secret_fields, content, f"{provider_id} download")
    return provider_id


def _validate_archive_download(provider_id, content):
    """Validate an archive-mode download payload.

    Providers may hand the raw archive bytes back to the Bazarr+ host, which extracts
    the member with its own rarfile/zipfile/7z stack. The SDK validates the shape offline:
    the bytes must be a real zip, rar, or 7z archive (direct subtitle bytes belong in
    content_b64), and a named member, if given, must exist. Full extraction is exercised
    host-side on a test server.
    """
    raw = base64.b64decode(content["archive_b64"].encode("ascii"), validate=True)
    expected = content.get("archive_sha256")
    if expected and hashlib.sha256(raw).hexdigest() != str(expected).lower():
        raise CatalogError(f"{provider_id} download archive_sha256 mismatch")
    stream = io.BytesIO(raw)
    is_rar = raw[:4] == b"Rar!"
    is_7z = raw[:6] == b"7z\xbc\xaf\x27\x1c"
    names = None
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
    elif not is_rar and not is_7z:
        # archive_b64 must be a real zip, rar, or 7z (the formats the host extracts). A
        # non-archive payload (empty bytes, an HTML/error page, or a bare subtitle stream)
        # would pass here but fail host-side extraction; direct subtitle bytes belong in
        # content_b64, not archive_b64. rar/7z are not listable with the stdlib, so they
        # leave names=None and skip the member/subtitle checks below (the host lists them).
        raise CatalogError(
            f"{provider_id} download archive_b64 is not a valid zip, rar, or 7z archive"
        )
    member = content.get("member")
    if member and names is not None and member not in names:
        raise CatalogError(f"{provider_id} download member is not in the archive: {member}")
    if names is not None and not any(
        name.lower().endswith((".srt", ".sub", ".ssa", ".ass", ".vtt")) for name in names
    ):
        raise CatalogError(f"{provider_id} download archive has no subtitle member")
    return names


def _smoke_select_archive_member(provider_id, provider, candidate, names, config):
    """Exercise the select_archive_member op that a select_member download asks the host
    to call after listing the archive members."""
    selector = getattr(provider, "select_archive_member", None)
    if selector is None:
        raise CatalogError(
            f"{provider_id} download set select_member but exposes no select_archive_member method"
        )
    members = list(names) if names is not None else []
    try:
        decision = selector(candidate["provider_payload"], {"alpha3": "eng"}, members, config)
    except Exception as exc:
        raise CatalogError(f"{provider_id} select_archive_member failed: {exc}") from exc
    if not isinstance(decision, dict) or decision.get("decision") not in ("pin", "defer", "reject"):
        raise CatalogError(
            f"{provider_id} select_archive_member must return {{'member', 'decision': pin|defer|reject}}"
        )
    chosen = decision.get("member")
    if decision["decision"] == "pin" and (chosen is None or (names is not None and chosen not in names)):
        raise CatalogError(
            f"{provider_id} select_archive_member pinned a member not in the archive: {chosen!r}"
        )


def command_validate(args):
    validate_catalog(args.root)
    print("catalog ok")
    return 0


def command_hash(args):
    provider_dir = Path(args.provider_dir).resolve()
    manifest = refresh_manifest_hashes(provider_dir)
    print(manifest["bundle_sha256"])
    return 0


def command_build_catalog(args):
    root = Path(args.root).resolve()
    catalog = build_catalog(root, write_manifests=not args.stdout)
    output = dump_json(catalog)
    if args.stdout:
        print(output, end="")
    else:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = root / output_path
        output_path.write_text(output, encoding="utf-8")
        try:
            display_path = output_path.relative_to(root)
        except ValueError:
            display_path = output_path
        print(f"wrote {display_path}")
    return 0


def command_smoke_test(args):
    defaults = DEFAULT_SMOKE_CONFIG if args.provider == EXPECTED_SMOKE_PROVIDER_ID else {}
    config = _load_config(args.config_file, args.config_json, args.secret, defaults=defaults)
    provider_id = smoke_test(
        args.root,
        args.provider,
        config=config,
        videos=_load_video_fixtures(args.video_fixture),
        languages=_language_payloads(args.language),
        expect_min_results=args.expect_min_results,
        skip_download=args.skip_download,
    )
    print(f"{provider_id} ok")
    return 0


def command_runtime_matrix(args):
    matrix = {
        "python_requires": SUPPORTED_PYTHON_REQUIRES,
        "python_versions": list(SUPPORTED_PYTHON_VERSIONS),
        "abi_tags": list(SUPPORTED_PYTHON_ABI_TAGS),
        "platform_tags": list(SUPPORTED_WHEEL_PLATFORM_TAGS),
        "wheel_coverage": WHEEL_COVERAGE_POLICY,
    }
    print(dump_json(matrix), end="")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="sdk", description="Provider Hub catalog SDK tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate catalog.json and provider manifests")
    validate.add_argument("root", nargs="?", default=".", help="catalog root directory")
    validate.set_defaults(func=command_validate)

    hash_command = subparsers.add_parser("hash", help="print a provider bundle SHA256")
    hash_command.add_argument("provider_dir", help="provider directory to hash")
    hash_command.set_defaults(func=command_hash)

    build = subparsers.add_parser("build-catalog", help="build catalog.json from providers/*/provider.json")
    build.add_argument("root", nargs="?", default=".", help="catalog root directory")
    build.add_argument("--output", default="catalog.json", help="output path relative to root")
    build.add_argument("--stdout", action="store_true", help="write generated catalog to stdout")
    build.set_defaults(func=command_build_catalog)

    smoke = subparsers.add_parser("smoke-test", help="run the deterministic smoke provider contract")
    smoke.add_argument("root", nargs="?", default=".", help="catalog root directory")
    smoke.add_argument("--provider", default=EXPECTED_SMOKE_PROVIDER_ID, help="provider id to smoke test")
    smoke.add_argument("--config-json", help="JSON object merged into the provider config")
    smoke.add_argument("--config-file", help="path to a JSON object merged into the provider config")
    smoke.add_argument(
        "--secret",
        action="append",
        default=[],
        metavar="FIELD=ENV_VAR",
        help="read a secret config field from an environment variable",
    )
    smoke.add_argument(
        "--video-fixture",
        action="append",
        default=[],
        help="JSON video fixture object, or list of objects, to use for search",
    )
    smoke.add_argument(
        "--language",
        action="append",
        default=[],
        help="language alpha3 code to request, default eng",
    )
    smoke.add_argument(
        "--expect-min-results",
        type=int,
        default=1,
        help="minimum expected candidates per video fixture",
    )
    smoke.add_argument("--skip-download", action="store_true", help="only run search")
    smoke.set_defaults(func=command_smoke_test)

    runtime_matrix = subparsers.add_parser(
        "runtime-matrix",
        help="print the Bazarr+ Provider Hub Python runtime matrix",
    )
    runtime_matrix.set_defaults(func=command_runtime_matrix)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
