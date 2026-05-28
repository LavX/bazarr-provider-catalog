"""SubsceneBest provider for Bazarr - scrapes subscene.best (Subscene clone)."""

import base64
import hashlib
import io
import re
import time
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser

PROVIDER_ID = "sub_scene"
BASE_URL = "https://sub-scene.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

LANGUAGE_MAP = {
    "Arabic": "ara",
    "Bengali": "ben",
    "Bulgarian": "bul",
    "Chinese BG code": "zho",
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


class SubsceneSearchParser(HTMLParser):
    """Parse search results page to extract movie/show links."""

    def __init__(self):
        super().__init__()
        self.results = []
        self.in_title_div = False
        self.in_link = False
        self.current_href = None
        self.current_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "div" and attrs_dict.get("class") == "title":
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


def _http_get(url, timeout=30):
    """Make HTTP GET request with proper headers."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


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


def _search_subscene(query, delay_ms=0):
    """Search subscene.best and return results."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    encoded_query = urllib.parse.quote(query)
    url = f"{BASE_URL}/search?query={encoded_query}"
    
    try:
        html = _http_get(url)
        parser = SubsceneSearchParser()
        parser.feed(html.decode("utf-8", errors="ignore"))
        return parser.results
    except Exception:
        return []


def _get_detail_page(url, delay_ms=0):
    """Fetch and parse detail page."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    full_url = f"{BASE_URL}{url}" if url.startswith("/") else url
    
    try:
        html = _http_get(full_url)
        parser = SubsceneDetailParser()
        parser.feed(html.decode("utf-8", errors="ignore"))
        return parser.subtitles
    except Exception:
        return []


def _get_subtitle_detail(url, delay_ms=0):
    """Fetch and parse subtitle detail page."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    full_url = f"{BASE_URL}{url}" if url.startswith("/") else url
    
    try:
        html = _http_get(full_url)
        parser = SubsceneSubtitleParser()
        parser.feed(html.decode("utf-8", errors="ignore"))
        return {
            "download_url": parser.download_url,
            "title": parser.title,
            "release_info": parser.release_info,
            "hearing_impaired": parser.hearing_impaired,
        }
    except Exception:
        return None


def _select_episode_file(srt_files, video):
    """Select the best matching episode file from a ZIP archive."""
    if not srt_files:
        return None
    
    episode = (video or {}).get("episode")
    season = (video or {}).get("season")
    
    if episode is None:
        return srt_files[0]
    
    try:
        episode_int = int(episode)
    except (TypeError, ValueError):
        return srt_files[0]
    
    try:
        season_int = int(season)
    except (TypeError, ValueError):
        season_int = None
    
    def score(name):
        base = name.lower()
        # Prefer explicit SxxExx markers
        if season_int is not None:
            pattern = rf"s{season_int:02d}e{episode_int:02d}"
            if pattern in base:
                return 200
            # Also match unpadded: s1e2
            pattern_unpadded = rf"s{season_int}e{episode_int}"
            if pattern_unpadded in base:
                return 200
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
    
    # If no episode match found, return first file as fallback
    if best_score == 0:
        return srt_files[0]
    
    return best_name


def _download_subtitle(download_url, delay_ms=0, video=None):
    """Download and extract subtitle ZIP file."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    
    full_url = f"{BASE_URL}{download_url}" if download_url.startswith("/") else download_url
    
    try:
        zip_data = _http_get(full_url)
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            srt_files = [name for name in zf.namelist() if name.lower().endswith(".srt")]
            if not srt_files:
                return None
            
            # Select episode-specific file if video metadata available
            srt_file = _select_episode_file(srt_files, video)
            content = zf.read(srt_file)
            
            return {
                "content": content,
                "filename": srt_file,
                "format": "srt",
            }
    except Exception:
        return None


def _coerce_text(value):
    """Coerce value to string, handling lists/tuples."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if item)
    return str(value)


def _calculate_score(video, subtitle):
    """Calculate match score based on video metadata."""
    score = 60
    release = subtitle.get("release", "").lower()
    
    if video.get("kind") == "movie":
        year = video.get("year")
        if year and str(year) in release:
            score += 20
        
        source = _coerce_text(video.get("source")).lower()
        if source:
            if "bluray" in source and "bluray" in release:
                score += 10
            elif "web" in source and ("web" in release or "webrip" in release or "web-dl" in release):
                score += 10
            elif "hdtv" in source and "hdtv" in release:
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

    def search(self, video, languages, config):
        """Search for subtitles matching the video."""
        if not video or not languages:
            return []
        
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
            search_results = _search_subscene(query, delay_ms)
            
            for result in search_results[:5]:
                subtitles = _get_detail_page(result["url"], delay_ms)
                
                for subtitle in subtitles:
                    lang_name = subtitle.get("language", "")
                    lang_code = _get_language_code(lang_name)
                    
                    if not lang_code or lang_code not in requested_alpha3:
                        continue
                    
                    subtitle_url = subtitle.get("url", "")
                    subtitle_id = subtitle_url.split("/")[-1] if subtitle_url else "unknown"
                    
                    # Deduplicate by subtitle ID
                    if subtitle_id in seen_ids:
                        continue
                    seen_ids.add(subtitle_id)
                    
                    score = _calculate_score(video, subtitle)
                    
                    results.append({
                        "provider": PROVIDER_ID,
                        "id": f"{PROVIDER_ID}_{subtitle_id}",
                        "language": {
                            "alpha3": lang_code,
                            "alpha2": lang_code,
                            "hi": False,
                            "forced": False,
                        },
                        "release_info": subtitle.get("release", ""),
                        "filename": f"{PROVIDER_ID}.{subtitle_id}.srt",
                        "matches": [],
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
        
        delay_ms = config.get("request_delay_ms", 0)
        
        detail = _get_subtitle_detail(subtitle["url"], delay_ms)
        if not detail or not detail.get("download_url"):
            raise ValueError("sub_scene could not find download URL on detail page")
        
        # Pass video metadata to select episode-specific file from ZIP
        video = subtitle.get("video")
        downloaded = _download_subtitle(detail["download_url"], delay_ms, video)
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
            "content_type": "application/x-subrip",
            "format": downloaded["format"],
            "encoding": encoding,
            "empty": False,
        }
