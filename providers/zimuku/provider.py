"""Zimuku provider for the Bazarr+ Provider Hub catalog."""

import base64
import binascii
import functools
import hashlib
import html
import io
import json
import os
import random
import re
import struct
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar

_PROVIDER_DIR = os.path.dirname(__file__)
if _PROVIDER_DIR and _PROVIDER_DIR not in sys.path:
    sys.path.insert(0, _PROVIDER_DIR)

from yunsuo_templates import YUNSUO_CAPTCHA_TEMPLATE_ROWS

PROVIDER_ID = "zimuku"
BASE_URL = "https://srtku.com"
SEARCH_URL = f"{BASE_URL}/search"
HTTP_TIMEOUT_SECONDS = 30
YUNSUO_MAX_VERIFY_ATTEMPTS = 8
YUNSUO_COORDINATE_X_RANGE = (800, 1920)
YUNSUO_COORDINATE_Y_RANGE = (600, 1080)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
SUPPORTED_LANGUAGES = {"eng", "zho", "zho-CN", "zho-TW"}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_ITEM_RE = re.compile(
    r"<div\b(?=[^>]*\bclass=[\"'][^\"']*\bitem\b)[^>]*>(?P<body>.*?)(?=<div\b(?=[^>]*\bclass=[\"'][^\"']*\bitem\b)|</body>|</html>|\Z)",
    re.I | re.S,
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>(?P<text>.*?)</a>", re.I | re.S)
_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)
_SXXEXX_RE = re.compile(r"\bs0*(?P<season>\d{1,2})[\s._-]*e0*(?P<episode>\d{1,3})\b", re.I)
_X_EPISODE_RE = re.compile(r"\b0*(?P<season>\d{1,2})x0*(?P<episode>\d{1,3})\b", re.I)


@dataclass
class HttpResponse:
    status: int
    content: bytes
    headers: dict
    url: str


def string_to_hex(value):
    return "".join(hex(ord(char))[2:] for char in value or "")


def parse_yunsuo_challenge(body):
    text = _decode(body)
    location_match = re.search(
        r"self\.location\s*=\s*([\"'])(?P<prefix>.*?)\1\s*\+\s*stringToHex\(",
        text,
        re.I | re.S,
    )
    image_match = re.search(
        r"src\s*=\s*([\"'])data:image/(?P<mime>[^;\"']+);base64,(?P<image>.*?)\1",
        text,
        re.I | re.S,
    )
    if not location_match:
        return None
    return {
        "verify_prefix": html.unescape(location_match.group("prefix")),
        "image_b64": html.unescape(image_match.group("image")) if image_match else "",
        "image_mime": f"image/{image_match.group('mime')}" if image_match else "",
    }


def solve_yunsuo_captcha_image(image_bytes):
    rows = _read_bmp_rows(image_bytes)
    glyphs = _extract_yunsuo_glyphs(rows)
    if len(glyphs) != 5:
        raise ValueError("zimuku yunsuo solver expected 5 glyphs")
    templates = _yunsuo_digit_templates()
    solved = []
    for glyph in glyphs:
        matching = [template for template in templates if template[1] == glyph[0] and template[2] == glyph[1]]
        candidates = matching or templates
        best = min(candidates, key=lambda template: _yunsuo_bitmap_distance(glyph, template[1:]))
        score = _yunsuo_bitmap_distance(glyph, best[1:])
        if score > max(8, glyph[0] * glyph[1] // 3):
            raise ValueError("zimuku yunsuo solver confidence too low")
        solved.append(best[0])
    return "".join(solved)


def _read_bmp_rows(image_bytes):
    if not image_bytes or image_bytes[:2] != b"BM" or len(image_bytes) < 54:
        raise ValueError("zimuku yunsuo captcha is not a BMP image")
    pixel_offset = struct.unpack_from("<I", image_bytes, 10)[0]
    dib_size = struct.unpack_from("<I", image_bytes, 14)[0]
    if dib_size < 40:
        raise ValueError("zimuku yunsuo captcha BMP header is unsupported")
    width = struct.unpack_from("<i", image_bytes, 18)[0]
    height = struct.unpack_from("<i", image_bytes, 22)[0]
    bits_per_pixel = struct.unpack_from("<H", image_bytes, 28)[0]
    compression = struct.unpack_from("<I", image_bytes, 30)[0]
    if width <= 0 or height == 0 or bits_per_pixel != 24 or compression != 0:
        raise ValueError("zimuku yunsuo captcha BMP format is unsupported")
    row_count = abs(height)
    stride = ((width * 3 + 3) // 4) * 4
    if pixel_offset + stride * row_count > len(image_bytes):
        raise ValueError("zimuku yunsuo captcha BMP data is truncated")
    rows = []
    for y in range(row_count):
        source_y = row_count - 1 - y if height > 0 else y
        source = pixel_offset + source_y * stride
        row = []
        for x in range(width):
            blue, green, red = image_bytes[source + x * 3 : source + x * 3 + 3]
            row.append((red, green, blue))
        rows.append(row)
    return rows


def _extract_yunsuo_glyphs(rows):
    if not rows or not rows[0]:
        return []
    width = len(rows[0])
    column_counts = [
        sum(1 for y in range(len(rows)) if _is_yunsuo_digit_pixel(rows[y][x]))
        for x in range(width)
    ]
    runs = []
    start = None
    for index, count in enumerate(column_counts + [0]):
        if count and start is None:
            start = index
        elif start is not None and not count:
            if index - start >= 3:
                runs.append((start, index - 1))
            start = None
    glyphs = []
    for left, right in runs:
        ys = [
            y
            for y, row in enumerate(rows)
            for x in range(left, right + 1)
            if _is_yunsuo_digit_pixel(row[x])
        ]
        if not ys:
            continue
        top = min(ys)
        bottom = max(ys)
        glyph_width = right - left + 1
        bits = []
        for y in range(top, bottom + 1):
            value = 0
            for x in range(left, right + 1):
                value = (value << 1) | (1 if _is_yunsuo_digit_pixel(rows[y][x]) else 0)
            bits.append(value)
        glyphs.append((glyph_width, bottom - top + 1, tuple(bits)))
    return glyphs


def _is_yunsuo_digit_pixel(pixel):
    red, green, blue = pixel
    return green > red + 18 and green > blue + 18 and green < 190


@functools.lru_cache(maxsize=1)
def _yunsuo_digit_templates():
    templates = []
    for line in YUNSUO_CAPTCHA_TEMPLATE_ROWS.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 4:
            raise ValueError("zimuku yunsuo captcha template row is invalid")
        digit = parts[0]
        width = int(parts[1])
        height = int(parts[2])
        rows = tuple(int(value, 16) for value in parts[3:])
        if len(rows) != height:
            raise ValueError("zimuku yunsuo captcha template height is invalid")
        templates.append((digit, width, height, rows))
    return tuple(templates)


def _yunsuo_bitmap_distance(left, right):
    left_width, left_height, left_rows = left
    right_width, right_height, right_rows = right
    penalty = (abs(left_width - right_width) + abs(left_height - right_height)) * 100
    height = min(left_height, right_height)
    distance = sum((left_rows[index] ^ right_rows[index]).bit_count() for index in range(height))
    return penalty + distance


def parse_search_results(body, video):
    video = video or {}
    rows = []
    for match in _ITEM_RE.finditer(_decode(body)):
        anchor = _first_anchor(match.group("body"))
        if not anchor:
            continue
        title = _strip_tags(anchor["text"])
        if not title:
            continue
        if video.get("kind") == "episode" and not _season_matches(title, video.get("season")):
            continue
        rows.append(
            {
                "title": title,
                "url": _absolute_url(anchor["href"]),
                "year": _result_year(title, video),
            }
        )
    return rows


def parse_search_subtitle_rows(body, video):
    video = video or {}
    rows = []
    seen = set()
    for match in _ITEM_RE.finditer(_decode(body)):
        item = match.group("body")
        title = _item_title(item)
        if not _item_matches_video(title, video):
            continue
        year = _result_year(title, video)
        for row_match in _ROW_RE.finditer(item):
            row = row_match.group("body")
            anchor = _first_anchor(row)
            if not anchor or "/detail/" not in anchor["href"]:
                continue
            release = _extract_name(_strip_tags(anchor.get("title") or anchor["text"]))
            release = os.path.splitext(release)[0]
            if not release:
                continue
            for language in _languages_from_row(row, release):
                key = (anchor["href"], language)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "language": language,
                        "detail_url": _absolute_url(anchor["href"]),
                        "release_info": release,
                        "year": year,
                    }
                )
    return rows


def parse_episode_page(body, year=None):
    text = _decode(body)
    rows = []
    seen = set()
    for match in _ROW_RE.finditer(text):
        row = match.group("body")
        anchor = _first_anchor(row)
        if not anchor:
            continue
        release = _extract_name(_strip_tags(anchor["text"]))
        release = os.path.splitext(release)[0]
        if not release:
            continue
        for language in _languages_from_row(row, release):
            key = (anchor["href"], language)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "language": language,
                    "detail_url": _absolute_url(anchor["href"]),
                    "release_info": release,
                    "year": year,
                }
            )
    return rows


def extract_download(body, payload=None):
    payload = payload or {}
    # Reject broken responses up front: the download endpoint can answer with an empty
    # stream or an HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError("zimuku empty download response")
    if _is_html_body(body):
        raise ValueError("zimuku returned an HTML/error page instead of a subtitle")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host, which lists it and detects encoding. A Zimuku archive routinely
        # bundles several languages (CHS + CHT + ENG) for the same release, which the
        # host's episode-only pick cannot tell apart, so when we can list a zip we pin
        # the language-matched member; otherwise (rar, 7z, single language, or no match)
        # let the host pick the member by episode.
        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        member = _select_language_member(body, payload)
        if member is not None:
            archive["member"] = member
        else:
            archive["episode"] = payload.get("episode")
        return archive
    # Direct, non-archive subtitle body.
    return _content_payload(body, _format_from_filename(payload.get("filename")))


def derive_matches(video, item):
    video = video or {}
    release = item.get("release_info") if isinstance(item, dict) else item
    normalized = _normalize(release)
    matches = []
    year = item.get("year") if isinstance(item, dict) else None
    if video.get("year") and year and int(video.get("year")) == int(year):
        matches.append("year")
    season, episode = _episode_markers(normalized)
    try:
        video_season = int(video.get("season"))
        video_episode = int(video.get("episode"))
    except (TypeError, ValueError):
        video_season = video_episode = None
    if video.get("kind") == "episode":
        series_tokens = _tokens(video.get("series"))
        if series_tokens and all(token in normalized.split() for token in series_tokens):
            matches.append("series")
        if video_season is not None and season == video_season:
            matches.append("season")
        if video_episode is not None and episode == video_episode:
            matches.append("episode")
        if not video.get("year") and {"series", "season", "episode"}.issubset(matches):
            matches.append("year")
    else:
        title_tokens = _tokens(video.get("title"))
        if title_tokens and all(token in normalized.split() for token in title_tokens):
            matches.append("title")
    if _matches_source(video.get("source"), normalized):
        matches.append("source")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _normalize_release_group(release_group) in _normalize_release_group(release):
        matches.append("release_group")
    return matches


def compute_score(video, item):
    matches = set(derive_matches(video, item))
    if video.get("kind") == "episode" and {"series", "season", "episode"}.issubset(matches):
        return 100
    if video.get("kind") == "movie" and "title" in matches:
        return 90
    return 70 if matches else 40


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None stops urllib from following the redirect so the caller
        # receives the 30x response itself. The Yunsuo verification request must
        # not follow its 302 target, which on a one-use download URL would read
        # and discard the subtitle before download() can fetch it.
        return None


class ZimukuProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        cookie_processor = urllib.request.HTTPCookieProcessor(self._cookie_jar)
        self._opener = urllib.request.build_opener(cookie_processor)
        self._no_redirect_opener = urllib.request.build_opener(cookie_processor, _NoRedirectHandler())

    def search(self, video, languages, config):
        video = video or {}
        if video.get("kind") not in {"movie", "episode"}:
            return []
        requested = {_language_code(language) for language in languages or []}
        requested = {language for language in requested if language in SUPPORTED_LANGUAGES}
        if not requested:
            return []
        results = []
        seen = set()
        for query in _queries(video):
            search_url = f"{SEARCH_URL}?q={urllib.parse.quote_plus(query)}"
            search_response = self._bypass_get(search_url, config or {})
            for row in parse_search_subtitle_rows(search_response.content, video):
                if not _requested(row["language"], requested):
                    continue
                if not _row_matches_episode(video, row):
                    continue
                key = (row["detail_url"], row["language"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, row))
            if results:
                break
            for result_page in parse_search_results(search_response.content, video):
                _sleep(config)
                rows = parse_episode_page(self._bypass_get(result_page["url"], config or {}, referer=search_url).content, result_page["year"])
                for row in rows:
                    if not _requested(row["language"], requested):
                        continue
                    if not _row_matches_episode(video, row):
                        continue
                    key = (row["detail_url"], row["language"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, row))
            if results:
                break
        return sorted(results, key=lambda result: result["score"], reverse=True)

    def download(self, provider_payload, language, config):
        provider_payload = dict(provider_payload or {})
        config = dict(config or {})
        detail_url = provider_payload.get("detail_url")
        if not detail_url:
            raise ValueError("zimuku detail_url missing from provider payload")
        selected_language = provider_payload.get("language") or _language_code(language)
        detail = self._bypass_get(detail_url, config)
        down_page_url = _download_page_url(detail.content, detail_url)
        down_page = self._bypass_get(down_page_url, config, referer=detail_url)
        file_url = _final_download_url(down_page.content, down_page_url)
        file_response = self._bypass_get(file_url, config, referer=detail_url)
        filename = _filename_from_headers(file_response.headers) or provider_payload.get("filename")
        payload = dict(provider_payload)
        payload.update({"filename": filename, "language": selected_language})
        return extract_download(file_response.content, payload)

    def _result(self, video, item):
        video = video or {}
        matches = derive_matches(video, item)
        score = compute_score(video, item)
        language_payload = _language_payload(item["language"])
        filename = f"zimuku.{_slug(item.get('release_info'))}.{item['language']}.zip"
        # Store episode (and season) so download() can pass episode for host-side
        # member selection by the Provider Hub.
        episode = _coerce_int(video.get("episode")) if video.get("kind") == "episode" else None
        season = _coerce_int(video.get("season")) if video.get("kind") == "episode" else None
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "detail_url": item["detail_url"],
            "release_info": item["release_info"],
            "filename": filename,
            "language": item["language"],
            "year": item.get("year"),
            "season": season,
            "episode": episode,
        }
        return {
            "id": hashlib.sha1(f"{item['detail_url']}|{item['language']}".encode("utf-8")).hexdigest(),
            "provider": PROVIDER_ID,
            "language": language_payload,
            "release_info": item["release_info"],
            "title": item["release_info"],
            "score": score,
            "matches": matches,
            "hearing_impaired": False,
            "page_link": item["detail_url"],
            "provider_payload": payload,
            "display": item["release_info"],
        }

    def _bypass_get(self, url, config, referer=None):
        current_url = url
        for _attempt in range(YUNSUO_MAX_VERIFY_ATTEMPTS):
            response = self._http_get_response(current_url, referer=referer)
            challenge = parse_yunsuo_challenge(response.content)
            if challenge:
                code = self._solve_yunsuo_image(challenge, config or {})
                if not code:
                    raise ValueError("zimuku yunsuo captcha response required")
                self._set_cookie("srcurl", string_to_hex(_yunsuo_source_url(challenge, response.url)), response.url)
                verify_url = _challenge_verify_url(challenge, response.url, code)
                self._http_get_response(verify_url, referer=response.url, allow_redirects=False)
                current_url = url
                continue
            if response.status >= 400:
                raise RuntimeError(f"zimuku request failed with HTTP {response.status}: {current_url}")
            return response
        raise RuntimeError("zimuku yunsuo verification did not complete")

    def _solve_yunsuo_image(self, challenge, config):
        if config.get("captcha_response"):
            return str(config["captcha_response"])
        image_b64 = _coerce_text(challenge.get("image_b64"))
        if not image_b64:
            return _fallback_yunsuo_coordinate()
        if image_b64:
            try:
                return solve_yunsuo_captcha_image(base64.b64decode(image_b64, validate=True))
            except (binascii.Error, ValueError):
                pass
        solver_url = _coerce_text(config.get("captcha_solver_url"))
        if not solver_url:
            return None
        timeout = max(1, int(config.get("captcha_solver_timeout_ms") or 30000) / 1000)
        payload = {
            "provider": PROVIDER_ID,
            "type": "image_to_text",
            "image_b64": challenge.get("image_b64") or "",
            "image_mime": challenge.get("image_mime") or "image/bmp",
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = _coerce_text(config.get("captcha_solver_token"))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = self._raw_request(solver_url, method="POST", data=json.dumps(payload).encode("utf-8"), headers=headers, timeout=timeout)
        try:
            response = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("zimuku captcha solver returned invalid JSON") from error
        for key in ("response", "text", "token", "captcha_response"):
            if response.get(key):
                return str(response[key])
        return None

    def _http_get_response(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None, allow_redirects=True):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers, method="GET")
        opener = self._opener if allow_redirects else self._no_redirect_opener
        try:
            with opener.open(request, timeout=timeout) as response:
                return HttpResponse(
                    getattr(response, "status", 200),
                    response.read(),
                    dict(response.headers.items()),
                    response.geturl(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read(), dict(error.headers.items()), error.geturl())

    def _raw_request(self, url, method="GET", data=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def _set_cookie(self, name, value, url):
        host = urllib.parse.urlparse(url).hostname or urllib.parse.urlparse(BASE_URL).hostname
        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=host,
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        self._cookie_jar.set_cookie(cookie)


def _queries(video):
    title_key = "series" if video.get("kind") == "episode" else "title"
    titles = [video.get(title_key)]
    alt_key = "alternative_series" if video.get("kind") == "episode" else "alternative_titles"
    alternatives = video.get(alt_key) or []
    if isinstance(alternatives, str):
        titles.append(alternatives)
    elif isinstance(alternatives, (list, tuple)):
        titles.extend(alternatives)
    queries = []
    for title in titles:
        if not _coerce_text(title):
            continue
        if video.get("kind") == "episode" and video.get("season"):
            queries.append(f"{title}.S{int(video['season']):02d}")
        elif video.get("year"):
            queries.append(f"{title} {int(video['year'])}")
        else:
            queries.append(str(title))
    return queries


def _requested(language, requested):
    if language in requested:
        return True
    return language.startswith("zho") and "zho" in requested


def _language_code(language):
    if isinstance(language, str):
        return "zho-CN" if language == "zho-CN" else language
    if isinstance(language, dict):
        alpha3 = language.get("alpha3") or ""
        if alpha3 in {"zho-CN", "zho-TW"}:
            return alpha3
        if alpha3 == "zho" and (language.get("country") or "").upper() == "TW":
            return "zho-TW"
        if alpha3 == "zho" and (language.get("country") or "").upper() == "CN":
            return "zho-CN"
        return alpha3
    return ""


def _language_payload(language):
    if language == "zho-TW":
        return {"alpha3": "zho", "alpha2": "zh", "country": "TW"}
    if language == "zho-CN":
        return {"alpha3": "zho", "alpha2": "zh", "country": "CN"}
    if language == "zho":
        return {"alpha3": "zho", "alpha2": "zh"}
    return {"alpha3": "eng", "alpha2": "en"}


def _anchors(text):
    for match in _ANCHOR_RE.finditer(text or ""):
        tag = match.group(0)
        href = _attr(tag, "href")
        if href:
            yield {"href": href, "text": match.group("text"), "title": _attr(tag, "title")}


def _first_anchor(text):
    return next(_anchors(text), None)


def _item_title(item):
    title_match = re.search(
        r"<p\b(?=[^>]*\bclass=[\"'][^\"']*\btt\b)[^>]*>(?P<body>.*?)</p>",
        item or "",
        re.I | re.S,
    )
    candidates = [title_match.group("body")] if title_match else []
    candidates.append(item)
    for candidate in candidates:
        for anchor in _anchors(candidate):
            title = _strip_tags(anchor["text"])
            if title:
                return title
    return None


def _item_matches_video(title, video):
    if not title:
        return False
    video = video or {}
    if video.get("kind") == "episode" and not _season_matches(title, video.get("season")):
        return False
    try:
        expected_year = int(video.get("year")) if video.get("year") else None
        actual_year = int(_result_year(title, video)) if _result_year(title, video) else None
    except (TypeError, ValueError):
        expected_year = actual_year = None
    if expected_year and actual_year and expected_year != actual_year:
        return False
    item_tokens = _title_latin_tokens(title)
    if not item_tokens:
        return True
    for candidate in _title_candidates(video):
        candidate_tokens = _title_latin_tokens(candidate)
        if not candidate_tokens:
            continue
        if item_tokens == candidate_tokens:
            return True
        if len(candidate_tokens) > 1 and item_tokens[: len(candidate_tokens)] == candidate_tokens:
            return True
    return False


def _title_candidates(video):
    title_key = "series" if (video or {}).get("kind") == "episode" else "title"
    alt_key = "alternative_series" if (video or {}).get("kind") == "episode" else "alternative_titles"
    values = [video.get(title_key)] if video else []
    alternatives = (video or {}).get(alt_key) or []
    if isinstance(alternatives, str):
        values.append(alternatives)
    elif isinstance(alternatives, (list, tuple)):
        values.extend(alternatives)
    return [value for value in values if _coerce_text(value)]


def _title_latin_tokens(value):
    tokens = re.findall(r"[a-z0-9]+", _coerce_text(value).lower())
    return [token for token in tokens if not re.fullmatch(r"(?:19|20)\d{2}", token)]


def _season_matches(title, season):
    try:
        expected = int(season)
    except (TypeError, ValueError):
        return True
    actual = _season_from_title(title)
    return (actual or 1) == expected


def _season_from_title(title):
    match = re.search(r"第\s*(?P<season>[^季]+)\s*季", title or "")
    if not match:
        return None
    return _cn_to_int(match.group("season").strip())


def _cn_to_int(value):
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[1:], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(value)


def _result_year(title, video):
    years = [int(item) for item in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", title or "")]
    if not years:
        return video.get("year")
    if video.get("kind") == "episode" and video.get("season"):
        return years[0] - int(video.get("season")) + 1
    return years[0]


def _languages_from_row(row, release):
    found = []
    searchable = f"{row} {release}".lower()
    for tag_match in _IMG_RE.finditer(row):
        tag = tag_match.group(0)
        src_alt = f"{_attr(tag, 'src') or ''} {_attr(tag, 'alt') or ''}".lower()
        if "hongkong" in src_alt or "繁" in src_alt or "cht" in src_alt:
            found.append("zho-TW")
        if "china" in src_alt or "jollyroger" in src_alt or "简" in src_alt or "chs" in src_alt:
            found.append("zho-CN")
        if "english" in src_alt or "英文" in src_alt or re.search(r"\beng\b", src_alt):
            found.append("eng")
    # Always merge filename/text-derived languages with the flag-derived ones.
    # A row can carry a Chinese flag plus a bilingual release such as
    # *.CHS.ENG.srt, and gating the text scan behind "if not found" would drop
    # the English half so English requests never see a valid candidate.
    if any(token in searchable for token in ("cht", "big5", "繁体", "繁體")):
        found.append("zho-TW")
    if any(token in searchable for token in ("chs", "gb", "简体", "簡體")):
        found.append("zho-CN")
    if re.search(r"\beng(?:lish)?\b|英文", searchable):
        found.append("eng")
    deduped = []
    for language in found:
        if language not in deduped:
            deduped.append(language)
    return deduped


def _download_page_url(body, base_url):
    text = _decode(body)
    for match in _ANCHOR_RE.finditer(text):
        tag = match.group(0)
        if _attr(tag, "id") == "down1":
            return urllib.parse.urljoin(base_url, _attr(tag, "href"))
    raise ValueError("zimuku download page link was not found")


def _final_download_url(body, base_url):
    text = _decode(body)
    for match in _ANCHOR_RE.finditer(text):
        tag = match.group(0)
        if (_attr(tag, "rel") or "").lower() == "nofollow":
            return urllib.parse.urljoin(base_url, _attr(tag, "href"))
    raise ValueError("zimuku final download link was not found")


def _filename_from_headers(headers):
    value = ""
    for key, header_value in (headers or {}).items():
        if key.lower() == "content-disposition":
            value = header_value
            break
    match = re.search(r"filename\*?=(?:UTF-8''|[\"']?)(?P<name>[^\"';]+)", value or "", re.I)
    return urllib.parse.unquote(match.group("name")) if match else None


def _extract_name(name):
    name, suffix = os.path.splitext(name or "")
    chinese = [match.start(0) for match in re.finditer(r"[\u4e00-\u9fff]", name)]
    latin = [match.start(0) for match in re.finditer(r"[a-zA-Z0-9]", name)]
    if not latin:
        return ""
    first_latin, last_latin = latin[0], latin[-1]
    first_chinese = chinese[0] if chinese else -1
    last_chinese = chinese[-1] if chinese else -1
    if last_chinese < first_latin:
        cleaned = name[first_latin:]
    elif last_latin < first_chinese:
        cleaned = name[:first_chinese]
    else:
        best = (0, 0)
        start = None
        for index, char in enumerate(name):
            if re.match(r"[a-zA-Z0-9 ._\-\[\]()]+", char):
                if start is None:
                    start = index
            elif start is not None:
                if index - start > best[1] - best[0]:
                    best = (start, index)
                start = None
        if start is not None and len(name) - start > best[1] - best[0]:
            best = (start, len(name))
        cleaned = name[best[0]:best[1]]
    return cleaned.strip(" ._-") + suffix


def _absolute_url(value):
    return _quote_url(_raw_absolute_url(value))


def _raw_absolute_url(value):
    value = html.unescape((value or "").strip())
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"{BASE_URL}{value}"
    return f"{BASE_URL}/{value.lstrip('/')}"


def _yunsuo_source_url(challenge, fallback_url):
    prefix = _coerce_text((challenge or {}).get("verify_prefix")) or ""
    source, marker, _tail = prefix.partition("&security_verify_img=")
    if marker and source:
        return urllib.parse.urljoin(fallback_url, html.unescape(source.strip()))
    return fallback_url


def _challenge_verify_url(challenge, response_url, code):
    prefix = _coerce_text((challenge or {}).get("verify_prefix"))
    return _quote_url(urllib.parse.urljoin(response_url, f"{prefix}{string_to_hex(code)}"))


def _fallback_yunsuo_coordinate():
    x = random.randrange(*YUNSUO_COORDINATE_X_RANGE)
    y = random.randrange(*YUNSUO_COORDINATE_Y_RANGE)
    return f"{x},{y}"


def _quote_url(value):
    parts = urllib.parse.urlsplit(value)
    path = urllib.parse.quote(parts.path, safe="/%")
    query = urllib.parse.quote(parts.query, safe="=&%+")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _attr(tag, name):
    match = re.search(rf"\b{name}\s*=\s*([\"'])(?P<value>.*?)\1", tag or "", re.I | re.S)
    return html.unescape(match.group("value")) if match else None


def _strip_tags(value):
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _decode(body):
    if isinstance(body, str):
        return body
    return (body or b"").decode("utf-8", errors="replace")


def _coerce_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize(value):
    value = unicodedata.normalize("NFKD", _coerce_text(value)).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM_RE.sub(" ", value.lower()).strip()


def _tokens(value):
    return _normalize(value).split()


def _slug(value):
    slug = "-".join(_tokens(value))[:90]
    return slug or "subtitle"


def _episode_markers(normalized):
    for pattern in (_SXXEXX_RE, _X_EPISODE_RE):
        match = pattern.search(normalized or "")
        if match:
            return int(match.group("season")), int(match.group("episode"))
    return None, None


def _row_matches_episode(video, row):
    # Episode searches can surface season packs or sibling episodes (S01E02,
    # S01E03) under the same season result. When a row carries an explicit
    # SxxEyy marker it must match the requested season and episode, otherwise a
    # request for S01E01 would offer the wrong-episode subtitle for download.
    video = video or {}
    if video.get("kind") != "episode":
        return True
    try:
        video_episode = int(video.get("episode"))
    except (TypeError, ValueError):
        return True
    season, episode = _episode_markers(_normalize(row.get("release_info")))
    if episode is None:
        return True
    if episode != video_episode:
        return False
    try:
        video_season = int(video.get("season"))
    except (TypeError, ValueError):
        return True
    if season is not None and season != video_season:
        return False
    return True


def _matches_source(value, normalized_text):
    if not _coerce_text(value):
        return False
    token = _normalize(value)
    aliases = {
        "blu ray": {"bluray", "brrip", "bdrip"},
        "bluray": {"bluray", "brrip", "bdrip"},
        "web": {"web", "webdl", "webrip"},
        "hdtv": {"hdtv"},
    }
    return token in normalized_text.split() or bool(aliases.get(token, set()) & set(normalized_text.split()))


def _normalize_release_group(value):
    return re.sub(r"[^a-z0-9]+", "", (_coerce_text(value) or "").lower())


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _is_7z_archive(body):
    return bool(body) and body.startswith(b"7z\xbc\xaf\x27\x1c")


def _is_archive_body(body):
    # The host extracts zip, rar, and 7z. Detect all three by signature so the raw
    # archive bytes are handed back for host-side member selection and encoding.
    return (
        _is_rar_archive(body)
        or _is_7z_archive(body)
        or zipfile.is_zipfile(io.BytesIO(body or b""))
    )


def _select_language_member(body, payload):
    # Pin the member matching the requested language. Listing only, no extraction or
    # decoding: the host reads the named member and runs chardet. Returns None for rar
    # or 7z (not stdlib-listable), a single-language archive, or no language match, so the
    # caller falls back to host-side episode selection.
    payload = payload or {}
    language = _language_code(payload.get("language"))
    if not language or not zipfile.is_zipfile(io.BytesIO(body or b"")):
        return None
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = [
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and _subtitle_extension(name)
            and not os.path.basename(name).startswith(".")
        ]
    tagged = {name: _language_from_subtitle_filename(name) for name in members}
    present = {lang for lang in tagged.values() if lang}
    # Only step in when the archive actually mixes languages and we requested one of them;
    # a single-language archive leaves nothing for us to disambiguate, so defer to the host.
    if len(present) < 2 or language not in present:
        return None
    pool = [name for name in members if tagged[name] == language]
    # A season pack carries several episodes per language; the host cannot combine episode
    # and language, so resolve the episode here as well before pinning a single member.
    season = _coerce_int(payload.get("season"))
    episode = _coerce_int(payload.get("episode"))
    if season is not None and episode is not None:
        episode_pool = [
            name
            for name in pool
            if _file_matches_episode(_normalize(os.path.basename(name)), season, episode)
        ]
        if episode_pool:
            return episode_pool[0]
        # Episode markers present but none matches the requested one: defer to the host.
        if any(_file_has_episode_marker(_normalize(os.path.basename(name))) for name in pool):
            return None
    return pool[0] if len(pool) == 1 else None


def _language_from_subtitle_filename(name):
    # Tag a member by the SAME markers _languages_from_row uses to classify a Zimuku row,
    # so member selection agrees with the row's declared language. Romanized markers are
    # matched as delimited tokens (so "gb"/"chs"/"cht" can't hit a substring like "webgb"),
    # and the script characters (简/繁) are unambiguous. "gb" is Zimuku's Simplified-Chinese
    # (GB encoding) marker, not British English; Zimuku tags English as eng/english/英文.
    # Bilingual *.CHS.ENG. members are tagged by their Chinese script (checked first) so an
    # English request does not steal a Chinese-first release.
    compact = " " + _normalize(os.path.basename(name or "")) + " "
    if any(token in compact for token in (" cht ", " big5 ")) or any(
        marker in (name or "") for marker in ("繁", "繁體", "繁体")
    ):
        return "zho-TW"
    if any(token in compact for token in (" chs ", " gb ")) or any(
        marker in (name or "") for marker in ("简", "簡", "简体", "簡體")
    ):
        return "zho-CN"
    if any(token in compact for token in (" eng ", " english ")) or "英文" in (name or ""):
        return "eng"
    return None


def _file_matches_episode(normalized_name, season, episode):
    # _normalize collapses separators to spaces, so compare whole tokens. The bare
    # "{season}{episode:02d}" form (S01E01 written as "101") is matched against split
    # tokens; a substring match would read the "720" in "720p" as S07E20.
    compact = normalized_name.lower()
    # SxxExx, tolerating the separator _normalize leaves between season and episode
    # (S01.E02 / S01 E02 normalize to "s01 e02"), as well as contiguous S01E02.
    if re.search(rf"s0*{season}[\s._-]*e0*{episode}(?!\d)", compact):
        return True
    tokens = compact.split()
    if f"{season}x{episode:02d}" in tokens or f"{season}x{episode}" in tokens:
        return True
    return f"{season}{episode:02d}" in tokens


def _file_has_episode_marker(normalized_name):
    compact = normalized_name.lower()
    return bool(
        re.search(r"\bs\d{1,2}[\s._-]*e\d{1,3}\b", compact)
        or re.search(r"\b\d{1,2}x\d{1,3}\b", compact)
        or any(token.isdigit() and len(token) == 3 for token in compact.split())
    )


def _is_html_body(body):
    if not body:
        return False
    head = body[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
        or head.startswith(b"<!--")
        or b"<body" in head
        or b"<head" in head
    )


def _content_payload(content, subtitle_format="srt"):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    content = _normalize_line_endings(content or b"")
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format or "srt",
        "empty": False,
    }


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    return "application/x-subrip"


def _normalize_line_endings(content):
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _decode_payload_text(payload):
    return base64.b64decode(payload["content_b64"]).decode("utf-8", errors="replace")


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000)
