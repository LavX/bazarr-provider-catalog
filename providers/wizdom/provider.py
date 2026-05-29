"""Wizdom provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile


PROVIDER_ID = "wizdom"
BASE_URL = "https://wizdom.xyz"
TMDB_BASE_URL = "https://api.tmdb.org/3"
TMDB_API_KEY = "a51ee051bcd762543373903de296e0a3"
HTTP_TIMEOUT_SECONDS = 15
DOWNLOAD_TIMEOUT_SECONDS = 10
SUPPORTED_EXTENSIONS = (".srt", ".sub")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

HEBREW_LANGUAGE = {
    "alpha3": "heb",
    "alpha2": "he",
    "hi": False,
    "forced": False,
}

MATCH_ORDER = (
    "series",
    "title",
    "year",
    "season",
    "episode",
    "series_imdb_id",
    "imdb_id",
    "resolution",
    "source",
    "video_codec",
    "audio_codec",
    "release_group",
)

_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SRT_TIMECODE_RE = re.compile(
    rb"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)
_MICRODVD_RE = re.compile(rb"\{\d+\}\{\d+\}")


def parse_releases(data, media_type, imdb_id, title, season=None, episode=None):
    rows = []
    for item in _release_items(data, media_type, season, episode):
        if not isinstance(item, dict):
            continue
        subtitle_id = item.get("id")
        release = item.get("version")
        if subtitle_id in (None, "") or not release:
            continue
        rows.append(
            {
                "subtitle_id": str(subtitle_id),
                "release": str(release),
                "imdb_id": _normalize_imdb_id(imdb_id),
                "title": _coerce_text(title) or "",
                "season": _safe_int(season),
                "episode": _safe_int(episode),
                "media_type": media_type,
                "page_link": _page_link(media_type, _normalize_imdb_id(imdb_id)),
            }
        )
    return rows


def extract_download(body, payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", "srt", empty=True)
    stream = io.BytesIO(body)
    if not zipfile.is_zipfile(stream):
        subtitle_format = _subtitle_extension(payload.get("filename", ""))
        if not subtitle_format or not _looks_like_subtitle_text(body, subtitle_format):
            raise ValueError("wizdom download did not return a zip subtitle payload")
        return _content_payload(_normalize_line_endings(body), subtitle_format)
    with zipfile.ZipFile(stream) as archive:
        name, content = _select_archive_subtitle(archive)
    subtitle_format = _subtitle_extension(name) or "srt"
    return _content_payload(_normalize_line_endings(content), subtitle_format)


class WizdomProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        del config
        language = _requested_hebrew_language(languages)
        if language is None:
            return []
        video = video or {}
        media_type = video.get("kind")
        if media_type not in {"movie", "episode"}:
            return []
        results = []
        seen = set()
        for title in _candidate_titles(video, media_type):
            imdb_id = _video_imdb_id(video, media_type)
            if not imdb_id:
                imdb_id = self._resolve_imdb_id(title, video.get("year"), media_type == "movie")
            if not imdb_id:
                continue
            releases = self._fetch_releases(imdb_id)
            rows = parse_releases(
                releases,
                media_type=media_type,
                imdb_id=imdb_id,
                title=title,
                season=video.get("season"),
                episode=video.get("episode"),
            )
            for row in rows:
                if row["subtitle_id"] in seen:
                    continue
                seen.add(row["subtitle_id"])
                results.append(_result(video, row, language))
            if results:
                return results
        return results

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        subtitle_id = payload.get("subtitle_id")
        if not subtitle_id:
            raise ValueError("wizdom download requires subtitle_id")
        body = self._http_get(
            f"{BASE_URL}/api/files/sub/{urllib.parse.quote(str(subtitle_id), safe='')}",
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            referer=payload.get("page_link"),
        )
        return extract_download(body, payload)

    def _fetch_releases(self, imdb_id):
        url = f"{BASE_URL}/api/releases/{urllib.parse.quote(_normalize_imdb_id(imdb_id), safe='')}"
        try:
            return _parse_json_bytes(self._http_get(url))
        except urllib.error.HTTPError as error:
            if error.code == 500:
                error.close()
                return {}
            raise
        except ValueError:
            return {}

    def _resolve_imdb_id(self, title, year, is_movie):
        title = (_coerce_text(title) or "").replace("'", "")
        if not title:
            return None
        category = "movie" if is_movie else "tv"
        params = [
            ("api_key", TMDB_API_KEY),
            ("query", title),
            ("language", "en"),
        ]
        if year:
            params.append(("year", str(year)))
        search_url = f"{TMDB_BASE_URL}/search/{category}?{urllib.parse.urlencode(params)}"
        try:
            search_data = _parse_json_bytes(self._http_get(search_url))
            results = search_data.get("results") if isinstance(search_data, dict) else []
            if not results:
                return None
            tmdb_id = results[0].get("id") if isinstance(results[0], dict) else None
            if not tmdb_id:
                return None
            if is_movie:
                detail_path = f"movie/{tmdb_id}"
            else:
                detail_path = f"tv/{tmdb_id}/external_ids"
            detail_params = urllib.parse.urlencode(
                [("api_key", TMDB_API_KEY), ("language", "en")]
            )
            detail_url = f"{TMDB_BASE_URL}/{detail_path}?{detail_params}"
            detail_data = _parse_json_bytes(self._http_get(detail_url))
            if not isinstance(detail_data, dict):
                return None
            return _normalize_imdb_id(detail_data.get("imdb_id"))
        except (ValueError, urllib.error.URLError):
            return None


def _release_items(data, media_type, season, episode):
    if not isinstance(data, dict):
        return []
    subs = data.get("subs") or []
    if media_type == "movie":
        return subs if isinstance(subs, list) else []
    season_number = _safe_int(season)
    episode_number = _safe_int(episode)
    if not season_number or not episode_number:
        return []
    episode_key = str(episode_number)
    season_nodes = []
    if isinstance(subs, dict):
        season_nodes.append(subs.get(str(season_number), {}))
    elif isinstance(subs, list):
        if 0 <= season_number < len(subs):
            season_nodes.append(subs[season_number])
        zero_based = season_number - 1
        if 0 <= zero_based < len(subs) and zero_based != season_number:
            season_nodes.append(subs[zero_based])
    rows = []
    seen = set()
    for node in season_nodes:
        if not isinstance(node, dict):
            continue
        for item in node.get(episode_key, []) or []:
            key = item.get("id") if isinstance(item, dict) else id(item)
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    return rows


def _result(video, row, language):
    matches = _derive_matches(video, row)
    score = _score(matches, row["media_type"])
    filename = f"wizdom.{_slug(row['release'])}.{language['alpha2']}.zip"
    return {
        "provider": PROVIDER_ID,
        "id": f"wizdom-{row['subtitle_id']}",
        "language": dict(language),
        "release_info": row["release"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": row["page_link"],
        "display": {
            "source": "wizdom.xyz",
            "title": row["title"],
            "release": row["release"],
            "subtitle_id": row["subtitle_id"],
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "page_link": row["page_link"],
            "release": row["release"],
            "filename": filename,
            "imdb_id": row["imdb_id"],
            "media_type": row["media_type"],
        },
    }


def _derive_matches(video, row):
    video = video or {}
    release = row.get("release") or ""
    matches = set()
    if row.get("media_type") == "episode":
        if _title_matches(row.get("title"), [video.get("series")] + list(video.get("alternative_series") or [])):
            matches.add("series")
        if row.get("season") and _safe_int(video.get("season")) == row.get("season"):
            matches.add("season")
        if row.get("episode") and _safe_int(video.get("episode")) == row.get("episode"):
            matches.add("episode")
        if _normalize_imdb_id(video.get("series_imdb_id")) == row.get("imdb_id"):
            matches.add("series_imdb_id")
    else:
        if _title_matches(row.get("title"), [video.get("title")] + list(video.get("alternative_titles") or [])):
            matches.add("title")
        if _normalize_imdb_id(video.get("imdb_id")) == row.get("imdb_id"):
            matches.add("imdb_id")
        year = video.get("year")
        if year and str(year) in release:
            matches.add("year")
    for field, match_name in (
        ("resolution", "resolution"),
        ("source", "source"),
        ("video_codec", "video_codec"),
        ("audio_codec", "audio_codec"),
        ("release_group", "release_group"),
    ):
        value = video.get(field)
        if value and _normalize(value) in _normalize(release):
            matches.add(match_name)
    return [name for name in MATCH_ORDER if name in matches]


def _score(matches, media_type):
    match_set = set(matches)
    if media_type == "episode":
        base = 40
        for name in ("series", "season", "episode", "series_imdb_id"):
            if name in match_set:
                base += 12
    else:
        base = 50
        if "title" in match_set:
            base += 20
        if "year" in match_set:
            base += 10
        if "imdb_id" in match_set:
            base += 10
    for name in ("resolution", "source", "video_codec", "audio_codec", "release_group"):
        if name in match_set:
            base += 2
    return min(base, 100)


def _requested_hebrew_language(languages):
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        alpha3 = (language.get("alpha3") or "").lower()
        alpha2 = (language.get("alpha2") or "").lower()
        if language.get("hi") or language.get("forced"):
            continue
        if alpha3 == "heb" or alpha2 == "he":
            return dict(HEBREW_LANGUAGE)
    return None


def _candidate_titles(video, media_type):
    primary = video.get("title") if media_type == "movie" else video.get("series")
    alternatives = video.get("alternative_titles") if media_type == "movie" else video.get("alternative_series")
    titles = []
    for title in [primary] + list(alternatives or []):
        title = _coerce_text(title)
        if title and title not in titles:
            titles.append(title)
    return titles


def _video_imdb_id(video, media_type):
    if media_type == "episode":
        return _normalize_imdb_id(video.get("series_imdb_id"))
    return _normalize_imdb_id(video.get("imdb_id"))


def _page_link(media_type, imdb_id):
    section = "movies" if media_type == "movie" else "series"
    return f"{BASE_URL}/{section}/{imdb_id}"


def _normalize_imdb_id(value):
    if value in (None, ""):
        return None
    value = str(value).strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith("tt"):
        digits = re.sub(r"\D", "", lowered[2:])
        return f"tt{digits.zfill(7)}" if digits else None
    if value.isdigit():
        return f"tt{value.zfill(7)}"
    return value


def _select_archive_subtitle(archive):
    names = [name for name in archive.namelist() if _subtitle_extension(name)]
    if not names:
        raise ValueError("wizdom archive contains no supported subtitle files")
    first = None
    for name in names:
        content = archive.read(name)
        if first is None:
            first = (name, content)
        if _looks_like_subtitle_text(content, _subtitle_extension(name)):
            return name, content
    return first


def _looks_like_subtitle_text(content, subtitle_format):
    sample = _normalize_line_endings(content or b"")[:4096].lstrip()
    if not sample:
        return False
    if subtitle_format == "sub":
        return bool(_MICRODVD_RE.search(sample))
    return (
        sample.startswith(b"WEBVTT")
        or b"[Script Info]" in sample
        or bool(_SRT_TIMECODE_RE.search(sample))
    )


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUPPORTED_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _content_payload(content, subtitle_format, empty=False):
    if empty:
        return {
            "content_b64": "",
            "content_sha256": "",
            "content_type": _content_type(subtitle_format),
            "format": subtitle_format,
            "encoding": "utf-8",
            "empty": True,
        }
    encoding = "utf-8"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(subtitle_format):
    if subtitle_format == "sub":
        return "text/plain"
    return "application/x-subrip"


def _parse_json_bytes(body):
    return json.loads((body or b"").decode("utf-8"))


def _title_matches(candidate, titles):
    candidate_norm = _normalize(candidate)
    if not candidate_norm:
        return False
    for title in titles:
        title_norm = _normalize(title)
        if title_norm and title_norm == candidate_norm:
            return True
    return False


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _slug(value):
    normalized = _normalize(value)
    return "-".join(part for part in normalized.split() if part) or "release"


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
