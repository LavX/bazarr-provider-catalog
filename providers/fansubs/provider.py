"""Fansubs.ru provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import re
import time
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "fansubs"
BASE_URL = "http://fansubs.ru"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 15
SUPPORTED_ALPHA3 = "rus"
SUPPORTED_ALPHA2 = "ru"
MAX_CANDIDATES_PER_QUERY = 8
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def build_queries(video):
    video = video or {}
    kind = video.get("kind")
    if kind == "movie":
        title = (_coerce_text(video.get("title")) or "").strip()
        if not title:
            return []
        year = video.get("year")
        if year:
            return [f"{title} {year}", title]
        return [title]
    if kind == "episode":
        series = (_coerce_text(video.get("series")) or "").strip()
        if not series:
            return []
        return [series]
    return []


def decode_page(body):
    if not body:
        return ""
    if body.startswith(b"\xef\xbb\xbf"):
        return body[3:].decode("utf-8", errors="replace")
    return body.decode("cp1251", errors="replace")


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_tags(fragment):
    text = _TAG_RE.sub("", fragment or "")
    return _WHITESPACE_RE.sub(" ", html.unescape(text)).strip()


_SEARCH_LINK_RE = re.compile(
    r"<a\s+href=[\"']?base\.php\?id=(\d+)[\"']?[^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_SMALL_RE = re.compile(r"<small\b[^>]*>.*?</small>", re.IGNORECASE | re.DOTALL)


def parse_search_results(body):
    text = decode_page(body)
    results = []
    seen = set()
    for match in _SEARCH_LINK_RE.finditer(text):
        media_id = match.group(1)
        if media_id in seen:
            continue
        title = _strip_tags(_SMALL_RE.sub("", match.group(2)))
        if not title:
            continue
        seen.add(media_id)
        results.append(
            {
                "media_id": media_id,
                "title": title,
                "detail_url": f"{BASE_URL}/base.php?id={media_id}",
            }
        )
    return results


def _is_rate_limited(body):
    text = decode_page(body).lower()
    return (
        "repeat the search in 5 seconds" in text
        or "повторите запрос через 5 секунд" in text
    )


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_FORM_RE = re.compile(
    r"(<form\b[^>]*action=[\"']?base\.php[\"']?[^>]*>.*?</form>\s*</table>)"
    r"(?P<author>\s*<table\b[^>]*class=[\"']?row1[\"']?[^>]*>.*?</table>)?",
    re.IGNORECASE | re.DOTALL,
)
_SRT_INPUT_RE = re.compile(
    r"<input\b[^>]*name=[\"']?srt[\"']?[^>]*value=[\"']?(\d+)[\"']?[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_ROW3_TD_RE = re.compile(
    r"<td\b[^>]*class=[\"']?row3[\"']?[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_AUTHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"']?base\.php\?au=\d+[\"']?[^>]*>\s*<b>(.*?)</b>",
    re.IGNORECASE | re.DOTALL,
)
_BOLD_RE = re.compile(r"<b>(.*?)</b>", re.IGNORECASE | re.DOTALL)


def parse_detail_page(body, media_id):
    text = decode_page(body)
    title_match = _TITLE_RE.search(text)
    page_title = _strip_tags(title_match.group(1)) if title_match else ""
    subtitles = []

    for match in _FORM_RE.finditer(text):
        form_html = match.group(1)
        srt_match = _SRT_INPUT_RE.search(form_html)
        if not srt_match:
            continue
        columns = [_strip_tags(item) for item in _ROW3_TD_RE.findall(form_html)]
        if len(columns) < 5:
            continue
        subtitle_title = columns[2]
        subtitle_format = (columns[3] or "srt").lower()
        date = columns[4]
        author = _parse_author(match.group("author") or "")
        subtitles.append(
            {
                "subtitle_id": srt_match.group(1),
                "media_id": str(media_id),
                "media_title": page_title,
                "title": subtitle_title,
                "format": subtitle_format,
                "date": date,
                "author": author,
                "has_note": f"base.php?note={srt_match.group(1)}" in form_html,
            }
        )

    return {"media_id": str(media_id), "title": page_title, "subtitles": subtitles}


def _parse_author(author_html):
    match = _AUTHOR_RE.search(author_html)
    if match:
        return _strip_tags(match.group(1))
    match = _BOLD_RE.search(author_html)
    if match:
        return _strip_tags(match.group(1))
    return ""


_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_RANGE_RE = re.compile(r"(?<!\d)(\d{1,3})\s*-\s*(\d{1,3})(?!\d)")
_SEASON_RE = re.compile(
    r"(?:season|сезон|сез\.?|s)\s*0*(\d{1,2})\b",
    re.IGNORECASE,
)


def _episode_match_kind(title, episode):
    """Return "matched", "missed", or "absent" for a subtitle title vs. episode."""
    try:
        episode = int(episode)
    except (TypeError, ValueError):
        return "absent"
    text = _coerce_text(title) or ""
    saw_marker = False
    for start, end in _RANGE_RE.findall(text):
        saw_marker = True
        if int(start) <= episode <= int(end):
            return "matched"
    for number in _NUMBER_RE.findall(text):
        saw_marker = True
        if int(number) == episode:
            return "matched"
    return "missed" if saw_marker else "absent"


def episode_range_matches(title, episode):
    return _episode_match_kind(title, episode) != "missed"


def episode_explicitly_matches(title, episode):
    return _episode_match_kind(title, episode) == "matched"


def _season_in_text(text, season):
    try:
        season = int(season)
    except (TypeError, ValueError):
        return None
    coerced = _coerce_text(text) or ""
    found = False
    for marker in _SEASON_RE.findall(coerced):
        found = True
        if int(marker) == season:
            return True
    return False if found else None


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _is_rar_archive(body):
    if not body:
        return False
    return (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _is_html_body(body):
    if not body:
        return False
    head = body[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
        or b"<head" in head
        or b"<body" in head
    )


def _archive_payload(body, episode):
    return {
        "archive_b64": base64.b64encode(body).decode("ascii"),
        "archive_sha256": hashlib.sha256(body).hexdigest(),
        "episode": episode,
    }


def extract_download(body, filename="", content_type="", episode=None):
    del content_type
    if not body or not body.strip():
        raise ValueError("fansubs download returned an empty body")

    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream) or _is_rar_archive(body):
        # Hand the raw archive to the host, which extracts the member and detects
        # the encoding (Provider Hub v1.1 host-side archive extraction).
        return _archive_payload(body, episode)

    if _is_html_body(body):
        raise ValueError("fansubs download returned an HTML/error page")

    return _content_payload(body, _format_from_filename(filename))


def _format_from_filename(filename):
    extension = _subtitle_extension(filename or "")
    return extension or "srt"


def _content_payload(content, subtitle_format):
    # Leave encoding unset; the host normalizes via chardet (Subtitle.normalize()).
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "empty": False,
    }


def _content_type(subtitle_format):
    return {
        "srt": "application/x-subrip",
        "ass": "text/x-ssa",
        "ssa": "text/x-ssa",
        "vtt": "text/vtt",
        "sub": "text/plain",
    }.get(subtitle_format, "text/plain")


def _requested_russian_language(languages):
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        alpha3 = (language.get("alpha3") or "").lower()
        alpha2 = (language.get("alpha2") or "").lower()
        if alpha3 == SUPPORTED_ALPHA3 or alpha2 == SUPPORTED_ALPHA2:
            return {
                "alpha3": SUPPORTED_ALPHA3,
                "alpha2": SUPPORTED_ALPHA2,
                "hi": False,
                "forced": False,
            }
    return None


def derive_matches(video, media_title, subtitle_title):
    video = video or {}
    media_norm = _normalize(media_title)
    matches = []
    if video.get("kind") == "movie":
        title = _normalize(video.get("title"))
        if title and title in media_norm:
            matches.append("title")
        year = video.get("year")
        if year and str(year) in media_norm:
            matches.append("year")
    elif video.get("kind") == "episode":
        series = _normalize(video.get("series"))
        if series and series in media_norm:
            matches.append("series")
        season = video.get("season")
        if season is not None:
            for source in (subtitle_title, media_title):
                if _season_in_text(source, season) is True:
                    matches.append("season")
                    break
        if episode_explicitly_matches(subtitle_title, video.get("episode")):
            matches.append("episode")
        year = video.get("year")
        if year and str(year) in media_norm:
            matches.append("year")
    return matches


def compute_score(matches):
    if "episode" in matches and "year" in matches:
        return 100
    if "episode" in matches:
        return 95
    if "title" in matches and "year" in matches:
        return 100
    if "title" in matches or "series" in matches:
        return 90
    return 60


def _normalize(text):
    return re.sub(r"[\W_]+", " ", (_coerce_text(text) or "").lower()).strip()


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(int(delay_ms), 5000) / 1000.0)


def _headers_to_dict(headers):
    return {str(key).lower(): str(value) for key, value in dict(headers or {}).items()}


def _filename_from_headers(headers):
    disposition = (headers or {}).get("content-disposition", "")
    match = re.search(r'filename\*?=(?:[^\'";]+\'\')?"?([^";]+)"?', disposition)
    if not match:
        return ""
    return urllib.parse.unquote(match.group(1)).strip()


class FansubsProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru,en;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS):
        encoded = urllib.parse.urlencode(
            data, encoding="cp1251", errors="replace"
        ).encode("ascii")
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru,en;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), _headers_to_dict(response.headers)

    def search(self, video, languages, config):
        config = dict(config or {})
        language = _requested_russian_language(languages)
        if language is None:
            return []
        queries = build_queries(video)
        if not queries:
            return []

        results = []
        seen_media = set()
        for query in queries:
            _sleep(config)
            body, _headers = self._http_post(
                f"{BASE_URL}/search.php",
                {"query": query},
            )
            if _is_rate_limited(body):
                time.sleep(5)
                body, _headers = self._http_post(
                    f"{BASE_URL}/search.php",
                    {"query": query},
                )
            fresh_media = []
            for media in parse_search_results(body):
                if media["media_id"] in seen_media:
                    continue
                seen_media.add(media["media_id"])
                fresh_media.append(media)
                if len(fresh_media) >= MAX_CANDIDATES_PER_QUERY:
                    break
            for media in fresh_media:
                _sleep(config)
                try:
                    detail_body = self._http_get(media["detail_url"])
                except Exception:
                    continue
                details = parse_detail_page(detail_body, media["media_id"])
                for subtitle in details["subtitles"]:
                    if not self._subtitle_matches_video(subtitle, video):
                        continue
                    matches = derive_matches(video, details["title"], subtitle["title"])
                    score = compute_score(matches)
                    results.append(
                        {
                            "provider": PROVIDER_ID,
                            "id": f"fansubs-{subtitle['subtitle_id']}",
                            "language": language,
                            "release_info": _release_info(details["title"], subtitle),
                            "filename": (
                                f"fansubs.{subtitle['subtitle_id']}."
                                f"{subtitle['format']}"
                            ),
                            "matches": matches,
                            "score": score,
                            "score_without_hash": score,
                            "score_out_of": 100,
                            "hash_verifiable": False,
                            "hearing_impaired_verifiable": False,
                            "hearing_impaired": language["hi"],
                            "page_link": media["detail_url"],
                            "display": {
                                "source": "fansubs.ru",
                                "title": details["title"],
                                "subtitle": subtitle["title"],
                                "author": subtitle["author"],
                            },
                            "provider_payload": {
                                "provider": PROVIDER_ID,
                                "schema": 1,
                                "subtitle_id": subtitle["subtitle_id"],
                                "format": subtitle["format"],
                                "media_id": subtitle["media_id"],
                                "season": (video or {}).get("season"),
                                "episode": (video or {}).get("episode"),
                                "video": _video_payload(video),
                            },
                        }
                    )
            if results:
                break
        return sorted(results, key=lambda item: item.get("score", 0), reverse=True)

    def _subtitle_matches_video(self, subtitle, video):
        video = video or {}
        if video.get("kind") != "episode":
            return True
        if not episode_range_matches(subtitle["title"], video.get("episode")):
            return False
        season = video.get("season")
        if season is not None:
            for source in (subtitle.get("title"), subtitle.get("media_title")):
                if _season_in_text(source, season) is False:
                    return False
        return True

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        subtitle_id = str(payload.get("subtitle_id") or "").strip()
        if not subtitle_id:
            raise ValueError("fansubs download requires subtitle_id")
        body, headers = self._http_post(
            f"{BASE_URL}/base.php",
            {"srt": subtitle_id, "x": "0", "y": "0"},
        )
        filename = _filename_from_headers(headers) or (
            f"fansubs.{subtitle_id}.{payload.get('format') or 'srt'}"
        )
        episode = payload.get("episode")
        if episode is None:
            episode = (payload.get("video") or {}).get("episode")
        return extract_download(
            body,
            filename=filename,
            content_type=headers.get("content-type", ""),
            episode=episode,
        )


def _release_info(media_title, subtitle):
    parts = [media_title, subtitle.get("title"), subtitle.get("author")]
    return " ".join(part for part in parts if part)


def _video_payload(video):
    video = video or {}
    return {
        "kind": video.get("kind"),
        "title": video.get("title"),
        "series": video.get("series"),
        "year": video.get("year"),
        "season": video.get("season"),
        "episode": video.get("episode"),
    }
