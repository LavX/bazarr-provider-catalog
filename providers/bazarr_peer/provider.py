"""Bazarr+ Peer provider for the Bazarr+ Provider Hub catalog.

A Bazarr+ peer is another Bazarr+ install that shares its subtitle library
and the providers it has configured. This provider takes a list of peer
addresses, each paired with its own API key, and searches all of them at once
inside one time budget. A failing peer is logged, and the results of the other
peers are kept. The search raises for invalid settings, and when every peer
failed.

The requests and responses follow the Bazarr+ compat API under /api/v1:
subtitle search, provider listing, login, and download. A search excludes
this provider, so a peer never returns results that came from another
Bazarr+ Peer provider. A download fetches the stream link the peer issues,
without the API key.
"""

import base64
import concurrent.futures
import hashlib
import http.client
import json
import logging
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple


PROVIDER_ID = "bazarr_peer"
LOGGER = logging.getLogger("bazarr_peer")
USER_AGENT = "BazarrProviderHub/1.0 (+https://github.com/LavX/bazarr-provider-catalog)"
MAX_ENDPOINTS = 16
MIN_TIMEOUT_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 45
MAX_TIMEOUT_SECONDS = 90
# A rate limit without a usable Retry-After header, any other failed search in which the endpoint sent
# requests and none succeeded, and an endpoint-level download error, cool its endpoint for this long.
DEFAULT_COOLDOWN_SECONDS = 60
MAX_COOLDOWN_SECONDS = 86400
# The peer's answer on whether it lists its local provider is reused for this long.
PROBE_CACHE_SECONDS = 600
# The download call's budget. Its login, its download request and its recovery search share it.
# The stream fetch gets this long of its own.
DOWNLOAD_TIMEOUT_SECONDS = 30
# A search page is a small JSON document. A download is one subtitle file, or a pack
# archive that the host extracts, so it gets the host's archive size cap.
MAX_SEARCH_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
# A body is read in chunks of this size, and the deadline is checked between them.
READ_CHUNK_BYTES = 64 * 1024
MAX_ERROR_BYTES = 4096
# The message text kept from a refusal, at most this many characters. It is never logged.
DETAIL_MAX_CHARS = 256
# A saved payload larger than this drops its recovery context and stable key, so it stays small.
CONTEXT_MAX_BYTES = 1024
QUERY_MAX_CHARS = 128
# The OpenSubtitles hash reads the first and last block of the file.
HASH_BLOCK_BYTES = 64 * 1024
HASH_KEYS = ("bsplayer", "opensubtitlescom", "napisy24", "opensubtitles")
SUBTITLE_FORMAT = "srt"
OPENSUBTITLES_HOST = "api.opensubtitles.com"
DISTRIBUTION_OFF = "Distribution Hub is off on this instance"
COMPAT_DISABLED_DETAIL = "compat endpoint disabled"
TRANSIENT_MESSAGE = "the peer is temporarily unavailable (peer_unavailable)"
# Stream responses that a candidate-local skip reports, by status.
_STREAM_REASONS = {410: "stream_expired", 403: "blocked_by_peer", 404: "not_found", 503: "peer_unavailable"}

# Base language codes the Bazarr+ compat API accepts, keyed by the alpha3 code
# Bazarr uses. A regional request maps to the code the peer searches by, and
# the peer reports the language of each subtitle with that code.
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
    """The peer rejected the API key."""


class ConfigurationError(ValueError):
    """The settings, or the peer's API path, cannot be used as configured."""


class ServiceUnavailable(RuntimeError):
    """A peer did not answer usefully."""


class APIThrottled(RuntimeError):
    """A peer is rate limiting the API. retry_after is the wait in seconds."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class _Refused(Exception):
    """An HTTP status a peer answered with. Callers map it to one of the classes above.

    reason is the x-reason header. detail is the text of the JSON "error" or "message" value of
    the body, and detail_key names which of the two it came from. Neither is ever logged.
    """

    def __init__(self, status, retry_after=None, reason=None, detail=None, detail_key=None):
        super().__init__(status)
        self.status = status
        self.retry_after = retry_after
        self.reason = reason
        self.detail = detail
        self.detail_key = detail_key


def _is_compat_disabled(refusal):
    """True for the two refusals a disabled compat API sends: a 404 at boot, and a 503 at runtime."""
    if refusal.status == 404:
        return refusal.reason == "compat-disabled"
    return refusal.status == 503 and refusal.detail_key == "error" and refusal.detail == COMPAT_DISABLED_DETAIL


class _Skip(Exception):
    """A download this endpoint cannot supply. The caller returns an empty download for it."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


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


class _Options(NamedTuple):
    library_only: bool
    use_hash: bool
    include_ai_translated: bool


class _Outcome(NamedTuple):
    candidates: list
    error: object
    failed: bool


def _endpoint_tag(base):
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _credential_key(endpoint):
    """The cache key for one endpoint and key: the endpoint tag and a short digest of its API key.

    The cooldown, the token cache and the provider list cache all use it, so a corrected key
    is queried at once, and the key itself is never stored.
    """
    return endpoint.tag, hashlib.sha256(endpoint.key.encode("utf-8")).hexdigest()[:16]


def _normalize_endpoint(url, position):
    """Return the normalized base URL and host of one configured endpoint."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} is not a valid URL") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} must start with http:// or https://")
    if "?" in url or "#" in url:
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} must not carry a query string or fragment")
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} must not carry user credentials")
    try:
        port = parts.port
    except ValueError:
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} has an invalid port") from None
    host = parts.hostname
    if not host:
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} must carry a host")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    base = f"{scheme}://{netloc}{parts.path.rstrip('/')}"
    if not base.isascii():
        raise ConfigurationError(f"Bazarr+ Peer endpoint {position} must use only ASCII in its host and path")
    return base, host


def _configured_endpoints(config):
    """Pair the endpoint list with the key list, validate both, and return the endpoints."""
    settings = config or {}
    urls = str(settings.get("endpoints") or "").split()
    keys = str(settings.get("api_keys") or "").split()
    if not urls:
        raise ConfigurationError("Bazarr+ Peer has no endpoints configured")
    if len(urls) != len(keys):
        raise ConfigurationError(
            f"Bazarr+ Peer has {len(urls)} endpoint(s) but {len(keys)} API key(s); "
            "list one key per endpoint, in the same order"
        )
    endpoints = []
    bases = set()
    tags = set()
    for position, (url, key) in enumerate(zip(urls, keys), start=1):
        if not re.fullmatch(r"[\x21-\x7e]+", key):
            raise ConfigurationError(
                f"Bazarr+ Peer endpoint {position} has an API key with a control or non-ASCII character"
            )
        base, host = _normalize_endpoint(url, position)
        if host.lower() == OPENSUBTITLES_HOST or host.lower().endswith("." + OPENSUBTITLES_HOST):
            raise ConfigurationError(
                f"Bazarr+ Peer endpoint {position}: this is the OpenSubtitles service, "
                "enter the address of a Bazarr+ instance"
            )
        tag = _endpoint_tag(base)
        if base in bases:
            raise ConfigurationError(f"Bazarr+ Peer endpoint {position} duplicates an earlier endpoint")
        if tag in tags:
            raise ConfigurationError(f"Bazarr+ Peer endpoint {position} collides with an earlier endpoint")
        bases.add(base)
        tags.add(tag)
        endpoints.append(_Endpoint(tag, base, host, key))
    if len(endpoints) > MAX_ENDPOINTS:
        LOGGER.warning(
            "Bazarr+ Peer accepts at most %d endpoints; ignoring the last %d of %d",
            MAX_ENDPOINTS,
            len(endpoints) - MAX_ENDPOINTS,
            len(endpoints),
        )
        endpoints = endpoints[:MAX_ENDPOINTS]
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
            f"Bazarr+ Peer timeout_seconds must be a whole number from {MIN_TIMEOUT_SECONDS} to {MAX_TIMEOUT_SECONDS}"
        )
    return value


def _options(config):
    settings = config or {}

    def flag(name, default):
        value = settings.get(name)
        return value if isinstance(value, bool) else default

    return _Options(
        library_only=flag("library_only", True),
        use_hash=flag("use_hash", True),
        include_ai_translated=flag("include_ai_translated", False),
    )


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
    """The (alpha3, code) pairs asked for, in request order, where code is the peer's language code.

    Forced requests are skipped because the peer has no forced-only search. Portuguese for
    Brazil is searched as pt-BR; every other request maps to its base language. Hearing-impaired
    requests are searched as the plain language.
    """
    pairs = []
    seen = set()
    for language in languages or []:
        country = None
        if isinstance(language, dict):
            if language.get("forced"):
                continue
            alpha3 = language.get("alpha3")
            country = language.get("country_alpha2")
        else:
            alpha3 = language
        base = str(alpha3 or "").split("-", 1)[0].lower()
        alpha2 = _LANGUAGE_CODES.get(base)
        if alpha2 is None:
            continue
        code = alpha2
        if base == "por" and isinstance(country, str) and country.upper() == "BR":
            code = "pt-BR"
        if code in seen:
            continue
        seen.add(code)
        pairs.append((base, code))
    return pairs


def _has_identifier(identity):
    return any(key in identity for key in ("imdb_id", "tmdb_id", "query", "moviehash"))


def _identity(video, options):
    """The identity parameters of one search, or None when the video has nothing to search by.

    The result is also the context a stale download recovers with, so every key here is one
    the search sends.
    """
    kind = video.get("kind")
    identity = {"type": kind}
    if kind == "episode":
        season = _as_int(video.get("season"))
        episode = _as_int(video.get("episode"))
        if season is None or not 0 <= season <= 65535 or episode is None or not 1 <= episode <= 65535:
            return None
        identity["season_number"] = season
        identity["episode_number"] = episode
        imdb_number = _imdb_number(video.get("series_imdb_id"))
    else:
        imdb_number = _imdb_number(video.get("imdb_id"))
    tmdb_id = _positive_int(video.get("tmdb_id"))
    if imdb_number is not None:
        identity["imdb_id"] = f"tt{imdb_number:07d}"
    elif tmdb_id is not None:
        identity["tmdb_id"] = tmdb_id
    if kind == "episode" and imdb_number is None and tmdb_id is not None:
        # A tmdb-only episode also sends the file name: the peer resolves tmdb ids through its movie table only.
        name = _file_name(video)
        if name is not None:
            identity["query"] = name
    if options.use_hash:
        digest = _video_hash(video)
        if digest is not None:
            identity["moviehash"] = digest
        size = _positive_int(video.get("size"))
        if size is None and digest is not None:
            size = _file_size(video.get("original_path"))
        if size is not None:
            identity["moviebytesize"] = size
    return identity if _has_identifier(identity) else None


def _video_hash(video):
    hashes = video.get("hashes")
    if isinstance(hashes, dict):
        for name in HASH_KEYS:
            value = hashes.get(name)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{16}", value):
                return value
    return _opensubtitles_hash(video.get("original_path"))


def _opensubtitles_hash(path):
    """The OpenSubtitles hash of a file: its size plus the little-endian 64-bit words of its first and last block.

    Returns None when the path cannot be read or the file is smaller than two blocks.
    """
    if not isinstance(path, str) or not path:
        return None
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size < 2 * HASH_BLOCK_BYTES:
                return None
            head = handle.read(HASH_BLOCK_BYTES)
            handle.seek(size - HASH_BLOCK_BYTES)
            tail = handle.read(HASH_BLOCK_BYTES)
    except (OSError, ValueError) as error:
        # The path stays out of the log line: it can name a private folder.
        LOGGER.warning("Bazarr+ Peer could not read the video for its hash: %s", type(error).__name__)
        return None
    total = size
    for block in (head, tail):
        for offset in range(0, len(block) - 7, 8):
            total = (total + int.from_bytes(block[offset:offset + 8], "little")) & 0xFFFFFFFFFFFFFFFF
    return f"{total:016x}"


def _file_size(path):
    """The size in bytes of a file, or None when the path is not a string or os.stat fails on it."""
    if not isinstance(path, str):
        return None
    try:
        return os.stat(path).st_size
    except OSError:
        return None


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


def _error_detail(error):
    """The text of the JSON "error" value of a small error body, or else its "message" value, with its key.

    Returns (None, None) when the body carries neither. The body is never logged.
    """
    # A chunked body that is cut short or malformed raises HTTPException (IncompleteRead, LineTooLong), not OSError.
    try:
        raw = error.read(MAX_ERROR_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    for key in ("error", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return key, value[:DETAIL_MAX_CHARS]
    return None, None


def _request(url, headers, timeout, max_bytes, deadline, method="GET", data=None):
    """Send one request and return (body, content_type) for HTTP 200.

    The timeout bounds the connection and each read of the response headers. The
    deadline is an absolute monotonic time that bounds the whole body: each body
    read waits no longer than the time left before it when the socket is reachable,
    and the deadline is checked between reads otherwise.

    Redirects are refused, so the key never follows a Location header. Failures
    carry a fixed message only: never the URL, the key, the token or a response body.
    A request whose timeout is no longer positive is not sent, because its budget is spent.
    """
    if timeout <= 0:
        raise ServiceUnavailable("the time budget was spent before the request was sent")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
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
            headers_out = error.headers
            retry_after = headers_out.get("Retry-After") if headers_out is not None else None
            reason = headers_out.get("x-reason") if headers_out is not None else None
            detail_key, detail = _error_detail(error)
            raise _Refused(error.code, retry_after, reason, detail, detail_key) from None
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


def _fetch(endpoint, path, params, timeout, max_bytes, deadline, method="GET", token=None, payload=None):
    """Call one API path of an endpoint with its API key, and return (body, content_type) for HTTP 200.

    The key travels in the Api-Key header. A token, when given, travels in the
    Authorization header as a bearer token. A payload is sent as a JSON body.
    """
    url = endpoint.base + path
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/json, */*",
        "Api-Key": endpoint.key,
        "User-Agent": USER_AGENT,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    return _request(url, headers, timeout, max_bytes, deadline, method=method, data=data)


def _fetch_stream(link, timeout, deadline):
    """Fetch a stream link the peer issued. No credential is sent, and redirects are refused."""
    headers = {"Accept": "*/*", "User-Agent": USER_AGENT}
    return _request(link, headers, timeout, MAX_DOWNLOAD_BYTES, deadline)


def _parse_search(body):
    """Return (rows, total_count) from one search response."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ServiceUnavailable("the search response is not JSON") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ServiceUnavailable("the search response has no results list")
    total = _as_int(payload.get("total_count"))
    return data, total


def _parse_providers(body):
    """Return the set of provider names one providers response lists."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ServiceUnavailable("the providers response is not JSON") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ServiceUnavailable("the providers response has no list")
    return frozenset(
        item["name"] for item in data if isinstance(item, dict) and isinstance(item.get("name"), str)
    )


def _parse_token(body):
    payload = _parse_object(body, "the login response is not JSON")
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise ServiceUnavailable("the login response carries no token")
    return token


def _parse_link(body):
    payload = _parse_object(body, "the download response is not JSON")
    link = payload.get("link")
    if not isinstance(link, str) or not link:
        raise ServiceUnavailable("the download response carries no link")
    return link


def _parse_object(body, message):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ServiceUnavailable(message) from None
    if not isinstance(payload, dict):
        raise ServiceUnavailable(message)
    return payload


def _row_identity(row, endpoint):
    """The ids one search row carries: its subtitle id, its stable key, its download file id and its kind.

    A local row (a subtitle the peer stores) gets a key without its trailing file id, which
    the peer mints on every search. Its key carries the file name, so a local row without a
    non-empty file name has no identity and is not offered. A remote row's key is its subtitle id.
    """
    if not isinstance(row, dict):
        return None
    attributes = row.get("attributes")
    if not isinstance(attributes, dict):
        return None
    subtitle_id = attributes.get("subtitle_id")
    if not isinstance(subtitle_id, str) or not subtitle_id:
        return None
    files = attributes.get("files")
    first = files[0] if isinstance(files, list) and files and isinstance(files[0], dict) else None
    file_id = _positive_int(first.get("file_id")) if first is not None else None
    if file_id is None:
        return None
    name = first.get("file_name")
    file_name = name if isinstance(name, str) and name else f"subtitle-{file_id}.srt"
    if subtitle_id.startswith("local-"):
        if not isinstance(name, str) or not name:
            LOGGER.debug(
                "Bazarr+ Peer endpoint %s: a local row without a file name is not offered (file %s)",
                endpoint.tag,
                file_id,
            )
            return None
        stem = subtitle_id.rsplit("-", 1)[0]
        return {
            "subtitle_id": subtitle_id,
            "key": f"{stem}|{file_name}",
            "file_id": file_id,
            "file_name": file_name,
            "row": "local",
        }
    return {
        "subtitle_id": subtitle_id,
        "key": subtitle_id,
        "file_id": file_id,
        "file_name": file_name,
        "row": "remote",
    }


def _extension(file_name):
    """The lower-case extension of a subtitle file name, or the subtitle format when it has none."""
    suffix = os.path.splitext(file_name)[1].lstrip(".").lower()
    return suffix if re.fullmatch(r"[a-z0-9]{1,8}", suffix) else SUBTITLE_FORMAT


def _row_language(value, requested):
    """(alpha3, alpha2, country) for a row's language code, or None when it is not requested.

    requested maps each requested alpha3 code to the countries asked for under it, with None for a
    plain request. A row with a country is kept only for a plain request or one that asks for that
    country. A row without a country is kept whenever its language is requested.
    """
    text = value if isinstance(value, str) else ""
    base, _separator, country = text.partition("-")
    alpha3 = _ALPHA2_TO_CODE.get(base.lower())
    if alpha3 is None or alpha3 not in requested:
        return None
    region = country.upper() if len(country) == 2 and country.isalpha() else None
    if region is not None and None not in requested[alpha3] and region not in requested[alpha3]:
        return None
    return alpha3, base.lower(), region


def _same_imdb(first, second):
    number = _imdb_number(first)
    return number is not None and number == _imdb_number(second)


def _matches(video, details):
    """The match keys one row earns against the video, from its feature details.

    Episode series identity uses the series IMDb id, so a bare season or episode number
    is never a match on its own. An episode matched by series IMDb id also reports the
    series match. A missing value on either side is never a match.
    """
    matches = []
    if video.get("kind") == "episode":
        if _same_imdb(video.get("series_imdb_id"), details.get("imdb_id")):
            matches.append("series_imdb_id")
            matches.append("series")
        season = _as_int(details.get("season_number"))
        if season is not None and season == _as_int(video.get("season")):
            matches.append("season")
        episode = _as_int(details.get("episode_number"))
        if episode and episode == _as_int(video.get("episode")):
            matches.append("episode")
    elif _same_imdb(video.get("imdb_id"), details.get("imdb_id")):
        matches.append("imdb_id")
    return matches


def _score(matches):
    """Identity strength: a verified hash is highest, an exact episode, series or movie id match is high."""
    if "hash" in matches:
        return 100
    if any(match in matches for match in ("episode", "series_imdb_id", "imdb_id", "tmdb_id")):
        return 95
    return 80


def _candidate(video, endpoint, row, requested, options, identity):
    """One worker candidate from one search row, or None when the row cannot be offered."""
    ident = _row_identity(row, endpoint)
    if ident is None:
        return None
    attributes = row["attributes"]
    language = _row_language(attributes.get("language"), requested)
    if language is None:
        return None
    details = attributes.get("feature_details")
    if not isinstance(details, dict):
        details = {}
    feature_type = details.get("feature_type")
    if isinstance(feature_type, str) and feature_type.lower() != str(video.get("kind")):
        return None
    ai_translated = attributes.get("ai_translated") is True
    if ai_translated and not options.include_ai_translated:
        return None
    alpha3, alpha2, region = language
    # A flag the peer omits, or sends as a non-boolean, is unknown: None, not False.
    hearing_impaired = attributes.get("hearing_impaired")
    forced = attributes.get("foreign_parts_only")
    hearing_impaired = hearing_impaired if isinstance(hearing_impaired, bool) else None
    forced = forced if isinstance(forced, bool) else None
    release = attributes.get("release") if isinstance(attributes.get("release"), str) else ""
    # The peer's hash match is its own claim. It is offered as verified only with a release name.
    hash_verifiable = attributes.get("moviehash_match") is True and bool(release)
    matches = _matches(video, details)
    score_without_hash = _score(matches)
    if hash_verifiable:
        matches.append("hash")
    uploader = attributes.get("uploader")
    uploader_name = uploader.get("name") if isinstance(uploader, dict) else None
    if not isinstance(uploader_name, str):
        uploader_name = None
    language_fields = {"alpha3": alpha3, "alpha2": alpha2, "hi": hearing_impaired, "forced": forced}
    if region is not None:
        language_fields["country_alpha2"] = region
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "endpoint": endpoint.tag,
        "row": ident["row"],
        "key": ident["key"],
        "file_id": ident["file_id"],
        "subtitle_id": ident["subtitle_id"],
        "extension": _extension(ident["file_name"]),
    }
    if video.get("kind") == "episode":
        payload["episode"] = _as_int(video.get("episode"))
    context = dict(identity, languages=[attributes["language"]])
    if "query" in context and len(context["query"]) > QUERY_MAX_CHARS:
        del context["query"]
        LOGGER.warning("Bazarr+ Peer endpoint %s: query_dropped, the saved context keeps no file name", endpoint.tag)
    payload["context"] = context
    if len(json.dumps(payload, sort_keys=True).encode("utf-8")) > CONTEXT_MAX_BYTES:
        # The stable key goes with the context: without both, a later 404 cannot be recovered.
        del payload["context"]
        del payload["key"]
        LOGGER.warning(
            "Bazarr+ Peer endpoint %s: context_too_large, the subtitle cannot be recovered after a peer restart",
            endpoint.tag,
        )
    # The stable key keeps a local row's id the same when the peer mints a new file id for it.
    candidate = {
        "provider": PROVIDER_ID,
        "id": f"{endpoint.tag}:{ident['key']}",
        "language": language_fields,
        "release_info": release,
        "filename": ident["file_name"],
        "matches": matches,
        "score": _score(matches),
        "score_without_hash": score_without_hash,
        "score_out_of": 100,
        "hash_verifiable": hash_verifiable,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": hearing_impaired,
        "ai_translated": ai_translated,
        "display": {
            "source": PROVIDER_ID,
            "peer": endpoint.host,
            "uploader": uploader_name,
            "downloads": _as_int(attributes.get("download_count")),
        },
        "provider_payload": payload,
    }
    url = attributes.get("url")
    if _is_web_url(url):
        candidate["page_link"] = url
    return candidate


def _content_type():
    return "application/x-subrip"


def _empty_download():
    return {
        "content_b64": "",
        "content_sha256": "",
        "content_type": _content_type(),
        "format": SUBTITLE_FORMAT,
        "empty": True,
    }


def _skip_download(endpoint, file_id, reason):
    """Log one line for a download this endpoint cannot supply, and return the empty result for it."""
    LOGGER.warning("Bazarr+ Peer endpoint %s skipped file %s: %s", endpoint.tag, file_id, reason)
    return _empty_download()


def _link_belongs_to(link, endpoint):
    """True when a stream link is on the endpoint's origin and under its base path."""
    if not isinstance(link, str):
        return False
    try:
        parts = urllib.parse.urlsplit(link)
        base = urllib.parse.urlsplit(endpoint.base)
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower())
        base_port = base.port or {"http": 80, "https": 443}.get(base.scheme.lower())
    except ValueError:
        return False
    if parts.scheme.lower() != base.scheme.lower():
        return False
    if (parts.hostname or "").lower() != (base.hostname or "").lower() or port != base_port:
        return False
    return parts.path.startswith(base.path.rstrip("/") + "/api/v1/download/stream/")


def _is_web_url(value):
    """True for an absolute http or https URL that names a host."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


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


def _search_params(identity, options, languages, timeout):
    """The query parameters of one search: the identity, the languages, the scope and the budget."""
    params = dict(identity, languages=languages, exclude_providers=PROVIDER_ID, page=1, timeout_seconds=timeout)
    if options.library_only:
        params["only_providers"] = "local"
    return params


def _log_failure(endpoint, error):
    """One bounded line per failed search: the tag, the class and the reason, never a body or a key."""
    LOGGER.warning("Bazarr+ Peer endpoint %s failed: %s: %s", endpoint.tag, type(error).__name__, error)


def _all_failed_error(errors):
    """The error raised when every endpoint failed: the shared class if they agree, else ServiceUnavailable."""
    kinds = {type(error) for error in errors}
    if len(kinds) == 1:
        (kind,) = kinds
        if kind is APIThrottled:
            return APIThrottled(str(errors[0]), retry_after=max(error.retry_after for error in errors))
        if kind in (AuthenticationError, ConfigurationError):
            return kind(str(errors[0]))
    return ServiceUnavailable("every Bazarr+ Peer endpoint failed; see the endpoint log lines")


def _remaining(deadline):
    return deadline - _monotonic()


def _timeout_param(remaining):
    """The timeout_seconds of one search: the remaining budget in whole seconds, from 1 to MAX_TIMEOUT_SECONDS."""
    return max(1, min(MAX_TIMEOUT_SECONDS, math.ceil(remaining)))


def _saved_search(payload):
    """The identity and the language code a payload's saved context searches by, or None when it has none."""
    context = payload.get("context")
    if not isinstance(context, dict) or not isinstance(payload.get("key"), str):
        return None
    languages = context.get("languages")
    code = languages[0] if isinstance(languages, list) and languages else None
    identity = {name: value for name, value in context.items() if name != "languages"}
    if not isinstance(code, str) or not _has_identifier(identity):
        return None
    return identity, code


class BazarrPeerProvider:
    def __init__(self):
        # Credential key (see _credential_key) to (the monotonic time its cooldown ends, the error that cooled it).
        # The worker keeps one provider instance, so cooldowns last across searches.
        self._cooldowns = {}
        # Guards the cooldown writes, which the search threads and the download call both make.
        self._cooldown_lock = threading.Lock()
        # Credential key to the bearer token the peer minted for it. Re-minted on a 401.
        self._tokens = {}
        # Credential key to (the monotonic time the cache ends, the provider names the peer listed).
        self._libraries = {}

    def search(self, video, languages, config):
        endpoints = _configured_endpoints(config)
        timeout = _timeout_seconds(config)
        options = _options(config)
        video = dict(video or {})
        pairs = _requested_codes(languages)
        if not pairs or video.get("kind") not in ("movie", "episode"):
            return []
        identity = _identity(video, options)
        if identity is None:
            for endpoint in endpoints:
                LOGGER.info("Bazarr+ Peer endpoint %s skipped: the video has no identifier to search by", endpoint.tag)
            return []
        # Each requested alpha3 code maps to the countries asked for under it. None is a plain request.
        requested = {}
        for alpha3, code in pairs:
            requested.setdefault(alpha3, set()).add(code.partition("-")[2] or None)
        codes = ",".join(code for _alpha3, code in pairs)
        deadline = _monotonic() + timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
            outcomes = list(
                pool.map(
                    lambda endpoint: self._search_contained(
                        endpoint, video, identity, codes, requested, options, deadline
                    ),
                    endpoints,
                )
            )
        if all(outcome.failed for outcome in outcomes):
            raise _all_failed_error([outcome.error for outcome in outcomes])
        return [candidate for outcome in outcomes for candidate in outcome.candidates]

    def download(self, provider_payload, language, config):
        del language  # the stored file id names the file, whatever language it was searched under
        endpoints = _configured_endpoints(config)
        options = _options(config)
        payload = dict(provider_payload or {})
        endpoint = next((item for item in endpoints if item.tag == payload.get("endpoint")), None)
        file_id = _positive_int(payload.get("file_id"))
        if endpoint is None:
            LOGGER.warning(
                "Bazarr+ Peer skipped a download: the endpoint %s is not configured", payload.get("endpoint")
            )
            return _empty_download()
        if file_id is None:
            LOGGER.warning("Bazarr+ Peer endpoint %s skipped a download: the payload carries no file id", endpoint.tag)
            return _empty_download()
        deadline = _monotonic() + DOWNLOAD_TIMEOUT_SECONDS
        try:
            return self._download_content(endpoint, payload, file_id, options, deadline, recovered=False)
        except _Skip as skip:
            return _skip_download(endpoint, file_id, skip.reason)
        except (AuthenticationError, ConfigurationError, APIThrottled) as error:
            # An endpoint-level error skips this candidate and cools the endpoint, as a failed search does.
            # A rate limit already cooled the endpoint in _refuse, with its Retry-After interval.
            if not isinstance(error, APIThrottled):
                self._cool_down(endpoint, error)
            _log_failure(endpoint, error)
            return _empty_download()

    def _search_contained(self, endpoint, video, identity, codes, requested, options, deadline):
        """Run one endpoint's search. An unexpected exception fails only this endpoint."""
        try:
            return self._search_endpoint(endpoint, video, identity, codes, requested, options, deadline)
        except Exception as error:
            # Last resort for an error outside the request. This path cools nothing.
            LOGGER.warning("Bazarr+ Peer endpoint %s failed unexpectedly: %s", endpoint.tag, type(error).__name__)
            return _Outcome([], ServiceUnavailable("the endpoint failed unexpectedly"), True)

    def _search_endpoint(self, endpoint, video, identity, codes, requested, options, deadline):
        """Send the one search of one endpoint and map its rows. A failed search cools the endpoint."""
        cooldown = self._cooldowns.get(_credential_key(endpoint))
        now = _monotonic()
        if cooldown is not None and cooldown[0] > now:
            LOGGER.info("Bazarr+ Peer endpoint %s skipped: cooling down after a failed search", endpoint.tag)
            error = cooldown[1]
            if isinstance(error, APIThrottled):
                error = APIThrottled(
                    "the peer is rate limiting searches", retry_after=math.ceil(cooldown[0] - now)
                )
            return _Outcome([], error, True)
        remaining = _remaining(deadline)
        if remaining <= 0:
            # Nothing is sent and no cooldown is set, because the endpoint was never queried.
            LOGGER.warning("Bazarr+ Peer endpoint %s: budget_spent, no request was sent", endpoint.tag)
            error = ServiceUnavailable("the search budget was spent before this endpoint was queried")
            return _Outcome([], error, True)
        params = _search_params(identity, options, codes, _timeout_param(remaining))
        try:
            rows, total = self._search_once(endpoint, params, deadline)
        except _Refused as refusal:
            error = (
                self._missing_route(endpoint, refusal, deadline)
                if refusal.status == 404
                else self._refuse(endpoint, refusal)
            )
            return self._failed_search(endpoint, error)
        except (ServiceUnavailable, ConfigurationError) as failure:
            return self._failed_search(endpoint, failure)
        if total is not None and total > len(rows):
            LOGGER.info("Bazarr+ Peer endpoint %s: more_results_not_fetched", endpoint.tag)
        if not rows and options.library_only:
            try:
                served = self._library_served(endpoint, deadline)
            except (AuthenticationError, ConfigurationError) as error:
                # The search answered, so the endpoint does not cool down for a refused probe.
                _log_failure(endpoint, error)
                return _Outcome([], error, True)
            if served is False:
                LOGGER.warning(
                    "Bazarr+ Peer endpoint %s: library_not_served, the peer does not serve its library to this key",
                    endpoint.tag,
                )
        candidates = []
        seen = set()
        for row in rows:
            candidate = _candidate(video, endpoint, row, requested, options, identity)
            if candidate is not None and candidate["id"] not in seen:
                seen.add(candidate["id"])
                candidates.append(candidate)
        return _Outcome(candidates, None, False)

    def _failed_search(self, endpoint, error):
        """Log one failed search and return its outcome. A rate limit already cooled the endpoint in _refuse."""
        if not isinstance(error, APIThrottled):
            self._cool_down(endpoint, error)
        _log_failure(endpoint, error)
        return _Outcome([], error, True)

    def _search_once(self, endpoint, params, deadline):
        """One search request. Returns (rows, total_count) or raises the refusal as _Refused."""
        body, _ = _fetch(
            endpoint, "/api/v1/subtitles", params, _remaining(deadline), MAX_SEARCH_BYTES, deadline
        )
        return _parse_search(body)

    def _provider_names(self, endpoint, deadline):
        body, _ = _fetch(
            endpoint, "/api/v1/providers", None, _remaining(deadline), MAX_SEARCH_BYTES, deadline
        )
        return _parse_providers(body)

    def _library_served(self, endpoint, deadline):
        """Whether the peer lists a local provider for this key: True or False, or None when the answer is unknown.

        The probe is not metered, and its answer is kept for PROBE_CACHE_SECONDS. A transient failure logs
        probe_failed and returns None, so the empty search result stands and the endpoint does not cool down.
        """
        key = _credential_key(endpoint)
        now = _monotonic()
        cached = self._libraries.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        if _remaining(deadline) <= 0:
            LOGGER.info("Bazarr+ Peer endpoint %s: the library probe was skipped, no budget is left", endpoint.tag)
            return None
        try:
            names = self._provider_names(endpoint, deadline)
        except _Refused as refusal:
            if refusal.status in (401, 403) or _is_compat_disabled(refusal):
                raise self._refuse(endpoint, refusal) from None
            failure = ServiceUnavailable(f"the library probe was refused (HTTP {refusal.status})")
        except ServiceUnavailable as error:
            failure = error
        else:
            served = "local" in names
            self._libraries[key] = (now + PROBE_CACHE_SECONDS, served)
            return served
        LOGGER.warning("Bazarr+ Peer endpoint %s: probe_failed: %s: %s", endpoint.tag, type(failure).__name__, failure)
        return None

    def _missing_route(self, endpoint, refusal, deadline):
        """Classify a 404 from the subtitles route. The providers route tells a version gap from a wrong address."""
        if _is_compat_disabled(refusal):
            return ConfigurationError(DISTRIBUTION_OFF)
        if _remaining(deadline) <= 0:
            return ServiceUnavailable(TRANSIENT_MESSAGE)
        try:
            self._provider_names(endpoint, deadline)
        except _Refused as probe:
            if _is_compat_disabled(probe):
                return ConfigurationError(DISTRIBUTION_OFF)
            if probe.status == 404:
                return ConfigurationError(
                    "the address does not answer as a Bazarr+ instance (not_bazarr_plus); check the address"
                )
            if probe.status in (401, 403):
                return AuthenticationError(f"the peer rejected the API key (HTTP {probe.status})")
            return ServiceUnavailable(TRANSIENT_MESSAGE)
        except ServiceUnavailable:
            return ServiceUnavailable(TRANSIENT_MESSAGE)
        except ConfigurationError as error:
            return error
        return ConfigurationError(
            "the subtitles route is missing on this instance (version_or_path); "
            "check the address and the Bazarr+ version"
        )

    def _cool_down(self, endpoint, error, seconds=DEFAULT_COOLDOWN_SECONDS):
        """Skip the endpoint in later searches for the given seconds, and keep the error that cooled it.

        A cooldown already in force that ends at or after the new one is kept, so a shorter cooldown
        never shortens it. It returns the expiry in force, on the monotonic clock.
        """
        key = _credential_key(endpoint)
        until = _monotonic() + seconds
        with self._cooldown_lock:
            current = self._cooldowns.get(key)
            if current is not None and current[0] >= until:
                return current[0]
            # The copy carries no traceback, so the stored error keeps no frames or response alive.
            self._cooldowns[key] = (until, type(error)(str(error)))
            return until

    def _refuse(self, endpoint, refusal):
        """The exception one HTTP refusal raises as. A 429 also cools the endpoint down."""
        status = refusal.status
        if _is_compat_disabled(refusal):
            return ConfigurationError(DISTRIBUTION_OFF)
        if status == 429:
            interval = _retry_after_seconds(refusal.retry_after) or DEFAULT_COOLDOWN_SECONDS
            error = APIThrottled("the peer is rate limiting searches (HTTP 429)", retry_after=interval)
            ends_at = self._cool_down(endpoint, error, interval)
            error.retry_after = math.ceil(ends_at - _monotonic())
            return error
        if status in (401, 403):
            return AuthenticationError(f"the peer rejected the API key (HTTP {status})")
        if status == 400:
            return ConfigurationError("the peer rejected the request as invalid (bad_request)")
        if status == 404:
            return ConfigurationError(f"the peer does not answer on this route (HTTP {status})")
        if status == 503:
            return ServiceUnavailable(TRANSIENT_MESSAGE)
        if 300 <= status < 400:
            return ServiceUnavailable(f"redirects are refused (HTTP {status})")
        return ServiceUnavailable(f"the peer failed to answer (HTTP {status})")

    def _token(self, endpoint, deadline, refresh=False):
        """The bearer token for an endpoint's key, logging in when none is cached or a refresh is asked for."""
        key = _credential_key(endpoint)
        if not refresh and key in self._tokens:
            return self._tokens[key]
        try:
            body, _ = _fetch(
                endpoint, "/api/v1/login", None, _remaining(deadline), MAX_SEARCH_BYTES, deadline, method="POST"
            )
        except _Refused as refusal:
            raise self._refuse(endpoint, refusal) from None
        token = _parse_token(body)
        self._tokens[key] = token
        return token

    def _post_download(self, endpoint, file_id, deadline):
        """The stream link for one file id. A 401 re-mints the token once. Other refusals are raised as _Refused."""
        refreshed = False
        while True:
            token = self._token(endpoint, deadline, refresh=refreshed)
            try:
                body, _ = _fetch(
                    endpoint,
                    "/api/v1/download",
                    None,
                    _remaining(deadline),
                    MAX_SEARCH_BYTES,
                    deadline,
                    method="POST",
                    token=token,
                    payload={"file_id": file_id, "sub_format": SUBTITLE_FORMAT},
                )
            except _Refused as refusal:
                if refusal.status == 401 and not refreshed:
                    refreshed = True
                    continue
                raise
            return _parse_link(body)

    def _recover(self, endpoint, payload, options, deadline):
        """The current file id of a download whose file id went stale, found by one search with its saved context.

        The search draws on the download call's budget. It returns the file id of the row with the saved
        key, and raises _Skip with the reason for every other outcome.
        """
        saved = _saved_search(payload)
        if saved is None:
            raise _Skip("stale_no_context")
        identity, code = saved
        remaining = _remaining(deadline)
        if remaining <= 0:
            raise _Skip("budget_spent")
        params = _search_params(identity, options, code, _timeout_param(remaining))
        try:
            rows, _total = self._search_once(endpoint, params, deadline)
        except _Refused as refusal:
            if refusal.status == 429:
                # The rate limit cools the endpoint, as it does in a search.
                self._refuse(endpoint, refusal)
                raise _Skip("recovery_throttled") from None
            if refusal.status in (401, 403):
                raise _Skip("recovery_rejected") from None
            if refusal.status == 400:
                raise _Skip("bad_request") from None
            if _is_compat_disabled(refusal):
                raise _Skip("compat_disabled") from None
            raise _Skip("recovery_failed") from None
        except ServiceUnavailable:
            raise _Skip("recovery_failed") from None
        for row in rows:
            ident = _row_identity(row, endpoint)
            if ident is not None and ident["key"] == payload["key"]:
                return ident["file_id"]
        raise _Skip("stale_not_found")

    def _download_content(self, endpoint, payload, file_id, options, deadline, recovered):
        """The download of one file id: the stream link, then the file. Raises _Skip for a candidate-local refusal."""
        try:
            link = self._post_download(endpoint, file_id, deadline)
        except _Refused as refusal:
            if refusal.status == 406 and refusal.reason == "throttled":
                raise _Skip("the download quota is exhausted") from None
            if refusal.status == 404 and not _is_compat_disabled(refusal):
                # A stale file id gets one recovery search and one more download. A second 404 is final.
                if recovered:
                    raise _Skip("not_found") from None
                fresh = self._recover(endpoint, payload, options, deadline)
                return self._download_content(endpoint, payload, fresh, options, deadline, recovered=True)
            error = self._refuse(endpoint, refusal)
            # A transient refusal skips this download only. Rate limits and key or request errors stay endpoint-level.
            if isinstance(error, ServiceUnavailable):
                raise _Skip(str(error)) from None
            raise error from None
        except ServiceUnavailable as error:
            raise _Skip(str(error)) from None
        if not _link_belongs_to(link, endpoint):
            raise _Skip("link_origin_mismatch")
        try:
            body, content_type = _fetch_stream(link, DOWNLOAD_TIMEOUT_SECONDS, _monotonic() + DOWNLOAD_TIMEOUT_SECONDS)
        except _Refused as refusal:
            raise _Skip(_STREAM_REASONS.get(refusal.status, f"HTTP {refusal.status}")) from None
        except ServiceUnavailable as error:
            raise _Skip(str(error)) from None
        if not body:
            raise _Skip("content_missing")
        if _is_rar(body):
            raise _Skip("the peer sent a RAR archive")
        if _is_archive(body):
            # The host lists the archive and picks the member for this episode.
            return {
                "archive_b64": base64.b64encode(body).decode("ascii"),
                "archive_sha256": hashlib.sha256(body).hexdigest(),
                "episode": _as_int(payload.get("episode")),
            }
        if _is_json(body, content_type):
            raise _Skip("the peer answered with JSON")
        if _looks_like_html(body):
            raise _Skip("the peer returned a web page")
        # No encoding is declared: the host detects it when it normalizes the text.
        return {
            "content_b64": base64.b64encode(body).decode("ascii"),
            "content_sha256": hashlib.sha256(body).hexdigest(),
            "content_type": _content_type(),
            "format": SUBTITLE_FORMAT,
            "empty": False,
        }
