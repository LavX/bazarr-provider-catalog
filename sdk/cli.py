import argparse
import base64
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 1
CATALOG_NAME = "Bazarr Provider Catalog"
API_VERSION = "bazarr.provider-hub.v1"
EXPECTED_SMOKE_PROVIDER_ID = "smokehub"
EXPECTED_SMOKE_SUBTITLE = """1
00:00:01,000 --> 00:00:02,500
SmokeHub deterministic subtitle.
"""
_HEX_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_HEX_COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ALLOWED_MEDIA = {"movie", "episode"}


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
    if manifest.get("api_version") != API_VERSION:
        raise CatalogError(f"{manifest_path} api_version must be {API_VERSION}")
    if not isinstance(manifest.get("config_schema"), dict):
        raise CatalogError(f"{manifest_path} config_schema must be an object")
    if not isinstance(manifest.get("secret_fields"), list) or any(not isinstance(item, str) for item in manifest["secret_fields"]):
        raise CatalogError(f"{manifest_path} secret_fields must be a string list")
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
    spec.loader.exec_module(module)
    cls = getattr(module, manifest["entry_class"], None)
    if cls is None:
        raise CatalogError(f"missing provider class: {manifest['entry_class']}")
    return cls


def smoke_test(root, provider_id=EXPECTED_SMOKE_PROVIDER_ID):
    root = Path(root).resolve()
    catalog = validate_catalog(root)
    manifest = next((item["manifest"] for item in catalog["providers"] if item["manifest"]["provider_id"] == provider_id), None)
    if manifest is None:
        raise CatalogError(f"provider not found in catalog: {provider_id}")

    provider_dir = root / manifest["source"]["path"]
    provider = load_provider_class(provider_dir, manifest)()
    for video in (
        {"kind": "movie", "title": "Smoke Movie"},
        {"kind": "episode", "series": "Smoke Show", "season": 1, "episode": 2},
    ):
        results = provider.search(video=video, languages=[{"alpha3": "eng", "hi": False, "forced": False}], config={})
        if len(results) != 1:
            raise CatalogError(f"{provider_id} returned {len(results)} candidates, expected 1")
        candidate = results[0]
        if candidate.get("provider") != provider_id:
            raise CatalogError(f"{provider_id} returned wrong provider id")
        if not isinstance(candidate.get("language"), dict) or candidate["language"].get("alpha3") != "eng":
            raise CatalogError(f"{provider_id} returned invalid language payload")
        if not isinstance(candidate.get("provider_payload"), dict):
            raise CatalogError(f"{provider_id} returned no provider_payload")

    content = provider.download(results[0]["provider_payload"], {"alpha3": "eng"}, {})
    if not isinstance(content, dict) or content.get("empty") is not False:
        raise CatalogError(f"{provider_id} download must return a content payload")
    data = base64.b64decode(content["content_b64"].encode("ascii"), validate=True)
    if hashlib.sha256(data).hexdigest() != content.get("content_sha256"):
        raise CatalogError(f"{provider_id} download hash mismatch")
    if data.decode("utf-8") != EXPECTED_SMOKE_SUBTITLE:
        raise CatalogError(f"{provider_id} download content does not match fixed SRT")
    return provider_id


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
    provider_id = smoke_test(args.root, args.provider)
    print(f"{provider_id} ok")
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
    smoke.set_defaults(func=command_smoke_test)
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
