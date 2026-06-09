"""Titlovi provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "titlovi"
API_BASE_URL = "https://kodi.titlovi.com/api/subtitles"
TOKEN_URL = f"{API_BASE_URL}/gettoken"
SEARCH_URL = f"{API_BASE_URL}/search"
HTTP_TIMEOUT_SECONDS = 15
# Transport-level retry for transient network blips (mirrors upstream subliminal's
# RetryingSession/ProviderRetryMixin: a few attempts with exponential backoff). Only
# raw transport errors and 5xx/429 are retried; every other failure propagates as-is.
HTTP_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

LANGUAGES = {
    "bos": {"alpha3": "bos", "alpha2": "bs", "titlovi": "Bosanski"},
    "eng": {"alpha3": "eng", "alpha2": "en", "titlovi": "English"},
    "hrv": {"alpha3": "hrv", "alpha2": "hr", "titlovi": "Hrvatski"},
    "mkd": {"alpha3": "mkd", "alpha2": "mk", "titlovi": "Makedonski"},
    "slv": {"alpha3": "slv", "alpha2": "sl", "titlovi": "Slovenski"},
    "srp": {"alpha3": "srp", "alpha2": "sr", "titlovi": "Srpski"},
    "srp-Cyrl": {"alpha3": "srp", "alpha2": "sr", "script": "Cyrl", "titlovi": "Cirilica"},
}
LANGUAGE_BY_TITLOVI = {value["titlovi"]: value for value in LANGUAGES.values()}

_AKA_RE = re.compile(r"^(?P<title>.+?)(?:\s+[Aa][Kk][Aa]\s+(?P<alt>.+))?$")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


class HttpResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code = int(status_code or 0)
        self.body = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
        self.headers = headers or {}


class TitloviProvider:
    def __init__(self):
        self._login_token = None
        self._user_id = None

    def search(self, video, languages, config):
        _require_credentials(config)
        requested = _requested_languages(languages)
        if not requested:
            return []
        self._ensure_token(config)
        video = video or {}
        title = _query_title(video)
        if not title:
            return []
        params = {
            "query": fix_inconsistent_naming(title),
            "lang": "|".join(language["titlovi"] for language in requested),
            "token": self._login_token,
            "userid": self._user_id,
            "json": True,
        }
        if video.get("kind") == "episode":
            season = _safe_int(video.get("season"))
            if season is not None:
                params["season"] = season
        imdb_id = _coerce_text(video.get("imdb_id"))
        if imdb_id:
            params["imdbID"] = imdb_id

        results = []
        try:
            _sleep(config)
            response = self._search_page(params)
            results.extend(response.get("SubtitleResults") or [])
            pages = min(_safe_int(response.get("PagesAvailable")) or 1, 3)
            for page in range(2, pages + 1):
                page_params = dict(params)
                page_params["pg"] = page
                _sleep(config)
                response = self._search_page(page_params)
                page_results = response.get("SubtitleResults") or []
                if not page_results:
                    break
                results.extend(page_results)
        except RuntimeError as error:
            if "Too many requests" in str(error):
                raise
            pass
        except urllib.error.HTTPError as error:
            error.close()
            pass
        except (ValueError, urllib.error.URLError):
            pass
        return _sort_results(_filter_requested_results(_results_from_api(video, results), requested))

    def download(self, provider_payload, language, config):
        del language
        _require_credentials(config)
        self._ensure_token(config)
        payload = provider_payload or {}
        url = payload.get("download_url") or payload.get("url")
        if not url:
            raise ValueError("titlovi download requires download_url")
        _sleep(config)
        response = self._http_get(url)
        if response.status_code == 429:
            raise RuntimeError("Too many requests")
        _raise_for_status(response, url)
        return extract_download(response.body, payload)

    def _ensure_token(self, config):
        if self._login_token and self._user_id:
            return
        response = self._http_post(
            TOKEN_URL,
            params={
                "username": config.get("username"),
                "password": config.get("password"),
                "json": True,
            },
        )
        if response.status_code == 401:
            raise PermissionError("Titlovi login failed")
        _raise_for_status(response, TOKEN_URL)
        payload = _json_body(response.body, "titlovi login did not return JSON")
        self._login_token = _coerce_text(payload.get("Token"))
        self._user_id = _coerce_text(payload.get("UserId"))
        if not self._login_token or not self._user_id:
            raise PermissionError("Titlovi login did not return token and user id")

    def _search_page(self, params):
        response = self._http_get(SEARCH_URL, params=params)
        if response.status_code == 429:
            raise RuntimeError("Too many requests")
        _raise_for_status(response, SEARCH_URL)
        return _json_body(response.body, "titlovi search did not return JSON")

    def _http_get(self, url, params=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{query}"
        request = urllib.request.Request(url, headers=_headers(headers))
        return _urlopen_with_retry(request, timeout)

    def _http_post(self, url, params=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        query = urllib.parse.urlencode(params or {})
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        request = urllib.request.Request(f"{url}{separator}{query}", data=b"", headers=_headers(headers), method="POST")
        return _urlopen_with_retry(request, timeout)


def _results_from_api(video, api_results):
    results = []
    wanted_episode = _safe_int((video or {}).get("episode"))
    wanted_season = _safe_int((video or {}).get("season"))
    is_episode = (video or {}).get("kind") == "episode"
    seen = set()
    for item in api_results:
        if not isinstance(item, dict):
            continue
        language = LANGUAGE_BY_TITLOVI.get(_coerce_text(item.get("Lang")))
        if not language:
            continue
        season = _safe_int(item.get("Season"))
        episode = _safe_int(item.get("Episode"))
        if is_episode:
            if wanted_season is not None and season != wanted_season:
                continue
            if wanted_episode is not None and episode not in {wanted_episode, 0}:
                continue
        subtitle_id = _coerce_text(item.get("Id"))
        if not subtitle_id or subtitle_id in seen:
            continue
        seen.add(subtitle_id)
        title, alt_title = _split_title(item.get("Title"))
        release = _coerce_text(item.get("Release"))
        matches = derive_matches(video, title, alt_title, release, season, episode, _safe_int(item.get("Year")))
        downloads = _safe_int(item.get("DownloadCount")) or 0
        rating = _float(item.get("Rating")) or 0.0
        is_pack = is_episode and episode == 0
        language_payload = _language_payload(language)
        score = min(100, 40 + len(matches) * 10 + int(rating * 3) + min(downloads, 250) // 25)
        if is_pack:
            score = max(0, score - 20)
        filename = f"titlovi.{subtitle_id}.{language_payload['alpha3']}.zip"
        results.append(
            {
                "provider": PROVIDER_ID,
                "id": f"titlovi-{subtitle_id}-{language_payload['alpha3']}",
                "language": language_payload,
                "release_info": release,
                "filename": filename,
                "matches": matches,
                "score": score,
                "score_without_hash": score,
                "score_out_of": 100,
                "hash_verifiable": False,
                "hearing_impaired_verifiable": False,
                "hearing_impaired": False,
                "page_link": _coerce_text(item.get("Link")),
                "display": {
                    "source": "titlovi.com",
                    "title": title,
                    "alternate_title": alt_title,
                    "release": release,
                    "rating": rating,
                    "downloads": downloads,
                },
                "provider_payload": {
                    "provider": PROVIDER_ID,
                    "schema": 1,
                    "subtitle_id": subtitle_id,
                    "download_url": _coerce_text(item.get("Link")),
                    "filename": filename,
                    "language": language_payload["alpha3"],
                    "script": language_payload.get("script"),
                    "season": wanted_season if is_episode else None,
                    "episode": wanted_episode if is_episode else None,
                    "is_pack": is_pack,
                    "release_info": release,
                },
            }
        )
    return results


def _filter_requested_results(results, requested):
    requested_keys = {(item["alpha3"], item.get("script")) for item in requested}
    return [
        item
        for item in results
        if (item["language"]["alpha3"], item["language"].get("script")) in requested_keys
    ]


def extract_download(body, payload=None):
    payload = payload or {}
    # Reject broken responses up front: the download endpoint can answer with an empty
    # stream or an HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError("titlovi download did not return a supported subtitle file")
    if _is_html_body(body):
        raise ValueError("titlovi download did not return a supported subtitle file")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host. A Serbian archive can carry both Latin and Cyrillic members for the
        # same episode, which the host's episode-based pick cannot tell apart, so when we
        # can list a zip we pin the script-matched member; otherwise (rar, no script, or a
        # single script present) let the host pick the member by episode.
        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        member = _select_script_member(body, payload)
        if member is not None:
            archive["member"] = member
        else:
            archive["episode"] = payload.get("episode")
        return archive
    # Direct, non-archive subtitle body.
    subtitle_format = _direct_subtitle_format(body, payload)
    if not subtitle_format:
        raise ValueError("titlovi download did not return a supported subtitle file")
    return _content_payload(body, subtitle_format)


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _select_script_member(body, payload):
    # Pin the Serbian script the user requested (Cyrl vs Latin). The host cannot tell the
    # two alphabets apart, so we only step in for Serbian zips that actually mix scripts;
    # rar (not stdlib-listable), single-script, or non-Serbian archives return None and the
    # caller falls back to host-side episode selection.
    payload = payload or {}
    if payload.get("language") != "srp" or _is_rar_archive(body) or not zipfile.is_zipfile(io.BytesIO(body)):
        return None
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if _subtitle_extension(name) and not name.rsplit("/", 1)[-1].startswith(".")
        ]
    if len(candidates) < 2:
        return None
    cyrillic, latin = [], []
    for name in candidates:
        # Match "cyr"/"cir" as a delimited token, not a substring, so a Latin release like
        # "...circle..." is not misread as Cyrillic.
        tokens = re.split(r"[^a-z0-9]+", name.lower())
        if "cyr" in tokens or "cir" in tokens:
            cyrillic.append(name)
        else:
            latin.append(name)
    if not (cyrillic and latin):
        return None  # single script: the host's episode pick is enough
    # Latin Serbian has no `script` key, so anything other than "Cyrl" means Latin.
    pool = cyrillic if payload.get("script") == "Cyrl" else latin
    # A season pack carries several episodes per script; the host cannot combine episode
    # and script, so resolve the episode here as well before pinning a single member.
    season = _safe_int(payload.get("season"))
    episode = _safe_int(payload.get("episode"))
    if season is not None and episode is not None:
        episode_pool = [name for name in pool if _member_has_episode(name, season, episode)]
        # Episode missing from the requested script: defer to host episode selection.
        return episode_pool[0] if episode_pool else None
    return pool[0]


def _member_has_episode(name, season, episode):
    # Tolerate separated SxxExx tokens (S01.E02, S01 E02, S01-E02) as well as contiguous
    # S01E02, while keeping the (?!\d) guard so "e02" never matches "e020".
    text = name.lower()
    return bool(
        re.search(rf"s0*{season}[\s._-]*e0*{episode}(?!\d)", text)
        or re.search(rf"(?<!\d){season}x0*{episode}(?!\d)", text)
    )


def derive_matches(video, title, alt_title, release, season=None, episode=None, year=None):
    video = video or {}
    release_tokens = set(_tokens(release))
    matches = []
    if video.get("kind") == "episode":
        wanted_series = _normalize(fix_inconsistent_naming(video.get("series")))
        if wanted_series and wanted_series in {_normalize(title), _normalize(alt_title)}:
            matches.append("series")
        if video.get("year") and (year is None or _safe_int(video.get("year")) == year):
            matches.append("year")
        if _safe_int(video.get("season")) is not None and _safe_int(video.get("season")) == season:
            matches.append("season")
        wanted_episode = _safe_int(video.get("episode"))
        if wanted_episode is not None and wanted_episode in {episode, 0}:
            matches.append("episode")
    else:
        wanted_title = _normalize(fix_inconsistent_naming(video.get("title")))
        if wanted_title and wanted_title in {_normalize(title), _normalize(alt_title)}:
            matches.append("title")
        if video.get("year") and _safe_int(video.get("year")) == year:
            matches.append("year")
    resolution = _coerce_text(video.get("resolution")).lower()
    if resolution and resolution in release.lower():
        matches.append("resolution")
    source_tokens = _tokens(video.get("source"))
    if source_tokens and all(token in release_tokens for token in source_tokens):
        matches.append("source")
    release_group = _normalize(video.get("release_group"))
    if release_group and release_group in release_tokens:
        matches.append("release_group")
    return matches


def fix_inconsistent_naming(title):
    mapping = {
        "DC's Legends of Tomorrow": "Legends of Tomorrow",
        "Marvel's Jessica Jones": "Jessica Jones",
    }
    return mapping.get(_coerce_text(title), _coerce_text(title))


def _requested_languages(languages):
    requested = []
    seen = set()
    for language in languages or []:
        item = _language_for_request(language)
        if not item:
            continue
        key = (item["titlovi"], item.get("script"))
        if key in seen:
            continue
        seen.add(key)
        requested.append(item)
    return requested


def _language_for_request(language):
    if not isinstance(language, dict):
        return None
    alpha3 = _language_alpha3(language)
    script = language.get("script")
    if alpha3 == "srp" and script == "Cyrl":
        return LANGUAGES["srp-Cyrl"]
    return LANGUAGES.get(alpha3)


def _language_payload(language):
    payload = {
        "alpha3": language["alpha3"],
        "alpha2": language["alpha2"],
        "hi": False,
        "forced": False,
    }
    if language.get("script"):
        payload["script"] = language["script"]
    return payload


def _language_alpha3(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        alpha2 = (language.get("alpha2") or "").lower()
    else:
        alpha3 = str(language or "").lower()
        alpha2 = ""
    if alpha3 in LANGUAGES:
        return alpha3
    alpha2_map = {"bs": "bos", "en": "eng", "hr": "hrv", "mk": "mkd", "sl": "slv", "sr": "srp"}
    return alpha2_map.get(alpha2, alpha3)


def _require_credentials(config):
    config = config or {}
    if not config.get("username") or not config.get("password"):
        raise PermissionError("Titlovi username and password are required")


def _headers(extra=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    headers.update(extra or {})
    return headers


def _http_error_headers(error):
    headers = getattr(error, "headers", None)
    return dict(headers.items()) if headers else {}


def _http_error_response(error):
    try:
        body = error.read()
    finally:
        error.close()
    return HttpResponse(error.code, body, _http_error_headers(error))


def _urlopen_with_retry(request, timeout):
    # Perform the raw urllib call with a bounded retry on transient transport failures.
    # The retry wraps the existing behaviour: a successful response and a non-retriable
    # HTTPError (4xx other than 429) return exactly what the helper returned before, and
    # 5xx/429 are still converted to an HttpResponse so callers keep their 429 -> error
    # mapping and _raise_for_status handling. Only genuine network blips are retried.
    last_error = None
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.getcode(), response.read(), dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            response = _http_error_response(error)
            if response.status_code in RETRY_STATUS_CODES and attempt < HTTP_MAX_ATTEMPTS:
                _sleep_backoff(attempt, response)
                continue
            return response
        except (socket.timeout, TimeoutError, urllib.error.URLError) as error:
            last_error = error
            if attempt >= HTTP_MAX_ATTEMPTS:
                raise
            _sleep_backoff(attempt, None)
    if last_error is not None:
        raise last_error
    raise RuntimeError("titlovi retry loop exited without a result")


def _sleep_backoff(attempt, response):
    delay = min(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX_SECONDS)
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        delay = min(retry_after, RETRY_BACKOFF_MAX_SECONDS)
    time.sleep(delay)


def _retry_after_seconds(response):
    if response is None or response.status_code != 429:
        return None
    value = (response.headers or {}).get("Retry-After")
    if value is None:
        return None
    seconds = _float(value)
    if seconds is None or seconds < 0:
        return None
    return seconds


def _json_body(body, message):
    try:
        return json.loads(_decode(body))
    except json.JSONDecodeError as exc:
        raise ValueError(message) from exc


def _raise_for_status(response, url):
    if response.status_code >= 400:
        raise urllib.error.HTTPError(url, response.status_code, f"HTTP {response.status_code}", response.headers, None)


def _sort_results(results):
    return sorted(results, key=lambda item: (item["score"], item["display"].get("downloads", 0)), reverse=True)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _split_title(value):
    title = _coerce_text(value)
    match = _AKA_RE.search(title)
    if not match:
        return title, ""
    return _coerce_text(match.group("title")), _coerce_text(match.group("alt"))


def _query_title(video):
    video = video or {}
    if video.get("kind") == "episode":
        return _coerce_text(video.get("series"))
    return _coerce_text(video.get("title"))


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _direct_subtitle_format(body, payload):
    subtitle_format = _subtitle_extension((payload or {}).get("filename", ""))
    if subtitle_format:
        return subtitle_format
    sample = _decode((body or b"")[:4096]).lstrip()
    if sample.upper().startswith("WEBVTT"):
        return "vtt"
    if "[Script Info]" in sample or "[Events]" in sample:
        return "ass"
    if "-->" in sample:
        return "srt"
    return None


def _content_payload(content, subtitle_format):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    content = _fix_line_endings(content)
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
    return "application/x-subrip"


def _fix_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


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


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _coerce_text(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return html.unescape(str(value)).strip()


def _safe_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decode(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
