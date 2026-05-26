"""SubHD provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
import html
import io
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

PROVIDER_ID = "subhd"
BASE_URL = "https://subhd.tv"
API_DOWN_URL = f"{BASE_URL}/api/sub/down"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 15
MAX_CANDIDATES_PER_QUERY = 12
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt")


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
    if kind == "episode":
        series = (_coerce_text(video.get("series")) or "").strip()
        season = video.get("season")
        episode = video.get("episode")
        if not series or season is None or episode is None:
            return []
        try:
            tag = f"S{int(season):02d}E{int(episode):02d}"
        except (TypeError, ValueError):
            return []
        return [f"{series} {tag}", series]
    if kind == "movie":
        title = (_coerce_text(video.get("title")) or "").strip()
        if not title:
            return []
        year = video.get("year")
        if year:
            return [f"{title} {year}", title]
        return [title]
    return []


_TAG_RE = re.compile(rb"<[^>]+>")
_WS_BYTES_RE = re.compile(rb"\s+")
_WS_RE = re.compile(r"\s+")
_CARD_SPLIT_RE = re.compile(rb'<div class="bg-white shadow-sm rounded-3 mb-4">')
_MEDIA_LINK_RE = re.compile(rb"href=['\"](?P<href>/d/(?P<id>\d+))['\"]", re.I)
_SUB_LINK_RE = re.compile(
    rb"<a\b(?P<attrs>[^>]*href=['\"](?P<href>/a/(?P<id>[A-Za-z0-9]+))['\"][^>]*)>"
    rb"(?P<body>.*?)</a>",
    re.I | re.S,
)
_LANG_LABEL_RE = re.compile(rb'<span class="p-1 fw-bold">(?P<label>.*?)</span>', re.I | re.S)
_FORMAT_RE = re.compile(rb'<span class="p-1 text-secondary">(?P<format>.*?)</span>', re.I | re.S)
_DOWNLOAD_COUNT_RE = re.compile(
    rb'bi bi-download.*?</svg>\s*<span class="align-text-top me-3">(?P<count>\d+)</span>',
    re.I | re.S,
)
_RELEASE_RE = re.compile(rb'<div class="f16 fw-bold mb-2">(?P<release>.*?)</div>', re.I | re.S)
_DOWN_LINK_RE = re.compile(
    rb'<a class="btn btn-danger down"\s+sid="(?P<sid>[A-Za-z0-9]+)"\s+href="(?P<href>/down/[A-Za-z0-9]+)"',
    re.I,
)
_DOWN_BUTTON_RE = re.compile(rb'<button class="btn btn-danger down"\s+sid="(?P<sid>[A-Za-z0-9]+)"', re.I)
_FILE_RE = re.compile(rb'data-filename="(?P<filename>[^"]+)"', re.I)
_TITLE_EN_RE = re.compile(rb"<b>Title</b>\s*[:\xef\xbc\x9a]\s*(?P<title>.*?)<br>", re.I | re.S)
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def _decode(data):
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _strip_tags(data):
    stripped = _TAG_RE.sub(b"", data or b"")
    stripped = _WS_BYTES_RE.sub(b" ", stripped).strip()
    return html.unescape(_decode(stripped))


def _clean_text(value):
    return _WS_RE.sub(" ", html.unescape(_decode(value))).strip()


def _absolute_url(path):
    if not path:
        return None
    value = _decode(path)
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return urllib.parse.urljoin(BASE_URL, value)


_LABEL_TO_ALPHA3 = {
    "\u7b80\u4f53": "zho",
    "\u7e41\u4f53": "zho",
    "\u82f1\u8bed": "eng",
    "\u6cd5\u8bed": "fra",
    "\u897f\u73ed\u7259\u8bed": "spa",
    "\u65e5\u8bed": "jpn",
    "\u97e9\u8bed": "kor",
    "\u5fb7\u8bed": "deu",
    "\u963f\u62c9\u4f2f\u8bed": "ara",
    "\u8461\u8404\u7259\u8bed": "por",
    "\u610f\u5927\u5229\u8bed": "ita",
    "\u8377\u5170\u8bed": "nld",
    "\u571f\u8033\u5176\u8bed": "tur",
    "\u6ce2\u5170\u8bed": "pol",
    "\u745e\u5178\u8bed": "swe",
}
_CHINESE_BILINGUAL_LABELS = {
    "\u53cc\u8bed",
    "\u4e2d\u82f1",
    "\u4e2d\u82f1\u53cc\u8bed",
    "\u7b80\u82f1",
    "\u7e41\u82f1",
}
_ALPHA3_TO_ALPHA2 = {
    "zho": "zh",
    "eng": "en",
    "fra": "fr",
    "spa": "es",
    "jpn": "ja",
    "kor": "ko",
    "deu": "de",
    "ara": "ar",
    "por": "pt",
    "ita": "it",
    "nld": "nl",
    "tur": "tr",
    "pol": "pl",
    "swe": "sv",
}
_ALPHA2_TO_ALPHA3 = {value: key for key, value in _ALPHA3_TO_ALPHA2.items()}


def _languages_from_block(block):
    labels = [_strip_tags(match.group("label")) for match in _LANG_LABEL_RE.finditer(block or b"")]
    if any(label in _CHINESE_BILINGUAL_LABELS for label in labels):
        return ["zho"]
    result = []
    for label in labels:
        alpha3 = _LABEL_TO_ALPHA3.get(label)
        if alpha3 and alpha3 not in result:
            result.append(alpha3)
    return result


def _format_from_block(block):
    match = _FORMAT_RE.search(block or b"")
    if not match:
        return "srt"
    value = _strip_tags(match.group("format")).lower()
    return value if value in {"srt", "ass", "ssa", "vtt"} else "srt"


def _download_count(block):
    match = _DOWNLOAD_COUNT_RE.search(block or b"")
    return int(match.group("count")) if match else 0


def parse_search_results(html_bytes):
    if not html_bytes:
        return []
    rows = []
    seen = set()
    for block in _CARD_SPLIT_RE.split(html_bytes)[1:]:
        media_match = _MEDIA_LINK_RE.search(block)
        sub_links = list(_SUB_LINK_RE.finditer(block))
        if not media_match or len(sub_links) < 2:
            continue
        title_link = sub_links[0]
        release_link = sub_links[1]
        subtitle_id = _decode(release_link.group("id"))
        if subtitle_id in seen:
            continue
        seen.add(subtitle_id)
        release_info = _strip_tags(release_link.group("body"))
        rows.append(
            {
                "subtitle_id": subtitle_id,
                "media_id": _decode(media_match.group("id")),
                "media_title": _strip_tags(title_link.group("body")),
                "release_info": release_info,
                "languages": _languages_from_block(block),
                "format": _format_from_block(block),
                "download_count": _download_count(block),
                "detail_url": _absolute_url(release_link.group("href")),
            }
        )
    return rows


def parse_detail_page(html_bytes, detail_url):
    if not html_bytes:
        return {}
    down_match = _DOWN_LINK_RE.search(html_bytes)
    button_match = _DOWN_BUTTON_RE.search(html_bytes)
    sid = None
    href = None
    if down_match:
        sid = _decode(down_match.group("sid"))
        href = _decode(down_match.group("href"))
    elif button_match:
        sid = _decode(button_match.group("sid"))
        href = f"/down/{sid}"
    release_match = _RELEASE_RE.search(html_bytes)
    title_match = _TITLE_EN_RE.search(html_bytes)
    files = [_decode(match.group("filename")) for match in _FILE_RE.finditer(html_bytes)]
    return {
        "subtitle_id": sid,
        "media_title": _strip_tags(title_match.group("title")) if title_match else "",
        "release_info": _strip_tags(release_match.group("release")) if release_match else "",
        "languages": _languages_from_block(html_bytes),
        "format": _format_from_block(html_bytes),
        "download_count": _download_count(html_bytes),
        "files": files,
        "detail_url": detail_url,
        "download_url": _absolute_url(href),
    }


def parse_download_response(body):
    try:
        data = json.loads(_decode(body))
    except json.JSONDecodeError as exc:
        raise ValueError("subhd download API returned invalid JSON") from exc
    if not data.get("success"):
        raise ValueError(data.get("msg") or "subhd download API failed")
    if data.get("pass") is False:
        raise ValueError("subhd captcha required")
    url = data.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("subhd download API returned no file URL")
    return url


def _normalize(text):
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", _coerce_text(text) or "")
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _tokens(text):
    return [token for token in _normalize(text).split(" ") if token]


def _episode_tag_in(text, season, episode):
    normalized = _normalize(text)
    return re.search(rf"\bs0*{int(season)}e0*{int(episode)}\b", normalized) is not None


def derive_matches(video, candidate_title):
    if not video:
        return []
    candidate_tokens = set(_tokens(candidate_title))
    matches = []
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
        if season is not None and f"s{season:02d}" in _normalize(candidate_title):
            matches.append("season")
        if season is not None and episode is not None and _episode_tag_in(candidate_title, season, episode):
            matches.append("episode")
    release_tokens = {token for token in re.split(r"[^A-Za-z0-9]+", candidate_title.lower()) if token}
    for key in ("source", "resolution", "video_codec", "audio_codec", "release_group"):
        value = _coerce_text(video.get(key))
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", str(value or "").lower()) if part]
        if parts and all(part in release_tokens for part in parts):
            matches.append(key)
    return matches


def compute_score(video, candidate_title):
    matches = derive_matches(video, candidate_title)
    if (video or {}).get("kind") == "movie":
        if "title" in matches and "year" in matches:
            return 100
        if "title" in matches:
            return 90
        return 60
    if (video or {}).get("kind") == "episode":
        if "series" in matches and "episode" in matches:
            return 95
        if "series" in matches:
            return 85
        return 60
    return 60


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    if alpha3:
        return alpha3
    alpha2 = (language.get("alpha2") or "").lower()
    return _ALPHA2_TO_ALPHA3.get(alpha2)


def _alpha2_for_alpha3(alpha3):
    return _ALPHA3_TO_ALPHA2.get(alpha3)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


class SubHDProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def _http_post_json(self, url, payload, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        config = dict(config or {})
        requested = {_alpha3_for_language(lang) for lang in languages or []}
        requested.discard(None)
        if not requested:
            return []
        results = []
        seen = set()
        for query in build_queries(video):
            url = f"{BASE_URL}/search/{urllib.parse.quote(query, safe='')}"
            _sleep(config)
            rows = parse_search_results(self._http_get(url))
            for row in rows[:MAX_CANDIDATES_PER_QUERY]:
                if row["languages"] and not any(lang in requested for lang in row["languages"]):
                    continue
                _sleep(config)
                try:
                    detail = parse_detail_page(
                        self._http_get(row["detail_url"], referer=url),
                        row["detail_url"],
                    )
                except Exception:
                    continue
                merged = dict(row)
                merged.update({key: value for key, value in detail.items() if value})
                usable_languages = [lang for lang in merged.get("languages", []) if lang in requested]
                if not usable_languages:
                    continue
                for alpha3 in usable_languages:
                    key = (merged["subtitle_id"], alpha3)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, merged, alpha3))
            if results:
                break
        return results

    def _result(self, video, entry, alpha3):
        alpha2 = _alpha2_for_alpha3(alpha3)
        candidate_title = f"{entry.get('media_title', '')} {entry.get('release_info', '')}"
        score = compute_score(video, candidate_title)
        subtitle_id = entry["subtitle_id"]
        fmt = entry.get("format") or "srt"
        return {
            "provider": PROVIDER_ID,
            "id": f"subhd-{subtitle_id}-{alpha3}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": entry.get("release_info") or entry.get("media_title"),
            "filename": f"subhd.{subtitle_id}.{alpha2}.{fmt}",
            "matches": derive_matches(video, candidate_title),
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": entry["detail_url"],
            "display": {
                "source": "subhd",
                "title": entry.get("media_title"),
                "release": entry.get("release_info"),
                "detail_url": entry["detail_url"],
                "downloads": entry.get("download_count", 0),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": subtitle_id,
                "detail_url": entry["detail_url"],
                "download_url": entry.get("download_url") or f"{BASE_URL}/down/{subtitle_id}",
                "language": alpha3,
                "format": fmt,
                "release_info": entry.get("release_info"),
            },
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        subtitle_id = payload.get("subtitle_id")
        detail_url = payload.get("detail_url")
        download_url = payload.get("download_url")
        if not subtitle_id or not detail_url or not download_url:
            raise ValueError("subhd download requires subtitle_id, detail_url, and download_url")
        self._http_get(detail_url)
        self._http_get(download_url, referer=detail_url)
        api_body = self._http_post_json(
            API_DOWN_URL,
            {"sid": subtitle_id, "cap": ""},
            referer=download_url,
        )
        final_url = parse_download_response(api_body)
        body = self._http_get(final_url, referer=download_url)
        fmt = _format_from_url(final_url, payload.get("format"))
        body, fmt = _extract_best_subtitle(body, fmt)
        return _content_payload(body, fmt)


def _format_from_url(url, fallback=None):
    suffix = urllib.parse.urlparse(url or "").path.rsplit(".", 1)[-1].lower()
    if suffix in {"srt", "ass", "ssa", "vtt", "zip"}:
        return suffix
    return (fallback or "srt").lower()


def _extract_best_subtitle(body, fmt):
    if fmt != "zip" and not (body or b"").startswith(b"PK\x03\x04"):
        return body, fmt if fmt in {"srt", "ass", "ssa", "vtt"} else "srt"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(SUBTITLE_EXTENSIONS)]
        if not names:
            raise ValueError("subhd archive contained no subtitle files")
        names.sort(key=lambda name: (not name.lower().endswith(".srt"), len(name), name.lower()))
        name = names[0]
        content = archive.read(name)
        if not content:
            raise ValueError("subhd downloaded empty subtitle")
        return content, name.rsplit(".", 1)[-1].lower()


def _content_payload(body, fmt):
    if not body:
        raise ValueError("subhd downloaded empty subtitle")
    try:
        body.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = "latin-1"
    return {
        "content_b64": _base64.b64encode(body).decode("ascii"),
        "content_sha256": _hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(fmt):
    if fmt == "ass":
        return "text/x-ssa"
    if fmt == "ssa":
        return "text/x-ssa"
    if fmt == "vtt":
        return "text/vtt"
    return "application/x-subrip"
