"""Yavka.net provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in manifest
    cloudscraper = None

PROVIDER_ID = "yavkanet"
BASE_URL = "https://yavka.net"
HOME_URL = f"{BASE_URL}/"
HTTP_TIMEOUT_SECONDS = 15
# Transport-level retry: a single transient network blip (connection reset, DNS
# hiccup, read timeout, a 5xx/429 from the edge) should not abort the whole
# search/download. Mirrors upstream subliminal's RetryingSession/ProviderRetryMixin
# (~3 tries with exponential backoff). Only raw transport failures are retried;
# Cloudflare/Anubis handling and 4xx errors are left untouched.
HTTP_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 10000
MAX_FLARESOLVERR_TIMEOUT_MS = 30000
FLARESOLVERR_HTTP_TIMEOUT_BUFFER_SECONDS = 5
MAX_FLARESOLVERR_HTTP_TIMEOUT_SECONDS = 30
SUPPORTED_LANGUAGES = {
    "bul": "bg",
    "eng": "en",
    "rus": "ru",
    "spa": "es",
    "ita": "it",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
CLOUDFLARE_STATUS_CODES = {403, 429, 503}
CLOUDFLARE_BODY_MARKERS = (
    "just a moment",
    "challenge-platform",
    "_cf_chl_opt",
    "cf_chl",
    "cf-chl",
    "turnstile",
)

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_WS_RE = re.compile(r"\s+")
_TR_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*\bclass\s*=\s*['\"][^'\"]*(?:balon|selector)[^'\"]*['\"][^>]*)>(?P<title>.*?)</a>", re.I | re.S)
_A_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
_IMDB_ITEM_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*\bclass\s*=\s*['\"][^'\"]*\bimdb-subtitle-item\b[^'\"]*['\"][^>]*)>(?P<body>.*?)</a>",
    re.I | re.S,
)
_STRONG_RE = re.compile(r"<strong\b[^>]*>(?P<text>.*?)</strong>", re.I | re.S)
_FW_SPAN_RE = re.compile(
    r"<span\b(?P<attrs>[^>]*\bclass\s*=\s*['\"][^'\"]*\bfw-semibold\b[^'\"]*['\"][^>]*)>(?P<text>.*?)</span>",
    re.I | re.S,
)
_CLICK_RE = re.compile(r"<a\b[^>]*\bclass\s*=\s*['\"][^'\"]*click[^'\"]*['\"][^>]*>(?P<text>.*?)</a>", re.I | re.S)
_SPAN_RE = re.compile(r"<span\b(?P<attrs>[^>]*)>(?P<text>.*?)</span>", re.I | re.S)
_FORM_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.I | re.S)
_INPUT_RE = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.I | re.S)
_ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^'\"\s>]+))")
_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)")
_FPS_RE = re.compile(r"^\d+(?:\.\d+)?$")
_SXXEXX_RE = re.compile(r"\bs(?P<season>\d{1,2})\s*[._ -]?e(?P<episode>\d{1,3})\b", re.I)
_SEASON_RE = re.compile(r"\b(?:season|sezon|s)\s*0*(?P<season>\d{1,2})\b", re.I)
_EPISODE_RE = re.compile(r"\b(?:episode|ep|e)\s*0*(?P<episode>\d{1,3})\b", re.I)
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)


class CloudflareBlockedError(RuntimeError):
    """Raised when yavka.net presents an unresolved Cloudflare challenge."""


class _MissingCloudscraper:
    def create_scraper(self, *args, **kwargs):
        raise CloudflareBlockedError("yavkanet ai-cloudscraper dependency is not installed")


if cloudscraper is None:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = _MissingCloudscraper()


def _create_cloudscraper_session():
    kwargs = {
        "browser": {"custom": USER_AGENT},
        "interpreter": "native",
        "enable_cookie_persistence": False,
        "debug": False,
    }
    try:
        return cloudscraper.create_scraper(**kwargs)
    except TypeError as exc:
        if "enable_cookie_persistence" not in str(exc):
            raise
        kwargs.pop("enable_cookie_persistence")
        return cloudscraper.create_scraper(**kwargs)


def parse_imdb_results(body):
    text = _decode(body)
    rows = []
    seen = set()
    for tr_match in _TR_RE.finditer(text):
        row_html = tr_match.group("body")
        anchor = _ANCHOR_RE.search(row_html)
        if not anchor:
            continue
        attrs = _attrs(anchor.group("attrs"))
        href = attrs.get("href") or ""
        if not href:
            continue
        page_url = _absolute_url(href).rstrip("/") + "/"
        if page_url in seen:
            continue
        seen.add(page_url)
        title = _strip_tags(anchor.group("title"))
        notes = _strip_tags(html.unescape(attrs.get("content") or ""))
        release = notes or title
        rows.append(
            {
                "page_url": page_url,
                "title": title,
                "release": release,
                "notes": notes,
                "year": _year_from_row(row_html),
                "fps": _fps_from_row(row_html),
                "uploader": _uploader_from_row(row_html),
                "language": _language_from_row(attrs, href),
            }
        )
    for item_match in _IMDB_ITEM_RE.finditer(text):
        attrs = _attrs(item_match.group("attrs"))
        href = attrs.get("href") or ""
        if not href:
            continue
        page_url = _absolute_url(href).rstrip("/") + "/"
        if page_url in seen:
            continue
        seen.add(page_url)
        row_html = item_match.group("body")
        strong = _STRONG_RE.search(row_html)
        title = _strip_tags(strong.group("text")) if strong else _strip_tags(row_html)
        release = _strip_tags(row_html)
        rows.append(
            {
                "page_url": page_url,
                "title": title,
                "release": release,
                "notes": release,
                "year": _year_from_row(row_html),
                "fps": _fps_from_row(row_html),
                "uploader": _current_uploader_from_row(row_html),
                "language": _language_from_row(attrs, href),
            }
        )
    return rows[-50:]


def parse_download_form(body, page_url=None):
    text = _decode(body)
    direct_url = _direct_download_url(text)
    if direct_url:
        return {"method": "GET", "action_url": direct_url, "data": {}}
    for match in _FORM_RE.finditer(text):
        attrs = _attrs(match.group("attrs"))
        action = attrs.get("action") or ""
        if _is_search_form(action, match.group("body")):
            continue
        method = (attrs.get("method") or "GET").upper()
        action_url = _absolute_url(action or page_url or HOME_URL).rstrip("/") + "/"
        data = {}
        for input_match in _INPUT_RE.finditer(match.group("body")):
            input_attrs = _attrs(input_match.group("attrs"))
            name = input_attrs.get("name")
            if name:
                data[name] = input_attrs.get("value", "")
        if not data:
            continue
        return {"method": method, "action_url": action_url, "data": data}
    raise ValueError("yavkanet detail page did not expose a download form")


def derive_matches(video, row):
    video = video or {}
    row = row or {}
    text = " ".join(_coerce_text(row.get(key)) for key in ("title", "release", "notes"))
    matches = []
    kind = video.get("kind")
    if kind == "movie":
        if _title_in_text(video.get("title"), text):
            matches.append("title")
        if _same_int(video.get("year"), row.get("year")):
            matches.append("year")
    elif kind == "episode":
        if _title_in_text(video.get("series"), text):
            matches.append("series")
        if _season_matches(video.get("season"), text):
            matches.append("season")
        if _episode_matches(video.get("episode"), text):
            matches.append("episode")
    if _release_group_matches(video.get("release_group"), text):
        matches.append("release_group")
    if _token_in_text(video.get("resolution"), text):
        matches.append("resolution")
    if _source_matches(video.get("source"), text):
        matches.append("source")
    return matches


def is_cloudflare_challenge(status_code, headers, body):
    normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    if normalized_headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        status = 0
    if status not in CLOUDFLARE_STATUS_CODES:
        return False
    body_text = (body or b"").decode("utf-8", errors="ignore").lower()
    return any(marker in body_text for marker in CLOUDFLARE_BODY_MARKERS)


def http_get(url, timeout=HTTP_TIMEOUT_SECONDS, config=None, state=None, referer=None):
    return _http_request("GET", url, timeout=timeout, config=config, state=state, referer=referer)


def http_post(url, data=None, timeout=30, config=None, state=None, referer=None):
    return _http_request("POST", url, data=data or {}, timeout=timeout, config=config, state=state, referer=referer)


def extract_download(body, payload=None):
    payload = payload or {}
    filename = payload.get("filename") or ""
    # Reject broken responses up front: a 200 with an empty stream or an HTML/error
    # page would otherwise look like a successful download.
    if not body:
        raise ValueError("yavkanet download returned an empty body")
    if _looks_like_html(body):
        raise ValueError("yavkanet download returned HTML instead of a subtitle archive")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host. A pack can hold several members for the same episode (different
        # release group / resolution / source); the host's episode-only pick cannot tell
        # them apart, so when we can list a zip we pin the member matching the fields the
        # result was scored on. Otherwise (rar, single member, or no clear winner) let the
        # host pick the member by episode.
        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        member = _select_zip_member(body, payload)
        if member is not None:
            archive["member"] = member
        else:
            archive["episode"] = payload.get("episode")
        return archive
    # Direct, non-archive subtitle body.
    return _content_payload(body, _format_from_filename(filename))


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _select_zip_member(body, payload):
    # Pin the zip member matching the scored release group / resolution / source. Listing
    # only, no extraction or decoding: the host reads the named member and runs chardet.
    # Returns None for rar (not stdlib-listable), a single member (nothing to
    # disambiguate), or when no field breaks the tie, so the caller falls back to
    # host-side episode selection.
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
    # Narrow to the requested episode first; the host can already do this, but a pack with
    # several release groups per episode leaves it guessing among the matches.
    season = _safe_int(payload.get("season"))
    episode = _safe_int(payload.get("episode"))
    pool = members
    if season is not None and episode is not None:
        episode_pool = [name for name in members if _member_has_episode(name, season, episode)]
        if not episode_pool:
            # Episode requested but absent from every member: pinning a member from another
            # episode would hard-fail the host download, so defer to host episode selection.
            return None
        pool = episode_pool
    if len(pool) < 2:
        return None  # a single episode match: host episode selection already lands here
    # Break the tie with the same fields the result was scored on. Pin only a unique winner.
    video = payload.get("video") or {}
    best, best_score, tied = None, 0, False
    for name in pool:
        score = _member_match_score(name, video)
        if score > best_score:
            best, best_score, tied = name, score, False
        elif score == best_score and best is not None:
            tied = True
    if best is None or best_score == 0 or tied:
        return None  # cannot confidently disambiguate; let the host pick by episode
    return best


def _member_has_episode(name, season, episode):
    # Tolerate separated SxxExx tokens (S01.E02, S01 E02, S01-E02) as well as contiguous
    # S01E02, while keeping the (?!\d) guard so "e02" never matches "e020".
    text = name.lower()
    return bool(
        re.search(rf"s0*{season}[\s._-]*e0*{episode}(?!\d)", text)
        or re.search(rf"(?<!\d){season}x0*{episode}(?!\d)", text)
    )


def _member_match_score(name, video):
    # release_group is the strongest release signal (mirrors search _score: rg 15 vs
    # resolution/source 8 each), so weight it above resolution and source combined.
    score = 0
    if _release_group_matches(video.get("release_group"), name):
        score += 5
    if _token_in_text(video.get("resolution"), name):
        score += 2
    if _source_matches(video.get("source"), name):
        score += 2
    return score


class YavkaNetProvider:
    def __init__(self):
        self._http_state = {}

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, config=None, state=None, referer=None):
        return http_get(url, timeout=timeout, config=config, state=state or self._http_state, referer=referer)

    def _http_post(self, url, data, timeout=30, config=None, state=None, referer=None):
        return http_post(url, data=data, timeout=timeout, config=config, state=state or self._http_state, referer=referer)

    def search(self, video, languages, config):
        config = dict(config or {})
        requested = _requested_languages(languages)
        if not requested:
            return []
        video = video or {}
        if video.get("kind") not in ("movie", "episode"):
            return []
        imdb_id = _imdb_id(video.get("series_imdb_id") if video.get("kind") == "episode" else video.get("imdb_id"))
        if not imdb_id:
            return []
        imdb_url = f"{BASE_URL}/imdb/{imdb_id}"
        _sleep(config)
        body = self._http_get(imdb_url, config=config, state=self._http_state, referer=HOME_URL)
        results = []
        seen = set()
        for row in parse_imdb_results(body):
            if not _row_matches_video(video, row):
                continue
            _sleep(config)
            try:
                form = parse_download_form(
                    self._http_get(row["page_url"], config=config, state=self._http_state, referer=imdb_url),
                    row["page_url"],
                )
            except Exception:
                continue
            for language in requested:
                if row.get("language") and row["language"] != language["alpha3"]:
                    continue
                key = (row["page_url"], language["alpha3"], language["hi"], language["forced"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(_result_from_row(video, row, form, language))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        config = dict(config or {})
        payload = provider_payload or {}
        download_url = payload.get("download_url")
        form_data = payload.get("form_data") or {}
        if not download_url:
            raise ValueError("yavkanet download requires download_url")
        method = str(payload.get("method") or ("POST" if form_data else "GET")).upper()
        if method == "GET":
            body = self._http_get(
                download_url,
                timeout=30,
                config=config,
                state=self._http_state,
                referer=payload.get("page_url") or download_url,
            )
        else:
            body = self._http_post(
                download_url,
                form_data,
                timeout=30,
                config=config,
                state=self._http_state,
                referer=payload.get("page_url") or download_url,
            )
        return extract_download(body, payload)


def _result_from_row(video, row, form, language):
    matches = derive_matches(video, row)
    score = _score(matches)
    alpha2 = language["alpha2"]
    filename = _filename(video, row, alpha2)
    return {
        "provider": PROVIDER_ID,
        "id": f"yavkanet-{hashlib.sha1((row['page_url'] + language['alpha3']).encode('utf-8')).hexdigest()[:16]}",
        "language": dict(language),
        "release_info": row["release"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": bool(language.get("hi")),
        "page_link": row["page_url"],
        "display": {
            "source": "yavka.net",
            "title": row["title"],
            "release": row["release"],
            "uploader": row.get("uploader"),
            "fps": row.get("fps"),
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "method": form["method"],
            "download_url": form["action_url"],
            "form_data": form["data"],
            "filename": filename,
            "release": row["release"],
            "page_url": row["page_url"],
            "language": language["alpha3"],
            "season": _safe_int((video or {}).get("season")),
            "episode": _safe_int((video or {}).get("episode")),
            "video": _video_payload(video),
        },
    }


def _http_request(method, url, data=None, timeout=HTTP_TIMEOUT_SECONDS, config=None, state=None, referer=None):
    state = state if state is not None else {}
    config = config or {}
    scraper = _get_cloudscraper(state)
    request_kwargs = {
        "timeout": timeout,
        "headers": _request_headers(state, referer),
    }
    if method == "POST":
        request_kwargs["data"] = data or {}
    try:
        response = _request_with_retry(scraper, method, url, request_kwargs)
    except Exception as exc:
        if _flaresolverr_url(config) and _is_cloudflare_exception(exc):
            return _flaresolverr_request(method, url, data=data, timeout=timeout, config=config, state=state)
        raise
    body = getattr(response, "content", None)
    if body is None:
        body = str(getattr(response, "text", "")).encode("utf-8")
    status_code = getattr(response, "status_code", 0)
    if is_anubis_challenge(getattr(response, "url", ""), status_code) or _has_anubis_challenge_body(body):
        solved = solve_anubis_challenge(scraper, response.url, url, timeout=timeout)
        if not solved:
            raise CloudflareBlockedError("yavkanet Anubis challenge could not be solved")
        response = _request_with_retry(scraper, method, url, request_kwargs)
        body = getattr(response, "content", None)
        if body is None:
            body = str(getattr(response, "text", "")).encode("utf-8")
        status_code = getattr(response, "status_code", 0)
    headers = getattr(response, "headers", {}) or {}
    if is_cloudflare_challenge(status_code, headers, body):
        if _flaresolverr_url(config):
            return _flaresolverr_request(method, url, data=data, timeout=timeout, config=config, state=state)
        raise CloudflareBlockedError(
            "yavkanet hit a Cloudflare challenge and no FlareSolverr URL is configured"
        )
    try:
        status = int(status_code)
    except (TypeError, ValueError):
        status = 0
    if status == 403 and _flaresolverr_url(config):
        return _flaresolverr_request(method, url, data=data, timeout=timeout, config=config, state=state)
    if status >= 400:
        raise urllib.error.HTTPError(url, status, f"HTTP {status}", headers, None)
    return body


def _request_with_retry(scraper, method, url, request_kwargs):
    """Perform the raw cloudscraper call with a bounded transport retry.

    Only TRANSIENT transport failures are retried: connection/DNS/reset errors,
    read timeouts, and responses carrying a transient HTTP status (5xx / 429).
    A Cloudflare/Anubis exception, an HTTP 4xx other than 429, and any other
    exception propagate unchanged so the caller's existing FlareSolverr fallback
    and status handling run exactly as before. When retries are exhausted the
    final exception is re-raised, or the final (still-transient) response is
    returned so the caller maps it the same way it does today.
    """
    last_response = None
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            response = scraper.post(url, **request_kwargs) if method == "POST" else scraper.get(url, **request_kwargs)
        except Exception as exc:
            # A Cloudflare/challenge exception is not a transport blip: hand it
            # straight back so the FlareSolverr fallback can take over.
            if _is_cloudflare_exception(exc) or not _is_transient_transport_error(exc):
                raise
            if attempt >= HTTP_MAX_ATTEMPTS:
                raise
            _sleep_backoff(attempt)
            continue
        retry_after = _transient_response_retry_after(response)
        if retry_after is None or attempt >= HTTP_MAX_ATTEMPTS:
            return response
        last_response = response
        _sleep_backoff(attempt, retry_after)
    return last_response


def _is_transient_transport_error(exc):
    # Raw transport blips that a quick retry can recover from. Parse/value errors,
    # auth failures, and HTTP 4xx HTTPErrors are deliberately excluded.
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError)):
        return True
    # cloudscraper rides on requests/urllib3; match their transient transport
    # families by name so we do not hard-depend on those modules being importable.
    transient_names = {
        "ConnectionError",
        "ConnectTimeout",
        "ReadTimeout",
        "Timeout",
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "NewConnectionError",
        "NameResolutionError",
        "ProtocolError",
        "MaxRetryError",
        "ChunkedEncodingError",
    }
    for klass in type(exc).__mro__:
        if klass.__name__ in transient_names:
            return True
    return False


def _transient_response_retry_after(response):
    """Return a non-negative delay if the response is a retryable 5xx/429.

    Returns None when the response is not transient (so it is returned as-is).
    A 429/5xx that is actually a Cloudflare challenge is left for the existing
    challenge handling, not retried here.
    """
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        return None
    if status not in RETRY_STATUS_CODES:
        return None
    headers = getattr(response, "headers", {}) or {}
    body = getattr(response, "content", None)
    if body is None:
        body = str(getattr(response, "text", "")).encode("utf-8")
    if is_cloudflare_challenge(status, headers, body):
        return None
    return _retry_after_seconds(headers)


def _retry_after_seconds(headers):
    normalized = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    raw = normalized.get("retry-after")
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return 0.0


def _sleep_backoff(attempt, retry_after=0.0):
    delay = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
    delay = min(delay, RETRY_BACKOFF_CAP_SECONDS)
    delay = max(delay, retry_after)
    delay = min(delay, RETRY_BACKOFF_CAP_SECONDS)
    # Module-level time.sleep so tests can monkeypatch provider.time.sleep.
    time.sleep(delay)


def _get_cloudscraper(state):
    scraper = state.get("cloudscraper")
    if scraper is None:
        scraper = _create_cloudscraper_session()
        state["cloudscraper"] = scraper
    return scraper


def is_anubis_challenge(url, status_code=0):
    return "/.within.website/" in (url or "") or (
        status_code in (307, 401, 403) and ".within.website" in (url or "")
    )


def _has_anubis_challenge_body(body):
    if isinstance(body, bytes):
        text = body.decode("utf-8", "ignore")
    else:
        text = str(body or "")
    return _extract_anubis_challenge(text) is not None


def _extract_anubis_challenge(html_text):
    meta_match = _META_REFRESH_RE.search(html_text or "")
    if meta_match and "/.within.website/" in meta_match.group("url"):
        return {
            "method": "metarefresh",
            "redirect_url": meta_match.group("url"),
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
    challenge = data.get("challenge") or {}
    if "randomData" not in challenge or "id" not in challenge:
        return None
    return {
        "id": challenge["id"],
        "randomData": challenge["randomData"],
        "difficulty": int(challenge.get("difficulty", 4)),
        "method": challenge.get("method", "fast"),
    }


def _leading_zero_bits(digest_bytes):
    bits = 0
    for byte in digest_bytes:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def _solve_pow(random_data, difficulty):
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{random_data}{nonce}".encode("utf-8")).digest()
        if _leading_zero_bits(digest) >= difficulty:
            return nonce, digest.hex()
        nonce += 1


def _solve_preact(random_data, difficulty):
    return hashlib.sha256(random_data.encode("utf-8")).hexdigest(), difficulty * 0.125


def solve_anubis_challenge(session, challenge_url, original_url, timeout=HTTP_TIMEOUT_SECONDS):
    del original_url
    parsed = urllib.parse.urlparse(challenge_url)
    query = urllib.parse.parse_qs(parsed.query)
    redir = query.get("redir", [parsed.path])[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    challenge_page_url = challenge_url if challenge_url.startswith("http") else base + challenge_url
    started = time.monotonic()

    response = session.get(challenge_page_url, timeout=(10, timeout), allow_redirects=True)
    challenge = _extract_anubis_challenge(getattr(response, "text", ""))
    if not challenge:
        return None

    method = challenge["method"]
    if method == "metarefresh":
        redirect_url = challenge["redirect_url"]
        if not redirect_url.startswith("http"):
            redirect_url = base + redirect_url
        time.sleep(challenge.get("delay", 1))
        solved = session.get(redirect_url, timeout=(10, timeout), allow_redirects=True)
        if getattr(solved, "cookies", None):
            session.cookies.update(solved.cookies)
    elif method == "preact":
        result, delay = _solve_preact(challenge["randomData"], challenge["difficulty"])
        time.sleep(delay)
        params = {
            "id": challenge["id"],
            "result": result,
            "redir": redir,
            "elapsedTime": str(int((time.monotonic() - started) * 1000)),
        }
        solved = session.get(
            f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?{urllib.parse.urlencode(params)}",
            timeout=(10, timeout),
            allow_redirects=False,
        )
        if getattr(solved, "cookies", None):
            session.cookies.update(solved.cookies)
    else:
        nonce, digest = _solve_pow(challenge["randomData"], challenge["difficulty"])
        params = {
            "id": challenge["id"],
            "response": digest,
            "nonce": str(nonce),
            "redir": redir,
            "elapsedTime": str(int((time.monotonic() - started) * 1000)),
        }
        solved = session.get(
            f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?{urllib.parse.urlencode(params)}",
            timeout=(10, timeout),
            allow_redirects=False,
        )
        if getattr(solved, "cookies", None):
            session.cookies.update(solved.cookies)

    cookies = {}
    for cookie in getattr(session, "cookies", []) or []:
        if "anubis" in cookie.name.lower() or cookie.name == "PHPSESSID":
            cookies[cookie.name] = cookie.value
    return cookies or None


def _flaresolverr_request(method, url, data=None, timeout=HTTP_TIMEOUT_SECONDS, config=None, state=None):
    endpoint = _flaresolverr_url(config)
    if not endpoint:
        raise CloudflareBlockedError(
            "yavkanet hit a Cloudflare challenge and no FlareSolverr URL is configured"
        )
    timeout_ms = _flaresolverr_timeout_ms(config)
    payload = {
        "cmd": "request.post" if method == "POST" else "request.get",
        "url": url,
        "maxTimeout": timeout_ms,
    }
    if method == "POST":
        payload["postData"] = urllib.parse.urlencode(data or {})
        payload["headers"] = {"Content-Type": "application/x-www-form-urlencoded"}
    cookies = (state or {}).get("flaresolverr_cookies")
    if cookies:
        payload["cookies"] = [{"name": name, "value": value} for name, value in cookies.items()]
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        solver_timeout = min(
            MAX_FLARESOLVERR_HTTP_TIMEOUT_SECONDS,
            timeout_ms / 1000 + FLARESOLVERR_HTTP_TIMEOUT_BUFFER_SECONDS,
        )
        with urllib.request.urlopen(request, timeout=max(timeout, solver_timeout)) as response:
            body = response.read()
    except Exception as exc:
        raise CloudflareBlockedError(f"yavkanet FlareSolverr request failed: {exc}") from exc
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudflareBlockedError("yavkanet FlareSolverr returned invalid JSON") from exc
    if parsed.get("status") not in (None, "ok"):
        message = parsed.get("message") or "FlareSolverr did not solve the challenge"
        raise CloudflareBlockedError(f"yavkanet {message}")
    solution = parsed.get("solution") or {}
    response_text = solution.get("response")
    if response_text is None:
        raise CloudflareBlockedError("yavkanet FlareSolverr response had no page body")
    result_body = response_text if isinstance(response_text, bytes) else str(response_text).encode("utf-8")
    if is_cloudflare_challenge(solution.get("status") or 200, solution.get("headers") or {}, result_body):
        raise CloudflareBlockedError("yavkanet FlareSolverr response is still a Cloudflare challenge")
    _store_flaresolverr_solution(state, solution)
    return result_body


def _store_flaresolverr_solution(state, solution):
    if state is None:
        return
    user_agent = solution.get("userAgent")
    if user_agent:
        state["flaresolverr_user_agent"] = user_agent
    cookies = state.setdefault("flaresolverr_cookies", {})
    for cookie in solution.get("cookies") or []:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            cookies[name] = value


def _request_headers(state=None, referer=None):
    state = state or {}
    headers = {
        "User-Agent": state.get("flaresolverr_user_agent") or USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    cookie = _cookie_header(state.get("flaresolverr_cookies"))
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    return headers


def _cookie_header(cookies):
    if not cookies:
        return ""
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if name and value is not None)


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config):
    try:
        timeout = int((config or {}).get("flaresolverr_timeout_ms") or DEFAULT_FLARESOLVERR_TIMEOUT_MS)
    except (TypeError, ValueError):
        return DEFAULT_FLARESOLVERR_TIMEOUT_MS
    return max(5000, min(timeout, MAX_FLARESOLVERR_TIMEOUT_MS))


def _is_cloudflare_exception(exc):
    text = f"{exc.__class__.__name__} {exc}".lower()
    return "cloudflare" in text or "challenge" in text


def _attrs(fragment):
    return {
        match.group(1).lower(): html.unescape(next(group for group in match.groups()[1:] if group is not None))
        for match in _ATTR_RE.finditer(fragment or "")
    }


def _year_from_row(row_html):
    match = _YEAR_RE.search(_strip_tags(row_html))
    return int(match.group("year")) if match else None


def _fps_from_row(row_html):
    for match in _SPAN_RE.finditer(row_html):
        value = _strip_tags(match.group("text"))
        if _FPS_RE.match(value):
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _uploader_from_row(row_html):
    match = _CLICK_RE.search(row_html)
    return _strip_tags(match.group("text")) if match else ""


def _current_uploader_from_row(row_html):
    match = _FW_SPAN_RE.search(row_html)
    return _strip_tags(match.group("text")) if match else ""


def _language_from_row(attrs, href):
    value = (attrs.get("data-lang") or "").strip().lower()
    if not value:
        path = urllib.parse.urlparse(_absolute_url(href)).path.rstrip("/")
        value = path.rsplit("/", 1)[-1].lower() if "/" in path else ""
    return ALPHA2_TO_ALPHA3.get(value, value if value in SUPPORTED_LANGUAGES else "")


def _direct_download_url(text):
    for match in _A_RE.finditer(text):
        attrs = _attrs(match.group("attrs"))
        href = attrs.get("href") or ""
        if attrs.get("id") == "down" or "/download" in href:
            return _absolute_url(href)
    return ""


def _is_search_form(action, form_body):
    if "/search" in (action or "").lower():
        return True
    names = set()
    for input_match in _INPUT_RE.finditer(form_body or ""):
        name = _attrs(input_match.group("attrs")).get("name")
        if name:
            names.add(name.lower())
    return bool(names) and names <= {"sea"}


def _row_matches_video(video, row):
    matches = set(derive_matches(video, row))
    if video.get("kind") == "movie":
        if row.get("year") and video.get("year") and not _same_int(row.get("year"), video.get("year")):
            return False
        return True
    if video.get("kind") == "episode":
        if "series" not in matches:
            return False
        if video.get("episode") and "episode" not in matches:
            return False
        if video.get("season") and "season" not in matches:
            row_text = " ".join(_coerce_text(row.get(key)) for key in ("title", "release", "notes"))
            if _has_season_episode_marker(row_text):
                return False
        return True
    return False


def _score(matches):
    score = 0
    for name, value in (
        ("title", 35),
        ("series", 30),
        ("year", 10),
        ("season", 10),
        ("episode", 20),
        ("release_group", 15),
        ("resolution", 8),
        ("source", 8),
    ):
        if name in matches:
            score += value
    return max(0, min(100, score))


def _requested_languages(languages):
    rows = []
    seen = set()
    for item in languages or []:
        alpha3 = _alpha3_for_language(item)
        if alpha3 not in SUPPORTED_LANGUAGES:
            continue
        if isinstance(item, dict):
            alpha2 = item.get("alpha2") or SUPPORTED_LANGUAGES[alpha3]
            forced = bool(item.get("forced", False))
        else:
            alpha2 = SUPPORTED_LANGUAGES[alpha3]
            forced = False
        # Yavka rows are never tagged as forced or hearing impaired, so a
        # forced-only request cannot be honoured. Drop it instead of returning a
        # full subtitle mislabeled as forced, and always emit the variant we can
        # actually verify: non-forced, non-HI.
        if forced:
            continue
        if alpha3 in seen:
            continue
        seen.add(alpha3)
        rows.append({"alpha3": alpha3, "alpha2": alpha2, "hi": False, "forced": False})
    return rows


def _alpha3_for_language(language):
    if isinstance(language, dict):
        value = language.get("alpha3") or ALPHA2_TO_ALPHA3.get(language.get("alpha2"))
    else:
        value = str(language or "")
    value = (value or "").lower()
    if value in ALPHA2_TO_ALPHA3:
        return ALPHA2_TO_ALPHA3[value]
    return value


def _video_payload(video):
    video = video or {}
    return {
        "kind": video.get("kind"),
        "title": video.get("title"),
        "series": video.get("series"),
        "year": video.get("year"),
        "season": video.get("season"),
        "episode": video.get("episode"),
        "release_group": video.get("release_group"),
        "resolution": video.get("resolution"),
        "source": video.get("source"),
    }


def _filename(video, row, alpha2):
    title = _slug((video or {}).get("title") or (video or {}).get("series") or row.get("title") or "subtitle")
    if (video or {}).get("kind") == "episode":
        season = _safe_int(video.get("season")) or 1
        episode = _safe_int(video.get("episode")) or 0
        return f"yavkanet.{title}.s{season:02d}e{episode:02d}.{alpha2}.zip"
    year = row.get("year") or (video or {}).get("year") or ""
    return f"yavkanet.{title}.{year}.{alpha2}.zip"


def _imdb_id(value):
    value = _coerce_text(value)
    if not value:
        return ""
    match = re.search(r"tt\d+", value)
    if match:
        return match.group(0)
    digits = re.search(r"\d+", value)
    return f"tt{digits.group(0)}" if digits else ""


def _title_in_text(title, text):
    title_tokens = _normalize(title).split()
    text_tokens = set(_normalize(text).split())
    return bool(title_tokens and all(token in text_tokens for token in title_tokens))


def _season_matches(season, text):
    try:
        season_int = int(season)
    except (TypeError, ValueError):
        return False
    for match in _SXXEXX_RE.finditer(text or ""):
        if int(match.group("season")) == season_int:
            return True
    for match in _SEASON_RE.finditer(text or ""):
        if int(match.group("season")) == season_int:
            return True
    return False


def _episode_matches(episode, text):
    try:
        episode_int = int(episode)
    except (TypeError, ValueError):
        return False
    for match in _SXXEXX_RE.finditer(text or ""):
        if int(match.group("episode")) == episode_int:
            return True
    for match in _EPISODE_RE.finditer(text or ""):
        if int(match.group("episode")) == episode_int:
            return True
    return False


def _has_season_episode_marker(text):
    return bool(_SXXEXX_RE.search(text or ""))


def _release_group_matches(release_group, text):
    release_group = _coerce_text(release_group)
    if not release_group:
        return False
    return release_group.lower() in (text or "").lower()


def _source_matches(source, text):
    source = _normalize(source)
    normalized = _normalize(text)
    if not source:
        return False
    if source in ("web", "webdl", "web dl", "web-dl"):
        return any(token in normalized for token in ("web dl", "web-dl", "webrip", "web rip", "web"))
    if source in ("bluray", "blu ray", "bdrip", "brrip"):
        return any(token in normalized for token in ("bluray", "blu ray", "bdrip", "brrip", "bd rip"))
    return source in normalized


def _token_in_text(token, text):
    token = _coerce_text(token)
    if not token:
        return False
    return token.lower() in (text or "").lower()


def _same_int(left, right):
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _content_payload(content, fmt):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    content = content or b""
    fmt = fmt or "srt"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "empty": False,
    }


def _content_type(fmt):
    if fmt == "srt":
        return "application/x-subrip"
    if fmt == "vtt":
        return "text/vtt"
    if fmt in ("ass", "ssa"):
        return "text/x-ssa"
    return "application/octet-stream"


def _format_from_filename(filename):
    return _subtitle_extension(filename) or "srt"


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _looks_like_html(body):
    sample = (body or b"").lstrip()[:1024].lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<body" in sample


def _is_rar_archive(body):
    return bool(body) and (body.startswith(b"Rar!\x1a\x07\x00") or body.startswith(b"Rar!\x1a\x07\x01\x00"))


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms", 0))
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)


def _decode(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _strip_tags(fragment):
    text = _BR_RE.sub(" ", fragment or "")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _coerce_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _normalize(value):
    text = _coerce_text(value).lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return _WS_RE.sub(" ", text).strip()


def _slug(value):
    normalized = _normalize(value)
    return re.sub(r"[^a-z0-9]+", ".", normalized).strip(".") or "subtitle"


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _absolute_url(url):
    url = html.unescape(url or "")
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urllib.parse.urljoin(HOME_URL, url)
