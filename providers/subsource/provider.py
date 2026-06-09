"""SubSource provider for the Bazarr+ Provider Hub catalog."""

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


PROVIDER_ID = "subsource"
BASE_API_URL = "https://api.subsource.net/api/v1"
SITE_URL = "https://subsource.net"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

# Transport-level retry for transient network failures only. Upstream subliminal
# uses a RetryingSession/ProviderRetryMixin with a few tries plus backoff so a
# single connection blip does not abort a search or download. We mirror that at
# the raw urllib call: at most RETRY_ATTEMPTS total tries (RETRY_ATTEMPTS - 1
# retries), exponential backoff capped at RETRY_BACKOFF_MAX_SECONDS. Only
# connection errors, timeouts, and HTTP 5xx/429 are retried. Every other error
# (4xx, auth failures, parse errors) propagates unchanged on the first attempt.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _retry_after_seconds(exc):
    # Honor a Retry-After header on a 429 if the server sent one. Only the simple
    # delta-seconds form is supported; anything else falls back to the backoff.
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, RETRY_BACKOFF_MAX_SECONDS)


def _is_retriable_http_error(exc):
    return isinstance(exc, urllib.error.HTTPError) and exc.code in RETRY_STATUS_CODES


def _is_retriable_transport_error(exc):
    # A urllib.error.HTTPError is a subclass of URLError, so check it first and
    # only retry the transient status codes; other 4xx HTTPErrors must propagate.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRY_STATUS_CODES
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, (socket.timeout, TimeoutError))


def _retry_delay(attempt, exc):
    if _is_retriable_http_error(exc) and exc.code == 429:
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            return retry_after
    delay = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
    return min(delay, RETRY_BACKOFF_MAX_SECONDS)


def _with_transport_retry(do_request):
    # Run do_request (a single raw urllib request that returns the response body)
    # under a bounded retry loop. Transient transport failures are retried with
    # exponential backoff up to RETRY_ATTEMPTS total tries; the final failure and
    # any non-transient error are re-raised unchanged so existing error handling
    # (HTTPError 429/4xx mapping, parse errors) still runs in the caller.
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return do_request()
        except Exception as exc:  # noqa: BLE001 - re-raised below unless transient
            if attempt >= RETRY_ATTEMPTS or not _is_retriable_transport_error(exc):
                raise
            delay = _retry_delay(attempt, exc)
            if delay > 0:
                time.sleep(delay)


_SUBSOURCE_TO_LANGUAGE = {
    "English": ("eng", None, None),
    "Farsi_persian": ("fas", None, None),
    "Abkhazian": ("abk", None, None),
    "Afrikaans": ("afr", None, None),
    "Albanian": ("sqi", None, None),
    "Amharic": ("amh", None, None),
    "Arabic": ("ara", None, None),
    "Aragonese": ("arg", None, None),
    "Armenian": ("hye", None, None),
    "Assamese": ("asm", None, None),
    "Azerbaijani": ("aze", None, None),
    "Basque": ("eus", None, None),
    "Belarusian": ("bel", None, None),
    "Bengali": ("ben", None, None),
    "Bosnian": ("bos", None, None),
    "Brazillian Portuguese": ("por", "BR", None),
    "Breton": ("bre", None, None),
    "Bulgarian": ("bul", None, None),
    "Burmese": ("mya", None, None),
    "Catalan": ("cat", None, None),
    "Chinese BG code": ("zho", None, None),
    "Croatian": ("hrv", None, None),
    "Czech": ("ces", None, None),
    "Danish": ("dan", None, None),
    "Dutch": ("nld", None, None),
    "Espranto": ("epo", None, None),
    "Estonian": ("est", None, None),
    "Finnish": ("fin", None, None),
    "French": ("fra", None, None),
    "Gaelic": ("gla", None, None),
    "Georgian": ("kat", None, None),
    "German": ("deu", None, None),
    "Greek": ("ell", None, None),
    "Hebrew": ("heb", None, None),
    "Hindi": ("hin", None, None),
    "Hungarian": ("hun", None, None),
    "Icelandic": ("isl", None, None),
    "Igbo": ("ibo", None, None),
    "Indonesian": ("ind", None, None),
    "Interlingua": ("ina", None, None),
    "Irish": ("gle", None, None),
    "Italian": ("ita", None, None),
    "Japanese": ("jpn", None, None),
    "Kannada": ("kan", None, None),
    "Kazakh": ("kaz", None, None),
    "Khmer": ("khm", None, None),
    "Korean": ("kor", None, None),
    "Kurdish": ("kur", None, None),
    "Latvian": ("lav", None, None),
    "Lithuanian": ("lit", None, None),
    "Luxembourgish": ("ltz", None, None),
    "Macedonian": ("mkd", None, None),
    "Malay": ("msa", None, None),
    "Malayalam": ("mal", None, None),
    "Marathi": ("mar", None, None),
    "Mongolian": ("mon", None, None),
    "Navajo": ("nav", None, None),
    "Nepali": ("nep", None, None),
    "Northen Sami": ("sme", None, None),
    "Norwegian": ("nor", None, None),
    "Occitan": ("oci", None, None),
    "Polish": ("pol", None, None),
    "Portuguese": ("por", None, None),
    "Pushto": ("pus", None, None),
    "Romanian": ("ron", None, None),
    "Russian": ("rus", None, None),
    "Serbian": ("srp", None, None),
    "Sindhi": ("snd", None, None),
    "Sinhala": ("sin", None, None),
    "Slovak": ("slk", None, None),
    "Slovenian": ("slv", None, None),
    "Somali": ("som", None, None),
    "Spanish": ("spa", None, None),
    "Swahili": ("swa", None, None),
    "Swedish": ("swe", None, None),
    "Tagalog": ("tgl", None, None),
    "Tamil": ("tam", None, None),
    "Tatar": ("tat", None, None),
    "Telugu": ("tel", None, None),
    "Thai": ("tha", None, None),
    "Turkish": ("tur", None, None),
    "Turkmen": ("tuk", None, None),
    "Ukrainian": ("ukr", None, None),
    "Urdu": ("urd", None, None),
    "Uzbek": ("uzb", None, None),
    "Vietnamese": ("vie", None, None),
    "Welsh": ("cym", None, None),
}
_LANGUAGE_TO_SUBSOURCE = {value: key for key, value in _SUBSOURCE_TO_LANGUAGE.items()}
_NAME_TO_ALPHA3 = {key.lower(): value[0] for key, value in _SUBSOURCE_TO_LANGUAGE.items()}
SUPPORTED_ALPHA3 = sorted({value[0] for value in _SUBSOURCE_TO_LANGUAGE.values()})

_WS_RE = re.compile(r"\s+")
_SEASON_EPISODE_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,4})\b", re.I)
_ALT_EPISODE_RE = re.compile(r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,4})\b", re.I)
_SEASON_RE = re.compile(r"\bS(?P<season>\d{1,2})(?!E\d)\b", re.I)
# Member-level episode matcher. Tolerates a separator between the season and
# episode parts (S01E02, S01.E02, S01 E02, S01-E02) and the NxNN form, plus the
# contiguous whole-token "{season}{episode:02d}" form (e.g. "101" for S01E01).
# The (?!\d) / delimited guards stop "720" matching "720p" or "264" matching
# "x264", and stop "e02" matching "e020".


def _member_has_episode(name, season, episode):
    if season is None or episode is None:
        return False
    text = _coerce_text(name).lower()
    return bool(
        re.search(rf"\bs0*{season}[\s._-]*e0*{episode}(?!\d)", text)
        or re.search(rf"(?<!\d){season}x0*{episode}(?!\d)", text)
        # Whole-token "{season}{episode:02d}" form (e.g. "101"). Require a
        # non-alphanumeric boundary on both sides so "720" never matches inside
        # "720p" and "264" never matches inside "x264".
        or re.search(rf"(?<![a-z0-9]){season}{episode:02d}(?![a-z0-9])", text)
    )


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


def _language_payload(language):
    if isinstance(language, dict):
        payload = dict(language)
    else:
        payload = {"alpha3": str(language)}
    payload.setdefault("alpha3", payload.get("alpha2") or "")
    if not payload.get("country") and payload.get("country_alpha2"):
        payload["country"] = payload.get("country_alpha2")
    payload.setdefault("hi", False)
    payload.setdefault("forced", False)
    return payload


def _subsource_name(language):
    payload = _language_payload(language)
    alpha3 = payload.get("alpha3")
    country = payload.get("country")
    script = payload.get("script")
    candidates = [
        (alpha3, country, script),
        (alpha3, country, None),
        (alpha3, None, script),
        (alpha3, None, None),
    ]
    for candidate in candidates:
        name = _LANGUAGE_TO_SUBSOURCE.get(candidate)
        if name:
            return name
    return None


def language_names(languages):
    names = {_subsource_name(language) for language in languages or []}
    return sorted(name for name in names if name)


def auth_headers(api_key):
    return {
        "Accept": "application/json",
        "User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT),
        "X-API-Key": api_key,
    }


def _alpha3_from_name(name):
    key = _clean_text(name).lower()
    if key in ("farsi_persian", "farsi/persian"):
        return "fas"
    return _NAME_TO_ALPHA3.get(key)


def _language_dict(name, hi=False, forced=False):
    alpha3 = _alpha3_from_name(name)
    if not alpha3:
        return None
    language = {"alpha3": alpha3, "hi": bool(hi), "forced": bool(forced)}
    mapped = _SUBSOURCE_TO_LANGUAGE.get(_clean_text(name))
    country = mapped[1] if mapped else None
    if country:
        language["country_alpha2"] = country
    return language


def _request_delay(config):
    value = _coerce_int((config or {}).get("request_delay_ms"))
    if value is None:
        return 0
    return max(0, min(value, 5000)) / 1000.0


def _require_api_key(config):
    api_key = _clean_text((config or {}).get("api_key"))
    if not api_key:
        raise ValueError("SubSource api_key is required")
    return api_key


def _candidate_titles(video):
    video = video or {}
    titles = set()
    if video.get("kind") == "episode":
        if video.get("series"):
            titles.add(_clean_text(video.get("series")).lower())
        for key in ("alternative_series", "alternative_titles"):
            for title in video.get(key) or []:
                if title:
                    titles.add(_clean_text(title).lower())
    else:
        if video.get("title"):
            titles.add(_clean_text(video.get("title")).lower())
        for title in video.get("alternative_titles") or []:
            if title:
                titles.add(_clean_text(title).lower())
    return titles


def _result_matches_video_title(result, video):
    titles = _candidate_titles(video)
    if not titles:
        return False
    result_titles = {_clean_text(result.get("title")).lower()}
    alternate = _clean_text(result.get("alternateTitle")).lower()
    if alternate:
        result_titles.add(alternate)
    if not any(title and (title in item or item in title) for title in titles for item in result_titles if item):
        return False
    video_year = _coerce_int((video or {}).get("year"))
    result_year = _coerce_int(result.get("releaseYear"))
    return video_year is None or result_year is None or video_year == result_year


def _title_search_params(video):
    video = video or {}
    kind = video.get("kind")
    if kind == "episode":
        title = _clean_text(video.get("series"))
        imdb_id = _clean_text(video.get("series_imdb_id"))
    elif kind == "movie":
        title = _clean_text(video.get("title"))
        imdb_id = _clean_text(video.get("imdb_id"))
    else:
        return []

    params = []
    if imdb_id:
        imdb = {"searchType": "imdb", "imdb": imdb_id}
        if kind == "episode" and video.get("season") is not None:
            imdb["season"] = video.get("season")
        params.append(imdb)
    if title:
        text = {"searchType": "text", "q": title.lower()}
        if kind == "episode" and video.get("season") is not None:
            text["season"] = video.get("season")
        params.append(text)
    return params


def parse_release_season_episode(release_info):
    season = None
    episode = None
    for release in release_info or []:
        text = _coerce_text(release)
        match = _SEASON_EPISODE_RE.search(text) or _ALT_EPISODE_RE.search(text)
        if match:
            season = int(match.group("season"))
            episode = int(match.group("episode"))
            break
        season_match = _SEASON_RE.search(text)
        if season is None and season_match:
            season = int(season_match.group("season"))
    return season, episode


def is_hearing_impaired(item):
    if item.get("hearingImpaired"):
        return True
    commentary = _clean_text(item.get("commentary")).lower()
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
    if any(tag in commentary for tag in non_hi_tags):
        return False
    hi_tags = ("_hi_", " hi ", ".hi.", "sdh", "_cc_", " cc ", ".cc.", "closed caption")
    return any(tag in commentary for tag in hi_tags)


def is_forced(item):
    if item.get("foreignParts"):
        return True
    commentary = _clean_text(item.get("commentary")).lower()
    return "forced" in commentary or "foreign" in commentary


def _uploader_name(item):
    uploader_id = item.get("uploaderId")
    for contributor in item.get("contributors") or []:
        if contributor.get("id") == uploader_id:
            return _clean_text(contributor.get("displayname"))
    return ""


def _release_names(item):
    releases = item.get("releaseInfo")
    if isinstance(releases, list):
        return [_clean_text(release) for release in releases if _clean_text(release)]
    text = _clean_text(releases)
    return [text] if text else []


def _release_info(item):
    return ", ".join(_release_names(item))


def _requested_variants_for_alpha3(requested_languages, alpha3):
    return [
        _language_payload(language)
        for language in requested_languages or []
        if _language_payload(language).get("alpha3") == alpha3
    ]


def _variant_allowed(requested_languages, alpha3, hi, forced):
    variants = _requested_variants_for_alpha3(requested_languages, alpha3)
    if not variants:
        return False
    for variant in variants:
        if variant.get("forced") and not forced:
            continue
        if not variant.get("forced") and forced:
            continue
        if variant.get("hi") and not hi:
            continue
        return True
    return False


def _matches_for_item(video, item, season, episode, is_pack, matched_imdb=False):
    video = video or {}
    matches = set()
    if video.get("kind") == "episode":
        matches.add("series")
        # Only claim an IMDb match when the title was actually selected by IMDb
        # id; a text-search fallback must not inflate the score with it.
        if matched_imdb and video.get("series_imdb_id"):
            matches.add("series_imdb_id")
        if season is not None and season == _coerce_int(video.get("season")):
            matches.add("season")
        if episode is not None and episode == _coerce_int(video.get("episode")):
            matches.add("episode")
        if is_pack:
            matches.add("episode")
    elif video.get("kind") == "movie":
        matches.add("title")
        if matched_imdb and video.get("imdb_id"):
            matches.add("imdb_id")
    return sorted(matches)


def _payload_for_item(video, item, season, episode, is_pack):
    return {
        "provider": PROVIDER_ID,
        "schema": 1,
        "subtitle_id": item.get("subtitleId"),
        "page_link": urllib.parse.urljoin(SITE_URL, _clean_text(item.get("link"))),
        "release_info": _release_info(item),
        "format": "zip",
        "kind": (video or {}).get("kind"),
        "season": season,
        "episode": _coerce_int((video or {}).get("episode")) if is_pack else episode,
        "is_pack": bool(is_pack),
    }


def _candidate_from_item(video, requested_languages, item, matched_imdb=False):
    hi = is_hearing_impaired(item)
    forced = is_forced(item)
    language = _language_dict(item.get("language"), hi=hi, forced=forced)
    if not language:
        return None
    if not _variant_allowed(requested_languages, language["alpha3"], hi, forced):
        return None

    season = None
    episode = None
    is_pack = False
    if (video or {}).get("kind") == "episode":
        season, episode = parse_release_season_episode(item.get("releaseInfo"))
        requested_season = _coerce_int((video or {}).get("season"))
        # The subtitles request already filters server side by seasonNumber and
        # episodeNumber, so only reject when a release token is present and
        # actually mismatches. Missing tokens (miniseries/DVD releases or
        # season-page packs) are treated as unknown, not as a mismatch.
        if season is not None and season != requested_season:
            return None
        if episode is not None and episode != _coerce_int((video or {}).get("episode")):
            return None
        is_pack = episode is None

    matches = _matches_for_item(video, item, season, episode, is_pack, matched_imdb)
    payload = _payload_for_item(video, item, season, episode, is_pack)
    score = min(100, 40 + len(matches) * 12)
    uploader = _uploader_name(item)
    return {
        "provider": PROVIDER_ID,
        "id": item.get("subtitleId"),
        "language": language,
        "release_info": _release_info(item),
        "filename": f"subsource-{item.get('subtitleId')}.zip",
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": hi,
        "display": {
            "source": "subsource-api",
            "uploader": uploader,
            "page_link": payload["page_link"],
        },
        "provider_payload": payload,
    }


def _is_subtitle_member(name):
    # Real subtitle members only: skip directory entries, archive sidecars
    # (__MACOSX/._x.srt, .DS_Store) and any AppleDouble/dotfile whose basename
    # starts with ".". The host does an exact "member in namelist" check and
    # hard-fails on a mismatch, so pinning one of these would silently deliver a
    # garbage resource fork instead of the subtitle.
    if name.endswith("/"):
        return False
    if not name.lower().endswith(SUBTITLE_EXTENSIONS):
        return False
    return not os.path.basename(name).startswith(".")


def _select_zip_member(data, payload):
    # List the archive with stdlib zipfile and pick the member ourselves; the host
    # then reads the named member and decodes it. We only pin a member on a
    # confident, unique match (the requested episode in a pack, or a lone
    # subtitle); otherwise we return None so the caller defers to host-side
    # episode selection, which fails loudly on a true no-match instead of
    # silently delivering the wrong member.
    payload = payload or {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [name for name in archive.namelist() if _is_subtitle_member(name)]
    if not names:
        return None
    if payload.get("is_pack") and payload.get("kind") == "episode":
        season = _coerce_int(payload.get("season"))
        episode = _coerce_int(payload.get("episode"))
        episode_names = [name for name in names if _member_has_episode(name, season, episode)]
        # Pin only when exactly one member carries the requested season+episode.
        # Zero matches or several ambiguous matches defer to the host.
        if len(episode_names) == 1:
            return episode_names[0]
        return None
    # Non-pack (movie or single episode): pin only a lone subtitle. A multi-member
    # archive has nothing here to disambiguate it confidently, so defer.
    if len(names) == 1:
        return names[0]
    return None


def _is_archive_body(body):
    if not body:
        return False
    if zipfile.is_zipfile(io.BytesIO(body)):
        return True
    return body.startswith(b"Rar!") or body.startswith(b"7z\xbc\xaf\x27\x1c")


def _is_html_body(body):
    head = (body or b"")[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
        or b"<body" in head
        or b"<head" in head
    )


def _archive_payload(body, payload):
    # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to the
    # host. When we can list the zip cheaply we pin the member; otherwise the host picks
    # by episode via guessit. The host detects the encoding via Subtitle.normalize().
    result = {
        "archive_b64": base64.b64encode(body).decode("ascii"),
        "archive_sha256": hashlib.sha256(body).hexdigest(),
    }
    member = None
    if zipfile.is_zipfile(io.BytesIO(body)):
        member = _select_zip_member(body, payload)
    if member:
        result["member"] = member
    else:
        result["episode"] = _coerce_int((payload or {}).get("episode"))
    return result


class SubSourceProvider:
    def _sleep(self, config):
        delay = _request_delay(config)
        if delay:
            time.sleep(delay)

    def _url_for_path(self, path, params=None):
        url = f"{BASE_API_URL}/{path.lstrip('/')}"
        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        if clean_params:
            url = f"{url}?{urllib.parse.urlencode(clean_params)}"
        return url

    def _http_get_json(self, path, params, config):
        api_key = _require_api_key(config)
        request = urllib.request.Request(
            self._url_for_path(path, params),
            headers=auth_headers(api_key),
        )

        def do_request():
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()

        try:
            body = _with_transport_retry(do_request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 400:
                raise RuntimeError("SubSource request parameters are invalid") from exc
            if exc.code == 401:
                raise ValueError("SubSource api_key is invalid or missing") from exc
            if exc.code == 403:
                raise RuntimeError("SubSource access denied") from exc
            if exc.code == 429:
                raise RuntimeError("SubSource rate limit exceeded") from exc
            raise RuntimeError(f"SubSource API error {exc.code}: {body}") from exc
        return json.loads(body.decode("utf-8"))

    def _http_get_bytes(self, path, config):
        api_key = _require_api_key(config)
        request = urllib.request.Request(
            self._url_for_path(path),
            headers=auth_headers(api_key),
        )

        def do_request():
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()

        try:
            return _with_transport_retry(do_request)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ValueError("SubSource api_key is invalid or missing") from exc
            if exc.code == 429:
                raise RuntimeError("SubSource rate limit exceeded") from exc
            raise

    def _search_title_id(self, video, config):
        params_list = _title_search_params(video)
        if not params_list:
            return None, False
        for params in params_list:
            self._sleep(config)
            data = self._http_get_json("movies/search", params, config)
            for result in data.get("data") or []:
                if _result_matches_video_title(result, video):
                    matched_imdb = params.get("searchType") == "imdb"
                    return result.get("movieId"), matched_imdb
        return None, False

    def search(self, video, languages, config):
        _require_api_key(config)
        video = video or {}
        requested_languages = [_language_payload(language) for language in languages or []]
        title_id, matched_imdb = self._search_title_id(video, config or {})
        if not title_id:
            return []

        results = []
        for language_name in language_names(requested_languages):
            params = {
                "language": language_name.lower(),
                "limit": 100,
                "movieId": title_id,
            }
            if video.get("kind") == "episode":
                params["seasonNumber"] = video.get("season")
                params["episodeNumber"] = video.get("episode")
            self._sleep(config or {})
            data = self._http_get_json("subtitles", params, config or {})
            if data.get("success") is False:
                continue
            for item in data.get("data") or []:
                if not isinstance(item, dict):
                    continue
                candidate = _candidate_from_item(video, requested_languages, item, matched_imdb)
                if candidate:
                    results.append(candidate)
        return results

    def download(self, provider_payload, language, config):
        del language
        _require_api_key(config)
        payload = dict(provider_payload or {})
        if payload.get("provider") not in (None, PROVIDER_ID):
            raise ValueError("SubSource download payload belongs to another provider")
        subtitle_id = payload.get("subtitle_id")
        if subtitle_id is None:
            raise ValueError("SubSource download requires subtitle_id")
        body = self._http_get_bytes(f"subtitles/{subtitle_id}/download", config or {})
        if not body or not body.strip():
            raise ValueError(f"SubSource empty download for subtitle {subtitle_id}")
        if _is_html_body(body):
            raise ValueError(f"SubSource returned an HTML/error page for subtitle {subtitle_id}")
        if not _is_archive_body(body):
            raise ValueError(f"SubSource download for subtitle {subtitle_id} is not an archive")
        return _archive_payload(body, payload)
