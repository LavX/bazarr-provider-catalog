"""Wizdom provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

PROVIDER_ID = "wizdom"
BASE_URL = "https://wizdom.xyz"
TMDB_BASE_URL = "https://api.tmdb.org/3"
TMDB_API_KEY = "a51ee051bcd762543373903de296e0a3"
HTTP_TIMEOUT_SECONDS = 15
DOWNLOAD_TIMEOUT_SECONDS = 10
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 45000
MAX_FLARESOLVERR_TIMEOUT_MS = 45000
SUPPORTED_EXTENSIONS = (".srt", ".sub")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

HEBREW_LANGUAGE = {
    "alpha3": "heb",
    "alpha2": "he",
    "hi": False,
    "forced": False,
}

MATCH_ORDER = (
    "series",
    "title",
    "year",
    "season",
    "episode",
    "series_imdb_id",
    "imdb_id",
    "resolution",
    "source",
    "video_codec",
    "audio_codec",
    "release_group",
)

_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SRT_TIMECODE_RE = re.compile(
    rb"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)
_MICRODVD_RE = re.compile(rb"\{\d+\}\{\d+\}")
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)


class ServiceUnavailable(RuntimeError):
    """The upstream service is not currently usable."""


class _MissingCloudscraper:
    @staticmethod
    def create_scraper(**kwargs):
        raise ServiceUnavailable("Wizdom ai-cloudscraper dependency is not installed")


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


def parse_releases(data, media_type, imdb_id, title, season=None, episode=None):
    rows = []
    for item in _release_items(data, media_type, season, episode):
        if not isinstance(item, dict):
            continue
        subtitle_id = item.get("id")
        release = item.get("version")
        if subtitle_id in (None, "") or not release:
            continue
        rows.append(
            {
                "subtitle_id": str(subtitle_id),
                "release": str(release),
                "imdb_id": _normalize_imdb_id(imdb_id),
                "title": _coerce_text(title) or "",
                "season": _safe_int(season),
                "episode": _safe_int(episode),
                "media_type": media_type,
                "page_link": _page_link(media_type, _normalize_imdb_id(imdb_id)),
            }
        )
    return rows


def extract_download(body, payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", "srt", empty=True)
    stream = io.BytesIO(body)
    if not zipfile.is_zipfile(stream):
        subtitle_format = _subtitle_extension(payload.get("filename", ""))
        if not subtitle_format or not _looks_like_subtitle_text(body, subtitle_format):
            raise ValueError("wizdom download did not return a zip subtitle payload")
        return _content_payload(_normalize_line_endings(body), subtitle_format)
    with zipfile.ZipFile(stream) as archive:
        name, content = _select_archive_subtitle(archive)
    subtitle_format = _subtitle_extension(name) or "srt"
    return _content_payload(_normalize_line_endings(content), subtitle_format)


class WizdomProvider:
    def __init__(self):
        self._session = None
        self._request_config = {}

    def _get_session(self):
        if self._session is None:
            self._session = _create_cloudscraper_session()
            self._session.headers.update({"User-Agent": USER_AGENT})
        return self._session

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None, config=None):
        if isinstance(timeout, dict) and config is None:
            config = timeout
            timeout = HTTP_TIMEOUT_SECONDS
        config = dict(config or self._request_config or {})
        session = self._get_session()
        headers = _headers(referer, session.headers.get("User-Agent") or USER_AGENT)
        try:
            response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        except Exception as exc:
            raise ServiceUnavailable(f"Wizdom request failed: {exc}") from exc
        body = _response_body(response)
        if is_anubis_challenge(getattr(response, "url", ""), getattr(response, "status_code", 0)) or _has_anubis_challenge_body(body):
            solved = solve_anubis_challenge(session, response.url, url, timeout=timeout)
            if not solved:
                raise ServiceUnavailable("Wizdom Anubis challenge could not be solved")
            response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if _is_cloudflare_challenge(response):
            self._fallback_to_flaresolverr(url, config)
            headers = _headers(referer, session.headers.get("User-Agent") or USER_AGENT)
            response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if _is_cloudflare_challenge(response):
                raise ServiceUnavailable("Wizdom Cloudflare challenge remained after FlareSolverr fallback")
        status = int(getattr(response, "status_code", 200) or 200)
        body = _response_body(response)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, f"HTTP {status}", getattr(response, "headers", {}), io.BytesIO(body))
        return body

    def search(self, video, languages, config):
        self._request_config = dict(config or {})
        language = _requested_hebrew_language(languages)
        if language is None:
            return []
        try:
            video = video or {}
            media_type = video.get("kind")
            if media_type not in {"movie", "episode"}:
                return []
            results = []
            seen = set()
            for title in _candidate_titles(video, media_type):
                imdb_id = _video_imdb_id(video, media_type)
                if not imdb_id:
                    imdb_id = self._resolve_imdb_id(title, video.get("year"), media_type == "movie")
                if not imdb_id:
                    continue
                releases = self._fetch_releases(imdb_id)
                rows = parse_releases(
                    releases,
                    media_type=media_type,
                    imdb_id=imdb_id,
                    title=title,
                    season=video.get("season"),
                    episode=video.get("episode"),
                )
                for row in rows:
                    if row["subtitle_id"] in seen:
                        continue
                    seen.add(row["subtitle_id"])
                    results.append(_result(video, row, language))
                if results:
                    return results
            return results
        finally:
            self._request_config = {}

    def download(self, provider_payload, language, config):
        del language
        self._request_config = dict(config or {})
        payload = provider_payload or {}
        subtitle_id = payload.get("subtitle_id")
        if not subtitle_id:
            raise ValueError("wizdom download requires subtitle_id")
        try:
            body = self._http_get(
                f"{BASE_URL}/api/files/sub/{urllib.parse.quote(str(subtitle_id), safe='')}",
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                referer=payload.get("page_link"),
            )
            return extract_download(body, payload)
        finally:
            self._request_config = {}

    def _fallback_to_flaresolverr(self, url, config):
        endpoint = _clean_text((config or {}).get("flaresolverr_url"))
        if not endpoint:
            raise ServiceUnavailable("Wizdom Cloudflare challenge requires optional FlareSolverr URL")
        max_timeout = _flaresolverr_timeout_ms(config)
        payload = {"cmd": "request.get", "url": url, "maxTimeout": max_timeout}
        data = self._post_flaresolverr(endpoint, payload, timeout=max(max_timeout / 1000 + 10, 20))
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
                domain=cookie.get("domain") or ".wizdom.xyz",
                path=cookie.get("path") or "/",
            )

    def _fetch_releases(self, imdb_id):
        url = f"{BASE_URL}/api/releases/{urllib.parse.quote(_normalize_imdb_id(imdb_id), safe='')}"
        try:
            return _parse_json_bytes(self._http_get(url))
        except urllib.error.HTTPError as error:
            if error.code == 500:
                error.close()
                return {}
            raise
        except ValueError:
            return {}

    def _resolve_imdb_id(self, title, year, is_movie):
        title = (_coerce_text(title) or "").replace("'", "")
        if not title:
            return None
        category = "movie" if is_movie else "tv"
        params = [
            ("api_key", TMDB_API_KEY),
            ("query", title),
            ("language", "en"),
        ]
        if year:
            params.append(("year", str(year)))
        search_url = f"{TMDB_BASE_URL}/search/{category}?{urllib.parse.urlencode(params)}"
        try:
            search_data = _parse_json_bytes(self._http_get(search_url))
            results = search_data.get("results") if isinstance(search_data, dict) else []
            if not results:
                return None
            tmdb_id = results[0].get("id") if isinstance(results[0], dict) else None
            if not tmdb_id:
                return None
            if is_movie:
                detail_path = f"movie/{tmdb_id}"
            else:
                detail_path = f"tv/{tmdb_id}/external_ids"
            detail_params = urllib.parse.urlencode(
                [("api_key", TMDB_API_KEY), ("language", "en")]
            )
            detail_url = f"{TMDB_BASE_URL}/{detail_path}?{detail_params}"
            detail_data = _parse_json_bytes(self._http_get(detail_url))
            if not isinstance(detail_data, dict):
                return None
            return _normalize_imdb_id(detail_data.get("imdb_id"))
        except (ValueError, urllib.error.URLError, ServiceUnavailable):
            return None


def _headers(referer=None, user_agent=None):
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def _as_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value if item not in (None, ""))
    return " ".join(str(value).split())


def _response_body(response):
    body = getattr(response, "content", b"")
    if isinstance(body, str):
        return body.encode("utf-8")
    if body:
        return body
    text = getattr(response, "text", "")
    if isinstance(text, str) and text:
        return text.encode("utf-8")
    if hasattr(response, "read"):
        return response.read()
    return b""


def _has_anubis_challenge_body(body):
    if isinstance(body, bytes):
        text = body.decode("utf-8", "ignore")
    else:
        text = str(body or "")
    return _extract_anubis_challenge(text) is not None


def _flaresolverr_timeout_ms(config):
    configured = _as_int((config or {}).get("flaresolverr_timeout_ms")) or DEFAULT_FLARESOLVERR_TIMEOUT_MS
    return max(1000, min(configured, MAX_FLARESOLVERR_TIMEOUT_MS))


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
            raise ServiceUnavailable("Wizdom Anubis proof-of-work timed out")
        digest = hashlib.sha256(f"{random_data}{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith(prefix):
            return nonce, digest
        nonce += 1


def _solve_preact(random_data, difficulty):
    return hashlib.sha256(random_data.encode("utf-8")).hexdigest(), difficulty * 0.125


def solve_anubis_challenge(session, challenge_url, original_url, timeout=HTTP_TIMEOUT_SECONDS):
    del original_url
    parsed = urllib.parse.urlparse(challenge_url)
    query = urllib.parse.parse_qs(parsed.query)
    redir = query.get("redir", [parsed.path])[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    challenge_page_url = challenge_url if challenge_url.startswith("http") else base + challenge_url
    started = time.monotonic()
    deadline = started + max(float(timeout or HTTP_TIMEOUT_SECONDS), 0.1)

    response = session.get(challenge_page_url, timeout=(10, timeout), allow_redirects=True)
    challenge = _extract_anubis_challenge(getattr(response, "text", ""))
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
            raise ServiceUnavailable("Wizdom Anubis preact challenge timed out")
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
    headers = {str(key).lower(): str(value).lower() for key, value in (getattr(response, "headers", {}) or {}).items()}
    text = (getattr(response, "text", "") or "").lower()
    if not text:
        text = _response_body(response).decode("utf-8", "ignore").lower()
    status = int(getattr(response, "status_code", 0) or 0)
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
            "challenges.cloudflare.com",
        )
    )


def _release_items(data, media_type, season, episode):
    if not isinstance(data, dict):
        return []
    subs = data.get("subs") or []
    if media_type == "movie":
        return subs if isinstance(subs, list) else []
    season_number = _safe_int(season)
    episode_number = _safe_int(episode)
    if season_number is None or episode_number is None:
        return []
    episode_key = str(episode_number)
    season_nodes = []
    if isinstance(subs, dict):
        season_nodes.append(subs.get(str(season_number), {}))
    elif isinstance(subs, list):
        if 0 <= season_number < len(subs):
            season_nodes.append(subs[season_number])
    rows = []
    seen = set()
    for node in season_nodes:
        if not isinstance(node, dict):
            continue
        for item in node.get(episode_key, []) or []:
            key = item.get("id") if isinstance(item, dict) else id(item)
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    return rows


def _result(video, row, language):
    matches = _derive_matches(video, row)
    score = _score(matches, row["media_type"])
    filename = f"wizdom.{_slug(row['release'])}.{language['alpha2']}.zip"
    return {
        "provider": PROVIDER_ID,
        "id": f"wizdom-{row['subtitle_id']}",
        "language": dict(language),
        "release_info": row["release"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": row["page_link"],
        "display": {
            "source": "wizdom.xyz",
            "title": row["title"],
            "release": row["release"],
            "subtitle_id": row["subtitle_id"],
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "page_link": row["page_link"],
            "release": row["release"],
            "filename": filename,
            "imdb_id": row["imdb_id"],
            "media_type": row["media_type"],
        },
    }


def _derive_matches(video, row):
    video = video or {}
    release = row.get("release") or ""
    matches = set()
    if row.get("media_type") == "episode":
        if _title_matches(row.get("title"), [video.get("series")] + list(video.get("alternative_series") or [])):
            matches.add("series")
        if row.get("season") and _safe_int(video.get("season")) == row.get("season"):
            matches.add("season")
        if row.get("episode") and _safe_int(video.get("episode")) == row.get("episode"):
            matches.add("episode")
        if _normalize_imdb_id(video.get("series_imdb_id")) == row.get("imdb_id"):
            matches.add("series_imdb_id")
    else:
        if _title_matches(row.get("title"), [video.get("title")] + list(video.get("alternative_titles") or [])):
            matches.add("title")
        if _normalize_imdb_id(video.get("imdb_id")) == row.get("imdb_id"):
            matches.add("imdb_id")
        year = video.get("year")
        if year and str(year) in release:
            matches.add("year")
    for field, match_name in (
        ("resolution", "resolution"),
        ("source", "source"),
        ("video_codec", "video_codec"),
        ("audio_codec", "audio_codec"),
        ("release_group", "release_group"),
    ):
        value = video.get(field)
        if value and _normalize(value) in _normalize(release):
            matches.add(match_name)
    return [name for name in MATCH_ORDER if name in matches]


def _score(matches, media_type):
    match_set = set(matches)
    if media_type == "episode":
        base = 40
        for name in ("series", "season", "episode", "series_imdb_id"):
            if name in match_set:
                base += 12
    else:
        base = 50
        if "title" in match_set:
            base += 20
        if "year" in match_set:
            base += 10
        if "imdb_id" in match_set:
            base += 10
    for name in ("resolution", "source", "video_codec", "audio_codec", "release_group"):
        if name in match_set:
            base += 2
    return min(base, 100)


def _requested_hebrew_language(languages):
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        alpha3 = (language.get("alpha3") or "").lower()
        alpha2 = (language.get("alpha2") or "").lower()
        if language.get("hi") or language.get("forced"):
            continue
        if alpha3 == "heb" or alpha2 == "he":
            return dict(HEBREW_LANGUAGE)
    return None


def _candidate_titles(video, media_type):
    primary = video.get("title") if media_type == "movie" else video.get("series")
    alternatives = video.get("alternative_titles") if media_type == "movie" else video.get("alternative_series")
    titles = []
    for title in [primary] + list(alternatives or []):
        title = _coerce_text(title)
        if title and title not in titles:
            titles.append(title)
    return titles


def _video_imdb_id(video, media_type):
    if media_type == "episode":
        return _normalize_imdb_id(video.get("series_imdb_id"))
    return _normalize_imdb_id(video.get("imdb_id"))


def _page_link(media_type, imdb_id):
    section = "movies" if media_type == "movie" else "series"
    return f"{BASE_URL}/{section}/{imdb_id}"


def _normalize_imdb_id(value):
    if value in (None, ""):
        return None
    value = str(value).strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith("tt"):
        digits = re.sub(r"\D", "", lowered[2:])
        return f"tt{digits.zfill(7)}" if digits else None
    if value.isdigit():
        return f"tt{value.zfill(7)}"
    return value


def _select_archive_subtitle(archive):
    names = [name for name in archive.namelist() if _subtitle_extension(name)]
    if not names:
        raise ValueError("wizdom archive contains no supported subtitle files")
    for name in names:
        content = archive.read(name)
        if _looks_like_subtitle_text(content, _subtitle_extension(name)):
            return name, content
    raise ValueError("wizdom archive contains no valid subtitle text")


def _looks_like_subtitle_text(content, subtitle_format):
    sample = _normalize_line_endings(content or b"")[:4096].lstrip()
    if not sample:
        return False
    if subtitle_format == "sub":
        return bool(_MICRODVD_RE.search(sample))
    return (
        sample.startswith(b"WEBVTT")
        or b"[Script Info]" in sample
        or bool(_SRT_TIMECODE_RE.search(sample))
    )


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUPPORTED_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


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
    encoding = _detect_encoding(content)
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "encoding": encoding,
        "empty": False,
    }


def _detect_encoding(content):
    try:
        (content or b"").decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        decoded = (content or b"").decode("cp1255")
        if re.search(r"[\u0590-\u05ff]", decoded):
            return "windows-1255"
    except UnicodeDecodeError:
        pass
    return "latin-1"


def _content_type(subtitle_format):
    if subtitle_format == "sub":
        return "text/plain"
    return "application/x-subrip"


def _parse_json_bytes(body):
    return json.loads((body or b"").decode("utf-8"))


def _title_matches(candidate, titles):
    candidate_norm = _normalize(candidate)
    if not candidate_norm:
        return False
    for title in titles:
        title_norm = _normalize(title)
        if title_norm and title_norm == candidate_norm:
            return True
    return False


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _slug(value):
    normalized = _normalize(value)
    return "-".join(part for part in normalized.split() if part) or "release"


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")
