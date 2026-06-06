#!/usr/bin/env python3
"""Expand provider.json dependency hashes to cover the Bazarr+ worker runtime matrix.

Matrix: CPython 3.12/3.13/3.14 (cp312/cp313/cp314 + abi3) on manylinux glibc
x86_64 and aarch64, plus pure py3-none-any wheels. Pulls authoritative wheel
sha256 digests from the PyPI JSON API and unions them into each provider.json
(existing hashes are never dropped). After running, regenerate the catalog and
validate:

    python3 scripts/expand_wheel_hashes.py
    python3 -B -m sdk build-catalog
    python3 -B -m sdk validate

Packages that ship no aarch64 wheel on PyPI (for example py7zz) are reported as
coverage gaps; those need a dependency decision, not more hashes.
"""
import glob
import json
import os
import sys
import urllib.parse
import urllib.request

MATRIX_ABIS = {"cp312", "cp313", "cp314", "abi3"}
_cache = {}


def fetch(name, version):
    key = (name, version)
    if key not in _cache:
        url = "https://pypi.org/pypi/{}/{}/json".format(
            urllib.parse.quote(name), urllib.parse.quote(version)
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            _cache[key] = json.load(response)
    return _cache[key]


def wheel_tags(filename):
    parts = filename[:-4].split("-")
    return parts[-3], parts[-2], parts[-1]  # python tag, abi tag, platform tag


def is_target(files):
    """True if the package ships ABI- or platform-specific wheels."""
    for entry in files:
        filename = entry["filename"]
        if not filename.endswith(".whl"):
            continue
        _, abi, platform = wheel_tags(filename)
        if abi.startswith("cp") or abi == "abi3" or (abi == "none" and platform != "any"):
            return True
    return False


def select(files):
    """Return {filename: sha256} for wheels covering the worker matrix."""
    selected = {}
    for entry in files:
        filename = entry["filename"]
        if not filename.endswith(".whl"):
            continue
        pytag, abi, platform = wheel_tags(filename)
        pure_any = abi == "none" and platform == "any"
        is_linux = (
            ("x86_64" in platform or "aarch64" in platform)
            and "manylinux" in platform
            and "musllinux" not in platform
        )
        abi_ok = abi in MATRIX_ABIS or (abi == "none" and pytag.startswith("py3"))
        if pure_any or (is_linux and abi_ok):
            selected[filename] = entry["digests"]["sha256"]
    return selected


def has_aarch64(filenames):
    return any("aarch64" in wheel_tags(fn)[2] for fn in filenames)


def has_pure(filenames):
    return any(wheel_tags(fn)[1] == "none" and wheel_tags(fn)[2] == "any" for fn in filenames)


def main(root=".", strict=False):
    changed = []
    expanded = {}
    gaps = {}
    fetch_failures = []
    for manifest_path in sorted(glob.glob(os.path.join(root, "providers", "*", "provider.json"))):
        manifest = json.load(open(manifest_path))
        requirements = manifest.get("dependencies", {}).get("requirements", [])
        touched = False
        for requirement in requirements:
            name, version = requirement["name"], requirement["version"]
            try:
                data = fetch(name, version)
            except Exception as exc:  # noqa: BLE001 - report and continue
                print("  WARN fetch {}=={}: {}".format(name, version, exc), file=sys.stderr)
                fetch_failures.append((name, version, str(exc)))
                continue
            files = data["urls"]
            if not is_target(files):
                continue
            selected = select(files)
            if not selected:
                continue
            if not has_pure(selected) and not has_aarch64(selected):
                gaps[(name, version)] = "aarch64"
            merged = sorted({"sha256:" + digest for digest in selected.values()} | set(requirement["hashes"]))
            if merged != sorted(requirement["hashes"]):
                expanded.setdefault(name, (len(requirement["hashes"]), len(merged)))
                requirement["hashes"] = merged
                touched = True
        if touched:
            json.dump(manifest, open(manifest_path, "w"), indent=2, sort_keys=True)
            open(manifest_path, "a").write("\n")
            changed.append(os.path.basename(os.path.dirname(manifest_path)))
    print("providers changed: {}".format(len(changed)))
    for name, (before, after) in sorted(expanded.items()):
        print("  {}: {} -> {}".format(name, before, after))
    if gaps:
        print("\naarch64 coverage gaps (no aarch64 wheel on PyPI; needs a dependency decision):")
        for (name, version), missing in sorted(gaps.items()):
            print("  {}=={}: missing {}".format(name, version, missing))
    if fetch_failures:
        print(
            "\n{} dependency fetch(es) failed; wheel coverage was NOT verified.".format(
                len(fetch_failures)
            ),
            file=sys.stderr,
        )
        if strict:
            return 1
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", nargs="?", default=".", help="catalog root directory")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any PyPI fetch fails, so a network/lookup failure "
        "cannot silently pass the CI freshness gate",
    )
    args = parser.parse_args()
    sys.exit(main(args.root, strict=args.strict))
