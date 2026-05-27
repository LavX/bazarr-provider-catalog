"""SubtitleStar provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "subtitlestar"
BASE_URL = "https://subtitlestar.com"
DOWNLOAD_BASE_URL = "https://dl2.subtitlestar.com/dlsub"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 15
SUPPORTED_ALPHA3 = "fas"
SUPPORTED_ALPHA2 = "fa"
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


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_tags(fragment):
    text = _TAG_RE.sub("", fragment or "")
    return _WHITESPACE_RE.sub(" ", text).strip()


_SEARCH_RESULT_RE = re.compile(
    r'<a[^>]+href="(https://subtitlestar\.com/persian-subtitles-[^"]+)"[^>]*>'
    r'[^<]*<img[^>]+alt="([^"]*)"',
    re.IGNORECASE | re.DOTALL,
)

_SEARCH_RESULT_ALT_RE = re.compile(
    r'<a[^>]+href="(https://subtitlestar\.com/persian-subtitles-[^"]+)"[^>]*>'
    r'\s*<h2[^>]*>([^<]+)</h2>',
    re.IGNORECASE | re.DOTALL,
)


def parse_search_results(html_bytes):
    if not html_bytes:
        return []
    text = html_bytes.decode("utf-8", errors="replace")
    results = []
    seen = set()
    
    for match in _SEARCH_RESULT_RE.finditer(text):
        url = match.group(1)
        title = match.group(2)
        if url in seen:
            continue
        seen.add(url)
        results.append({
            "detail_url": url,
            "title": title,
        })
    
    for match in _SEARCH_RESULT_ALT_RE.finditer(text):
        url = match.group(1)
        title = _strip_tags(match.group(2))
        if url in seen:
            continue
        seen.add(url)
        results.append({
            "detail_url": url,
            "title": title,
        })
    
    return results


_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE | re.DOTALL)
_IMDB_RE = re.compile(r'imdb\.com/title/(tt\d+)', re.IGNORECASE)
_YEAR_RE = re.compile(r'icon-years[^>]*>.*?<a[^>]*>(\d{4})</a>', re.IGNORECASE | re.DOTALL)
_QUALITY_RE = re.compile(r'<b>کیفیت\s*:</b>([^<]+)</span>', re.IGNORECASE | re.DOTALL)
_DOWNLOAD_RE = re.compile(
    r'<a[^>]+id="link-download"[^>]+href="([^"]+)"[^>]*class="dlbtn"',
    re.IGNORECASE | re.DOTALL,
)
_DOWNLOAD_ALT_RE = re.compile(
    r'<a[^>]+class="dlbtn"[^>]+href="([^"]+)"',
    re.IGNORECASE | re.DOTALL,
)


def parse_detail_page(html_bytes):
    if not html_bytes:
        return None
    text = html_bytes.decode("utf-8", errors="replace")
    
    title_match = _TITLE_RE.search(text)
    page_title = _strip_tags(title_match.group(1)) if title_match else ""
    
    imdb_match = _IMDB_RE.search(text)
    imdb_id = imdb_match.group(1) if imdb_match else None
    
    year_match = _YEAR_RE.search(text)
    year = year_match.group(1) if year_match else None
    
    quality_match = _QUALITY_RE.search(text)
    quality = _strip_tags(quality_match.group(1)) if quality_match else None
    
    downloads = []
    for match in _DOWNLOAD_RE.finditer(text):
        downloads.append(match.group(1))
    for match in _DOWNLOAD_ALT_RE.finditer(text):
        url = match.group(1)
        if url not in downloads:
            downloads.append(url)
    
    return {
        "title": page_title,
        "imdb_id": imdb_id,
        "year": year,
        "quality": quality,
        "downloads": downloads,
    }


def _normalize(text):
    return re.sub(r"[\W_]+", " ", (_coerce_text(text) or "").lower()).strip()


def compute_score(video, candidate_title):
    video = video or {}
    kind = video.get("kind")
    candidate_norm = _normalize(candidate_title)
    
    if kind == "movie":
        title = _normalize(video.get("title"))
        if title and title in candidate_norm:
            year = video.get("year")
            if year and str(year) in candidate_norm:
                return 100
            return 90
        return 60
    
    if kind == "episode":
        series = _normalize(video.get("series"))
        if series and series in candidate_norm:
            return 85
        return 60
    
    return 60


def derive_matches(video, candidate_title):
    video = video or {}
    kind = video.get("kind")
    candidate_norm = _normalize(candidate_title)
    matches = []
    
    if kind == "movie":
        title = _normalize(video.get("title"))
        if title and title in candidate_norm:
            matches.append("title")
        year = video.get("year")
        if year and str(year) in candidate_norm:
            matches.append("year")
    elif kind == "episode":
        series = _normalize(video.get("series"))
        if series and series in candidate_norm:
            matches.append("series")
        season = video.get("season")
        if season is not None and f"s{int(season):02d}" in candidate_norm:
            matches.append("season")
        episode = video.get("episode")
        if episode is not None and f"e{int(episode):02d}" in candidate_norm:
            matches.append("episode")
    
    return matches


def select_subtitle_file(names, video):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("subtitlestar archive contains no supported subtitle files")
    episode = (video or {}).get("episode")
    try:
        episode_int = int(episode)
    except (TypeError, ValueError):
        episode_int = None
    if episode_int is None:
        return candidates[0]

    def score(name):
        base = os.path.basename(name)
        if re.search(rf"(?<!\d)0*{episode_int}(?!\d)", base):
            return 100
        return 0

    return max(candidates, key=score)


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def extract_download(body, filename="", video=None):
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)

    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
            selected = select_subtitle_file(names, video or {})
            return _content_payload(
                archive.read(selected),
                _subtitle_extension(selected) or _format_from_filename(selected),
            )

    return _content_payload(body, _format_from_filename(filename))


def _format_from_filename(filename):
    extension = _subtitle_extension(filename or "")
    return extension or "srt"


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
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "windows-1256"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "encoding": encoding,
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


def _requested_persian_language(languages):
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


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(int(delay_ms), 5000) / 1000.0)


def _filename_from_headers(headers):
    disposition = (headers or {}).get("content-disposition", "")
    match = re.search(r'filename\*?=(?:[^\'";]+\'\')?"?([^";]+)"?', disposition)
    if not match:
        return ""
    return urllib.parse.unquote(match.group(1)).strip()


class SubtitlestarProvider:
    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "fa,en;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        config = dict(config or {})
        language = _requested_persian_language(languages)
        if language is None:
            return []
        queries = build_queries(video)
        if not queries:
            return []

        results = []
        seen_urls = set()
        for query in queries:
            _sleep(config)
            search_url = f"{BASE_URL}/?s={urllib.parse.quote(query)}&post_type=post"
            try:
                body = self._http_get(search_url)
            except Exception:
                continue
            
            candidates = parse_search_results(body)
            for candidate in candidates[:MAX_CANDIDATES_PER_QUERY]:
                if candidate["detail_url"] in seen_urls:
                    continue
                seen_urls.add(candidate["detail_url"])
                
                _sleep(config)
                try:
                    detail_body = self._http_get(candidate["detail_url"])
                except Exception:
                    continue
                
                details = parse_detail_page(detail_body)
                if not details or not details["downloads"]:
                    continue
                
                score = compute_score(video, candidate["title"])
                matches = derive_matches(video, candidate["title"])
                
                for download_url in details["downloads"][:1]:
                    results.append({
                        "provider": PROVIDER_ID,
                        "id": f"subtitlestar-{hashlib.md5(download_url.encode()).hexdigest()[:12]}",
                        "language": language,
                        "release_info": f"{candidate['title']} [{details['quality'] or 'Unknown'}]",
                        "filename": f"subtitlestar.{os.path.basename(download_url)}",
                        "matches": matches,
                        "score": score,
                        "score_without_hash": score,
                        "score_out_of": 100,
                        "hash_verifiable": False,
                        "hearing_impaired_verifiable": False,
                        "hearing_impaired": False,
                        "page_link": candidate["detail_url"],
                        "display": {
                            "source": "subtitlestar.com",
                            "title": candidate["title"],
                            "quality": details["quality"],
                            "year": details["year"],
                        },
                        "provider_payload": {
                            "provider": PROVIDER_ID,
                            "schema": 1,
                            "download_url": download_url,
                            "detail_url": candidate["detail_url"],
                            "video": _video_payload(video),
                        },
                    })
            
            if results:
                break
        
        return sorted(results, key=lambda item: item.get("score", 0), reverse=True)

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        download_url = payload.get("download_url")
        if not download_url:
            raise ValueError("subtitlestar download requires download_url")
        
        body = self._http_get(download_url)
        filename = os.path.basename(download_url)
        return extract_download(
            body,
            filename=filename,
            video=payload.get("video") or {},
        )


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
