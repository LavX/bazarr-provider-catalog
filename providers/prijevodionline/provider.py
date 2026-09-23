"""Prijevodi-Online provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import os
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import Cookie, CookieJar

PROVIDER_ID = "prijevodionline"
BASE_URL = "https://www.prijevodi-online.org"
HTTP_TIMEOUT_SECONDS = 10
# Bounded transport retry: a single transient network blip (reset/DNS/timeout,
# or a 5xx/429 from the host) should not abort a search or download. Mirrors the
# ~3-try behaviour of upstream subliminal's RetryingSession.
HTTP_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0
SUPPORTED_LANGUAGES = {
    "hrv": "hr",
    "srp": "sr",
    "cnr": "me",
    "hbs": "sh",
}
ALPHA2_TO_ALPHA3 = {
    "hr": "hrv",
    "sr": "srp",
    "me": "cnr",
    "cg": "cnr",
    "sh": "hbs",
}
LANGUAGE_BY_SUFFIX = {
    "hr": "hrv",
    "sr": "srp",
    "cg": "cnr",
}
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ssa", ".ass", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
COOKIE_DOMAIN = ".prijevodi-online.org"
# The site fronts everything with a Cloudflare managed challenge, which no
# plain HTTP client clears: challenged requests are delegated to FlareSolverr
# and the solved cookies and User-Agent are reused, so later requests pass on
# their own clearance. A challenge announces itself either with the
# cf-mitigated header or with the challenge page body; a plain 403/503 from
# the site keeps its ordinary meaning.
CLOUDFLARE_STATUS_CODES = {403, 503}
# A rate-limit challenge arrives as 429 with cf-mitigated: challenge; the
# header is definitive, so 429 must reach the detector. Body-marker detection
# stays restricted to CLOUDFLARE_STATUS_CODES so an ordinary 429 keeps its
# Retry-After retry semantics.
CLOUDFLARE_CHECK_STATUSES = {403, 429, 503}
# Challenge-specific markers only: Cloudflare's generic error template (an
# ordinary 403 access-denied or 503 outage page) contains cf-error-details
# too, and misreading those as challenges would replace the site's real
# errors with a misleading FlareSolverr message or a pointless solve.
# "Attention Required! | Cloudflare" is deliberately absent: that is the WAF
# block page (error 1020 and friends), which no solver clears; treating it as
# a challenge turns an IP block into a misleading FlareSolverr error.
CLOUDFLARE_BODY_MARKERS = (
    "just a moment",
    "cf-challenge",
    "cf_chl_opt",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
)
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 25000
# The Provider Hub kills a worker call at 30 seconds. Everything one request
# does (the challenged attempt, the solve, the byte-preserving replay and its
# retries) shares that budget, or the worker dies after a successful solve
# with nothing to show for it. The reserve keeps enough of the budget back
# for the replay to actually run; the safety margin is the time to hand the
# result over.
WORKER_DEADLINE_SECONDS = 30
DEADLINE_SAFETY_SECONDS = 2
REPLAY_RESERVE_SECONDS = 5
# The solver HTTP call is allowed maxTimeout plus this transport buffer, so
# the buffer must come out of the solve window, not out of the replay reserve.
SOLVER_TRANSPORT_BUFFER_SECONDS = 2
MIN_SOLVE_WINDOW_MS = 5000


class CloudflareBlockedError(RuntimeError):
    """Cloudflare answered with a challenge that could not be cleared."""

_SERIES_ROW_RE = re.compile(r"<tr\b[^>]*id=['\"]serija-(?P<id>\d+)['\"][^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>", re.I | re.S)
_TITLE_ATTR_RE = re.compile(r"\btitle=['\"](?P<title>[^'\"]+)['\"]", re.I)
_KEY_RE = re.compile(r"epizode\.key\s*=\s*['\"](?P<key>[0-9a-fA-F]{32})['\"]")
_SEASON_RE = re.compile(r"<h3\b[^>]*id=['\"]sezona-(?P<season>\d+)['\"][^>]*>.*?</h3>", re.I | re.S)
_EPISODE_DIV_RE = re.compile(r"<div\b[^>]*id=['\"]epizoda-(?P<id>\d+)['\"][^>]*>(?P<body>.*?)(?=<div\b[^>]*id=['\"]epizoda-\d+['\"]|<h3\b[^>]*id=['\"]sezona-\d+['\"]|</div>\s*</div>|\Z)", re.I | re.S)
_LIST_ITEM_RE = re.compile(r"<li\b[^>]*class=['\"][^'\"]*\b(?P<class>broj|naziv)\b[^'\"]*['\"][^>]*>(?P<body>.*?)</li>", re.I | re.S)
_SUB_ROW_RE = re.compile(r"<tr\b[^>]*id=['\"]prijevod-(?P<id>\d+)['\"][^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_OPIS_RE_TEMPLATE = r"<tr\b[^>]*id=['\"]prijevod-opis-{subtitle_id}['\"][^>]*>(?P<body>.*?)</tr>"
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(?P<body>.*?)</t[dh]>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_series_index(body):
    text = _decode_html(body)
    rows = []
    for row_match in _SERIES_ROW_RE.finditer(text):
        row = row_match.group("body")
        anchor = _ANCHOR_RE.search(row)
        if not anchor:
            continue
        href = html.unescape(anchor.group("href"))
        parsed = urllib.parse.urlparse(href)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 4 or parts[0:2] != ["serije", "view"]:
            continue
        title = _title_from_anchor(anchor.group(0), anchor.group("body"))
        rows.append(
            {
                "series_id": row_match.group("id"),
                "title": title,
                "slug": parts[3],
                "url": _absolute_url(href),
            }
        )
    return rows


def parse_series_page(body):
    text = _decode_html(body)
    key_match = None
    for match in _KEY_RE.finditer(text):
        key_match = match
    key = key_match.group("key") if key_match else ""
    episodes = {}
    season_matches = list(_SEASON_RE.finditer(text))
    for index, season_match in enumerate(season_matches):
        season = int(season_match.group("season"))
        start = season_match.end()
        end = season_matches[index + 1].start() if index + 1 < len(season_matches) else len(text)
        section = text[start:end]
        for episode_match in _EPISODE_DIV_RE.finditer(section):
            fields = _episode_fields(episode_match.group("body"))
            if fields.get("number") is None:
                continue
            episodes[(season, fields["number"])] = {
                "episode_id": episode_match.group("id"),
                "title": fields.get("title") or "",
            }
    return {"key": key, "episodes": episodes}


def parse_subtitle_rows(body):
    text = _decode_html(body)
    rows = []
    for row_match in _SUB_ROW_RE.finditer(text):
        subtitle_id = row_match.group("id")
        row = row_match.group("body")
        anchor = _ANCHOR_RE.search(row)
        if not anchor:
            continue
        href = html.unescape(anchor.group("href"))
        language = _language_from_href(href)
        if not language:
            continue
        cells = [match.group("body") for match in _CELL_RE.finditer(row)]
        status = _strip_tags(cells[2]) if len(cells) > 2 else ""
        releases = _releases_for_subtitle(text, subtitle_id)
        rows.append(
            {
                "subtitle_id": subtitle_id,
                "language": language,
                "url": _absolute_url(href),
                "filename": _strip_tags(anchor.group("body")),
                "verified": _is_verified_status(status),
                "releases": releases,
            }
        )
    return rows


def derive_matches(video, item):
    matches = []
    if _series_matches((video or {}).get("series"), item.get("series")):
        matches.append("series")
    try:
        if int((video or {}).get("season")) == int(item.get("season")):
            matches.append("season")
        if int((video or {}).get("episode")) == int(item.get("episode")):
            matches.append("episode")
    except (TypeError, ValueError):
        pass
    releases = [_normalize_release(value) for value in item.get("releases") or []]
    release_group = _normalize_release((video or {}).get("release_group"))
    if release_group and any(release_group in release for release in releases):
        matches.append("release_group")
    source = _normalize_release((video or {}).get("source"))
    if source and any(source in release for release in releases):
        matches.append("source")
    resolution = _normalize_release((video or {}).get("resolution"))
    if resolution and any(resolution in release for release in releases):
        matches.append("resolution")
    return matches


class PrijevodiOnlineProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))
        self._config = {}
        self._deadline = None
        # The Cloudflare clearance cookie is bound to the User-Agent that
        # earned it, so once FlareSolverr solves a challenge every request
        # must present the solved agent instead of the static default.
        self._user_agent = USER_AGENT

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,sr;q=0.8,en-US;q=0.7,en;q=0.6",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        return self._open_with_retry(request, timeout)

    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,sr;q=0.8,en-US;q=0.7,en;q=0.6",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        # The POST is the subtitle-list fetch (key lookup); it is read-only and
        # safe to repeat, so the same bounded retry applies.
        return self._open_with_retry(request, timeout)

    def _open_with_retry(self, request, timeout, allow_solve=True, deadline=None):
        if deadline is None:
            # search()/download() set the shared budget at entry; a direct call
            # (tests, future callers) still gets a full fresh one.
            deadline = self._deadline
        if deadline is None:
            deadline = time.monotonic() + WORKER_DEADLINE_SECONDS - DEADLINE_SAFETY_SECONDS
        # Wrap ONLY the raw urllib transport in a bounded retry. Retries cover
        # transient failures (connection reset/DNS/refused, timeouts, HTTP 5xx
        # and 429) and nothing else: 4xx other than 429, parse errors, and any
        # non-network exception propagate unchanged on their first occurrence.
        for attempt in range(1, HTTP_RETRIES + 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "prijevodi-online.org request ran out of its worker-deadline budget"
                )
            try:
                with self._opener.open(request, timeout=min(timeout, remaining)) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code in CLOUDFLARE_CHECK_STATUSES:
                    body = b""
                    try:
                        body = error.read()
                    except Exception:  # pragma: no cover, defensive read
                        pass
                    if _is_cloudflare_challenge(error.code, error.headers, body):
                        # A challenge is not transient: retrying hammers the
                        # wall. Delegate to FlareSolverr (or fail actionably).
                        if not allow_solve:
                            raise CloudflareBlockedError(
                                "prijevodi-online.org is still challenged after a "
                                "FlareSolverr clearance"
                            )
                        return self._solve_challenge(request, timeout, deadline)
                if not _is_retryable_status(error.code) or attempt >= HTTP_RETRIES + 1:
                    raise
                if time.monotonic() + _backoff_seconds(attempt, _retry_after_seconds(error)) >= deadline:
                    raise
                _sleep_backoff(attempt, _retry_after_seconds(error))
            except (socket.timeout, TimeoutError):
                if attempt >= HTTP_RETRIES + 1:
                    raise
                if time.monotonic() + _backoff_seconds(attempt, None) >= deadline:
                    raise
                _sleep_backoff(attempt, None)
            except urllib.error.URLError:
                # URLError covers connection refused / DNS / reset. HTTPError is a
                # subclass and was already handled above, so this branch is the
                # genuine transport failure that is always transient here.
                if attempt >= HTTP_RETRIES + 1:
                    raise
                if time.monotonic() + _backoff_seconds(attempt, None) >= deadline:
                    raise
                _sleep_backoff(attempt, None)

    def _pause(self, config):
        """The politeness delay, clamped so it never sleeps into the deadline.

        The worker being killed mid-sleep reports nothing; a delay that does
        not fit the remaining budget is pure waste, since the transport would
        reject the next request anyway.
        """
        delay_ms = (config or {}).get("request_delay_ms", 0) or 0
        delay = min(int(delay_ms), 5000) / 1000.0 if delay_ms > 0 else 0.0
        if self._deadline is not None:
            delay = max(0.0, min(delay, self._deadline - time.monotonic() - 1.0))
        if delay > 0:
            time.sleep(delay)

    def _solve_challenge(self, request, timeout, deadline):
        """Clear the challenge, then replay the original request ourselves.

        The solve is used only for what it earns: the clearance cookies and
        the User-Agent they are bound to. FlareSolverr's own response body is
        JSON text, which cannot carry a ZIP/RAR download intact, and using it
        would also bypass the transport retry for the origin's transient
        errors. Replaying through the opener keeps downloads byte-exact and
        keeps 429/5xx on their ordinary bounded-retry path; a challenge on the
        replay means the clearance did not take, which is a hard failure, not
        a loop.
        """
        endpoint = _flaresolverr_url(self._config)
        if not endpoint:
            raise CloudflareBlockedError(
                "prijevodi-online.org answered with a Cloudflare challenge; "
                "configure a FlareSolverr URL in the provider settings to clear it"
            )
        remaining_ms = int(
            (
                deadline
                - time.monotonic()
                - REPLAY_RESERVE_SECONDS
                - SOLVER_TRANSPORT_BUFFER_SECONDS
            )
            * 1000
        )
        if remaining_ms < MIN_SOLVE_WINDOW_MS:
            raise CloudflareBlockedError(
                "prijevodi-online.org needs a Cloudflare solve but the worker "
                "deadline leaves no room for one; the next attempt starts fresh"
            )
        timeout_ms = min(_flaresolverr_timeout_ms(self._config), remaining_ms)
        payload = {"cmd": "request.get", "url": request.full_url, "maxTimeout": timeout_ms}
        if request.data:
            # Replaying the subtitle-list POST as a GET would drop the key the
            # endpoint requires; FlareSolverr posts a urlencoded body, which is
            # exactly what this site expects.
            payload["cmd"] = "request.post"
            payload["postData"] = request.data.decode("utf-8")
        cookies = [{"name": cookie.name, "value": cookie.value} for cookie in self._cookie_jar]
        if cookies:
            payload["cookies"] = cookies
        parsed = self._flaresolverr_transport(payload)
        if not isinstance(parsed, dict) or parsed.get("status") not in (None, "ok"):
            message = ""
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or "")
            raise CloudflareBlockedError(
                f"PrijevodiOnline {message or 'FlareSolverr did not solve the challenge'}"
            )
        solution = parsed.get("solution") or {}
        body = (solution.get("response") or "").encode("utf-8")
        status = _safe_int(solution.get("status")) or 0
        if _is_cloudflare_challenge(status, solution.get("headers") or {}, body):
            raise CloudflareBlockedError(
                "PrijevodiOnline FlareSolverr response is still a Cloudflare block"
            )
        self._store_flaresolverr_solution(solution)
        replay = urllib.request.Request(
            request.full_url,
            data=request.data,
            # The opener's cookie processor stamped the ORIGINAL request with a
            # Cookie header (the stale clearance included), and a request that
            # already carries one is left alone by the processor. Drop it so
            # the replay is stamped fresh from the jar the solve just filled.
            headers={
                key: value
                for key, value in request.header_items()
                if key.lower() != "cookie"
            },
            method=request.get_method(),
        )
        replay.add_header("User-Agent", self._user_agent)
        return self._open_with_retry(replay, timeout, allow_solve=False, deadline=deadline)

    def _flaresolverr_transport(self, payload):
        """POST one command to FlareSolverr and return the parsed JSON.

        An instance attribute (like ``_opener``) so tests can script it.
        """
        endpoint = _flaresolverr_url(self._config)
        timeout_ms = _safe_int(payload.get("maxTimeout")) or _flaresolverr_timeout_ms(self._config)
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_ms / 1000 + SOLVER_TRANSPORT_BUFFER_SECONDS
            ) as response:
                raw = response.read()
        except Exception as error:
            raise CloudflareBlockedError(
                f"PrijevodiOnline FlareSolverr request failed: {error}"
            ) from error
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CloudflareBlockedError(
                "PrijevodiOnline FlareSolverr returned invalid JSON"
            ) from error

    def _store_flaresolverr_solution(self, solution):
        user_agent = solution.get("userAgent")
        if user_agent:
            self._user_agent = str(user_agent)
        for cookie in solution.get("cookies") or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            # A jar keeps one cookie per (domain, path, name), so a stale
            # clearance scoped to another domain variant would ride along as a
            # second cf_clearance and Cloudflare may keep rejecting. Drop every
            # same-name cookie first, then store under the solution's own
            # domain and path when it names them.
            stale = [
                existing for existing in self._cookie_jar if existing.name == name
            ]
            for existing in stale:
                try:
                    self._cookie_jar.clear(existing.domain, existing.path, existing.name)
                except KeyError:  # pragma: no cover, already gone
                    pass
            self._cookie_jar.set_cookie(
                _jar_cookie(
                    name,
                    value,
                    domain=str(cookie.get("domain") or COOKIE_DOMAIN),
                    path=str(cookie.get("path") or "/"),
                )
            )

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "episode":
            return []
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            return []
        requested = _requested_languages(languages)
        if not requested or not (video or {}).get("series"):
            return []

        config = dict(config or {})
        self._config = config
        # Every request this search makes (index walks, series page, subtitle
        # POST, a solve and its replay, the politeness delays between them)
        # shares one budget under the Provider Hub's worker deadline.
        self._deadline = time.monotonic() + WORKER_DEADLINE_SECONDS - DEADLINE_SAFETY_SECONDS
        titles = _series_titles(video)
        for series_title in titles:
            self._pause(config)
            series = self._find_series(series_title)
            if not series:
                continue
            self._pause(config)
            series_body = self._http_get(series["url"], referer=_index_url(series_title))
            page = parse_series_page(series_body)
            episode_info = page["episodes"].get((season, episode))
            if not episode_info:
                continue
            subtitles_url = f"{BASE_URL}/prijevod/get/{episode_info['episode_id']}"
            self._pause(config)
            subtitle_rows = parse_subtitle_rows(
                self._http_post(
                    subtitles_url,
                    {"key": page.get("key") or ""},
                    referer=series["url"],
                )
            )
            results = []
            seen = set()
            for row in subtitle_rows:
                output_language = _output_language(row["language"], requested)
                if not output_language:
                    continue
                merged = {
                    **row,
                    "series": series["title"],
                    "season": season,
                    "episode": episode,
                    "episode_id": episode_info["episode_id"],
                    "episode_title": episode_info.get("title") or "",
                    "language": output_language,
                    "source_language": row["language"],
                }
                key = (merged["subtitle_id"], merged["language"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, merged))
            if results:
                return sorted(results, key=lambda item: item["score"], reverse=True)
        return []

    def _find_series(self, title):
        rows = parse_series_index(self._http_get(_index_url(title), referer=BASE_URL + "/serije"))
        wanted = _normalize(title)
        # The site sometimes drops possessive apostrophes (e.g. "Da Vincis
        # Demons" vs "Da Vinci's Demons"), which _normalize turns into a stray
        # word boundary. Comparing the squashed form too keeps those matches.
        wanted_squashed = wanted.replace(" ", "")
        for row in rows:
            candidate = _normalize(row["title"])
            if candidate == wanted or candidate.replace(" ", "") == wanted_squashed:
                return row
        return None

    def _result(self, video, item):
        alpha3 = item["language"]
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        matches = derive_matches(video, item)
        release_info = ", ".join(item.get("releases") or []) or item.get("filename") or ""
        score = 45
        score += 20 if "series" in matches else 0
        score += 15 if "season" in matches else 0
        score += 15 if "episode" in matches else 0
        score += 10 if item.get("verified") else 0
        score += 5 if "release_group" in matches else 0
        filename = (
            f"prijevodionline.{_slug(item.get('series'))}."
            f"s{int(item.get('season')):02d}e{int(item.get('episode')):02d}."
            f"{alpha2}.zip"
        )
        return {
            "provider": PROVIDER_ID,
            "id": f"prijevodionline-{item['subtitle_id']}-{alpha3}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": release_info,
            "filename": filename,
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["url"],
            "display": {
                "source": "prijevodi-online.org",
                "title": item.get("series"),
                "release": release_info,
                "verified": bool(item.get("verified")),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": item["subtitle_id"],
                "episode_id": item["episode_id"],
                "url": item["url"],
                "filename": filename,
                "season": item.get("season"),
                "episode": item.get("episode"),
                "language": alpha3,
                "source_language": item.get("source_language"),
                "releases": list(item.get("releases") or []),
            },
        }

    def download(self, provider_payload, language, config):
        del language
        self._config = dict(config or {})
        self._deadline = time.monotonic() + WORKER_DEADLINE_SECONDS - DEADLINE_SAFETY_SECONDS
        payload = dict(provider_payload or {})
        url = payload.get("url")
        if not url:
            raise ValueError("prijevodionline download requires url")
        body = self._http_get(url, timeout=30)
        return _download_payload(body, payload)


def _download_payload(body, payload):
    payload = payload or {}
    # Reject broken responses up front: the download endpoint can answer with an empty
    # stream or an HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError(f"prijevodionline empty download for subtitle {payload.get('subtitle_id')}")
    if _is_html_body(body):
        raise ValueError(f"prijevodionline returned an HTML/error page for subtitle {payload.get('subtitle_id')}")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host, which lists it, detects encoding, and picks the member. A single
        # subtitle row's archive can bundle several releases for the same episode (and a
        # season pack repeats the episode number across seasons); the host's episode-only
        # pick cannot tell them apart. When we can list a zip we pin the member whose
        # filename overlaps the scored releases (the old select_subtitle_file intent).
        # Otherwise (rar, single member, the requested episode absent, or no unique
        # winner) let the host pick the member by episode.
        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        member = _select_release_member(body, payload)
        if member is not None:
            archive["member"] = member
        else:
            archive["episode"] = payload.get("episode")
        return archive
    # Direct, non-archive subtitle body.
    return _content_payload(body, _format_from_filename(payload.get("filename")))


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _select_release_member(body, payload):
    # Pin the zip member matching the scored releases, reproducing the old
    # select_subtitle_file() intent: narrow to the requested season+episode, then choose
    # the filename whose tokens overlap the provider's releases. Listing only, no
    # extraction or decoding: the host reads the named member and runs chardet. Returns
    # None for rar (not stdlib-listable), a single member (nothing to disambiguate), the
    # requested episode being absent, or no unique overlap winner, so the caller falls
    # back to host-side episode selection.
    payload = payload or {}
    if _is_rar_archive(body) or not zipfile.is_zipfile(io.BytesIO(body)):
        return None
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = [
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and _subtitle_extension(name)
            and not name.rsplit("/", 1)[-1].startswith(".")
        ]
    if len(members) < 2:
        return None  # a lone member: the host's episode pick already lands here
    # Narrow to the requested season+episode first. A season pack repeats an episode
    # number across seasons, so the SxxExx form (which encodes the season) is required;
    # the host can already do this, but a pack with several releases per episode leaves it
    # guessing among the matches.
    season = _safe_int(payload.get("season"))
    episode = _safe_int(payload.get("episode"))
    pool = members
    if season is not None and episode is not None:
        episode_pool = [name for name in members if _member_matches_episode(name, season, episode)]
        if episode_pool:
            # A lone SxxExx match is a confident unique pin: the SxxExx form encodes the
            # season, so for a pack that repeats an episode number across seasons (S01E05 vs
            # S02E05) the host's episode-only pick would be season-blind and could deliver the
            # other season's same-numbered episode. Pin it here instead of deferring.
            if len(episode_pool) == 1:
                return episode_pool[0]
            pool = episode_pool
        elif any(_member_has_episode_marker(name) for name in members):
            # Episode markers present but none matches the requested one: pinning a member
            # from another episode/season would hard-fail the host download, so defer.
            return None
    if len(pool) < 2:
        return None  # a single candidate: host episode selection already lands here
    # Score by releases token overlap, then pin only a unique winner with a positive
    # score. Ties or no overlap mean defer to the host.
    release_tokens = set()
    for value in payload.get("releases") or []:
        release_tokens.update(_tokens(value))
    if not release_tokens:
        return None  # nothing to disambiguate on; let the host pick by episode
    best, best_score, tied = None, 0, False
    for name in pool:
        score = _member_release_score(name, release_tokens)
        if score > best_score:
            best, best_score, tied = name, score, False
        elif score == best_score and best is not None:
            tied = True
    if best is None or best_score <= 0 or tied:
        return None  # cannot confidently disambiguate; let the host pick by episode
    return best


def _member_release_score(name, release_tokens):
    # Mirror the old select_subtitle_file scoring: overlap count between the releases
    # tokens and the member filename tokens, with a heavy penalty for forced tracks so a
    # forced sidecar never outranks the main subtitle.
    name_tokens = set(_tokens(os.path.basename(name)))
    score = len(release_tokens.intersection(name_tokens))
    if "forced" in name_tokens:
        score -= 5
    return score


def _member_matches_episode(name, season, episode):
    # Tolerate separated SxxExx tokens (S01.E02, S01 E02, S01-E02) as well as contiguous
    # S01E02; keep (?!\d) so "e02" never matches "e020". A left boundary (?<![a-z0-9])
    # guards the leading marker so "Bonus.Extras1E02.srt" never matches S01E02 (the "s" in
    # "Extras" must not start the token). NxNN (1x02) is matched too, with the same boundary.
    text = (name or "").lower()
    if re.search(rf"(?<![a-z0-9])s0*{season}[\s._-]*e0*{episode}(?!\d)", text):
        return True
    if re.search(rf"(?<![a-z0-9]){season}x0*{episode}(?!\d)", text):
        return True
    # Whole-token NNN form (e.g. S07E20 written as "720"): compare against delimited
    # tokens so "720" never matches inside "720p" and "264" never matches inside "x264".
    compact = f"{season}{episode:02d}"
    return compact in _tokens(name)


def _member_has_episode_marker(name):
    text = (name or "").lower()
    if re.search(r"s\d{1,2}[\s._-]*e\d{1,3}", text) or re.search(r"(?<!\d)\d{1,2}x\d{1,3}", text):
        return True
    return any(token.isdigit() and len(token) == 3 for token in _tokens(name))


def _safe_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_cloudflare_challenge(status, headers, body):
    """True only for an actual challenge, not for anything Cloudflare proxied.

    A "Server: cloudflare" header says the response came through the proxy, not
    that it is a challenge: the site's own 403 and 503 carry it too. The
    challenge announces itself either with cf-mitigated: challenge or in the
    body.
    """
    normalized = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    if normalized.get("cf-mitigated", "").strip().lower() == "challenge":
        return True
    if (_safe_int(status) or 0) not in CLOUDFLARE_STATUS_CODES:
        return False
    text = body.decode("utf-8", "ignore").lower() if isinstance(body, bytes) else str(body or "").lower()
    return any(marker in text for marker in CLOUDFLARE_BODY_MARKERS)


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config):
    value = _safe_int((config or {}).get("flaresolverr_timeout_ms"))
    if value is None:
        value = DEFAULT_FLARESOLVERR_TIMEOUT_MS
    # Capped below the Provider Hub's 30s worker deadline: the solver HTTP
    # call waits timeout + 2s of transport overhead, and a worker killed
    # mid-solve reports nothing useful.
    return max(5000, min(25000, value))


def _jar_cookie(name, value, domain=COOKIE_DOMAIN, path="/"):
    return Cookie(
        version=0,
        name=str(name),
        value=str(value),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=path,
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


def _episode_fields(body):
    fields = {}
    for match in _LIST_ITEM_RE.finditer(body or ""):
        class_name = match.group("class").lower()
        text = _strip_tags(match.group("body"))
        if class_name == "broj":
            try:
                fields["number"] = int(text.rstrip("."))
            except ValueError:
                pass
        elif class_name == "naziv":
            anchor = _ANCHOR_RE.search(match.group("body"))
            fields["title"] = _strip_tags(anchor.group("body") if anchor else match.group("body"))
    return fields


def _releases_for_subtitle(text, subtitle_id):
    pattern = re.compile(_OPIS_RE_TEMPLATE.format(subtitle_id=re.escape(str(subtitle_id))), re.I | re.S)
    match = pattern.search(text or "")
    if not match:
        return []
    cells = [cell.group("body") for cell in _CELL_RE.finditer(match.group("body"))]
    release_text = _strip_tags(cells[-1] if cells else match.group("body"))
    return [part.strip() for part in release_text.split("/") if part.strip()]


def _language_from_href(href):
    slug = os.path.basename(urllib.parse.urlparse(href or "").path).lower()
    for segment in reversed([part for part in slug.split("-") if part]):
        if segment in LANGUAGE_BY_SUFFIX:
            return LANGUAGE_BY_SUFFIX[segment]
    return None


def _requested_languages(languages):
    requested = set()
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 in SUPPORTED_LANGUAGES:
            requested.add(alpha3)
    return requested


def _output_language(row_language, requested):
    if row_language in requested:
        return row_language
    if "hbs" in requested and row_language in {"hrv", "srp", "cnr"}:
        return "hbs"
    return None


def _series_titles(video):
    titles = []
    values = [video.get("series")]
    values.extend(video.get("alternative_series") or [])
    for value in values:
        value = str(value or "").strip()
        if value and value not in titles:
            titles.append(value)
    return titles


def _index_url(title):
    title = str(title or "").strip()
    folded = _ascii_fold(title).lower()
    first = folded[:1]
    letter = first if first.isalpha() else "num"
    return f"{BASE_URL}/serije/index/{letter}"


def _title_from_anchor(anchor_html, body):
    title_match = _TITLE_ATTR_RE.search(anchor_html or "")
    if title_match:
        return html.unescape(title_match.group("title")).strip()
    return _strip_tags(body)


def _series_matches(wanted, candidate):
    wanted_tokens = _tokens(wanted)
    candidate_tokens = set(_tokens(candidate))
    return bool(wanted_tokens) and all(token in candidate_tokens for token in wanted_tokens)


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", value)


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _is_html_body(body):
    if not body:
        return False
    head = body[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
        or head.startswith(b"<!--")
        or b"<body" in head
        or b"<head" in head
    )


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _content_payload(content, subtitle_format):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "empty": False,
    }


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    if subtitle_format == "sub":
        return "text/plain"
    return "application/x-subrip"


def _alpha3_for_language(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        if alpha3:
            return alpha3
        return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())
    value = str(language or "").lower()
    return ALPHA2_TO_ALPHA3.get(value, value)


def _is_verified_status(status):
    return _normalize(status) == "provjereno"


def _ascii_fold(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return normalized.encode("ascii", "ignore").decode("ascii")


def _is_retryable_status(code):
    return code == 429 or 500 <= int(code) <= 599


def _retry_after_seconds(error):
    # Honour a Retry-After header on 429 when the host sends one. Only the
    # numeric (delta-seconds) form is supported; an HTTP-date or junk value is
    # ignored in favour of exponential backoff.
    headers = getattr(error, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _backoff_seconds(attempt, retry_after):
    # Exponential backoff with a small base, capped. A valid Retry-After wins.
    if retry_after is not None:
        delay = retry_after
    else:
        delay = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
    return min(delay, RETRY_BACKOFF_CAP_SECONDS)


def _sleep_backoff(attempt, retry_after):
    # Call the module-level time.sleep so tests can monkeypatch it.
    time.sleep(_backoff_seconds(attempt, retry_after))


def _strip_tags(value):
    stripped = _TAG_RE.sub("", value or "")
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()


def _decode_html(body):
    if isinstance(body, str):
        return body
    raw = body or b""
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _slug(value):
    return "-".join(_tokens(value)) or "release"


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_release(value):
    return _normalize(value).replace(" ", "")
