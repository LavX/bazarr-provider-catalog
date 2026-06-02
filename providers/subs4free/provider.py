"""Subs4Free provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import random
import re
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

PROVIDER_ID = "subs4free"
BASE_URL = "https://www.subs4free.info"
SEARCH_URL = f"{BASE_URL}/search_report.php"
DOWNLOAD_URL = f"{BASE_URL}/getSub.php"
ANTI_BLOCK_URLS = (
    "https://images.subs4free.info/favicon.ico",
    "https://www.subs4series.com/includes/anti-block-layover.php?launch=1",
    "https://www.subs4series.com/includes/anti-block.php",
)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 15
MAX_CANDIDATES_PER_QUERY = 20
SUPPORTED_LANGUAGES = {
    "ell": "el",
    "eng": "en",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

_ROW_RE = re.compile(
    r"<div\s+class=[\"'][^\"']*\bmovie-details\b[^\"']*[\"'][^>]*>(?P<body>.*?)(?=<div\s+class=[\"'][^\"']*\bmovie-details\b|\Z)",
    re.I | re.S,
)
_HEADING_RE = re.compile(
    r"<a\b(?=[^>]*\bclass=[\"'][^\"']*\bmovie-heading\b)[^>]*>",
    re.I | re.S,
)
_SPAN_RE = re.compile(r"<span\b[^>]*>(?P<text>.*?)</span>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_OPTION_RE = re.compile(
    r"<option\b[^>]*value=[\"'](?P<value>[^\"']+)[\"'][^>]*>(?P<title>.*?)</option>",
    re.I | re.S,
)
_UPLOADER_RE = re.compile(r"Uploaded\s+by\s*<a\b[^>]*>(?P<name>.*?)</a>", re.I | re.S)
_DOWNLOADS_RE = re.compile(r"<b>\s*(?P<count>\d+)\s*</b>\s*DLs", re.I)
_ID_INPUT_RE = re.compile(
    r"<input\b(?=[^>]*\bname=[\"']id[\"'])(?=[^>]*\bvalue=[\"'](?P<id>[^\"']+)[\"'])[^>]*>",
    re.I | re.S,
)
_IMAGE_INPUT_RE = re.compile(
    r"<input\b(?=[^>]*\btype=[\"']image[\"'])[^>]*>",
    re.I | re.S,
)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def build_queries(video):
    video = video or {}
    if video.get("kind") != "movie":
        return []
    title = (_coerce_text(video.get("title")) or "").strip()
    if not title:
        return []
    year = video.get("year")
    if year:
        return [f"{title} {year}", title]
    return [title]


def parse_search_results(body):
    text = _decode(body)
    rows = []
    seen = set()
    for match in _ROW_RE.finditer(text):
        row = match.group("body")
        heading = _HEADING_RE.search(row)
        if not heading:
            continue
        tag = heading.group(0)
        href = _attr(tag, "href")
        if not href:
            continue
        release_info = _release_from_row(row, tag)
        if not release_info:
            continue
        language = _language_from_row(row, tag, href)
        if not language:
            continue
        detail_url = _absolute_url(href)
        key = (detail_url, language)
        if key in seen:
            continue
        seen.add(key)
        alpha2 = SUPPORTED_LANGUAGES[language]
        rows.append(
            {
                "id": _subtitle_id_from_url(detail_url),
                "detail_url": detail_url,
                "language": language,
                "alpha2": alpha2,
                "release_info": release_info,
                "title": _movie_title_from_release(release_info),
                "year": _release_year_from_text(release_info),
                "years": _years_from_text(release_info),
                "uploader": _uploader_from_row(row),
                "downloads": _downloads_from_row(row),
            }
        )
    return rows


def parse_suggestions(body):
    rows = []
    for match in _OPTION_RE.finditer(_decode(body)):
        row = _suggestion_from_option(match)
        if row:
            rows.append(row)
    return rows


def parse_download_form(body):
    text = _decode(body)
    id_match = _ID_INPUT_RE.search(text)
    if not id_match:
        raise ValueError("subs4free download form id was not found")
    image_match = _IMAGE_INPUT_RE.search(text)
    image_tag = image_match.group(0) if image_match else ""
    return {
        "id": html.unescape(id_match.group("id")),
        "width": _dimension(_attr(image_tag, "width")),
        "height": _dimension(_attr(image_tag, "height")),
    }


def extract_download(body, payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", _format_from_filename(payload.get("filename")), empty=True)
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    return _content_payload(body, _format_from_filename(payload.get("filename")))


def select_subtitle_file(names, payload=None):
    payload = payload or {}
    candidates = [name for name in names if _subtitle_extension(name) and not _is_hidden_path(name)]
    if not candidates:
        raise ValueError("subs4free archive contains no supported subtitle files")
    release = _normalize(payload.get("release_info") or payload.get("filename") or "")
    if not release:
        return candidates[0]

    def score(name):
        normalized = _normalize(os.path.basename(name))
        score_value = 0
        for token in release.split():
            if token in normalized:
                score_value += 1
        return score_value

    return max(candidates, key=score)


def derive_matches(video, item):
    video = video or {}
    release = item.get("release_info") if isinstance(item, dict) else item
    release_norm = _normalize(release)
    release_tokens = set(release_norm.split())
    matches = []
    title_tokens = _tokens(video.get("title"))
    if title_tokens and all(token in release_tokens for token in title_tokens):
        matches.append("title")
    year = video.get("year")
    if year and str(year) in release_tokens:
        matches.append("year")
    if _coerce_text(video.get("resolution")) and _normalize(video.get("resolution")) in release_tokens:
        matches.append("resolution")
    if _matches_token_table(video.get("source"), _SOURCE_TOKENS, release_tokens):
        matches.append("source")
    if _matches_token_table(video.get("video_codec"), _VIDEO_CODEC_TOKENS, release_tokens):
        matches.append("video_codec")
    if _matches_token_table(video.get("audio_codec"), _AUDIO_CODEC_TOKENS, release_tokens):
        matches.append("audio_codec")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _normalize_release_group(release_group) in _normalize_release_group(release):
        matches.append("release_group")
    return matches


def compute_score(video, item):
    matches = set(derive_matches(video, item))
    if {"title", "year"}.issubset(matches):
        return 100
    if "title" in matches:
        return 90
    return 60


class Subs4FreeProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "movie":
            return []
        requested = {_alpha3_for_language(language) for language in languages or []}
        requested = {language for language in requested if language in SUPPORTED_LANGUAGES}
        if not requested:
            return []
        results = []
        seen = set()
        covered_languages = set()
        for query in build_queries(video):
            _sleep(config)
            search_url = _search_url(query)
            body = self._http_get(search_url, referer=BASE_URL)
            candidates = parse_search_results(body)
            candidates.extend(self._candidates_from_suggestions(video, body, config, search_url))
            accepted = _accepted_candidates(video, candidates, requested, seen)
            covered_languages.update(item["language"] for item in accepted)
            results.extend(self._result(video, item) for item in accepted)
            if requested.issubset(covered_languages):
                break
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        detail_url = payload.get("detail_url")
        if not detail_url:
            raise ValueError("subs4free download requires detail_url")
        _sleep(config)
        page = self._http_get(detail_url, referer=BASE_URL)
        form = parse_download_form(page)
        self._apply_anti_block(detail_url, config)
        post_data = {
            "id": form["id"],
            "x": str(random.randint(0, max(form["width"], 1))),
            "y": str(random.randint(0, max(form["height"], 1))),
        }
        _sleep(config)
        body = self._http_post(DOWNLOAD_URL, post_data, referer=detail_url)
        return extract_download(body, payload)

    def _candidates_from_suggestions(self, video, body, config, referer):
        candidates = []
        for suggestion in parse_suggestions(body):
            if not _suggestion_matches(video, suggestion):
                continue
            _sleep(config)
            candidates.extend(parse_search_results(self._http_get(suggestion["url"], referer=referer)))
        return candidates

    def _result(self, video, item):
        alpha3 = item["language"]
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        matches = derive_matches(video, item)
        score = compute_score(video, item)
        filename = f"subs4free.{_slug(item['release_info'])}.{alpha2}.zip"
        return {
            "provider": PROVIDER_ID,
            "id": f"subs4free-{item['id']}-{alpha3}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": item["release_info"],
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["detail_url"],
            "display": {
                "source": "subs4free",
                "release": item["release_info"],
                "uploader": item.get("uploader"),
                "downloads": item.get("downloads"),
                "detail_url": item["detail_url"],
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "detail_url": item["detail_url"],
                "filename": filename,
                "release_info": item["release_info"],
                "language": alpha3,
            },
        }

    def _apply_anti_block(self, detail_url, config):
        for url in ANTI_BLOCK_URLS:
            _sleep(config)
            try:
                self._http_get(url, referer=detail_url)
            except Exception:
                continue

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


def _search_url(query):
    return f"{SEARCH_URL}?{urllib.parse.urlencode({'search': query, 'searchType': '1'})}"


def _headers(referer=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _release_from_row(row, heading_tag):
    span = _SPAN_RE.search(row)
    if span:
        release = _strip_tags(span.group("text"))
        if release:
            return release
    title = _attr(heading_tag, "title")
    title = re.sub(r"^\s*(?:Greek|English)\s+subtitles\s+for\s+", "", title or "", flags=re.I)
    return _clean_text(title)


def _language_from_row(row, heading_tag, href):
    lowered = f"{row} {heading_tag} {href}".lower()
    if "elgif" in lowered or "/greek-subtitles/" in lowered or "greek subtitles" in lowered:
        return "ell"
    if "engif" in lowered or "/english-subtitles/" in lowered or "english subtitles" in lowered:
        return "eng"
    return None


def _uploader_from_row(row):
    match = _UPLOADER_RE.search(row)
    return _strip_tags(match.group("name")) if match else None


def _downloads_from_row(row):
    match = _DOWNLOADS_RE.search(row)
    return int(match.group("count")) if match else None


def _movie_title_from_release(release):
    title = _YEAR_RE.split(release or "", maxsplit=1)[0]
    return _clean_text(title)


def _year_from_text(text):
    match = _YEAR_RE.search(text or "")
    return int(match.group(1)) if match else None


def _years_from_text(text):
    return [int(match.group(1)) for match in _YEAR_RE.finditer(text or "")]


def _release_year_from_text(text):
    years = _years_from_text(text)
    return years[-1] if years else None


def _suggestion_matches(video, suggestion):
    suggestion_tokens = set(_tokens(suggestion.get("title")))
    title_tokens = _tokens((video or {}).get("title"))
    if title_tokens and not all(token in suggestion_tokens for token in title_tokens):
        return False
    year = (video or {}).get("year")
    return not year or str(year) in suggestion_tokens


def _suggestion_from_option(match):
    value = html.unescape(match.group("value"))
    title = _strip_tags(match.group("title"))
    if not value or not title:
        return None
    return {"title": title, "url": _absolute_url(value)}


def _accepted_candidates(video, candidates, requested, seen):
    accepted = []
    for item in candidates:
        if item["language"] not in requested:
            continue
        if not _candidate_matches_video(video, item):
            continue
        key = (item["detail_url"], item["language"])
        if key in seen:
            continue
        seen.add(key)
        accepted.append(item)
        if len(accepted) >= MAX_CANDIDATES_PER_QUERY:
            break
    return accepted


def _candidate_matches_video(video, item):
    matches = set(derive_matches(video, item))
    if "title" not in matches:
        return False
    requested_year = (video or {}).get("year")
    candidate_years = list((item or {}).get("years") or [])
    candidate_year = (item or {}).get("year")
    if not candidate_years and candidate_year:
        candidate_years.append(candidate_year)
    if requested_year and candidate_years and int(requested_year) not in {int(year) for year in candidate_years}:
        return False
    return True


def _attr(tag, name):
    match = re.search(rf"\b{name}\s*=\s*([\"'])(.*?)\1", tag or "", re.I | re.S)
    return html.unescape(match.group(2)) if match else ""


def _absolute_url(url):
    if not url:
        return ""
    value = html.unescape(url).strip()
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    legacy_path = (query.get("p") or [""])[0]
    if legacy_path.startswith(("movie-details/", "/movie-details/")):
        return urllib.parse.urljoin(f"{BASE_URL}/", legacy_path.lstrip("/"))
    return urllib.parse.urljoin(f"{BASE_URL}/", value)


def _subtitle_id_from_url(url):
    path = urllib.parse.urlparse(url).path.strip("/")
    return path.split("/")[-2] if "/" in path else _slug(path)


def _dimension(value):
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def _decode(body):
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return body.decode("utf-8", errors="replace")


def _strip_tags(fragment):
    return _clean_text(_TAG_RE.sub("", fragment or ""))


def _clean_text(value):
    return _WS_RE.sub(" ", html.unescape(value or "")).strip()


def _normalize(text):
    text = _coerce_text(text) or ""
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _tokens(text):
    return [token for token in _normalize(text).split() if token]


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _alpha3_for_language(language):
    if isinstance(language, str):
        value = language
    else:
        value = (language or {}).get("alpha3") or (language or {}).get("alpha2") or ""
    value = value.lower()
    if value in SUPPORTED_LANGUAGES:
        return value
    return ALPHA2_TO_ALPHA3.get(value, value)


def _matches_token_table(value, table, release_tokens):
    text = _coerce_text(value)
    if not text:
        return False
    for item in text.split():
        normalized = _normalize(item)
        candidates = table.get(item) or table.get(normalized) or []
        if any(_normalize(candidate) in release_tokens for candidate in candidates):
            return True
    normalized = _normalize(text)
    candidates = table.get(text) or table.get(normalized) or []
    return any(_normalize(candidate) in release_tokens for candidate in candidates)


def _normalize_release_group(value):
    return re.sub(r"[^a-z0-9]+", "", (_coerce_text(value) or "").lower())


def _slug(value):
    slug = "-".join(_tokens(value))[:80]
    return slug or "subtitle"


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _is_hidden_path(name):
    return any(part.startswith(".") for part in (name or "").split("/"))


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _extract_rar_files(body):
    if py7zz is None:
        raise RuntimeError("Subs4Free RAR extraction requires bundled py7zz")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subs4free.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if _subtitle_extension(rel) and not _is_hidden_path(rel):
                with open(path, "rb") as handle:
                    files.append((rel, handle.read()))
    if not files:
        raise ValueError("subs4free archive contains no supported subtitle files")
    return files


def _content_payload(content, subtitle_format="srt", empty=False):
    content = _normalize_line_endings(content or b"")
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": "text/plain",
        "format": subtitle_format or "srt",
        "encoding": _detect_subtitle_encoding(content),
        "empty": bool(empty),
    }


def _normalize_line_endings(content):
    normalized = content.replace(b"\r\n", b"\n")
    return normalized.replace(b"\r", b"\n")


def _detect_subtitle_encoding(content):
    if not content:
        return "utf-8"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def _decode_payload_text(payload):
    return base64.b64decode(payload["content_b64"]).decode("utf-8", errors="replace")


_SOURCE_TOKENS = {
    "blu ray": ["bluray", "brrip", "bdrip"],
    "bluray": ["bluray", "brrip", "bdrip"],
    "web": ["web", "webdl", "webrip"],
    "web dl": ["webdl", "web"],
    "webrip": ["webrip", "web"],
    "hdtv": ["hdtv"],
    "dvd": ["dvd", "dvdrip"],
    "hdrip": ["hdrip"],
}
_VIDEO_CODEC_TOKENS = {
    "h 264": ["h264", "x264"],
    "h264": ["h264", "x264"],
    "h 265": ["h265", "x265", "hevc"],
    "h265": ["h265", "x265", "hevc"],
    "divx": ["divx"],
    "xvid": ["xvid"],
}
_AUDIO_CODEC_TOKENS = {
    "ac3": ["ac3"],
    "eac3": ["eac3", "ddp"],
    "aac": ["aac"],
    "dts": ["dts"],
    "flac": ["flac"],
    "mp3": ["mp3"],
    "truehd": ["truehd"],
}
