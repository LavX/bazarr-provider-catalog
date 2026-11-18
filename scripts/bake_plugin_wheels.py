#!/usr/bin/env python3
"""Bake the wheels of each plugin's locked closure into its bundle, under ``plugins/<id>/wheels/``.

``plugin.json`` locks every dependency to an exact version with sha256 hashes. This script downloads
the wheels those hashes name from PyPI and writes them beside the plugin's code, so the bundle carries
what its environment is built from. A host that installs from the bundle then does not need PyPI.
Bazarr+ does not do that yet: it still installs the locked requirements from PyPI. A downloaded file
whose digest is not in the lock is refused.

For a dependency whose lock names a pure ``py3-none-any`` wheel, only that wheel is baked: it runs
everywhere. For one that has none, every wheel the lock names is baked, one per platform and Python.
Wheels the lock no longer names are removed. Afterwards refresh the manifest and check it:

    python3 scripts/bake_plugin_wheels.py
    python3 -B -m sdk build-catalog
    python3 -B -m sdk validate

Pass plugin ids to bake only those.
"""
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def release_files(name, version):
    url = "https://pypi.org/pypi/{}/{}/json".format(urllib.parse.quote(name), urllib.parse.quote(version))
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)["urls"]


def wanted_wheels(requirement, files):
    """The wheel files of the release that the lock names: the pure one alone, else all of them."""
    locked = {digest.removeprefix("sha256:") for digest in requirement["hashes"]}
    named = [f for f in files if f["filename"].endswith(".whl") and f["digests"]["sha256"] in locked]
    pure = [f for f in named if f["filename"].endswith("-py3-none-any.whl")]
    return pure or named


def bake(plugin_dir):
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    target = plugin_dir / "wheels"
    target.mkdir(exist_ok=True)
    keep = set()
    for requirement in manifest.get("dependencies", {}).get("requirements", []):
        wheels = wanted_wheels(requirement, release_files(requirement["name"], requirement["version"]))
        if not wheels:
            raise SystemExit("{} {}: no wheel on PyPI matches the locked hashes".format(requirement["name"], requirement["version"]))
        for wheel in wheels:
            path = target / wheel["filename"]
            keep.add(path.name)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == wheel["digests"]["sha256"]:
                continue
            with urllib.request.urlopen(wheel["url"], timeout=120) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
                raise SystemExit("{}: the download does not match its published digest".format(wheel["filename"]))
            path.write_bytes(data)
            print("baked", path.relative_to(plugin_dir.parent.parent))
    for stale in sorted(target.glob("*.whl")):
        if stale.name not in keep:
            stale.unlink()
            print("removed", stale.relative_to(plugin_dir.parent.parent))
    if not keep:
        target.rmdir()


def main(argv):
    root = Path(__file__).resolve().parents[1]
    plugin_dirs = sorted(path.parent for path in (root / "plugins").glob("*/plugin.json"))
    if argv:
        plugin_dirs = [path for path in plugin_dirs if path.name in argv]
        missing = sorted(set(argv) - {path.name for path in plugin_dirs})
        if missing:
            raise SystemExit("no such plugin: " + ", ".join(missing))
    for plugin_dir in plugin_dirs:
        bake(plugin_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
