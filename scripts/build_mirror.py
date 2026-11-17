#!/usr/bin/env python3
"""Build the static GitHub Pages mirror of the provider catalog from git history.

For every channel (a branch) the last RETENTION first-parent commits are
published under ``v1/<channel>/<commit>/``. These are the paths the Bazarr+ client asks
for, and nothing else:

    catalog.json                         byte-for-byte the git blob of that commit
    signed/<channel>/catalog.json        the signed body, when the commit has one
    signed/<channel>/catalog.json.sig    its detached envelope, when the commit has one
    keys/catalog-signing-keys.json       the root-signed key file, when the commit has one
    <source.path>/<file>                 every bundle file the commit's catalog.json or
                                         plugins/<id>/plugin.json manifests pin

The signed files are opaque bytes: nothing here parses or checks a signature. A signed body
lags its branch head by design and names the commit its bundles were built from
(``source_commit``), so the client reads the bundle files at that commit. Those files are
published under that commit by the pins read from its own tree, and ``verify_mirror.py``
checks that a head's signed body can be fully served that way.

``v1/<channel>/current`` is a small JSON pointer:

    {"channel", "commit", "catalog_sha256", "published_at", "history"}

``history`` lists the retained commits, newest first, so ``history[0]`` is
``commit``. Clients can use it to prove that a pointer is strictly ahead of a
commit they already hold.

Retention contract (``verify_mirror.py`` recomputes it from git independently): the published
history of a channel is the RETENTION newest first-parent commits ending at the pointer
commit, minus exactly those commits that fail validation. Validation failures are
deterministic properties of the commit: no or malformed catalog.json, an unsafe or clashing
pinned path, a pinned file that is missing, not a blob or does not match its pin, and the
per-file, per-commit and pin-count bounds. An older commit failing validation is dropped
(never replaced by an even older one); the newest commit failing it fails the build. Any other
error (git failure, I/O, bad output) fails the build, so a healthy commit is never omitted.

The site is rebuilt from git on every run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RETENTION = 20
CHANNELS = {"main": "origin/main", "beta": "origin/beta"}
KEY_FILE_PATH = "keys/catalog-signing-keys.json"
PLUGINS_DIR = "plugins"
PLUGIN_MANIFEST = "plugin.json"


def opaque_paths(channel: str) -> list[str]:
    """The signed files the client reads per commit, published as opaque bytes when present."""
    return [f"signed/{channel}/catalog.json", f"signed/{channel}/catalog.json.sig", KEY_FILE_PATH]

MAX_FILES = 50_000
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SITE_BYTES = 600 * 1024 * 1024
MAX_PINS = 5_000
MAX_COMMIT_BYTES = 64 * 1024 * 1024

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MirrorError(Exception):
    """Any failure that must stop the build or the verification."""


class ValidationError(MirrorError):
    """A commit's content fails validation. Deterministic from git, so verify recomputes it."""


def git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "--no-pager", "-C", str(repo), *args],
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip()
        raise MirrorError(f"git {' '.join(args)} failed: {detail}") from exc


def retained_commits(repo: Path, ref: str, limit: int = RETENTION) -> list[str]:
    out = git(repo, "rev-list", "--first-parent", "-n", str(limit), ref).decode().split()
    if not out:
        raise MirrorError(f"no commits found for {ref}")
    for commit in out:
        if not _COMMIT_RE.fullmatch(commit):
            raise MirrorError(f"unexpected rev-list output for {ref}: {commit!r}")
    return out


def object_info(repo: Path, commit: str, paths: list[str]) -> dict[str, tuple[str, int] | None]:
    """Return ``{path: (type, size)}`` (None when missing) without reading any content."""
    if not paths:
        return {}
    query = "".join(f"{commit}:{path}\n" for path in paths)
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-C", str(repo), "cat-file", "--batch-check"],
            input=query.encode(),
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise MirrorError(f"git cat-file --batch-check failed: {exc.stderr.decode('utf-8', 'replace').strip()}") from exc
    lines = result.stdout.decode().splitlines()
    if len(lines) != len(paths):
        raise MirrorError(f"{commit}: unexpected cat-file --batch-check output")
    info: dict[str, tuple[str, int] | None] = {}
    for path, line in zip(paths, lines):
        parts = line.split()
        if len(parts) == 2 and parts[1] == "missing":
            info[path] = None
        elif len(parts) == 3 and parts[2].isdigit():
            info[path] = (parts[1], int(parts[2]))
        else:
            raise MirrorError(f"{commit}: unexpected cat-file output for {path}: {line!r}")
    return info


def blob_or_none(repo: Path, commit: str, path: str, max_bytes: int | None = None) -> bytes | None:
    """Return the blob at ``commit:path``, or None when the path does not exist.

    Type and size are read first, so an oversized or non-blob object is refused before any
    content is loaded.
    """
    limit = MAX_FILE_BYTES if max_bytes is None else max_bytes
    entry = object_info(repo, commit, [path])[path]
    if entry is None:
        return None
    kind, size = entry
    if kind != "blob":
        raise ValidationError(f"{commit}: {path} is a {kind}, not a file")
    if size > limit:
        raise ValidationError(f"{commit}: {path} exceeds {limit} bytes")
    return git(repo, "cat-file", "blob", f"{commit}:{path}")


def safe_relative_path(path: str) -> str:
    """Accept only plain forward-slash relative paths (no empty, ``.`` or ``..`` segments)."""
    if not isinstance(path, str) or not path:
        raise ValidationError(f"unsafe path: {path!r}")
    for segment in path.split("/"):
        if not _SEGMENT_RE.fullmatch(segment) or segment in {".", ".."} or ".." in segment:
            raise ValidationError(f"unsafe path: {path!r}")
    return path


def _manifest_of(entry):
    """The manifest of a catalog entry: ``{"manifest": {...}}``, or a bare manifest."""
    if isinstance(entry, dict) and isinstance(entry.get("manifest"), dict):
        return entry["manifest"]
    return entry


def _add_pins(pins: dict[str, str], entries, count: int) -> int:
    """Add the ``<source.path>/<file>`` pins of every manifest in ``entries``; return the running count."""
    for entry in entries:
        try:
            manifest = _manifest_of(entry)
            source_path = manifest["source"].get("path", "")
            files = manifest["files"]
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValidationError(f"malformed entry: {exc}") from exc
        if not isinstance(files, dict) or not isinstance(source_path, str):
            raise ValidationError("entry files or source path has the wrong type")
        count += len(files)
        if count > MAX_PINS:
            raise ValidationError(f"catalog pins more than {MAX_PINS} files")
        for name, digest in files.items():
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise ValidationError(f"bad sha256 pin for {name!r}")
            full = safe_relative_path("/".join(part for part in (source_path, name) if part))
            if pins.setdefault(full, digest) != digest:
                raise ValidationError(f"conflicting pins for {full}")
    return count


def _check_pin_paths(pins: dict[str, str], reserved: set[str]) -> None:
    taken = set(pins) | reserved
    for path in pins:
        if path in reserved:
            raise ValidationError(f"pinned file collides with a mirror file: {path}")
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        while parent:
            if parent in taken:
                raise ValidationError(f"{parent} is pinned as a file and also used as a directory by {path}")
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""


def catalog_pins(
    catalog_bytes: bytes, plugin_manifests: list | None = None, reserved: set[str] | None = None
) -> dict[str, str]:
    """Map each pinned bundle file (``<source.path>/<file>``) to its sha256.

    Pins come from the providers of ``catalog.json`` and from the given plugin manifests. The same
    path pinned to two different hashes is a catalog error, and so is a pin on a path the mirror
    writes itself (``reserved``, by default only ``catalog.json``).
    """
    try:
        catalog = json.loads(catalog_bytes)
        providers = catalog["providers"]
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ValidationError(f"catalog.json is not a catalog: {exc}") from exc
    if not isinstance(providers, list):
        raise ValidationError("catalog.json providers is not a list")
    pins: dict[str, str] = {}
    count = _add_pins(pins, providers, 0)
    _add_pins(pins, plugin_manifests or [], count)
    _check_pin_paths(pins, {"catalog.json"} if reserved is None else reserved)
    return pins


def signed_body_pins(body_bytes: bytes) -> tuple[str, dict[str, str]]:
    """The ``source_commit`` of a signed body and the bundle files its providers and plugins pin."""
    try:
        body = json.loads(body_bytes)
        source_commit = body["source_commit"]
        providers, plugins = body["providers"], body.get("plugins", [])
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ValidationError(f"signed catalog is not a catalog: {exc}") from exc
    if not isinstance(source_commit, str) or not _COMMIT_RE.fullmatch(source_commit):
        raise ValidationError("signed catalog source_commit is not a commit")
    if not isinstance(providers, list) or not isinstance(plugins, list):
        raise ValidationError("signed catalog providers or plugins is not a list")
    pins: dict[str, str] = {}
    count = _add_pins(pins, providers, 0)
    _add_pins(pins, plugins, count)
    return source_commit, pins


def plugin_manifests(repo: Path, commit: str) -> list[dict]:
    """The ``plugins/<id>/plugin.json`` manifests of one commit, sorted by directory name."""
    raw = git(repo, "ls-tree", "-z", "-d", "--name-only", commit, f"{PLUGINS_DIR}/")
    names = sorted(entry.decode("utf-8", "replace") for entry in raw.split(b"\0") if entry)
    paths = [f"{safe_relative_path(name)}/{PLUGIN_MANIFEST}" for name in names]
    info = object_info(repo, commit, paths)
    manifests = []
    for path in paths:
        entry = info[path]
        if entry is None:
            continue
        data = blob_or_none(repo, commit, path)
        try:
            manifest = json.loads(data)
        except (ValueError, RecursionError) as exc:
            raise ValidationError(f"{commit}: {path} is not JSON: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ValidationError(f"{commit}: {path} is not an object")
        manifests.append(manifest)
    return manifests


def commit_files(repo: Path, commit: str, channel: str) -> dict[str, bytes]:
    """Collect every file published for one commit, verifying each bundle file against its pin.

    Raises ValidationError for content problems and MirrorError for everything else.
    """
    catalog = blob_or_none(repo, commit, "catalog.json")
    if catalog is None:
        raise ValidationError(f"{commit}: no catalog.json")
    opaque = opaque_paths(channel)
    pins = catalog_pins(catalog, plugin_manifests(repo, commit), {"catalog.json", *opaque})
    paths = opaque + sorted(pins)
    info = object_info(repo, commit, paths)
    total = len(catalog)
    for path in paths:
        entry = info[path]
        if entry is None:
            if path in pins:
                raise ValidationError(f"{commit}: pinned file missing: {path}")
            continue
        kind, size = entry
        if kind != "blob":
            raise ValidationError(f"{commit}: {path} is a {kind}, not a file")
        if size > MAX_FILE_BYTES:
            raise ValidationError(f"{commit}: {path} exceeds {MAX_FILE_BYTES} bytes")
        total += size
        if total > MAX_COMMIT_BYTES:
            raise ValidationError(f"{commit}: files exceed {MAX_COMMIT_BYTES} bytes in total")
    files: dict[str, bytes] = {"catalog.json": catalog}
    for path in paths:
        if info[path] is None:
            continue
        data = git(repo, "cat-file", "blob", f"{commit}:{path}")
        if path in pins and hashlib.sha256(data).hexdigest() != pins[path]:
            raise ValidationError(f"{commit}: {path} does not match its pin")
        files[path] = data
    return files


def build_site(
    repo: Path,
    out: Path,
    channels: dict[str, str] | None = None,
    retention: int = RETENTION,
    now: datetime | None = None,
    max_files: int = MAX_FILES,
    max_site_bytes: int = MAX_SITE_BYTES,
    log=lambda message: print(message, file=sys.stderr),
) -> dict[str, dict]:
    """Write the site under ``out`` and return the pointer document per channel."""
    channels = channels or CHANNELS
    if out.exists() and any(out.iterdir()):
        raise MirrorError(f"output directory is not empty: {out}")
    published_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_files = 0
    total_bytes = 0
    pointers: dict[str, dict] = {}

    for channel, ref in channels.items():
        safe_relative_path(channel)
        kept: list[str] = []
        for index, commit in enumerate(retained_commits(repo, ref, retention)):
            try:
                files = commit_files(repo, commit, channel)
            except ValidationError as exc:
                # The head must be publishable. An older commit that is not is dropped
                # from the window, which only makes the history shorter.
                if index == 0:
                    raise
                log(f"skipping {channel}/{commit}: {exc}")
                continue
            total_files += len(files)
            total_bytes += sum(len(data) for data in files.values())
            if total_files + len(channels) > max_files:
                raise MirrorError(f"site exceeds {max_files} files")
            if total_bytes > max_site_bytes:
                raise MirrorError(f"site exceeds {max_site_bytes} bytes")
            base = out / "v1" / channel / commit
            for path, data in files.items():
                target = base / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            kept.append(commit)
            if index == 0:
                head_catalog = files["catalog.json"]
        pointer = {
            "channel": channel,
            "commit": kept[0],
            "catalog_sha256": hashlib.sha256(head_catalog).hexdigest(),
            "published_at": published_at,
            "history": kept,
        }
        pointer_path = out / "v1" / channel / "current"
        pointer_path.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pointers[channel] = pointer
        log(f"{channel}: {len(kept)} commits, head {kept[0]}")
    log(f"site: {total_files} files, {total_bytes} bytes")
    return pointers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--retention", type=int, default=RETENTION)
    parser.add_argument("--outputs-file", type=Path, help="append heads=<json> here (GITHUB_OUTPUT)")
    args = parser.parse_args(argv)
    try:
        pointers = build_site(args.repo, args.out, retention=args.retention)
    except MirrorError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    if args.outputs_file:
        heads = {channel: pointer["commit"] for channel, pointer in pointers.items()}
        with args.outputs_file.open("a", encoding="utf-8") as handle:
            handle.write(f"heads={json.dumps(heads, sort_keys=True)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
