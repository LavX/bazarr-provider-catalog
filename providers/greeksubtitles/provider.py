"""GreekSubtitles provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
import html
import io
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

PROVIDER_ID = "greeksubtitles"
BASE_URL = "https://gr.greek-subtitles.com"
DOWNLOAD_URL = "https://www.greeksubtitles.info/getp.php?id={}"
HTTP_TIMEOUT_SECONDS = 75
HTTP_RETRIES = 2
MAX_PAGES = 6
MAX_RESULTS = 50
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
SUPPORTED_LANGUAGES = {
    "ell": "el",
    "eng": "en",
}
ALPHA2_TO_ALPHA3 = {"el": "ell", "gr": "ell", "en": "eng"}
USER_AGENT = "Subliminal/2.1 BazarrProviderHub"

_ROW_RE = re.compile(rb"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_CELL_RE = re.compile(rb"<td\b(?P<attrs>[^>]*)>(?P<body>.*?)</td>", re.I | re.S)
_ANCHOR_RE = re.compile(rb"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>", re.I | re.S)
_IMG_RE = re.compile(rb"<img\b[^>]*src=['\"](?P<src>[^'\"]+)['\"][^>]*>", re.I | re.S)
_NEXT_RE = re.compile(
    rb"<a\b[^>]*href\s*=\s*['\"](?P<href>[^'\"]*search\.php[^'\"]+)['\"][^>]*>\s*Next\s*(?:&gt;|>){2}\s*</a>",
    re.I,
)
_DOWNLOAD_ID_RE = re.compile(r"/(\d+)/?$")
_TAG_RE = re.compile(rb"<[^>]+>")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_search_page(body, page_url):
    rows = []
    for row_match in _ROW_RE.finditer(body or b""):
        row = _parse_result_row(row_match.group("body"))
        if row:
            rows.append(row)
    next_url = None
    next_match = _NEXT_RE.search(body or b"")
    if next_match:
        next_url = _absolute_url(_decode(next_match.group("href")), page_url)
    return {"rows": rows, "next_url": next_url}


def build_search_queries(video):
    video = video or {}
    kind = video.get("kind")
    if kind == "episode":
        series = video.get("series")
        if not series:
            return []
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            return []
        suffix = f"S{season:02d}E{episode:02d}"
        titles = [series] + list(video.get("alternative_series") or [])
        return _dedupe([f"{title} {suffix}" for title in titles if title])
    if kind == "movie":
        title = video.get("title")
        if not title:
            return []
        titles = [title] + list(video.get("alternative_titles") or [])
        queries = []
        for item in titles:
            if not item:
                continue
            if video.get("year"):
                queries.append(f"{item} {int(video['year'])}")
            else:
                queries.append(item)
        return _dedupe(queries)
    return []


def search_url_for(query):
    return f"{BASE_URL}/search.php?{urllib.parse.urlencode({'name': query})}"


def derive_matches(video, release):
    video = video or {}
    release_normalized = _normalize(release)
    release_tokens = set(release_normalized.split())
    matches = []
    kind = video.get("kind")
    if kind == "movie":
        if _all_tokens_in(video.get("title"), release_normalized):
            matches.append("title")
        if video.get("year") and str(video["year"]) in release_tokens:
            matches.append("year")
    elif kind == "episode":
        if _all_tokens_in(video.get("series"), release_normalized):
            matches.append("series")
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            season = episode = None
        if season is not None and (
            re.search(rf"\bs0*{season}\s*e0*{episode}\b", release_normalized)
            or re.search(rf"\b0*{season}x0*{episode}\b", release_normalized)
        ):
            matches.extend(["season", "episode"])
        elif episode is not None and re.search(rf"\be0*{episode}\b", release_normalized):
            matches.append("episode")
        if video.get("year") and str(video["year"]) in release_tokens:
            matches.append("year")
    for key in ("source", "resolution", "video_codec", "audio_codec", "release_group"):
        value = video.get(key)
        if value and _all_tokens_in(value, release_normalized):
            matches.append(key)
    return _dedupe(matches)


class GreekSubtitlesProvider:
    def __init__(self):
        cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "el,en-US;q=0.7,en;q=0.3",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        for attempt in range(HTTP_RETRIES + 1):
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError:
                raise
            except (TimeoutError, socket.timeout, urllib.error.URLError):
                if attempt >= HTTP_RETRIES:
                    raise
                time.sleep(0.25 * (attempt + 1))
        raise RuntimeError("unreachable greeksubtitles retry state")

    def search(self, video, languages, config):
        requested = {_alpha3_for_language(language) for language in languages or []}
        requested = {language for language in requested if language in SUPPORTED_LANGUAGES}
        if not requested or (video or {}).get("kind") not in {"movie", "episode"}:
            return []
        config = dict(config or {})
        results = []
        seen = set()
        for query in build_search_queries(video):
            page_url = search_url_for(query)
            page_count = 0
            while page_url and page_count < MAX_PAGES:
                page_count += 1
                _sleep(config)
                page = parse_search_page(self._http_get(page_url), page_url)
                for row in page["rows"]:
                    if row["language"] not in requested:
                        continue
                    key = (row["subtitle_id"], row["language"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, row, query))
                    if len(results) >= MAX_RESULTS:
                        return sorted(results, key=lambda item: item["score"], reverse=True)
                page_url = page["next_url"]
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, row, search_query):
        video = video or {}
        language = row["language"]
        alpha2 = row["alpha2"]
        release = row["release"]
        matches = derive_matches(video, release)
        score = _score(matches, row)
        filename = f"greeksubtitles.{_slug(release)}.{alpha2}.zip"
        download_url = DOWNLOAD_URL.format(row["subtitle_id"])
        season = _int_or_none(video.get("season")) if video.get("kind") == "episode" else None
        episode = _int_or_none(video.get("episode")) if video.get("kind") == "episode" else None
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "download_url": download_url,
            "page_url": row["page_url"],
            "filename": filename,
            "language": language,
            "search_query": search_query,
            "release": release,
            "season": season,
            "episode": episode,
        }
        return {
            "provider": PROVIDER_ID,
            "id": f"greeksubtitles-{row['subtitle_id']}-{language}",
            "language": {
                "alpha3": language,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": release,
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": row["page_url"],
            "display": {
                "source": "greeksubtitles",
                "title": release,
                "release": release,
                "downloads": row["downloads"],
            },
            "provider_payload": payload,
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("download_url")
        if not url:
            raise ValueError("greeksubtitles download requires download_url")
        body = self._http_get(url, referer=payload.get("page_url"))
        return _download_payload(body, payload)


def _download_payload(body, payload):
    payload = payload or {}
    # Reject broken responses up front: getp.php can answer with an empty stream or an
    # HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError("greeksubtitles download returned an empty body")
    if _looks_like_html(body):
        raise ValueError("greeksubtitles download returned HTML instead of subtitle content")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host, which lists it, picks the member by episode, and detects encoding.
        return {
            "archive_b64": _base64.b64encode(body).decode("ascii"),
            "archive_sha256": _hashlib.sha256(body).hexdigest(),
            "episode": payload.get("episode"),
        }
    # Direct, non-archive subtitle body.
    return _content_payload(_normalize_line_endings(body), _format_from_filename(payload.get("filename")))


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _parse_result_row(row_body):
    cells = _cells(row_body)
    latest = [cell for cell in cells if "latest_name" in cell["class"]]
    downloads = [cell for cell in cells if "latest_downloads" in cell["class"]]
    if len(latest) < 2:
        return None
    content = latest[1]["body"]
    img_match = _IMG_RE.search(content)
    link_match = _ANCHOR_RE.search(content)
    if not img_match or not link_match:
        return None
    alpha2 = _language_alpha2(_decode(img_match.group("src")))
    language = ALPHA2_TO_ALPHA3.get(alpha2)
    if not language:
        return None
    page_url = html.unescape(_decode(link_match.group("href")))
    subtitle_id = _subtitle_id_from_url(page_url)
    if not subtitle_id:
        return None
    return {
        "subtitle_id": subtitle_id,
        "language": language,
        "alpha2": SUPPORTED_LANGUAGES[language],
        "page_url": page_url,
        "release": _strip_tags(link_match.group("body")),
        "downloads": _int_from_text(_strip_tags(downloads[0]["body"]) if downloads else ""),
    }


def _cells(row_body):
    parsed = []
    for match in _CELL_RE.finditer(row_body):
        attrs = _decode(match.group("attrs"))
        parsed.append(
            {
                "class": _attr_value(attrs, "class"),
                "body": match.group("body"),
            }
        )
    return parsed


def _language_alpha2(src):
    basename = urllib.parse.urlsplit(src).path.rsplit("/", 1)[-1].split(".", 1)[0].lower()
    if basename == "gr":
        return "el"
    return basename


def _subtitle_id_from_url(value):
    match = _DOWNLOAD_ID_RE.search(urllib.parse.urlsplit(value).path)
    return match.group(1) if match else ""


def _score(matches, row):
    score = 55
    for key, points in {
        "title": 15,
        "series": 15,
        "year": 8,
        "season": 8,
        "episode": 10,
        "source": 5,
        "video_codec": 4,
        "audio_codec": 4,
        "release_group": 6,
    }.items():
        if key in matches:
            score += points
    if row.get("downloads"):
        score += min(int(row["downloads"]) // 250, 6)
    return min(score, 100)


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    if alpha3:
        return alpha3
    return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())


def _all_tokens_in(value, normalized_haystack):
    tokens = [token for token in _normalize(value).split() if token]
    haystack_tokens = set((normalized_haystack or "").split())
    return bool(tokens) and all(token in haystack_tokens for token in tokens)


def _looks_like_html(body):
    prefix = (body or b"").lstrip()[:512].lower()
    return (
        prefix.startswith(b"<!doctype html")
        or prefix.startswith(b"<html")
        or b"<body" in prefix
        or b"<head" in prefix
    )


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _absolute_url(value, base_url):
    joined = urllib.parse.urljoin(base_url, html.unescape(value or ""))
    parts = urllib.parse.urlsplit(joined)
    path = urllib.parse.quote(parts.path, safe="/%:@+")
    query = urllib.parse.quote(parts.query, safe="=&%:+")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _attr_value(attrs, name):
    match = re.search(rf"\b{name}\s*=\s*['\"]([^'\"]+)['\"]", attrs, re.I)
    return html.unescape(match.group(1)) if match else ""


def _int_from_text(value):
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values):
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _format_from_filename(filename):
    return _subtitle_extension(filename or "")


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _content_payload(body, subtitle_format):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    subtitle_format = subtitle_format or "srt"
    return {
        "content_b64": _base64.b64encode(body).decode("ascii"),
        "content_sha256": _hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
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


def _normalize_line_endings(body):
    return (body or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _slug(value):
    return re.sub(r"\s+", "-", _normalize(value)).strip("-") or "subtitle"


def _normalize(value):
    return _NON_ALNUM_RE.sub(" ", str(value or "").lower()).strip()


def _strip_tags(value):
    value = _TAG_RE.sub(b" ", value or b"")
    return re.sub(r"\s+", " ", html.unescape(_decode(value))).strip()


def _decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")
