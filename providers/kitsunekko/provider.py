"""Kitsunekko provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
import html
import io
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import socket

PROVIDER_ID = "kitsunekko"
BASE_URL = "https://kitsunekko.net"
HTTP_TIMEOUT_SECONDS = 30
HTTP_RETRIES = 2
MAX_DIRECTORIES_PER_LANGUAGE = 5
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt")
SUPPORTED_FILE_EXTENSIONS = SUBTITLE_EXTENSIONS + (".zip",)
ARCHIVE_EXTENSIONS = (".zip",)
LANGUAGE_ROOTS = {
    "eng": "subtitles/",
    "jpn": "subtitles/japanese/",
}
ALPHA3_TO_ALPHA2 = {
    "eng": "en",
    "jpn": "ja",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in ALPHA3_TO_ALPHA2.items()}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_ANCHOR_RE = re.compile(
    rb"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>\s*"
    rb"<strong>(?P<title>.*?)</strong>",
    re.I | re.S,
)
_ROW_RE = re.compile(rb"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_SIZE_RE = re.compile(rb'<td\b[^>]*class=["\']tdleft["\'][^>]*title=["\'](?P<size>\d+)["\']', re.I)
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_BYTES_RE = re.compile(rb"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def index_url_for_language(alpha3):
    root = LANGUAGE_ROOTS.get(alpha3)
    if not root:
        return None
    return f"{BASE_URL}/dirlist.php?dir={urllib.parse.quote(root, safe='')}"


def parse_index_directories(html_bytes):
    rows = []
    for match in _ANCHOR_RE.finditer(html_bytes or b""):
        href = _decode_attr(match.group("href"))
        if "dirlist.php?dir=" not in href:
            continue
        title = _strip_tags(match.group("title"))
        if title:
            rows.append({"title": title, "url": _absolute_url(href)})
    return rows


def parse_file_listing(html_bytes, directory_title):
    rows = []
    for row_match in _ROW_RE.finditer(html_bytes or b""):
        row = row_match.group("body")
        link_match = _ANCHOR_RE.search(row)
        if not link_match:
            continue
        filename = _strip_tags(link_match.group("title"))
        fmt = _format_from_filename(filename)
        if fmt not in SUPPORTED_FILE_EXTENSIONS:
            continue
        size_match = _SIZE_RE.search(row)
        size_bytes = int(size_match.group("size")) if size_match else 0
        rows.append(
            {
                "directory_title": directory_title,
                "filename": filename,
                "format": fmt.lstrip("."),
                "archive_format": fmt.lstrip(".") if fmt in ARCHIVE_EXTENSIONS else None,
                "size_bytes": size_bytes,
                "url": _absolute_url(_decode_attr(link_match.group("href"))),
            }
        )
    return rows


def derive_matches(video, candidate_title):
    if not video:
        return []
    matches = []
    candidate_tokens = set(_tokens(candidate_title))
    kind = video.get("kind")
    if kind == "movie":
        title_tokens = _tokens(video.get("title"))
        if title_tokens and all(token in candidate_tokens for token in title_tokens):
            matches.append("title")
        year = video.get("year")
        if year and str(year) in candidate_tokens:
            matches.append("year")
    elif kind == "episode":
        series_tokens = _tokens(video.get("series"))
        if series_tokens and all(token in candidate_tokens for token in series_tokens):
            matches.append("series")
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            season = episode = None
        normalized = _normalize(candidate_title)
        if season is not None and re.search(rf"\bs0*{season}\b", normalized):
            matches.append("season")
        if season is not None and episode is not None and _episode_in_text(normalized, episode, season=season):
            matches.append("episode")
    return matches


def compute_score(video, candidate, directory_score):
    title = f"{candidate.get('directory_title', '')} {candidate.get('filename', '')}"
    matches = derive_matches(video, title)
    kind = (video or {}).get("kind")
    if kind == "movie":
        if "title" in matches and "year" in matches:
            return 100
        if "title" in matches:
            return max(90, directory_score)
        return directory_score
    if kind == "episode":
        if "episode" in matches:
            return 98
        if "series" in matches and candidate.get("archive_format") == "zip":
            return 90
        if "series" in matches:
            return 80
    return directory_score


class KitsunekkoProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
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
        raise RuntimeError("unreachable kitsunekko retry state")

    def search(self, video, languages, config):
        config = dict(config or {})
        requested = [_alpha3_for_language(lang) for lang in languages or []]
        requested = [lang for lang in requested if lang in LANGUAGE_ROOTS]
        results = []
        seen = set()
        for alpha3 in requested:
            index_url = index_url_for_language(alpha3)
            _sleep(config)
            directories = parse_index_directories(self._http_get(index_url))
            candidates = _rank_directories(video, directories)
            for directory, directory_score in candidates[:MAX_DIRECTORIES_PER_LANGUAGE]:
                _sleep(config)
                files = parse_file_listing(
                    self._http_get(directory["url"], referer=index_url),
                    directory["title"],
                )
                for item in files:
                    key = (item["url"], alpha3)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, item, alpha3, directory_score))
        results.sort(key=lambda item: item["score"], reverse=True)
        return results

    def _result(self, video, item, alpha3, directory_score):
        candidate_title = f"{item.get('directory_title', '')} {item.get('filename', '')}"
        score = compute_score(video, item, directory_score)
        alpha2 = ALPHA3_TO_ALPHA2.get(alpha3)
        archive_format = item.get("archive_format")
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "url": item["url"],
            "filename": item["filename"],
            "format": item["format"],
            "language": alpha3,
        }
        if archive_format:
            payload["archive_format"] = archive_format
        if (video or {}).get("kind") == "episode":
            payload["season"] = video.get("season")
            payload["episode"] = video.get("episode")
        return {
            "provider": PROVIDER_ID,
            "id": _stable_id(item["url"], alpha3),
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": item["filename"],
            "filename": item["filename"],
            "matches": derive_matches(video, candidate_title),
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["url"],
            "display": {
                "source": "kitsunekko",
                "title": item["directory_title"],
                "release": item["filename"],
                "size_bytes": item["size_bytes"],
            },
            "provider_payload": payload,
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        url = payload.get("url")
        filename = payload.get("filename") or ""
        fmt = (payload.get("format") or _format_from_filename(filename).lstrip(".") or "srt").lower()
        if not url:
            raise ValueError("kitsunekko download requires url")
        body = self._http_get(url)
        if payload.get("archive_format") == "zip" or zipfile.is_zipfile(io.BytesIO(body or b"")):
            # Host-side extraction (Provider Hub v1.1+): list the zip cheaply with stdlib
            # zipfile, pick the member, and hand the raw archive bytes back to the host,
            # which extracts that member and detects the encoding.
            member = _select_zip_member(body, payload.get("episode"))
            return {
                "archive_b64": _base64.b64encode(body).decode("ascii"),
                "archive_sha256": _hashlib.sha256(body).hexdigest(),
                "member": member,
            }
        return _content_payload(body, fmt)


def _rank_directories(video, directories):
    ranked = []
    for directory in directories:
        score = _directory_score(video, directory["title"])
        if score > 0:
            ranked.append((directory, score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _directory_score(video, title):
    if not video:
        return 0
    kind = video.get("kind")
    title_tokens = set(_tokens(title))
    if kind == "movie":
        wanted = _tokens(video.get("title"))
        if wanted and all(token in title_tokens for token in wanted):
            return 95
    if kind == "episode":
        wanted = _tokens(video.get("series"))
        if wanted and all(token in title_tokens for token in wanted):
            return 95
    return 0


def _select_zip_member(body, episode):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(SUBTITLE_EXTENSIONS)]
    if not names:
        raise ValueError("kitsunekko archive contained no subtitle files")
    names.sort(key=lambda name: _archive_sort_key(name, episode))
    return names[0]


def _archive_sort_key(name, episode):
    extension_rank = {".srt": 0, ".ass": 1, ".ssa": 2, ".vtt": 3}
    suffix = "." + name.rsplit(".", 1)[-1].lower()
    episode_rank = 1
    try:
        episode_number = int(episode)
    except (TypeError, ValueError):
        episode_number = None
    if episode_number is not None and _episode_in_text(_normalize(name), episode_number):
        episode_rank = 0
    return (episode_rank, extension_rank.get(suffix, 9), len(name), name.lower())


def _episode_in_text(normalized, episode, season=None):
    if season is not None and re.search(rf"\bs0*{season}e0*{episode}\b", normalized):
        return True
    return re.search(rf"(^|[^0-9])0*{episode}([^0-9]|$)", normalized) is not None


def _content_payload(body, fmt):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    if not body:
        return {
            "content_b64": "",
            "content_sha256": "",
            "content_type": _content_type(fmt),
            "format": fmt,
            "empty": True,
        }
    return {
        "content_b64": _base64.b64encode(body).decode("ascii"),
        "content_sha256": _hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "empty": False,
    }


def _content_type(fmt):
    if fmt in {"ass", "ssa"}:
        return "text/x-ssa"
    if fmt == "vtt":
        return "text/vtt"
    return "application/x-subrip"


def _format_from_filename(filename):
    lower = (filename or "").lower()
    for extension in SUPPORTED_FILE_EXTENSIONS + (".7z", ".rar"):
        if lower.endswith(extension):
            return extension
    return ""


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    if alpha3:
        return alpha3
    return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _stable_id(url, alpha3):
    digest = _hashlib.sha1(f"{url}:{alpha3}".encode("utf-8")).hexdigest()[:16]
    return f"kitsunekko-{digest}"


def _absolute_url(value):
    joined = urllib.parse.urljoin(f"{BASE_URL}/", value)
    parts = urllib.parse.urlsplit(joined)
    path = urllib.parse.quote(parts.path, safe="/%:@+")
    query = urllib.parse.quote(parts.query, safe="=&%:+")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _decode_attr(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return html.unescape(value)


def _strip_tags(value):
    stripped = _TAG_RE.sub(b"", value or b"")
    stripped = _WS_BYTES_RE.sub(b" ", stripped).strip()
    return html.unescape(stripped.decode("utf-8", errors="replace"))


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()
