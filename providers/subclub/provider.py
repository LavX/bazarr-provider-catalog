"""Subclub provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in manifest
    py7zz = None

PROVIDER_ID = "subclub"
BASE_URL = "https://www.subclub.eu"
SEARCH_URL = f"{BASE_URL}/jutud.php"
ARCHIVE_LIST_URL = f"{BASE_URL}/subtitles_archivecontent.php"
DOWNLOAD_URL = f"{BASE_URL}/down.php"
HTTP_TIMEOUT_SECONDS = 30
SUPPORTED_LANGUAGES = {"est": "et"}
ALPHA2_TO_ALPHA3 = {"et": "est"}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_ROW_START_RE = re.compile(r"<tr\b[^>]*class=['\"][^'\"]*\balt\d?\b[^'\"]*['\"][^>]*>", re.I)
_TABLE_END_RE = re.compile(r"</table>", re.I)
_ANCHOR_RE = re.compile(r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>", re.I | re.S)
_DOWN_ID_RE = re.compile(r"down\.php\?id=(?P<id>\d+)", re.I)
_TITLE_RE = re.compile(
    r"^(?P<title>.+?)\s*\((?P<year>\d{4})\)(?:\s*\[(?P<season>\d+)x(?P<episode>\d+)\])?\s*$",
    re.I,
)
_IMDB_RE = re.compile(r"tt\d+", re.I)
_FPS_RE = re.compile(r"class=['\"]fps['\"][^>]*>(?P<fps>.*?)</span>", re.I | re.S)
_RATING_RE = re.compile(r"<span\b[^>]*title=['\"]Hindajaid:[^'\"]*['\"][^>]*>(?P<rating>.*?)</span>", re.I | re.S)
_ARCHIVE_LINK_RE = re.compile(
    r"<a\b[^>]*href=['\"](?P<href>[^'\"]*down\.php\?id=\d+[^'\"]*filename=[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    starts = list(_ROW_START_RE.finditer(text))
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else _table_end(text, start.end())
        row = text[start.start() : end]
        link = _subtitle_link(row)
        if not link:
            continue
        archive_id = link["archive_id"]
        title_match = _TITLE_RE.match(link["title"])
        if not title_match:
            continue
        imdb_match = _IMDB_RE.search(row)
        rows.append(
            {
                "archive_id": archive_id,
                "page_link": f"{DOWNLOAD_URL}?id={archive_id}",
                "archive_url": f"{DOWNLOAD_URL}?id={archive_id}",
                "archive_list_url": f"{ARCHIVE_LIST_URL}?id={archive_id}",
                "title": title_match.group("title").strip(),
                "year": int(title_match.group("year")),
                "season": int(title_match.group("season")) if title_match.group("season") else None,
                "episode": int(title_match.group("episode")) if title_match.group("episode") else None,
                "imdb_id": imdb_match.group(0) if imdb_match else None,
                "fps": _parse_float(_first_match(_FPS_RE, row, "fps")),
                "rating": _parse_float(_first_match(_RATING_RE, row, "rating")),
                "uploader": _uploader_from_row(row),
            }
        )
    rows.sort(key=lambda row: -(row.get("rating") or 0.0))
    return rows


def parse_archive_listing(body):
    text = _decode_html(body)
    rows = []
    for match in _ARCHIVE_LINK_RE.finditer(text):
        href = html.unescape(match.group("href"))
        filename = _strip_tags(match.group("body"))
        if not filename.lower().endswith(SUBTITLE_EXTENSIONS):
            continue
        rows.append(
            {
                "filename": filename,
                "url": _subclub_url(href),
            }
        )
    return rows


def derive_matches(video, item):
    video = video or {}
    matches = []
    if item.get("media_type") == "movie":
        if _title_matches(video.get("title"), item.get("title")):
            matches.append("title")
        if video.get("year") and item.get("year") and int(video.get("year")) == int(item.get("year")):
            matches.append("year")
        if video.get("imdb_id") and item.get("imdb_id") == video.get("imdb_id"):
            matches.append("imdb_id")
    else:
        if _title_matches(video.get("series"), item.get("title")):
            matches.append("series")
        if video.get("season") is not None and item.get("season") == int(video.get("season")):
            matches.append("season")
        if video.get("episode") is not None and item.get("episode") == int(video.get("episode")):
            matches.append("episode")
        if video.get("series_imdb_id") and item.get("imdb_id") == video.get("series_imdb_id"):
            matches.append("series_imdb_id")
    release_text = _normalize_release(item.get("filename") or item.get("release_info"))
    for match_name, key in (("release_group", "release_group"), ("source", "source"), ("resolution", "resolution")):
        value = _normalize_release(video.get(key))
        if value and value in release_text:
            matches.append(match_name)
    return matches


class SubclubProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "et-EE,et;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        video = dict(video or {})
        if "est" not in _requested_languages(languages):
            return []
        if video.get("kind") == "movie":
            if not video.get("title"):
                return []
            return self._search(video, str(video["title"]), "movie", dict(config or {}))
        if video.get("kind") == "episode":
            if not video.get("series") or video.get("season") is None or video.get("episode") is None:
                return []
            return self._search(video, str(video["series"]), "episode", dict(config or {}))
        return []

    def _search(self, video, title, media_type, config):
        _sleep(config)
        search_body = self._http_get(_search_url(title), referer=BASE_URL + "/")
        hits = [hit for hit in parse_search_results(search_body) if _hit_matches_video(hit, video, media_type)]
        results = []
        seen = set()
        for hit in hits[:5]:
            _sleep(config)
            files = parse_archive_listing(self._http_get(hit["archive_list_url"], referer=_search_url(title)))
            if not files:
                files = [{"filename": f"subclub-{hit['archive_id']}.zip", "url": None, "synthetic_archive": True}]
            for file_info in files:
                item = {**hit, **file_info, "media_type": media_type}
                key = (hit["archive_id"], file_info.get("filename"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, item))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, item):
        matches = derive_matches(video, item)
        score = 35
        for match_name, value in (
            ("title", 20),
            ("series", 20),
            ("year", 10),
            ("season", 12),
            ("episode", 15),
            ("imdb_id", 15),
            ("series_imdb_id", 15),
            ("release_group", 7),
            ("source", 5),
            ("resolution", 4),
        ):
            if match_name in matches:
                score += value
        filename = item.get("filename") or f"subclub-{item['archive_id']}.zip"
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "archive_id": item["archive_id"],
            "url": item.get("url"),
            "archive_url": item.get("archive_url"),
            "filename": filename,
            "title": item.get("title"),
            "year": item.get("year"),
            "season": item.get("season"),
            "episode": item.get("episode"),
            "imdb_id": item.get("imdb_id"),
            "fps": item.get("fps"),
            "rating": item.get("rating"),
            "uploader": item.get("uploader"),
            "media_type": item.get("media_type"),
            "release_info": filename,
        }
        # A synthetic fallback archive has only the generic "subclub-<id>.zip" name,
        # so carry the video's release hints into the payload. Without this,
        # select_subtitle_file() has nothing but "subclub" and the archive id and
        # would pick the first .srt instead of the file matching the wanted release.
        if item.get("synthetic_archive"):
            hints = _video_release_hints(video)
            if hints:
                payload["release_hints"] = hints
        return {
            "provider": PROVIDER_ID,
            "id": f"subclub-{item['archive_id']}-{hashlib.sha1(filename.encode('utf-8')).hexdigest()[:12]}",
            "language": {"alpha3": "est", "alpha2": "et", "hi": False, "forced": False},
            "release_info": filename,
            "filename": filename,
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item.get("page_link"),
            "display": {
                "source": "subclub.eu",
                "title": item.get("title"),
                "release": filename,
                "rating": item.get("rating"),
                "uploader": item.get("uploader"),
            },
            "provider_payload": payload,
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("url")
        if url:
            body = self._http_get(url, timeout=30)
            if not body:
                raise ValueError(f"subclub empty response for archive {payload.get('archive_id')}")
            return _content_payload(_normalize_line_endings(body), _format_from_filename(payload.get("filename")))
        archive_url = payload.get("archive_url")
        if not archive_url:
            raise ValueError("subclub download requires url or archive_url")
        body = self._http_get(archive_url, timeout=60)
        return extract_archive_download(body, payload)


def extract_archive_download(body, payload=None):
    payload = payload or {}
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(_normalize_line_endings(dict(files)[selected]), _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body or b"")
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(_normalize_line_endings(archive.read(selected)), _subtitle_extension(selected) or "srt")
    # Non-archive fallback: the archive endpoint can answer down.php with an HTML/error
    # page or an empty body. The synthetic fallback filename ends in ".zip", so
    # _format_from_filename() defaults to "srt" and we would otherwise hand back an
    # invalid subtitle that looks successful. Reject those bodies instead.
    if not body or not body.strip():
        raise ValueError(f"subclub empty download for archive {payload.get('archive_id')}")
    if _is_html_body(body):
        raise ValueError(f"subclub returned an HTML/error page for archive {payload.get('archive_id')}")
    return _content_payload(_normalize_line_endings(body), _format_from_filename(payload.get("filename")))


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("subclub archive contains no supported subtitle files")
    try:
        season = int((payload or {}).get("season"))
        episode = int((payload or {}).get("episode"))
    except (TypeError, ValueError):
        season = episode = None
    release_info = _normalize_release((payload or {}).get("release_info") or (payload or {}).get("filename"))
    release_hints = _normalize_release((payload or {}).get("release_hints"))
    hint_tokens = {
        token
        for source in (release_info, release_hints)
        for token in source.split(".")
        if len(token) > 2
    }

    def score(index_name):
        index, name = index_name
        normalized = _normalize_release(os.path.basename(name))
        value = max(0, 10 - index)
        if season is not None and episode is not None:
            if f"s{season:02d}e{episode:02d}" in normalized:
                value += 70
            elif f"{season}x{episode:02d}" in normalized or f"{season}x{episode}" in normalized:
                value += 65
        for token in hint_tokens:
            if token in normalized:
                value += 4
        if name.lower().endswith(".srt"):
            value += 5
        return value

    return max(enumerate(candidates), key=score)[1]


def _extract_rar_files(body):
    errors = []
    if py7zz is not None:
        try:
            return _extract_rar_files_with_py7zz(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("unar"):
        try:
            return _extract_rar_files_with_unar(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("7z") or shutil.which("7zz"):
        try:
            return _extract_rar_files_with_7z(body)
        except Exception as error:
            errors.append(error)
    if errors:
        details = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        raise RuntimeError(f"Subclub RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Subclub RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Subclub bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subclub.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Subclub RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subclub.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract Subclub RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Subclub RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subclub.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", "-y", f"-o{output_dir}", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract Subclub RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if not _subtitle_extension(rel):
                continue
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    if not files:
        raise ValueError("subclub archive contains no supported subtitle files")
    return files


def _subtitle_link(row):
    for anchor in _ANCHOR_RE.finditer(row or ""):
        href = html.unescape(anchor.group("href"))
        id_match = _DOWN_ID_RE.search(href)
        if not id_match:
            continue
        if "filename=" in href:
            continue
        title = _strip_tags(anchor.group("body"))
        return {"archive_id": id_match.group("id"), "title": title}
    return None


def _hit_matches_video(hit, video, media_type):
    if media_type == "episode":
        if hit.get("season") is None or hit.get("episode") is None:
            return False
        if int(video.get("season")) != hit["season"] or int(video.get("episode")) != hit["episode"]:
            return False
        return _title_matches(video.get("series"), hit.get("title"))
    if hit.get("season") is not None or hit.get("episode") is not None:
        return False
    if video.get("year") and hit.get("year") != int(video.get("year")):
        return False
    return _title_matches(video.get("title"), hit.get("title"))


def _table_end(text, start):
    match = _TABLE_END_RE.search(text, start)
    return match.start() if match else len(text)


def _first_match(pattern, text, group_name):
    match = pattern.search(text or "")
    return match.group(group_name) if match else ""


def _parse_float(text):
    if not text:
        return None
    try:
        return float(_strip_tags(text).strip().replace(",", ".").split()[0])
    except (ValueError, IndexError):
        return None


def _uploader_from_row(row):
    cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row or "", re.I | re.S)
    return _strip_tags(cells[-1]) if cells else None


def _requested_languages(languages):
    requested = set()
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 in SUPPORTED_LANGUAGES:
            requested.add(alpha3)
    return requested


def _alpha3_for_language(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        if alpha3:
            return alpha3
        return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())
    value = str(language or "").lower()
    return ALPHA2_TO_ALPHA3.get(value, value)


def _search_url(title):
    return f"{SEARCH_URL}?{urllib.parse.urlencode({'otsing': title})}"


def _subclub_url(href):
    cleaned = html.unescape(href or "").lstrip("./")
    while cleaned.startswith("../"):
        cleaned = cleaned[3:]
    return urllib.parse.urljoin(BASE_URL + "/", cleaned)


def _is_rar_archive(body):
    return bool(body) and (body.startswith(b"Rar!\x1a\x07\x00") or body.startswith(b"Rar!\x1a\x07\x01\x00"))


def _is_html_body(body):
    if not body:
        return False
    head = body[:1024].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or head.startswith(b"<?xml") or head.startswith(b"<!--") or b"<body" in head or b"<head" in head


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _normalize_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_payload(content, subtitle_format):
    encoding = "utf-8"
    for candidate in ("utf-8", "cp1257", "latin-1"):
        try:
            content.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    if subtitle_format == "sub":
        return "text/plain"
    return "application/x-subrip"


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(int(delay_ms), 5000) / 1000.0)


def _title_matches(wanted, candidate):
    wanted_tokens = _tokens(wanted)
    candidate_tokens = set(_tokens(candidate))
    return bool(wanted_tokens) and all(token in candidate_tokens for token in wanted_tokens)


def _strip_tags(value):
    stripped = _TAG_RE.sub("", value or "")
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()


def _decode_html(body):
    if isinstance(body, str):
        return body
    raw = body or b""
    for encoding in ("utf-8-sig", "utf-8", "cp1257", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_release(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", ".", folded.lower()).strip(".")


def _video_release_hints(video):
    video = video or {}
    parts = []
    for key in ("filename", "name", "release_group", "source", "resolution", "video_codec", "audio_codec"):
        value = video.get(key)
        if value:
            parts.append(str(value))
    return _normalize_release(".".join(parts))
