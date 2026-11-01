#!/usr/bin/env python3
"""Copy a released Bazarr+ host's manifest validator into sdk/released_hosts/.

Reads a local LavX/bazarr clone at a tag or commit and writes, under
sdk/released_hosts/<name>/:

- __init__.py, manifest.py and migration.py from bazarr/provider_hub/, byte for byte
  except for the transformations recorded in environment.json. Two kinds are
  possible: lines left out with --drop-line, and the fixed rewrites in
  RELEASED_REWRITES, which apply only to a copy whose source blob is listed there.
- registry.py, a stand-in that registers no hub plugins
- _stand_in.py, written only when a rewrite imports it: the settings sections and
  built-in provider module names the rewritten imports read, taken from the same
  source as the rest of environment.json
- environment.json: the source ref and commit, the upstream git blob id of each
  file (source_blobs), the git blob id of each file as written (vendored_blobs),
  the transformations with the blob id before and after each one, the built-in
  provider module names and the settings sections, both read from the source with
  ast (the same names the running host derives)

    python3 scripts/snapshot_released_host.py --bazarr-repo ../bazarr --ref v2.6.2 --name bazarr_v2_6_2
"""

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPIED_FILES = ("__init__.py", "manifest.py", "migration.py")
REGISTRY_STUB = (
    "# Stand-in for bazarr/provider_hub/registry.py: the catalog check runs with no\n"
    "# hub plugins registered, so the built-in set is the host's own providers.\n"
    "_REGISTERED_PROVIDER_HUB_IDS: set[str] = set()\n"
)
STAND_IN_TEMPLATE = (
    "# Stands in for two upstream imports the vendored copy does not carry.\n"
    "from types import SimpleNamespace\n"
    "\n"
    "SETTINGS_SECTIONS = {sections!r}\n"
    "BUILT_IN_PROVIDER_MODULES = {modules!r}\n"
    "\n"
    "validators = [SimpleNamespace(names=(f\"{{section}}.enabled\",)) for section in SETTINGS_SECTIONS]\n"
    "\n"
    "\n"
    "class _ProviderRegistry:\n"
    "    def names(self):\n"
    "        return list(BUILT_IN_PROVIDER_MODULES)\n"
    "\n"
    "\n"
    "provider_registry = _ProviderRegistry()\n"
)
STAND_IN_IMPORT_PREFIX = "from ._stand_in import"
SKIPPED_PROVIDER_MODULES = {"__init__.py", "mixins.py", "utils.py"}
# Fixed rewrites, keyed by the git blob id of the released file they apply to.
# Each entry is (file, exact old line, new line). Released manifest.py at v2.7.0
# and v2.7.2 imports two Bazarr host modules at run time; the vendored copy
# imports _stand_in instead. Other releases (v2.6.x) have no entry and are copied
# unchanged.
RELEASED_REWRITES = {
    "17e2ff71b8c77f3122ba4841038569b68ca265da": (
        (
            "manifest.py",
            "        from app.config import validators as settings_validators",
            "        from ._stand_in import validators as settings_validators",
        ),
        (
            "manifest.py",
            "        from subliminal_patch.extensions import provider_registry",
            "        from ._stand_in import provider_registry",
        ),
    ),
}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def extract_settings_sections(config_source):
    """Top-level sections named by the `validators = [Validator('section.key', ...)]` list."""
    tree = ast.parse(config_source)
    sections = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "validators" for target in node.targets):
            continue
        for call in getattr(node.value, "elts", []):
            if (
                isinstance(call, ast.Call)
                and getattr(call.func, "id", None) == "Validator"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                section = call.args[0].value.split(".", 1)[0].strip().lower()
                if section:
                    sections.add(section)
    if not sections:
        raise SystemExit("no settings sections found in bazarr/app/config.py")
    return sorted(sections)


def registers_provider(module_source):
    """Mirror subliminal_patch/providers/__init__.py: a module registers under its
    own name when it exposes a public name ending in Provider (other than Provider)."""
    tree = ast.parse(module_source)
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).rsplit(".", 1)[-1])
    return any(name.endswith("Provider") and name != "Provider" and not name.startswith("_") for name in names)


def built_in_provider_modules(repo, ref):
    listing = git(repo, "ls-tree", "--name-only", f"{ref}:custom_libs/subliminal_patch/providers").decode()
    modules = []
    for filename in sorted(listing.split()):
        if not filename.endswith(".py") or filename in SKIPPED_PROVIDER_MODULES:
            continue
        source = git(repo, "show", f"{ref}:custom_libs/subliminal_patch/providers/{filename}")
        if registers_provider(source):
            modules.append(filename[:-3])
    return modules


def blob_id(data):
    """The id `git hash-object` gives these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def stand_in_source(settings_sections, provider_modules):
    return STAND_IN_TEMPLATE.format(
        sections=tuple(settings_sections),
        modules=tuple(provider_modules),
    )


def apply_edits(filename, data, edits):
    """Apply (file, old line, new line) edits in order. A new line of None drops the old line.

    Each old line must occur exactly once in the file as it stands at that step.
    Returns the edited bytes and one record per edit.
    """
    records = []
    for edit_file, old, new in edits:
        if edit_file != filename:
            continue
        lines = data.decode("utf-8").split("\n")
        matches = [index for index, line in enumerate(lines) if line == old]
        if len(matches) != 1:
            raise SystemExit(f"{filename}: expected exactly one line {old!r}, found {len(matches)}")
        index = matches[0]
        before = blob_id(data)
        if new is None:
            del lines[index]
        else:
            lines[index] = new
        data = "\n".join(lines).encode("utf-8")
        records.append({
            "file": filename,
            "action": "drop" if new is None else "rewrite",
            "line": index + 1,
            "old": old,
            "new": new,
            "blob_before": before,
            "blob_after": blob_id(data),
        })
    return data, records


def snapshot(repo, ref, name, drop_lines=()):
    """`drop_lines` is a list of "<file>:<exact line>" the maintainer asked to leave out."""
    target = ROOT / "sdk" / "released_hosts" / name
    target.mkdir(parents=True, exist_ok=True)
    commit = git(repo, "rev-parse", f"{ref}^{{commit}}").decode().strip()
    drop_edits = []
    for spec in drop_lines:
        wanted_file, _, wanted_line = spec.partition(":")
        drop_edits.append((wanted_file, wanted_line, None))
    source_blobs, vendored_blobs, transformations = {}, {}, []
    for filename in COPIED_FILES:
        path = f"bazarr/provider_hub/{filename}"
        data = git(repo, "show", f"{commit}:{path}")
        source_blobs[filename] = git(repo, "rev-parse", f"{commit}:{path}").decode().strip()
        edits = list(RELEASED_REWRITES.get(source_blobs[filename], ())) + drop_edits
        data, records = apply_edits(filename, data, edits)
        transformations.extend(records)
        (target / filename).write_bytes(data)
        vendored_blobs[filename] = blob_id(data)
    (target / "registry.py").write_text(REGISTRY_STUB, encoding="utf-8")
    provider_modules = built_in_provider_modules(repo, commit)
    settings_sections = extract_settings_sections(git(repo, "show", f"{commit}:bazarr/app/config.py"))
    if any(record["new"] and record["new"].lstrip().startswith(STAND_IN_IMPORT_PREFIX) for record in transformations):
        stand_in = stand_in_source(settings_sections, provider_modules).encode("utf-8")
        (target / "_stand_in.py").write_bytes(stand_in)
        vendored_blobs["_stand_in.py"] = blob_id(stand_in)
    environment = {
        "bazarr_ref": ref,
        "bazarr_commit": commit,
        "source_blobs": source_blobs,
        "vendored_blobs": vendored_blobs,
        "transformations": transformations,
        "built_in_provider_modules": provider_modules,
        "settings_sections": settings_sections,
    }
    (target / "environment.json").write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bazarr-repo", required=True, help="path to a LavX/bazarr clone")
    parser.add_argument("--ref", required=True, help="tag or commit to copy from")
    parser.add_argument("--name", required=True, help="package name under sdk/released_hosts/")
    parser.add_argument("--drop-line", action="append", default=[], metavar="FILE:LINE",
                        help="leave one exact line out of one copied file (recorded in environment.json)")
    args = parser.parse_args(argv)
    if not args.name.isidentifier():
        parser.error("--name must be a Python identifier")
    target = snapshot(Path(args.bazarr_repo), args.ref, args.name, args.drop_line)
    print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
