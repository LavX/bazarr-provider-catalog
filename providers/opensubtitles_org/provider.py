"""OpenSubtitles.org provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, replace as _dataclass_replace

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

PROVIDER_ID = "opensubtitles"
BASE_URL = "https://www.opensubtitles.org"
DOWNLOAD_BASE_URL = "https://dl.opensubtitles.org"
# Registrable host of the site, used to check that a redirect stayed on it.
_SITE_DOMAIN = "opensubtitles.org"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 60000
SUBTITLE_FORMAT = "srt"

# Transport-level retry for raw network blips only. Matches upstream subliminal's
# RetryingSession/ProviderRetryMixin (about three tries with exponential backoff).
# This wraps only the urllib/cloudscraper GET; it never retries challenge
# responses, 4xx other than 429, or any non-network error.
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0

# Re-solve a fresh anti-bot challenge on a transient 401/403 (Anubis / Cloudflare rate
# limit) before failing the search. opensubtitles.org issues a new Anubis challenge per
# request and rate-limits bursts; the embedded proof-of-work solves reliably, so a brief
# re-solve recovers the block instead of losing the whole search on the first 401.
CHALLENGE_RETRY_ATTEMPTS = 3
# opensubtitles.org stacks Cloudflare in FRONT of Anubis, so a single fetch can surface a
# CF gate, then an Anubis gate once CF clears. Resolve whichever gate is present and
# re-fetch this many rounds within one attempt so a CF->Anubis (or Anubis->CF) chain
# clears in a single pass instead of leaning on the per-attempt retry.
CHALLENGE_GATE_ROUNDS = 3
_RETRYABLE_TRANSPORT_ERRORS = (
    urllib.error.URLError,
    socket.timeout,
    TimeoutError,
    ConnectionError,
)

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_EPISODE_TAG_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SEARCH_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?</tr>)", re.I | re.S)
_HREF_RE = re.compile(r"""href=["'](?P<href>[^"']+)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_SUBTITLE_LINK_RE = re.compile(r"""href=["'](?P<href>/en/subtitles/(?P<id>\d+)[^"']*)["']""", re.I)
_SUBLANGUAGE_RE = re.compile(r"/sublanguageid-(?P<code>[a-z0-9,]+)", re.I)
_SEARCH_FORM_PATH_RE = re.compile(r"/search2/?$", re.I)
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
    # OpenSubtitles.org identifies Serbian as "scc", not the standard "srp".
    # Asking for "srp" returns nothing, which is why Serbian appeared to be
    # missing from the site entirely. The legacy built-in provider maps this
    # too; the mapping was lost when this plugin was written.
    "srp": "scc",
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
        # No "mne" entry: _language_from_opensubtitles_code answers Montenegrin
        # above, with the country this table cannot carry.
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


def _create_cloudscraper_session():
    kwargs = {
        "browser": {"custom": USER_AGENT},
        "interpreter": "native",
        "enable_cookie_persistence": False,
        "debug": False,
    }
    try:
        return cloudscraper.create_scraper(**kwargs)
    except TypeError as exc:
        if "enable_cookie_persistence" not in str(exc):
            raise
        kwargs.pop("enable_cookie_persistence")
        return cloudscraper.create_scraper(**kwargs)


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
    size: int | str | None
    season: int | None
    episode: int | None
    tag: str | None
    use_tag_search: bool = False
    hash: str | None = None


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


def _episode_numbers(value):
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    numbers = set()
    for item in items:
        parsed = _as_int(item)
        if parsed is not None:
            numbers.add(parsed)
    return numbers


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


def _language_from_opensubtitles_code(code, forced=False, hi=False):
    code = _clean_text(code).lower()
    if not code or code == "all" or "," in code:
        return None
    if code == "pob":
        return LanguageInfo(alpha3="por", alpha2="pt", country_alpha2="BR", forced=forced, hi=hi)
    if code == "spl":
        return LanguageInfo(alpha3="spa", alpha2="es", country_alpha2="MX", forced=forced, hi=hi)
    if code == "mne":
        return LanguageInfo(alpha3="srp", alpha2="sr", country_alpha2="ME", forced=forced, hi=hi)
    if len(code) == 2:
        return _language_from_alpha2(code, forced=forced, hi=hi)
    alpha3 = _OPENSUBTITLES_TO_ALPHA3.get(code, code if len(code) == 3 else "")
    if not alpha3:
        return None
    return LanguageInfo(
        alpha3=alpha3,
        alpha2=_ALPHA3_TO_ALPHA2.get(alpha3),
        forced=forced,
        hi=hi,
    )


def _opensubtitles_code(language):
    alpha3 = language.alpha3
    if alpha3 == "srp" and language.country_alpha2 == "ME":
        # The site distinguishes Montenegrin, and the manifest declares srp-ME.
        # Without this it would be asked for as plain Serbian.
        return "mne"
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


def _language_requested(language, languages, config=None):
    requested = _requested_languages(languages)
    if not requested:
        return False
    config = config or {}
    only_foreign = _as_bool(config.get("only_foreign"))
    also_foreign = _as_bool(config.get("also_foreign"))
    for requested_language in requested:
        if language.alpha3 != requested_language.alpha3:
            continue
        if not _country_matches_request(language, requested_language):
            continue
        if language.hi != requested_language.hi:
            continue
        if only_foreign:
            if language.forced:
                return True
            continue
        if requested_language.forced:
            if language.forced:
                return True
            continue
        if language.forced and not also_foreign:
            continue
        return True
    return False


def _country_matches_request(language, requested_language):
    if language.country_alpha2 == requested_language.country_alpha2:
        return True
    if not requested_language.country_alpha2 or language.country_alpha2:
        return False
    return _opensubtitles_code(language) == _opensubtitles_code(requested_language)


def _subtitle_language_codes(languages):
    codes = []
    seen = set()
    for language in _requested_languages(languages):
        code = _opensubtitles_code(language)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return sorted(codes)


def _score_from_matches(matches, include_hash=True):
    weights = {
        "hash": 100,
        "imdb_id": 40,
        "series": 20,
        "title": 20,
        "season": 10,
        "episode": 10,
        "year": 10,
    }
    score = 0
    for match in matches:
        if match == "hash" and not include_hash:
            continue
        score += weights.get(match, 0)
    return min(score, 100)


def _content_payload(content, format_=SUBTITLE_FORMAT):
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    if isinstance(content, str):
        content = content.encode("utf-8")
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(format_),
        "empty": False,
        "format": format_,
    }


def _content_type(format_):
    normalized = (format_ or SUBTITLE_FORMAT).lower()
    if normalized in {"ass", "ssa"}:
        return "text/x-ssa"
    if normalized == "vtt":
        return "text/vtt"
    if normalized == "sub":
        return "text/plain"
    return "application/x-subrip"


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
    movie_hash = _clean_text(hashes.get(PROVIDER_ID)) or None

    return SearchContext(
        kind=kind,
        query=query,
        imdb_id=imdb_id,
        size=video.get("size"),
        season=season,
        episode=episode,
        tag=_clean_text(video.get("original_name")) or None,
        use_tag_search=_as_bool((config or {}).get("use_tag_search")),
        hash=movie_hash,
    )


def _wrong_fps(video, subtitle_fps):
    video_fps = _as_float((video or {}).get("fps"))
    sub_fps = _as_float(subtitle_fps)
    if not video_fps or not sub_fps:
        return False
    return abs(video_fps - sub_fps) > 0.02


def _matches_for_video(
    video,
    movie_kind,
    movie_name,
    release_name,
    movie_year,
    movie_imdb_id,
    season,
    episode,
    subtitle_hash,
):
    matches = set()
    kind = (video or {}).get("kind")
    if kind == "episode" and movie_kind == "episode":
        video_series = _clean_text(video.get("series"))
        if video_series and _normalize(video_series) in _normalize(movie_name):
            matches.add("series")
        video_season = _as_int(video.get("season"))
        video_episodes = _episode_numbers(video.get("episode"))
        if video_season is None and season is not None:
            video_season = season
        if not video_episodes and episode is not None:
            video_episodes = {episode}
        if season is not None and video_season == season:
            matches.add("season")
        if episode is not None and episode in video_episodes:
            matches.add("episode")
        tag_match = _EPISODE_TAG_RE.search(release_name or "")
        if tag_match:
            if _as_int(tag_match.group("season")) == video_season:
                matches.add("season")
            if _as_int(tag_match.group("episode")) in video_episodes:
                matches.add("episode")
    elif kind == "movie" and movie_kind == "movie":
        if video.get("title") and _normalize(video.get("title")) == _normalize(movie_name):
            matches.add("title")
        if video.get("year") and movie_year and int(video.get("year")) == int(movie_year):
            matches.add("year")

    hashes = (video or {}).get("hashes") or {}
    if subtitle_hash and subtitle_hash == hashes.get(PROVIDER_ID):
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
    subtitle_hash,
    uploader,
    download_count,
    video,
    suppress_matches=False,
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
        subtitle_hash,
    )
    if suppress_matches:
        matches = []
    score = _score_from_matches(matches)
    score_without_hash = _score_from_matches(matches, include_hash=False)
    return {
        "id": f"{PROVIDER_ID}-native-{subtitle_id}",
        "provider": PROVIDER_ID,
        "language": language.payload(),
        "hearing_impaired": language.hi,
        "hash_verifiable": bool(subtitle_hash),
        "hearing_impaired_verifiable": True,
        "page_link": page_link,
        "release_info": release_name,
        "filename": filename,
        "uploader": uploader or "anonymous",
        "matches": matches,
        "score": score,
        "score_without_hash": score_without_hash,
        "score_out_of": 100,
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "mode": "native",
            "subtitle_id": subtitle_id,
            "download_url": page_link,
            "filename": filename,
            "release_info": release_name,
            "moviehash": subtitle_hash or None,
            "season": season,
            "episode": episode,
        },
        "display": {
            "download_count": int(download_count or 0),
            "fps": fps,
            "matched_by": "imdbid",
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


def _solve_pow(random_data, difficulty, deadline=None):
    prefix = "0" * difficulty
    nonce = 0
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise ServiceUnavailable("OpenSubtitles.org Anubis proof-of-work timed out")
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
    deadline = started + max(float(timeout or DEFAULT_TIMEOUT_SECONDS), 0.1)

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
        remaining = deadline - time.monotonic()
        if delay > remaining:
            raise ServiceUnavailable("OpenSubtitles.org Anubis preact challenge timed out")
        if delay > 0:
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
        nonce, digest = _solve_pow(challenge["randomData"], challenge["difficulty"], deadline=deadline)
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


def _is_site_url(url):
    host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    if not host:
        return False
    return host == _SITE_DOMAIN or host.endswith(f".{_SITE_DOMAIN}")


def _landed_url(response, requested_url):
    """The URL a fetch landed on, as long as it is still on the site.

    It becomes a refetch target, the base row hrefs are resolved against, and the
    source of the row language, so a redirect off the site would otherwise steer
    all three. Anything off-site falls back to the URL we asked for.
    """
    landed = _clean_text(getattr(response, "url", ""))
    if landed and _is_site_url(landed):
        return landed
    return requested_url


def _response_text(response):
    return getattr(response, "text", "") or (getattr(response, "content", b"") or b"").decode("utf-8", "replace")


def _page_language_code(url):
    """The site language id a listing URL is restricted to, or "" when it is not.

    The site spells the restriction two ways. A canonical listing carries a
    /sublanguageid-<code>/ path segment. The search form at /en/search2 carries
    its own SubLanguageID field instead, and a filtered request can be answered
    in place rather than redirected onto the canonical path, so the query string
    has to be read as well or such a page yields no language at all.
    """
    parsed = urllib.parse.urlparse(url or "")
    match = _SUBLANGUAGE_RE.search(parsed.path)
    if match:
        return match.group("code")
    for name, value in urllib.parse.parse_qsl(parsed.query):
        if name.lower() == "sublanguageid":
            return value
    return ""


def _language_from_page_url(url, forced=False, hi=False):
    return _language_from_opensubtitles_code(_page_language_code(url), forced=forced, hi=hi)


def _language_filtered_url(url, language_code):
    """Restrict a listing URL to one site language id, or None if it cannot be.

    Two shapes can be restricted. An imdb/tag/hash listing carries the filter as
    a /sublanguageid-all/ path segment, swapped in place. The search form at
    /en/search2 carries the form's own SubLanguageID field instead, and only
    grows a path segment once the site redirects onto the real listing.
    Everything else is left alone, including a listing already narrowed to one
    language, which the caller has no reason to refetch.

    Matching runs on the parsed components, since the URL now comes back from the
    site rather than being built here: a search term, a fragment or the site's own
    casing must not be mistaken for the filter.
    """
    parsed = urllib.parse.urlparse(url or "")
    match = _SUBLANGUAGE_RE.search(parsed.path)
    if match:
        if match.group("code").lower() != "all":
            return None
        head = parsed.path[: match.start()]
        tail = parsed.path[match.end() :]
        return urllib.parse.urlunparse(
            parsed._replace(path=f"{head}/sublanguageid-{language_code}{tail}")
        )
    if not _SEARCH_FORM_PATH_RE.search(parsed.path):
        return None
    params = [
        (name, value)
        for name, value in urllib.parse.parse_qsl(parsed.query)
        if name.lower() != "sublanguageid"
    ]
    params.append(("SubLanguageID", language_code))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(params)))


def _language_source_url(landed_url, requested_url):
    """The URL a fetched page's rows read their language off.

    Prefer the URL the fetch landed on: a redirect onto the canonical listing is
    how the site spells the filter it applied. Fall back to the URL we asked for
    when the landing one carries no filter, so a canonicalising redirect cannot
    strip the language off rows we did ask to have filtered.
    """
    if _page_language_code(landed_url):
        return landed_url
    return requested_url or landed_url


def _row_language_flags(row):
    text = _strip_tags(row).lower()
    hi = "hearing impaired" in text or "hearing-impaired" in text
    forced = "foreign parts" in text or "forced" in text
    return forced, hi


def _html_lines(fragment):
    with_breaks = re.sub(r"(?i)<br\s*/?>", "\n", fragment or "")
    text = _TAG_RE.sub(" ", with_breaks)
    return [_clean_text(line) for line in html.unescape(text).splitlines() if _clean_text(line)]


def _release_name_from_row(row, title_text):
    main = re.sub(r"<strong\b.*?</strong>", "\n", row, flags=re.I | re.S)
    for line in _html_lines(main):
        lower = line.lower()
        if lower in {"hearing impaired", "foreign parts only", "forced"}:
            continue
        if re.fullmatch(r"\d+x", line):
            continue
        if re.fullmatch(r"\d{2}\.\d{3}", line):
            continue
        return line
    return re.sub(r"\s*\(\d{4}\).*$", "", title_text).strip()


def _with_listing_country(language, listing_language):
    """Let the listing supply a country the row slug leaves out.

    The site has one id for Serbian and another for Montenegrin, but a row on a
    Montenegrin listing can still be slugged with the plain Serbian code. Read
    literally that row is country-less Serbian, which no longer satisfies a
    Montenegrin request, so a Montenegrin search comes back empty. The listing
    the site answered is the more specific statement of the two, so it supplies
    the country whenever the language itself agrees. A slug naming a different
    language, or one that already carries its own country, is left alone.
    """
    if not listing_language or not listing_language.country_alpha2:
        return language
    if language.country_alpha2 or language.alpha3 != listing_language.alpha3:
        return language
    return _dataclass_replace(language, country_alpha2=listing_language.country_alpha2)


def _language_from_subtitle_url(url, fallback_url=None, forced=False, hi=False):
    slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    listing_language = _language_from_page_url(fallback_url, forced=forced, hi=hi)
    if "-" in slug:
        code = slug.rsplit("-", 1)[-1].lower()
        language = _language_from_opensubtitles_code(code, forced=forced, hi=hi)
        if language:
            return _with_listing_country(language, listing_language)
    if listing_language:
        return listing_language
    # Do not guess. This used to default to English, which meant every row whose
    # language could not be determined was served to English searches as an
    # English subtitle. On a "sublanguageid-all" listing the page URL yields no
    # language either, so unparseable rows accumulated under English while the
    # language they were actually in went missing. The caller keeps such a row,
    # so the page still registers as a direct subtitle listing, and drops it when
    # candidates are built: absent beats wrong.
    return None


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


def _parse_subtitle_rows(html_text, movie_url, language_url=None):
    # movie_url is the base row hrefs are resolved against; language_url is what
    # a row with no language of its own falls back to. They differ when a fetch
    # lands on a URL that no longer carries the filter we asked for.
    language_url = language_url or movie_url
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
        release_name = _release_name_from_row(row, title_text)

        download_match = re.search(r"(?P<count>\d+)x", row)
        fps_match = re.search(
            r"""<span[^>]*class=["'][^"']*\bp\b[^"']*["'][^>]*>\s*(?P<fps>\d{2}\.\d{3})""",
            row,
            re.I,
        )
        uploader_match = re.search(r"/en/profile/[^\"']+[\"'][^>]*>(?P<name>.*?)</a>", row, re.I | re.S)
        forced, hi = _row_language_flags(row)
        language = _language_from_subtitle_url(page_link, language_url, forced=forced, hi=hi)
        # A row whose language cannot be resolved is KEPT, carrying None. The
        # caller decides whether a page is a direct subtitle listing by whether
        # this returns anything, so dropping such rows makes an unfiltered
        # listing look like a movie results page and the search returns nothing.
        # Candidates are filtered on language later instead.
        suffix = (language.alpha2 or language.alpha3) if language else "und"
        subtitles.append(
            {
                "subtitle_id": subtitle_id,
                "language": language,
                "filename": f"{release_name.replace(' ', '.')}.{suffix}.srt",
                "release_name": release_name,
                "uploader": _strip_tags(uploader_match.group("name")) if uploader_match else "anonymous",
                "download_count": int(download_match.group("count")) if download_match else 0,
                "fps": float(fps_match.group("fps")) if fps_match else None,
                "download_url": page_link,
                "movie_year": int(year_match.group("year")) if year_match else None,
                "hash_value": None,
            }
        )
    return subtitles


def _select_zip_member(content, preferred_filename=None):
    # List the archive with stdlib zipfile and pick a member, but leave the actual
    # extraction and encoding detection to the host (Provider Hub v1.1+).
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
    return selected


def _archive_payload(content, member):
    return {
        "archive_b64": base64.b64encode(content).decode("ascii"),
        "archive_sha256": hashlib.sha256(content).hexdigest(),
        "member": member,
    }


def _is_rar(content):
    return content[:4] == b"Rar!"


def _is_7z(content):
    return content[:6] == b"7z\xbc\xaf\x27\x1c"


def _episode_mismatch(item, context):
    if context.kind != "episode" or context.season is None or context.episode is None:
        return False
    match = _EPISODE_TAG_RE.search(item.get("release_name") or "")
    if not match:
        return False
    return (
        _as_int(match.group("season")) != context.season
        or _as_int(match.group("episode")) != context.episode
    )


# Transient exceptions raised by the requests/cloudscraper transport. They are
# matched by name so this provider does not need to import requests, and so a
# non-transient requests error (HTTPError, TooManyRedirects, etc.) is never
# retried.
_RETRYABLE_REQUESTS_ERROR_NAMES = frozenset(
    {
        "ConnectionError",
        "ConnectTimeout",
        "ReadTimeout",
        "Timeout",
        "ChunkedEncodingError",
    }
)


def _is_retryable_transport_error(exc):
    # urllib.error.HTTPError is a URLError subclass but carries an HTTP status; a
    # 4xx (other than 429) is a definitive answer, not a transient blip, so it
    # must propagate on the first occurrence.
    if isinstance(exc, urllib.error.HTTPError):
        code = getattr(exc, "code", 0) or 0
        return code == 429 or code >= 500
    if isinstance(exc, _RETRYABLE_TRANSPORT_ERRORS):
        return True
    # requests/cloudscraper transient transport errors, matched by class name so
    # the provider stays import-light. A requests HTTPError carries a response and
    # would have a 4xx/5xx status, so it is deliberately excluded here.
    module = type(exc).__module__ or ""
    if module.startswith("requests") or module.startswith("urllib3"):
        return type(exc).__name__ in _RETRYABLE_REQUESTS_ERROR_NAMES
    return False


def _retry_after_seconds(response):
    headers = getattr(response, "headers", None) or {}
    getter = getattr(headers, "get", None)
    raw = getter("Retry-After") if getter else None
    value = _as_int(raw)
    if value is None or value < 0:
        return None
    return min(float(value), RETRY_BACKOFF_CAP_SECONDS)


def _backoff_delay(attempt):
    # attempt is 1-based; first retry waits RETRY_BACKOFF_SECONDS, then doubles.
    return min(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_CAP_SECONDS)


class OpenSubtitlesOrgProvider:
    def __init__(self):
        self._session = None
        self._last_request_at = 0.0

    def search(self, video, languages, config):
        config = config or {}
        context = build_search_context(video, config)
        query = context.query[0] if context.query else _clean_text((video or {}).get("title") or (video or {}).get("series"))

        # A known opensubtitles hash is the strongest possible match. Query the
        # moviehash listing first so hash-matched results come back, then merge
        # with the regular imdb/title lookup (deduplicated by subtitle id).
        seen = set()
        candidates = self._hash_candidates(video or {}, languages or [], context, config, seen)

        candidates.extend(
            self._regular_candidates(video or {}, languages or [], context, config, query, seen)
        )
        return candidates

    def _hash_candidates(self, video, languages, context, config, seen):
        hash_url = self._build_hash_search_url(context)
        if not hash_url:
            return []
        hash_result = {
            "title": context.query[0] if context.query else _clean_text(video.get("title") or video.get("series")),
            "year": video.get("year"),
            "imdb_id": context.imdb_id,
            "kind": context.kind or "movie",
            "url": hash_url,
        }
        if _subtitle_language_codes(languages):
            return self._subtitles_for_result(
                hash_result, video, languages, context, config, seen, is_hash_lookup=True
            )
        response = self._http_get(hash_url, config)
        return self._candidates_from_items(
            _parse_subtitle_rows(_response_text(response), hash_url),
            hash_result,
            video,
            languages,
            context,
            config,
            seen,
            is_hash_lookup=True,
        )

    def _regular_candidates(self, video, languages, context, config, query, seen):
        search_url = self._build_search_url(query, context)
        search_response = self._http_get(search_url, config)
        search_html = _response_text(search_response)
        # Prefer the URL we landed on over the one we asked for: a title lookup
        # can be redirected onto the real listing, whose path segment is the only
        # place that listing states its language filter.
        listing_url = _landed_url(search_response, search_url)
        direct_items = _parse_subtitle_rows(
            search_html, listing_url, _language_source_url(listing_url, search_url)
        )
        if direct_items:
            language_codes = _subtitle_language_codes(languages)
            filter_url = self._language_filter_base(language_codes, listing_url, search_url)
            direct_result = {
                "title": query,
                "year": video.get("year"),
                "imdb_id": context.imdb_id,
                "kind": context.kind or "movie",
                "url": filter_url or listing_url,
            }
            if filter_url:
                # Speculative: the listing in hand already answers the search, and
                # the refetch only narrows it to the requested languages. The site
                # throttles bursts, so let a transient block on the extra requests
                # fall back to what we have rather than lose the whole search.
                try:
                    language_candidates = self._subtitles_for_result(
                        direct_result, video, languages, context, config, seen
                    )
                except OpenSubtitlesError:
                    language_candidates = []
                if language_candidates:
                    return language_candidates
            return self._candidates_from_items(
                direct_items, direct_result, video, languages, context, config, seen
            )
        results = _parse_search_results(search_html, listing_url, context.kind)
        best_result = select_best_result(results, context.imdb_id, query, video.get("year"))
        if not best_result:
            return []
        return self._subtitles_for_result(best_result, video, languages, context, config, seen)

    @staticmethod
    def _language_filter_base(language_codes, *urls):
        # Whichever of these URLs can express the language filter. The URL we
        # landed on is preferred, since it is the listing the site chose, but a
        # canonicalising redirect can drop the filter segment, and that must not
        # silently turn the refetch off: fall back to what we asked for.
        if not language_codes:
            return None
        for url in urls:
            if url and _language_filtered_url(url, language_codes[0]):
                return url
        return None

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
        archive = self._archive_download(content, content_type, payload)
        if archive is not None:
            return archive
        if b"<html" in content[:200].lower():
            page_html = _response_text(response)
            match = _DOWNLOAD_LINK_RE.search(page_html)
            if not match:
                raise ServiceUnavailable("OpenSubtitles.org download page contained no subtitle link")
            response = self._http_get(_absolute_url(match.group("href"), BASE_URL), config or {})
            content = getattr(response, "content", b"") or b""
            archive = self._archive_download(content, "", payload)
            if archive is not None:
                return archive
        if not content:
            raise ServiceUnavailable("OpenSubtitles.org downloaded empty subtitle")
        return _content_payload(content, format_=os.path.splitext(payload.get("filename") or "")[1].lstrip(".") or SUBTITLE_FORMAT)

    def _archive_download(self, content, content_type, payload):
        # Host-side extraction (Provider Hub v1.1+): hand back the raw archive bytes.
        # Zip members are cheap to list, so select the member here; for rar/7z let the
        # host pick by episode. Either way the host extracts and detects the encoding.
        if not content:
            return None
        if zipfile.is_zipfile(io.BytesIO(content)) or content.startswith(b"PK") or "zip" in content_type:
            return _archive_payload(content, _select_zip_member(content, payload.get("filename")))
        if _is_rar(content) or _is_7z(content):
            archive = {
                "archive_b64": base64.b64encode(content).decode("ascii"),
                "archive_sha256": hashlib.sha256(content).hexdigest(),
                "episode": payload.get("episode"),
            }
            return archive
        return None

    def _build_hash_search_url(self, context):
        if not context.hash:
            return None
        moviehash = urllib.parse.quote(context.hash, safe="")
        url = f"{BASE_URL}/en/search/sublanguageid-all/moviehash-{moviehash}"
        if context.size:
            url += f"/moviebytesize-{urllib.parse.quote(str(context.size), safe='')}"
        return url

    def _build_search_url(self, query, context):
        if context.use_tag_search and context.tag:
            tag = urllib.parse.quote(context.tag, safe="")
            return f"{BASE_URL}/en/search/sublanguageid-all/tag-{tag}"
        if context.imdb_id:
            imdb_number = re.sub(r"\D", "", context.imdb_id)
            return f"{BASE_URL}/en/search/sublanguageid-all/imdbid-{imdb_number}"
        params = {"MovieName": query, "action": "search"}
        if context.kind == "episode":
            params["SearchOnlyTVSeries"] = "on"
            if context.season is not None:
                params["Season"] = str(context.season)
            if context.episode is not None:
                params["Episode"] = str(context.episode)
        elif context.kind == "movie":
            params["SearchOnlyMovies"] = "on"
        return f"{BASE_URL}/en/search2?{urllib.parse.urlencode(params)}"

    def _subtitles_for_result(self, result, video, languages, context, config, seen=None, is_hash_lookup=False):
        language_codes = _subtitle_language_codes(languages)
        page_urls = []
        if language_codes:
            for language_code in language_codes:
                # A listing that cannot carry the filter collapses every language
                # onto the same URL. Fetch it once instead of once per language.
                page_url = _language_filtered_url(result["url"], language_code) or result["url"]
                if page_url not in page_urls:
                    page_urls.append(page_url)
        else:
            page_urls.append(result["url"])
        candidates = []
        if seen is None:
            seen = set()
        for page_url in page_urls:
            response = self._http_get(page_url, config)
            # Same reason as in _regular_candidates: a language filtered request
            # can be redirected onto the canonical listing, and the row language
            # is read off whichever URL still carries the filter.
            landed_url = _landed_url(response, page_url)
            candidates.extend(
                self._candidates_from_items(
                    _parse_subtitle_rows(
                        _response_text(response),
                        landed_url,
                        _language_source_url(landed_url, page_url),
                    ),
                    result,
                    video,
                    languages,
                    context,
                    config,
                    seen,
                    is_hash_lookup=is_hash_lookup,
                )
            )
        return candidates

    def _candidates_from_items(self, items, result, video, languages, context, config, seen, is_hash_lookup=False):
        candidates = []
        for item in items:
            if item["subtitle_id"] in seen:
                continue
            language = item["language"]
            # Rows kept purely so the page registers as a direct listing. No
            # verdict has been reached on such a row, so its id is deliberately
            # left unseen: a later pass over a page that does resolve it must not
            # skip it as a duplicate.
            if language is None:
                continue
            seen.add(item["subtitle_id"])
            if not _language_requested(language, languages, config):
                continue
            if _episode_mismatch(item, context):
                continue
            # A moviehash lookup returns rows that genuinely match the requested
            # hash. The native pages do not echo the MovieHash, so carry the
            # requested hash onto those rows only, mirroring upstream attaching
            # the queried hash to each result so the 'hash' match (and
            # hash_verifiable) can be awarded when it equals video.hashes.
            subtitle_hash = item.get("hash_value")
            if is_hash_lookup and not subtitle_hash:
                subtitle_hash = context.hash
            suppress_matches = _as_bool(config.get("skip_wrong_fps"), default=True) and _wrong_fps(video, item["fps"])
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
                    subtitle_hash=subtitle_hash,
                    uploader=item["uploader"],
                    download_count=item["download_count"],
                    video=video,
                    suppress_matches=suppress_matches,
                )
            )
        return candidates

    def _get_session(self):
        if self._session is None:
            self._session = _create_cloudscraper_session()
            self._session.headers.update({"User-Agent": USER_AGENT})
        return self._session

    def _http_get(self, url, config):
        config = config or {}
        self._apply_delay(config)
        session = self._get_session()
        timeout = _as_int(config.get("timeout")) or DEFAULT_TIMEOUT_SECONDS
        last_status = None
        for attempt in range(1, CHALLENGE_RETRY_ATTEMPTS + 1):
            response = self._session_get(session, url, timeout)
            # Resolve layered anti-bot gates (Cloudflare in front of Anubis): solve whichever
            # gate the current response shows and re-fetch, a few rounds, so a CF->Anubis
            # chain clears in this pass rather than waiting for the next retry attempt.
            for _ in range(CHALLENGE_GATE_ROUNDS):
                challenge_url = getattr(response, "url", "") or url
                if is_anubis_challenge(challenge_url, getattr(response, "status_code", 0)) or _extract_anubis_challenge(
                    _response_text(response)
                ):
                    solved = solve_anubis_challenge(session, challenge_url, url, timeout=timeout)
                    if not solved:
                        raise ServiceUnavailable("OpenSubtitles.org Anubis challenge could not be solved")
                    response = session.get(url, timeout=timeout, allow_redirects=True)
                    continue
                if _is_cloudflare_challenge(response):
                    self._fallback_to_flaresolverr(url, config)
                    response = session.get(url, timeout=timeout, allow_redirects=True)
                    if _is_cloudflare_challenge(response):
                        raise ServiceUnavailable("OpenSubtitles.org Cloudflare challenge remained after FlareSolverr fallback")
                    continue
                break
            status = getattr(response, "status_code", 200)
            if status == 429:
                raise RateLimited("OpenSubtitles.org rate limited the request")
            if status >= 400:
                last_status = status
                # A 401/403 after challenge handling is a transient anti-bot block: the
                # site rotates the Anubis challenge per request and throttles bursts. Drop
                # the stale clearance cookie and re-solve after a short backoff rather than
                # failing the entire search on the first block.
                if status in (401, 403) and attempt < CHALLENGE_RETRY_ATTEMPTS:
                    self._reset_anubis_cookies(session)
                    time.sleep(_backoff_delay(attempt))
                    continue
                raise ServiceUnavailable(f"OpenSubtitles.org HTTP {status}")
            return response
        raise ServiceUnavailable(f"OpenSubtitles.org HTTP {last_status}")

    def _session_get(self, session, url, timeout):
        # Wrap only the raw transport GET in a bounded retry so a single transient
        # network blip (connection reset, DNS hiccup, timeout, an isolated 5xx/429)
        # does not abort the search/download. Challenge responses (Anubis /
        # Cloudflare) and any 4xx other than 429 are returned untouched so the
        # existing fallback and status-mapping logic decides what to do. The final
        # failure raises the same error the provider raised before this retry.
        last_exc = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                response = session.get(url, timeout=timeout, allow_redirects=True)
            except Exception as exc:  # noqa: BLE001 - mirror prior catch-all mapping
                if not _is_retryable_transport_error(exc):
                    raise ServiceUnavailable(f"OpenSubtitles.org request failed: {exc}") from exc
                last_exc = exc
                if attempt >= RETRY_MAX_ATTEMPTS:
                    raise ServiceUnavailable(f"OpenSubtitles.org request failed: {exc}") from exc
                time.sleep(_backoff_delay(attempt))
                continue
            if attempt < RETRY_MAX_ATTEMPTS and self._should_retry_response(response):
                delay = _retry_after_seconds(response)
                time.sleep(delay if delay is not None else _backoff_delay(attempt))
                continue
            return response
        # Unreachable: the loop always returns or raises, but keep a definite fallback.
        if last_exc is not None:
            raise ServiceUnavailable(f"OpenSubtitles.org request failed: {last_exc}")
        raise ServiceUnavailable("OpenSubtitles.org request failed")

    @staticmethod
    def _should_retry_response(response):
        # Retry only a genuine transient HTTP status. A Cloudflare or Anubis
        # challenge can ride on a 503/403, so never retry those here: they belong
        # to the challenge fallback path, not the transport blip path.
        status = getattr(response, "status_code", 200) or 200
        if status != 429 and status < 500:
            return False
        if _is_cloudflare_challenge(response):
            return False
        challenge_url = getattr(response, "url", "") or ""
        if is_anubis_challenge(challenge_url, status):
            return False
        if _extract_anubis_challenge(_response_text(response)):
            return False
        return True

    @staticmethod
    def _reset_anubis_cookies(session):
        # Drop the Anubis clearance cookie so the next request re-solves a fresh
        # challenge instead of replaying a stale/rejected token after a 401/403 block.
        try:
            for cookie in list(session.cookies):
                name = (cookie.name or "").lower()
                if "anubis" in name or "within" in name:
                    session.cookies.clear(cookie.domain, cookie.path, cookie.name)
        except Exception:  # noqa: BLE001 - cookie jar internals vary; best-effort reset
            pass

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
