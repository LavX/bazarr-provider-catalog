#!/usr/bin/env python3
"""Refuse to deploy a mirror that would move a channel backwards.

Compares the freshly built pointer of every channel with the one the live site serves. A build
may deploy only when, for each channel, its commit is the live commit or a descendant of it, and
its ``published_at`` is not older than the live one. A rerun of an old workflow run, a branch that
was reset, and a runner whose clock is behind all fail here instead of replacing newer files.

A channel the site does not serve yet (a first deploy) passes. A live pointer that cannot be
fetched or read fails, because an advance cannot be proven. ``--allow-non-advance`` is the
deliberate way around the refusal, for a reviewed force-push or a repair.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from build_mirror import MirrorError
from verify_mirror import FetchError, NotFound, http_fetcher, parse_pointer


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    """Whether ``older`` is ``newer`` or reachable from it. An unknown commit counts as not."""
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
        capture_output=True,
    )
    return result.returncode == 0


def check_channel(fetch, repo: Path, site: Path, channel: str) -> str | None:
    """Return the reason the channel may not be deployed, or None."""
    new = parse_pointer(channel, (site / "v1" / channel / "current").read_bytes())
    try:
        live_raw = fetch(f"v1/{channel}/current", retry_not_found=False)
    except NotFound:
        return None
    except FetchError as exc:
        return f"{channel}: cannot read the live pointer, so an advance cannot be proven: {exc}"
    try:
        live = parse_pointer(channel, live_raw)
    except MirrorError as exc:
        return f"{channel}: the live pointer is unreadable: {exc}"
    if new["published_at"] < live["published_at"]:
        return (f"{channel}: this build is stamped {new['published_at']}, older than the live "
                f"pointer's {live['published_at']}")
    if new["commit"] != live["commit"] and not is_ancestor(repo, live["commit"], new["commit"]):
        return (f"{channel}: the live commit {live['commit']} is not an ancestor of {new['commit']}, "
                "so deploying would roll the channel back or rewrite it")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", type=Path, required=True, help="the built site directory")
    parser.add_argument("--base-url", required=True, help="the live Pages site URL")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--allow-non-advance", action="store_true")
    args = parser.parse_args(argv)
    try:
        fetch = http_fetcher(args.base_url)
        channels = sorted(path.parent.name for path in (args.site / "v1").glob("*/current"))
        if not channels:
            raise MirrorError(f"no channel pointers under {args.site}")
        problems = [p for p in (check_channel(fetch, args.repo, args.site, c) for c in channels) if p]
    except (MirrorError, OSError, ValueError) as exc:
        problems = [str(exc)]
    for problem in problems:
        print(f"REFUSED {problem}", file=sys.stderr)
    if problems and args.allow_non_advance:
        print("allowed anyway: --allow-non-advance is set", file=sys.stderr)
        return 0
    print("advance ok" if not problems else "not deployed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
