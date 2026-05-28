"""SubsceneBest provider for Bazarr - scrapes subscene.best (Subscene clone)."""

import base64
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

PROVIDER_ID = "sub_scene"
BASE_URL = "https://sub-scene.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 60000
CLOUDFLARE_STATUS_CODES = {403, 429, 503}
CLOUDFLARE_BODY_MARKERS = (
    "just a moment",
    "challenge-platform",
    "_cf_chl_opt",
    "cf_chl",
    "cf-chl",
    "turnstile",
)
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

LANGUAGE_MAP = {
    "Arabic": "ara",
    "Bengali": "ben",
    "Bulgarian": "bul",
    "Chinese BG code": "zho",
    "Big 5 code": "zho",
    "Croatian": "hrv",
    "Czech": "ces",
    "Danish": "dan",
    "Dutch": "nld",
    "English": "eng",
    "Farsi/Persian": "fas",
    "Finnish": "fin",
    "French": "fra",
    "German": "deu",
    "Greek": "ell",
    "Hebrew": "heb",
    "Hindi": "hin",
    "Hungarian": "hun",
    "Indonesian": "ind",
    "Italian": "ita",
    "Japanese": "jpn",
    "Korean": "kor",
    "Malay": "msa",
    "Norwegian": "nor",
    "Polish": "pol",
    "Portuguese": "por",
    "Brazillian Portuguese": "por",
    "Romanian": "ron",
    "Russian": "rus",
    "Serbian": "srp",
    "Slovak": "slk",
    "Slovenian": "slv",
    "Spanish": "spa",
    "Swedish": "swe",
    "Thai": "tha",
    "Turkish": "tur",
    "Ukrainian": "ukr",
    "Vietnamese": "vie",
}

ALPHA3_TO_ALPHA2 = {
    "ara": "ar",
    "ben": "bn",
    "bul": "bg",
    "ces": "cs",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "heb": "he",
    "hin": "hi",
    "hrv": "hr",
    "hun": "hu",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kor": "ko",
    "msa": "ms",
    "nld": "nl",
    "nor": "no",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "rus": "ru",
    "slk": "sk",
    "slv": "sl",
    "spa": "es",
    "sqi": "sq",
    "srp": "sr",
    "swe": "sv",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
    "zho": "zh",
}


class CloudflareBlockedError(RuntimeError):
    """Raised when sub-scene.com presents an unresolved Cloudflare challenge."""


class _MissingCloudscraper:
    def create_scraper(self, *args, **kwargs):
        raise CloudflareBlockedError("sub_scene cloudscraper dependency is not installed")


if cloudscraper is None:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = _MissingCloudscraper()


class SubsceneSearchParser(HTMLParser):
    """Parse search results page to extract movie/show links."""

    def __init__(self):
        super().__init__()
        self.results = []
        self.in_search_result = False
        self.search_result_depth = 0
        self.in_title_div = False
        self.in_link = False
        self.current_href = None
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        classes = class_name.split()
        if tag == "div" and "search-result" in classes:
            self.in_search_result = True
            self.search_result_depth = 1
        elif tag == "div" and self.in_search_result:
            self.search_result_depth += 1

        if tag == "div" and self.in_search_result and "title" in classes:
            self.in_title_div = True
        elif tag == "a" and self.in_title_div:
            href = attrs_dict.get("href", "")
            if href.startswith("/subscene/"):
                self.in_link = True
                self.current_href = href
                self.current_text = ""

    def handle_data(self, data):
        if self.in_link:
            self.current_text += data

    def handle_endtag(self, tag):
        if tag == "a" and self.in_link:
            self.in_link = False
            if self.current_href and self.current_text.strip():
                self.results.append({
                    "url": self.current_href,
                    "title": self.current_text.strip()
                })
        elif tag == "div" and self.in_title_div:
            self.in_title_div = False
        if tag == "div" and self.in_search_result:
            self.search_result_depth -= 1
            if self.search_result_depth <= 0:
                self.in_search_result = False
                self.search_result_depth = 0


class SubsceneDetailParser(HTMLParser):
    """Parse detail page to extract subtitle entries."""

    def __init__(self):
        super().__init__()
        self.subtitles = []
        self.in_table = False
        self.in_tbody = False
        self.in_row = False
        self.in_language_cell = False
        self.in_language_span = False
        self.in_release_span = False
        self.in_link = False
        self.in_hi_cell = False
        self.current_row = {}
        self.current_href = None
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == "table":
            self.in_table = True
        elif tag == "tbody" and self.in_table:
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.in_row = True
            self.current_row = {}
        elif tag == "td" and self.in_row:
            class_name = attrs_dict.get("class", "")
            if class_name == "a1":
                self.in_language_cell = True
            elif class_name == "a40":
                self.in_hi_cell = True
        elif tag == "span" and self.in_language_cell:
            class_name = attrs_dict.get("class", "")
            classes = class_name.split()
            if "l" in classes and "r" in classes:
                self.in_language_span = True
                self.current_text = ""
            elif class_name == "new":
                self.in_release_span = True
                self.current_text = ""
        elif tag == "a" and self.in_language_cell:
            href = attrs_dict.get("href", "")
            if href.startswith("/subtitle/"):
                self.in_link = True
                self.current_href = href

    def handle_data(self, data):
        if self.in_language_span:
            self.current_text += data
        elif self.in_release_span:
            self.current_text += data
        elif self.in_hi_cell:
            if data.strip().lower() == "yes":
                self.current_row["hi"] = True

    def handle_endtag(self, tag):
        if tag == "span" and self.in_language_span:
            self.in_language_span = False
            self.current_row["language"] = self.current_text.strip()
        elif tag == "span" and self.in_release_span:
            self.in_release_span = False
            self.current_row["release"] = self.current_text.strip()
        elif tag == "a" and self.in_link:
            self.in_link = False
            self.current_row["url"] = self.current_href
        elif tag == "td" and self.in_hi_cell:
            self.in_hi_cell = False
            if "hi" not in self.current_row:
                self.current_row["hi"] = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row.get("url") and self.current_row.get("language"):
                self.subtitles.append(self.current_row)
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "table":
            self.in_table = False


class SubsceneSubtitleParser(HTMLParser):
    """Parse subtitle detail page to extract download URL and metadata."""

    def __init__(self):
        super().__init__()
        self.download_url = None
        self.title = None
        self.release_info = []
        self.hearing_impaired = False
        self.in_download_div = False
        self.in_download_link = False
        self.in_title_div = False
        self.in_release_div = False
        self.in_hi_span = False
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == "div":
            class_name = attrs_dict.get("class", "")
            if class_name == "download":
                self.in_download_div = True
            elif class_name == "title":
                self.in_title_div = True
            elif class_name == "release":
                self.in_release_div = True
        elif tag == "a" and self.in_download_div:
            href = attrs_dict.get("href", "")
            if href.startswith("/download/"):
                self.in_download_link = True
                self.download_url = href
        elif tag == "span" and self.in_release_div:
            class_name = attrs_dict.get("class", "")
            if class_name == "new":
                self.current_text = ""
        elif tag == "div" and attrs_dict.get("class") == "subtle":
            self.current_text = ""

    def handle_data(self, data):
        if self.in_title_div and not self.title:
            self.title = data.strip()
        elif self.in_release_div:
            text = data.strip()
            if text and text not in ["Release Info:", ""]:
                self.release_info.append(text)
        elif "Hearing Impaired:" in self.current_text:
            if "Yes" in data:
                self.hearing_impaired = True

    def handle_endtag(self, tag):
        if tag == "div" and self.in_download_div:
            self.in_download_div = False
        elif tag == "div" and self.in_title_div:
            self.in_title_div = False
        elif tag == "div" and self.in_release_div:
            self.in_release_div = False


def _normalize_headers(headers):
    return {
        str(key).lower(): str(value)
        for key, value in (headers or {}).items()
    }


def _is_cloudflare_challenge(status_code, headers, body):
    normalized_headers = _normalize_headers(headers)
    if normalized_headers.get("cf-mitigated", "").lower() == "challenge":
        return True

    try:
        status = int(status_code)
    except (TypeError, ValueError):
        status = 0
    if status not in CLOUDFLARE_STATUS_CODES:
        return False

    body_text = (body or b"").decode("utf-8", errors="ignore").lower()
    return any(marker in body_text for marker in CLOUDFLARE_BODY_MARKERS)


def _is_cloudflare_exception(exc):
    text = f"{exc.__class__.__name__} {exc}".lower()
    return "cloudflare" in text or "challenge" in text


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config):
    try:
        timeout = int((config or {}).get("flaresolverr_timeout_ms") or DEFAULT_FLARESOLVERR_TIMEOUT_MS)
    except (TypeError, ValueError):
        return DEFAULT_FLARESOLVERR_TIMEOUT_MS
    return max(5000, min(timeout, 180000))


def _cookie_header(cookies):
    if not cookies:
        return ""
    return "; ".join(
        f"{name}={value}"
        for name, value in cookies.items()
        if name and value is not None
    )


def _request_headers(state=None, referer=None):
    state = state or {}
    headers = {
        "User-Agent": state.get("flaresolverr_user_agent") or USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    cookie = _cookie_header(state.get("flaresolverr_cookies"))
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    return headers


def _get_cloudscraper(state):
    if state is None:
        state = {}
    scraper = state.get("cloudscraper")
    if scraper is None:
        scraper = cloudscraper.create_scraper(
            browser={"custom": USER_AGENT},
        )
        state["cloudscraper"] = scraper
    return scraper


def _store_flaresolverr_solution(state, solution):
    if state is None:
        return
    user_agent = solution.get("userAgent")
    if user_agent:
        state["flaresolverr_user_agent"] = user_agent

    cookies = state.setdefault("flaresolverr_cookies", {})
    for cookie in solution.get("cookies") or []:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            cookies[name] = value


def _flaresolverr_get(url, timeout=30, config=None, state=None):
    endpoint = _flaresolverr_url(config)
    if not endpoint:
        raise CloudflareBlockedError(
            "sub_scene hit a Cloudflare challenge and no FlareSolverr URL is configured"
        )

    timeout_ms = _flaresolverr_timeout_ms(config)
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout_ms,
    }
    cookies = (state or {}).get("flaresolverr_cookies")
    if cookies:
        payload["cookies"] = [
            {"name": name, "value": value}
            for name, value in cookies.items()
        ]

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(timeout, timeout_ms / 1000),
        ) as response:
            response_body = response.read()
    except Exception as exc:
        raise CloudflareBlockedError(f"sub_scene FlareSolverr request failed: {exc}") from exc

    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudflareBlockedError("sub_scene FlareSolverr returned invalid JSON") from exc

    if payload.get("status") not in (None, "ok"):
        message = payload.get("message") or "FlareSolverr did not solve the challenge"
        raise CloudflareBlockedError(f"sub_scene {message}")

    solution = payload.get("solution") or {}
    response_text = solution.get("response")
    if response_text is None:
        raise CloudflareBlockedError("sub_scene FlareSolverr response had no page body")

    if isinstance(response_text, bytes):
        body = response_text
    else:
        body = str(response_text).encode("utf-8")

    solution_status = solution.get("status") or 200
    if _is_cloudflare_challenge(solution_status, solution.get("headers") or {}, body):
        raise CloudflareBlockedError("sub_scene FlareSolverr response is still a Cloudflare challenge")

    _store_flaresolverr_solution(state, solution)
    return body


def _http_get(url, timeout=30, config=None, state=None, referer=None):
    """Make HTTP GET request with cloudscraper and optional FlareSolverr fallback."""
    state = state if state is not None else {}
    config = config or {}
    try:
        response = _get_cloudscraper(state).get(
            url,
            timeout=timeout,
            headers=_request_headers(state, referer),
        )
    except Exception as exc:
        if _flaresolverr_url(config) and _is_cloudflare_exception(exc):
            return _flaresolverr_get(url, timeout=timeout, config=config, state=state)
        raise

    status_code = getattr(response, "status_code", 0)
    headers = getattr(response, "headers", {}) or {}
    body = getattr(response, "content", None)
    if body is None:
        body = str(getattr(response, "text", "")).encode("utf-8")

    if _is_cloudflare_challenge(status_code, headers, body):
        if _flaresolverr_url(config):
            return _flaresolverr_get(url, timeout=timeout, config=config, state=state)
        raise CloudflareBlockedError(
            "sub_scene hit a Cloudflare challenge and no FlareSolverr URL is configured"
        )

    try:
        status = int(status_code)
    except (TypeError, ValueError):
        status = 0
    if status >= 400:
        raise urllib.error.HTTPError(url, status, f"HTTP {status}", headers, None)

    return body


def _build_search_queries(video):
    """Build search queries based on video metadata."""
    queries = []
    
    if video.get("kind") == "movie":
        title = video.get("title", "")
        year = video.get("year")
        if title:
            if year:
                queries.append(f"{title} {year}")
            queries.append(title)
    elif video.get("kind") == "episode":
        series = video.get("series", "")
        if series:
            queries.append(series)
    
    return queries


def _search_subscene(query, delay_ms=0, config=None, state=None):
    """Search subscene.best and return results."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    encoded_query = urllib.parse.quote(query)
    url = f"{BASE_URL}/search?query={encoded_query}"
    
    html = _http_get(url, config=config, state=state)
    parser = SubsceneSearchParser()
    parser.feed(html.decode("utf-8", errors="ignore"))
    return parser.results


def _get_detail_page(url, delay_ms=0, config=None, state=None):
    """Fetch and parse detail page."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    full_url = f"{BASE_URL}{url}" if url.startswith("/") else url
    
    try:
        html = _http_get(full_url, config=config, state=state)
        parser = SubsceneDetailParser()
        parser.feed(html.decode("utf-8", errors="ignore"))
        return parser.subtitles
    except CloudflareBlockedError:
        raise
    except Exception:
        return []


def _get_subtitle_detail(url, delay_ms=0, config=None, state=None):
    """Fetch and parse subtitle detail page."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    full_url = f"{BASE_URL}{url}" if url.startswith("/") else url
    
    try:
        html = _http_get(full_url, config=config, state=state)
        parser = SubsceneSubtitleParser()
        parser.feed(html.decode("utf-8", errors="ignore"))
        return {
            "download_url": parser.download_url,
            "title": parser.title,
            "release_info": parser.release_info,
            "hearing_impaired": parser.hearing_impaired,
        }
    except CloudflareBlockedError:
        raise
    except Exception:
        return None


def _select_episode_file(srt_files, video):
    """Select the best matching episode file from a ZIP archive."""
    selected_files = _select_subtitle_files(srt_files, video)
    return selected_files[0] if selected_files else None


def _select_subtitle_files(srt_files, video):
    """Select matching subtitle files from a ZIP archive."""
    if not srt_files:
        return []
    
    episode = (video or {}).get("episode")
    season = (video or {}).get("season")
    
    if episode is None:
        multipart = _multipart_subset(srt_files)
        if multipart:
            return multipart
        return [srt_files[0]]
    
    try:
        episode_int = int(episode)
    except (TypeError, ValueError):
        return [srt_files[0]]
    
    try:
        season_int = int(season)
    except (TypeError, ValueError):
        season_int = None
    
    def score(name):
        base = name.lower()
        season_markers = _explicit_season_markers(base)
        if _episode_range_includes(base, episode_int, season_int):
            if season_int is None or not season_markers or season_int in season_markers:
                return 200 if season_int is not None else 100
            return 0

        markers = _season_episode_markers(base)
        if markers:
            for marker_season, marker_episode in markers:
                if marker_episode != episode_int:
                    continue
                if season_int is not None and marker_season != season_int:
                    continue
                return 200 if season_int is not None else 100
            return 0

        if season_int is not None and season_markers and season_int not in season_markers:
            return 0

        # Fallback: match episode number with E prefix and boundary
        e_pattern = rf"e{episode_int:02d}(?!\d)"
        if re.search(e_pattern, base):
            return 100
        e_pattern_unpadded = rf"e{episode_int}(?!\d)"
        if re.search(e_pattern_unpadded, base):
            return 100
        return 0
    
    # Score all candidates and find the best match
    scored = [(score(name), name) for name in srt_files]
    best_score, best_name = max(scored, key=lambda x: x[0])
    
    if best_score == 0:
        if len(srt_files) == 1:
            only_file = srt_files[0].lower()
            if not _explicit_episode_markers(only_file):
                season_markers = _explicit_season_markers(only_file)
                if (
                    season_int is None
                    or not season_markers
                    or season_int in season_markers
                ):
                    return srt_files
        return []
    
    matching_files = [name for score_value, name in scored if score_value == best_score]
    multipart = _multipart_subset(matching_files)
    if multipart:
        return multipart
    return [best_name]


def _is_archive_sidecar(name):
    path = (name or "").replace("\\", "/")
    parts = [part for part in path.split("/") if part]
    return "__MACOSX" in parts or os.path.basename(path).startswith("._")


def _is_paired_vobsub_sub(name, names):
    path = (name or "").replace("\\", "/").lower()
    if not path.endswith(".sub"):
        return False
    idx_path = f"{os.path.splitext(path)[0]}.idx"
    normalized_names = {
        (candidate or "").replace("\\", "/").lower()
        for candidate in names
    }
    return idx_path in normalized_names


def _part_index(name):
    normalized = re.sub(
        r"[\W_]+",
        " ",
        os.path.splitext(os.path.basename(name))[0].lower(),
    ).strip()
    match = re.search(r"\b(?:cd|part|disc|disk)\s*0*(\d+)\b", normalized)
    return int(match.group(1)) if match else 0


def _multipart_subset(names):
    groups = {}
    for name in names:
        part_index = _part_index(name)
        if part_index <= 0:
            continue
        groups.setdefault((_multipart_key(name), _subtitle_extension(name)), []).append(name)
    valid_groups = []
    for group in groups.values():
        part_numbers = [_part_index(name) for name in group]
        if len(group) > 1 and len(set(part_numbers)) == len(part_numbers):
            valid_groups.append(group)
    if not valid_groups:
        return []
    best_group = max(
        valid_groups,
        key=lambda group: (len(group), -min(_part_index(name) for name in group)),
    )
    return sorted(best_group, key=lambda name: (_part_index(name), name.lower()))


def _multipart_key(name):
    stem = os.path.splitext(os.path.basename(name))[0]
    normalized = re.sub(r"[\W_]+", " ", stem.lower()).strip()
    return re.sub(r"\b(?:cd|part|disc|disk)\s*0*\d+\b", "", normalized).strip()


def _subtitle_extension(name):
    path = urllib.parse.urlparse(name or "").path or (name or "")
    lowered = path.lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _download_subtitle(download_url, delay_ms=0, video=None, config=None, state=None):
    """Download and extract subtitle ZIP file."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    full_url = f"{BASE_URL}{download_url}" if download_url.startswith("/") else download_url
    
    try:
        zip_data = _http_get(full_url, config=config, state=state)
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            archive_names = zf.namelist()
            subtitle_files = [
                name
                for name in archive_names
                if (
                    _subtitle_extension(name)
                    and not _is_archive_sidecar(name)
                    and not _is_paired_vobsub_sub(name, archive_names)
                )
            ]
            if not subtitle_files:
                return None
            
            # Select episode-specific file if video metadata available
            selected_files = _select_subtitle_files(subtitle_files, video)
            if not selected_files:
                return None
            content = b"\n\n".join(zf.read(name) for name in selected_files)
            if not content:
                return None
            subtitle_file = selected_files[0]
            
            return {
                "content": content,
                "filename": subtitle_file,
                "format": _subtitle_extension(subtitle_file) or "srt",
            }
    except CloudflareBlockedError:
        raise
    except Exception:
        return None


def _coerce_text(value):
    """Coerce value to string, handling lists/tuples."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if item)
    return str(value)


def _normalized_tokens(value):
    return set(re.sub(r"[\W_]+", " ", _coerce_text(value).lower()).split())


def _release_matches_source(source, release):
    source = _coerce_text(source).lower()
    release = _coerce_text(release).lower()
    if not source:
        return False
    if "bluray" in source and "bluray" in release:
        return True
    if "web" in source and (
        "web" in release or "webrip" in release or "web-dl" in release
    ):
        return True
    if "hdtv" in source and "hdtv" in release:
        return True
    return source in release


ORDINAL_SEASONS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _explicit_season_markers(text):
    markers = set()
    text = _coerce_text(text).lower()
    for marker in re.finditer(r"\bs0*(\d{1,2})(?!\d)", text):
        markers.add(int(marker.group(1)))
    for marker in re.finditer(r"\b0*(\d{1,2})(?=x\d{1,2}(?!\d))", text):
        markers.add(int(marker.group(1)))
    for marker in re.finditer(r"\bseason\s*0*(\d{1,2})(?!\d)", text):
        markers.add(int(marker.group(1)))
    for word, season in ORDINAL_SEASONS.items():
        if re.search(rf"\b{word}\s+season\b", text):
            markers.add(season)
    return markers


def _season_episode_markers(text):
    text = _coerce_text(text).lower()
    markers = []
    for marker in re.finditer(
        r"\bs0*(\d{1,2})((?:[\s._-]*e0*\d{1,3})+)",
        text,
    ):
        season = int(marker.group(1))
        for episode in re.findall(r"e0*(\d{1,3})", marker.group(2)):
            markers.append((season, int(episode)))
    for marker in re.finditer(r"\b0*(\d{1,2})x0*(\d{1,3})(?!\d)", text):
        season_episode = (int(marker.group(1)), int(marker.group(2)))
        if season_episode not in markers:
            markers.append(season_episode)
    return markers


def _season_episode_marker(text):
    markers = _season_episode_markers(text)
    return markers[0] if markers else None


def _explicit_episode_markers(text):
    markers = set()
    text = _coerce_text(text).lower()
    for _, episode in _season_episode_markers(text):
        markers.add(episode)
    for pattern in (
        r"\be0*(\d{1,3})(?!\d)",
    ):
        for marker in re.finditer(pattern, text):
            markers.add(int(marker.group(1)))
    return markers


def _episode_range_includes(text, episode, season=None):
    try:
        episode_int = int(episode)
    except (TypeError, ValueError):
        return False
    try:
        season_int = int(season)
    except (TypeError, ValueError):
        season_int = None
    text = _coerce_text(text).lower()
    for pattern in (
        r"\bs0*(\d{1,2})e0*(\d{1,3})\s*(?:-|to|through|thru)\s*e?0*(\d{1,3})(?!\d)",
        r"\bs0*(\d{1,2})[\s._-]+e0*(\d{1,3})\s*(?:-|to|through|thru)\s*e?0*(\d{1,3})(?!\d)",
    ):
        for marker in re.finditer(pattern, text):
            marker_season = int(marker.group(1))
            if season_int is not None and marker_season != season_int:
                continue
            start = int(marker.group(2))
            end = int(marker.group(3))
            lower, upper = sorted((start, end))
            if lower <= episode_int <= upper:
                return True
    for pattern in (
        r"\be0*(\d{1,3})\s*(?:-|to|through|thru)\s*e?0*(\d{1,3})(?!\d)",
        r"\bepisodes?\s*0*(\d{1,3})\s*(?:-|to|through|thru)\s*0*(\d{1,3})(?!\d)",
    ):
        for marker in re.finditer(pattern, text):
            season_markers = _explicit_season_markers(text)
            if season_int is not None and season_markers and season_int not in season_markers:
                continue
            start = int(marker.group(1))
            end = int(marker.group(2))
            lower, upper = sorted((start, end))
            if lower <= episode_int <= upper:
                return True
    return False


def _has_conflicting_episode_marker(text, season, episode):
    if episode is not None and _episode_range_includes(text, episode, season):
        season_markers = _explicit_season_markers(text)
        if season is None or not season_markers or season in season_markers:
            return False
    marker = _season_episode_marker(text)
    if not marker:
        return False
    marker_season, marker_episode = marker
    if episode is not None and marker_episode != episode:
        return True
    if season is not None and marker_season != season:
        return True
    return False


def _derive_matches(video, result_title, subtitle):
    """Return Provider Hub match keys represented by the SubScene candidate."""
    video = video or {}
    release = subtitle.get("release", "")
    candidate_text = f"{result_title or ''} {release}"
    candidate_tokens = _normalized_tokens(candidate_text)
    matches = []

    if video.get("kind") == "movie":
        title_tokens = _normalized_tokens(video.get("title"))
        if title_tokens and title_tokens.issubset(candidate_tokens):
            matches.append("title")

        year = video.get("year")
        if year and str(year) in candidate_tokens:
            matches.append("year")

        if _release_matches_source(video.get("source"), release):
            matches.append("source")

        resolution = _coerce_text(video.get("resolution")).lower()
        if resolution and resolution in _coerce_text(release).lower():
            matches.append("resolution")

    elif video.get("kind") == "episode":
        series_tokens = _normalized_tokens(video.get("series"))
        if series_tokens and series_tokens.issubset(candidate_tokens):
            matches.append("series")

        try:
            season = int(video.get("season"))
        except (TypeError, ValueError):
            season = None
        try:
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            episode = None

        release_lower = _coerce_text(release).lower()
        candidate_lower = _coerce_text(candidate_text).lower()
        title_lower = _coerce_text(result_title).lower()
        season_markers = _explicit_season_markers(candidate_lower)
        has_wrong_title_episode = _has_conflicting_episode_marker(
            title_lower,
            season,
            episode,
        )
        if season is not None and season in season_markers and not has_wrong_title_episode:
            matches.append("season")
        if episode is not None:
            markers = _season_episode_markers(release_lower)
            if markers:
                if any(
                    marker_episode == episode
                    and (season is None or marker_season == season)
                    for marker_season, marker_episode in markers
                ):
                    matches.append("episode")
            elif _episode_range_includes(release_lower, episode, season):
                if season is None or not season_markers or season in season_markers:
                    matches.append("episode")
            elif _episode_range_includes(candidate_lower, episode, season):
                if season is None or not season_markers or season in season_markers:
                    matches.append("episode")
            elif re.search(rf"e0*{episode}(?!\d)", release_lower):
                if season is None or not season_markers or season in season_markers:
                    matches.append("episode")

    return matches


def _has_required_match(video, matches, subtitle=None):
    video = video or {}
    matches = set(matches or [])
    if video.get("kind") == "movie":
        if "title" not in matches:
            return False
        if video.get("year") and "year" not in matches:
            return False
    elif video.get("kind") == "episode":
        if video.get("series") and "series" not in matches:
            return False
        if video.get("episode") is None:
            return True
        if "episode" in matches:
            return True
        release = (subtitle or {}).get("release", "")
        if "season" in matches and not _explicit_episode_markers(release):
            return True
        if video.get("episode") is not None:
            return False
    return True


def _calculate_score(video, subtitle):
    """Calculate match score based on video metadata."""
    score = 60
    release = subtitle.get("release", "").lower()
    
    if video.get("kind") == "movie":
        year = video.get("year")
        if year and str(year) in release:
            score += 20
        
        if _release_matches_source(video.get("source"), release):
            score += 10
        
        resolution = _coerce_text(video.get("resolution")).lower()
        if resolution and resolution in release:
            score += 10
    
    elif video.get("kind") == "episode":
        season = video.get("season")
        episode = video.get("episode")
        
        if season and episode:
            s_str = f"s{int(season):02d}"
            e_str = f"e{int(episode):02d}"
            if s_str in release and e_str in release:
                score += 30
            elif s_str in release:
                score += 15
    
    return min(score, 100)


def _get_language_code(language_name):
    """Convert language name to ISO 639-2 code."""
    return LANGUAGE_MAP.get(language_name)


def _alpha2_from_alpha3(alpha3):
    return ALPHA3_TO_ALPHA2.get(str(alpha3 or "").lower(), str(alpha3 or "").lower())


def _content_type(subtitle_format):
    return {
        "srt": "application/x-subrip",
        "ass": "text/x-ssa",
        "ssa": "text/x-ssa",
        "vtt": "text/vtt",
        "sub": "text/plain",
    }.get(subtitle_format, "text/plain")


def _alpha2_for(language):
    """Extract alpha2 code from language dict or string."""
    if isinstance(language, dict):
        return (language.get("alpha2") or "").lower()
    return str(language).lower()


def _alpha3_for(language):
    """Extract alpha3 code from language dict or string."""
    if isinstance(language, dict):
        return (language.get("alpha3") or "").lower()
    return str(language).lower()


class SubSceneProvider:
    """Sub-scene.com subtitle provider."""

    def __init__(self):
        self._http_state = {}

    def search(self, video, languages, config):
        """Search for subtitles matching the video."""
        if not video or not languages:
            return []
        
        config = config or {}
        delay_ms = config.get("request_delay_ms", 0)
        requested_alpha3 = set()
        for lang in languages:
            alpha3 = _alpha3_for(lang)
            if alpha3:
                requested_alpha3.add(alpha3)
        
        if not requested_alpha3:
            return []
        
        results = []
        seen_ids = set()
        queries = _build_search_queries(video)
        if not queries:
            return []
        
        for query in queries:
            search_results = _search_subscene(query, delay_ms, config, self._http_state)
            
            for result in search_results[:5]:
                subtitles = _get_detail_page(result["url"], delay_ms, config, self._http_state)
                
                for subtitle in subtitles:
                    lang_name = subtitle.get("language", "")
                    lang_code = _get_language_code(lang_name)
                    
                    if not lang_code or lang_code not in requested_alpha3:
                        continue
                    
                    subtitle_url = subtitle.get("url", "")
                    subtitle_id = subtitle_url.split("/")[-1] if subtitle_url else "unknown"

                    if subtitle_id in seen_ids:
                        continue

                    score = _calculate_score(video, subtitle)
                    matches = _derive_matches(video, result.get("title", ""), subtitle)
                    if not _has_required_match(video, matches, subtitle):
                        continue

                    seen_ids.add(subtitle_id)

                    results.append({
                        "provider": PROVIDER_ID,
                        "id": f"{PROVIDER_ID}_{subtitle_id}",
                        "language": {
                            "alpha3": lang_code,
                            "alpha2": _alpha2_from_alpha3(lang_code),
                            "hi": False,
                            "forced": False,
                        },
                        "release_info": subtitle.get("release", ""),
                        "filename": f"{PROVIDER_ID}.{subtitle_id}.srt",
                        "matches": matches,
                        "score": score,
                        "score_without_hash": score,
                        "score_out_of": 100,
                        "hash_verifiable": False,
                        "hearing_impaired_verifiable": False,
                        "hearing_impaired": subtitle.get("hi", False),
                        "page_link": f"{BASE_URL}{subtitle_url}" if subtitle_url.startswith("/") else subtitle_url,
                        "display": {
                            "source": "sub-scene.com",
                            "title": result.get("title", ""),
                            "release": subtitle.get("release", ""),
                        },
                        "provider_payload": {
                            "provider": PROVIDER_ID,
                            "schema": 1,
                            "url": subtitle_url,
                            "video": video,
                        },
                    })
            
            if results:
                break
        
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:10]

    def download(self, provider_payload, language, config):
        """Download subtitle file."""
        subtitle = provider_payload or {}
        if not subtitle or not subtitle.get("url"):
            raise ValueError("sub_scene download requires url in provider_payload")
        
        config = config or {}
        delay_ms = config.get("request_delay_ms", 0)
        
        detail = _get_subtitle_detail(subtitle["url"], delay_ms, config, self._http_state)
        if not detail or not detail.get("download_url"):
            raise ValueError("sub_scene could not find download URL on detail page")
        
        # Pass video metadata to select episode-specific file from ZIP
        video = subtitle.get("video")
        downloaded = _download_subtitle(
            detail["download_url"],
            delay_ms,
            video,
            config,
            self._http_state,
        )
        if not downloaded:
            raise ValueError("sub_scene failed to download or extract subtitle file")
        
        content = downloaded["content"]
        content_b64 = base64.b64encode(content).decode("ascii")
        content_sha256 = hashlib.sha256(content).hexdigest()
        
        encoding = "utf-8"
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            encoding = "latin-1"
        
        return {
            "content_b64": content_b64,
            "content_sha256": content_sha256,
            "content_type": _content_type(downloaded["format"]),
            "format": downloaded["format"],
            "encoding": encoding,
            "empty": False,
        }
