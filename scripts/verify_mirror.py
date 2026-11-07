#!/usr/bin/env python3
"""Verify the deployed Pages mirror against git.

For every channel this fetches ``v1/<channel>/current`` and then, for each commit in its
history, the published ``catalog.json`` (and ``catalog.json.sig`` when git has one) and
every bundle file the catalog pins. The checks are:

* each published ``catalog.json`` is byte-identical (sha256) to ``git cat-file blob
  <commit>:catalog.json``;
* the pointer's ``catalog_sha256`` matches its head catalog, and ``history[0]`` is ``commit``;
* each bundle file matches the sha256 pinned by the git blob of the catalog;
* ``history`` is an ordered subsequence of the first-parent chain of ``origin/<channel>``;
* the pointer reached the commit this run built (or a newer one).

Every request carries a unique cache-buster query and ``Cache-Control: no-cache`` so the CDN
cannot answer from an old copy. Any mismatch makes the exit status non-zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from build_mirror import (
    MAX_FILE_BYTES,
    OPAQUE_FILES,
    RETENTION,
    MirrorError,
    blob_or_none,
    catalog_pins,
    git,
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_POINTER_KEYS = {"channel", "commit", "catalog_sha256", "published_at", "history"}


class FetchError(Exception):
    pass


def http_fetcher(base_url: str, retries: int = 3, backoff: float = 2.0, timeout: float = 30.0):
    """Return ``fetch(path) -> bytes`` for a Pages site, with a cache-buster on every request."""
    parsed = urllib.parse.urlsplit(base_url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise MirrorError(f"base url must be https: {base_url!r}")
    base = base_url if base_url.endswith("/") else base_url + "/"

    def fetch(path: str) -> bytes:
        last = "no attempt"
        for attempt in range(retries):
            url = f"{base}{urllib.parse.quote(path)}?cb={uuid.uuid4().hex}"
            request = urllib.request.Request(
                url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"}
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = response.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise FetchError(f"{path}: larger than {MAX_FILE_BYTES} bytes")
                return data
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = str(exc)
                if attempt + 1 < retries:
                    time.sleep(backoff * (attempt + 1))
        raise FetchError(f"{path}: {last}")

    return fetch


def parse_pointer(channel: str, raw: bytes) -> dict:
    try:
        pointer = json.loads(raw)
    except ValueError as exc:
        raise MirrorError(f"{channel}: current is not JSON: {exc}") from exc
    if not isinstance(pointer, dict) or set(pointer) != _POINTER_KEYS:
        raise MirrorError(f"{channel}: current has unexpected keys")
    history = pointer["history"]
    if (
        pointer["channel"] != channel
        or not isinstance(pointer["commit"], str)
        or not _COMMIT_RE.match(pointer["commit"])
        or not isinstance(pointer["catalog_sha256"], str)
        or not _SHA256_RE.match(pointer["catalog_sha256"])
        or not isinstance(pointer["published_at"], str)
        or not isinstance(history, list)
        or not 1 <= len(history) <= RETENTION
        or len(set(history)) != len(history)
        or not all(isinstance(c, str) and _COMMIT_RE.match(c) for c in history)
        or history[0] != pointer["commit"]
    ):
        raise MirrorError(f"{channel}: current is malformed")
    return pointer


def fetch_pointer(fetch, channel: str, expected: str | None, attempts: int = 6, delay: float = 10.0) -> dict:
    """Fetch ``current`` until it shows ``expected`` (or a newer deploy), retrying for CDN lag."""
    problem = "unknown"
    for attempt in range(attempts):
        try:
            pointer = parse_pointer(channel, fetch(f"v1/{channel}/current"))
            if expected is None or expected in pointer["history"]:
                return pointer
            problem = f"{channel}: current is at {pointer['commit']}, expected {expected}"
        except FetchError as exc:
            problem = f"{channel}: {exc}"
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise MirrorError(problem)


def check_history(repo: Path, channel: str, ref: str, pointer: dict) -> str | None:
    """Check the published history against git; return a problem or None.

    ``history`` must be an ordered (newest first) subsequence of the first-parent chain of
    ``ref``, starting at ``commit``. A build may drop an older commit that failed its pins,
    so entries need not be adjacent, but order and membership are exact. This also rules
    out commits that are only reachable from another channel.
    """
    try:
        chain = git(repo, "rev-list", "--first-parent", ref).decode().split()
    except MirrorError as exc:
        return f"{channel}: cannot read {ref}: {exc}"
    position = {commit: index for index, commit in enumerate(chain)}
    last = -1
    for commit in pointer["history"]:
        index = position.get(commit)
        if index is None:
            return f"{channel}: history entry {commit} is not on the first-parent chain of {ref}"
        if index <= last:
            return f"{channel}: history is out of order at {commit}"
        last = index
    if pointer["history"][0] != pointer["commit"]:
        return f"{channel}: history does not start at the pointer commit"
    return None


def verify_channel(fetch, repo: Path, channel: str, expected: str | None = None,
                   attempts: int = 6, delay: float = 10.0, workers: int = 16,
                   ref: str | None = None) -> list[str]:
    """Return the list of problems found for one channel (empty means verified)."""
    try:
        pointer = fetch_pointer(fetch, channel, expected, attempts, delay)
    except MirrorError as exc:
        return [str(exc)]
    problem = check_history(repo, channel, ref or f"origin/{channel}", pointer)
    if problem:
        return [problem]

    errors: list[str] = []
    jobs: list[tuple[str, str, str, bytes | None]] = []  # (label, url path, kind, git bytes / digest)

    for index, commit in enumerate(pointer["history"]):
        base = f"v1/{channel}/{commit}"
        git_catalog = blob_or_none(repo, commit, "catalog.json")
        if git_catalog is None:
            errors.append(f"{channel}/{commit}: no catalog.json in git")
            continue
        if index == 0 and hashlib.sha256(git_catalog).hexdigest() != pointer["catalog_sha256"]:
            errors.append(f"{channel}/{commit}: pointer catalog_sha256 differs from the git blob")
        jobs.append((f"{channel}/{commit}/catalog.json", f"{base}/catalog.json", "bytes", git_catalog))
        for name in OPAQUE_FILES:
            data = blob_or_none(repo, commit, name)
            if data is not None:
                jobs.append((f"{channel}/{commit}/{name}", f"{base}/{name}", "bytes", data))
        try:
            pins = catalog_pins(git_catalog)
        except MirrorError as exc:
            errors.append(f"{channel}/{commit}: {exc}")
            continue
        for path, digest in sorted(pins.items()):
            jobs.append((f"{channel}/{commit}/{path}", f"{base}/{path}", "sha256", digest.encode()))

    def run(job):
        label, url_path, kind, want = job
        try:
            got = fetch(url_path)
        except FetchError as exc:
            return f"{label}: {exc}"
        if kind == "bytes" and got != want:
            return f"{label}: published bytes differ from the git blob"
        if kind == "sha256" and hashlib.sha256(got).hexdigest().encode() != want:
            return f"{label}: published bytes do not match the catalog pin"
        return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        errors.extend(result for result in pool.map(run, jobs) if result)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="Pages site URL, e.g. https://host/repo/")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--heads", default="{}", help='JSON {"channel": "commit"} the build produced')
    parser.add_argument("--ref-template", default="origin/{channel}",
                        help="git ref that holds each channel's history")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        heads = json.loads(args.heads)
        fetch = http_fetcher(args.base_url)
    except (ValueError, MirrorError) as exc:
        print(f"verify failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(heads, dict) or not heads:
        print("verify failed: --heads must name at least one channel", file=sys.stderr)
        return 1
    failed = False
    for channel, expected in sorted(heads.items()):
        problems = verify_channel(fetch, args.repo, channel, expected, args.attempts, args.delay,
                                  ref=args.ref_template.format(channel=channel))
        for problem in problems[:50]:
            print(f"MISMATCH {problem}", file=sys.stderr)
        if len(problems) > 50:
            print(f"... and {len(problems) - 50} more", file=sys.stderr)
        print(f"{channel}: {'FAILED, ' + str(len(problems)) + ' problems' if problems else 'verified'}")
        failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
