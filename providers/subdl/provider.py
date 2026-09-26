"""SubDL provider for the Bazarr+ Provider Hub catalog."""

import base64
import errno
import hashlib
import io
import json
import logging
import math
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


PROVIDER_ID = "subdl"
API_URL = "https://api.subdl.com/api/v1/subtitles"
ACCOUNT_API_URL = "https://api.subdl.com/api/v1/me"
DOWNLOAD_BASE_URL = "https://dl.subdl.com"
# The only origin the configured API key may be sent to on a download.
SIGNED_DOWNLOAD_HOSTS = frozenset({"dl.subdl.com"})
# Every download, signed or not, stays on SubDL's own HTTPS hosts, so a tampered
# row URL cannot make the worker fetch loopback, private-network or foreign hosts.
DOWNLOAD_HOST_DOMAIN = "subdl.com"
TRANSLATION_API_BASE_URL = "https://api.subdl.com/api/v1/pro/translate"
TRANSLATION_POLL_INTERVAL_SECONDS = 4
TRANSLATION_POLL_FAILURE_LIMIT = 5
TRANSLATION_JOB_TTL_SECONDS = 24 * 60 * 60
TRANSLATION_STATE_MAX_ITEMS = 256
TRANSLATION_FILE_MAX_BYTES = 16 * 1024 * 1024
ACCOUNT_STATUS_TTL_SECONDS = 15 * 60
ACCOUNT_STATUS_TIMEOUT_SECONDS = 10
ACCOUNT_STATUS_MAX_BYTES = 64 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 30
SUBS_PER_PAGE = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
ARCHIVE_EXTENSIONS = (".zip",)

# Transport-level retry for transient network failures. Upstream subliminal wraps its
# session in a RetryingSession/ProviderRetryMixin with ~3 tries and backoff; mirror that
# here so a single connection blip or 5xx/429 does not abort a whole search or download.
HTTP_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0
_MAX_ERROR_BODY_BYTES = 64 * 1024


class DownloadLimitExceeded(RuntimeError):
    pass


class TooManyRequests(RuntimeError):
    pass


class ServiceUnavailable(RuntimeError):
    pass


def _http_error_token(exc):
    try:
        body = exc.read(_MAX_ERROR_BODY_BYTES + 1)
        if not isinstance(body, bytes) or len(body) > _MAX_ERROR_BODY_BYTES:
            return None
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    token = payload.get("error")
    return token if isinstance(token, str) else None


def _raise_semantic_http_error(exc):
    if exc.code != 429 and not 500 <= exc.code < 600:
        return
    try:
        if 500 <= exc.code < 600:
            raise ServiceUnavailable(f"SubDL service unavailable: HTTP {exc.code}") from exc
        token = _http_error_token(exc)
        if token in ("daily_limit", "api_download_limit_exceeded"):
            raise DownloadLimitExceeded("SubDL download quota exceeded") from exc
        if token == "service_busy":
            raise ServiceUnavailable("SubDL service is busy") from exc
        raise TooManyRequests("SubDL rate limit exceeded") from exc
    finally:
        exc.close()


def _is_transient_http_error(exc):
    # Only 5xx and 429 are worth retrying; every other 4xx is a permanent client error
    # (bad request, auth, not found) that must propagate on the first occurrence.
    return exc.code == 429 or 500 <= exc.code < 600


def _retry_after_seconds(exc):
    # Honor a Retry-After header on 429 when it carries a plain integer delay.
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        delay = int(str(header).strip())
    except (TypeError, ValueError):
        return None
    if delay < 0:
        return None
    return min(float(delay), RETRY_BACKOFF_CAP_SECONDS)


def _backoff_seconds(attempt):
    delay = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
    return min(delay, RETRY_BACKOFF_CAP_SECONDS)


def _urlopen_with_retry(request, timeout, opener=None):
    # Wrap only the raw urllib call in a bounded retry loop. Transient failures
    # (connection reset/refused/DNS via URLError, socket timeouts, and 5xx/429) are
    # retried up to HTTP_MAX_ATTEMPTS times with exponential backoff. Any other error,
    # including 4xx HTTPError other than 429, propagates unchanged to the caller's existing
    # error handling. The successful response is read and returned as bytes so the caller
    # keeps its existing return type and post-processing.
    last_exc = None
    open_url = urllib.request.urlopen if opener is None else opener.open
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            with open_url(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if attempt >= HTTP_MAX_ATTEMPTS or not _is_transient_http_error(exc):
                raise
            last_exc = exc
            delay = _retry_after_seconds(exc) if exc.code == 429 else None
            if delay is None:
                delay = _backoff_seconds(attempt)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt >= HTTP_MAX_ATTEMPTS:
                raise
            last_exc = exc
            delay = _backoff_seconds(attempt)
        if delay:
            time.sleep(delay)
    # Defensive: the loop always returns or raises above, but keep a clear failure path.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("SubDL request failed without a response")


_SUBDL_TO_LANGUAGE = {
    "AR": ("ara", None, None),
    "DA": ("dan", None, None),
    "NL": ("nld", None, None),
    "EN": ("eng", None, None),
    "FA": ("fas", None, None),
    "FI": ("fin", None, None),
    "FR": ("fra", None, None),
    "ID": ("ind", None, None),
    "IT": ("ita", None, None),
    "NO": ("nor", None, None),
    "RO": ("ron", None, None),
    "ES": ("spa", None, None),
    "SV": ("swe", None, None),
    "VI": ("vie", None, None),
    "SQ": ("sqi", None, None),
    "AZ": ("aze", None, None),
    "BE": ("bel", None, None),
    "BN": ("ben", None, None),
    "BS": ("bos", None, None),
    "BG": ("bul", None, None),
    "MY": ("mya", None, None),
    "CA": ("cat", None, None),
    "ZH": ("zho", None, None),
    "HR": ("hrv", None, None),
    "CS": ("ces", None, None),
    "EO": ("epo", None, None),
    "ET": ("est", None, None),
    "KA": ("kat", None, None),
    "DE": ("deu", None, None),
    "EL": ("ell", None, None),
    "KL": ("kal", None, None),
    "HE": ("heb", None, None),
    "HI": ("hin", None, None),
    "HU": ("hun", None, None),
    "IS": ("isl", None, None),
    "JA": ("jpn", None, None),
    "KO": ("kor", None, None),
    "KU": ("kur", None, None),
    "LV": ("lav", None, None),
    "LT": ("lit", None, None),
    "MK": ("mkd", None, None),
    "MS": ("msa", None, None),
    "ML": ("mal", None, None),
    "PL": ("pol", None, None),
    "PT": ("por", None, None),
    "RU": ("rus", None, None),
    "SR": ("srp", None, None),
    "SI": ("sin", None, None),
    "SK": ("slk", None, None),
    "SL": ("slv", None, None),
    "TL": ("tgl", None, None),
    "TA": ("tam", None, None),
    "TE": ("tel", None, None),
    "TH": ("tha", None, None),
    "TR": ("tur", None, None),
    "UK": ("ukr", None, None),
    "UR": ("urd", None, None),
    "HY": ("hye", None, None),
    "KK": ("kaz", None, None),
    "KY": ("kir", None, None),
    "KM": ("khm", None, None),
    "KN": ("kan", None, None),
    "MN": ("mon", None, None),
    "EU": ("eus", None, None),
    "GL": ("glg", None, None),
    "GA": ("gle", None, None),
    "JV": ("jav", None, None),
    "SU": ("sun", None, None),
    "BR_PT": ("por", "BR", None),
    "ZH_BG": ("zho", "TW", None),
}
_LANGUAGE_TO_SUBDL = {value: key for key, value in _SUBDL_TO_LANGUAGE.items()}
SUPPORTED_ALPHA3 = sorted({value[0] for value in _SUBDL_TO_LANGUAGE.values()})

_SEASON_EPISODE_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,4})\b", re.I)
_EPISODE_RE = re.compile(r"\b(?:EP?|Episode)[ ._-]?(?P<episode>\d{1,4})\b", re.I)
_RANGE_RE = re.compile(r"\b(?:EP?|Episode)[ ._-]?(?P<start>\d{1,4})[ ._-]*-[ ._-]*(?P<end>\d{1,4})\b", re.I)
_WS_RE = re.compile(r"\s+")


def _coerce_int(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _clean_text(value):
    return _WS_RE.sub(" ", _coerce_text(value)).strip()


def _language_dict(language):
    if isinstance(language, dict):
        payload = dict(language)
    else:
        payload = {"alpha3": str(language)}
    payload.setdefault("alpha3", payload.get("alpha2") or "")
    payload.setdefault("hi", False)
    payload.setdefault("forced", False)
    return payload


def _subdl_code(language):
    payload = _language_dict(language)
    alpha3 = payload.get("alpha3")
    country = payload.get("country_alpha2") or payload.get("country")
    script = payload.get("script")
    if alpha3 == "zho" and (
        str(country or "").upper() == "TW"
        or str(script or "").lower() in {"hant", "traditional"}
    ):
        return "ZH_BG"
    candidates = [
        (alpha3, country, script),
        (alpha3, country, None),
        (alpha3, None, script),
        (alpha3, None, None),
    ]
    for candidate in candidates:
        code = _LANGUAGE_TO_SUBDL.get(candidate)
        if code:
            return code
    return None


def language_codes(languages):
    codes = {_subdl_code(language) for language in languages or []}
    return sorted(code for code in codes if code)


def _language_for_code(code, hi=False, forced=False):
    mapping = _SUBDL_TO_LANGUAGE.get(_coerce_text(code).upper())
    if not mapping:
        return None
    alpha3, country, script = mapping
    language = {"alpha3": alpha3, "hi": bool(hi), "forced": bool(forced)}
    if country:
        language["country_alpha2"] = country
    if script:
        language["script"] = script
    return language


def _language_matches(requested_languages, alpha3, hi=False, forced=False):
    for requested in requested_languages or []:
        payload = _language_dict(requested)
        if payload.get("alpha3") != alpha3:
            continue
        if bool(payload.get("hi", False)) != bool(hi):
            continue
        if bool(payload.get("forced", False)) != bool(forced):
            continue
        return True
    return False


def _base_params(video, languages, api_key):
    video = video or {}
    kind = video.get("kind")
    codes = language_codes(languages)
    if not codes:
        return None
    params = {
        "api_key": api_key,
        "languages": ",".join(codes),
        "subs_per_page": SUBS_PER_PAGE,
        "comment": 1,
        "releases": 1,
        "bazarr": 1,
        "client": "bazarr",
        "unpack": 1,
    }
    if kind == "episode":
        title = _clean_text(video.get("series"))
        imdb_id = _clean_text(video.get("series_imdb_id"))
        params["type"] = "tv"
        if imdb_id:
            params["imdb_id"] = imdb_id
        elif title:
            params["film_name"] = title
    elif kind == "movie":
        title = _clean_text(video.get("title"))
        imdb_id = _clean_text(video.get("imdb_id"))
        params["type"] = "movie"
        if imdb_id:
            params["imdb_id"] = imdb_id
        elif title:
            params["film_name"] = title
    else:
        return None
    return params


def build_search_requests(video, languages, api_key, anime_mode=False):
    video = video or {}
    params = _base_params(video, languages, api_key)
    if not params:
        return []
    kind = video.get("kind")
    if kind == "movie":
        return [("primary", params)]

    season = _coerce_int(video.get("season"))
    episode = _coerce_int(video.get("episode"))
    if season is None or episode is None:
        return []
    primary = dict(params)
    primary["season_number"] = season
    primary["episode_number"] = episode
    requests = [("primary", primary)]

    if anime_mode:
        absolute_episode = _coerce_int(video.get("absolute_episode"))
        if absolute_episode and absolute_episode != episode:
            absolute = dict(params)
            absolute["episode_number"] = absolute_episode
            requests.append(("absolute", absolute))
        season_only = dict(params)
        season_only["season_number"] = season
        requests.append(("season", season_only))
    return requests


def _title_only_request(video, languages, api_key):
    params = _base_params(video, languages, api_key)
    if not params:
        return None
    return params


def _is_empty_response(data):
    if not isinstance(data, dict):
        return False
    if data.get("status") is False or data.get("success") is False:
        error = _coerce_text(data.get("error")).lower()
        if not error or "can't find" in error or "cant find" in error:
            return True
        raise RuntimeError(data.get("error") or "SubDL API returned an error")
    return False


def _response_items(data):
    if _is_empty_response(data):
        return []
    if not isinstance(data, dict):
        return []
    return [item for item in data.get("subtitles", []) if isinstance(item, dict)]


def _apply_runtime_policy(policy, data):
    """Apply only the bounded search controls returned by SubDL's API."""
    if not isinstance(data, dict):
        return
    api_policy = data.get("bazarr_policy")
    if not isinstance(api_policy, dict):
        return
    for key in ("enabled", "season_fallback_enabled", "title_fallback_enabled", "unpack_enabled", "ai_translation_enabled"):
        value = api_policy.get(key)
        if isinstance(value, bool):
            policy[key] = value
    max_pages = api_policy.get("max_pages")
    if isinstance(max_pages, int) and not isinstance(max_pages, bool):
        policy["max_pages"] = max(1, min(max_pages, 2))


def _advertises_ai_translation(data):
    if not isinstance(data, dict):
        return False
    policy = data.get("bazarr_policy")
    return (
        isinstance(policy, dict)
        and policy.get("ai_translation_enabled") is True
    )


def _only_hidden_ai_rows(item):
    if _is_ai_translated(item):
        return True
    children = [child for child in item.get("unpack_files") or [] if isinstance(child, dict)]
    return bool(children) and all(_is_ai_translated(None, child) for child in children)


def _merge_items(target, seen, data, include_ai_translated=True):
    for item in _response_items(data):
        # A hidden AI row is dropped before deduplication, so it can never take
        # the name of a visible human row and remove it from the results.
        if not include_ai_translated and _only_hidden_ai_rows(item):
            continue
        item_id = _clean_text(item.get("name")) or _clean_text(item.get("url"))
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        target.append(item)


def _movie_tmdb_fallback_params(video, primary_params):
    tmdb_id = video.get("tmdb_id")
    if not tmdb_id:
        return None
    params = dict(primary_params)
    params.pop("film_name", None)
    params.pop("imdb_id", None)
    params["tmdb_id"] = tmdb_id
    return params


def _item_language_code(item, child=None):
    if child and child.get("language"):
        return _coerce_text(child.get("language")).upper()
    return _coerce_text(item.get("language")).upper()


def _joined_metadata(item, child=None):
    parts = [
        item.get("comment"),
        item.get("name"),
        item.get("release_name"),
    ]
    if child:
        parts.extend([child.get("name"), child.get("release_name")])
    releases = item.get("releases") or []
    if isinstance(releases, list):
        parts.extend(releases)
    return " ".join(_clean_text(part).lower() for part in parts if part)


def is_hearing_impaired(item, child=None):
    if child and child.get("hi") is not None:
        return bool(child.get("hi"))
    if item.get("hi"):
        return True
    metadata = _joined_metadata(item, child)
    non_hi_tags = (
        "hi remove",
        "non hi",
        "nonhi",
        "non-hi",
        "non-sdh",
        "non sdh",
        "nonsdh",
        "sdh remove",
    )
    if any(tag in metadata for tag in non_hi_tags):
        return False
    hi_tags = ("_hi_", " hi ", ".hi.", "sdh", "𝓢𝓓𝓗")
    return any(tag in metadata for tag in hi_tags)


def is_forced(item):
    metadata = _joined_metadata(item)
    return "forced" in metadata or "foreign" in metadata


def _episode_range_from_releases(release_names):
    for name in release_names or []:
        match = _RANGE_RE.search(_coerce_text(name))
        if match:
            return int(match.group("start")), int(match.group("end"))
    return None, None


def _episode_range(item):
    start = _coerce_int(item.get("episode_from"))
    end = _coerce_int(item.get("episode_end"))
    if start is not None and end is not None:
        return start, end
    releases = item.get("releases") if isinstance(item.get("releases"), list) else []
    return _episode_range_from_releases(releases)


def _is_pack(item):
    start, end = _episode_range(item)
    if start is not None and end is not None and start != end:
        return True
    if item.get("full_season"):
        return True
    unpack_files = item.get("unpack_files")
    return isinstance(unpack_files, list) and bool(unpack_files)


def _pack_contains_episode(item, video):
    unpack_files = item.get("unpack_files")
    if isinstance(unpack_files, list) and unpack_files:
        # When the API provides archive members, those entries are the most
        # precise episode identity available. Do not fall back to the pack's
        # season flag when none of its listed files matches the request.
        return bool(_children_for_item(item, video))

    target_season = _coerce_int(video.get("season"))
    item_season = _coerce_int(item.get("season"))
    season_matches = target_season is None or item_season is None or item_season == target_season
    start, end = _episode_range(item)
    if start is None or end is None:
        # A full-season flag without member listings or an episode range is
        # usable only when both sides identify the same season.
        return target_season is not None and item_season == target_season
    absolute_episode = _coerce_int(video.get("absolute_episode"))
    if absolute_episode is not None and start <= absolute_episode <= end:
        return True
    episode = _coerce_int(video.get("episode"))
    return season_matches and episode is not None and start <= episode <= end


def _child_matches_video(child, video, parent_season=None):
    if not child:
        return False
    child_episode = _coerce_int(child.get("episode"))
    if child_episode is None:
        return False

    target_episode = _coerce_int(video.get("episode"))
    if target_episode is not None and child_episode == target_episode:
        target_season = _coerce_int(video.get("season"))
        child_season = _coerce_int(child.get("season"))
        effective_season = child_season if child_season is not None else parent_season
        return target_season is None or effective_season is None or effective_season == target_season

    absolute_episode = _coerce_int(video.get("absolute_episode"))
    if (
        absolute_episode is not None
        and absolute_episode != target_episode
        and child_episode == absolute_episode
    ):
        # Anime absolute numbering can map a requested season/episode to a
        # different season number in the provider's catalogue.
        return True
    return False


def _children_for_item(item, video):
    children = item.get("unpack_files")
    if not isinstance(children, list):
        return []
    parent_season = _coerce_int(item.get("season"))
    return [
        child
        for child in children
        if isinstance(child, dict)
        and _child_matches_video(child, video, parent_season=parent_season)
    ]


def _release_names(item, child=None):
    names = []
    if child and child.get("release_name"):
        names.append(_clean_text(child.get("release_name")))
    if item.get("release_name"):
        names.append(_clean_text(item.get("release_name")))
    for release in item.get("releases") or []:
        text = _clean_text(release)
        if text and text not in names:
            names.append(text)
    if not names and item.get("name"):
        names.append(_clean_text(item.get("name")))
    return names


def _release_info(item, child=None):
    return ", ".join(_release_names(item, child))


def _format_from_name(*names):
    known = {ext[1:] for ext in SUBTITLE_EXTENSIONS + ARCHIVE_EXTENSIONS}
    for name in names:
        value = _coerce_text(name).lower()
        if value in known:
            return value
        path = urllib.parse.urlparse(_coerce_text(name)).path.lower()
        for ext in SUBTITLE_EXTENSIONS + ARCHIVE_EXTENSIONS:
            if path.endswith(ext):
                return ext[1:]
    return "srt"


def _matches_for_item(video, item, child, is_pack):
    video = video or {}
    kind = video.get("kind")
    matches = set()
    if kind == "episode":
        matches.add("series")
        video_season = _coerce_int(video.get("season"))
        video_episode = _coerce_int(video.get("episode"))
        absolute_episode = _coerce_int(video.get("absolute_episode"))
        item_season = _coerce_int((child or {}).get("season")) or _coerce_int(item.get("season"))
        item_episode = _coerce_int((child or {}).get("episode")) or _coerce_int(item.get("episode"))
        if video_season is not None and item_season == video_season:
            matches.add("season")
        elif is_pack and absolute_episode:
            matches.add("season")
        expected_episodes = {
            value for value in (video_episode, absolute_episode) if value is not None
        }
        if item_episode is not None and item_episode in expected_episodes:
            matches.add("episode")
        elif is_pack:
            matches.add("episode")
        if video.get("series_imdb_id"):
            matches.add("series_imdb_id")
        if video.get("year"):
            matches.add("year")
    elif kind == "movie":
        matches.add("title")
        if video.get("imdb_id"):
            matches.add("imdb_id")
        if video.get("tmdb_id"):
            matches.add("tmdb_id")
    return sorted(matches)


def _account_digest(api_key):
    return hashlib.sha256(_coerce_text(api_key).encode("utf-8")).hexdigest()


def _without_api_key(url):
    """Return a row URL without any api_key query parameter, and whether one was there.

    SubDL can sign the download URLs it returns with the caller's key. The key must
    never travel in a candidate, a payload or an error message, so it is removed
    here and added back from the configuration at download time.
    """
    value = _clean_text(url)
    if "api_key" not in value.casefold():
        return value, False
    parts = urllib.parse.urlsplit(value)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [(name, item) for name, item in query if name.casefold() != "api_key"]
    if len(kept) == len(query):
        return value, False
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(kept))), True


def _download_request_url(url):
    """Return the HTTPS URL to fetch for a SubDL download, or None if it is not SubDL's.

    A plain http link on a SubDL host is upgraded, because dl.subdl.com only
    redirects it to the same path over HTTPS.
    """
    value = _coerce_text(url)
    if not value or "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").casefold()
    if (
        parts.scheme not in ("http", "https")
        or parts.username is not None
        or parts.password is not None
        or "@" in parts.netloc
        or not (host == DOWNLOAD_HOST_DOMAIN or host.endswith("." + DOWNLOAD_HOST_DOMAIN))
        or port not in (None, 443 if parts.scheme == "https" else 80)
    ):
        return None
    return urllib.parse.urlunsplit(parts._replace(scheme="https", netloc=host))


def _is_https_download_url(url):
    return _download_request_url(url) is not None and urllib.parse.urlsplit(url).scheme == "https"


class _DownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None makes urllib raise the redirect as an HTTPError.
        if not _is_https_download_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_opener():
    return urllib.request.build_opener(_DownloadRedirectHandler())


def _is_signed_download_origin(url):
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").casefold() in SIGNED_DOWNLOAD_HOSTS
        and port in (None, 443)
    )


def _with_api_key(url, api_key):
    parts = urllib.parse.urlsplit(url)
    query = [
        (name, item)
        for name, item in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if name.casefold() != "api_key"
    ]
    query.append(("api_key", api_key))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _result_id(item, child=None):
    if child:
        value = _clean_text(child.get("file_n_id")) or _clean_text(child.get("name")) or _clean_text(child.get("url"))
    else:
        value = _clean_text(item.get("name")) or _clean_text(item.get("url"))
    return _without_api_key(value)[0]


def _result_filename(item, child=None):
    if child and child.get("name"):
        return _clean_text(child.get("name"))
    return _clean_text(item.get("name")) or "subdl-subtitle"


def _payload_for_item(video, item, child, is_pack):
    download_url, download_signed = _without_api_key(
        _clean_text((child or {}).get("url")) or _clean_text(item.get("url"))
    )
    archive_download_url = _without_api_key(item.get("url"))[0] if child else ""
    subtitle_id = _result_id(item, child)
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "subtitle_id": subtitle_id,
        "page_link": urllib.parse.urljoin("https://subdl.com", _without_api_key(item.get("subtitlePage"))[0]),
        "download_url": download_url,
        "release_info": _release_info(item, child),
        "format": _format_from_name(download_url, _result_filename(item, child)),
        "kind": (video or {}).get("kind"),
        "season": _coerce_int((video or {}).get("season")),
        "episode": _coerce_int((video or {}).get("episode")),
        "absolute_episode": _coerce_int((video or {}).get("absolute_episode")),
        "is_pack": bool(is_pack),
    }
    if archive_download_url:
        payload["archive_download_url"] = archive_download_url
    if download_signed:
        payload["download_url_signed"] = True
    return payload


def _candidate_from_item(video, requested_languages, item, child=None):
    code = _item_language_code(item, child)
    forced = is_forced(item)
    hi = is_hearing_impaired(item, child)
    language = _language_for_code(code, hi=hi, forced=forced)
    if not language:
        return None
    if not _language_matches(requested_languages, language["alpha3"], hi=hi, forced=forced):
        return None

    is_pack = _is_pack(item)
    matches = _matches_for_item(video, item, child, is_pack)
    payload = _payload_for_item(video, item, child, is_pack)
    release_info = _release_info(item, child)
    score = min(100, 40 + len(matches) * 12)
    return {
        "provider": PROVIDER_ID,
        "id": payload["subtitle_id"],
        "language": language,
        "release_info": release_info,
        "filename": _result_filename(item, child),
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": hi,
        "display": {
            "source": "subdl-api",
            "uploader": _clean_text(item.get("author")),
            "page_link": payload.get("page_link"),
        },
        "provider_payload": payload,
    }


def _is_ai_translated(item, child=None):
    return bool(
        isinstance(item, dict) and item.get("ai_translated") is True
        or isinstance(child, dict) and child.get("ai_translated") is True
    )


def _quota_count(value):
    if type(value) is int and 0 <= value <= 10_000_000:
        return value
    return None


def _account_translation_status(data, api_key=""):
    if not isinstance(data, dict):
        return None
    pro = data.get("pro")
    if not isinstance(pro, dict):
        return None
    entitled = pro.get("isTranslationEligible")
    if type(entitled) is not bool:
        return None
    quota = pro.get("translationQuota")
    quota = quota if isinstance(quota, dict) else {}
    remaining = _quota_count(quota.get("remaining"))
    limit = _quota_count(quota.get("limit"))
    reset_at = quota.get("periodEnd")
    if not isinstance(reset_at, str) or (api_key and api_key in reset_at):
        reset_at = None
    else:
        reset_at = _clean_text(reset_at)[:64] or None
    exhausted = remaining == 0 if remaining is not None else None
    return {
        "entitled": entitled,
        "exhausted": exhausted,
        "remaining": remaining,
        "limit": limit,
        "reset_at": reset_at,
    }


def _source_release_names(source):
    releases = source.get("releases")
    if not isinstance(releases, list) or any(not isinstance(name, str) for name in releases):
        releases = []
    return list(dict.fromkeys(_clean_text(name) for name in releases if _clean_text(name)))


def _release_overlap_count(video, release_names):
    release_text = " ".join(release_names).casefold()
    if not release_text:
        return 0
    fields = ("release_group", "source", "resolution", "streaming_service")
    values = dict.fromkeys(_clean_text((video or {}).get(key)).casefold() for key in fields)
    return sum(1 for value in values if value and value in release_text)


def _same_language_variant(left, right):
    left = _language_dict(left)
    right = _language_dict(right)
    left_country = left.get("country_alpha2") or left.get("country")
    right_country = right.get("country_alpha2") or right.get("country")
    return (
        _clean_text(left.get("alpha3")).casefold() == _clean_text(right.get("alpha3")).casefold()
        and _clean_text(left_country).casefold() == _clean_text(right_country).casefold()
        and _clean_text(left.get("script")).casefold() == _clean_text(right.get("script")).casefold()
    )


def _row_can_suppress_translation(video, item, child, candidate):
    """Whether a regular row shows that this exact episode already has the language."""
    if (video or {}).get("kind") != "episode":
        return True
    matches = candidate.get("matches", [])
    if "episode" not in matches:
        return False
    row_season = _coerce_int((child or {}).get("season")) or _coerce_int((item or {}).get("season"))
    return row_season is None or "season" in matches


def _valid_translation_block(translation):
    return (
        isinstance(translation, dict)
        and type(translation.get("entitled")) is bool
        and isinstance(translation.get("missing_languages"), list)
        and all(isinstance(code, str) for code in translation["missing_languages"])
        and isinstance(translation.get("sources"), list)
    )


def _valid_translation_source(source):
    if not isinstance(source, dict):
        return False
    n_id = source.get("n_id")
    return (
        (type(n_id) is int or isinstance(n_id, str) and bool(_clean_text(n_id)))
        and isinstance(source.get("language"), str)
        and type(source.get("hi")) is bool
    )


def _translation_candidate(video, wanted_language, target_code, source):
    n_id = source.get("n_id")
    if not _valid_translation_source(source):
        return None
    source_code = _clean_text(source.get("language")).upper()
    if source_code not in _SUBDL_TO_LANGUAGE:
        return None
    release_names = _source_release_names(source)
    release_info = ", ".join(release_names)
    hi = source.get("hi") is True
    language = dict(wanted_language)
    language["hi"] = hi
    language["forced"] = False
    video = video or {}
    matches = _matches_for_item(
        video,
        {"season": video.get("season"), "episode": video.get("episode")},
        None,
        False,
    )
    score = min(100, 40 + len(matches) * 12)
    variant = "hi" if hi else "plain"
    candidate_id = f"ai:{n_id}:{target_code}:{variant}"
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "kind": "ai_translation",
        "n_id": n_id,
        "target_language": target_code,
        "source_language": source_code,
        "season": _coerce_int(video.get("season")),
        "episode": _coerce_int(video.get("episode")),
        "absolute_episode": _coerce_int(video.get("absolute_episode")),
    }
    return {
        "provider": PROVIDER_ID,
        "id": candidate_id,
        "language": language,
        "release_info": release_info,
        "filename": f"ai-translation-{target_code.lower()}.srt",
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": hi,
        "ai_translated": True,
        "display": {
            "source": "subdl-api",
            "uploader": f"SubDL AI translation from {source_code}",
            "page_link": "https://subdl.com/pro?ref=bazarr",
        },
        "provider_payload": payload,
    }


def _build_ai_candidates(video, requested_languages, translation, existing_candidates, blocked=False):
    if not _valid_translation_block(translation) or translation.get("entitled") is not True or blocked:
        return []
    missing = translation.get("missing_languages")
    sources = [
        source for source in translation["sources"]
        if _valid_translation_source(source)
        and _clean_text(source.get("language")).upper() in _SUBDL_TO_LANGUAGE
    ]
    if not missing or not sources or _quota_count(translation.get("remaining")) == 0:
        return []
    missing_codes = {_clean_text(code).upper() for code in missing if _clean_text(code)}
    results = []
    seen_variants = set()
    for requested in requested_languages or []:
        wanted = _language_dict(requested)
        if wanted.get("forced") is True:
            continue
        target_code = _subdl_code(wanted)
        if not target_code or target_code not in missing_codes:
            continue
        hi = wanted.get("hi") is True
        variant = (target_code, hi)
        if variant in seen_variants:
            continue
        seen_variants.add(variant)
        selected = None
        selected_overlap = -1
        for source in sources:
            if not isinstance(source, dict) or (source.get("hi") is True) != hi:
                continue
            overlap = _release_overlap_count(video, _source_release_names(source))
            if overlap > selected_overlap:
                selected = source
                selected_overlap = overlap
        if selected is None:
            continue
        candidate = _translation_candidate(video, wanted, target_code, selected)
        if candidate is None:
            continue
        suppressed = False
        for existing in existing_candidates or []:
            language = existing.get("language")
            if not isinstance(language, dict):
                continue
            language = _language_dict(language)
            if language.get("forced") is True or (language.get("hi") is True) != hi:
                continue
            if not _same_language_variant(language, wanted):
                continue
            suppressed = True
            break
        if not suppressed:
            results.append(candidate)
    return results


def _absolute_download_url(path):
    value = _coerce_text(path)
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return urllib.parse.urljoin(DOWNLOAD_BASE_URL, value)


def _normalize_subtitle_bytes(content):
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _filename_episode_matches(name, payload):
    text = _coerce_text(name)
    target_season = _coerce_int(payload.get("season"))
    target_episode = _coerce_int(payload.get("episode"))
    absolute_episode = _coerce_int(payload.get("absolute_episode"))
    season_match = _SEASON_EPISODE_RE.search(text)
    if season_match:
        season = int(season_match.group("season"))
        episode = int(season_match.group("episode"))
        if season == target_season and episode == target_episode:
            return 3
        if absolute_episode is not None and episode == absolute_episode:
            return 2
    episode_match = _EPISODE_RE.search(text)
    if episode_match:
        episode = int(episode_match.group("episode"))
        if episode == target_episode:
            return 2
        if absolute_episode is not None and episode == absolute_episode:
            return 2
    return 0


def _select_zip_member(data, payload):
    # List the zip with stdlib zipfile and pick the member the provider wants, but do not
    # extract or decode it. The host (Provider Hub v1.1+) reads the named member and runs
    # chardet via Subtitle.normalize(). Return None to let the host pick by episode.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [
            name for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith(SUBTITLE_EXTENSIONS)
        ]
        if not names:
            return None
        if (payload or {}).get("is_pack") and (payload or {}).get("kind") == "episode":
            names.sort(key=lambda name: (_filename_episode_matches(name, payload), name), reverse=True)
            if _filename_episode_matches(names[0], payload) > 0:
                return names[0]
            return None
        names.sort()
        return names[0]


def _content_type(format_name):
    mapping = {
        "srt": "application/x-subrip",
        "ass": "text/x-ssa",
        "ssa": "text/x-ssa",
        "vtt": "text/vtt",
        "sub": "text/plain",
    }
    return mapping.get(format_name, "text/plain")


def _content_payload(content, format_name):
    # Direct, non-archive subtitle body. Do not guess an encoding: the host runs chardet
    # via Subtitle.normalize(), and a worker guess only reintroduces mojibake.
    content = _normalize_subtitle_bytes(content)
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(format_name),
        "format": format_name,
        "empty": False,
    }


def _is_html_body(body):
    if not body:
        return False
    head = body[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
        or b"<body" in head
        or b"<head" in head
    )


def _require_api_key(config):
    api_key = _clean_text((config or {}).get("api_key"))
    if not api_key:
        raise ValueError("SubDL api_key is required")
    return api_key


def _request_delay(config):
    value = _coerce_int((config or {}).get("request_delay_ms"))
    if value is None:
        return 0
    return max(0, min(value, 5000)) / 1000.0


def _translation_timeout_seconds(config):
    value = (config or {}).get("ai_translate_timeout_seconds")
    if isinstance(value, bool):
        value = None
    try:
        value = float(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError, OverflowError):
        return 240
    if not math.isfinite(value):
        return 240
    return max(60.0, min(value, 600.0))


def _safe_translation_error_token(token, api_key):
    if not isinstance(token, str) or not token or (api_key and api_key in token):
        return "unknown"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", token):
        return "unknown"
    return token


def _translation_json(body):
    if not isinstance(body, bytes) or len(body) > _MAX_ERROR_BODY_BYTES:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _translation_error_token(body):
    payload = _translation_json(body)
    token = payload.get("error") if isinstance(payload, dict) else None
    return token if isinstance(token, str) else None

def _translation_job(data):
    if not isinstance(data, dict):
        return {}
    job = data.get("job")
    return job if isinstance(job, dict) else data


def _translation_job_ready(job):
    return isinstance(job, dict) and job.get("download_ready") is True


def _translation_file_format(headers):
    headers = headers or {}
    disposition = _coerce_text(headers.get("Content-Disposition"))
    filename = ""
    for part in disposition.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip().lower() in ("filename", "filename*"):
            filename = value.strip().strip("\"'")
            if "''" in filename:
                filename = filename.split("''", 1)[1]
            break
    supported = {extension[1:] for extension in SUBTITLE_EXTENSIONS}
    if filename:
        format_name = _format_from_name(filename)
        if format_name in supported:
            return format_name
    mime = _coerce_text(headers.get("Content-Type")).split(";", 1)[0].strip().lower()
    return {
        "application/x-subrip": "srt",
        "application/srt": "srt",
        "text/vtt": "vtt",
        "text/x-ssa": "ssa",
        "text/x-ass": "ass",
        "text/plain": "srt",
    }.get(mime, "srt")


def _set_response_read_timeout(response, timeout):
    """Tighten the HTTP socket timeout to the remaining operation budget."""
    try:
        first_stream = response.fp
    except (AttributeError, OSError, TypeError, ValueError):
        return

    streams = [first_stream]
    try:
        streams.append(first_stream.fp)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    for stream in streams:
        try:
            stream.raw._sock.settimeout(max(0.001, timeout))
            return
        except (AttributeError, OSError, TypeError, ValueError):
            pass


def _read_response_until_deadline(response, deadline, max_bytes=None):
    """Read an HTTP body without letting a trickling response reset the budget."""
    read_one = getattr(response, "read1", None)
    if not callable(read_one):
        remaining = deadline - time.monotonic()
        if remaining < 0:
            raise TimeoutError("SubDL response deadline reached")
        _set_response_read_timeout(response, remaining)
        amount = -1 if max_bytes is None else max_bytes + 1
        body = response.read(amount)
        if time.monotonic() > deadline:
            raise TimeoutError("SubDL response deadline reached")
        return body

    chunks = []
    total = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SubDL response deadline reached")
        _set_response_read_timeout(response, remaining)
        amount = 64 * 1024
        if max_bytes is not None:
            amount = min(amount, max_bytes + 1 - total)
            if amount <= 0:
                break
        chunk = read_one(amount)
        if time.monotonic() >= deadline:
            raise TimeoutError("SubDL response deadline reached")
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


class SubDLProvider:
    def __init__(self):
        self._pending_event = None
        self._quota_blocked_until = 0.0
        self._quota_blocked_account = None
        self._quota_status = None
        self._logged_video_keys = set()
        self._logged_video_order = []
        self._logger = logging.getLogger("subdl")
        self._not_entitled_logged = False
        self._quota_exhaustion_logged = False
        self._last_empty_sources_reset = object()
        self._malformed_translation_warned = False
        self._ai_candidate_error_warned = False
        self._translation_jobs = {}
        self._translation_uncertainty = {}
        self._account_status = None
        self._account_status_key = None
        self._account_status_refresh_after = 0.0
        self._account_request_thread = None

    def _quota_blocked(self, api_key):
        return (
            time.monotonic() < self._quota_blocked_until
            and self._quota_blocked_account == _account_digest(api_key)
        )

    def drain_events(self):
        if self._pending_event is None:
            return []
        event = dict(self._pending_event)
        self._pending_event = None
        return [event]

    def _set_translation_event(self, translation, api_key="", entitled=None, exhausted=None):
        translation = translation if isinstance(translation, dict) else {}
        if type(entitled) is not bool:
            entitled_value = translation.get("entitled")
            entitled = entitled_value if type(entitled_value) is bool else None
        if type(exhausted) is not bool:
            sources = translation.get("sources")
            if self._quota_blocked(api_key):
                exhausted = True
            elif _quota_count(translation.get("remaining")) == 0:
                exhausted = True
            elif isinstance(sources, list):
                exhausted = not sources
            else:
                exhausted = None
        reset_at = translation.get("quota_reset_at")
        if not isinstance(reset_at, str) or (api_key and api_key in reset_at):
            reset_at = None
        else:
            reset_at = _clean_text(reset_at)[:64] or None
        event = {
            "type": "translation_quota",
            "entitled": entitled,
            "exhausted": exhausted,
            "remaining": _quota_count(translation.get("remaining")),
            "limit": _quota_count(translation.get("limit")),
            "reset_at": reset_at,
        }
        self._quota_status = event
        self._pending_event = event
        return event

    def _record_successful_submission(self, response, api_key):
        if response.get("reused") is True or _translation_job(response).get("reused") is True:
            return
        reported_remaining = _quota_count(response.get("remaining"))
        if reported_remaining is not None:
            status = dict(self._quota_status or {})
            status.update(response)
            self._set_translation_event(status, api_key=api_key, exhausted=reported_remaining == 0)
            return
        if self._quota_status is None:
            return
        status = dict(self._quota_status)
        remaining = _quota_count(status.get("remaining"))
        if remaining is None:
            status["exhausted"] = None
            self._quota_status = status
            self._pending_event = dict(status)
            return
        status["remaining"] = max(0, remaining - 1)
        status["exhausted"] = status["remaining"] == 0
        self._quota_status = status
        self._pending_event = dict(status)

    def _warn_malformed_translation_once(self):
        if not self._malformed_translation_warned:
            self._malformed_translation_warned = True
            self._logger.warning("SubDL returned malformed AI translation data; skipping AI candidates")

    def _warn_ai_candidate_error_once(self):
        if not self._ai_candidate_error_warned:
            self._ai_candidate_error_warned = True
            self._logger.warning("SubDL AI candidate generation failed safely")

    def _log_not_entitled_once(self, upgrade_url=None, api_key=""):
        if not self._not_entitled_logged:
            self._not_entitled_logged = True
            upgrade_url = _clean_text(upgrade_url)
            parsed = urllib.parse.urlparse(upgrade_url)
            hostname = (parsed.hostname or "").casefold()
            if (
                parsed.scheme != "https"
                or hostname not in ("subdl.com", "www.subdl.com")
                or len(upgrade_url) > 512
                or (api_key and api_key in upgrade_url)
            ):
                upgrade_url = "https://subdl.com/pro?ref=bazarr"
            self._logger.info(
                "SubDL AI translation requires Plus or Pro: %s",
                upgrade_url,
            )

    def _log_empty_sources_for_reset(self, reset_at, api_key):
        if not isinstance(reset_at, str) or (api_key and api_key in reset_at):
            reset_at = None
        else:
            reset_at = _clean_text(reset_at)[:64] or None
        if reset_at == self._last_empty_sources_reset:
            return
        self._last_empty_sources_reset = reset_at
        self._logger.info(
            "SubDL AI translation quota is exhausted; resets at %s",
            reset_at or "not provided",
        )

    def _prune_translation_state(self, now):
        cutoff = now - TRANSLATION_JOB_TTL_SECONDS
        for mapping in (self._translation_jobs, self._translation_uncertainty):
            for key, value in list(mapping.items()):
                timestamp = value["submitted_at"] if isinstance(value, dict) else value
                if timestamp <= cutoff:
                    del mapping[key]
            while len(mapping) > TRANSLATION_STATE_MAX_ITEMS:
                del mapping[next(iter(mapping))]

    def _translation_key(self, payload, api_key):
        n_id = (payload or {}).get("n_id")
        if type(n_id) not in (int, str) or not _clean_text(n_id):
            return None
        target = _clean_text((payload or {}).get("target_language")).upper()
        if not target:
            return None
        # A digest, never the key itself, ties the job to its account, so a new
        # API key neither polls the old account's job nor inherits its markers.
        account = _account_digest(api_key)
        return (
            account, str(n_id), target,
            _coerce_int((payload or {}).get("season")),
            _coerce_int((payload or {}).get("episode")),
        )

    def _active_uncertainty(self, key, now):
        self._prune_translation_state(now)
        return key is not None and key in self._translation_uncertainty

    def _remember_translation_job(self, key, request_id, now):
        if key is None:
            return
        self._prune_translation_state(now)
        self._translation_jobs.pop(key, None)
        self._translation_jobs[key] = {"request_id": request_id, "submitted_at": now}
        while len(self._translation_jobs) > TRANSLATION_STATE_MAX_ITEMS:
            del self._translation_jobs[next(iter(self._translation_jobs))]
        self._translation_uncertainty.pop(key, None)

    def _mark_translation_uncertain(self, key, now):
        if key is None:
            return
        self._prune_translation_state(now)
        self._translation_uncertainty.pop(key, None)
        self._translation_uncertainty[key] = now
        if self._quota_status is not None:
            status = dict(self._quota_status)
            status["remaining"] = None
            status["exhausted"] = None
            self._quota_status = status
            self._pending_event = dict(status)
        while len(self._translation_uncertainty) > TRANSLATION_STATE_MAX_ITEMS:
            del self._translation_uncertainty[next(iter(self._translation_uncertainty))]

    def _log_once_for_video(self, category, video, message, *args):
        video = video or {}
        identity = {
            key: video.get(key)
            for key in (
                "kind", "title", "series", "year", "season", "episode",
                "absolute_episode", "imdb_id", "series_imdb_id", "tmdb_id", "n_id",
            )
            if video.get(key) is not None
        }
        encoded = json.dumps(identity, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        key = category + ":" + hashlib.sha256(encoded).hexdigest()
        if key in self._logged_video_keys:
            return
        self._logged_video_keys.add(key)
        self._logged_video_order.append(key)
        if len(self._logged_video_order) > 256:
            expired = self._logged_video_order.pop(0)
            self._logged_video_keys.discard(expired)
        self._logger.info(message, *args)

    def _http_get_json(self, params):
        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
        request = urllib.request.Request(
            f"{API_URL}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT),
            },
        )
        try:
            body = _urlopen_with_retry(request, HTTP_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            _raise_semantic_http_error(exc)
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                raise ValueError("Invalid SubDL api_key") from exc
            raise RuntimeError(f"SubDL API error {exc.code}: {body}") from exc
        return json.loads(body.decode("utf-8"))

    def _http_get_bytes(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        if not _is_https_download_url(url):
            raise ValueError("SubDL download URL must use HTTPS on a subdl.com host")
        request = urllib.request.Request(
            url,
            headers={"User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT)},
        )
        try:
            return _urlopen_with_retry(request, timeout, opener=_download_opener())
        except urllib.error.HTTPError as exc:
            _raise_semantic_http_error(exc)
            if exc.code == 403:
                raise ValueError("Invalid SubDL api_key") from exc
            raise

    def _http_get_account_json(self, api_key):
        query = urllib.parse.urlencode({"api_key": api_key})
        request = urllib.request.Request(
            f"{ACCOUNT_API_URL}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT),
            },
        )
        active = self._account_request_thread
        if active is not None and active.is_alive():
            raise TimeoutError("SubDL account request is still finishing")

        deadline = time.monotonic() + ACCOUNT_STATUS_TIMEOUT_SECONDS
        outcome = queue.Queue(maxsize=1)

        def fetch():
            try:
                try:
                    with urllib.request.urlopen(
                        request, timeout=ACCOUNT_STATUS_TIMEOUT_SECONDS,
                    ) as response:
                        body = _read_response_until_deadline(
                            response, deadline, max_bytes=ACCOUNT_STATUS_MAX_BYTES,
                        )
                except urllib.error.HTTPError as exc:
                    try:
                        raise RuntimeError(f"SubDL account API error {exc.code}") from exc
                    finally:
                        exc.close()
                if not isinstance(body, bytes) or len(body) > ACCOUNT_STATUS_MAX_BYTES:
                    raise ValueError("SubDL account response is invalid or too large")
                value = json.loads(body.decode("utf-8"))
            except Exception as exc:
                outcome.put((False, exc))
            else:
                outcome.put((True, value))

        worker = threading.Thread(
            target=fetch,
            name="subdl-account-status",
            daemon=True,
        )
        self._account_request_thread = worker
        worker.start()
        try:
            ok, value = outcome.get(timeout=ACCOUNT_STATUS_TIMEOUT_SECONDS)
        except queue.Empty:
            raise TimeoutError("SubDL account request deadline reached") from None
        finally:
            if not worker.is_alive():
                self._account_request_thread = None
        if not ok:
            raise value
        return value

    def _cached_account_translation_status(self, api_key):
        now = time.monotonic()
        key = hashlib.sha256(api_key.encode("utf-8")).digest()
        if key == self._account_status_key and now < self._account_status_refresh_after:
            return dict(self._account_status) if isinstance(self._account_status, dict) else None
        self._account_status_key = key
        self._account_status_refresh_after = now + ACCOUNT_STATUS_TTL_SECONDS
        self._account_status = None
        try:
            data = self._http_get_account_json(api_key)
        except Exception:
            return None
        status = _account_translation_status(data, api_key=api_key)
        if status is not None:
            self._account_status = dict(status)
            return dict(status)
        return None

    def _set_account_translation_event(self, status, api_key):
        self._set_translation_event(
            {
                "entitled": status["entitled"],
                "remaining": status["remaining"],
                "limit": status["limit"],
                "quota_reset_at": status["reset_at"],
            },
            api_key=api_key,
            entitled=status["entitled"],
            exhausted=status["exhausted"],
        )

    def _sleep(self, config):
        delay = _request_delay(config)
        if delay:
            time.sleep(delay)

    def _translation_url(self, path, api_key):
        query = urllib.parse.urlencode({"api_key": api_key})
        return f"{TRANSLATION_API_BASE_URL}/{path}?{query}"

    def _translation_call(self, path, api_key, deadline, method="GET", payload=None, max_body=_MAX_ERROR_BODY_BYTES):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, b"", {}, None
        url = self._translation_url(path, api_key)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=min(HTTP_TIMEOUT_SECONDS, remaining)) as response:
                body = _read_response_until_deadline(response, deadline, max_bytes=max_body)
                status = response.getcode() if hasattr(response, "getcode") else getattr(response, "status", 200)
                return status, body, getattr(response, "headers", {}), None
        except urllib.error.HTTPError as exc:
            try:
                body = _read_response_until_deadline(exc, deadline, max_bytes=max_body)
            except Exception:
                body = b""
            return exc.code, body, getattr(exc, "headers", {}), exc
        except Exception as exc:
            return None, b"", {}, exc

    def _translation_log_once(self, category, payload, message, *args):
        payload = payload or {}
        identity = {
            "kind": "episode" if payload.get("season") is not None else "movie",
            "season": payload.get("season"),
            "episode": payload.get("episode"),
            "n_id": payload.get("n_id"),
        }
        self._log_once_for_video(category, identity, message, *args)

    def _handle_translation_submit_error(self, status, token, api_key, payload):
        safe_token = _safe_translation_error_token(token, api_key)
        if safe_token == "translation_quota_exhausted":
            self._quota_blocked_until = time.monotonic() + 900.0
            self._quota_blocked_account = _account_digest(api_key)
            self._set_translation_event({}, api_key=api_key, exhausted=True)
            if not self._quota_exhaustion_logged:
                self._quota_exhaustion_logged = True
                self._logger.info(
                    "SubDL AI translation quota is exhausted; new candidates are paused for 900 seconds"
                )
        elif safe_token == "translation_not_entitled":
            self._set_translation_event({}, api_key=api_key, entitled=False)
            self._log_not_entitled_once()
        else:
            self._logger.warning(
                "SubDL AI translation submit failed (HTTP %s, token %s)",
                status if type(status) is int else "unknown",
                safe_token,
            )

    def _download_translation_file(self, request_id, api_key, deadline):
        path = f"jobs/{urllib.parse.quote(request_id, safe='')}/download"
        url = self._translation_url(path, api_key)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT)},
        )
        for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                with urllib.request.urlopen(request, timeout=min(HTTP_TIMEOUT_SECONDS, remaining)) as response:
                    body = _read_response_until_deadline(
                        response, deadline, max_bytes=TRANSLATION_FILE_MAX_BYTES,
                    )
                    status = response.getcode() if hasattr(response, "getcode") else getattr(response, "status", 200)
                    headers = getattr(response, "headers", {})
                if type(status) is int and 200 <= status < 300:
                    mime = _coerce_text(headers.get("Content-Type")).split(";", 1)[0].strip().lower()
                    if (
                        not body
                        or not body.strip()
                        or len(body) > TRANSLATION_FILE_MAX_BYTES
                        or _is_html_body(body)
                        or mime == "application/json"
                        or mime.endswith("+json")
                        or _translation_json(body) is not None
                    ):
                        self._logger.warning("SubDL AI translation returned an empty or invalid subtitle file")
                        return None
                    return _content_payload(body, _translation_file_format(headers))
                transient = type(status) is int and (status == 429 or 500 <= status < 600)
                if not transient or attempt >= HTTP_MAX_ATTEMPTS:
                    return None
                delay = _backoff_seconds(attempt)
            except urllib.error.HTTPError as exc:
                transient = _is_transient_http_error(exc)
                delay = _retry_after_seconds(exc) if exc.code == 429 else None
                exc.close()
                if not transient or attempt >= HTTP_MAX_ATTEMPTS:
                    return None
                if delay is None:
                    delay = _backoff_seconds(attempt)
            except Exception:
                if attempt >= HTTP_MAX_ATTEMPTS:
                    return None
                delay = _backoff_seconds(attempt)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(delay, remaining))
        return None

    def _download_ai_translation(self, payload, config, api_key):
        if (config or {}).get("ai_translate") is not True:
            return None
        started = time.monotonic()
        deadline = started + _translation_timeout_seconds(config)
        key = self._translation_key(payload, api_key)
        try:
            if key is None:
                self._logger.warning("SubDL AI translation candidate has invalid job details")
                return None
            self._prune_translation_state(started)
            if self._active_uncertainty(key, started):
                return None

            n_id = payload.get("n_id")
            target_language = _clean_text(payload.get("target_language")).upper()
            submit_payload = {"n_id": n_id, "target_language": target_language}
            season = _coerce_int(payload.get("season"))
            episode = _coerce_int(payload.get("episode"))
            if season is not None and episode is not None:
                submit_payload["season"] = season
                submit_payload["episode"] = episode

            remembered = self._translation_jobs.get(key)
            response = None
            if remembered is not None:
                request_id = remembered["request_id"]
                job = {}
            else:
                status, body, _, error = self._translation_call(
                    "subtitles", api_key, deadline, method="POST", payload=submit_payload,
                )
                if error is not None:
                    token = None
                    if isinstance(error, urllib.error.HTTPError):
                        token = _translation_error_token(body)
                        error.close()
                        safe_token = _safe_translation_error_token(token, api_key)
                        recognized = safe_token in (
                            "translation_quota_exhausted", "translation_not_entitled",
                        )
                        if status >= 500 or not recognized:
                            self._mark_translation_uncertain(key, time.monotonic())
                        self._handle_translation_submit_error(status, token, api_key, payload)
                    else:
                        reason = getattr(error, "reason", error)
                        before_send = isinstance(
                            reason, (socket.gaierror, socket.herror, ConnectionRefusedError),
                        ) or getattr(reason, "errno", None) == errno.ECONNREFUSED
                        if not before_send:
                            self._mark_translation_uncertain(key, time.monotonic())
                        self._logger.warning(
                            "SubDL AI translation submit failed before send" if before_send
                            else "SubDL AI translation submit response was lost"
                        )
                    return None
                if type(status) is not int or not 200 <= status < 300:
                    token = _translation_error_token(body)
                    safe_token = _safe_translation_error_token(token, api_key)
                    recognized = safe_token in (
                        "translation_quota_exhausted", "translation_not_entitled",
                    )
                    if status is not None and status >= 500 or not recognized:
                        self._mark_translation_uncertain(key, time.monotonic())
                    self._handle_translation_submit_error(status, token, api_key, payload)
                    return None

                response = _translation_json(body)
                if not isinstance(response, dict):
                    self._mark_translation_uncertain(key, time.monotonic())
                    self._logger.warning("SubDL AI translation submit returned an unreadable response")
                    return None
                request_id = response.get("request_id")
                if type(request_id) not in (str, int):
                    request_id = ""
                request_id = str(request_id).strip()
                if not request_id or len(request_id) > 256 or api_key in request_id:
                    self._mark_translation_uncertain(key, time.monotonic())
                    self._logger.warning("SubDL AI translation submit returned no readable request id")
                    return None
                self._remember_translation_job(key, request_id, time.monotonic())
                self._record_successful_submission(response, api_key)
                job = _translation_job(response)

            log_request_id = request_id if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", request_id) else "redacted"
            if _clean_text(job.get("status")).lower() == "failed":
                self._translation_jobs.pop(key, None)
                self._logger.warning("SubDL AI translation job %s failed", log_request_id)
                return None

            if not _translation_job_ready(job):
                now = time.monotonic()
                poll_deadline = now + max(0.0, deadline - now - 35.0)
                failures = 0
                while not _translation_job_ready(job):
                    remaining = poll_deadline - time.monotonic()
                    if remaining < 0:
                        self._logger.info(
                            "SubDL AI translation job %s is still running; SubDL will publish it for a later search",
                            log_request_id,
                        )
                        return None
                    if remaining > 0:
                        time.sleep(min(TRANSLATION_POLL_INTERVAL_SECONDS, remaining))
                    path = f"jobs/{urllib.parse.quote(request_id, safe='')}"
                    poll_status, poll_body, _, poll_error = self._translation_call(
                        path, api_key, deadline - 30.0,
                    )
                    if poll_error is not None:
                        if isinstance(poll_error, urllib.error.HTTPError):
                            if poll_error.code == 404:
                                poll_error.close()
                                self._translation_jobs.pop(key, None)
                                return None
                            poll_error.close()
                        failures += 1
                    elif poll_status == 404:
                        self._translation_jobs.pop(key, None)
                        return None
                    elif type(poll_status) is int and 200 <= poll_status < 300:
                        polled = _translation_json(poll_body)
                        if isinstance(polled, dict):
                            job = _translation_job(polled)
                            failures = 0
                            if _clean_text(job.get("status")).lower() == "failed":
                                self._translation_jobs.pop(key, None)
                                self._logger.warning("SubDL AI translation job %s failed", log_request_id)
                                return None
                        else:
                            failures += 1
                    else:
                        failures += 1
                    if failures >= TRANSLATION_POLL_FAILURE_LIMIT:
                        self._logger.warning(
                            "SubDL AI translation job %s polling stopped after repeated failures",
                            log_request_id,
                        )
                        return None
                    if not _translation_job_ready(job) and time.monotonic() >= poll_deadline:
                        self._logger.info(
                            "SubDL AI translation job %s is still running; SubDL will publish it for a later search",
                            log_request_id,
                        )
                        return None

            if time.monotonic() >= deadline:
                self._logger.info(
                    "SubDL AI translation job %s is still running; SubDL will publish it for a later search",
                    log_request_id,
                )
                return None
            result = self._download_translation_file(request_id, api_key, deadline)
            if result is None:
                self._logger.warning("SubDL AI translation job %s download failed", log_request_id)
            return result
        except Exception:
            self._logger.warning("SubDL AI translation failed safely")
            return None

    def search(self, video, languages, config):
        api_key = _require_api_key(config)
        config = dict(config or {})
        anime_mode = bool(config.get("anime_mode"))
        ai_translate_enabled = config.get("ai_translate") is True
        include_ai_translated = config.get("include_ai_translated") is True or ai_translate_enabled
        if not ai_translate_enabled:
            self._quota_status = None
            self._pending_event = None
        video = video or {}
        requested_languages = [_language_dict(language) for language in languages or []]
        requests = build_search_requests(video, requested_languages, api_key, anime_mode=anime_mode)
        if not requests:
            return []

        runtime_policy = {
            "enabled": True,
            "ai_translation_enabled": True,
            "max_pages": 2,
            "season_fallback_enabled": True,
            "title_fallback_enabled": True,
            "unpack_enabled": True,
        }
        all_items = []
        seen = set()
        primary_params = requests[0][1]
        primary_data = None
        translation_data = None
        account_status_advertised = False
        for label, params in requests:
            if not runtime_policy["enabled"]:
                break
            if label == "season" and not runtime_policy["season_fallback_enabled"]:
                continue
            page = 1
            while True:
                call_params = dict(params)
                if not runtime_policy["unpack_enabled"]:
                    call_params.pop("unpack", None)
                if page > 1:
                    call_params["page"] = page
                self._sleep(config)
                data = self._http_get_json(call_params)
                page_items = _response_items(data)
                if page == 1:
                    _apply_runtime_policy(runtime_policy, data)
                    account_status_advertised = account_status_advertised or _advertises_ai_translation(data)
                    if label == "primary":
                        primary_data = data
                        if ai_translate_enabled and isinstance(data, dict) and "translation" in data:
                            translation_data = data.get("translation")
                _merge_items(all_items, seen, data, include_ai_translated)
                max_pages = runtime_policy["max_pages"] if label == "primary" else 1
                if page >= max_pages or len(page_items) < SUBS_PER_PAGE:
                    break
                page += 1
            if not runtime_policy["enabled"]:
                break

        if not runtime_policy["enabled"]:
            return []

        if not all_items and video.get("kind") == "movie" and primary_data is not None and _is_empty_response(primary_data):
            fallback = _movie_tmdb_fallback_params(video, primary_params)
            if fallback:
                if not runtime_policy["unpack_enabled"]:
                    fallback.pop("unpack", None)
                self._sleep(config)
                fallback_data = self._http_get_json(fallback)
                _apply_runtime_policy(runtime_policy, fallback_data)
                account_status_advertised = account_status_advertised or _advertises_ai_translation(fallback_data)
                if ai_translate_enabled and not translation_data and isinstance(fallback_data, dict):
                    if "translation" in fallback_data:
                        translation_data = fallback_data.get("translation")
                if not runtime_policy["enabled"]:
                    return []
                _merge_items(all_items, seen, fallback_data, include_ai_translated)

        if (
            anime_mode
            and runtime_policy["title_fallback_enabled"]
            and not all_items
            and video.get("kind") == "episode"
        ):
            fallback = _title_only_request(video, requested_languages, api_key)
            if fallback:
                if not runtime_policy["unpack_enabled"]:
                    fallback.pop("unpack", None)
                self._sleep(config)
                fallback_data = self._http_get_json(fallback)
                _apply_runtime_policy(runtime_policy, fallback_data)
                if not runtime_policy["enabled"]:
                    return []
                _merge_items(all_items, seen, fallback_data, include_ai_translated)

        candidates = []
        suppressors = []

        def append_candidate(item, child=None):
            marked_ai = _is_ai_translated(item, child)
            if marked_ai and not include_ai_translated:
                return
            candidate = _candidate_from_item(video, requested_languages, item, child)
            if candidate is None:
                return
            if marked_ai:
                display = candidate.get("display")
                if not isinstance(display, dict):
                    display = {}
                    candidate["display"] = display
                uploader = _clean_text(display.get("uploader"))
                display["uploader"] = f"{uploader} (AI translated)" if uploader else "AI translated"
                candidate["ai_translated"] = True
            candidates.append(candidate)
            if ai_translate_enabled and _row_can_suppress_translation(video, item, child, candidate):
                suppressors.append(candidate)

        for item in all_items:
            is_pack = _is_pack(item)
            if video.get("kind") == "episode":
                if is_pack:
                    if not _pack_contains_episode(item, video):
                        continue
                    children = _children_for_item(item, video)
                    if children:
                        for child in children:
                            append_candidate(item, child)
                        continue
            append_candidate(item)

        if ai_translate_enabled and translation_data is not None:
            if not _valid_translation_block(translation_data):
                self._warn_malformed_translation_once()
            else:
                self._set_translation_event(translation_data, api_key=api_key)
                if translation_data["entitled"] is False:
                    self._log_not_entitled_once(
                        translation_data.get("upgrade_url"), api_key=api_key,
                    )
                else:
                    self._not_entitled_logged = False
                sources = translation_data["sources"]
                if translation_data["entitled"] is True and not sources:
                    self._log_empty_sources_for_reset(translation_data.get("quota_reset_at"), api_key)
                blocked = self._quota_blocked(api_key)
                if runtime_policy["ai_translation_enabled"] and not blocked:
                    try:
                        ai_candidates = _build_ai_candidates(
                            video, requested_languages, translation_data, suppressors,
                        )
                        now = time.monotonic()
                        for candidate in ai_candidates:
                            key = self._translation_key(candidate.get("provider_payload"), api_key)
                            if not self._active_uncertainty(key, now):
                                candidates.append(candidate)
                    except Exception:
                        self._warn_ai_candidate_error_once()
        elif ai_translate_enabled and account_status_advertised:
            account_status = self._cached_account_translation_status(api_key)
            if account_status is not None:
                self._set_account_translation_event(account_status, api_key)
                if account_status["entitled"] is False:
                    self._log_not_entitled_once(api_key=api_key)
                else:
                    self._not_entitled_logged = False
        return candidates

    def download(self, provider_payload, language, config):
        del language
        api_key = _require_api_key(config)
        payload = dict(provider_payload or {})
        if payload.get("provider") not in (None, PROVIDER_ID):
            raise ValueError("SubDL download payload belongs to another provider")
        if payload.get("kind") == "ai_translation":
            return self._download_ai_translation(payload, config, api_key)
        download_url, legacy_signed = _without_api_key(payload.get("download_url"))
        if not download_url:
            raise ValueError("SubDL download requires download_url")
        request_url = _download_request_url(_absolute_download_url(download_url))
        if request_url is None:
            raise ValueError("SubDL download URL must be on a subdl.com host")
        if (payload.get("download_url_signed") is True or legacy_signed) and _is_signed_download_origin(request_url):
            request_url = _with_api_key(request_url, api_key)
        body = self._http_get_bytes(request_url, timeout=HTTP_TIMEOUT_SECONDS)
        if not body or not body.strip():
            raise ValueError(f"SubDL empty download for {download_url}")
        if _is_html_body(body):
            raise ValueError(f"SubDL returned an HTML/error page for {download_url}")
        if zipfile.is_zipfile(io.BytesIO(body)):
            # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
            # the host. Keep our member selection when the zip names a specific file, and
            # fall back to host episode-based selection otherwise.
            archive = {
                "archive_b64": base64.b64encode(body).decode("ascii"),
                "archive_sha256": hashlib.sha256(body).hexdigest(),
            }
            member = _select_zip_member(body, payload)
            if member is not None:
                archive["member"] = member
            else:
                archive["episode"] = _coerce_int(payload.get("episode"))
            return archive
        format_name = _format_from_name(download_url, payload.get("format"))
        if format_name in ARCHIVE_EXTENSIONS:
            format_name = _format_from_name(payload.get("subtitle_id")) or "srt"
        return _content_payload(body, format_name)
