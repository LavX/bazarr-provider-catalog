#!/usr/bin/env python3
"""Refuse to deploy a mirror that would move a channel backwards.

Compares the freshly built pointer of every channel with the one the live site serves. A build
may deploy only when, for each channel, its commit is the live commit or a descendant of it, and
its ``published_at`` is not older than the live one. A rerun of an old workflow run, a branch that
was reset, and a runner whose clock is behind all fail here instead of replacing newer files.

A channel the site does not serve yet (a first deploy) passes. A live pointer that cannot be
fetched or read fails, because an advance cannot be proven. ``--allow-non-advance`` is the
deliberate way around the refusal, for a reviewed force-push or a repair.

With ``--outputs-file`` the pointers seen on the live site are recorded (``live=<json>``), so the
verify step can require that what is served after the deploy is not older than what was served
before it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from build_mirror import MirrorError, advance_problem
from verify_mirror import FetchError, NotFound, http_fetcher, parse_pointer


def check_channel(fetch, repo: Path, site: Path, channel: str) -> tuple[str | None, dict | None]:
    """Return ``(refusal, live pointer)``: why the channel may not be deployed, or None, and the
    pointer the live site serves now (None when it serves none)."""
    new = parse_pointer(channel, (site / "v1" / channel / "current").read_bytes())
    try:
        live_raw = fetch(f"v1/{channel}/current", retry_not_found=False)
    except NotFound:
        return None, None
    except FetchError as exc:
        return f"{channel}: cannot read the live pointer, so an advance cannot be proven: {exc}", None
    try:
        live = parse_pointer(channel, live_raw)
    except MirrorError as exc:
        return f"{channel}: the live pointer is unreadable: {exc}", None
    problem = advance_problem(repo, channel, new, live)
    if problem:
        return problem, live
    return None, live


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", type=Path, required=True, help="the built site directory")
    parser.add_argument("--base-url", required=True, help="the live Pages site URL")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--allow-non-advance", action="store_true")
    parser.add_argument("--outputs-file", type=Path, help="append live=<json> here (GITHUB_OUTPUT)")
    args = parser.parse_args(argv)
    try:
        fetch = http_fetcher(args.base_url)
        channels = sorted(path.parent.name for path in (args.site / "v1").glob("*/current"))
        if not channels:
            raise MirrorError(f"no channel pointers under {args.site}")
        results = {c: check_channel(fetch, args.repo, args.site, c) for c in channels}
        problems = [problem for problem, _live in results.values() if problem]
        if args.outputs_file:
            live = {c: pointer for c, (_problem, pointer) in results.items()}
            with args.outputs_file.open("a", encoding="utf-8") as handle:
                handle.write(f"live={json.dumps(live, sort_keys=True)}\n")
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
