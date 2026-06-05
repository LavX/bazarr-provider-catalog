"""Nekur provider for the Bazarr+ Provider Hub catalog."""

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
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in manifest
    py7zz = None

PROVIDER_ID = "nekur"
BASE_URL = "https://subtitri.nekur.net"
SEARCH_URL = f"{BASE_URL}/modules/Subtitles.php"
HTTP_TIMEOUT_SECONDS = 10
SUPPORTED_LANGUAGES = {"lav": "lv"}
LANGUAGE_ALIASES = {"lv": "lav", "lva": "lav", "lva-lv": "lav"}
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ssa", ".ass", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(?P<body>.*?)</t[dh]>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>", re.I | re.S)
_IMDB_RE = re.compile(r"imdb\.com/title/(?P<imdb>tt\d+)/?", re.I)
_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    for row_match in _ROW_RE.finditer(text):
        row_html = row_match.group("body")
        if "filmu-subtitri/download/" not in row_html:
            continue
        cells = [match.group("body") for match in _CELL_RE.finditer(row_html)]
        if len(cells) < 6:
            continue
        title_anchor = _ANCHOR_RE.search(cells[0])
        if not title_anchor:
            continue
        href = html.unescape(title_anchor.group("href"))
        title_text = _strip_tags(title_anchor.group("body"))
        year = _year_from_text(title_text)
        title = _YEAR_RE.sub("", title_text).strip()
        imdb = _imdb_from_cell(cells[3])
        if not title or not imdb:
            continue
        rows.append(
            {
                "title": title,
                "year": year,
                "download_url": _absolute_url(href),
                "subtitle_id": _subtitle_id_from_url(href),
                "imdb_id": imdb,
                "fps": _strip_tags(cells[1]),
                "notes": _strip_tags(cells[-1]),
            }
        )
    return rows


def derive_matches(video, item, searched_title=None):
    matches = []
    title_candidates = _search_titles(video or {})
    searched_title = str(searched_title or "").strip()
    if searched_title and searched_title not in title_candidates:
        title_candidates.append(searched_title)
    if any(_title_matches(title, item.get("title")) for title in title_candidates):
        matches.append("title")
    try:
        if (video or {}).get("year") and int(video.get("year")) == int(item.get("year")):
            matches.append("year")
    except (TypeError, ValueError):
        pass
    if _normalize_imdb((video or {}).get("imdb_id")) == _normalize_imdb(item.get("imdb_id")):
        matches.append("imdb_id")
    source = _normalize_release((video or {}).get("source"))
    notes = _normalize_release(item.get("notes"))
    if source and source in notes:
        matches.append("source")
    release_group = _normalize_release((video or {}).get("release_group"))
    if release_group and release_group in notes:
        matches.append("release_group")
    return matches


class NekurProvider:
    def _http_post_search(self, title, timeout=HTTP_TIMEOUT_SECONDS):
        data = urllib.parse.urlencode({"ajax": "1", "sSearch": title}).encode("utf-8")
        request = urllib.request.Request(
            SEARCH_URL,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "lv-LV,lv;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            if error.code >= 500 and body:
                return body
            raise

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": f"{BASE_URL}/",
                "Accept": "*/*",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), _headers_to_dict(response.headers)

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "movie":
            return []
        if not _requests_latvian(languages):
            return []
        titles = _search_titles(video)
        if not titles:
            return []

        config = dict(config or {})
        results = []
        seen = set()
        for title in titles:
            _sleep(config)
            rows = parse_search_results(self._http_post_search(title))
            for row in rows:
                matches = derive_matches(video, row, searched_title=title)
                if not _has_required_match(video, matches):
                    continue
                key = row["download_url"]
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(row, matches))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, item, matches):
        score = 45
        score += 30 if "imdb_id" in matches else 0
        score += 15 if "title" in matches else 0
        score += 10 if "year" in matches else 0
        score += 5 if "source" in matches else 0
        release_info = item.get("notes") or f"{item.get('title')} {item.get('year') or ''}".strip()
        filename = f"nekur.{_slug(item.get('title'))}.{item.get('year') or 'unknown'}.lv.zip"
        return {
            "provider": PROVIDER_ID,
            "id": f"nekur-{item['subtitle_id']}",
            "language": {
                "alpha3": "lav",
                "alpha2": "lv",
                "hi": False,
                "forced": False,
            },
            "release_info": release_info,
            "filename": filename,
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["download_url"],
            "display": {
                "source": "subtitri.nekur.net",
                "title": item.get("title"),
                "release": release_info,
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "download_url": item["download_url"],
                "subtitle_id": item["subtitle_id"],
                "filename": filename,
                "title": item.get("title"),
                "year": item.get("year"),
                "imdb_id": item.get("imdb_id"),
                "fps": item.get("fps"),
                "notes": item.get("notes"),
            },
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("download_url")
        if not url:
            raise ValueError("nekur download requires download_url")
        body, headers = self._http_get(url)
        # The synthetic ".zip" filename only describes archive responses. When the
        # endpoint serves a direct subtitle, prefer the real Content-Disposition
        # name so its true extension survives into extract_download().
        filename = _filename_from_headers(headers) or payload.get("filename", "")
        return extract_download(body, filename, payload)


def extract_download(body, filename="", payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_files([name for name, _data in files], payload)
        file_map = dict(files)
        content = b"\n\n".join(file_map[name] for name in selected)
        return _content_payload(content, _subtitle_extension(selected[0]) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_files(archive.namelist(), payload)
            content = b"\n\n".join(archive.read(name) for name in selected)
            return _content_payload(content, _subtitle_extension(selected[0]) or "srt")
    if _looks_like_html(body):
        raise ValueError("nekur download did not return a supported subtitle file")
    # Direct (non-archive) subtitle: trust the real filename extension, then fall
    # back to sniffing the body so a valid .srt/.sub is not rejected just because
    # the synthetic ".zip" filename carried no usable extension.
    subtitle_format = _subtitle_extension(filename or "") or _format_from_content(body)
    if not subtitle_format:
        raise ValueError("nekur download did not return a supported subtitle file")
    return _content_payload(body, subtitle_format)


def select_subtitle_file(names, payload):
    return select_subtitle_files(names, payload)[0]


def select_subtitle_files(names, payload):
    candidates = [
        name
        for name in names
        if _subtitle_extension(name) and not _is_sidecar(name)
    ]
    if not candidates:
        raise ValueError("nekur archive contains no supported subtitle files")

    best_single = max(
        enumerate(candidates),
        key=lambda index_name: _subtitle_file_score(index_name, payload),
    )[1]
    best_single_score = _subtitle_file_score((0, best_single), payload)

    multipart = _multipart_subset(candidates, payload)
    if multipart:
        multipart_score = _group_score(multipart, payload)
        # Only prefer the multipart set when it scores at least as well as the
        # best single file. Otherwise a low-scoring CD1/CD2 pair would shadow a
        # better matching single subtitle.
        if multipart_score >= best_single_score:
            return multipart

    return [best_single]


def _is_sidecar(name):
    parts = name.replace("\\", "/").split("/")
    if any(part == "__MACOSX" for part in parts):
        return True
    return os.path.basename(name).startswith("._")


def _group_score(names, payload):
    return max(_subtitle_file_score((0, name), payload) for name in names)


def _subtitle_file_score(index_name, payload):
    index, name = index_name
    del index
    title_tokens = _tokens((payload or {}).get("title"))
    year = str((payload or {}).get("year") or "")
    note_tokens = _tokens((payload or {}).get("notes"))
    wants_forced = bool((payload or {}).get("forced"))
    normalized = _normalize(os.path.basename(name))
    tokens = set(normalized.split())
    value = 0
    if title_tokens and all(token in tokens for token in title_tokens):
        value += 80
    if year and year in normalized:
        value += 50
    for token in note_tokens:
        if token in tokens:
            value += 5
    if not wants_forced and "forced" in tokens:
        value -= 25
    return value


def _multipart_subset(names, payload):
    groups = {}
    for name in names:
        part_index = _part_index(name)
        if part_index <= 0:
            continue
        groups.setdefault((_multipart_key(name), _subtitle_extension(name)), []).append(name)
    valid_groups = []
    for group in groups.values():
        part_numbers = [_part_index(name) for name in group]
        if len(group) > 1 and len(set(part_numbers)) == len(part_numbers):
            valid_groups.append(group)
    if not valid_groups:
        return []
    best_group = max(
        valid_groups,
        key=lambda group: (
            sum(_subtitle_file_score((index, name), payload) for index, name in enumerate(group)),
            len(group),
            -min(_part_index(name) for name in group),
        ),
    )
    return sorted(best_group, key=lambda name: (_part_index(name), name.lower()))


def _part_index(name):
    normalized = _normalize(os.path.basename(name))
    match = re.search(r"\b(?:cd|part|disc|disk)\s*0*(\d+)\b", normalized)
    return int(match.group(1)) if match else 0


def _multipart_key(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    normalized = _normalize(stem)
    return re.sub(r"\b(?:cd|part|disc|disk)\s*0*\d+\b", "", normalized).strip()


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
        raise RuntimeError(f"Nekur RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Nekur RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Nekur bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "nekur.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Nekur RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "nekur.rar")
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
            raise RuntimeError(f"unar failed to extract Nekur RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Nekur RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "nekur.rar")
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
            raise RuntimeError(f"7z failed to extract Nekur RAR: {message}")
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
        raise ValueError("nekur archive contains no supported subtitle files")
    return files


def _requests_latvian(languages):
    for language in languages or []:
        if _alpha3_for_language(language) == "lav":
            return True
    return False


def _search_titles(video):
    titles = []
    for value in [video.get("title")] + list(video.get("alternative_titles") or []):
        value = str(value or "").strip()
        if value and value not in titles:
            titles.append(value)
    return titles


def _has_required_match(video, matches):
    if "imdb_id" in matches:
        return True
    if "title" in matches and ("year" in matches or not (video or {}).get("year")):
        return True
    return False


def _title_matches(wanted, candidate):
    wanted_tokens = _tokens(wanted)
    candidate_tokens = set(_tokens(candidate))
    return bool(wanted_tokens) and all(token in candidate_tokens for token in wanted_tokens)


def _year_from_text(text):
    match = _YEAR_RE.search(text or "")
    if not match:
        return None
    try:
        return int(match.group("year"))
    except (TypeError, ValueError):
        return None


def _imdb_from_cell(cell):
    match = _IMDB_RE.search(cell or "")
    return match.group("imdb") if match else ""


def _subtitle_id_from_url(url):
    path = urllib.parse.urlparse(url or "").path
    return os.path.basename(path.rstrip("/"))


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", value)


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


def _headers_to_dict(headers):
    return {str(key).lower(): str(value) for key, value in dict(headers or {}).items()}


def _filename_from_headers(headers):
    disposition = (headers or {}).get("content-disposition", "")
    match = re.search(r'filename\*?=(?:[^\'";]+\'\')?"?([^";]+)"?', disposition)
    if not match:
        return ""
    return urllib.parse.unquote(match.group(1)).strip()


def _format_from_content(body):
    sample = (body or b"").lstrip()[:512]
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:].lstrip()
    if sample.upper().startswith(b"WEBVTT"):
        return "vtt"
    lowered = sample.lower()
    if b"[script info]" in lowered or b"[v4+ styles]" in lowered or b"[v4 styles]" in lowered:
        return "ass"
    # SubRip cue: a numeric index line followed by a "hh:mm:ss,mmm --> ..." timecode.
    if re.search(rb"(?m)^\s*\d+\s*\r?\n\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->", sample):
        return "srt"
    # MicroDVD .sub frames such as "{0}{25}text".
    if re.match(rb"\s*\{\d+\}\{\d+\}", sample):
        return "sub"
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


def _looks_like_html(body):
    sample = (body or b"").lstrip()[:512].lower()
    return sample.startswith((b"<!doctype html", b"<html")) or b"<title" in sample


def _alpha3_for_language(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        if alpha3 in LANGUAGE_ALIASES:
            return LANGUAGE_ALIASES[alpha3]
        if alpha3:
            return alpha3
        alpha2 = (language.get("alpha2") or "").lower()
        return LANGUAGE_ALIASES.get(alpha2, alpha2)
    value = str(language or "").lower()
    return LANGUAGE_ALIASES.get(value, value)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(int(delay_ms), 5000) / 1000.0)


def _strip_tags(value):
    stripped = _TAG_RE.sub("", value or "")
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()


def _decode_html(body):
    if isinstance(body, str):
        return body
    raw = body or b""
    for encoding in ("utf-8", "cp1257", "latin-1"):
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


def _normalize_imdb(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return value if value.startswith("tt") else f"tt{value}"


def _normalize_release(value):
    return _normalize(value).replace(" ", "")
