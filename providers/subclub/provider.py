"""Subclub provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

PROVIDER_ID = "subclub"
BASE_URL = "https://www.subclub.eu"
SEARCH_URL = f"{BASE_URL}/jutud.php"
ARCHIVE_LIST_URL = f"{BASE_URL}/subtitles_archivecontent.php"
DOWNLOAD_URL = f"{BASE_URL}/down.php"
HTTP_TIMEOUT_SECONDS = 30
# Transport-level retry: tolerate a single transient network blip (DNS / reset /
# timeout / 5xx / 429) without aborting the whole search or download. Mirrors the
# ~3-try behaviour of upstream subliminal's RetryingSession / ProviderRetryMixin.
HTTP_RETRIES = 2
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0
RETRY_STATUS = 429
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
        for attempt in range(1, HTTP_RETRIES + 2):
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                # 5xx and 429 are transient; every other 4xx (auth, not-found,
                # forbidden) is a real answer and must propagate on the first try.
                transient = error.code == RETRY_STATUS or 500 <= error.code <= 599
                if not transient or attempt > HTTP_RETRIES:
                    raise
                _retry_sleep(attempt, _retry_after_seconds(error))
            except (TimeoutError, socket.timeout, urllib.error.URLError):
                # Connection refused / DNS / reset / read timeout: transient transport.
                if attempt > HTTP_RETRIES:
                    raise
                _retry_sleep(attempt, None)
        raise RuntimeError("unreachable subclub retry state")

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
            return _download_payload(body, payload)
        archive_url = payload.get("archive_url")
        if not archive_url:
            raise ValueError("subclub download requires url or archive_url")
        body = self._http_get(archive_url, timeout=60)
        return _download_payload(body, payload)


def _download_payload(body, payload):
    payload = payload or {}
    # Reject broken responses up front: the down.php endpoint can answer with an empty
    # stream or an HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError(f"subclub empty download for archive {payload.get('archive_id')}")
    if _is_html_body(body):
        raise ValueError(f"subclub returned an HTML/error page for archive {payload.get('archive_id')}")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host, which lists it, picks the member by episode, and detects encoding.
        return {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
            "episode": payload.get("episode"),
        }
    # Direct, non-archive subtitle body.
    return _content_payload(_normalize_line_endings(body), _format_from_filename(payload.get("filename")))


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


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
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
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


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(int(delay_ms), 5000) / 1000.0)


def _retry_sleep(attempt, retry_after):
    # Exponential backoff with a small base, capped. Honor a server Retry-After
    # hint (429) when it is larger than the computed backoff. time.sleep is looked
    # up on the module so tests can monkeypatch it.
    backoff = min(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_CAP_SECONDS)
    if retry_after is not None:
        backoff = min(max(backoff, retry_after), RETRY_BACKOFF_CAP_SECONDS)
    time.sleep(backoff)


def _retry_after_seconds(error):
    # Only delay-seconds form is honored; an HTTP-date Retry-After falls back to
    # the normal backoff. Never let a malformed header break the retry.
    header = None
    try:
        header = error.headers.get("Retry-After")
    except AttributeError:
        header = None
    if not header:
        return None
    try:
        seconds = float(str(header).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


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
