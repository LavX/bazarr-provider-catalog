"""SubDL provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


PROVIDER_ID = "subdl"
API_URL = "https://api.subdl.com/api/v1/subtitles"
DOWNLOAD_BASE_URL = "https://dl.subdl.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
ARCHIVE_EXTENSIONS = (".zip",)

# Transport-level retry for transient network failures. Upstream subliminal wraps its
# session in a RetryingSession/ProviderRetryMixin with ~3 tries and backoff; mirror that
# here so a single connection blip or 5xx/429 does not abort a whole search or download.
HTTP_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0


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


def _urlopen_with_retry(request, timeout):
    # Wrap only the raw urllib call in a bounded retry loop. Transient failures
    # (connection reset/refused/DNS via URLError, socket timeouts, and 5xx/429) are
    # retried up to HTTP_MAX_ATTEMPTS times with exponential backoff. Any other error,
    # including 4xx HTTPError other than 429, propagates unchanged to the caller's existing
    # error handling. The successful response is read and returned as bytes so the caller
    # keeps its existing return type and post-processing.
    last_exc = None
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
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
    "BR_PT": ("por", "BR", None),
    "ZH_BG": ("zho", None, "Hant"),
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
        "subs_per_page": 30,
        "comment": 1,
        "releases": 1,
        "bazarr": 1,
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


def _merge_items(target, seen, data):
    for item in _response_items(data):
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
    hi_tags = ("_hi_", " hi ", ".hi.", "sdh")
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
    start, end = _episode_range(item)
    if start is None or end is None:
        return True
    targets = [
        _coerce_int(video.get("episode")),
        _coerce_int(video.get("absolute_episode")),
    ]
    return any(target is not None and start <= target <= end for target in targets)


def _child_matches_video(child, video):
    if not child:
        return False
    targets = {
        _coerce_int(video.get("episode")),
        _coerce_int(video.get("absolute_episode")),
    }
    targets.discard(None)
    child_episode = _coerce_int(child.get("episode"))
    return child_episode in targets


def _children_for_item(item, video):
    children = item.get("unpack_files")
    if not isinstance(children, list):
        return []
    return [child for child in children if isinstance(child, dict) and _child_matches_video(child, video)]


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
        if item_episode in {video_episode, absolute_episode}:
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


def _result_id(item, child=None):
    if child:
        return _clean_text(child.get("file_n_id")) or _clean_text(child.get("name")) or _clean_text(child.get("url"))
    return _clean_text(item.get("name")) or _clean_text(item.get("url"))


def _result_filename(item, child=None):
    if child and child.get("name"):
        return _clean_text(child.get("name"))
    return _clean_text(item.get("name")) or "subdl-subtitle"


def _payload_for_item(video, item, child, is_pack):
    download_url = _clean_text((child or {}).get("url")) or _clean_text(item.get("url"))
    archive_download_url = _clean_text(item.get("url")) if child else ""
    subtitle_id = _result_id(item, child)
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "subtitle_id": subtitle_id,
        "page_link": urllib.parse.urljoin("https://subdl.com", _clean_text(item.get("subtitlePage"))),
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


class SubDLProvider:
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
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                raise ValueError("Invalid SubDL api_key") from exc
            if exc.code == 429:
                raise RuntimeError("SubDL rate limit exceeded") from exc
            if 500 <= exc.code < 600:
                raise RuntimeError(f"SubDL API unavailable: HTTP {exc.code}") from exc
            raise RuntimeError(f"SubDL API error {exc.code}: {body}") from exc
        return json.loads(body.decode("utf-8"))

    def _http_get_bytes(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT)},
        )
        try:
            return _urlopen_with_retry(request, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise ValueError("Invalid SubDL api_key") from exc
            if exc.code == 429 or exc.code == 500:
                raise RuntimeError("SubDL download limit exceeded") from exc
            raise

    def _sleep(self, config):
        delay = _request_delay(config)
        if delay:
            time.sleep(delay)

    def search(self, video, languages, config):
        api_key = _require_api_key(config)
        config = dict(config or {})
        anime_mode = bool(config.get("anime_mode"))
        video = video or {}
        requested_languages = [_language_dict(language) for language in languages or []]
        requests = build_search_requests(video, requested_languages, api_key, anime_mode=anime_mode)
        if not requests:
            return []

        all_items = []
        seen = set()
        primary_params = requests[0][1]
        primary_data = None
        for label, params in requests:
            self._sleep(config)
            data = self._http_get_json(params)
            if label == "primary":
                primary_data = data
            _merge_items(all_items, seen, data)

        if not all_items and video.get("kind") == "movie" and primary_data is not None and _is_empty_response(primary_data):
            fallback = _movie_tmdb_fallback_params(video, primary_params)
            if fallback:
                self._sleep(config)
                _merge_items(all_items, seen, self._http_get_json(fallback))

        if anime_mode and not all_items and video.get("kind") == "episode":
            fallback = _title_only_request(video, requested_languages, api_key)
            if fallback:
                self._sleep(config)
                _merge_items(all_items, seen, self._http_get_json(fallback))

        candidates = []
        for item in all_items:
            is_pack = _is_pack(item)
            if video.get("kind") == "episode":
                if is_pack:
                    if not anime_mode:
                        continue
                    if not _pack_contains_episode(item, video):
                        continue
                    children = _children_for_item(item, video)
                    if children:
                        for child in children:
                            candidate = _candidate_from_item(video, requested_languages, item, child)
                            if candidate:
                                candidates.append(candidate)
                        continue
            candidate = _candidate_from_item(video, requested_languages, item)
            if candidate:
                candidates.append(candidate)
        return candidates

    def download(self, provider_payload, language, config):
        del language
        _require_api_key(config)
        payload = dict(provider_payload or {})
        if payload.get("provider") not in (None, PROVIDER_ID):
            raise ValueError("SubDL download payload belongs to another provider")
        download_url = payload.get("download_url")
        if not download_url:
            raise ValueError("SubDL download requires download_url")
        body = self._http_get_bytes(_absolute_download_url(download_url), timeout=HTTP_TIMEOUT_SECONDS)
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
