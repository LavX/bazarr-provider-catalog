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
  and carries every bundle file the body pins, with the pinned bytes; a source commit outside
  the retained history is published as bundles only, for every signed body in the history;
* with ``--live-before``, the pointer the site serves is not older than the one the site served
  before this run deployed.

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
    advance_problem,
    commit_files,
    opaque_paths,
    RETENTION,
    MirrorError,
    git,
    signed_pin_problems,
    signed_source,
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


def verify_channel(fetch, repo: Path, channel: str, expected: str | None = None,
                   attempts: int = 6, delay: float = 10.0, workers: int = 16,
                   ref: str | None = None, live_before: dict | None = None,
                   retention: int = RETENTION) -> list[str]:
    """Return the list of problems found for one channel (empty means verified).

    ``live_before`` is the pointer the site served before this run deployed, when there was one. The
    pointer served now may not be older than it.
    """
    try:
        pointer = fetch_pointer(fetch, channel, expected, attempts, delay)
    except MirrorError as exc:
        return [str(exc)]
    problem = check_history(repo, channel, ref or f"origin/{channel}", pointer, retention)
    if problem:
        return [problem]
    if live_before:
        problem = advance_problem(repo, channel, pointer, live_before)
        if problem:
            return [f"the site went backwards: {problem}"]

    errors: list[str] = []
    jobs: list[tuple[str, str, str, str]] = []  # (label, url path, kind, expected sha256)
    opaque = {"catalog.json", *opaque_paths(channel)}
    history = pointer["history"]
    head_files: dict[str, bytes] = {}
    sources: list[tuple[int, str]] = []

    def add_jobs(commit: str, files: dict[str, bytes]) -> None:
        for path, data in sorted(files.items()):
            kind = "bytes" if path in opaque else "pin"
            jobs.append((f"{channel}/{commit}/{path}", f"v1/{channel}/{commit}/{path}", kind,
                         hashlib.sha256(data).hexdigest()))

    for index, commit in enumerate(history):
        try:
            files = commit_files(repo, commit, channel)
        except ValidationError as exc:
            errors.append(f"{channel}/{commit}: {exc}")
            continue
        if index == 0:
            head_files = files
            if hashlib.sha256(files["catalog.json"]).hexdigest() != pointer["catalog_sha256"]:
                errors.append(f"{channel}/{commit}: pointer catalog_sha256 differs from the git blob")
        add_jobs(commit, files)
        try:
            source = signed_source(files, channel)
        except ValidationError as exc:
            if index == 0:
                errors.append(f"{channel}/{commit}: {exc}")
            continue
        if source is not None:
            sources.append((index, source))

    # Bundles of signed bodies whose source commit fell out of the window are published on their own.
    extras: set[str] = set()
    for index, source in sources:
        if source in history or source in extras:
            continue
        try:
            source_files = commit_files(repo, source, channel)
        except ValidationError as exc:
            if index == 0:
                errors.append(f"{channel}/{history[0]}: its signed catalog names {source}: {exc}")
            continue
        extras.add(source)
        add_jobs(source, source_files)

    signed = head_files.get(opaque_paths(channel)[0])
    head_source = dict(sources).get(0)
    if signed is not None and head_source is not None:
        try:
            source_files = commit_files(repo, head_source, channel)
            missing = signed_pin_problems(signed, source_files)
        except ValidationError as exc:
            errors.append(f"{channel}/{head_source}: {exc}")
        else:
            errors.extend(
                f"{channel}/{head_source}/{path}: the signed catalog pins a file the mirror does not carry as pinned"
                for path in missing
            )

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
    parser.add_argument("--live-before", default="{}",
                        help='JSON {"channel": pointer or null}: what the site served before the deploy')
    parser.add_argument("--allow-non-advance", action="store_true",
                        help="do not require the pointer to be at or ahead of --live-before")
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--delay", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        heads = json.loads(args.heads)
        live_before = {} if args.allow_non_advance else json.loads(args.live_before)
        if not isinstance(live_before, dict):
            raise ValueError("--live-before must be a JSON object")
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
                                      ref=args.ref_template.format(channel=channel),
                                      live_before=live_before.get(channel))
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
