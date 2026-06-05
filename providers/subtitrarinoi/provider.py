"""Subtitrari Noi provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
import html
import io
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "subtitrarinoi"
BASE_URL = "https://www.subtitrari-noi.ro"
API_URL = f"{BASE_URL}/paginare_filme.php"
HTTP_TIMEOUT_SECONDS = 15
SUPPORTED_LANGUAGES = {"ron": "ro"}
ALPHA2_TO_ALPHA3 = {"ro": "ron"}
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ssa", ".ass", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_ALIASES = {
    "dc s legends of tomorrow": "Legends of Tomorrow",
    "marvel s jessica jones": "Jessica Jones",
}
_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.I | re.S)
_BOLD_COMMENT_RE = re.compile(
    r"<div\b[^>]*style=['\"][^'\"]*font-weight\s*:\s*bold[^'\"]*font-style\s*:\s*italic[^'\"]*['\"][^>]*>(?P<body>.*?)</div>",
    re.I | re.S,
)
_DOWNLOAD_RE = re.compile(
    r"<p\b[^>]*class=['\"][^'\"]*\bbuton\b[^'\"]*['\"][^>]*>.*?<a\b(?P<attrs>[^>]*)>",
    re.I | re.S,
)
_FIELD_RE = re.compile(r"<p\b[^>]*>(?P<body>.*?)</p>", re.I | re.S)
_IMDB_RE = re.compile(r"imdb\.com/title/(?P<id>tt\d+)/?", re.I)
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_ROUND_RE = re.compile(r"<div\b[^>]*id=['\"]round['\"][^>]*>", re.I)
_SEASON_RE = re.compile(r"\b(?:s|season|sezonul)\s*0*(?P<season>\d{1,2})\b", re.I)
_SEASON_RANGE_RE = re.compile(r"\bsezoanele\s+(?P<start>\d{1,2})\s*[-,]\s*(?P<end>\d{1,2})\b", re.I)
_EPISODE_RANGE_RE = re.compile(
    r"\b(?:ep\.?|episod(?:ul|ele)?|episoadele)\s*0*(?P<start>\d{1,3})(?:\s*[-,]\s*0*(?P<end>\d{1,3}))?",
    re.I,
)
_SXXEXX_RE = re.compile(r"\bs(?P<season>\d{1,2})e(?P<episode>\d{1,3})\b", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_YEAR_RE = re.compile(r"^(?P<title>.*?)\s*\((?P<year>\d{4})\)\s*$")
_WS_RE = re.compile(r"\s+")


def build_query_params(video):
    video = video or {}
    query = _imdb_query(video) or _search_title(video)
    return {
        "search_q": "1",
        "tip": "2",
        "an": "Toti anii",
        "gen": "Toate",
        "cautare": query,
        "query_q": query,
    }


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    starts = [match.start() for match in _ROUND_RE.finditer(text)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        chunk = text[start:end]
        title_link = _first_content_main_link(chunk)
        download_href = _download_href(chunk)
        if not title_link or not download_href:
            continue
        title, year = _split_title_year(title_link["label"])
        download_url = _download_url(download_href)
        subtitle_id = _subtitle_id_from_download(download_href) or _subtitle_id_from_page(title_link["href"])
        rows.append(
            {
                "subtitle_id": subtitle_id,
                "title": title,
                "year": year,
                "imdb_id": _imdb_id(chunk),
                "download_url": download_url,
                "page_link": _absolute_url(title_link["href"]),
                "filename": os.path.basename(urllib.parse.urlparse(download_url).path) or f"subtitrarinoi-{subtitle_id}.zip",
                "uploader": _field_value(chunk, "Uploader"),
                "translator": _field_value(chunk, "Traducator"),
                "download_count": _int_from_text(_field_value(chunk, "Descarcari")),
                "comments": _comments(chunk),
            }
        )
    return rows


def derive_matches(video, row):
    video = video or {}
    row = row or {}
    matches = []
    if video.get("kind") == "movie":
        if _title_matches(video.get("title"), row.get("title")):
            matches.append("title")
        if _safe_int(video.get("year")) is not None and _safe_int(video.get("year")) == row.get("year"):
            matches.append("year")
        if _clean_imdb(video.get("imdb_id")) and _clean_imdb(video.get("imdb_id")) == row.get("imdb_id"):
            matches.append("imdb_id")
    elif video.get("kind") == "episode":
        if _title_matches(video.get("series"), _strip_season_suffix(row.get("title"))):
            matches.append("series")
        if _clean_imdb(video.get("series_imdb_id")) and _clean_imdb(video.get("series_imdb_id")) == row.get("imdb_id"):
            matches.append("imdb_id")
        if _season_matches(row.get("comments"), video.get("season")):
            matches.append("season")
        if {"imdb_id", "season"} <= set(matches) and _episode_matches(row.get("comments"), video.get("episode")):
            matches.append("episode")
    matches.extend(_release_matches(video, row.get("comments")))
    return list(dict.fromkeys(matches))


class SubtitrariNoiProvider:
    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.7,en;q=0.6",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _http_get(self, url, timeout=30, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/zip,application/octet-stream,text/plain,text/html;q=0.8,*/*;q=0.5",
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.7,en;q=0.6",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        if (video or {}).get("kind") not in {"movie", "episode"}:
            return []
        variants = _requested_variants(languages)
        if not variants:
            return []
        query = build_query_params(video)
        if not query.get("cautare"):
            return []
        config = dict(config or {})
        _sleep(config)
        rows = parse_search_results(self._http_post(API_URL, query, referer=BASE_URL))
        results = []
        seen = set()
        for row in rows:
            matches = derive_matches(video, row)
            if not _row_matches_video(video, row, matches):
                continue
            for variant in variants:
                key = (row["subtitle_id"], variant["hi"], variant["forced"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, row, variant, matches))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, row, language, matches):
        score = 45
        score += 20 if ("title" in matches or "series" in matches) else 0
        score += 15 if ("year" in matches or "season" in matches) else 0
        score += 15 if ("imdb_id" in matches or "episode" in matches) else 0
        score += 5 if "release_group" in matches else 0
        score += 3 if "source" in matches else 0
        score += 2 if "resolution" in matches else 0
        variant_suffix = _variant_suffix(language)
        filename = row.get("filename") or f"subtitrarinoi.{_slug(row.get('title'))}.{variant_suffix}.zip"
        provider_payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "url": row["download_url"],
            "page_url": row["page_link"],
            "filename": filename,
            "language": "ron",
            "hi": bool(language.get("hi")),
            "forced": bool(language.get("forced")),
            "release_info": row.get("comments") or "",
        }
        if (video or {}).get("kind") == "episode":
            provider_payload["season"] = _safe_int(video.get("season"))
            provider_payload["episode"] = _safe_int(video.get("episode"))
        return {
            "provider": PROVIDER_ID,
            "id": f"subtitrarinoi-{row['subtitle_id']}-{variant_suffix}",
            "language": dict(language),
            "release_info": row.get("comments") or row.get("title") or "",
            "filename": filename,
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": bool(language.get("hi")),
            "page_link": row["page_link"],
            "display": {
                "source": "subtitrari-noi.ro",
                "title": row.get("title"),
                "downloads": row.get("download_count"),
                "uploader": row.get("uploader"),
            },
            "provider_payload": provider_payload,
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("url")
        if not url:
            raise ValueError("subtitrarinoi download requires url")
        body = self._http_get(url, timeout=30, referer=API_URL)
        return extract_download(body, payload)


def extract_download(body, payload=None):
    payload = dict(payload or {})
    filename = payload.get("filename") or ""
    # Reject broken responses up front: the download endpoint can answer with an empty
    # stream or an HTML/error page that would otherwise look like a successful download.
    if not body:
        raise ValueError("subtitrarinoi download did not return a supported subtitle file")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host, which lists it, picks the member by episode, and detects encoding.
        return {
            "archive_b64": _base64.b64encode(body).decode("ascii"),
            "archive_sha256": _hashlib.sha256(body).hexdigest(),
            "episode": payload.get("episode"),
        }
    subtitle_format = _subtitle_extension(filename)
    if not subtitle_format or _looks_like_unavailable_text(body):
        raise ValueError("subtitrarinoi download did not return a supported subtitle file")
    # Direct, non-archive subtitle body.
    return _content_payload(body, subtitle_format)


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _first_content_main_link(chunk):
    match = re.search(r"<div\b[^>]*id=['\"]content-main['\"][^>]*>(?P<body>.*?)</div>", chunk or "", re.I | re.S)
    if not match:
        return None
    anchor = _ANCHOR_RE.search(match.group("body"))
    if not anchor:
        return None
    return {
        "href": _attr(anchor.group("attrs"), "href"),
        "label": _strip_tags(anchor.group("body")),
    }


def _download_href(chunk):
    match = _DOWNLOAD_RE.search(chunk or "")
    if not match:
        return ""
    return _attr(match.group("attrs"), "href")


def _split_title_year(value):
    match = _TITLE_YEAR_RE.match(_strip_tags(value))
    if match:
        return match.group("title").strip(), int(match.group("year"))
    return _strip_tags(value), None


def _field_value(chunk, label):
    label_norm = _normalize(label)
    for match in _FIELD_RE.finditer(chunk or ""):
        text = _strip_tags(match.group("body"))
        if _normalize(text).startswith(label_norm):
            return text.split(":", 1)[1].strip() if ":" in text else _int_from_text(text)
    return ""


def _comments(chunk):
    match = _BOLD_COMMENT_RE.search(chunk or "")
    return _strip_tags(match.group("body")) if match else ""


def _imdb_id(chunk):
    match = _IMDB_RE.search(chunk or "")
    return match.group("id").lower() if match else ""


def _download_url(href):
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"{BASE_URL}/{html.unescape(href).lstrip('/')}"


def _absolute_url(href):
    return urllib.parse.urljoin(BASE_URL + "/", html.unescape(href or ""))


def _subtitle_id_from_download(href):
    match = re.match(r"(?P<id>\d+)[-_]", href or "")
    return match.group("id") if match else ""


def _subtitle_id_from_page(href):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(href or "").query)
    return (query.get("id") or [""])[0]


def _row_matches_video(video, row, matches):
    kind = (video or {}).get("kind")
    if kind == "movie":
        wanted_year = _safe_int(video.get("year"))
        if wanted_year is not None and row.get("year") is not None and row.get("year") != wanted_year:
            return False
        return bool({"title", "imdb_id"} & set(matches))
    if kind == "episode":
        if "season" not in matches or not {"series", "imdb_id"} & set(matches):
            return False
        if "episode" not in matches and _comments_have_episode_hint(row.get("comments")):
            return False
        return True
    return False


def _requested_variants(languages):
    variants = []
    seen = set()
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 != "ron":
            continue
        if bool((language or {}).get("hi")) or bool((language or {}).get("forced")):
            continue
        variant = {
            "alpha3": "ron",
            "alpha2": "ro",
            "hi": False,
            "forced": False,
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
    if alpha3:
        return alpha3
    return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())


def _variant_suffix(language):
    parts = ["ro"]
    if language.get("hi"):
        parts.append("hi")
    if language.get("forced"):
        parts.append("forced")
    return "-".join(parts)


def _imdb_query(video):
    video = video or {}
    if video.get("kind") == "episode":
        value = video.get("series_imdb_id") or video.get("imdb_id")
    else:
        value = video.get("imdb_id") or video.get("series_imdb_id")
    return _clean_imdb(value).removeprefix("tt")


def _clean_imdb(value):
    value = str(value or "").strip().lower().rstrip("/")
    if not value:
        return ""
    return value if value.startswith("tt") else f"tt{value}"


def _search_title(video):
    if not video:
        return ""
    title = video.get("title") if video.get("kind") == "movie" else video.get("series")
    normalized = _normalize(title)
    return _ALIASES.get(normalized, str(title or "").strip())


def _title_matches(wanted, candidate):
    wanted_norm = _normalize(_ALIASES.get(_normalize(wanted), wanted))
    return bool(wanted_norm) and wanted_norm == _normalize(candidate)


def _strip_season_suffix(value):
    return re.sub(r"\s+-\s+Sezonul\s+\d+\s*$", "", str(value or ""), flags=re.I).strip()


def _season_matches(comments, wanted_season):
    wanted = _safe_int(wanted_season)
    if wanted is None:
        return False
    text = _normalize_text(comments)
    for match in _SEASON_RANGE_RE.finditer(text):
        start = int(match.group("start"))
        end = int(match.group("end"))
        if min(start, end) <= wanted <= max(start, end):
            return True
    for match in _SEASON_RE.finditer(text):
        if int(match.group("season")) == wanted:
            return True
    for match in _SXXEXX_RE.finditer(text):
        if int(match.group("season")) == wanted:
            return True
    return False


def _episode_matches(comments, wanted_episode):
    wanted = _safe_int(wanted_episode)
    if wanted is None:
        return False
    text = _normalize_text(comments)
    found_episode_hint = False
    for match in _EPISODE_RANGE_RE.finditer(text):
        found_episode_hint = True
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if min(start, end) <= wanted <= max(start, end):
            return True
    for match in _SXXEXX_RE.finditer(text):
        found_episode_hint = True
        if int(match.group("episode")) == wanted:
            return True
    return not found_episode_hint


def _comments_have_episode_hint(comments):
    text = _normalize_text(comments)
    return bool(_EPISODE_RANGE_RE.search(text) or _SXXEXX_RE.search(text))


def _release_matches(video, comments):
    matches = []
    normalized = _normalize_release(comments)
    for key, match_name in (("release_group", "release_group"), ("source", "source"), ("resolution", "resolution")):
        value = _normalize_release((video or {}).get(key))
        if value and value in normalized:
            matches.append(match_name)
    return matches


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


def _content_payload(content, subtitle_format):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    return {
        "content_b64": _base64.b64encode(content).decode("ascii"),
        "content_sha256": _hashlib.sha256(content).hexdigest(),
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


def _looks_like_unavailable_text(body):
    sample = (body or b"").lstrip()[:256].lower()
    return sample.startswith((b"<!doctype html", b"<html")) or b"subtitrarea nu este disponib" in sample


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _int_from_text(text):
    match = re.search(r"\d+", str(text or "").replace(".", ""))
    return int(match.group(0)) if match else 0


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attr(attrs, name):
    if not attrs:
        return ""
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*=\s*(['\"])(?P<value>.*?)\1", re.I | re.S)
    match = pattern.search(attrs)
    return html.unescape(match.group("value")).strip() if match else ""


def _strip_tags(value):
    text = _TAG_RE.sub("", value or "")
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_text(value):
    return str(value or "").lower()


def _normalize_release(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[\W_]+", " ", folded.lower()).strip()


def _slug(value):
    return "-".join(_tokens(value)) or "release"


def _decode_html(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
