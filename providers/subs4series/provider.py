"""Subs4Series provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

PROVIDER_ID = "subs4series"
BASE_URL = "https://www.subs4series.com"
SEARCH_URL = f"{BASE_URL}/search_report.php"
ANTI_BLOCK_URLS = (
    f"{BASE_URL}/includes/anti-block-layover.php?launch=1",
    f"{BASE_URL}/includes/anti-block.php",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 15
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 25000
MIN_FLARESOLVERR_TIMEOUT_MS = 5000
MAX_FLARESOLVERR_TIMEOUT_MS = 25000
CLOUDFLARE_STATUS_CODES = {403, 429, 503}
CLOUDFLARE_BODY_MARKERS = (
    "attention required! | cloudflare",
    "just a moment",
    "cf-challenge",
    "cf-error-details",
)
SUPPORTED_LANGUAGES = {
    "ell": "el",
    "eng": "en",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

_OPTION_RE = re.compile(r"<option\b(?P<attrs>[^>]*)>(?P<title>.*?)</option>", re.I | re.S)
_ROW_RE = re.compile(
    r"<div\b(?=[^>]*\bclass=[\"'][^\"']*\bsee(?:Dark|Medium)\b)[^>]*>"
    r"(?P<body>.*?)(?=<div\b(?=[^>]*\bclass=[\"'][^\"']*\bsee(?:Dark|Medium)\b)|</body>|</html>|\Z)",
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_B_RE = re.compile(r"<b\b[^>]*>(?P<text>.*?)</b>", re.I | re.S)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>", re.I | re.S)
_FORM_RE = re.compile(r"<form\b[^>]*>", re.I | re.S)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_SXXEXX_RE = re.compile(r"\bs0*(?P<season>\d{1,2})e0*(?P<episode>\d{1,3})\b", re.I)
_X_EPISODE_RE = re.compile(r"\b0*(?P<season>\d{1,2})x0*(?P<episode>\d{1,3})\b", re.I)
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)


class CloudflareBlockedError(RuntimeError):
    """Raised when subs4series.com presents an unresolved Cloudflare block."""


def parse_suggestions(body):
    rows = []
    for match in _OPTION_RE.finditer(_decode(body)):
        tag = f"<option {match.group('attrs')}>"
        value = _attr(tag, "value")
        title = _strip_tags(match.group("title"))
        if not value or not title:
            continue
        url = _absolute_url(value)
        show_path = _show_path_from_url(url)
        if not show_path:
            continue
        rows.append({"title": title, "url": url, "show_path": show_path})
    return rows


def parse_episode_page(body):
    text = _decode(body)
    series_title = _series_title_from_page(text)
    page_year = _series_year_from_page(text)
    rows = []
    seen = set()
    for match in _ROW_RE.finditer(text):
        row = match.group("body")
        language, alpha2 = _language_from_row(row)
        if not language:
            continue
        release_info = _release_from_row(row)
        detail_url = _detail_url_from_row(row)
        if not release_info or not detail_url:
            continue
        key = (detail_url, language)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "language": language,
                "alpha2": alpha2,
                "series_title": series_title or _series_from_release(release_info),
                "year": _year_from_row(row) or page_year,
                "release_info": release_info,
                "detail_url": detail_url,
                "uploader": _uploader_from_row(row),
                "downloads": _downloads_from_row(row),
            }
        )
    return rows


def parse_download_target(body):
    text = _decode(body)
    target = _direct_download_target(text) or _form_download_target(text)
    if not target:
        raise ValueError("subs4series download target was not found")
    site_key = _captcha_site_key(text)
    return {
        "url": _absolute_url(target),
        "captcha_required": bool(site_key or "g-recaptcha" in text or "grecaptcha" in text),
        "site_key": site_key,
    }


def extract_download(body, payload=None):
    payload = payload or {}
    # Reject broken responses up front: the download endpoint can answer with an empty
    # stream or an HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError("subs4series empty download body")
    if _is_html_body(body):
        raise ValueError("subs4series returned an HTML/error page instead of a subtitle")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host, which lists it, picks the member by episode, and detects encoding.
        return {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
            "episode": payload.get("episode"),
        }
    # Direct, non-archive subtitle body.
    return _content_payload(body, _format_from_filename(payload.get("filename")))


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def derive_matches(video, item):
    video = video or {}
    release = item.get("release_info") if isinstance(item, dict) else item
    series_title = item.get("series_title", "") if isinstance(item, dict) else ""
    text = f"{series_title} {release}"
    normalized = _normalize(text)
    matches = []
    series_tokens = _tokens(video.get("series"))
    if series_tokens and all(token in normalized.split() for token in series_tokens):
        matches.append("series")
    season, episode = _episode_markers(normalized)
    try:
        video_season = int(video.get("season"))
        video_episode = int(video.get("episode"))
    except (TypeError, ValueError):
        video_season = video_episode = None
    if video_season is not None and season == video_season:
        matches.append("season")
    if video_episode is not None and episode == video_episode:
        matches.append("episode")
    title_tokens = _tokens(video.get("title"))
    if title_tokens and all(token in normalized.split() for token in title_tokens):
        matches.append("title")
    if video.get("year") and str(video.get("year")) in normalized.split():
        matches.append("year")
    if _matches_token(video.get("source"), normalized):
        matches.append("source")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _normalize_release_group(release_group) in _normalize_release_group(release):
        matches.append("release_group")
    return matches


def compute_score(video, item):
    matches = set(derive_matches(video, item))
    if {"series", "season", "episode"}.issubset(matches):
        return 100
    if {"series", "episode"}.issubset(matches):
        return 95
    if "series" in matches:
        return 75
    return 40


class Subs4SeriesProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))
        self._scraper = None
        self._scraper_initialized = False
        self._flaresolverr_cookies = {}
        self._flaresolverr_user_agent = ""

    def search(self, video, languages, config):
        video = video or {}
        if video.get("kind") != "episode":
            return []
        requested = {_alpha3_for_language(language) for language in languages or []}
        requested = {language for language in requested if language in SUPPORTED_LANGUAGES}
        if not requested:
            return []
        show_paths = self._find_show_paths(video, config or {})
        results = []
        seen = set()
        for show_path in show_paths:
            episode_url = _episode_url(show_path, video)
            if not episode_url:
                continue
            _sleep(config)
            for item in parse_episode_page(self._http_get(episode_url, referer=BASE_URL, config=config)):
                if item["language"] not in requested:
                    continue
                key = (item["detail_url"], item["language"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, item, episode_url))
        return sorted(results, key=lambda result: result["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        provider_payload = dict(provider_payload or {})
        config = dict(config or {})
        detail_url = provider_payload.get("detail_url")
        if not detail_url:
            raise ValueError("subs4series detail_url missing from provider payload")
        _sleep(config)
        page_body = self._http_get(detail_url, referer=provider_payload.get("page_link"), config=config)
        target = parse_download_target(page_body)
        data = {"my_recaptcha_challenge_field": "manual_challenge"}
        if target["captcha_required"]:
            captcha_response = self._captcha_response(target["site_key"], detail_url, config)
            if not captcha_response:
                raise ValueError("subs4series captcha response required")
            data["g-recaptcha-response"] = captcha_response
            data["recaptcha_response"] = captcha_response
        self._apply_anti_block(detail_url, config)
        _sleep(config)
        try:
            download_body = self._http_post(target["url"], data, referer=detail_url)
        except urllib.error.HTTPError as error:
            if error.code == 403:
                raise RuntimeError("subs4series captcha expired waiting to be solved") from error
            raise
        return extract_download(download_body, provider_payload)

    def _find_show_paths(self, video, config):
        paths = []
        seen = set()
        for title in _video_titles(video):
            query = urllib.parse.urlencode({"search": title, "searchType": "1"})
            url = f"{SEARCH_URL}?{query}"
            _sleep(config)
            for suggestion in parse_suggestions(self._http_get(url, referer=BASE_URL, config=config)):
                if not _title_matches(suggestion["title"], title, video.get("year")):
                    continue
                if suggestion["show_path"] in seen:
                    continue
                seen.add(suggestion["show_path"])
                paths.append(suggestion["show_path"])
            if paths:
                break
        return paths

    def _result(self, video, item, episode_url):
        matches = derive_matches(video, item)
        score = compute_score(video, item)
        alpha2 = item["alpha2"]
        filename = (
            f"subs4series.{_slug(item.get('series_title'))}."
            f"s{int(video.get('season')):02d}e{int(video.get('episode')):02d}."
            f"{_slug(item.get('release_info'))}.{alpha2}.zip"
        )
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "detail_url": item["detail_url"],
            "page_link": episode_url,
            "release_info": item["release_info"],
            "filename": filename,
            "series_title": item.get("series_title"),
            "season": video.get("season"),
            "episode": video.get("episode"),
            "year": item.get("year"),
            "uploader": item.get("uploader"),
        }
        return {
            "id": hashlib.sha1(f"{item['detail_url']}|{item['language']}".encode("utf-8")).hexdigest(),
            "provider": PROVIDER_ID,
            "language": {"alpha3": item["language"], "alpha2": alpha2},
            "release_info": item["release_info"],
            "title": item["release_info"],
            "score": score,
            "matches": matches,
            "hearing_impaired": False,
            "page_link": item["detail_url"],
            "download_count": item.get("downloads"),
            "provider_payload": payload,
            "display": item["release_info"],
        }

    def _apply_anti_block(self, referer, config):
        for url in ANTI_BLOCK_URLS:
            _sleep(config)
            self._http_get(url, referer=referer, config=config)

    def _captcha_response(self, site_key, page_url, config):
        if config.get("captcha_response"):
            return str(config["captcha_response"])
        solver_url = _coerce_text(config.get("captcha_solver_url"))
        if not solver_url:
            return None
        payload = {
            "provider": PROVIDER_ID,
            "site_key": site_key,
            "site_url": page_url,
            "url": page_url,
            "invisible": True,
        }
        timeout = max(1, int(config.get("captcha_solver_timeout_ms") or 30000) / 1000)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = _coerce_text(config.get("captcha_solver_token"))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = self._raw_request(solver_url, method="POST", data=json.dumps(payload).encode("utf-8"), headers=headers, timeout=timeout)
        try:
            response = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("subs4series captcha solver returned invalid JSON") from error
        for key in ("response", "token", "captcha_response", "gRecaptchaResponse", "g-recaptcha-response"):
            if response.get(key):
                return str(response[key])
        return None

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None, config=None):
        headers = _browser_headers(referer, self._flaresolverr_user_agent, self._flaresolverr_cookies)
        scraper = self._get_scraper()
        if scraper is not None:
            try:
                response = scraper.get(url, headers=headers, timeout=timeout)
            except Exception as error:
                if _flaresolverr_url(config) and _is_cloudflare_exception(error):
                    return self._flaresolverr_get(url, timeout, config)
                raise
            body = getattr(response, "content", None)
            if body is None:
                body = str(getattr(response, "text", "")).encode("utf-8")
            if is_anubis_challenge(getattr(response, "url", ""), getattr(response, "status_code", 0)) or _has_anubis_challenge_body(body):
                solved = solve_anubis_challenge(scraper, response.url, url, timeout=timeout)
                if not solved:
                    raise CloudflareBlockedError("subs4series Anubis challenge could not be solved")
                response = scraper.get(url, headers=headers, timeout=timeout)
                body = getattr(response, "content", None)
                if body is None:
                    body = str(getattr(response, "text", "")).encode("utf-8")
            if _is_cloudflare_challenge(getattr(response, "status_code", 0), getattr(response, "headers", {}) or {}, body):
                if _flaresolverr_url(config):
                    return self._flaresolverr_get(url, timeout, config)
                raise CloudflareBlockedError(
                    "subs4series hit a Cloudflare block and no FlareSolverr URL is configured"
                )
            response.raise_for_status()
            return body
        try:
            return self._raw_request(url, method="GET", headers=headers, timeout=timeout)
        except urllib.error.HTTPError as error:
            body = error.read()
            if _is_cloudflare_challenge(error.code, error.headers, body) and _flaresolverr_url(config):
                return self._flaresolverr_get(url, timeout, config)
            raise

    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = _browser_headers(referer, self._flaresolverr_user_agent, self._flaresolverr_cookies)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        encoded = urllib.parse.urlencode(data or {}).encode("utf-8")
        scraper = self._get_scraper()
        if scraper is not None:
            response = scraper.post(url, data=data, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response.content
        return self._raw_request(url, method="POST", data=encoded, headers=headers, timeout=timeout)

    def _raw_request(self, url, method="GET", data=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def _flaresolverr_get(self, url, timeout, config):
        endpoint = _flaresolverr_url(config)
        if not endpoint:
            raise CloudflareBlockedError(
                "subs4series hit a Cloudflare block and no FlareSolverr URL is configured"
            )
        timeout_ms = _flaresolverr_timeout_ms(config, timeout)
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": timeout_ms,
        }
        if self._flaresolverr_cookies:
            payload["cookies"] = [
                {"name": name, "value": value}
                for name, value in self._flaresolverr_cookies.items()
            ]
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_ms / 1000 + 2) as response:
                response_body = response.read()
        except Exception as error:
            raise CloudflareBlockedError(f"subs4series FlareSolverr request failed: {error}") from error

        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CloudflareBlockedError("subs4series FlareSolverr returned invalid JSON") from error
        if payload.get("status") not in (None, "ok"):
            message = payload.get("message") or "FlareSolverr did not solve the challenge"
            raise CloudflareBlockedError(f"subs4series {message}")

        solution = payload.get("solution") or {}
        response_text = solution.get("response")
        if response_text is None:
            raise CloudflareBlockedError("subs4series FlareSolverr response had no page body")
        body = response_text if isinstance(response_text, bytes) else str(response_text).encode("utf-8")
        if _is_cloudflare_challenge(solution.get("status") or 200, solution.get("headers") or {}, body):
            raise CloudflareBlockedError("subs4series FlareSolverr response is still a Cloudflare block")
        self._store_flaresolverr_solution(solution)
        return body

    def _store_flaresolverr_solution(self, solution):
        user_agent = solution.get("userAgent")
        if user_agent:
            self._flaresolverr_user_agent = user_agent
        for cookie in solution.get("cookies") or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            self._flaresolverr_cookies[name] = value
            if self._scraper is not None:
                try:
                    self._scraper.cookies.set(name, value, domain=".subs4series.com")
                except Exception:
                    pass

    def _get_scraper(self):
        if not self._scraper_initialized:
            self._scraper = _create_cloudscraper()
            self._scraper_initialized = True
        return self._scraper


def _create_cloudscraper():
    if cloudscraper is None:
        return None
    options = {
        "browser": {"custom": USER_AGENT},
        "interpreter": "native",
        "enable_cookie_persistence": False,
        "debug": False,
    }
    try:
        return cloudscraper.create_scraper(**options)
    except TypeError as error:
        if "enable_cookie_persistence" not in str(error):
            raise
        fallback_options = dict(options)
        fallback_options.pop("enable_cookie_persistence", None)
        return cloudscraper.create_scraper(**fallback_options)


def _normalize_headers(headers):
    return {
        str(key).lower(): str(value)
        for key, value in (headers or {}).items()
    }


def is_anubis_challenge(url, status_code=0):
    return "/.within.website/" in (url or "") or (
        status_code in (307, 401, 403) and ".within.website" in (url or "")
    )


def _has_anubis_challenge_body(body):
    if isinstance(body, bytes):
        text = body.decode("utf-8", "ignore")
    else:
        text = str(body or "")
    return _extract_anubis_challenge(text) is not None


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
    rules = data.get("rules") or {}
    if not isinstance(rules, dict):
        rules = {}
    challenge = data.get("challenge") or {}
    if isinstance(challenge, str):
        challenge = {
            "id": data.get("id") or challenge,
            "randomData": challenge,
        }
    if not isinstance(challenge, dict):
        return None
    if "randomData" not in challenge or "id" not in challenge:
        return None
    return {
        "id": challenge["id"],
        "randomData": challenge["randomData"],
        "difficulty": int(challenge.get("difficulty", rules.get("difficulty", 4))),
        "method": challenge.get(
            "method",
            challenge.get("algorithm", rules.get("method", rules.get("algorithm", "fast"))),
        ),
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


def solve_anubis_challenge(session, challenge_url, original_url, timeout=HTTP_TIMEOUT_SECONDS):
    del original_url
    parsed = urllib.parse.urlparse(challenge_url)
    query = urllib.parse.parse_qs(parsed.query)
    redir = query.get("redir", [parsed.path])[0]
    base = f"{parsed.scheme}://{parsed.netloc}"
    challenge_page_url = challenge_url if challenge_url.startswith("http") else base + challenge_url
    started = time.monotonic()

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
        if getattr(solved, "cookies", None):
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
        if getattr(solved, "cookies", None):
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
        if getattr(solved, "cookies", None):
            session.cookies.update(solved.cookies)

    cookies = {}
    for cookie in getattr(session, "cookies", []) or []:
        if "anubis" in cookie.name.lower() or cookie.name == "PHPSESSID":
            cookies[cookie.name] = cookie.value
    return cookies or None


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


def _is_cloudflare_exception(error):
    text = f"{error.__class__.__name__} {error}".lower()
    return "cloudflare" in text or "challenge" in text


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config, request_timeout=None):
    del request_timeout
    try:
        timeout = int((config or {}).get("flaresolverr_timeout_ms") or DEFAULT_FLARESOLVERR_TIMEOUT_MS)
    except (TypeError, ValueError):
        timeout = DEFAULT_FLARESOLVERR_TIMEOUT_MS
    return max(MIN_FLARESOLVERR_TIMEOUT_MS, min(timeout, MAX_FLARESOLVERR_TIMEOUT_MS))


def _cookie_header(cookies):
    if not cookies:
        return ""
    return "; ".join(
        f"{name}={value}"
        for name, value in cookies.items()
        if name and value is not None
    )


def _browser_headers(referer=None, user_agent=None, cookies=None):
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "el,en-US;q=0.9,en;q=0.8",
    }
    cookie = _cookie_header(cookies)
    if cookie:
        headers["Cookie"] = cookie
    if referer:
        headers["Referer"] = referer
    return headers


def _video_titles(video):
    titles = []
    for key in ("series", "original_series"):
        if _coerce_text(video.get(key)):
            titles.append(str(video[key]))
    for key in ("alternative_series", "alternative_titles"):
        value = video.get(key)
        if isinstance(value, str):
            titles.append(value)
        elif isinstance(value, (list, tuple)):
            titles.extend(str(item) for item in value if _coerce_text(item))
    seen = set()
    unique = []
    for title in titles:
        normalized = _normalize_title(title)
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(title)
    return unique


def _episode_url(show_path, video):
    try:
        season = int(video.get("season"))
        episode = int(video.get("episode"))
    except (TypeError, ValueError):
        return None
    path = (show_path or "").strip("/")
    if not path:
        return None
    if not path.startswith("tv-series/"):
        path = f"tv-series/{path}"
    return f"{BASE_URL}/{path}/season-{season}/episode-{episode}"


def _title_matches(candidate, title, year=None):
    candidate_norm = _normalize_title(candidate)
    title_norm = _normalize_title(title)
    if candidate_norm == title_norm:
        return True
    return bool(year and candidate_norm == f"{title_norm} {year}")


def _series_title_from_page(text):
    header = _dates_header(text)
    match = re.search(r"<u\b[^>]*>(?P<title>.*?)</u>", header, re.I | re.S)
    if match:
        return _strip_tags(match.group("title"))
    match = re.search(r"<meta\b(?=[^>]*property=[\"']og:title[\"'])(?=[^>]*content=[\"'](?P<title>[^\"']+)[\"'])", text, re.I | re.S)
    if match:
        title = html.unescape(match.group("title"))
        title = re.sub(r"\bTV series\b.*", "", title, flags=re.I).strip()
        return title or None
    return None


def _series_year_from_page(text):
    header = _dates_header(text)
    match = re.search(r"\(((?:19|20)\d{2})\)", header)
    return int(match.group(1)) if match else None


def _dates_header(text):
    match = re.search(
        r"<td\b(?=[^>]*\bid=[\"']dates_header_br[\"'])[^>]*>(?P<body>.*?)(?=</td>)",
        text,
        re.I | re.S,
    )
    return match.group("body") if match else ""


def _language_from_row(row):
    for tag_match in _IMG_RE.finditer(row):
        src = _attr(tag_match.group(0), "src")
        match = re.search(r"/(?P<alpha2>[a-z]{2})\.gif(?:\?|$)", src or "", re.I)
        if not match:
            continue
        alpha2 = match.group("alpha2").lower()
        language = ALPHA2_TO_ALPHA3.get(alpha2)
        if language:
            return language, alpha2
    return None, None


def _release_from_row(row):
    match = _B_RE.search(row)
    return _strip_tags(match.group("text")) if match else None


def _detail_url_from_row(row):
    for tag_match in _ANCHOR_RE.finditer(row):
        href = _attr(tag_match.group(0), "href")
        if href and re.search(r"/(?:greek|english)-subtitles/", href, re.I):
            return _absolute_url(href)
    return None


def _uploader_from_row(row):
    match = re.search(r"Uploaded\s+by\s*<a\b[^>]*>\s*<b\b[^>]*>(?P<name>.*?)</b>", row, re.I | re.S)
    if match:
        return _strip_tags(match.group("name"))
    names = [_strip_tags(match.group("text")) for match in _B_RE.finditer(row)]
    return names[1] if len(names) > 1 else None


def _downloads_from_row(row):
    match = re.search(r"<b\b[^>]*>\s*(?P<count>\d+)\s*</b>\s*DLs", row, re.I)
    return int(match.group("count")) if match else None


def _year_from_row(row):
    match = re.search(r"Year:\s*((?:19|20)\d{2})", row, re.I)
    return int(match.group(1)) if match else None


def _series_from_release(release):
    cleaned = re.split(r"\bs\d{1,2}e\d{1,3}\b|\b\d{1,2}x\d{1,3}\b", release or "", maxsplit=1, flags=re.I)[0]
    return cleaned.strip(" -._") or None


def _direct_download_target(text):
    for tag_match in _ANCHOR_RE.finditer(text):
        tag = tag_match.group(0)
        if "style55ws" not in (_attr(tag, "class") or ""):
            continue
        href = _attr(tag, "href")
        if href:
            return href
    return None


def _form_download_target(text):
    for tag_match in _FORM_RE.finditer(text):
        tag = tag_match.group(0)
        method = (_attr(tag, "method") or "").lower()
        if method and method != "post":
            continue
        action = _attr(tag, "action")
        if action:
            return action
    return None


def _captcha_site_key(text):
    for pattern in (
        r"g-recaptcha\b[^>]*\bdata-sitekey=[\"'](?P<key>[^\"']+)[\"']",
        r"grecaptcha\.execute\([\"'](?P<key>[^\"']+)[\"']",
    ):
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return html.unescape(match.group("key"))
    return None


def _absolute_url(value):
    value = html.unescape((value or "").strip())
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return f"{BASE_URL}{value}"
    return f"{BASE_URL}/{value.lstrip('/')}"


def _show_path_from_url(url):
    parsed = urllib.parse.urlparse(_absolute_url(url))
    path = parsed.path.strip("/")
    if path.startswith("tv-series/"):
        return path
    marker = "/tv-series/"
    if marker in path:
        return path.split(marker, 1)[1]
    return None


def _attr(tag, name):
    match = re.search(rf"\b{name}\s*=\s*([\"'])(?P<value>.*?)\1", tag or "", re.I | re.S)
    return html.unescape(match.group("value")) if match else None


def _strip_tags(value):
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _decode(body):
    if isinstance(body, str):
        return body
    for encoding in ("utf-8", "windows-1253", "iso-8859-7", "latin-1"):
        try:
            return (body or b"").decode(encoding)
        except UnicodeDecodeError:
            continue
    return (body or b"").decode("utf-8", errors="replace")


def _coerce_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize(value):
    value = unicodedata.normalize("NFKD", _coerce_text(value)).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM_RE.sub(" ", value.lower()).strip()


def _normalize_title(value):
    return _normalize(value)


def _tokens(value):
    return _normalize(value).split()


def _slug(value):
    slug = "-".join(_tokens(value))[:90]
    return slug or "subtitle"


def _normalize_release_group(value):
    return re.sub(r"[^a-z0-9]+", "", (_coerce_text(value) or "").lower())


def _episode_markers(normalized):
    for pattern in (_SXXEXX_RE, _X_EPISODE_RE):
        match = pattern.search(normalized or "")
        if match:
            return int(match.group("season")), int(match.group("episode"))
    return None, None


def _matches_token(value, normalized_text):
    if not _coerce_text(value):
        return False
    token = _normalize(value)
    if token in normalized_text.split():
        return True
    aliases = {
        "blu ray": {"bluray", "brrip", "bdrip"},
        "bluray": {"bluray", "brrip", "bdrip"},
        "web": {"web", "webdl", "webrip"},
        "web dl": {"webdl", "web"},
        "webrip": {"webrip", "web"},
        "hdtv": {"hdtv"},
    }
    return bool(aliases.get(token, set()) & set(normalized_text.split()))


def _alpha3_for_language(language):
    if isinstance(language, str):
        return language
    if isinstance(language, dict):
        return language.get("alpha3")
    return None


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


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


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
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


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    return "application/x-subrip"


def _normalize_line_endings(content):
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _sleep(config):
    delay_ms = 0
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000)
