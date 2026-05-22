# Writing a Subtitle Scraper Provider for Bazarr+ — Worked Example

> **Audience:** Developers writing their first [Bazarr+](https://github.com/LavX/bazarr) Provider Hub catalog plugin against a third-party subtitle website that has no API.
> **Worked example:** [`providers/subtitlecat/`](../providers/subtitlecat/) — the first production catalog plugin. This guide walks the full journey from "I want to add subtitle site X" to "the manifest is in `catalog.json` and Bazarr+ installs it from the Marketplace". Every step references real files and commands.

Bazarr+ is an enhanced Bazarr fork that loads subtitle providers from an external catalog (this repo) and runs each in an isolated worker. Shipping a provider through the catalog is the fastest way to add a new subtitle source to Bazarr+ — no Bazarr release cycle, no rebuild, just a Marketplace install.

The shorter [Bazarr+ Provider Author Quickstart](../README.md#writing-your-own-provider) in the README covers the SDK commands; this guide fills in the parts that the quickstart skips: how to reverse-engineer the target site, how to keep the code testable without hitting the network, and the actual decisions you have to make.

---

## 0. Decide where the provider lives

There are two ways to ship a Bazarr+ subtitle provider:

| Option | Where it lives | When to pick |
| --- | --- | --- |
| **Built-in Subliminal-patch provider** | The [Bazarr+ repo](https://github.com/LavX/bazarr), under `custom_libs/subliminal_patch/providers/` | The provider is a core, always-on capability; you control the Bazarr+ release cycle. |
| **Provider Hub catalog plugin** | This repo (`bazarr-provider-catalog`), under `providers/<id>/` | You want to ship and iterate independently of Bazarr+ releases; the Bazarr+ Marketplace is the install channel; the plugin runs in worker isolation. |

This guide is for **catalog plugins**. The shape is the same as [`providers/smoke/`](../providers/smoke/): one `provider.py`, one `provider.json`. Two files. The Bazarr+ Provider Hub worker imports `provider.py` and calls `search()` and `download()`.

---

## 1. Reverse-engineer the target site

Before writing a single line of Python, you need to understand:

1. **The search URL pattern** — how do you turn "Interstellar (2014)" into an HTTP request?
2. **The result list structure** — what HTML markers identify a single search result?
3. **The detail page structure** — how does each result expose its downloadable subtitle files?
4. **The download flow** — does the SRT come back as a direct response, behind an auth wall, or behind some kind of redirect / token?
5. **Anti-bot signals** — CAPTCHAs, login walls, IP rate limits, JS challenges.

Do this with `curl` and your eyeballs. Tools like Chrome DevTools' Network tab are useful, but the goal is to find the simplest, most stable HTTP request shape — not to replay a JS-driven UI.

### Worked example: subtitlecat.com

```bash
# 1. What does a search look like?
curl -sL "https://www.subtitlecat.com/index.php?search=Interstellar+2014" \
  | grep -oE 'href="[^"]*subs/[^"]+"' | head -5
```

Output:

```
href="subs/55/La%20Ciencia%20de%20Interstellar%20(...).html"
href="subs/1244/Interstellar.2014.2014.1080p.BluRay.x264.YIFY.html"
href="subs/976/Interstellar.2014.2014.1080p.BluRay.x264.YIFY.eng-en.html"
```

So:

- Search URL: `https://www.subtitlecat.com/index.php?search=<urlencoded query>`.
- Each result is an anchor with **relative** `href="subs/<id>/<filename>.html"` (no leading slash; URL-encoded path).
- Result `<id>` is numeric and unique per upload.

> **Lesson learned:** The first regex I wrote used `href="/subs/..."` (with leading slash), which matched nothing in the search-results fixture. Always grep the actual captured HTML before locking in your parser — assumptions about leading slashes, quote types, and casing fail half the time. Capture, then verify.

### Look at the detail page

```bash
curl -sL "https://www.subtitlecat.com/subs/1459/Interstellar_2014_Bluray_720p_AAC_HEVC_x265.English.html" \
  | grep -oE 'id="download_[a-z]+"[^>]*href="[^"]+\.srt"' | head -3
```

Output:

```
id="download_ar"  onclick="log_download(14911970); show_voting('ar');" href="/subs/1491/Interstellar_2014_Bluray_720p_AAC_HEVC_x265.English-ar.srt"
id="download_bn"  ... href="/subs/1467/Interstellar_2014_Bluray_720p_AAC_HEVC_x265.English-bn.srt"
```

So each pre-existing translation is a direct `.srt` download under `/subs/<download_id>/<filename>-<lang>.srt`.

### Look for buttons that AREN'T downloads

```bash
curl -sL "https://www.subtitlecat.com/subs/.../...html" \
  | grep -oE '<button id="[a-z]+"[^>]*translate_from_server' | head -3
```

Output:

```
<button id="af" onclick="translate_from_server_folder('af', 'X-orig.srt', '/subs/1459/')
```

> **Critical lesson:** What looks like a server-side translation endpoint is actually a client-side JavaScript function. Reading `/js/translate.js` reveals it calls `translate.googleapis.com` directly from the user's browser, then POSTs the assembled translation back. From a Python worker we **cannot** replicate this flow (Google's gtx endpoint is rate-limited per IP and against their ToS for programmatic use). So our plugin only handles pre-existing translations.
>
> This is the single biggest decision you may face: **what does the site do client-side vs server-side?** Spending 10 minutes reading the inline JS is cheaper than 3 days of fragile "trigger and poll" code.

### Capture HTML fixtures *now*

You will write parser tests later, and they need stable input. Save raw HTML to `tests/fixtures/<provider>_<scenario>.html` and commit it:

```bash
mkdir -p tests/fixtures
curl -sL -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ..." \
  "https://www.subtitlecat.com/index.php?search=Interstellar+2014" \
  -o tests/fixtures/subtitlecat_search_interstellar.html
curl -sL -A "..." "https://www.subtitlecat.com/subs/1459/..." \
  -o tests/fixtures/subtitlecat_detail_interstellar.html
```

> **Best practice:** Use a realistic browser User-Agent on the capture. Some sites serve a different (e.g. legacy or anti-bot) layout to plain `curl` — your fixture should match what the plugin's runtime UA will receive.

Spot-check the fixtures with the same `grep` commands you used to discover the structure. If grep returns empty, the capture is bad (a Cloudflare interstitial, a 503, etc.) — recapture before moving on.

---

## 2. Sketch the plugin's data flow

A scraper plugin is a tiny pipeline:

```
video dict  ──►  build_queries  ──►  GET search URL  ──►  parse_search_results
                                                                │
                                                                ▼
                                                      list of detail candidates
                                                                │
                                                                ▼
                                          for each: GET detail URL  ──►  parse_detail_languages
                                                                │
                                                                ▼
                                                  filter by requested language
                                                                │
                                                                ▼
                                                       emit result dicts
```

Then `download()` is a separate, much simpler step:

```
provider_payload  ──►  GET subtitle_url  ──►  detect encoding  ──►  base64 + sha256  ──►  download dict
```

### Lock the shapes early

The smoke provider (`providers/smoke/provider.py`) is the canonical reference for the dict shapes. Lift them verbatim:

- `search()` returns `list[dict]` where each dict has: `provider`, `id`, `language`, `release_info`, `filename`, `matches`, `score`, `score_without_hash`, `score_out_of`, `hash_verifiable`, `hearing_impaired_verifiable`, `hearing_impaired`, `display`, `provider_payload`.
- `download()` returns `dict` with: `content_b64`, `content_sha256`, `content_type`, `format`, `encoding`, `empty`.

`language` is a babelfish-shaped dict: `{alpha3, alpha2, hi, forced}`.

### Decompose into pure functions first

The provider class has exactly one job: hold HTTP plumbing and orchestrate. Everything else (query building, HTML parsing, scoring) should be a **module-level pure function** that takes inputs and returns outputs. Why:

- Pure functions are trivially unit-tested with captured fixtures — no monkeypatching, no mocks.
- The class becomes a thin wrapper, so tests for `search()` only need to stub `_http_get`.
- Future you (or someone else) can change the HTTP layer without touching parser logic.

For subtitlecat that means:

| Function | Input | Output |
| --- | --- | --- |
| `build_queries(video)` | video dict | list of query strings, precise first then loose |
| `parse_search_results(html)` | raw HTML bytes | list of `{detail_id, detail_url, title}` |
| `parse_detail_languages(html)` | raw HTML bytes | tuple `(source_alpha2, {alpha2: download_url})` |
| `compute_score(video, candidate_title)` | video + a candidate's release title | int in [60, 100] |
| `derive_matches(video, candidate_title)` | video + candidate release title | subliminal-shaped match-key list (`title`, `year`, `series`, `season`, `episode`, `source`, `resolution`, `video_codec`, `audio_codec`, `release_group`, ...) |

The class wires them together and owns one method nobody mocks: `_http_get(url)`.

> **Why `derive_matches` matters.** Bazarr's downstream scoring (`subliminal_patch/score.py`) weights each returned match key. A provider that only returns `title`/`year` shows up far lower than one that surfaces `source`/`resolution`/`video_codec` when the release name supports it. Treat `derive_matches` as the *ranking signal*, not optional metadata.

A handful of smaller helpers tend to grow alongside these as you handle real-world quirks: a Unicode-aware normaliser so CJK titles match, a `_canonical_alpha2` table that folds deprecated language codes (`iw`→`he`) and ISO 639-2/B alpha3 (`fre`→`fr`) onto bazarr's canonical alpha2 set, a `_coerce_text` that collapses list-valued metadata (subliminal sometimes hands you `audio_codec=['DTS-HD','MA']` — passing a list to `dict.get` raises `TypeError: unhashable type: 'list'`), and boundary-aware tag matchers so episode 2 doesn't false-match `S01E20`. Look at `providers/subtitlecat/provider.py` for concrete shapes; the unit tests next to each helper document the cases they're meant to survive.

---

## 3. Write tests against the captured fixtures

This is the part that makes the difference between "a script that works today" and "a plugin that survives". Tests against captured HTML do not hit the network and do not flake.

Catalog tests use Python's built-in `unittest`, discovered by:

```bash
python3 -B -m unittest discover -s tests
```

Skeleton:

```python
# tests/test_<provider>.py
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "<provider>"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "<provider>_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

For each pure function, write 4–6 small cases covering:

- **Happy path** — well-formed input produces the expected output.
- **Edge case** — empty input, malformed input, fields missing from the video dict.
- **Negative case** — the function should return an empty list / empty dict / `None`, not raise.
- **Boundary case** — values at the limits (e.g. `S10E11` to ensure zero-padding doesn't break on double-digit seasons).

For the integration tests (the `SubtitlecatProvider.search()` flow), you do need to stub HTTP. The smallest viable pattern is to monkeypatch `_http_get` on the provider instance:

```python
def _provider_with_stub(self, responses):
    provider = self.mod.SubtitlecatProvider()
    def stub(url, timeout=15):
        if url not in responses:
            raise AssertionError(f"unexpected URL: {url}")
        return responses[url]
    provider._http_get = stub
    return provider
```

Pass a `{url: html_bytes}` map. Every URL the production code touches must be in the map, otherwise the stub raises and you immediately see the gap.

---

## 4. Implement the provider class

The class is small. It owns:

- A single HTTP-fetch method (`_http_get`) that uses `urllib.request` from the stdlib. No `requests`, no `httpx` — fewer pinned wheels is better.
- A User-Agent constant and a timeout constant. Hardcode both; don't make them config knobs.
- `search()` that calls the helpers in order: build queries, hit the search endpoint, parse, visit candidates, filter languages, emit dicts.
- `download()` that hits one URL and returns the standard download payload.

Politeness lever: a single `request_delay_ms` config knob, plus a `_sleep(config)` helper that the orchestration calls between HTTP requests. No retries — let exceptions surface so the worker reports them cleanly.

### Why no retries?

Bazarr's scheduler is the right layer for retry policy. A provider plugin retrying internally hides flakiness from the scheduler, multiplies request volume, and can mask real outages. If the site is down, fail fast and let the hub mark the worker unhealthy.

---

## 5. Build, validate, smoke-test

This is where the SDK earns its keep.

```bash
# Compute the provider.py SHA + bundle SHA
python3 -B -m sdk hash providers/<provider>
# Paste both values into provider.json (files.<file>.<sha> and bundle_sha256).

# Validate the manifest against the schema and assert the SHAs match.
python3 -B -m sdk validate

# Regenerate catalog.json from all provider.json files.
python3 -B -m sdk build-catalog

# Run the live smoke test (real HTTP to the target site).
python3 -B -m sdk smoke-test \
  --provider <provider> \
  --config-json '{"<knob>": <value>}' \
  --video-fixture <fixture.json> \
  --language eng
```

> **Run smoke-test from a real IP.** CI on a cloud provider may be blocked or rate-limited; the smoke test is intentionally not in the unit-test suite. Run it locally before merging.

A `--skip-download` option exists if you want to verify the search shape but defer download verification:

```bash
python3 -B -m sdk smoke-test --provider <provider> --skip-download ...
```

---

## 6. Final unit-test pass

Last thing before commit:

```bash
python3 -B -m unittest discover -s tests
```

If `tests/test_catalog.py` asserts on the set of provider IDs (`{"smokehub"}`), relax it to `assertIn(...)` for each expected ID. The test exists to catch *missing* providers, not to forbid new ones.

---

## 7. Commit

```bash
git add providers/<provider>/provider.py \
        providers/<provider>/provider.json \
        tests/test_<provider>.py \
        tests/fixtures/<provider>_*.html \
        tests/test_catalog.py \
        catalog.json
git commit -m "Add <provider> provider

<single sentence about what it does, single sentence about how>"
```

> **Don't push to `main`** until you've smoke-tested. The catalog.json on main is what every Bazarr+ install consumes via the Marketplace — a broken provider on main breaks installs in the wild.

---

## Anti-patterns to avoid

- **Inline HTML parsing inside `search()`.** Move the regex to a module-level function so it's testable.
- **Calling `time.sleep()` directly in tests.** Wrap delays in a helper that reads from config; tests pass `request_delay_ms: 0`.
- **Adding `requests` as a dependency for one HTTP call.** stdlib `urllib.request` works fine. Each wheel you pin is one more thing to verify across Python patch releases.
- **Catching exceptions in `_http_get`.** Let urllib raise. The orchestration layer (`search()`) decides whether to surface or swallow.
- **Hardcoding the User-Agent's version.** A realistic, stable UA is fine; a UA that pretends to be Chrome 117 in 2026 looks bot-y.
- **Returning Bazarr-internal types from the worker.** The plugin lives in an isolated process and talks to the hub via plain dicts. Never `import subliminal` or `import babelfish` from a plugin.
- **Swallowing a top-level search failure into an empty list.** If the search URL itself fails (DNS, 5xx, timeout), let the exception propagate. A network error is not a "no results"; the scheduler must see the difference. *Per-candidate* detail-page errors are a different case: skipping one failed detail fetch so the other candidates still surface results is fine, and arguably required for resilience — what you must not do is collapse the *entire* search into `[]` because something fetched halfway through went wrong.

---

## Appendix A — Common HTML parsing patterns

`html.parser` (stdlib) is more annoying than `BeautifulSoup` for tree walking, but compiled regex on raw bytes is faster and dependency-free. Use regex when the structure is shallow and the markers are unambiguous. Switch to `html.parser` if you need to count nested tags or track state.

A few patterns that come up:

```python
import re

# Match a relative or absolute anchor to a detail page.
_DETAIL_RE = re.compile(
    rb'<a[^>]+href="(/?subs/(\d+)/([^"]+\.html))"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# Strip inner tags from a link's text, normalize whitespace.
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_RE = re.compile(rb"\s+")
def _strip_tags(b):
    return _WS_RE.sub(b" ", _TAG_RE.sub(b"", b)).strip().decode("utf-8", errors="replace")
```

Note the `r` for **raw** string + `b` for **bytes** — `urllib.request` returns bytes, and treating HTML as bytes throughout avoids encoding surprises until the very last `.decode(...)` step.

---

## Appendix B — Encoding fallback for SRT downloads

SRT files are messy in the wild. Use a small chain:

```python
try:
    body.decode("utf-8")
    encoding = "utf-8"
except UnicodeDecodeError:
    encoding = "latin-1"
```

You don't need to actually decode and re-encode — the worker passes the body as base64 and the encoding string. Bazarr's downstream pipeline handles re-encoding to the user's chosen output charset.

Reject obviously-binary responses (`b"\x00"` in the first 4 bytes is a classic non-text signature) defensively by returning `empty: True` — better than uploading binary garbage as a subtitle.

---

## Appendix C — Sanity-check checklist before commit

- [ ] `python3 -B -m unittest discover -s tests` passes locally.
- [ ] `python3 -B -m sdk validate` passes.
- [ ] `python3 -B -m sdk smoke-test --provider <id> ...` returns `ok` for at least one movie fixture and one episode fixture.
- [ ] `git status --short` shows no `__pycache__` or fixture caches accidentally staged.
- [ ] `catalog.json` regenerated and the diff makes sense.
- [ ] Manifest's `languages` array is the *declared* set (Marketplace card) — the runtime filters down further.
- [ ] No third-party deps unless absolutely necessary, and if any, every wheel hash is pinned in `dependencies.requirements`.
