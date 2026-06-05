"""SubSource provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import json
import os
import re
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


def _filename_episode_matches(name, payload):
    text = _coerce_text(name)
    target_season = _coerce_int((payload or {}).get("season"))
    target_episode = _coerce_int((payload or {}).get("episode"))
    match = _SEASON_EPISODE_RE.search(text) or _ALT_EPISODE_RE.search(text)
    if not match:
        return 0
    season = int(match.group("season"))
    episode = int(match.group("episode"))
    return 2 if season == target_season and episode == target_episode else 0


def _select_zip_member(data, payload):
    # List the archive with stdlib zipfile and pick the member ourselves; the host
    # extracts and decodes it. For an episode pack we keep the SxxEyy match; only when
    # no member matches confidently do we hand selection to the host via guessit.
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
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = response.read()
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
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.read()
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
