"""Subs4Free provider for the Bazarr+ Provider Hub catalog."""

import base64
import datetime
import email.utils
import hashlib
import html
import io
import json
import logging
import math
import random
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

import ua_generator
from ua_generator.options import Options

logger = logging.getLogger(__name__)

PROVIDER_ID = "subs4free"
BASE_URL = "https://www.subs4free.info"
# The host whose Anubis clearance cookie the search, detail and getSub.php calls need.
# Only a failed gate here renews the identity; other subs4free.info hosts do not.
_SITE_HOST = urllib.parse.urlparse(BASE_URL).hostname
SEARCH_URL = f"{BASE_URL}/search_report.php"
DOWNLOAD_URL = f"{BASE_URL}/getSub.php"
HTTP_TIMEOUT_SECONDS = 15
# subs4free.info sits behind Anubis (itself behind Cloudflare). Every page answers 200 with
# a proof-of-work challenge until the session holds a *-anubis-auth cookie. Solve inline,
# and on a failed solve renew the browser identity (UA + cookies) before trying again.
CHALLENGE_RETRY_ATTEMPTS = 3
# One site call, all attempts, solves and backoff included, stays well inside the host's
# 120 s worker timeout. Running out aborts the call; it is never retried.
SITE_CALL_BUDGET_SECONDS = 60
RETRY_BACKOFF_SECONDS = 0.5
ANUBIS_PASS_PATH = "/.within.website/x/cmd/anubis/api/pass-challenge"
MAX_CANDIDATES_PER_QUERY = 20
SUPPORTED_LANGUAGES = {
    "ell": "el",
    "eng": "en",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

_ROW_RE = re.compile(
    r"<div\s+class=[\"'][^\"']*\bmovie-details\b[^\"']*[\"'][^>]*>(?P<body>.*?)(?=<div\s+class=[\"'][^\"']*\bmovie-details\b|\Z)",
    re.I | re.S,
)
_HEADING_RE = re.compile(
    r"<a\b(?=[^>]*\bclass=[\"'][^\"']*\bmovie-heading\b)[^>]*>",
    re.I | re.S,
)
_SPAN_RE = re.compile(r"<span\b[^>]*>(?P<text>.*?)</span>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OPTION_RE = re.compile(
    r"<option\b[^>]*value=[\"'](?P<value>[^\"']+)[\"'][^>]*>(?P<title>.*?)</option>",
    re.I | re.S,
)
_UPLOADER_RE = re.compile(r"Uploaded\s+by\s*<a\b[^>]*>(?P<name>.*?)</a>", re.I | re.S)
_DOWNLOADS_RE = re.compile(r"<b>\s*(?P<count>\d+)\s*</b>\s*DLs", re.I)
_ID_INPUT_RE = re.compile(
    r"<input\b(?=[^>]*\bname=[\"']id[\"'])(?=[^>]*\bvalue=[\"'](?P<id>[^\"']+)[\"'])[^>]*>",
    re.I | re.S,
)
_IMAGE_INPUT_RE = re.compile(
    r"<input\b(?=[^>]*\btype=[\"']image[\"'])[^>]*>",
    re.I | re.S,
)
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_MARKER_RE = re.compile(
    r"""<script\b[^>]*\bid\s*=\s*["']anubis_challenge["']""", re.I
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)
# getSub.php answers a flagged client with "Refresh timorias [1]" or "Polla alla isxiei
# timoria..." (Greeklish for "punishment"): an anti-flood penalty on the client IP.
_PENALTY_RE = re.compile(rb"timori", re.I)
_PENALTY_BODY_MAX_BYTES = 512
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def build_queries(video):
    video = video or {}
    if video.get("kind") != "movie":
        return []
    title = (_coerce_text(video.get("title")) or "").strip()
    if not title:
        return []
    year = video.get("year")
    if year:
        return [f"{title} {year}", title]
    return [title]


def parse_search_results(body):
    text = _decode(body)
    rows = []
    seen = set()
    for match in _ROW_RE.finditer(text):
        row = match.group("body")
        heading = _HEADING_RE.search(row)
        if not heading:
            continue
        tag = heading.group(0)
        href = _attr(tag, "href")
        if not href:
            continue
        release_info = _release_from_row(row, tag)
        if not release_info:
            continue
        language = _language_from_row(row, tag, href)
        if not language:
            continue
        detail_url = _absolute_url(href)
        key = (detail_url, language)
        if key in seen:
            continue
        seen.add(key)
        alpha2 = SUPPORTED_LANGUAGES[language]
        rows.append(
            {
                "id": _subtitle_id_from_url(detail_url),
                "detail_url": detail_url,
                "language": language,
                "alpha2": alpha2,
                "release_info": release_info,
                "title": _movie_title_from_release(release_info),
                "year": _release_year_from_text(release_info),
                "years": _years_from_text(release_info),
                "uploader": _uploader_from_row(row),
                "downloads": _downloads_from_row(row),
            }
        )
    return rows


def parse_suggestions(body):
    rows = []
    for match in _OPTION_RE.finditer(_decode(body)):
        row = _suggestion_from_option(match)
        if row:
            rows.append(row)
    return rows


def parse_download_form(body):
    text = _decode(body)
    id_match = _ID_INPUT_RE.search(text)
    if not id_match:
        raise ValueError("subs4free download form id was not found")
    image_match = _IMAGE_INPUT_RE.search(text)
    image_tag = image_match.group(0) if image_match else ""
    return {
        "id": html.unescape(id_match.group("id")),
        "width": _dimension(_attr(image_tag, "width")),
        "height": _dimension(_attr(image_tag, "height")),
    }


class APIThrottled(Exception):
    """subs4free refused the download because of its anti-flood penalty.

    The penalty is short (it cleared within minutes in live checks), so this is a rate
    limit rather than a quota. The worker maps this class name onto the host's counted
    APIThrottled: the provider sits out the current run, and repeated refusals pause it
    for 10 minutes by default.
    """


def extract_download(body, payload=None):
    payload = payload or {}
    # Reject broken responses: getSub.php can answer with an empty stream or an HTML/error
    # page that otherwise looks like a successful download.
    if not body or not body.strip():
        raise ValueError("subs4free returned an empty download")
    if _is_refused_download(body):
        raise APIThrottled(
            "subs4free refused the download: the site's anti-flood penalty is active for this IP"
        )
    if _is_html_body(body):
        raise ValueError("subs4free returned an HTML/error page instead of a subtitle")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to the
        # host, which lists it, detects encoding, and picks the member. A subs4free zip is
        # one release for one movie in one language, but it can still bundle several subtitle
        # members (e.g. variant cuts). This is a movie-only provider, so there is no episode
        # to select on: the host's episode pick has no signal. When we can list a zip we pin
        # the member whose filename overlaps the scored release_info (the old
        # select_subtitle_file intent); on rar, a single member, or no unique winner we fall
        # back to the host (carrying the always-None movie episode).
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


def _select_release_member(body, payload):
    # Pin the zip member matching the scored release_info, reproducing the old
    # select_subtitle_file() intent: choose the filename whose tokens overlap the provider's
    # release_info (its filename slug carries the same release string). Listing only, no
    # extraction or decoding: the host reads the named member and runs chardet. Returns None
    # for rar (not stdlib-listable), a single member (nothing to disambiguate), no
    # release_info to score on, or no unique winner, so the caller defers to the host.
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
    release_tokens = set(_tokens(payload.get("release_info") or payload.get("filename")))
    if not release_tokens:
        return None  # nothing to disambiguate on; let the host pick
    best, best_score, tied = None, 0, False
    for name in members:
        score = _member_release_score(name, release_tokens)
        if score > best_score:
            best, best_score, tied = name, score, False
        elif score == best_score and best is not None:
            tied = True
    if best is None or best_score <= 0 or tied:
        return None  # cannot confidently disambiguate; defer to the host
    return best


def _member_release_score(name, release_tokens):
    # Mirror the old select_subtitle_file scoring: overlap count between the release_info
    # tokens and the member filename tokens, with a heavy penalty for forced tracks so a
    # forced sidecar never outranks the main subtitle.
    name_tokens = set(_tokens(name.rsplit("/", 1)[-1]))
    score = len(release_tokens.intersection(name_tokens))
    if "forced" in name_tokens:
        score -= 5
    return score


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _is_refused_download(body):
    # A refused download is a 302 from getSub.php back to the subtitle's detail page. Its
    # short body names the penalty; once the redirect is followed the response is that
    # detail page, which still carries the getSub.php form. Archives are never refusals,
    # and the penalty word only counts in a short body, so a subtitle quoting it is safe.
    if _is_archive_body(body):
        return False
    if len(body) <= _PENALTY_BODY_MAX_BYTES and _PENALTY_RE.search(body):
        return True
    if not _is_html_body(body):
        return False
    text = _decode(body)
    return "getSub.php" in text and _ID_INPUT_RE.search(text) is not None


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


def derive_matches(video, item):
    video = video or {}
    release = item.get("release_info") if isinstance(item, dict) else item
    release_norm = _normalize(release)
    release_tokens = set(release_norm.split())
    matches = []
    title_tokens = _tokens(video.get("title"))
    if title_tokens and all(token in release_tokens for token in title_tokens):
        matches.append("title")
    year = video.get("year")
    if year and str(year) in release_tokens:
        matches.append("year")
    if _coerce_text(video.get("resolution")) and _normalize(video.get("resolution")) in release_tokens:
        matches.append("resolution")
    if _matches_token_table(video.get("source"), _SOURCE_TOKENS, release_tokens):
        matches.append("source")
    if _matches_token_table(video.get("video_codec"), _VIDEO_CODEC_TOKENS, release_tokens):
        matches.append("video_codec")
    if _matches_token_table(video.get("audio_codec"), _AUDIO_CODEC_TOKENS, release_tokens):
        matches.append("audio_codec")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _normalize_release_group(release_group) in _normalize_release_group(release):
        matches.append("release_group")
    return matches


def compute_score(video, item):
    matches = set(derive_matches(video, item))
    if {"title", "year"}.issubset(matches):
        return 100
    if "title" in matches:
        return 90
    return 60


class Subs4FreeError(RuntimeError):
    """Base class for Subs4Free failures."""


class RateLimited(Subs4FreeError):
    """The upstream service asked the caller to slow down.

    retry_after is the site's Retry-After in seconds, when it sent a usable one. The
    worker passes it to the host, which keeps the provider out at least that long.
    """

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after_seconds(value):
    # Retry-After is either delta-seconds or an HTTP-date (RFC 9110, section 10.2.3).
    # Anything unparseable, non-finite, or already past is ignored.
    value = (value or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        seconds = (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return seconds


class ServiceUnavailable(Subs4FreeError):
    """The upstream service is not currently usable."""


def is_anubis_challenge(url, status_code=0):
    return "/.within.website/" in (url or "") or (
        status_code in (307, 401, 403) and ".within.website" in (url or "")
    )


def _extract_anubis_challenge(html_text):
    meta_match = _META_REFRESH_RE.search(html_text or "")
    if meta_match and "/.within.website/" in meta_match.group("url"):
        return {
            "method": "metarefresh",
            "redirect_url": html.unescape(meta_match.group("url")),
            "delay": int(meta_match.group("delay")),
            "difficulty": 0,
        }
    match = _ANUBIS_CHALLENGE_RE.search(html_text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    challenge = data.get("challenge")
    if isinstance(challenge, str):
        # Older Anubis builds publish the random data as a bare string, with the id (if
        # any) at the top level and the policy only under "rules".
        challenge = {"id": data.get("id") or challenge, "randomData": challenge}
    if not isinstance(challenge, dict) or "randomData" not in challenge or "id" not in challenge:
        return None
    if any(not isinstance(challenge[key], str) or not challenge[key] for key in ("id", "randomData")):
        return None
    # Anubis >= 1.2x also publishes the policy under "rules"; older builds only carry the
    # values on the challenge itself.
    rules = data.get("rules") if isinstance(data.get("rules"), dict) else {}
    try:
        difficulty = int(challenge.get("difficulty", rules.get("difficulty", 4)))
    except (TypeError, ValueError, OverflowError):
        return None
    if difficulty < 0:
        return None
    method = (
        challenge.get("method")
        or challenge.get("algorithm")
        or rules.get("method")
        or rules.get("algorithm")
        or "fast"
    )
    return {
        "id": challenge["id"],
        "randomData": challenge["randomData"],
        "difficulty": difficulty,
        "method": method,
    }


def _solve_pow(random_data, difficulty, deadline=None):
    prefix = "0" * difficulty
    nonce = 0
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise ServiceUnavailable("subs4free Anubis stage=proof_of_work budget_exhausted")
        digest = hashlib.sha256(f"{random_data}{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith(prefix):
            return nonce, digest
        nonce += 1


def _solve_preact(random_data, difficulty):
    return hashlib.sha256(random_data.encode("utf-8")).hexdigest(), difficulty * 0.125


def _is_anubis_response(url, status_code, body):
    text = _decode(body[:65536] if isinstance(body, bytes) else body)
    return bool(
        is_anubis_challenge(url, status_code)
        or _ANUBIS_CHALLENGE_MARKER_RE.search(text)
        or _extract_anubis_challenge(text)
    )


def _renews_identity(url):
    return (urllib.parse.urlparse(url or "").hostname or "").lower() == _SITE_HOST


def _remaining(deadline, stage):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ServiceUnavailable(f"subs4free Anubis stage={stage} budget_exhausted")
    return remaining


def _request_timeout(timeout, deadline, stage):
    return min(timeout, _remaining(deadline, stage))


def _budget_sleep(seconds, deadline, stage):
    if seconds >= _remaining(deadline, stage):
        raise ServiceUnavailable(f"subs4free Anubis stage={stage} budget_exhausted")
    time.sleep(seconds)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # The pass endpoint answers 302 with the auth cookie. Keep that response (the cookie
    # processor has already stored the cookie) instead of following it to the resource.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Subs4FreeProvider:
    def __init__(self):
        self._user_agent = None
        self._new_identity()

    def _new_identity(self):
        # A rejected identity includes its browser header and cookies. Keep the next
        # attempt isolated instead of replaying either half of that pair.
        previous = self._user_agent
        user_agent = previous
        for _ in range(3):
            user_agent = ua_generator.generate(
                device="desktop",
                platform=("linux", "windows"),
                browser=("firefox", "chrome"),
                options=Options(latest_versions=True),
            ).text
            if user_agent != previous:
                break
        self._user_agent = user_agent
        self._cookie_jar = CookieJar()
        cookies = urllib.request.HTTPCookieProcessor(self._cookie_jar)
        self._opener = urllib.request.build_opener(cookies)
        self._pass_opener = urllib.request.build_opener(cookies, _NoRedirect())

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "movie":
            return []
        requested = {_alpha3_for_language(language) for language in languages or []}
        requested = {language for language in requested if language in SUPPORTED_LANGUAGES}
        if not requested:
            return []
        results = []
        seen = set()
        covered_languages = set()
        for query in build_queries(video):
            _sleep(config)
            search_url = _search_url(query)
            body = self._http_get(search_url, referer=BASE_URL)
            candidates = parse_search_results(body)
            candidates.extend(self._candidates_from_suggestions(video, body, config, search_url))
            accepted = _accepted_candidates(video, candidates, requested, seen)
            covered_languages.update(item["language"] for item in accepted)
            results.extend(self._result(video, item) for item in accepted)
            if requested.issubset(covered_languages):
                break
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        detail_url = payload.get("detail_url")
        if not detail_url:
            raise ValueError("subs4free download requires detail_url")
        _sleep(config)
        page = self._http_get(detail_url, referer=BASE_URL)
        form = parse_download_form(page)
        # The legacy "anti-block" calls (images.subs4free.info and two subs4series.com
        # pages) only detected ISP blocking for visitors in Greece; getSub.php does not
        # depend on them, so the POST follows the detail page directly.
        post_data = {
            "id": form["id"],
            "x": str(random.randint(0, max(form["width"], 1))),
            "y": str(random.randint(0, max(form["height"], 1))),
        }
        _sleep(config)
        body = self._http_post(DOWNLOAD_URL, post_data, referer=detail_url)
        return extract_download(body, payload)

    def _candidates_from_suggestions(self, video, body, config, referer):
        candidates = []
        for suggestion in parse_suggestions(body):
            if not _suggestion_matches(video, suggestion):
                continue
            _sleep(config)
            candidates.extend(parse_search_results(self._http_get(suggestion["url"], referer=referer)))
        return candidates

    def _result(self, video, item):
        alpha3 = item["language"]
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        matches = derive_matches(video, item)
        score = compute_score(video, item)
        filename = f"subs4free.{_slug(item['release_info'])}.{alpha2}.zip"
        return {
            "provider": PROVIDER_ID,
            "id": f"subs4free-{item['id']}-{alpha3}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": item["release_info"],
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["detail_url"],
            "display": {
                "source": "subs4free",
                "release": item["release_info"],
                "uploader": item.get("uploader"),
                "downloads": item.get("downloads"),
                "detail_url": item["detail_url"],
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "detail_url": item["detail_url"],
                "filename": filename,
                "release_info": item["release_info"],
                "language": alpha3,
                "season": item.get("season"),
                "episode": item.get("episode"),
            },
        }

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        def build():
            return urllib.request.Request(url, headers=_headers(self._user_agent, referer))

        return self._open_with_anubis(build, url, timeout)

    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        encoded = urllib.parse.urlencode(data).encode("utf-8")

        def build():
            headers = _headers(self._user_agent, referer)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            return urllib.request.Request(url, data=encoded, headers=headers, method="POST")

        # A POST interrupted by a challenge is re-sent once the gate clears; the pass
        # endpoint redirects back to the page the form lives on.
        return self._open_with_anubis(build, url, timeout, redir_url=referer)

    def _fetch(self, request, timeout, opener=None):
        # Return error bodies too: a gate can ride on a 401/403 and must still be read.
        try:
            with (opener or self._opener).open(request, timeout=timeout) as response:
                return response.getcode(), response.geturl(), response.read()
        except urllib.error.HTTPError as error:
            if error.code == 429:
                # Raised here, where the headers are still at hand, so the site's
                # Retry-After reaches the host on every request (page, challenge, pass).
                raise RateLimited(
                    "subs4free rate limited the request",
                    retry_after=_retry_after_seconds((error.headers or {}).get("Retry-After")),
                ) from None
            try:
                body = error.read()
            except Exception:
                body = b""
            return error.code, error.geturl() or request.full_url, body

    def _open_with_anubis(self, build_request, url, timeout, redir_url=None):
        # Only a failed gate on www.subs4free.info renews the identity and retries. A gate
        # on any other host (another subs4free.info host or a third-party page) gets one
        # attempt, so it can never wipe the clearance cookie the getSub.php POST needs. Running out of the call budget raises ServiceUnavailable
        # from wherever it happens and is not retried: a new identity cannot make the
        # proof of work cheaper.
        deadline = time.monotonic() + SITE_CALL_BUDGET_SECONDS
        attempts = CHALLENGE_RETRY_ATTEMPTS if _renews_identity(url) else 1
        for attempt in range(1, attempts + 1):
            status, landed, body = self._fetch(build_request(), _request_timeout(timeout, deadline, "resource_fetch"))
            if status == 429:
                raise RateLimited("subs4free rate limited the request")
            if _is_anubis_response(landed, status, body):
                if self._solve_anubis(landed or url, redir_url or url, body, timeout, deadline):
                    status, landed, body = self._fetch(
                        build_request(), _request_timeout(timeout, deadline, "resource_refetch")
                    )
                    if status == 429:
                        raise RateLimited("subs4free rate limited the request")
            if not _is_anubis_response(landed, status, body):
                if status >= 400:
                    # Same failure as before the gate: an ordinary HTTP error, not a
                    # provider-wide throttle.
                    raise urllib.error.HTTPError(landed or url, status, f"subs4free HTTP {status}", None, None)
                return body
            logger.warning("subs4free Anubis stage=recovery attempt=%s/%s status=%s", attempt, attempts, status)
            if attempt < attempts:
                _budget_sleep(RETRY_BACKOFF_SECONDS * attempt, deadline, "recovery_backoff")
                self._new_identity()
        raise ServiceUnavailable(f"subs4free Anubis challenge could not be solved after {attempts} attempts")

    def _solve_anubis(self, challenge_url, original_url, body, timeout, deadline):
        # Returns False when the site rejects the solve (the caller may retry with a new
        # identity). Budget exhaustion raises instead and ends the whole call.
        started = time.monotonic()
        parsed = urllib.parse.urlparse(challenge_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        original = urllib.parse.urlparse(original_url)
        original_target = urllib.parse.urlunparse(("", "", original.path or "/", original.params, original.query, ""))
        redir = urllib.parse.parse_qs(parsed.query).get("redir", [original_target])[0]
        challenge = _extract_anubis_challenge(_decode(body))
        if not challenge and "/.within.website/" in challenge_url:
            # Redirected to a challenge page without the inline payload: fetch it once.
            _, _, page = self._fetch(
                self._anubis_request(challenge_url, original_url),
                _request_timeout(timeout, deadline, "challenge_fetch"),
            )
            challenge = _extract_anubis_challenge(_decode(page))
        if not challenge:
            logger.warning("subs4free Anubis stage=challenge_parse")
            return False

        method = challenge["method"]
        if method == "metarefresh":
            pass_url = urllib.parse.urljoin(base + "/", challenge["redirect_url"])
            _budget_sleep(challenge.get("delay", 1), deadline, "pass_submit")
        elif method == "preact":
            result, delay = _solve_preact(challenge["randomData"], challenge["difficulty"])
            if delay > 0:
                _budget_sleep(delay, deadline, "pass_submit")
            params = {
                "id": challenge["id"],
                "result": result,
                "redir": redir,
                "elapsedTime": str(int((time.monotonic() - started) * 1000)),
            }
            pass_url = f"{base}{ANUBIS_PASS_PATH}?{urllib.parse.urlencode(params)}"
        else:
            nonce, digest = _solve_pow(challenge["randomData"], challenge["difficulty"], deadline=deadline)
            params = {
                "id": challenge["id"],
                "response": digest,
                "nonce": str(nonce),
                "redir": redir,
                "elapsedTime": str(int((time.monotonic() - started) * 1000)),
            }
            pass_url = f"{base}{ANUBIS_PASS_PATH}?{urllib.parse.urlencode(params)}"

        status, _, _ = self._fetch(
            self._anubis_request(pass_url, original_url),
            _request_timeout(timeout, deadline, "pass_submit"),
            opener=self._pass_opener,
        )
        if status == 429:
            raise RateLimited("subs4free rate limited the Anubis request")
        if not 200 <= status < 400:
            logger.warning("subs4free Anubis stage=pass_status status=%s", status)
            return False
        if not self._has_anubis_auth(parsed.hostname):
            logger.warning("subs4free Anubis stage=auth_cookie status=%s", status)
            return False
        return True

    def _anubis_request(self, url, referer):
        return urllib.request.Request(url, headers=_headers(self._user_agent, referer))

    def _has_anubis_auth(self, host):
        host = (host or "").lower()
        for cookie in self._cookie_jar:
            if not cookie.name.endswith("-anubis-auth") or not cookie.value or cookie.is_expired():
                continue
            domain = (cookie.domain or "").lstrip(".").lower()
            if not domain or host == domain or host.endswith(f".{domain}"):
                return True
        return False


def _search_url(query):
    return f"{SEARCH_URL}?{urllib.parse.urlencode({'search': query, 'searchType': '1'})}"


def _headers(user_agent, referer=None):
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _release_from_row(row, heading_tag):
    span = _SPAN_RE.search(row)
    if span:
        release = _strip_tags(span.group("text"))
        if release:
            return release
    title = _attr(heading_tag, "title")
    title = re.sub(r"^\s*(?:Greek|English)\s+subtitles\s+for\s+", "", title or "", flags=re.I)
    return _clean_text(title)


def _language_from_row(row, heading_tag, href):
    lowered = f"{row} {heading_tag} {href}".lower()
    if "elgif" in lowered or "/greek-subtitles/" in lowered or "greek subtitles" in lowered:
        return "ell"
    if "engif" in lowered or "/english-subtitles/" in lowered or "english subtitles" in lowered:
        return "eng"
    return None


def _uploader_from_row(row):
    match = _UPLOADER_RE.search(row)
    return _strip_tags(match.group("name")) if match else None


def _downloads_from_row(row):
    match = _DOWNLOADS_RE.search(row)
    return int(match.group("count")) if match else None


def _movie_title_from_release(release):
    title = _YEAR_RE.split(release or "", maxsplit=1)[0]
    return _clean_text(title)


def _years_from_text(text):
    return [int(match.group(1)) for match in _YEAR_RE.finditer(text or "")]


def _release_year_from_text(text):
    years = _years_from_text(text)
    return years[-1] if years else None


def _suggestion_matches(video, suggestion):
    suggestion_tokens = set(_tokens(suggestion.get("title")))
    title_tokens = _tokens((video or {}).get("title"))
    if title_tokens and not all(token in suggestion_tokens for token in title_tokens):
        return False
    year = (video or {}).get("year")
    return not year or str(year) in suggestion_tokens


def _suggestion_from_option(match):
    value = html.unescape(match.group("value"))
    title = _strip_tags(match.group("title"))
    if not value or not title:
        return None
    return {"title": title, "url": _absolute_url(value)}


def _accepted_candidates(video, candidates, requested, seen):
    accepted = []
    for item in candidates:
        if item["language"] not in requested:
            continue
        if not _candidate_matches_video(video, item):
            continue
        key = (item["detail_url"], item["language"])
        if key in seen:
            continue
        seen.add(key)
        accepted.append(item)
        if len(accepted) >= MAX_CANDIDATES_PER_QUERY:
            break
    return accepted


def _candidate_matches_video(video, item):
    matches = set(derive_matches(video, item))
    if "title" not in matches:
        return False
    requested_year = (video or {}).get("year")
    candidate_years = list((item or {}).get("years") or [])
    candidate_year = (item or {}).get("year")
    if not candidate_years and candidate_year:
        candidate_years.append(candidate_year)
    if requested_year and candidate_years and int(requested_year) not in {int(year) for year in candidate_years}:
        return False
    return True


def _attr(tag, name):
    match = re.search(rf"\b{name}\s*=\s*([\"'])(.*?)\1", tag or "", re.I | re.S)
    return html.unescape(match.group(2)) if match else ""


def _absolute_url(url):
    if not url:
        return ""
    value = html.unescape(url).strip()
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    legacy_path = (query.get("p") or [""])[0]
    if legacy_path.startswith(("movie-details/", "/movie-details/")):
        return urllib.parse.urljoin(f"{BASE_URL}/", legacy_path.lstrip("/"))
    return urllib.parse.urljoin(f"{BASE_URL}/", value)


def _subtitle_id_from_url(url):
    path = urllib.parse.urlparse(url).path.strip("/")
    return path.split("/")[-2] if "/" in path else _slug(path)


def _dimension(value):
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def _decode(body):
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return body.decode("utf-8", errors="replace")


def _strip_tags(fragment):
    return _clean_text(_TAG_RE.sub("", fragment or ""))


def _clean_text(value):
    return _WS_RE.sub(" ", html.unescape(value or "")).strip()


def _normalize(text):
    text = _coerce_text(text) or ""
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _tokens(text):
    return [token for token in _normalize(text).split() if token]


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _alpha3_for_language(language):
    if isinstance(language, str):
        value = language
    else:
        value = (language or {}).get("alpha3") or (language or {}).get("alpha2") or ""
    value = value.lower()
    if value in SUPPORTED_LANGUAGES:
        return value
    return ALPHA2_TO_ALPHA3.get(value, value)


def _matches_token_table(value, table, release_tokens):
    text = _coerce_text(value)
    if not text:
        return False
    for item in text.split():
        normalized = _normalize(item)
        candidates = table.get(item) or table.get(normalized) or []
        if any(_normalize(candidate) in release_tokens for candidate in candidates):
            return True
    normalized = _normalize(text)
    candidates = table.get(text) or table.get(normalized) or []
    return any(_normalize(candidate) in release_tokens for candidate in candidates)


def _normalize_release_group(value):
    return re.sub(r"[^a-z0-9]+", "", (_coerce_text(value) or "").lower())


def _slug(value):
    slug = "-".join(_tokens(value))[:80]
    return slug or "subtitle"


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _content_payload(content, subtitle_format="srt", empty=False):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially latin-1, which never fails to decode) only reintroduces mojibake.
    content = _normalize_line_endings(content or b"")
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": "text/plain",
        "format": subtitle_format or "srt",
        "empty": bool(empty),
    }


def _normalize_line_endings(content):
    normalized = content.replace(b"\r\n", b"\n")
    return normalized.replace(b"\r", b"\n")


def _decode_payload_text(payload):
    return base64.b64decode(payload["content_b64"]).decode("utf-8", errors="replace")


_SOURCE_TOKENS = {
    "blu ray": ["bluray", "brrip", "bdrip"],
    "bluray": ["bluray", "brrip", "bdrip"],
    "web": ["web", "webdl", "webrip"],
    "web dl": ["webdl", "web"],
    "webrip": ["webrip", "web"],
    "hdtv": ["hdtv"],
    "dvd": ["dvd", "dvdrip"],
    "hdrip": ["hdrip"],
}
_VIDEO_CODEC_TOKENS = {
    "h 264": ["h264", "x264"],
    "h264": ["h264", "x264"],
    "h 265": ["h265", "x265", "hevc"],
    "h265": ["h265", "x265", "hevc"],
    "divx": ["divx"],
    "xvid": ["xvid"],
}
_AUDIO_CODEC_TOKENS = {
    "ac3": ["ac3"],
    "eac3": ["eac3", "ddp"],
    "aac": ["aac"],
    "dts": ["dts"],
    "flac": ["flac"],
    "mp3": ["mp3"],
    "truehd": ["truehd"],
}
