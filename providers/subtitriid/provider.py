"""Subtitri.id provider for the Bazarr+ Provider Hub catalog."""

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
import urllib.parse
import urllib.request
import zipfile

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in manifest
    py7zz = None

PROVIDER_ID = "subtitriid"
BASE_URL = "https://subtitri.do.am"
HTTP_TIMEOUT_SECONDS = 15
SUPPORTED_LANGUAGE = {"alpha3": "lav", "alpha2": "lv"}
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ssa", ".ass", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_ATTR_RE = re.compile(r"""(?P<name>[-:\w]+)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", re.S)
_DOWNLOAD_LINK_RE = re.compile(
    r"<a\b(?P<attrs>[^>]class\s*=\s*['\"][^'\"]*\bhvr\b[^'\"]*['\"][^>]*)>",
    re.I | re.S,
)
_EBLOCK_RE = re.compile(
    r"<table\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\beBlock\b)[^>]*>(?P<body>.*?)</table>",
    re.I | re.S,
)
_ENTRY_ID_RE = re.compile(r"-(?P<id>\d+)(?:[/?#].*)?$")
_IMDB_RE = re.compile(r"imdb\.com/title/(?P<id>tt\d+)/?", re.I)
_INT_RE = re.compile(r"\d+")
_MAIN_HEADER_RE = re.compile(
    r"<h1\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\bmain-header\b)[^>]*>(?P<body>.*?)</h1>",
    re.I | re.S,
)
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SEARCH_TITLE_RE = re.compile(
    r"<div\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\beTitle\b)[^>]*>.*?"
    r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title>(?P<body>.*?)</title>", re.I | re.S)
_WS_RE = re.compile(r"\s+")
_YEAR_FIELD_RE = re.compile(r"<[^>]+\bid\s*=\s*['\"]film-page-year['\"][^>]*>(?P<body>.*?)</[^>]+>", re.I | re.S)


def build_queries(video):
    video = video or {}
    if video.get("kind") != "movie":
        return []
    queries = []
    for value in [video.get("title"), *list(video.get("alternative_titles") or [])]:
        text = (_coerce_text(value) or "").strip()
        if text and text not in queries:
            queries.append(text)
    return queries


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    seen = set()
    for block in _EBLOCK_RE.finditer(text):
        match = _SEARCH_TITLE_RE.search(block.group("body"))
        if not match:
            continue
        href = _attr(match.group("attrs"), "href")
        page_url = _absolute_url(href)
        entry_id = _entry_id_from_url(page_url)
        if not page_url or page_url in seen:
            continue
        seen.add(page_url)
        rows.append(
            {
                "entry_id": entry_id,
                "title": _strip_tags(match.group("body")),
                "page_url": page_url,
            }
        )
    return rows


def parse_detail_page(body, page_url=""):
    text = _decode_html(body)
    if not text:
        return None
    title, local_title = _parse_titles(text)
    download_url = _download_url(text)
    if not title or not download_url:
        return None
    entry_id = _entry_id_from_url(page_url) or _entry_id_from_url(download_url)
    return {
        "entry_id": entry_id,
        "title": title,
        "local_title": local_title,
        "year": _year(text),
        "imdb_id": _imdb_id(text),
        "download_count": _download_count(text),
        "download_url": download_url,
        "page_url": page_url,
    }


def derive_matches(video, row):
    video = video or {}
    row = row or {}
    matches = []
    if video.get("kind") != "movie":
        return matches
    candidate_titles = [row.get("title"), row.get("local_title")]
    wanted_titles = [video.get("title"), *list(video.get("alternative_titles") or [])]
    if any(_title_matches(wanted, candidate) for wanted in wanted_titles for candidate in candidate_titles):
        matches.append("title")
    wanted_year = _safe_int(video.get("year"))
    if wanted_year is not None and wanted_year == row.get("year"):
        matches.append("year")
    wanted_imdb = _clean_imdb(video.get("imdb_id"))
    if wanted_imdb and wanted_imdb == row.get("imdb_id"):
        matches.append("imdb_id")
    return matches


class SubtitriIdProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.7,en;q=0.6",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "movie":
            return []
        variants = _requested_variants(languages)
        if not variants:
            return []
        results = []
        seen = set()
        for query in build_queries(video):
            _sleep(config)
            search_url = _search_url(query)
            for candidate in parse_search_results(self._http_get(search_url)):
                _sleep(config)
                detail = parse_detail_page(
                    self._http_get(candidate["page_url"], referer=search_url),
                    candidate["page_url"],
                )
                if not detail:
                    continue
                if not detail.get("title") and candidate.get("title"):
                    detail["title"] = candidate["title"]
                matches = derive_matches(video, detail)
                if not _row_matches_video(video, detail, matches):
                    continue
                for language in variants:
                    key = (detail["entry_id"], language["hi"], language["forced"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, detail, language, matches))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, row, language, matches):
        score = 45
        score += 20 if "title" in matches else 0
        score += 15 if "year" in matches else 0
        score += 20 if "imdb_id" in matches else 0
        score = min(score, 100)
        release_info = _release_info(row)
        filename = f"subtitriid.{_slug(row.get('title'))}.{row.get('year') or 'movie'}.{_variant_suffix(language)}.zip"
        return {
            "provider": PROVIDER_ID,
            "id": f"subtitriid-{row['entry_id']}-{_variant_suffix(language)}",
            "language": dict(language),
            "release_info": release_info,
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": bool(language.get("hi")),
            "page_link": row["page_url"],
            "display": {
                "source": "subtitri.do.am",
                "title": row.get("title"),
                "local_title": row.get("local_title"),
                "downloads": row.get("download_count"),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "entry_id": row["entry_id"],
                "url": row["download_url"],
                "page_url": row["page_url"],
                "filename": filename,
                "language": "lav",
                "hi": bool(language.get("hi")),
                "forced": bool(language.get("forced")),
            },
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("url")
        if not url:
            raise ValueError("subtitriid download requires url")
        body = self._http_get(url, timeout=30, referer=payload.get("page_url") or BASE_URL)
        return extract_download(body, payload)


def extract_download(body, payload=None):
    payload = dict(payload or {})
    filename = payload.get("filename") or ""
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _content in files])
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist())
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    subtitle_format = _subtitle_extension(filename) or ("srt" if _looks_like_subtitle(body) else "")
    if not subtitle_format:
        raise ValueError("subtitriid download did not return a supported subtitle file")
    return _content_payload(body, subtitle_format)


def select_subtitle_file(names):
    candidates = [name for name in names if _subtitle_extension(name) and not os.path.basename(name).startswith(".")]
    if not candidates:
        raise ValueError("subtitriid archive contains no supported subtitle files")
    return candidates[0]


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
        raise RuntimeError(f"Subtitri.id RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Subtitri.id RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Subtitri.id bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subtitriid.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    return _extract_rar_files_with_command(body, "unar", ["unar", "-quiet", "-o"])


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Subtitri.id RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subtitriid.rar")
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
            raise RuntimeError(f"7z failed to extract Subtitri.id RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_command(body, command, args):
    executable = shutil.which(command)
    if not executable:
        raise RuntimeError(f"Subtitri.id RAR fallback requires {command}")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subtitriid.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [*args, output_dir, archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"{command} failed to extract Subtitri.id RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            relative_path = os.path.relpath(path, output_dir)
            if not _subtitle_extension(relative_path):
                continue
            with open(path, "rb") as handle:
                files.append((relative_path, handle.read()))
    if not files:
        raise ValueError("subtitriid archive contains no supported subtitle files")
    return files


def _parse_titles(text):
    header_match = _MAIN_HEADER_RE.search(text)
    if header_match:
        title_text = _strip_tags(header_match.group("body"))
    else:
        title_match = _TITLE_RE.search(text)
        title_text = _strip_tags(title_match.group("body")) if title_match else ""
        title_text = title_text.split(" - ", 1)[0]
    parts = [part.strip() for part in title_text.split("/") if part.strip()]
    if len(parts) >= 2:
        return parts[-1], parts[0]
    if parts:
        return parts[0], ""
    return "", ""


def _year(text):
    match = _YEAR_FIELD_RE.search(text)
    source = match.group("body") if match else text
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", _strip_tags(source))
    return int(year_match.group(1)) if year_match else None


def _imdb_id(text):
    match = _IMDB_RE.search(text or "")
    return match.group("id").lower() if match else ""


def _download_url(text):
    for match in _DOWNLOAD_LINK_RE.finditer(text or ""):
        href = _attr(match.group("attrs"), "href")
        if href:
            return _absolute_url(href)
    return ""


def _download_count(text):
    match = re.search(
        r"<span\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\be-loads\b)[^>]*>.*?"
        r"<span\b(?=[^>]*\bclass\s*=\s*['\"][^'\"]*\bed-value\b)[^>]*>(?P<body>.*?)</span>",
        text or "",
        re.I | re.S,
    )
    if not match:
        return None
    return _int_from_text(_strip_tags(match.group("body")))


def _requested_variants(languages):
    variants = []
    seen = set()
    for language in languages or []:
        if _alpha3_for_language(language) != "lav":
            continue
        variant = {
            "alpha3": "lav",
            "alpha2": "lv",
            "hi": bool((language or {}).get("hi")),
            "forced": bool((language or {}).get("forced")),
        }
        key = (variant["hi"], variant["forced"])
        if key not in seen:
            seen.add(key)
            variants.append(variant)
    return variants


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    alpha2 = (language.get("alpha2") or "").lower()
    if alpha3 in {"lav", "lva"} or alpha2 == "lv":
        return "lav"
    return alpha3 or None


def _search_url(query):
    return f"{BASE_URL}/search/?{urllib.parse.urlencode({'q': query})}"


def _row_matches_video(video, row, matches):
    wanted_year = _safe_int((video or {}).get("year"))
    if wanted_year is not None and row.get("year") is not None and wanted_year != row.get("year"):
        return False
    if _clean_imdb((video or {}).get("imdb_id")) and row.get("imdb_id"):
        return "imdb_id" in matches
    return "title" in matches


def _release_info(row):
    title = row.get("title") or ""
    local_title = row.get("local_title") or ""
    title_part = f"{local_title} / {title}" if local_title and local_title != title else title
    year = row.get("year")
    return f"{title_part} ({year})" if year else title_part


def _variant_suffix(language):
    parts = ["lv"]
    if language.get("hi"):
        parts.append("hi")
    if language.get("forced"):
        parts.append("forced")
    return "-".join(parts)


def _content_payload(content, subtitle_format, empty=False):
    content = content or b""
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": "text/plain",
        "format": subtitle_format or "srt",
        "encoding": _guess_encoding(content),
        "empty": bool(empty),
    }


def _guess_encoding(content):
    try:
        content.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1257"


def _format_from_filename(filename):
    return _subtitle_extension(filename) or "srt"


def _subtitle_extension(filename):
    extension = os.path.splitext(urllib.parse.urlparse(filename or "").path)[1].lower()
    return extension[1:] if extension in SUBTITLE_EXTENSIONS else ""


def _looks_like_subtitle(body):
    sample = (body or b"")[:4096].decode("utf-8", errors="ignore").lower()
    return "-->" in sample or "[script info]" in sample or "{\\an" in sample


def _is_rar_archive(body):
    return (body or b"").startswith(b"Rar!\x1a\x07\x00") or (body or b"").startswith(b"Rar!\x1a\x07\x01\x00")


def _decode_html(body):
    if isinstance(body, str):
        return body
    return (body or b"").decode("utf-8", errors="replace")


def _attr(attrs, name):
    wanted = name.lower()
    for match in _ATTR_RE.finditer(attrs or ""):
        if match.group("name").lower() == wanted:
            return html.unescape(match.group("value").strip())
    return ""


def _absolute_url(href):
    return urllib.parse.urljoin(BASE_URL + "/", html.unescape(href or ""))


def _entry_id_from_url(url):
    parsed = urllib.parse.urlparse(url or "")
    download_match = re.search(r"/load/0-0-0-(?P<id>\d+)-\d+(?:[/?#].*)?$", parsed.path)
    if download_match:
        return download_match.group("id")
    match = _ENTRY_ID_RE.search(parsed.path)
    if match:
        return match.group("id")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 5 and parts[-5:-1] == ["load", "0", "0", "0"]:
        return parts[-1]
    return ""


def _strip_tags(value):
    text = _TAG_RE.sub("", html.unescape(value or ""))
    return _WS_RE.sub(" ", text).strip()


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _title_matches(wanted, candidate):
    return bool(wanted and candidate and _normalize(wanted) == _normalize(candidate))


def _normalize(value):
    return _NON_ALNUM_RE.sub(" ", _coerce_text(value).lower() if _coerce_text(value) else "").strip()


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int_from_text(value):
    match = _INT_RE.search(value or "")
    return int(match.group(0)) if match else None


def _clean_imdb(value):
    value = str(value or "").strip().lower().rstrip("/")
    if not value:
        return ""
    match = re.search(r"(tt\d+)", value)
    return match.group(1) if match else value


def _slug(value):
    value = _normalize(value) or "subtitle"
    return re.sub(r"\s+", "-", value).strip("-")[:80] or "subtitle"


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)
