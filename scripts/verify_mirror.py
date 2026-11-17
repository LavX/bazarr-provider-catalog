#!/usr/bin/env python3
"""Verify the deployed Pages mirror against git.

For every channel this fetches ``v1/<channel>/current`` and then, for each commit in its
history, every file the build publishes for that commit: ``catalog.json``, the signed trio
(``signed/<channel>/catalog.json``, its ``.sig`` and ``keys/catalog-signing-keys.json``) when git
has them, and every bundle file the commit's manifests pin. The checks are:

* each published file is byte-identical (sha256) to ``git cat-file blob <commit>:<path>``;
* the pointer's ``catalog_sha256`` matches its head catalog, and ``history[0]`` is ``commit``;
* each bundle file matches the sha256 pinned by the git blobs;
* ``history`` equals the retained history recomputed from git (see below);
* the pointer reached the commit this run built (or a newer one);
* when the head carries a signed body, the commit it names as ``source_commit`` is published
  and carries every bundle file the body pins, with the pinned bytes.

Retention contract: the expected history is the RETENTION newest first-parent commits ending
at the pointer commit (which must lie on the first-parent chain of ``origin/<channel>``), minus
exactly the commits that fail the build's validation, which is recomputed here with the same
code. Omitting a healthy commit, listing an extra one, or reordering fails. See build_mirror.py.

Every request carries a unique cache-buster query and ``Cache-Control: no-cache`` so the CDN
cannot answer from an old copy. Any mismatch makes the exit status non-zero.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
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
    ValidationError,
    commit_files,
    opaque_paths,
    RETENTION,
    MirrorError,
    git,
    signed_body_pins,
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_POINTER_KEYS = {"channel", "commit", "catalog_sha256", "published_at", "history"}


class FetchError(Exception):
    pass


class NotFound(FetchError):
    """The site answered 404: the file is not published."""


def http_fetcher(base_url: str, retries: int = 3, backoff: float = 2.0, timeout: float = 30.0):
    """Return ``fetch(path) -> bytes`` for a Pages site, with a cache-buster on every request."""
    parsed = urllib.parse.urlsplit(base_url)
    loopback = parsed.hostname in {"127.0.0.1", "localhost"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise MirrorError(f"base url must be https: {base_url!r}")
    base = base_url if base_url.endswith("/") else base_url + "/"

    def fetch(path: str, retry_not_found: bool = True) -> bytes:
        """Fetch one file. A 404 is retried like any other failure (a fresh deploy can lag) unless
        ``retry_not_found`` is false, and it ends as ``NotFound`` so a caller can tell it apart."""
        last = "no attempt"
        missing = False
        for attempt in range(retries):
            url = f"{base}{urllib.parse.quote(path)}?cb={uuid.uuid4().hex}"
            request = urllib.request.Request(
                url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"}
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = response.read(MAX_FILE_BYTES + 1)
                    declared = response.headers.get("Content-Length")
                if declared is not None and declared.isdigit() and len(data) < int(declared) <= MAX_FILE_BYTES:
                    # A bounded read returns what arrived when the connection drops.
                    raise http.client.IncompleteRead(data, int(declared) - len(data))
                if len(data) > MAX_FILE_BYTES:
                    raise FetchError(f"{path}: larger than {MAX_FILE_BYTES} bytes")
                return data
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                # OSError covers timeouts, resets and TLS failures raised while reading the body.
                last = str(exc)
                missing = isinstance(exc, urllib.error.HTTPError) and exc.code == 404
                if isinstance(exc, urllib.error.HTTPError):
                    exc.close()
                if missing and not retry_not_found:
                    break
                if attempt + 1 < retries:
                    time.sleep(backoff * (attempt + 1))
        raise (NotFound if missing else FetchError)(f"{path}: {last}")

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
        or not _COMMIT_RE.fullmatch(pointer["commit"])
        or not isinstance(pointer["catalog_sha256"], str)
        or not _SHA256_RE.fullmatch(pointer["catalog_sha256"])
        or not isinstance(pointer["published_at"], str)
        or not isinstance(history, list)
        or not 1 <= len(history) <= RETENTION
        or len(set(history)) != len(history)
        or not all(isinstance(c, str) and _COMMIT_RE.fullmatch(c) for c in history)
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


def check_history(repo: Path, channel: str, ref: str, pointer: dict, retention: int = RETENTION) -> str | None:
    """Recompute the retained history from git and require an exact match; return a problem or None."""
    try:
        chain = git(repo, "rev-list", "--first-parent", ref).decode().split()
    except MirrorError as exc:
        return f"{channel}: cannot read {ref}: {exc}"
    position = {commit: index for index, commit in enumerate(chain)}
    if pointer["commit"] not in position:
        return f"{channel}: commit {pointer['commit']} is not on the first-parent chain of {ref}"
    last = -1
    for commit in pointer["history"]:
        index = position.get(commit)
        if index is None:
            return f"{channel}: history entry {commit} is not on the first-parent chain of {ref}"
        if index <= last:
            return f"{channel}: history is out of order at {commit}"
        last = index
    try:
        window = git(repo, "rev-list", "--first-parent", "-n", str(retention), pointer["commit"]).decode().split()
        expected = []
        for index, commit in enumerate(window):
            try:
                commit_files(repo, commit, channel)
            except ValidationError as exc:
                if index == 0:
                    return f"{channel}: pointer commit fails validation: {exc}"
                continue
            expected.append(commit)
    except MirrorError as exc:
        return f"{channel}: cannot recompute the retained history: {exc}"
    if pointer["history"] != expected:
        missing = [c for c in expected if c not in pointer["history"]]
        extra = [c for c in pointer["history"] if c not in expected]
        return f"{channel}: history differs from the retained history in git (missing {missing}, unexpected {extra})"
    return None


def check_signed_body(repo: Path, channel: str, pointer: dict, signed: bytes) -> list[str]:
    """Check that the head's signed body can be served: the commit it names carries its bundle files.

    The client reads a signed body at the head and its bundle files at the body's ``source_commit``,
    which can be an older commit. That commit has to be published, and each file the body pins has to
    be published there with the pinned bytes. The published bytes of every file are fetched and compared
    with git in the per-commit jobs, so this only compares the body's pins with what git holds there.
    """
    try:
        source_commit, pins = signed_body_pins(signed)
    except ValidationError as exc:
        return [f"{channel}/{pointer['commit']}: {exc}"]
    if source_commit not in pointer["history"]:
        return [f"{channel}/{pointer['commit']}: the signed catalog names {source_commit}, which the mirror does not carry"]
    try:
        files = commit_files(repo, source_commit, channel)
    except ValidationError as exc:
        return [f"{channel}/{source_commit}: {exc}"]
    problems = []
    for path, digest in sorted(pins.items()):
        data = files.get(path)
        if data is None or hashlib.sha256(data).hexdigest() != digest:
            problems.append(f"{channel}/{source_commit}/{path}: the signed catalog pins a file the mirror does not carry as pinned")
    return problems


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
    jobs: list[tuple[str, str, str, str]] = []  # (label, url path, kind, expected sha256)
    opaque = {"catalog.json", *opaque_paths(channel)}
    head_files: dict[str, bytes] = {}

    for index, commit in enumerate(pointer["history"]):
        try:
            files = commit_files(repo, commit, channel)
        except ValidationError as exc:
            errors.append(f"{channel}/{commit}: {exc}")
            continue
        if index == 0:
            head_files = files
            if hashlib.sha256(files["catalog.json"]).hexdigest() != pointer["catalog_sha256"]:
                errors.append(f"{channel}/{commit}: pointer catalog_sha256 differs from the git blob")
        for path, data in sorted(files.items()):
            kind = "bytes" if path in opaque else "pin"
            jobs.append((f"{channel}/{commit}/{path}", f"v1/{channel}/{commit}/{path}", kind,
                         hashlib.sha256(data).hexdigest()))

    signed = head_files.get(f"signed/{channel}/catalog.json")
    if signed is not None:
        errors.extend(check_signed_body(repo, channel, pointer, signed))

    def run(job):
        label, url_path, kind, want = job
        try:
            got = fetch(url_path)
        except FetchError as exc:
            return f"{label}: {exc}"
        if hashlib.sha256(got).hexdigest() != want:
            if kind == "bytes":
                return f"{label}: published bytes differ from the git blob"
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
        try:
            problems = verify_channel(fetch, args.repo, channel, expected, args.attempts, args.delay,
                                      ref=args.ref_template.format(channel=channel))
        except MirrorError as exc:
            problems = [f"{channel}: {exc}"]
        for problem in problems[:50]:
            print(f"MISMATCH {problem}", file=sys.stderr)
        if len(problems) > 50:
            print(f"... and {len(problems) - 50} more", file=sys.stderr)
        print(f"{channel}: {'FAILED, ' + str(len(problems)) + ' problems' if problems else 'verified'}")
        failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
