"""UNIT3D provider for the Bazarr+ Provider Hub catalog.

UNIT3D is the software behind many torrent trackers. Its subtitle API
lists the subtitles uploaded with each tracker's torrents, and a member can
download them with an API key. A Bazarr+ install usually belongs to several
trackers, so this provider takes a list of endpoints, each paired with its own
API key, and searches all of them at once inside one time budget. A failing
endpoint is logged, and the results it returned before it failed are kept. The
search raises for invalid settings, and when every endpoint failed.

The requests and responses follow the UNIT3D subtitle API, GET /api/subtitles
and GET /api/subtitles/{id}/download. A tracker that does not serve that API is
reported as lacking it.
"""

import base64
import concurrent.futures
import hashlib
import http.client
import json
import logging
import math
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple


PROVIDER_ID = "unit3d"
LOGGER = logging.getLogger("unit3d")
USER_AGENT = "BazarrProviderHub/1.0 (+https://github.com/LavX/bazarr-provider-catalog)"
MAX_ENDPOINTS = 16
MIN_TIMEOUT_SECONDS = 10
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 60
# A rate limit without a usable Retry-After header, and any other failed search
# in which the endpoint attempted requests and none succeeded, cools its endpoint for this long.
DEFAULT_COOLDOWN_SECONDS = 60
MAX_COOLDOWN_SECONDS = 86400
PER_PAGE = 50
# The API matches at most this many language codes in one request.
LANGUAGE_CHUNK = 50
DOWNLOAD_TIMEOUT_SECONDS = 30
# A search page is a small JSON document. A download may be a pack archive,
# which the host extracts, so it gets the host's archive size cap.
MAX_SEARCH_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
# A body is read in chunks of this size, and the deadline is checked between them.
READ_CHUNK_BYTES = 64 * 1024
SUBTITLE_EXTENSIONS = ("srt", "ass", "ssa", "vtt")
ARCHIVE_EXTENSIONS = ("zip",)
CANDIDATE_EXTENSIONS = SUBTITLE_EXTENSIONS + ARCHIVE_EXTENSIONS

# Base language codes the subtitle API accepts, keyed by the alpha3 code Bazarr
# uses. The API carries ISO 639-1 codes and does not separate regional
# variants, so a regional request maps to its base code.
_LANGUAGE_CODES = {
    "ara": "ar",
    "aze": "az",
    "bel": "be",
    "ben": "bn",
    "bos": "bs",
    "bul": "bg",
    "cat": "ca",
    "ces": "cs",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "epo": "eo",
    "est": "et",
    "eus": "eu",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "heb": "he",
    "hin": "hi",
    "hrv": "hr",
    "hun": "hu",
    "hye": "hy",
    "ind": "id",
    "isl": "is",
    "ita": "it",
    "jpn": "ja",
    "kal": "kl",
    "kan": "kn",
    "kat": "ka",
    "khm": "km",
    "kin": "rw",
    "kor": "ko",
    "kur": "ku",
    "lav": "lv",
    "lit": "lt",
    "mal": "ml",
    "mkd": "mk",
    "mon": "mn",
    "msa": "ms",
    "mya": "my",
    "nep": "ne",
    "nld": "nl",
    "nor": "no",
    "pan": "pa",
    "pol": "pl",
    "por": "pt",
    "pus": "ps",
    "ron": "ro",
    "rus": "ru",
    "sin": "si",
    "slk": "sk",
    "slv": "sl",
    "som": "so",
    "spa": "es",
    "sqi": "sq",
    "srp": "sr",
    "sun": "su",
    "swa": "sw",
    "swe": "sv",
    "tam": "ta",
    "tel": "te",
    "tgl": "tl",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "urd": "ur",
    "vie": "vi",
    "yor": "yo",
    "zho": "zh",
}
_ALPHA2_TO_CODE = {alpha2: alpha3 for alpha3, alpha2 in _LANGUAGE_CODES.items()}

_IMDB_MAX = 10**10
_monotonic = time.monotonic


class AuthenticationError(ValueError):
    """A tracker rejected the API key."""


class ConfigurationError(ValueError):
    """The settings, or a tracker's API path, cannot be used as configured."""


class ServiceUnavailable(RuntimeError):
    """A tracker did not answer usefully."""


class APIThrottled(RuntimeError):
    """A tracker is rate limiting the API. retry_after is the wait in seconds."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class _Refused(Exception):
    """An HTTP status a tracker answered with. Callers map it to one of the classes above."""

    def __init__(self, status, retry_after=None):
        super().__init__(status)
        self.status = status
        self.retry_after = retry_after


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so the API key never follows a Location header to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class _Endpoint(NamedTuple):
    tag: str
    base: str
    host: str
    key: str


class _Outcome(NamedTuple):
    candidates: list
    error: object
    failed: bool


def _endpoint_tag(base):
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _cooldown_key(endpoint):
    """The cooldown key: the endpoint tag and a short digest of its API key, never the key itself."""
    return endpoint.tag, hashlib.sha256(endpoint.key.encode("utf-8")).hexdigest()[:16]


def _normalize_endpoint(url, position):
    """Return the normalized base URL and host of one configured endpoint."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise ConfigurationError(f"UNIT3D endpoint {position} is not a valid URL") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ConfigurationError(f"UNIT3D endpoint {position} must start with http:// or https://")
    if "?" in url or "#" in url:
        raise ConfigurationError(f"UNIT3D endpoint {position} must not carry a query string or fragment")
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError(f"UNIT3D endpoint {position} must not carry user credentials")
    try:
        port = parts.port
    except ValueError:
        raise ConfigurationError(f"UNIT3D endpoint {position} has an invalid port") from None
    host = parts.hostname
    if not host:
        raise ConfigurationError(f"UNIT3D endpoint {position} must carry a host")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    base = f"{scheme}://{netloc}{parts.path.rstrip('/')}"
    if not base.isascii():
        raise ConfigurationError(f"UNIT3D endpoint {position} must use only ASCII in its host and path")
    return base, host


def _configured_endpoints(config):
    """Pair the endpoint list with the key list, validate both, and return the endpoints."""
    settings = config or {}
    urls = str(settings.get("endpoints") or "").split()
    keys = str(settings.get("api_keys") or "").split()
    if not urls:
        raise ConfigurationError("UNIT3D has no endpoints configured")
    if len(urls) != len(keys):
        raise ConfigurationError(
            f"UNIT3D has {len(urls)} endpoint(s) but {len(keys)} API key(s); "
            "list one key per endpoint, in the same order"
        )
    if len(urls) > MAX_ENDPOINTS:
        LOGGER.warning(
            "UNIT3D accepts at most %d endpoints; ignoring the last %d of %d",
            MAX_ENDPOINTS,
            len(urls) - MAX_ENDPOINTS,
            len(urls),
        )
        urls, keys = urls[:MAX_ENDPOINTS], keys[:MAX_ENDPOINTS]
    endpoints = []
    bases = set()
    tags = set()
    for position, (url, key) in enumerate(zip(urls, keys), start=1):
        if not re.fullmatch(r"[\x21-\x7e]+", key):
            raise ConfigurationError(
                f"UNIT3D endpoint {position} has an API key with a control or non-ASCII character"
            )
        base, host = _normalize_endpoint(url, position)
        tag = _endpoint_tag(base)
        if base in bases:
            raise ConfigurationError(f"UNIT3D endpoint {position} duplicates an earlier endpoint")
        if tag in tags:
            raise ConfigurationError(f"UNIT3D endpoint {position} collides with an earlier endpoint")
        bases.add(base)
        tags.add(tag)
        endpoints.append(_Endpoint(tag, base, host, key))
    return endpoints


def _timeout_seconds(config):
    value = (config or {}).get("timeout_seconds")
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, str) and value.strip().isdecimal():
        try:
            value = int(value.strip())
        except ValueError:
            value = None
    if isinstance(value, bool) or not isinstance(value, int):
        value = None
    if value is None or not MIN_TIMEOUT_SECONDS <= value <= MAX_TIMEOUT_SECONDS:
        raise ConfigurationError(
            f"UNIT3D timeout_seconds must be a whole number from {MIN_TIMEOUT_SECONDS} to {MAX_TIMEOUT_SECONDS}"
        )
    return value


def _match_files(config):
    value = (config or {}).get("match_files")
    return True if value is None else bool(value)


def _as_int(value):
    """An int from an int or a plain decimal string, or None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _positive_int(value):
    number = _as_int(value)
    return number if number is not None and number > 0 else None


def _imdb_number(value):
    text = str(value or "").strip().lower()
    if text.startswith("tt"):
        text = text[2:]
    if not text.isdecimal():
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if 0 < number < _IMDB_MAX else None


def _retry_after_seconds(value):
    seconds = _positive_int(value)
    return min(seconds, MAX_COOLDOWN_SECONDS) if seconds else None


def _file_name(video):
    original = video.get("original_name")
    if not isinstance(original, str):
        return None
    name = re.split(r"[\\/]", original)[-1].strip()
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return name if 0 < len(name) <= 255 else None


def _requested_codes(languages):
    """The (alpha3, alpha2) base pairs asked for, in request order.

    Forced requests are skipped because the API has no forced subtitles. A
    regional request maps to its base language, and hearing-impaired requests
    are searched as the plain language.
    """
    pairs = []
    seen = set()
    for language in languages or []:
        if isinstance(language, dict):
            if language.get("forced"):
                continue
            alpha3 = language.get("alpha3")
        else:
            alpha3 = language
        base = str(alpha3 or "").split("-", 1)[0].lower()
        alpha2 = _LANGUAGE_CODES.get(base)
        if alpha2 is None or alpha2 in seen:
            continue
        seen.add(alpha2)
        pairs.append((base, alpha2))
    return pairs


def _search_query(video, match_files):
    """The request parameters that identify the video, or None when it cannot be searched."""
    kind = video.get("kind")
    query = {"type": kind}
    if kind == "episode":
        season = _as_int(video.get("season"))
        episode = _as_int(video.get("episode"))
        if season is None or not 0 <= season <= 65535 or episode is None or not 1 <= episode <= 65535:
            return None
        query["season"] = season
        query["episode"] = episode
        tvdb_id = _positive_int(video.get("series_tvdb_id"))
        if tvdb_id is not None:
            query["tvdb_id"] = tvdb_id
        imdb_number = _imdb_number(video.get("series_imdb_id"))
        title = video.get("series")
    else:
        tmdb_id = _positive_int(video.get("tmdb_id"))
        if tmdb_id is not None:
            query["tmdb_id"] = tmdb_id
        imdb_number = _imdb_number(video.get("imdb_id"))
        title = video.get("title")
    if imdb_number is not None:
        query["imdb_id"] = f"tt{imdb_number:07d}"
    if not any(key in query for key in ("tmdb_id", "tvdb_id", "imdb_id")):
        year = _as_int(video.get("year"))
        title = title.strip() if isinstance(title, str) else ""
        if not title or len(title) > 255 or year is None or not 1870 <= year <= 2100:
            return None
        try:
            title.encode("utf-8")
        except UnicodeEncodeError:
            return None
        query["title"] = title
        query["year"] = year
    if match_files:
        size = _positive_int(video.get("size"))
        if size is not None:
            query["file_size"] = size
        file_name = _file_name(video)
        if file_name is not None:
            query["file_name"] = file_name
    return query


def _response_socket(response):
    # http.client keeps the socket on private attributes and has no public way to
    # change its timeout mid-response. Without them, only the deadline check applies.
    raw = getattr(getattr(response, "fp", None), "raw", None)
    return getattr(raw, "_sock", None)


def _read_body(response, max_bytes, deadline):
    """Read at most max_bytes + 1 bytes in chunks, checking the deadline before every read.

    When _response_socket returns the socket, each read waits no longer than the
    time left before the deadline. Otherwise the deadline is checked between reads
    only. A body that ends before its Content-Length is an error. A body past
    max_bytes stops the read early, and the caller reports it as too large.
    """
    sock = _response_socket(response)
    body = bytearray()
    # length is the number of bytes a Content-Length response still owes, or None.
    # Stopping at zero avoids an extra read that would only detect the end.
    while len(body) <= max_bytes and response.length != 0:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise ServiceUnavailable("the endpoint did not finish sending within the time budget")
        if sock is not None:
            sock.settimeout(remaining)
        chunk = response.read1(min(READ_CHUNK_BYTES, max_bytes + 1 - len(body)))
        if not chunk:
            break
        body.extend(chunk)
    if len(body) <= max_bytes and getattr(response, "length", None):
        raise ServiceUnavailable("the endpoint closed the connection before the body was complete")
    return bytes(body)


def _fetch(endpoint, path, params, timeout, max_bytes, deadline):
    """GET one API path with the endpoint's key. Returns (body, content_type) for HTTP 200.

    The timeout bounds the connection and each read of the response headers. The
    deadline is an absolute monotonic time that bounds the whole body: each body
    read waits no longer than the time left before it when the socket is reachable,
    and the deadline is checked between reads otherwise.

    Redirects are refused, so the key never follows a Location header. Failures
    carry a fixed message only: never the URL, the key, or a response body.
    """
    url = endpoint.base + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, */*",
            "Authorization": f"Bearer {endpoint.key}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = response.getcode()
            if status != 200:
                raise _Refused(status)
            try:
                body = _read_body(response, max_bytes, deadline)
            except ssl.SSLError:
                raise ServiceUnavailable(
                    "the endpoint closed the TLS connection before the body was complete"
                ) from None
            content_type = response.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as error:
        try:
            retry_after = error.headers.get("Retry-After") if error.headers is not None else None
            raise _Refused(error.code, retry_after) from None
        finally:
            error.close()
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLError):
            raise ConfigurationError("the TLS handshake with the endpoint failed") from None
        raise ServiceUnavailable("the endpoint is unreachable") from None
    except ssl.SSLError:
        raise ConfigurationError("the TLS handshake with the endpoint failed") from None
    except (OSError, http.client.HTTPException):
        raise ServiceUnavailable("the endpoint closed the connection or timed out") from None
    if len(body) > max_bytes:
        raise ServiceUnavailable("the endpoint sent a response larger than the limit")
    return body, content_type


def _parse_page(body):
    """Return (records, matched_by, current_page, last_page) from one search page."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ServiceUnavailable("the search response is not JSON") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not isinstance(meta, dict):
        raise ServiceUnavailable("the search response has no results list")
    current = _as_int(meta.get("current_page"))
    last = _as_int(meta.get("last_page"))
    if current is None or last is None:
        raise ServiceUnavailable("the search response has no page metadata")
    return data, meta.get("matched_by"), current, last


def _same_imdb(first, second):
    number = _imdb_number(first)
    return number is not None and number == _imdb_number(second)


def _same_id(first, second):
    number = _positive_int(first)
    return number is not None and number == _positive_int(second)


def _matches(video, record, matched_by):
    """The subliminal-shaped match keys one record earns against the video.

    Episode series identity uses the series keys, because a bare imdb_id or
    tvdb_id would also award season and episode equivalence. A missing value on
    either side is never a match. An episode matched by series id also reports
    the series match, which the host requires before it accepts an episode.
    """
    matches = []
    if video.get("kind") == "episode":
        if _same_imdb(video.get("series_imdb_id"), record.get("imdb_id")):
            matches.append("series_imdb_id")
        if _same_id(video.get("series_tvdb_id"), record.get("tvdb_id")):
            matches.append("series_tvdb_id")
        if "series_imdb_id" in matches or "series_tvdb_id" in matches:
            matches.append("series")
        season = _as_int(record.get("season"))
        if season is not None and season == _as_int(video.get("season")):
            matches.append("season")
        episode = _as_int(record.get("episode"))
        if episode is not None and episode == _as_int(video.get("episode")):
            matches.append("episode")
        if matched_by == "title":
            if "series" not in matches:
                matches.append("series")
            matches.append("year")
    else:
        if _same_id(video.get("tmdb_id"), record.get("tmdb_id")):
            matches.append("tmdb_id")
        if _same_imdb(video.get("imdb_id"), record.get("imdb_id")):
            matches.append("imdb_id")
        if matched_by == "title":
            matches.extend(("title", "year"))
    return matches


def _exact_file(release_match):
    """True when the tracker flagged both the size and the name of the exact file."""
    return (
        isinstance(release_match, dict)
        and release_match.get("file_size") is True
        and release_match.get("file_name") is True
    )


def _score(matches):
    """Identity strength: a verified hash is highest, an exact episode, title or movie id match is high."""
    if "hash" in matches:
        return 100
    if "episode" in matches or "title" in matches or "imdb_id" in matches or "tmdb_id" in matches:
        return 95
    return 80


def _candidate(video, endpoint, record, matched_by, requested):
    """One worker candidate from one search record, or None when the record cannot be offered."""
    if not isinstance(record, dict):
        return None
    record_id = _positive_int(record.get("id"))
    alpha2 = str(record.get("language") or "").lower()
    alpha3 = _ALPHA2_TO_CODE.get(alpha2)
    extension = str(record.get("extension") or "").lower().lstrip(".")
    filename = record.get("filename")
    if record_id is None or alpha3 not in requested or extension not in CANDIDATE_EXTENSIONS:
        return None
    if not isinstance(filename, str) or not filename:
        return None
    # A record of another media type is never offered, and a pack is offered only as a ZIP archive.
    if record.get("type") != video.get("kind"):
        return None
    if record.get("pack") and extension != "zip":
        return None
    release = record.get("release") if isinstance(record.get("release"), str) else ""
    matches = _matches(video, record, matched_by)
    score_without_hash = _score(matches)
    # The tracker's flag for the exact file is the basis for the hash claim. The host
    # verifies the claim against series, season, episode and source.
    hash_verifiable = bool(release) and _exact_file(record.get("release_match"))
    if hash_verifiable:
        matches.append("hash")
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "endpoint": endpoint.tag,
        "subtitle_id": record_id,
        "extension": extension,
    }
    if video.get("kind") == "episode":
        payload["season"] = _as_int(video.get("season"))
        payload["episode"] = _as_int(video.get("episode"))
    uploader = record.get("uploader") if isinstance(record.get("uploader"), str) else None
    candidate = {
        "provider": PROVIDER_ID,
        "id": f"{endpoint.tag}:{record_id}",
        "language": {"alpha3": alpha3, "alpha2": alpha2, "hi": None, "forced": None},
        "release_info": release,
        "filename": filename,
        "matches": matches,
        "score": _score(matches),
        "score_without_hash": score_without_hash,
        "score_out_of": 100,
        "hash_verifiable": hash_verifiable,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": None,
        "display": {
            "source": PROVIDER_ID,
            "tracker": endpoint.host,
            "uploader": uploader,
            "downloads": _as_int(record.get("downloads")),
        },
        "provider_payload": payload,
    }
    torrent_id = _positive_int(record.get("torrent_id"))
    if torrent_id is not None:
        candidate["page_link"] = f"{endpoint.base}/torrents/{torrent_id}"
    return candidate


def _content_type(subtitle_format):
    if subtitle_format in ("ass", "ssa"):
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    return "application/x-subrip"


def _empty_download(subtitle_format):
    return {
        "content_b64": "",
        "content_sha256": "",
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "empty": True,
    }


def _skip_download(endpoint, subtitle_id, subtitle_format, reason):
    """Log one line for a download this endpoint cannot supply, and return the empty result for it."""
    LOGGER.warning("UNIT3D endpoint %s skipped subtitle %s: %s", endpoint.tag, subtitle_id, reason)
    return _empty_download(subtitle_format)


def _is_archive(body):
    return body.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def _is_rar(body):
    return body.startswith(b"Rar!\x1a\x07")


def _is_json(body, content_type):
    if "json" in (content_type or "").lower():
        return True
    if body.lstrip()[:1] not in (b"{", b"["):
        return False
    try:
        json.loads(body)
    except ValueError:
        return False
    except RecursionError:
        # Nested too deeply to parse, so it is treated as JSON and skipped.
        return True
    return True


def _looks_like_html(body):
    head = body[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<!doctype", b"<html", b"<head", b"<body"))


def _log_failure(endpoint, error, partial):
    """One bounded line per failed request: the tag, the class and the reason, never a body or a key."""
    outcome = "returned partial results" if partial else "failed"
    LOGGER.warning("UNIT3D endpoint %s %s: %s: %s", endpoint.tag, outcome, type(error).__name__, error)


def _all_failed_error(errors):
    """The error raised when every endpoint failed: the shared class if they agree, else ServiceUnavailable."""
    kinds = {type(error) for error in errors}
    if len(kinds) == 1:
        (kind,) = kinds
        if kind is APIThrottled:
            return APIThrottled(str(errors[0]), retry_after=max(error.retry_after for error in errors))
        if kind in (AuthenticationError, ConfigurationError):
            return kind(str(errors[0]))
    return ServiceUnavailable("every UNIT3D endpoint failed; see the endpoint log lines")


class UNIT3DProvider:
    def __init__(self):
        # Cooldown key (see _cooldown_key) to (the monotonic time its cooldown ends, a copy of the error).
        # The worker keeps one provider instance, so cooldowns last across searches.
        self._cooldowns = {}
        # Guards the cooldown writes, which the search threads and the download call both make.
        self._cooldown_lock = threading.Lock()

    def search(self, video, languages, config):
        endpoints = _configured_endpoints(config)
        timeout = _timeout_seconds(config)
        match_files = _match_files(config)
        video = dict(video or {})
        pairs = _requested_codes(languages)
        if not pairs or video.get("kind") not in ("movie", "episode"):
            return []
        query = _search_query(video, match_files)
        if query is None:
            return []
        requested = {alpha3 for alpha3, _alpha2 in pairs}
        chunks = [pairs[index:index + LANGUAGE_CHUNK] for index in range(0, len(pairs), LANGUAGE_CHUNK)]
        deadline = _monotonic() + timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
            outcomes = list(
                pool.map(
                    lambda endpoint: self._search_contained(endpoint, video, query, chunks, requested, deadline),
                    endpoints,
                )
            )
        if all(outcome.failed for outcome in outcomes):
            raise _all_failed_error([outcome.error for outcome in outcomes])
        return [candidate for outcome in outcomes for candidate in outcome.candidates]

    def download(self, provider_payload, language, config):
        del language  # the subtitle id names the file, whatever language it was searched under
        endpoints = _configured_endpoints(config)
        payload = dict(provider_payload or {})
        endpoint = next((item for item in endpoints if item.tag == payload.get("endpoint")), None)
        extension = payload.get("extension")
        subtitle_format = extension if extension in SUBTITLE_EXTENSIONS else "srt"
        subtitle_id = _positive_int(payload.get("subtitle_id"))
        if endpoint is None:
            LOGGER.warning(
                "UNIT3D skipped subtitle %s: the endpoint %s is not configured", subtitle_id, payload.get("endpoint")
            )
            return _empty_download(subtitle_format)
        if subtitle_id is None:
            LOGGER.warning("UNIT3D endpoint %s skipped a download: the payload carries no subtitle id", endpoint.tag)
            return _empty_download(subtitle_format)
        try:
            body, content_type = _fetch(
                endpoint,
                f"/api/subtitles/{subtitle_id}/download",
                None,
                DOWNLOAD_TIMEOUT_SECONDS,
                MAX_DOWNLOAD_BYTES,
                _monotonic() + DOWNLOAD_TIMEOUT_SECONDS,
            )
        except _Refused as refusal:
            if refusal.status == 429:
                raise self._refuse(endpoint, refusal) from None
            return _skip_download(endpoint, subtitle_id, subtitle_format, f"HTTP {refusal.status}")
        except (ServiceUnavailable, ConfigurationError) as error:
            return _skip_download(endpoint, subtitle_id, subtitle_format, error)
        if not body:
            return _skip_download(endpoint, subtitle_id, subtitle_format, "the tracker returned an empty download")
        if _is_rar(body):
            return _skip_download(endpoint, subtitle_id, subtitle_format, "the tracker sent a RAR archive")
        if _is_archive(body):
            # The host lists the archive and picks the member for this episode.
            return {
                "archive_b64": base64.b64encode(body).decode("ascii"),
                "archive_sha256": hashlib.sha256(body).hexdigest(),
                "episode": _as_int(payload.get("episode")),
            }
        if _is_json(body, content_type):
            return _skip_download(endpoint, subtitle_id, subtitle_format, "the tracker answered with JSON")
        if _looks_like_html(body):
            return _skip_download(endpoint, subtitle_id, subtitle_format, "the tracker returned a web page")
        # No encoding is declared: the host detects it when it normalizes the text.
        return {
            "content_b64": base64.b64encode(body).decode("ascii"),
            "content_sha256": hashlib.sha256(body).hexdigest(),
            "content_type": _content_type(subtitle_format),
            "format": subtitle_format,
            "empty": False,
        }

    def _search_contained(self, endpoint, video, query, chunks, requested, deadline):
        """Run one endpoint's search. An unexpected exception fails only this endpoint."""
        try:
            return self._search_endpoint(endpoint, video, query, chunks, requested, deadline)
        except Exception as error:
            # Last resort for an error outside the page loop. This path cools nothing.
            LOGGER.warning("UNIT3D endpoint %s failed unexpectedly: %s", endpoint.tag, type(error).__name__)
            return _Outcome([], ServiceUnavailable("the endpoint failed unexpectedly"), True)

    def _search_endpoint(self, endpoint, video, query, chunks, requested, deadline):
        """Fetch every page of every language chunk from one endpoint. A rate limit ends it early."""
        cooldown = self._cooldowns.get(_cooldown_key(endpoint))
        now = _monotonic()
        if cooldown is not None and cooldown[0] > now:
            LOGGER.info("UNIT3D endpoint %s skipped: cooling down after a failed search", endpoint.tag)
            error = cooldown[1]
            if isinstance(error, APIThrottled):
                error = APIThrottled(
                    "the tracker is rate limiting the API", retry_after=math.ceil(cooldown[0] - now)
                )
            return _Outcome([], error, True)
        candidates = []
        seen = set()
        errors = []
        successes = 0
        budget_spent = False
        rate_limited = False
        for chunk in chunks:
            page = 1
            while True:
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    budget_spent = True
                    LOGGER.info("UNIT3D endpoint %s: search budget spent, skipping the remaining pages", endpoint.tag)
                    break
                params = dict(
                    query,
                    language=",".join(alpha2 for _alpha3, alpha2 in chunk),
                    perPage=PER_PAGE,
                    page=page,
                )
                try:
                    body, _content_type = _fetch(
                        endpoint, "/api/subtitles", params, remaining, MAX_SEARCH_BYTES, deadline
                    )
                    records, matched_by, current, last = _parse_page(body)
                    successes += 1
                    mapped = [_candidate(video, endpoint, record, matched_by, requested) for record in records]
                except _Refused as refusal:
                    errors.append(self._refuse(endpoint, refusal))
                    # A rate limit ends the endpoint's search, so its remaining chunks are not requested.
                    rate_limited = refusal.status == 429
                    break
                except (ServiceUnavailable, ConfigurationError) as error:
                    errors.append(error)
                    break
                except Exception as error:
                    LOGGER.warning("UNIT3D endpoint %s failed unexpectedly: %s", endpoint.tag, type(error).__name__)
                    errors.append(ServiceUnavailable("the endpoint failed unexpectedly"))
                    break
                # A page merges only after every one of its records has mapped, so a mapping failure drops the page.
                for candidate in mapped:
                    if candidate is not None and candidate["id"] not in seen:
                        seen.add(candidate["id"])
                        candidates.append(candidate)
                if current >= last:
                    break
                page += 1
            if budget_spent or rate_limited:
                break
        # A rate limit already put its cooldown in force in _refuse.
        if not successes and errors and not rate_limited:
            self._cool_down(endpoint, errors[0])
        if not successes and not errors:
            errors.append(ServiceUnavailable("the search budget was spent before this endpoint was queried"))
        for error in errors:
            _log_failure(endpoint, error, partial=successes > 0)
        if successes:
            return _Outcome(candidates, None, False)
        # A rate limit is the error the cooldown replays, so the first search raises the same class.
        return _Outcome([], errors[-1] if rate_limited else errors[0], True)

    def _cool_down(self, endpoint, error, seconds=DEFAULT_COOLDOWN_SECONDS):
        """Skip the endpoint in later searches for the given seconds, and keep a copy of the error.

        A cooldown already in force that ends later than the new one is kept, so a shorter cooldown
        never shortens it. It returns the expiry in force after the call, as a monotonic time.
        """
        key = _cooldown_key(endpoint)
        until = _monotonic() + seconds
        with self._cooldown_lock:
            current = self._cooldowns.get(key)
            if current is not None and current[0] > until:
                return current[0]
            # The copy carries no traceback, so the stored error keeps no frames or response alive.
            self._cooldowns[key] = (until, type(error)(str(error)))
            return until

    def _refuse(self, endpoint, refusal):
        """The exception one HTTP refusal raises as. A 429 also cools the endpoint down."""
        status = refusal.status
        if status in (401, 403):
            return AuthenticationError(f"the tracker rejected the API key (HTTP {status})")
        if status == 404:
            return ConfigurationError(f"the tracker does not provide the UNIT3D subtitle API (HTTP {status})")
        if status == 422:
            LOGGER.error(
                "UNIT3D endpoint %s rejected the request as invalid (HTTP 422); this is a provider bug",
                endpoint.tag,
            )
            return ConfigurationError(f"the tracker rejected the request as invalid (HTTP {status})")
        if status == 429:
            interval = _retry_after_seconds(refusal.retry_after) or DEFAULT_COOLDOWN_SECONDS
            error = APIThrottled(f"the tracker is rate limiting the API (HTTP {status})", retry_after=interval)
            ends_at = self._cool_down(endpoint, error, interval)
            error.retry_after = math.ceil(ends_at - _monotonic())
            return error
        if 300 <= status < 400:
            return ServiceUnavailable(f"redirects are refused (HTTP {status})")
        return ServiceUnavailable(f"the tracker failed to answer (HTTP {status})")
