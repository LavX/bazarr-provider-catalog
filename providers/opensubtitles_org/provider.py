"""OpenSubtitles.org provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

PROVIDER_ID = "opensubtitles_org"
LEGACY_PROVIDER_ID = "opensubtitles"
BASE_URL = "https://www.opensubtitles.org"
DOWNLOAD_BASE_URL = "https://dl.opensubtitles.org"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 60000
SUBTITLE_FORMAT = "srt"

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_EPISODE_TAG_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SEARCH_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?</tr>)", re.I | re.S)
_HREF_RE = re.compile(r"""href=["'](?P<href>[^"']+)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SUBTITLE_LINK_RE = re.compile(r"""href=["'](?P<href>/en/subtitles/(?P<id>\d+)[^"']*)["']""", re.I)
_DOWNLOAD_LINK_RE = re.compile(
    r"""href=["'](?P<href>(?:https?://(?:www\.|dl\.)?opensubtitles\.org)?/(?:en/)?(?:download|subtitleserve)/(?:sub/)?[^"']+)["']""",
    re.I,
)
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)

_ALPHA3_TO_ALPHA2 = {
    "ara": "ar",
    "bos": "bs",
    "bul": "bg",
    "cat": "ca",
    "ces": "cs",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "est": "et",
    "eus": "eu",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "glg": "gl",
    "heb": "he",
    "hrv": "hr",
    "hun": "hu",
    "ind": "id",
    "isl": "is",
    "ita": "it",
    "jpn": "ja",
    "kat": "ka",
    "kor": "ko",
    "lav": "lv",
    "lit": "lt",
    "mkd": "mk",
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
_ALPHA2_TO_ALPHA3 = {value: key for key, value in _ALPHA3_TO_ALPHA2.items()}

_ALPHA3_TO_OPENSUBTITLES = {
    "ces": "cze",
    "deu": "ger",
    "ell": "gre",
    "eus": "baq",
    "fas": "per",
    "fra": "fre",
    "hye": "arm",
    "isl": "ice",
    "kat": "geo",
    "mkd": "mac",
    "msa": "may",
    "mya": "bur",
    "nld": "dut",
    "ron": "rum",
    "slk": "slo",
    "sqi": "alb",
    "zho": "chi",
}
_OPENSUBTITLES_TO_ALPHA3 = {
    value: key for key, value in _ALPHA3_TO_OPENSUBTITLES.items()
}
_OPENSUBTITLES_TO_ALPHA3.update(
    {
        "alb": "sqi",
        "arm": "hye",
        "baq": "eus",
        "bur": "mya",
        "chi": "zho",
        "cze": "ces",
        "dut": "nld",
        "fre": "fra",
        "geo": "kat",
        "ger": "deu",
        "gre": "ell",
        "ice": "isl",
        "mac": "mkd",
        "may": "msa",
        "per": "fas",
        "pob": "por",
        "rum": "ron",
        "scc": "srp",
        "slo": "slk",
        "spl": "spa",
        "zht": "zho",
    }
)


class OpenSubtitlesError(RuntimeError):
    """Base class for OpenSubtitles.org failures."""


class RateLimited(OpenSubtitlesError):
    """The upstream service asked the caller to slow down."""


class ServiceUnavailable(OpenSubtitlesError):
    """The upstream service is not currently usable."""


class _MissingCloudscraper:
    @staticmethod
    def create_scraper(**kwargs):
        raise ServiceUnavailable("OpenSubtitles.org ai-cloudscraper dependency is not installed")


if cloudscraper is None:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = _MissingCloudscraper()


@dataclass(frozen=True)
class LanguageInfo:
    alpha3: str
    alpha2: str | None = None
    country_alpha2: str | None = None
    forced: bool = False
    hi: bool = False

    @property
    def key(self):
        return (self.alpha3, self.country_alpha2, self.forced, self.hi)

    def payload(self):
        payload = {"alpha3": self.alpha3, "forced": self.forced, "hi": self.hi}
        if self.alpha2:
            payload["alpha2"] = self.alpha2
        if self.country_alpha2:
            payload["country_alpha2"] = self.country_alpha2
        return payload


@dataclass(frozen=True)
class SearchContext:
    kind: str | None
    query: list[str]
    imdb_id: str | None
    hash_value: str | None
    size: int | str | None
    season: int | None
    episode: int | None
    tag: str | None
    use_tag_search: bool = False


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value if item not in (None, ""))
    return _WS_RE.sub(" ", html.unescape(str(value))).strip()


def _strip_tags(value):
    return _clean_text(_TAG_RE.sub(" ", value or ""))


def _normalize(value):
    return _NON_ALNUM_RE.sub("", _clean_text(value).lower())


def _imdb_without_prefix(value):
    value = _clean_text(value)
    if value.lower().startswith("tt"):
        return value[2:]
    return value


def _normalize_imdb(value):
    stripped = _imdb_without_prefix(value).lstrip("0")
    return stripped or None


def _with_tt(value):
    value = _clean_text(value)
    if not value:
        return None
    return value if value.lower().startswith("tt") else f"tt{value}"


def _language_from_payload(payload):
    alpha3 = _clean_text((payload or {}).get("alpha3"))
    alpha2 = _clean_text((payload or {}).get("alpha2")) or None
    country = _clean_text((payload or {}).get("country_alpha2")) or None
    if not alpha3 and alpha2:
        alpha3 = _ALPHA2_TO_ALPHA3.get(alpha2.lower(), alpha2.lower())
    if not alpha3:
        return None
    alpha3 = alpha3.lower()
    return LanguageInfo(
        alpha3=alpha3,
        alpha2=alpha2.lower() if alpha2 else _ALPHA3_TO_ALPHA2.get(alpha3),
        country_alpha2=country.upper() if country else None,
        forced=_as_bool((payload or {}).get("forced")),
        hi=_as_bool((payload or {}).get("hi") or (payload or {}).get("hearing_impaired")),
    )


def _language_from_alpha2(alpha2, forced=False, hi=False):
    code = _clean_text(alpha2).lower()
    alpha3 = _ALPHA2_TO_ALPHA3.get(code)
    if not alpha3:
        return None
    return LanguageInfo(alpha3=alpha3, alpha2=code, forced=forced, hi=hi)


def _opensubtitles_code(language):
    alpha3 = language.alpha3
    if alpha3 == "por" and language.country_alpha2 == "BR":
        return "pob"
    if alpha3 == "spa" and language.country_alpha2 == "MX":
        return "spl"
    return _ALPHA3_TO_OPENSUBTITLES.get(alpha3, alpha3)


def _requested_languages(languages):
    parsed = []
    for payload in languages or []:
        language = _language_from_payload(payload)
        if language:
            parsed.append(language)
    return parsed


def _requested_keys(languages):
    return {language.key for language in _requested_languages(languages)}


def _language_requested(language, languages):
    return language.key in _requested_keys(languages)


def _subtitle_language_codes(languages):
    codes = []
    seen = set()
    for language in _requested_languages(languages):
        code = _opensubtitles_code(language)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return sorted(codes)


def _content_payload(content, format_=SUBTITLE_FORMAT, encoding=None):
    if isinstance(content, str):
        content = content.encode(encoding or "utf-8")
    digest = hashlib.sha256(content).hexdigest()
    payload = {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": digest,
        "empty": False,
        "format": format_,
    }
    if encoding:
        payload["encoding"] = encoding
    return payload


def build_search_context(video, config):
    video = video or {}
    kind = video.get("kind")
    query = []
    season = None
    episode = None
    if kind == "episode":
        series = _clean_text(video.get("series"))
        if series:
            query.append(series)
        season = _as_int(video.get("season"))
        episode_value = video.get("episode")
        if isinstance(episode_value, list):
            episode = min((_as_int(item) for item in episode_value if _as_int(item) is not None), default=None)
        else:
            episode = _as_int(episode_value)
        imdb_id = _clean_text(video.get("imdb_id")) or _clean_text(video.get("series_imdb_id")) or None
    elif kind == "movie":
        title = _clean_text(video.get("title"))
        if title:
            query.append(title)
        imdb_id = _clean_text(video.get("imdb_id")) or None
    else:
        imdb_id = None

    hashes = video.get("hashes") or {}
    return SearchContext(
        kind=kind,
        query=query,
        imdb_id=imdb_id,
        hash_value=hashes.get(LEGACY_PROVIDER_ID) or hashes.get(PROVIDER_ID),
        size=video.get("size"),
        season=season,
        episode=episode,
        tag=_clean_text(video.get("original_name")) or None,
        use_tag_search=_as_bool((config or {}).get("use_tag_search")),
    )


def _wrong_fps(video, subtitle_fps):
    video_fps = _as_float((video or {}).get("fps"))
    sub_fps = _as_float(subtitle_fps)
    if not video_fps or not sub_fps:
        return False
    return abs(video_fps - sub_fps) > 0.02


def _matches_for_video(video, movie_kind, movie_name, release_name, movie_year, movie_imdb_id, season, episode, hash_value):
    matches = set()
    kind = (video or {}).get("kind")
    if kind == "episode" and movie_kind == "episode":
        video_series = _clean_text(video.get("series"))
        if video_series and _normalize(video_series) in _normalize(movie_name):
            matches.add("series")
        if season is not None and _as_int(video.get("season")) == season:
            matches.add("season")
        if episode is not None and _as_int(video.get("episode")) == episode:
            matches.add("episode")
        tag_match = _EPISODE_TAG_RE.search(release_name or "")
        if tag_match:
            if _as_int(tag_match.group("season")) == _as_int(video.get("season")):
                matches.add("season")
            if _as_int(tag_match.group("episode")) == _as_int(video.get("episode")):
                matches.add("episode")
    elif kind == "movie" and movie_kind == "movie":
        if video.get("title") and _normalize(video.get("title")) == _normalize(movie_name):
            matches.add("title")
        if video.get("year") and movie_year and int(video.get("year")) == int(movie_year):
            matches.add("year")

    hashes = (video or {}).get("hashes") or {}
    if hash_value and hash_value in {hashes.get(LEGACY_PROVIDER_ID), hashes.get(PROVIDER_ID)}:
        matches.add("hash")

    target_ids = [video.get("imdb_id")]
    if kind == "episode":
        target_ids.append(video.get("series_imdb_id"))
    if _normalize_imdb(movie_imdb_id) in {_normalize_imdb(item) for item in target_ids if item}:
        matches.add("imdb_id")
    return sorted(matches)


def _candidate(
    *,
    subtitle_id,
    language,
    page_link,
    movie_kind,
    movie_name,
    release_name,
    movie_year,
    movie_imdb_id,
    season,
    episode,
    filename,
    fps,
    hash_value,
    uploader,
    download_count,
    video,
):
    subtitle_id = str(subtitle_id)
    matches = _matches_for_video(
        video or {},
        movie_kind,
        movie_name,
        release_name,
        movie_year,
        movie_imdb_id,
        season,
        episode,
        hash_value,
    )
    return {
        "id": f"{PROVIDER_ID}-native-{subtitle_id}",
        "provider": PROVIDER_ID,
        "language": language.payload(),
        "hearing_impaired": language.hi,
        "hash_verifiable": True,
        "hearing_impaired_verifiable": True,
        "page_link": page_link,
        "release_info": release_name,
        "filename": filename,
        "uploader": uploader or "anonymous",
        "matches": matches,
        "provider_payload": {
            "provider": PROVIDER_ID,
            "legacy_provider_id": LEGACY_PROVIDER_ID,
            "schema": 1,
            "mode": "native",
            "subtitle_id": subtitle_id,
            "download_url": page_link,
            "filename": filename,
            "release_info": release_name,
        },
        "display": {
            "download_count": int(download_count or 0),
            "fps": fps,
            "matched_by": "imdbid",
            "legacy_provider_id": LEGACY_PROVIDER_ID,
        },
    }


def is_anubis_challenge(url, status_code=0):
    return "/.within.website/" in (url or "") or (
        status_code in (307, 401, 403) and ".within.website" in (url or "")
    )


def _extract_anubis_challenge(html_text):
    meta_match = _META_REFRESH_RE.search(html_text or "")
    if meta_match and "/.within.website/" in meta_match.group("url"):
        return {
            "method": "metarefresh",
            "redirect_url": meta_match.group("url"),
            "delay": int(meta_match.group("delay")),
            "difficulty": 0,
        }
    match = _ANUBIS_CHALLENGE_RE.search(html_text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group("json"))
    except json.JSONDecodeError:
        return None
    challenge = data.get("challenge") or {}
    if "randomData" not in challenge or "id" not in challenge:
        return None
    return {
        "id": challenge["id"],
        "randomData": challenge["randomData"],
        "difficulty": int(challenge.get("difficulty", 4)),
        "method": challenge.get("method", "fast"),
    }


def _solve_pow(random_data, difficulty):
    prefix = "0" * difficulty
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{random_data}{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith(prefix):
            return nonce, digest
        nonce += 1


def _solve_preact(random_data, difficulty):
    return hashlib.sha256(random_data.encode("utf-8")).hexdigest(), difficulty * 0.125


def solve_anubis_challenge(session, challenge_url, original_url, timeout=DEFAULT_TIMEOUT_SECONDS):
    del original_url
    parsed = urllib.parse.urlparse(challenge_url)
    query = urllib.parse.parse_qs(parsed.query)
    redir = query.get("redir", [parsed.path])[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    challenge_page_url = challenge_url if challenge_url.startswith("http") else base + challenge_url
    started = time.monotonic()

    response = session.get(challenge_page_url, timeout=(10, timeout), allow_redirects=True)
    challenge = _extract_anubis_challenge(response.text)
    if not challenge:
        return None

    method = challenge["method"]
    if method == "metarefresh":
        redirect_url = challenge["redirect_url"]
        if not redirect_url.startswith("http"):
            redirect_url = base + redirect_url
        time.sleep(challenge.get("delay", 1))
        solved = session.get(redirect_url, timeout=(10, timeout), allow_redirects=True)
        if solved.cookies:
            session.cookies.update(solved.cookies)
    elif method == "preact":
        result, delay = _solve_preact(challenge["randomData"], challenge["difficulty"])
        time.sleep(delay)
        params = {
            "id": challenge["id"],
            "result": result,
            "redir": redir,
            "elapsedTime": str(int((time.monotonic() - started) * 1000)),
        }
        solved = session.get(
            f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?{urllib.parse.urlencode(params)}",
            timeout=(10, timeout),
            allow_redirects=False,
        )
        if solved.cookies:
            session.cookies.update(solved.cookies)
    else:
        nonce, digest = _solve_pow(challenge["randomData"], challenge["difficulty"])
        params = {
            "id": challenge["id"],
            "response": digest,
            "nonce": str(nonce),
            "redir": redir,
            "elapsedTime": str(int((time.monotonic() - started) * 1000)),
        }
        solved = session.get(
            f"{base}/.within.website/x/cmd/anubis/api/pass-challenge?{urllib.parse.urlencode(params)}",
            timeout=(10, timeout),
            allow_redirects=False,
        )
        if solved.cookies:
            session.cookies.update(solved.cookies)

    cookies = {}
    for cookie in session.cookies:
        if "anubis" in cookie.name.lower() or cookie.name == "PHPSESSID":
            cookies[cookie.name] = cookie.value
    return cookies or None


def _is_cloudflare_challenge(response):
    headers = getattr(response, "headers", {}) or {}
    text = (getattr(response, "text", "") or "").lower()
    status = getattr(response, "status_code", 0)
    if headers.get("cf-ray") and status in {403, 503}:
        return True
    if status == 200:
        title_match = re.search(r"<title[^>]*>\s*([^<]+)", text)
        return bool(title_match and "just a moment" in title_match.group(1))
    if status not in {403, 503}:
        return False
    return any(
        marker in text
        for marker in (
            "just a moment",
            "challenge-platform",
            "cf-turnstile",
            "cf_chl",
            "cf-spinner",
        )
    )


def _absolute_url(url, base=BASE_URL):
    value = html.unescape(_clean_text(url))
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith(("http://", "https://")):
        return value
    return urllib.parse.urljoin(base, value)


def _response_text(response):
    return getattr(response, "text", "") or (getattr(response, "content", b"") or b"").decode("utf-8", "replace")


def _language_from_subtitle_url(url):
    slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    if "-" in slug:
        code = slug.rsplit("-", 1)[-1].lower()
        if len(code) == 2:
            return _language_from_alpha2(code)
    return _language_from_alpha2("en")


def _parse_search_results(html_text, fallback_url, fallback_kind):
    results = []
    for row_match in _SEARCH_ROW_RE.finditer(html_text or ""):
        row = row_match.group("body")
        if "id=\"search_results\"" in row.lower():
            continue
        href_match = _HREF_RE.search(row)
        if not href_match:
            continue
        href = href_match.group("href")
        if "/en/search/" not in href and "/en/subtitles/" not in href:
            continue
        title = _strip_tags(row)
        if not title:
            continue
        imdb_match = re.search(r"tt(?P<id>\d+)", row)
        year_match = re.search(r"\((?P<year>\d{4})\)", title)
        count_match = re.search(r">(?P<count>\d+)<", row)
        clean_title = re.sub(r"\s*\(\d{4}\).*$", "", title).strip()
        clean_title = re.sub(r"^\s*\"([^\"]+)\".*$", r"\1", clean_title).strip()
        results.append(
            {
                "title": clean_title,
                "year": int(year_match.group("year")) if year_match else None,
                "imdb_id": f"tt{imdb_match.group('id')}" if imdb_match else None,
                "url": _absolute_url(href),
                "subtitle_count": int(count_match.group("count")) if count_match else 0,
                "kind": fallback_kind or "movie",
            }
        )
    if not results and "/en/subtitles/" in (html_text or ""):
        results.append(
            {
                "title": "",
                "year": None,
                "imdb_id": None,
                "url": fallback_url,
                "subtitle_count": 1,
                "kind": fallback_kind or "movie",
            }
        )
    return results


def _score_result(result, imdb_id, query, year):
    if imdb_id and result.get("imdb_id") == imdb_id:
        return 10_000
    score = 0
    query_norm = _normalize(query)
    title_norm = _normalize(result.get("title"))
    if query_norm and title_norm:
        if query_norm == title_norm:
            score += 100
        elif query_norm in title_norm or title_norm in query_norm:
            score += 50
    if year and result.get("year") == year:
        score += 30
    return score


def select_best_result(results, imdb_id, query, year):
    if not results:
        return None
    return max(results, key=lambda item: _score_result(item, imdb_id, query, year))


def _parse_subtitle_rows(html_text, movie_url):
    subtitles = []
    for row_match in _SEARCH_ROW_RE.finditer(html_text or ""):
        row = row_match.group("body")
        link_match = _SUBTITLE_LINK_RE.search(row)
        if not link_match:
            continue
        page_link = _absolute_url(link_match.group("href"), movie_url)
        subtitle_id = link_match.group("id")
        title_text = _strip_tags(row)
        year_match = re.search(r"\((?P<year>\d{4})\)", title_text)
        release_name = ""
        main_text = _strip_tags(re.sub(r"<strong\b.*?</strong>", "", row, flags=re.I | re.S))
        for part in re.split(r"\s{2,}", main_text):
            if part and "x" != part and not part.endswith("x"):
                release_name = part
                break
        if not release_name:
            release_name = re.sub(r"\s*\(\d{4}\).*$", "", title_text).strip()

        download_match = re.search(r"(?P<count>\d+)x", row)
        fps_match = re.search(
            r"""<span[^>]*class=["'][^"']*\bp\b[^"']*["'][^>]*>\s*(?P<fps>\d{2}\.\d{3})""",
            row,
            re.I,
        )
        uploader_match = re.search(r"/en/profile/[^\"']+[\"'][^>]*>(?P<name>.*?)</a>", row, re.I | re.S)
        language = _language_from_subtitle_url(page_link)
        if not language:
            continue
        subtitles.append(
            {
                "subtitle_id": subtitle_id,
                "language": language,
                "filename": f"{release_name.replace(' ', '.')}.{language.alpha2 or language.alpha3}.srt",
                "release_name": release_name,
                "uploader": _strip_tags(uploader_match.group("name")) if uploader_match else "anonymous",
                "download_count": int(download_match.group("count")) if download_match else 0,
                "fps": float(fps_match.group("fps")) if fps_match else None,
                "download_url": page_link,
                "movie_year": int(year_match.group("year")) if year_match else None,
            }
        )
    return subtitles


def _extract_subtitle_from_zip(content, preferred_filename=None):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        subtitle_names = [
            name
            for name in names
            if os.path.splitext(name.lower())[1] in {".srt", ".ass", ".ssa", ".vtt", ".sub"}
        ]
        if not subtitle_names:
            raise ServiceUnavailable("OpenSubtitles.org archive contained no subtitle file")
        selected = subtitle_names[0]
        if preferred_filename:
            preferred = os.path.basename(preferred_filename).lower()
            for name in subtitle_names:
                if os.path.basename(name).lower() == preferred:
                    selected = name
                    break
        return archive.read(selected), selected


class OpenSubtitlesOrgProvider:
    def __init__(self):
        self._session = None
        self._last_request_at = 0.0

    def search(self, video, languages, config):
        config = config or {}
        context = build_search_context(video, config)
        query = context.query[0] if context.query else _clean_text((video or {}).get("title") or (video or {}).get("series"))
        search_url = self._build_search_url(query, context)
        search_response = self._http_get(search_url, config)
        search_html = _response_text(search_response)
        direct_items = _parse_subtitle_rows(search_html, search_url)
        if direct_items:
            direct_result = {
                "title": query,
                "year": (video or {}).get("year"),
                "imdb_id": context.imdb_id,
                "kind": context.kind or "movie",
            }
            return self._candidates_from_items(
                direct_items, direct_result, video or {}, languages or [], context, config, set()
            )
        results = _parse_search_results(search_html, search_url, context.kind)
        best_result = select_best_result(results, context.imdb_id, query, (video or {}).get("year"))
        if not best_result:
            return []
        return self._subtitles_for_result(best_result, video or {}, languages or [], context, config)

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        subtitle_id = str(payload.get("subtitle_id") or "")
        if not subtitle_id:
            raise ValueError("opensubtitles.org download requires subtitle_id")
        direct_url = f"{DOWNLOAD_BASE_URL}/en/download/sub/{subtitle_id}"
        response = self._http_get(direct_url, config or {})
        content = getattr(response, "content", b"") or b""
        content_type = (getattr(response, "headers", {}) or {}).get("content-type", "").lower()
        if content.startswith(b"PK") or "zip" in content_type:
            content, filename = _extract_subtitle_from_zip(content, payload.get("filename"))
            return _content_payload(content, format_=os.path.splitext(filename)[1].lstrip(".") or SUBTITLE_FORMAT)
        if b"<html" in content[:200].lower():
            page_html = _response_text(response)
            match = _DOWNLOAD_LINK_RE.search(page_html)
            if not match:
                raise ServiceUnavailable("OpenSubtitles.org download page contained no subtitle link")
            response = self._http_get(_absolute_url(match.group("href"), BASE_URL), config or {})
            content = getattr(response, "content", b"") or b""
            if content.startswith(b"PK"):
                content, filename = _extract_subtitle_from_zip(content, payload.get("filename"))
                return _content_payload(content, format_=os.path.splitext(filename)[1].lstrip(".") or SUBTITLE_FORMAT)
        if not content:
            raise ServiceUnavailable("OpenSubtitles.org downloaded empty subtitle")
        return _content_payload(content, format_=os.path.splitext(payload.get("filename") or "")[1].lstrip(".") or SUBTITLE_FORMAT)

    def _build_search_url(self, query, context):
        if context.imdb_id:
            imdb_number = re.sub(r"\D", "", context.imdb_id)
            return f"{BASE_URL}/en/search/sublanguageid-all/imdbid-{imdb_number}"
        params = {"MovieName": query, "action": "search"}
        if context.kind == "episode":
            params["SearchOnlyTVSeries"] = "on"
        elif context.kind == "movie":
            params["SearchOnlyMovies"] = "on"
        return f"{BASE_URL}/en/search2?{urllib.parse.urlencode(params)}"

    def _subtitles_for_result(self, result, video, languages, context, config):
        language_codes = _subtitle_language_codes(languages)
        page_urls = []
        if language_codes:
            for language_code in language_codes:
                page_urls.append(result["url"].replace("sublanguageid-all", f"sublanguageid-{language_code}"))
        else:
            page_urls.append(result["url"])
        candidates = []
        seen = set()
        for page_url in page_urls:
            response = self._http_get(page_url, config)
            candidates.extend(
                self._candidates_from_items(
                    _parse_subtitle_rows(_response_text(response), page_url),
                    result,
                    video,
                    languages,
                    context,
                    config,
                    seen,
                )
            )
        return candidates

    def _candidates_from_items(self, items, result, video, languages, context, config, seen):
        candidates = []
        for item in items:
            if item["subtitle_id"] in seen:
                continue
            seen.add(item["subtitle_id"])
            language = item["language"]
            if not _language_requested(language, languages):
                continue
            if _as_bool(config.get("skip_wrong_fps"), default=True) and _wrong_fps(video, item["fps"]):
                continue
            candidates.append(
                _candidate(
                    subtitle_id=item["subtitle_id"],
                    language=language,
                    page_link=item["download_url"],
                    movie_kind=context.kind or result.get("kind") or "movie",
                    movie_name=result.get("title") or video.get("series") or video.get("title") or "",
                    release_name=item["release_name"],
                    movie_year=item["movie_year"] or result.get("year"),
                    movie_imdb_id=result.get("imdb_id") or context.imdb_id,
                    season=context.season,
                    episode=context.episode,
                    filename=item["filename"],
                    fps=item["fps"],
                    hash_value=context.hash_value,
                    uploader=item["uploader"],
                    download_count=item["download_count"],
                    video=video,
                )
            )
        return candidates

    def _get_session(self):
        if self._session is None:
            self._session = cloudscraper.create_scraper(
                browser={"custom": USER_AGENT},
                interpreter="native",
                enable_cookie_persistence=False,
                debug=False,
            )
            self._session.headers.update({"User-Agent": USER_AGENT})
        return self._session

    def _http_get(self, url, config):
        config = config or {}
        self._apply_delay(config)
        session = self._get_session()
        timeout = _as_int(config.get("timeout")) or DEFAULT_TIMEOUT_SECONDS
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
        except Exception as exc:
            raise ServiceUnavailable(f"OpenSubtitles.org request failed: {exc}") from exc
        if is_anubis_challenge(getattr(response, "url", ""), getattr(response, "status_code", 0)):
            solved = solve_anubis_challenge(session, response.url, url, timeout=timeout)
            if not solved:
                raise ServiceUnavailable("OpenSubtitles.org Anubis challenge could not be solved")
            response = session.get(url, timeout=timeout, allow_redirects=True)
        if _is_cloudflare_challenge(response):
            self._fallback_to_flaresolverr(url, config)
            response = session.get(url, timeout=timeout, allow_redirects=True)
            if _is_cloudflare_challenge(response):
                raise ServiceUnavailable("OpenSubtitles.org Cloudflare challenge remained after FlareSolverr fallback")
        status = getattr(response, "status_code", 200)
        if status == 429:
            raise RateLimited("OpenSubtitles.org rate limited the request")
        if status >= 500:
            raise ServiceUnavailable(f"OpenSubtitles.org HTTP {status}")
        return response

    def _apply_delay(self, config):
        delay_ms = _as_int((config or {}).get("request_delay_ms")) or 0
        if delay_ms <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait_for = delay_ms / 1000 - elapsed
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()

    def _fallback_to_flaresolverr(self, url, config):
        endpoint = _clean_text((config or {}).get("flaresolverr_url"))
        if not endpoint:
            raise ServiceUnavailable("OpenSubtitles.org Cloudflare challenge requires optional FlareSolverr URL")
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": _as_int(config.get("flaresolverr_timeout_ms")) or DEFAULT_FLARESOLVERR_TIMEOUT_MS,
        }
        data = self._post_flaresolverr(endpoint, payload, timeout=max(payload["maxTimeout"] / 1000 + 10, 20))
        solution = data.get("solution") or {}
        self._inject_solution(solution)

    def _post_flaresolverr(self, url, payload, timeout):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read()
        except urllib.error.URLError as exc:
            raise ServiceUnavailable(f"FlareSolverr unavailable: {exc.reason}") from exc
        try:
            data = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ServiceUnavailable("FlareSolverr returned invalid JSON") from exc
        if data.get("status") == "error":
            raise ServiceUnavailable(f"FlareSolverr error: {data.get('message') or 'unknown error'}")
        return data

    def _inject_solution(self, solution):
        session = self._get_session()
        user_agent = solution.get("userAgent")
        if user_agent:
            session.headers["User-Agent"] = user_agent
        for cookie in solution.get("cookies") or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            session.cookies.set(
                name,
                value,
                domain=cookie.get("domain") or ".opensubtitles.org",
                path=cookie.get("path") or "/",
            )
