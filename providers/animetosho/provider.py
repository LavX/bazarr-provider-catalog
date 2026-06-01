"""AnimeTosho provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
import json
import lzma
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

PROVIDER_ID = "animetosho"
FEED_URL = "https://feed.animetosho.org/json"
STORAGE_ATTACH_URL = "https://animetosho.org/storage/attach"
HTTP_TIMEOUT_SECONDS = 15
HTTP_RETRIES = 2
DEFAULT_SEARCH_THRESHOLD = 5
MAX_SEARCH_THRESHOLD = 50
XZ_MAGIC = b"\xfd7zXZ\x00"
SUBTITLE_CODEC_FORMATS = {
    "ass": "ass",
    "ssa": "ssa",
    "srt": "srt",
    "subrip": "srt",
    "webvtt": "vtt",
}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

SUPPORTED_LANGUAGES = {
    "ara",
    "eng",
    "fin",
    "fra",
    "deu",
    "heb",
    "ind",
    "ita",
    "jpn",
    "por",
    "pol",
    "rus",
    "spa",
    "swe",
    "tha",
    "tur",
    "vie",
}
ALPHA3_TO_ALPHA2 = {
    "ara": "ar",
    "eng": "en",
    "fin": "fi",
    "fra": "fr",
    "deu": "de",
    "heb": "he",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "por": "pt",
    "pol": "pl",
    "rus": "ru",
    "spa": "es",
    "swe": "sv",
    "tha": "th",
    "tur": "tr",
    "vie": "vi",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in ALPHA3_TO_ALPHA2.items()}
BIBLIOGRAPHIC_TO_CANONICAL = {
    "alb": "sqi",
    "arm": "hye",
    "baq": "eus",
    "bur": "mya",
    "chi": "zho",
    "cze": "ces",
    "dut": "nld",
    "fre": "fra",
    "geo": "kat",
    "ger": "deu",
    "gre": "ell",
    "ice": "isl",
    "mac": "mkd",
    "mao": "mri",
    "may": "msa",
    "per": "fas",
    "rum": "ron",
    "slo": "slk",
    "tib": "bod",
    "wel": "cym",
}

_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SXXEYY_RE = re.compile(r"\bs0*(?P<season>\d+)e0*(?P<episode>\d+)\b", re.IGNORECASE)
_EXX_RE = re.compile(r"\be0*(?P<episode>\d+)\b", re.IGNORECASE)


def series_feed_url(episode_id):
    return f"{FEED_URL}?{urllib.parse.urlencode({'eid': episode_id})}"


def torrent_feed_url(entry_id):
    return f"{FEED_URL}?{urllib.parse.urlencode({'show': 'torrent', 'id': entry_id})}"


def parse_series_entries(body, search_threshold=DEFAULT_SEARCH_THRESHOLD):
    data = _load_json(body)
    if not isinstance(data, list):
        return []
    threshold = _coerce_search_threshold(search_threshold)
    entries = [item for item in data if isinstance(item, dict) and item.get("status") == "complete"]
    entries = entries[:threshold]
    entries.sort(key=lambda item: _int_or_zero(item.get("timestamp")), reverse=True)
    return entries


def parse_torrent_subtitles(body, entry, video=None):
    data = _load_json(body)
    if not isinstance(data, dict):
        return []
    entry = entry or {}
    rows = []
    for media_file, filename in _matching_media_files(data, entry, video):
        for attachment in media_file.get("attachments") or []:
            if not isinstance(attachment, dict) or attachment.get("type") != "subtitle":
                continue
            info = attachment.get("info") if isinstance(attachment.get("info"), dict) else {}
            language = _language_payload(info)
            if not language:
                continue
            subtitle_id = _int_or_none(attachment.get("id"))
            if subtitle_id is None:
                continue
            fmt = _format_from_attachment(info)
            if not fmt:
                continue
            row = {
                "subtitle_id": subtitle_id,
                "entry_id": entry.get("id"),
                "language": language,
                "format": fmt,
                "filename": filename,
                "release_info": entry.get("title") or filename,
                "download_url": _storage_url(subtitle_id),
                "size_bytes": _int_or_zero(attachment.get("size")),
                "forced": language["forced"],
                "hearing_impaired": _is_hearing_impaired(info),
                "codec": info.get("codec"),
                "track_id": info.get("trackid"),
                "track_number": info.get("tracknum"),
            }
            rows.append(row)
    return rows


def derive_matches(video, filename):
    video = video or {}
    matches = set()
    if video.get("kind") != "episode":
        return []

    normalized = _normalize(filename)
    series_tokens = _tokens(video.get("series"))
    filename_tokens = set(_tokens(filename))
    if series_tokens and all(token in filename_tokens for token in series_tokens):
        matches.add("series")
    try:
        season = int(video.get("season"))
        episode = int(video.get("episode"))
    except (TypeError, ValueError):
        season = episode = None
    if season is not None and re.search(rf"\bs0*{season}\b", normalized):
        matches.add("season")
    if season is not None and episode is not None and re.search(rf"\bs0*{season}e0*{episode}\b", normalized):
        matches.add("episode")
    elif episode is not None and re.search(rf"(^|[^0-9])0*{episode}([^0-9]|$)", normalized):
        matches.add("episode")

    # The legacy provider added these API-derived fields unconditionally.
    matches.update({"title", "series", "tvdb_id", "season", "episode"})
    return sorted(matches)


def compute_score(video, row):
    matches = set(derive_matches(video, row.get("filename")))
    if {"series", "season", "episode"}.issubset(matches):
        return 96
    if "episode" in matches:
        return 88
    if "series" in matches:
        return 78
    return 50


class AnimeToshoProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        for attempt in range(HTTP_RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError:
                raise
            except (TimeoutError, socket.timeout, urllib.error.URLError):
                if attempt >= HTTP_RETRIES:
                    raise
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError("unreachable animetosho retry state")

    def search(self, video, languages, config):
        video = video or {}
        if video.get("kind") != "episode":
            return []
        episode_id = video.get("series_anidb_episode_id")
        if not episode_id:
            return []

        config = dict(config or {})
        requested = _requested_languages(languages)
        if not requested:
            return []

        threshold = _coerce_search_threshold(config.get("search_threshold", DEFAULT_SEARCH_THRESHOLD))
        entries = parse_series_entries(self._http_get(series_feed_url(episode_id)), threshold)
        results = []
        seen = set()
        for entry in entries:
            entry_id = entry.get("id")
            if entry_id is None:
                continue
            _sleep(config)
            try:
                rows = parse_torrent_subtitles(
                    self._http_get(torrent_feed_url(entry_id)),
                    entry,
                    video,
                )
            except (TimeoutError, socket.timeout, urllib.error.HTTPError, urllib.error.URLError, ValueError):
                continue
            for row in rows:
                language = row["language"]
                alpha3 = language["alpha3"]
                if not _language_matches_request(language, requested):
                    continue
                key = (row["download_url"], alpha3, row["language"].get("country_alpha2"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, row))
        results.sort(key=lambda item: item["score"], reverse=True)
        return results

    def _result(self, video, row):
        score = compute_score(video, row)
        language = dict(row["language"])
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "entry_id": row.get("entry_id"),
            "download_url": row["download_url"],
            "filename": row.get("filename"),
            "release_info": row.get("release_info"),
            "format": row.get("format") or "srt",
            "language": language["alpha3"],
            "country_alpha2": language.get("country_alpha2"),
        }
        return {
            "provider": PROVIDER_ID,
            "id": _stable_id(row["download_url"], language["alpha3"], language.get("country_alpha2")),
            "language": language,
            "release_info": row.get("release_info"),
            "filename": row.get("filename"),
            "matches": derive_matches(video, row.get("filename")),
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": bool(row.get("hearing_impaired")),
            "page_link": row["download_url"],
            "display": {
                "source": "AnimeTosho",
                "title": row.get("release_info"),
                "release": row.get("filename"),
                "size_bytes": row.get("size_bytes", 0),
            },
            "provider_payload": payload,
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        url = payload.get("download_url")
        if not url:
            raise ValueError("animetosho download requires download_url")
        body = self._http_get(url)
        if not body.startswith(XZ_MAGIC):
            raise ValueError("animetosho download did not return an xz subtitle attachment")
        content = lzma.decompress(body)
        return _content_payload(content, (payload.get("format") or "srt").lower())


def _load_json(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return json.loads((body or b"").decode("utf-8", errors="replace"))


def _language_payload(info):
    raw_value = info.get("lang")
    raw = str(raw_value or "eng").lower()
    alpha3 = BIBLIOGRAPHIC_TO_CANONICAL.get(raw, raw)
    if len(alpha3) == 2:
        alpha3 = ALPHA2_TO_ALPHA3.get(alpha3, alpha3)
    if alpha3 not in SUPPORTED_LANGUAGES:
        return None
    name = str(info.get("name") or "")
    country_alpha2 = _portuguese_country(name) if alpha3 == "por" else None
    return {
        "alpha3": alpha3,
        "alpha2": ALPHA3_TO_ALPHA2.get(alpha3),
        "country_alpha2": country_alpha2,
        "hi": _is_hearing_impaired(info),
        "forced": _is_forced(info),
    }


def _portuguese_country(name):
    normalized = _normalize(name)
    if "brazil" in normalized or "brasil" in normalized or "por br" in normalized or "pt br" in normalized:
        return "BR"
    if "portugal" in normalized or "por pt" in normalized or "pt pt" in normalized:
        return "PT"
    return None


def _is_hearing_impaired(info):
    name = _normalize(info.get("name") or "")
    return "sdh" in name.split() or "hearing impaired" in name or "hi" in name.split()


def _is_forced(info):
    name = _normalize(info.get("name") or "")
    return bool(_int_or_zero(info.get("forced"))) or "forced" in name.split()


def _format_from_attachment(info):
    codec = str(info.get("codec") or "").lower()
    if not codec:
        return "srt"
    return SUBTITLE_CODEC_FORMATS.get(codec)


def _storage_url(subtitle_id):
    return f"{STORAGE_ATTACH_URL}/{subtitle_id:08x}/{subtitle_id}.xz"


def _content_payload(body, fmt):
    if not body:
        return {
            "content_b64": "",
            "content_sha256": "",
            "content_type": _content_type(fmt),
            "format": fmt,
            "encoding": "utf-8",
            "empty": True,
        }
    encoding = "utf-8"
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"
    return {
        "content_b64": _base64.b64encode(body).decode("ascii"),
        "content_sha256": _hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(fmt):
    if fmt in {"ass", "ssa"}:
        return "text/x-ssa"
    if fmt == "vtt":
        return "text/vtt"
    return "application/x-subrip"


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    if alpha3:
        return BIBLIOGRAPHIC_TO_CANONICAL.get(alpha3, alpha3)
    return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())


def _requested_languages(languages):
    requested = set()
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 not in SUPPORTED_LANGUAGES:
            continue
        country_alpha2 = str((language or {}).get("country_alpha2") or "").upper() or None
        requested.add((alpha3, country_alpha2))
    return requested


def _language_matches_request(language, requested):
    alpha3 = language.get("alpha3")
    country_alpha2 = language.get("country_alpha2")
    for requested_alpha3, requested_country in requested:
        if alpha3 != requested_alpha3:
            continue
        if not requested_country or country_alpha2 == requested_country:
            return True
    return False


def _media_file_matches_video(video, filename):
    video = video or {}
    if video.get("kind") != "episode":
        return True
    try:
        season = int(video.get("season"))
        episode = int(video.get("episode"))
    except (TypeError, ValueError):
        return True
    markers = list(_episode_markers(filename))
    if not markers:
        return True
    return (season, episode) in markers or (None, episode) in markers


def _matching_media_files(data, entry, video):
    for media_file in data.get("files") or []:
        if not isinstance(media_file, dict):
            continue
        filename = media_file.get("filename") or data.get("torrent_name") or entry.get("title") or ""
        if _media_file_matches_video(video, filename):
            yield media_file, filename


def _episode_markers(filename):
    value = str(filename or "")
    for match in _SXXEYY_RE.finditer(value):
        yield int(match.group("season")), int(match.group("episode"))
    for match in _EXX_RE.finditer(value):
        yield None, int(match.group("episode"))


def _coerce_search_threshold(value):
    try:
        threshold = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("animetosho search_threshold must be an integer") from exc
    if threshold < 1:
        raise ValueError("animetosho search_threshold must be at least 1")
    return min(threshold, MAX_SEARCH_THRESHOLD)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _stable_id(download_url, alpha3, country_alpha2=None):
    key = f"{download_url}:{alpha3}:{country_alpha2 or ''}"
    digest = _hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"animetosho-{digest}"


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value):
    parsed = _int_or_none(value)
    return parsed if parsed is not None else 0
