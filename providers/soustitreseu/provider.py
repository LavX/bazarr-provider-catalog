"""Sous-Titres.eu provider for the Bazarr+ Provider Hub catalog."""

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

PROVIDER_ID = "soustitreseu"
BASE_URL = "https://www.sous-titres.eu"
SEARCH_URL = f"{BASE_URL}/search.html"
HTTP_TIMEOUT_SECONDS = 15
SUPPORTED_LANGUAGES = {"eng": "en", "fra": "fr"}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
LANGUAGE_ORDER = ("eng", "fra")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_MEDIA_ROW_RE = re.compile(
    r"<li\b[^>]*class=['\"](?P<class>[^'\"]*\b(?:serie|film)\b[^'\"]*)['\"][^>]*>(?P<body>.*?)</li>",
    re.I | re.S,
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>", re.I | re.S)
_H3_RE = re.compile(r"<h3\b[^>]*>(?P<body>.*?)</h3>", re.I | re.S)
_SPAN_RE = re.compile(
    r"<span\b[^>]*class=['\"](?P<class>[^'\"]+)['\"][^>]*>(?P<body>.*?)</span>",
    re.I | re.S,
)
_ANY_SPAN_RE = re.compile(r"<span\b[^>]*>(?P<body>.*?)</span>", re.I | re.S)
_ARCHIVE_RE = re.compile(
    r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*class=['\"][^'\"]*\bsubList\b[^'\"]*['\"][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_IMG_LANG_RE = re.compile(r"<img\b[^>]*(?:alt|title)=['\"](?P<lang>[a-z]{2})['\"][^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)\s*$")
_EPISODE_LABEL_RE = re.compile(r"(?P<season>\d{1,2})\s*(?:x|×)\s*(?P<episode>\d{1,3})", re.I)
_SEASON_LABEL_RE = re.compile(r"\bS(?P<season>\d{1,2})\b", re.I)
_SXXEXX_RE = re.compile(r"s(?P<season>\d{1,2})e(?P<episode>\d{1,3})", re.I)
_X_EP_RE = re.compile(r"(?P<season>\d{1,2})x(?P<episode>\d{1,3})", re.I)


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    for match in _MEDIA_ROW_RE.finditer(text):
        class_name = match.group("class")
        media_type = "series" if re.search(r"\bserie\b", class_name, re.I) else "film"
        h3 = _H3_RE.search(match.group("body"))
        if not h3:
            continue
        anchor = _ANCHOR_RE.search(h3.group("body"))
        if not anchor:
            continue
        title, year, aliases = _title_from_heading(anchor.group("body"))
        if not title:
            continue
        rows.append(
            {
                "media_type": media_type,
                "title": title,
                "year": year,
                "aliases": aliases,
                "exact": bool(re.search(r"\bexact\b", class_name, re.I)),
                "url": _absolute_url(anchor.group("href")),
            }
        )
    return rows


def parse_archive_rows(body, page_url, media_type):
    text = _decode_html(body)
    rows = []
    for match in _ARCHIVE_RE.finditer(text):
        block = match.group("body")
        fields = _span_fields(block)
        filename = (
            fields.get("filenameSerie")
            or fields.get("filenameFilm")
            or fields.get("smallFilenameSerie")
            or fields.get("smallFilenameFilm")
            or _filename_from_url(match.group("href"))
        )
        if not filename:
            continue
        label = fields.get("episodenum") or ""
        season, episode = _season_episode_from_label(label)
        release_bits = [filename]
        if fields.get("team"):
            release_bits.append(fields["team"])
        if label:
            release_bits.append(label)
        rows.append(
            {
                "media_type": media_type,
                "url": urllib.parse.urljoin(page_url, html.unescape(match.group("href"))),
                "filename": filename,
                "episode_label": label,
                "season": season,
                "episode": episode,
                "languages": _languages_from_archive(filename, block),
                "release_info": " ".join(part for part in release_bits if part).strip(),
                "updated": fields.get("update") or "",
            }
        )
    return rows


def derive_matches(video, item):
    video = video or {}
    matches = []
    if item.get("media_type") == "film":
        if _title_matches(video.get("title"), item.get("title")):
            matches.append("title")
        if video.get("year") and item.get("year") and int(video.get("year")) == int(item.get("year")):
            matches.append("year")
    else:
        if _title_matches(video.get("series"), item.get("title")):
            matches.append("series")
        try:
            if int(video.get("season")) == int(item.get("season")):
                matches.append("season")
            if item.get("archive_episode") is not None and int(video.get("episode")) == int(item.get("archive_episode")):
                matches.append("episode")
        except (TypeError, ValueError):
            pass
    release_text = _normalize_release(item.get("release_info"))
    for match_name, video_key in (
        ("release_group", "release_group"),
        ("source", "source"),
        ("resolution", "resolution"),
    ):
        value = _normalize_release(video.get(video_key))
        if value and value in release_text:
            matches.append(match_name)
    return matches


class SoustitreseuProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        video = dict(video or {})
        requested = _requested_languages(languages)
        if not requested:
            return []
        kind = video.get("kind")
        if kind == "episode":
            if not video.get("series") or video.get("season") is None or video.get("episode") is None:
                return []
            return self._search_episode(video, requested, dict(config or {}))
        if kind == "movie":
            if not video.get("title"):
                return []
            return self._search_movie(video, requested, dict(config or {}))
        return []

    def _search_episode(self, video, requested, config):
        title = str(video.get("series") or "").strip()
        _sleep(config)
        search_body = self._http_get(_search_url(title), referer=BASE_URL + "/")
        rows = _rank_search_rows(parse_search_results(search_body), title, "series")
        results = []
        seen = set()
        for row in rows[:3]:
            _sleep(config)
            detail_body = self._http_get(row["url"], referer=_search_url(title))
            for archive in parse_archive_rows(detail_body, row["url"], "series"):
                if not _archive_matches_episode(archive, video):
                    continue
                results.extend(self._results_for_archive(video, row, archive, requested, seen))
            if results:
                return sorted(results, key=lambda item: item["score"], reverse=True)
        return []

    def _search_movie(self, video, requested, config):
        title = str(video.get("title") or "").strip()
        _sleep(config)
        search_body = self._http_get(_search_url(title), referer=BASE_URL + "/")
        rows = _rank_search_rows(parse_search_results(search_body), title, "film", video.get("year"))
        results = []
        seen = set()
        for row in rows[:3]:
            _sleep(config)
            detail_body = self._http_get(row["url"], referer=_search_url(title))
            for archive in parse_archive_rows(detail_body, row["url"], "film"):
                results.extend(self._results_for_archive(video, row, archive, requested, seen))
            if results:
                return sorted(results, key=lambda item: item["score"], reverse=True)
        return []

    def _results_for_archive(self, video, page_row, archive, requested, seen):
        results = []
        for alpha3 in archive.get("languages") or []:
            if alpha3 not in requested:
                continue
            item = {
                **archive,
                "title": page_row["title"],
                "year": page_row.get("year"),
                "language": alpha3,
                "season": archive.get("season") if archive.get("season") is not None else video.get("season"),
                "episode": video.get("episode") if page_row["media_type"] == "series" else None,
                "archive_episode": archive.get("episode"),
            }
            key = (item["url"], alpha3)
            if key in seen:
                continue
            seen.add(key)
            results.append(self._result(video, item))
        return results

    def _result(self, video, item):
        alpha3 = item["language"]
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        matches = derive_matches(video, item)
        score = 35
        for match_name, value in (
            ("title", 25),
            ("series", 25),
            ("year", 10),
            ("season", 15),
            ("episode", 15),
            ("release_group", 6),
            ("source", 5),
            ("resolution", 4),
        ):
            if match_name in matches:
                score += value
        filename = item.get("filename") or f"soustitreseu.{_slug(item.get('title'))}.{alpha2}.zip"
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "media_type": item.get("media_type"),
            "url": item["url"],
            "filename": filename,
            "title": item.get("title"),
            "year": item.get("year"),
            "season": item.get("season"),
            "episode": item.get("episode"),
            "archive_episode": item.get("archive_episode"),
            "language": alpha3,
            "release_info": item.get("release_info") or filename,
        }
        return {
            "provider": PROVIDER_ID,
            "id": f"soustitreseu-{hashlib.sha1((item['url'] + alpha3).encode('utf-8')).hexdigest()[:16]}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": item.get("release_info") or filename,
            "filename": filename,
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["url"],
            "display": {
                "source": "sous-titres.eu",
                "title": item.get("title"),
                "release": item.get("release_info") or filename,
            },
            "provider_payload": payload,
        }

    def download(self, provider_payload, language, config):
        del config
        payload = dict(provider_payload or {})
        url = payload.get("url")
        if not url:
            raise ValueError("soustitreseu download requires url")
        body = self._http_get(url, timeout=30)
        alpha3 = _alpha3_for_language(language)
        return extract_download(body, payload.get("filename", ""), payload, alpha3)


def extract_download(body, filename="", payload=None, language=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload, language)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload, language)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    return _content_payload(body, _format_from_filename(filename))


def select_subtitle_file(names, payload, language=None):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("soustitreseu archive contains no supported subtitle files")
    try:
        season = int((payload or {}).get("season"))
        episode = int((payload or {}).get("episode"))
    except (TypeError, ValueError):
        season = episode = None
    release_info = _normalize_release((payload or {}).get("release_info"))

    def score(index_name):
        index, name = index_name
        normalized = _normalize_release(os.path.basename(name))
        value = max(0, 10 - index)
        file_language = _language_from_subtitle_filename(name)
        if language and file_language == language:
            value += 100
        elif language and file_language and file_language != language:
            value -= 100
        if season is not None and episode is not None and _file_matches_episode(normalized, season, episode):
            value += 70
        if release_info:
            for token in _release_tokens(release_info):
                if len(token) > 2 and token in normalized:
                    value += 3
        if name.lower().endswith(".srt"):
            value += 6
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
        raise RuntimeError(f"Soustitres.eu RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Soustitres.eu RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Soustitres.eu bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "soustitreseu.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Soustitres.eu RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "soustitreseu.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [unar, "-quiet", "-o", output_dir, archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract Soustitres.eu RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Soustitres.eu RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "soustitreseu.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [sevenzip, "x", "-y", f"-o{output_dir}", archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract Soustitres.eu RAR: {message}")
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
        raise ValueError("soustitreseu archive contains no supported subtitle files")
    return files


def _rank_search_rows(rows, title, media_type, year=None):
    filtered = [row for row in rows if row.get("media_type") == media_type and _row_matches_title(row, title)]
    if not filtered:
        filtered = [row for row in rows if row.get("media_type") == media_type]

    def score(row):
        value = 0
        if row.get("exact"):
            value += 50
        if _normalize(row.get("title")) == _normalize(title):
            value += 40
        elif _title_matches(title, row.get("title")):
            value += 20
        if year and row.get("year") and int(year) == int(row["year"]):
            value += 20
        return value

    return sorted(filtered, key=score, reverse=True)


def _row_matches_title(row, title):
    if _title_matches(title, row.get("title")):
        return True
    return any(_title_matches(title, alias) for alias in row.get("aliases") or [])


def _archive_matches_episode(archive, video):
    try:
        wanted_season = int(video.get("season"))
        wanted_episode = int(video.get("episode"))
    except (TypeError, ValueError):
        return False
    if archive.get("season") != wanted_season:
        return False
    archive_episode = archive.get("episode")
    return archive_episode is None or archive_episode == wanted_episode


def _span_fields(block):
    fields = {}
    for match in _SPAN_RE.finditer(block or ""):
        class_name = (match.group("class") or "").split()[0]
        fields[class_name] = _strip_tags(match.group("body"))
    return fields


def _title_from_heading(value):
    aliases = [_strip_tags(match.group("body")).lstrip("- ").strip() for match in _ANY_SPAN_RE.finditer(value or "")]
    primary_html = _ANY_SPAN_RE.sub("", value or "")
    primary = _strip_tags(primary_html)
    year = None
    year_match = _YEAR_RE.search(primary)
    if year_match:
        year = int(year_match.group("year"))
        primary = _YEAR_RE.sub("", primary).strip()
    return primary, year, [alias for alias in aliases if alias]


def _season_episode_from_label(label):
    label = html.unescape(label or "")
    match = _EPISODE_LABEL_RE.search(label)
    if match:
        return int(match.group("season")), int(match.group("episode"))
    match = _SEASON_LABEL_RE.search(label)
    if match:
        return int(match.group("season")), None
    return None, None


def _languages_from_archive(filename, block):
    normalized = "." + _normalize_release(filename) + "."
    languages = set()
    if "enfr" in normalized or ".en." in normalized or ".eng." in normalized:
        languages.add("eng")
    if "enfr" in normalized or ".fr." in normalized or ".fre." in normalized:
        languages.add("fra")
    for match in _IMG_LANG_RE.finditer(block or ""):
        language = ALPHA2_TO_ALPHA3.get((match.group("lang") or "").lower())
        if language:
            languages.add(language)
    return [language for language in LANGUAGE_ORDER if language in languages]


def _language_from_subtitle_filename(name):
    compact = "." + _normalize_release(name) + "."
    if ".vo." in compact or ".en." in compact or ".eng." in compact:
        return "eng"
    if ".vf." in compact or ".fr." in compact or ".fre." in compact:
        return "fra"
    return None


def _file_matches_episode(normalized_name, season, episode):
    compact = normalized_name.lower()
    if f"s{season:02d}e{episode:02d}" in compact:
        return True
    if f"{season}x{episode:02d}" in compact or f"{season}x{episode}" in compact:
        return True
    episode_code = f"{season}{episode:02d}"
    return bool(re.search(rf"(?<!\d){re.escape(episode_code)}(?!\d)", compact))


def _release_tokens(value):
    return [token for token in _normalize_release(value).split(".") if token]


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
    return f"{SEARCH_URL}?{urllib.parse.urlencode({'q': title})}"


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", html.unescape(value or ""))


def _filename_from_url(url):
    return os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url or "").path))


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


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


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
    for candidate in ("utf-8", "cp1252", "latin-1"):
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
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _slug(value):
    return "-".join(_tokens(value)) or "release"


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
