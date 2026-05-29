"""SubsUnacs provider for the Bazarr+ Provider Hub catalog."""

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

PROVIDER_ID = "subsunacs"
BASE_URL = "https://subsunacs.net"
SEARCH_URL = f"{BASE_URL}/search.php"
HOME_URL = f"{BASE_URL}/index.php"
HTTP_TIMEOUT_SECONDS = 10
SUPPORTED_LANGUAGES = {"bul": "bg", "eng": "en"}
ALPHA2_TO_ALPHA3 = {"bg": "bul", "en": "eng"}
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".txt")
ARCHIVE_FILE_COUNT_LIMIT = 256
ARCHIVE_SUBTITLE_FILE_COUNT_LIMIT = 64
ARCHIVE_MEMORY_LIMIT = 100 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
TV_NAME_FIXES = {
    "Marvel's Daredevil": "Daredevil",
    "Marvel's Luke Cage": "Luke Cage",
    "Marvel's Iron Fist": "Iron Fist",
    "DC's Legends of Tomorrow": "Legends of Tomorrow",
    "Doctor Who (2005)": "Doctor Who",
    "Star Trek: Deep Space Nine": "Star Trek DS9",
    "Star Trek: The Next Generation": "Star Trek TNG",
    "Superman & Lois": "Superman and Lois",
}
MOVIE_NAME_FIXES = {
    "Back to the Future Part III": "Back to the Future 3",
    "Back to the Future Part II": "Back to the Future 2",
    "Bill & Ted Face the Music": "Bill Ted Face the Music",
}

_ROW_RE = re.compile(r"<tr\b[^>]*\bonmouseover=['\"][^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_CELL_RE = re.compile(r"<td\b(?P<attrs>[^>]*)>(?P<body>.*?)</td>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r"\bhref=['\"](?P<href>[^'\"]+)['\"]", re.I)
_TITLE_ATTR_RE = re.compile(r"\btitle=['\"](?P<title>[^'\"]*)['\"]", re.I | re.S)
_YEAR_RE = re.compile(r"<span\b[^>]*class=['\"][^'\"]*\bsmGray\b[^'\"]*['\"][^>]*>\s*&nbsp;\((?P<year>\d{4})\)", re.I)
_RATING_RE = re.compile(r"<img\b[^>]*(?:alt|title)=['\"](?P<rating>[\d.]+)", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    for match in _ROW_RE.finditer(text):
        row = match.group("body")
        cells = [cell.group("body") for cell in _CELL_RE.finditer(row)]
        if len(cells) < 6:
            continue
        title_cell = next((cell for cell in cells if "tdMovie" in cell or "tooltip" in cell), cells[0])
        anchor = _first_anchor(title_cell)
        if not anchor:
            continue
        href = anchor["href"]
        if "/subtitles/" not in href:
            continue
        year_match = _YEAR_RE.search(title_cell)
        rating_match = _RATING_RE.search(cells[3] if len(cells) > 3 else "")
        rows.append(
            {
                "subtitle_id": _subtitle_id_from_href(href),
                "page_url": _absolute_url(href),
                "download_url": _download_url(href),
                "title": _strip_tags(anchor["body"]),
                "year": int(year_match.group("year")) if year_match else None,
                "num_cds": _parse_int(cells[1] if len(cells) > 1 else ""),
                "fps": _parse_float(cells[2] if len(cells) > 2 else ""),
                "rating": _parse_float(rating_match.group("rating") if rating_match else ""),
                "uploader": _strip_tags(cells[5] if len(cells) > 5 else "") or None,
                "notes": _notes_from_anchor(anchor["attrs"]),
            }
        )
    return rows


def parse_detail_entries(body):
    text = _decode_html(body)
    entries = []
    for anchor in _ANCHOR_RE.finditer(text):
        attrs = anchor.group("attrs")
        href_match = _HREF_RE.search(attrs)
        if not href_match:
            continue
        href = html.unescape(href_match.group("href"))
        if "getentry.php" not in href:
            continue
        filename = _strip_tags(anchor.group("body"))
        if not _is_subtitle_file(filename):
            continue
        entries.append({"filename": os.path.basename(filename), "entry_url": _absolute_url(href)})
    return entries


def extract_archive_files(body):
    if not body:
        return []
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = sorted(archive.namelist())
            if len(names) > ARCHIVE_FILE_COUNT_LIMIT:
                return []
            candidates = [name for name in names if _is_subtitle_file(name)]
            if len(candidates) > ARCHIVE_SUBTITLE_FILE_COUNT_LIMIT:
                return []
            return _archive_rows_from_pairs((name, archive.read(name)) for name in candidates)
    if _is_external_archive(body):
        return _archive_rows_from_pairs(_extract_external_archive_files(body))
    return []


def derive_matches(video, item):
    video = video or {}
    matches = []
    if item.get("media_type") == "episode":
        if _title_matches(video.get("series"), item.get("title")):
            matches.append("series")
        season, episode = _season_episode_from_filename(item.get("filename"))
        if season is not None and video.get("season") is not None and season == int(video.get("season")):
            matches.append("season")
        if episode is not None and video.get("episode") is not None and episode == int(video.get("episode")):
            matches.append("episode")
    else:
        if _title_matches(video.get("title"), item.get("title")):
            matches.append("title")
        if video.get("year") and item.get("year") and int(video.get("year")) == int(item.get("year")):
            matches.append("year")
    if item.get("fps") and video.get("fps"):
        try:
            if abs(float(item["fps"]) - float(video["fps"])) < 0.001:
                matches.append("fps")
        except (TypeError, ValueError):
            pass
    release_text = _normalize_release(f"{item.get('filename') or ''} {item.get('notes') or ''}")
    for match_name, key in (("release_group", "release_group"), ("source", "source"), ("resolution", "resolution")):
        value = _normalize_release(video.get(key))
        if value and value in release_text:
            matches.append(match_name)
    return matches


class SubsUnacsProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        request = urllib.request.Request(url, headers=_headers(referer))
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        headers = _headers(referer)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        video = dict(video or {})
        requested = _requested_languages(languages)
        if not requested:
            return []
        if video.get("kind") == "episode":
            if not video.get("series") or video.get("season") is None or video.get("episode") is None:
                return []
            media_type = "episode"
            title = _search_episode_title(video)
        elif video.get("kind") == "movie":
            if not video.get("title"):
                return []
            media_type = "movie"
            title = _search_title(video["title"], MOVIE_NAME_FIXES)
        else:
            return []

        results = []
        seen = set()
        config = dict(config or {})
        for alpha3, variants in requested.items():
            _sleep(config)
            rows = parse_search_results(self._http_post(SEARCH_URL, _search_payload(video, title, alpha3), referer=HOME_URL))
            for row in rows[:20]:
                if not _row_matches_video(row, video, media_type):
                    continue
                _sleep(config)
                try:
                    entries = _entries_from_download_body(self._http_get(row["download_url"], referer=SEARCH_URL), row["download_url"])
                except Exception:
                    continue
                for entry in entries:
                    if not _file_matches_video(entry["filename"], video, media_type):
                        continue
                    for variant in variants:
                        item = {**row, **entry, "media_type": media_type, "language": variant}
                        key = (row["download_url"], entry["filename"], variant["alpha3"])
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(self._result(video, item))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, item):
        language = item["language"]
        matches = derive_matches(video, item)
        score = 35
        for match_name, value in (
            ("title", 20),
            ("series", 20),
            ("year", 10),
            ("season", 12),
            ("episode", 15),
            ("release_group", 7),
            ("source", 5),
            ("resolution", 4),
            ("fps", 4),
        ):
            if match_name in matches:
                score += value
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "download_url": item["download_url"],
            "entry_url": item.get("entry_url"),
            "filename": item["filename"],
            "title": item.get("title"),
            "year": item.get("year"),
            "language": language["alpha3"],
            "release_info": item["filename"],
        }
        return {
            "provider": PROVIDER_ID,
            "id": f"subsunacs-{hashlib.sha1((item['download_url'] + item['filename'] + language['alpha3']).encode('utf-8')).hexdigest()[:16]}",
            "language": {
                "alpha3": language["alpha3"],
                "alpha2": SUPPORTED_LANGUAGES[language["alpha3"]],
                "hi": language["hi"],
                "forced": language["forced"],
            },
            "release_info": item["filename"],
            "filename": item["filename"],
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": language["hi"],
            "page_link": item.get("page_url") or item["download_url"],
            "display": {
                "source": "subsunacs.net",
                "title": item.get("title"),
                "release": item["filename"],
                "uploader": item.get("uploader"),
            },
            "provider_payload": payload,
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        if payload.get("entry_url"):
            data = _normalize_line_endings(self._http_get(payload["entry_url"], referer=payload.get("download_url") or SEARCH_URL))
            return _content_payload(data, _subtitle_extension(payload.get("filename")) or "srt")
        url = payload.get("download_url")
        if not url:
            raise ValueError("subsunacs download requires download_url or entry_url")
        entries = _entries_from_download_body(self._http_get(url, referer=SEARCH_URL), url)
        selected = select_subtitle_file(entries, payload)
        if selected.get("entry_url"):
            data = _normalize_line_endings(self._http_get(selected["entry_url"], referer=url))
        else:
            data = _normalize_line_endings(selected["content"])
        return _content_payload(data, _subtitle_extension(selected["filename"]) or "srt")


def select_subtitle_file(files, payload):
    if not files:
        raise ValueError("subsunacs download contains no supported subtitle files")
    wanted = payload.get("filename")
    for item in files:
        if item["filename"] == wanted:
            return item
    release_info = _normalize_release(payload.get("release_info") or wanted)

    def score(index_item):
        index, item = index_item
        value = max(0, 10 - index)
        name = _normalize_release(item["filename"])
        if release_info:
            for token in [part for part in release_info.split(".") if len(part) > 2]:
                if token in name:
                    value += 4
        if item["filename"].lower().endswith(".srt"):
            value += 5
        return value

    return max(enumerate(files), key=score)[1]


def _entries_from_download_body(body, download_url):
    entries = parse_detail_entries(body)
    if entries:
        return entries
    files = extract_archive_files(body)
    for item in files:
        item["download_url"] = download_url
    return files


def _extract_external_archive_files(body):
    errors = []
    if py7zz is not None:
        try:
            return _extract_external_archive_files_with_py7zz(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("unar"):
        try:
            return _extract_external_archive_files_with_unar(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("7z") or shutil.which("7zz"):
        try:
            return _extract_external_archive_files_with_7z(body)
        except Exception as error:
            errors.append(error)
    if errors:
        details = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        raise RuntimeError(f"SubsUnacs archive extraction failed: {details}") from errors[-1]
    raise RuntimeError("SubsUnacs archive extraction requires bundled py7zz")


def _extract_external_archive_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("SubsUnacs bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subsunacs.archive")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_external_archive_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("SubsUnacs archive fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subsunacs.archive")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract SubsUnacs archive: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_external_archive_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("SubsUnacs archive fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subsunacs.archive")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", "-y", f"-o{output_dir}", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract SubsUnacs archive: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if not _is_subtitle_file(rel):
                continue
            if os.path.getsize(path) > ARCHIVE_MEMORY_LIMIT:
                continue
            try:
                os.chmod(path, os.stat(path).st_mode | 0o600)
            except OSError:
                pass
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    if len(files) > ARCHIVE_SUBTITLE_FILE_COUNT_LIMIT:
        return []
    return files


def _archive_rows_from_pairs(pairs):
    rows = []
    for filename, content in pairs:
        if _is_subtitle_file(filename):
            rows.append({"filename": os.path.basename(filename), "content": content})
    return rows


def _is_external_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
        or body.startswith(b"7z\xbc\xaf\x27\x1c")
    )


def _is_ignored_txt_file(filename):
    lowered = os.path.basename(filename or "").lower()
    return lowered.endswith(".txt") and bool(
        re.search(r"subsunacs\.net|read ?me|procheti", lowered, re.I)
    )


def _is_subtitle_file(filename):
    lowered = (filename or "").lower()
    return lowered.endswith(SUBTITLE_EXTENSIONS) and not _is_ignored_txt_file(filename)


def _first_anchor(value):
    match = _ANCHOR_RE.search(value or "")
    if not match:
        return None
    href_match = _HREF_RE.search(match.group("attrs"))
    if not href_match:
        return None
    return {
        "href": html.unescape(href_match.group("href")),
        "attrs": match.group("attrs"),
        "body": match.group("body"),
    }


def _notes_from_anchor(attrs):
    match = _TITLE_ATTR_RE.search(attrs or "")
    if not match:
        return ""
    value = html.unescape(match.group("title"))
    return _strip_tags(re.sub(r"<img\b[^>]*>", "", value, flags=re.I))


def _subtitle_id_from_href(href):
    match = re.search(r"-(\d+)/?!?$", href or "")
    return match.group(1) if match else None


def _download_url(href):
    url = _absolute_url(href)
    if not url.endswith("/"):
        url += "/"
    return url + "!"


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", html.unescape(value or ""))


def _row_matches_video(row, video, media_type):
    if media_type == "movie":
        if not _title_matches(video.get("title"), row.get("title")):
            return False
        return not video.get("year") or row.get("year") == int(video.get("year"))
    if not _title_matches(video.get("series"), row.get("title")):
        return False
    return True


def _file_matches_video(filename, video, media_type):
    if media_type == "movie":
        return True
    season, episode = _season_episode_from_filename(filename)
    if season is None or episode is None:
        return True
    return season == int(video.get("season")) and episode == int(video.get("episode"))


def _season_episode_from_filename(filename):
    normalized = _normalize_release(filename)
    match = re.search(r"s(?P<season>\d{1,2})e(?P<episode>\d{1,3})", normalized)
    if match:
        return int(match.group("season")), int(match.group("episode"))
    match = re.search(r"(?P<season>\d{1,2})x(?P<episode>\d{1,3})", normalized)
    if match:
        return int(match.group("season")), int(match.group("episode"))
    match = re.search(r"(?<!\d)(?P<season>\d)(?P<episode>\d{2})(?!\d)", normalized)
    if match:
        return int(match.group("season")), int(match.group("episode"))
    return None, None


def _requested_languages(languages):
    grouped = {}
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 not in SUPPORTED_LANGUAGES:
            continue
        variant = {
            "alpha3": alpha3,
            "hi": bool(language.get("hi")) if isinstance(language, dict) else False,
            "forced": bool(language.get("forced")) if isinstance(language, dict) else False,
        }
        grouped.setdefault(alpha3, []).append(variant)
    return grouped


def _alpha3_for_language(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        if alpha3:
            return alpha3
        return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())
    value = str(language or "").lower()
    return ALPHA2_TO_ALPHA3.get(value, value)


def _search_payload(video, title, alpha3):
    payload = {
        "m": title,
        "l": 1 if alpha3 == "eng" else 0,
        "c": "",
        "y": "",
        "action": "   \u0422\u044a\u0440\u0441\u0438   ",
        "a": "",
        "d": "",
        "u": "",
        "g": "",
        "t": "",
        "imdbcheck": 1,
    }
    if (video or {}).get("kind") == "movie" and (video or {}).get("year"):
        payload["y"] = int(video["year"])
    return payload


def _search_episode_title(video):
    series = _search_title(video.get("series"), TV_NAME_FIXES)
    return f"{series} {int(video.get('season')):02d} {int(video.get('episode')):02d}"


def _search_title(title, replacements):
    value = str(title or "")
    for old, new in replacements.items():
        if value == old or value.startswith(old):
            value = value.replace(old, new, 1)
            break
    return _ascii_fold(value).replace("'", "")


def _headers(referer=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8,bg;q=0.7",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _parse_int(value):
    try:
        return int(_strip_tags(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_float(value):
    try:
        return float(_strip_tags(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _normalize_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_payload(content, subtitle_format):
    encoding = "utf-8"
    for candidate in ("utf-8", "cp1251", "windows-1251", "latin-1"):
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
    return "application/x-subrip" if subtitle_format == "srt" else "text/plain"


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
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "windows-1251", "latin-1"):
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
    return _NON_ALNUM_RE.sub(" ", _ascii_fold(value).lower()).strip()


def _normalize_release(value):
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", ".", _ascii_fold(value).lower()).strip(".")


def _ascii_fold(value):
    decomposed = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
