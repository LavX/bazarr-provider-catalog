"""Assrt provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import json
import math
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

PROVIDER_ID = "assrt"
BASE_URL = "https://api.assrt.net/v1"
HTTP_TIMEOUT_SECONDS = 15
USER_AGENT = "Sub-Zero/2"
MEANINGLESS_VIDEO_NAMES = {"\u4e0d\u77e5\u9053"}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

LANGUAGE_CODES = {
    "eng": {
        "alpha3": "eng",
        "alpha2": "en",
        "assrt": "eng",
        "aliases": {"eng", "english"},
    },
    "zho-CN": {
        "alpha3": "zho",
        "alpha2": "zh",
        "country": "CN",
        "assrt": "chs",
        "aliases": {"chs", "chn"},
    },
    "zho-TW": {
        "alpha3": "zho",
        "alpha2": "zh",
        "country": "TW",
        "assrt": "cht",
        "aliases": {"cht", "twn"},
    },
}
ASSRT_TO_LANGUAGE = {}
for _key, _meta in LANGUAGE_CODES.items():
    for _alias in _meta["aliases"]:
        ASSRT_TO_LANGUAGE[_alias] = _meta

_LANGLIST_RE = re.compile(r"^lang(?P<code>\w+)$")
_SXXEYY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\s*e0*(?P<episode>\d{1,3})\b", re.I)
_SEASON_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\b|\bseason[\W_]+0*(?P<season_word>\d{1,2})\b", re.I)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


class AssrtProvider:
    def __init__(self):
        self._quota_by_token = {}

    def search(self, video, languages, config):
        token = _token(config)
        requested = _requested_languages(languages)
        if not requested:
            return []
        video = dict(video or {})
        if video.get("kind") not in {"movie", "episode"}:
            return []
        quota = self._quota(token, config)
        self._sleep(request_delay_seconds(quota))
        payload = self._http_get_json(
            f"{BASE_URL}/sub/search?{urllib.parse.urlencode({'token': token, 'q': build_query(video), 'is_file': 1})}",
            config=config,
        )
        check_api_status(payload)
        results = []
        seen = set()
        for item in ((payload.get("sub") or {}).get("subs") or []):
            for language_code, language_meta in _languages_from_search_item(item):
                requested_language = _match_requested_language(language_meta, requested)
                if not requested_language:
                    continue
                video_name = _video_name(item)
                if not video_name:
                    continue
                result = self._result(video, item, video_name, language_code, requested_language)
                key = (result["provider_payload"]["subtitle_id"], result["provider_payload"]["language_code"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(result)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        token = _token(config)
        payload = dict(provider_payload or {})
        subtitle_id = payload.get("subtitle_id")
        if not subtitle_id:
            raise ValueError("assrt download requires subtitle_id")
        quota = self._quota(token, config)
        self._sleep(request_delay_seconds(quota))
        detail = self._http_get_json(
            f"{BASE_URL}/sub/detail?{urllib.parse.urlencode({'token': token, 'id': subtitle_id})}",
            config=config,
        )
        check_api_status(detail)
        download_url = select_download_url(detail, payload)
        if not download_url:
            raise ValueError(f"assrt detail did not contain a download URL for {subtitle_id}")
        self._sleep(request_delay_seconds(quota))
        body = self._http_get_bytes(download_url, config=config)
        body = _normalize_line_endings(body)
        return _content_payload(body, _subtitle_extension(payload.get("filename")) or "srt")

    def _quota(self, token, config):
        if token not in self._quota_by_token:
            payload = self._http_get_json(
                f"{BASE_URL}/user/quota?{urllib.parse.urlencode({'token': token})}",
                config=config,
            )
            check_api_status(payload)
            quota = ((payload.get("user") or {}).get("quota"))
            if not isinstance(quota, int) or quota <= 0:
                raise ValueError(f"Cannot get a positive Assrt quota from provider: {payload}")
            self._quota_by_token[token] = quota
        return self._quota_by_token[token]

    def _http_get_json(self, url, timeout=HTTP_TIMEOUT_SECONDS, config=None):
        body = self._http_get_bytes(url, timeout=timeout, config=config)
        return json.loads(body.decode("utf-8"))

    def _http_get_bytes(self, url, timeout=HTTP_TIMEOUT_SECONDS, config=None):
        del config
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT),
                "Accept": "application/json,*/*",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)

    def _result(self, video, item, video_name, language_code, requested_language):
        matches = derive_matches(video, video_name)
        score = 95 if "episode" in matches or "title" in matches else 80
        subtitle_id = str(item.get("id"))
        filename = f"assrt.{_slug(video_name)}.{language_code}.{subtitle_id}.srt"
        language = {
            "alpha3": requested_language["alpha3"],
            "alpha2": requested_language["alpha2"],
            "hi": False,
            "forced": False,
        }
        if requested_language.get("country"):
            language["country"] = requested_language["country"]
        return {
            "provider": PROVIDER_ID,
            "id": f"assrt-{subtitle_id}-{language_code}",
            "language": language,
            "release_info": video_name,
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": None,
            "display": {
                "source": "assrt",
                "title": video_name,
                "language_code": language_code,
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": subtitle_id,
                "language_code": language_code,
                "filename": filename,
                "season": _safe_int((video or {}).get("season")),
                "episode": _safe_int((video or {}).get("episode")),
            },
        }


def check_api_status(payload):
    if isinstance(payload, dict) and "status" in payload and "errmsg" in payload:
        raise ValueError(f"{payload['errmsg']} ({payload['status']})")


def request_delay_seconds(max_request_per_minute):
    return int(math.ceil(60 / max_request_per_minute))


def build_query(video):
    video = video or {}
    if video.get("kind") == "episode":
        parts = []
        if video.get("series"):
            parts.append(str(video["series"]))
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        if season is not None and episode is not None:
            parts.append(f"S{season:02d}E{episode:02d}")
        elif episode is not None:
            parts.append(f"E{episode:02d}")
        return " ".join(parts)
    parts = []
    if video.get("title"):
        parts.append(str(video["title"]))
    if video.get("year"):
        parts.append(str(video["year"]))
    return " ".join(parts)


def derive_matches(video, video_name):
    video = video or {}
    candidate_tokens = set(_tokens(video_name))
    matches = []
    if video.get("kind") == "episode":
        series_tokens = _tokens(video.get("series"))
        if series_tokens and all(token in candidate_tokens for token in series_tokens):
            matches.append("series")
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        if season is not None and _text_has_season(video_name, season):
            matches.append("season")
        if season is not None and episode is not None:
            if _text_has_episode(video_name, season, episode):
                matches.append("episode")
            elif "series" in matches and "season" in matches and not _any_episode(video_name):
                matches.append("episode")
    else:
        title_tokens = _tokens(video.get("title"))
        if title_tokens and all(token in candidate_tokens for token in title_tokens):
            matches.append("title")
        year = _safe_int(video.get("year"))
        if year is not None and str(year) in candidate_tokens:
            matches.append("year")
    return matches


def select_download_url(detail, payload):
    subs = ((detail or {}).get("sub") or {}).get("subs") or []
    if not subs:
        return None
    sub = subs[0]
    files = sub.get("filelist") if isinstance(sub.get("filelist"), list) else []
    if not files:
        return sub.get("url")
    target_episode = _safe_int((payload or {}).get("episode"))
    if target_episode is not None:
        episode_files = [item for item in files if _file_episode(item.get("f")) == target_episode]
        if episode_files:
            files = episode_files
    language_code = str((payload or {}).get("language_code") or "").lower()
    for item in files:
        filename = str(item.get("f") or "").lower()
        if language_code and language_code in filename and item.get("url"):
            return item["url"]
    for item in files:
        if item.get("url"):
            return item["url"]
    return None


def _requested_languages(languages):
    requested = []
    seen = set()
    for language in languages or []:
        meta = _requested_language_meta(language)
        if not meta:
            continue
        key = (meta["alpha3"], meta.get("country"))
        if key in seen:
            continue
        seen.add(key)
        requested.append(meta)
    return requested


def _requested_language_meta(language):
    if isinstance(language, str):
        alpha3 = language
        country = None
    elif isinstance(language, dict):
        alpha3 = language.get("alpha3") or language.get("code") or language.get("alpha2")
        country = language.get("country") or language.get("region")
    else:
        return None
    alpha3 = str(alpha3 or "").lower()
    if alpha3 in {"zh", "zho"}:
        country = str(country or "").upper() or None
        if country == "TW":
            return dict(LANGUAGE_CODES["zho-TW"])
        if country == "CN":
            return dict(LANGUAGE_CODES["zho-CN"])
        return {
            "alpha3": "zho",
            "alpha2": "zh",
            "assrt": "chs",
            "aliases": {"chs", "cht", "chn", "twn"},
        }
    if alpha3 in {"en", "eng"}:
        return dict(LANGUAGE_CODES["eng"])
    return None


def _languages_from_search_item(item):
    langlist = (((item or {}).get("lang") or {}).get("langlist") or {})
    for key in langlist:
        match = _LANGLIST_RE.match(str(key))
        if not match:
            continue
        code = match.group("code").lower()
        meta = ASSRT_TO_LANGUAGE.get(code)
        if meta:
            yield code, meta


def _match_requested_language(found_language, requested_languages):
    for requested in requested_languages:
        if requested["alpha3"] != found_language["alpha3"]:
            continue
        if requested.get("country") and requested.get("country") != found_language.get("country"):
            continue
        return requested
    return None


def _video_name(item):
    name = item.get("videoname")
    if isinstance(name, str) and name and name not in MEANINGLESS_VIDEO_NAMES:
        return name
    native = item.get("native_name")
    if isinstance(native, str):
        return native
    if isinstance(native, list) and native:
        return str(native[0])
    return name if isinstance(name, str) else None


def _token(config):
    token = str((config or {}).get("token") or "").strip()
    if not token:
        raise ValueError("assrt token must be specified")
    return token


def _text_has_episode(text, season, episode):
    for match in _SXXEYY_RE.finditer(_normalize(text)):
        if _safe_int(match.group("season")) == season and _safe_int(match.group("episode")) == episode:
            return True
    return False


def _text_has_season(text, season):
    for match in _SEASON_RE.finditer(_normalize(text)):
        if _safe_int(match.group("season") or match.group("season_word")) == season:
            return True
    return False


def _any_episode(text):
    return bool(_SXXEYY_RE.search(_normalize(text)))


def _file_episode(filename):
    match = _SXXEYY_RE.search(_normalize(filename))
    if not match:
        return None
    return _safe_int(match.group("episode"))


def _normalize_line_endings(body):
    return (body or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_payload(body, extension, empty=False):
    data = body or b""
    return {
        "content_b64": base64.b64encode(data).decode("ascii"),
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "format": (extension or "srt").lstrip(".").lower(),
        "empty": bool(empty),
    }


def _subtitle_extension(name):
    lower_name = str(name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower_name.endswith(extension):
            return extension.lstrip(".")
    return None


def _slug(value, max_length=80):
    slug = "-".join(_tokens(value))
    return (slug[:max_length].strip("-") or "subtitle")


def _tokens(value):
    normalized = _normalize(value)
    return [token for token in _NON_ALNUM_RE.split(normalized) if token]


def _normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return _WS_RE.sub(" ", value.lower()).strip()


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
