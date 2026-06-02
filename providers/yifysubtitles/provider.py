"""YIFYSubtitles provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "yifysubtitles"
BASE_URL = "https://yifysubtitles.ch"
HOME_URL = f"{BASE_URL}/"
HTTP_TIMEOUT_SECONDS = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

LANGUAGE_NAMES = {
    "Albanian": ("sqi", "sq"),
    "Arabic": ("ara", "ar"),
    "Bengali": ("ben", "bn"),
    "Brazilian Portuguese": ("por", "pt"),
    "Brazillian Portuguese": ("por", "pt"),
    "Bulgarian": ("bul", "bg"),
    "Chinese": ("zho", "zh"),
    "Chinese BG code": ("zho", "zh"),
    "Croatian": ("hrv", "hr"),
    "Czech": ("ces", "cs"),
    "Danish": ("dan", "da"),
    "Dutch": ("nld", "nl"),
    "English": ("eng", "en"),
    "Farsi/Persian": ("fas", "fa"),
    "Finnish": ("fin", "fi"),
    "French": ("fra", "fr"),
    "German": ("deu", "de"),
    "Greek": ("ell", "el"),
    "Hebrew": ("heb", "he"),
    "Hungarian": ("hun", "hu"),
    "Indonesian": ("ind", "id"),
    "Italian": ("ita", "it"),
    "Japanese": ("jpn", "ja"),
    "Korean": ("kor", "ko"),
    "Lithuanian": ("lit", "lt"),
    "Macedonian": ("mkd", "mk"),
    "Malay": ("msa", "ms"),
    "Norwegian": ("nor", "no"),
    "Polish": ("pol", "pl"),
    "Portuguese": ("por", "pt"),
    "Romanian": ("ron", "ro"),
    "Russian": ("rus", "ru"),
    "Serbian": ("srp", "sr"),
    "Slovenian": ("slv", "sl"),
    "Spanish": ("spa", "es"),
    "Swedish": ("swe", "sv"),
    "Thai": ("tha", "th"),
    "Turkish": ("tur", "tr"),
    "Urdu": ("urd", "ur"),
    "Vietnamese": ("vie", "vi"),
}
SUPPORTED_LANGUAGES = {alpha3: alpha2 for alpha3, alpha2 in LANGUAGE_NAMES.values()}
ALPHA2_TO_ALPHA3 = {alpha2: alpha3 for alpha3, alpha2 in SUPPORTED_LANGUAGES.items()}

_TR_RE = re.compile(r"<tr\b(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", re.I | re.S)
_TD_RE = re.compile(r"<td\b(?P<attrs>[^>]*)>(?P<body>.*?)</td>", re.I | re.S)
_ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^'\"\s>]+))")
_SUB_LANG_RE = re.compile(r"<span\b[^>]*class\s*=\s*['\"][^'\"]*sub-lang[^'\"]*['\"][^>]*>(?P<text>.*?)</span>", re.I | re.S)
_SUBTITLE_LINK_RE = re.compile(r"<a\b(?P<attrs>[^>]*href\s*=\s*['\"][^'\"]*/subtitles/[^'\"]+['\"][^>]*)>(?P<text>.*?)</a>", re.I | re.S)
_DOWNLOAD_LINK_RE = re.compile(r"<a\b(?P<attrs>[^>]*class\s*=\s*['\"][^'\"]*download-subtitle[^'\"]*['\"][^>]*)>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_WS_RE = re.compile(r"\s+")


def parse_movie_page(body):
    text = _decode(body)
    rows = []
    for match in _TR_RE.finditer(text):
        row_attrs = _attrs(match.group("attrs"))
        row_html = match.group("body")
        subtitle_id = row_attrs.get("data-id") or ""
        link_match = _SUBTITLE_LINK_RE.search(row_html)
        lang_match = _SUB_LANG_RE.search(row_html)
        if not link_match or not lang_match:
            continue
        language_name = _strip_tags(lang_match.group("text"))
        language = LANGUAGE_NAMES.get(language_name)
        if not language:
            continue
        link_attrs = _attrs(link_match.group("attrs"))
        page_url = _absolute_url(link_attrs.get("href"))
        if not subtitle_id:
            id_match = re.search(r"-(\d+)$", page_url)
            subtitle_id = id_match.group(1) if id_match else hashlib.sha1(page_url.encode("utf-8")).hexdigest()[:12]
        release = _release_text(link_match.group("text"))
        rows.append(
            {
                "subtitle_id": subtitle_id,
                "language": language[0],
                "alpha2": language[1],
                "release": release,
                "page_url": page_url,
                "rating": _rating(row_html),
                "hi": "hi-subtitle" in row_html,
                "uploader": _uploader(row_html),
            }
        )
    return rows


def parse_download_url(body):
    text = _decode(body)
    match = _DOWNLOAD_LINK_RE.search(text)
    if not match:
        raise ValueError("yifysubtitles detail page did not expose a download link")
    href = _attrs(match.group("attrs")).get("href")
    if not href:
        raise ValueError("yifysubtitles download link has no href")
    return _absolute_url(href)


def derive_matches(video, row):
    video = video or {}
    row = row or {}
    release = row.get("release") or ""
    matches = []
    if video.get("imdb_id"):
        matches.append("imdb_id")
    if _title_in_text(video.get("title"), release):
        matches.append("title")
    if _release_group_matches(video.get("release_group"), release):
        matches.append("release_group")
    if _token_in_text(video.get("resolution"), release):
        matches.append("resolution")
    if _source_matches(video.get("source"), release):
        matches.append("source")
    return matches


def extract_download(body, payload=None):
    payload = payload or {}
    filename = payload.get("filename") or ""
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    if _looks_like_html(body):
        raise ValueError("yifysubtitles download returned HTML instead of a ZIP archive")
    return _content_payload(body, _format_from_filename(filename))


def select_subtitle_file(names, payload=None):
    payload = payload or {}
    release = payload.get("release") or ""
    candidates = [name for name in names if _is_supported_subtitle_name(name)]
    if not candidates:
        raise ValueError("yifysubtitles archive contains no supported subtitle files")

    def score(name):
        value = 0
        text = name
        if _title_in_text(_release_title(release), name):
            value += 10
        if _token_in_text(payload.get("resolution"), text):
            value += 20
        if _source_matches(payload.get("source"), text):
            value += 20
        if _release_group_matches(payload.get("release_group"), text):
            value += 20
        return value

    return max(candidates, key=score)


class YifySubtitlesProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        config = dict(config or {})
        video = video or {}
        if video.get("kind") != "movie" or not video.get("imdb_id"):
            return []
        requested = _requested_languages(languages)
        if not requested:
            return []
        _sleep(config)
        movie_url = f"{BASE_URL}/movie-imdb/{_imdb_id(video.get('imdb_id'))}"
        try:
            rows = parse_movie_page(self._http_get(movie_url, referer=HOME_URL))
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 404:
                return []
            raise
        results = []
        seen = set()
        for row in rows:
            for language in requested:
                if row["language"] != language["alpha3"] or row["hi"] != language["hi"]:
                    continue
                key = (row["subtitle_id"], language["alpha3"], language["hi"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(_result_from_row(video, row, language))
        return sorted(results, key=lambda item: (item["score"], item["provider_payload"]["rating"]), reverse=True)

    def download(self, provider_payload, language, config):
        del language
        config = dict(config or {})
        payload = provider_payload or {}
        page_url = payload.get("page_url")
        if not page_url:
            raise ValueError("yifysubtitles download requires page_url")
        detail = self._http_get(page_url, referer=HOME_URL)
        download_url = parse_download_url(detail)
        _sleep(config)
        body = self._http_get(download_url, referer=page_url)
        merged = dict(payload)
        merged["download_url"] = download_url
        return extract_download(body, merged)


def _result_from_row(video, row, language):
    matches = derive_matches(video, row)
    score = _score(matches, row)
    filename = f"yifysubtitles.{row['subtitle_id']}.{language['alpha2']}.zip"
    return {
        "provider": PROVIDER_ID,
        "id": f"yifysubtitles-{row['subtitle_id']}-{language['alpha3']}",
        "language": dict(language),
        "release_info": row["release"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": row["hi"],
        "page_link": row["page_url"],
        "display": {
            "source": "yifysubtitles.ch",
            "release": row["release"],
            "uploader": row["uploader"],
            "rating": row["rating"],
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "page_url": row["page_url"],
            "filename": filename,
            "release": row["release"],
            "rating": row["rating"],
            "language": language["alpha3"],
            "resolution": video.get("resolution"),
            "source": video.get("source"),
            "release_group": video.get("release_group"),
        },
    }


def _score(matches, row):
    score = int(row.get("rating") or 0)
    weights = {
        "imdb_id": 25,
        "title": 25,
        "release_group": 15,
        "resolution": 10,
        "source": 10,
    }
    return min(100, score + sum(weights[name] for name in matches if name in weights))


def _requested_languages(languages):
    rows = []
    seen = set()
    for item in languages or []:
        alpha3 = _alpha3_for_language(item)
        if alpha3 not in SUPPORTED_LANGUAGES:
            continue
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        hi = bool(item.get("hi", False)) if isinstance(item, dict) else False
        forced = bool(item.get("forced", False)) if isinstance(item, dict) else False
        if forced:
            continue
        key = (alpha3, hi, forced)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"alpha3": alpha3, "alpha2": alpha2, "hi": hi, "forced": forced})
    return rows


def _alpha3_for_language(language):
    if isinstance(language, dict):
        value = language.get("alpha3") or ALPHA2_TO_ALPHA3.get(language.get("alpha2"))
    else:
        value = str(language or "")
    return (value or "").lower()


def _release_text(fragment):
    text = _strip_tags(fragment)
    return re.sub(r"^subtitle\s+", "", text, flags=re.I).strip()


def _rating(row_html):
    text = _strip_tags(row_html)
    match = re.search(r"(?<!\d)(\d{1,3})(?!\d)", text)
    return int(match.group(1)) if match else 0


def _uploader(row_html):
    for td_match in _TD_RE.finditer(row_html):
        attrs = _attrs(td_match.group("attrs"))
        if "uploader-cell" in attrs.get("class", ""):
            return _strip_tags(td_match.group("body"))
    return ""


def _release_title(release):
    normalized = _normalize(release)
    tokens = []
    for token in normalized.split():
        if re.fullmatch(r"\d{4}|\d+p|web|webrip|webdl|hmax|bluray|brrip|hdrip", token):
            break
        tokens.append(token)
    return " ".join(tokens)


def _title_in_text(title, text):
    title_tokens = _normalize(title).split()
    text_tokens = set(_normalize(text).split())
    return bool(title_tokens and all(token in text_tokens for token in title_tokens))


def _release_group_matches(release_group, text):
    release_group = _coerce_text(release_group)
    return bool(release_group and release_group.lower() in (text or "").lower())


def _source_matches(source, text):
    source = _normalize(source)
    normalized = _normalize(text)
    if not source:
        return False
    if source in ("web", "webdl", "web dl", "web-dl"):
        return any(token in normalized for token in ("web dl", "web-dl", "webrip", "web rip", "web"))
    if source in ("bluray", "blu ray", "bdrip", "brrip"):
        return any(token in normalized for token in ("bluray", "blu ray", "bdrip", "brrip", "bd rip"))
    return source in normalized


def _token_in_text(token, text):
    token = _coerce_text(token)
    return bool(token and token.lower() in (text or "").lower())


def _content_payload(content, fmt, empty=False):
    content = content or b""
    fmt = fmt or "srt"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "encoding": _detect_subtitle_encoding(content),
        "empty": bool(empty),
    }


def _content_type(fmt):
    if fmt == "srt":
        return "application/x-subrip"
    if fmt == "vtt":
        return "text/vtt"
    if fmt in ("ass", "ssa"):
        return "text/x-ssa"
    return "application/octet-stream"


def _detect_subtitle_encoding(content):
    if not content:
        return "utf-8"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def _format_from_filename(filename):
    return _subtitle_extension(filename) or "srt"


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _is_supported_subtitle_name(name):
    base = os.path.basename(name or "")
    return bool(base and not base.startswith(".") and _subtitle_extension(base))


def _looks_like_html(body):
    sample = (body or b"").lstrip()[:1024].lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<body" in sample


def _attrs(fragment):
    return {
        match.group(1).lower(): html.unescape(next(group for group in match.groups()[1:] if group is not None))
        for match in _ATTR_RE.finditer(fragment or "")
    }


def _absolute_url(url):
    return urllib.parse.urljoin(HOME_URL, html.unescape(url or ""))


def _imdb_id(value):
    value = _coerce_text(value)
    match = re.search(r"tt\d+", value)
    if match:
        return match.group(0)
    digits = re.search(r"\d+", value)
    return f"tt{digits.group(0)}" if digits else ""


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms", 0))
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)


def _decode(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _strip_tags(fragment):
    text = _BR_RE.sub(" ", fragment or "")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _coerce_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _normalize(value):
    text = _coerce_text(value).lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return _WS_RE.sub(" ", text).strip()
