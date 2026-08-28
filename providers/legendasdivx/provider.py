"""LegendasDivx provider for the Bazarr+ Provider Hub catalog.

legendasdivx.pt sits behind Cloudflare and behind a phpBB login, so the plugin
drives it through ai-cloudscraper (the catalog's anti-bot HTTP convention) with
an optional FlareSolverr fallback, and it keeps one scraper for the worker's
lifetime because the PHP session cookie set at login is what makes search and
download work at all.

Two things this file deliberately does NOT do:

* It never decompresses a response body. cloudscraper advertises brotli and gzip
  and the HTTP stack has already decoded the body by the time `content` is read.
  Decompressing again is the exact bug that was removed from the built-in
  provider's session, and it is the most likely way to break this plugin.
* It never extracts an archive. A zip or rar comes back to the host as raw bytes
  (Provider Hub v1.1+), and the host lists the members and calls
  `select_archive_member` so this worker can still pick the right episode
  without an extraction library and without shelling out.
"""

import base64
import hashlib
import html
import io
import json
import os
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

PROVIDER_ID = "legendasdivx"
BASE_URL = "https://www.legendasdivx.pt"
LOGIN_URL = f"{BASE_URL}/forum/ucp.php?mode=login"
SEARCH_URL = f"{BASE_URL}/modules.php"
HTTP_TIMEOUT_SECONDS = 30
# The site's real ceiling is 150 searches a day; stop short of it so a search
# never trips the block that follows.
SAFE_SEARCH_LIMIT = 145
MAX_PAGES = 6
RESULTS_PER_PAGE = 10
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt", ".smi", ".mpl")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 25000
DEFAULT_CAPTCHA_SOLVER_TIMEOUT_MS = 30000
CLOUDFLARE_STATUS_CODES = {403, 429, 503}
CLOUDFLARE_BODY_MARKERS = (
    "attention required! | cloudflare",
    "just a moment",
    "cf-challenge",
    "cf-error-details",
    "cf_chl_opt",
)
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

# phpBB names its cookies after the board prefix. u == 1 is the anonymous user,
# which is what a rejected login leaves behind, so a 200 response on its own
# proves nothing about being logged in.
COOKIE_USER_ID = "phpbb3_2z8zs_u"
COOKIE_SESSION_IDS = ("PHPSESSID", "phpbb3_2z8zs_sid")
# Cookies that are the login session. Everything else in the jar, the
# Cloudflare clearance above all, survives a re-login: it is expensive to get
# back and has nothing to do with whether the credentials are still good.
SESSION_COOKIE_PREFIXES = ("phpbb3_", "phpsessid")
ANONYMOUS_USER_ID = "1"
# Punctuation the site puts between a bold title and the rest of the header. The
# long dashes go in by codepoint so this source file stays pure ASCII.
TITLE_TRIM_CHARS = " -:" + chr(0x2013) + chr(0x2014)

LANGUAGES = {
    "por": {"alpha2": "pt", "country_alpha2": None, "filter_id": "28"},
    "por-BR": {"alpha2": "pt", "country_alpha2": "BR", "filter_id": "29"},
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Block-level markup the site uses to break a description into lines. The line
# structure matters: extract_release_info scores the description line by line,
# so collapsing it to one line would make this plugin and the built-in disagree.
_LINE_BREAK_RE = re.compile(r"<\s*br\s*/?>|</\s*(?:p|div|li|tr)\s*>", re.I)
_LINE_WS_RE = re.compile(r"[^\S\n]+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_INPUT_RE = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.I | re.S)
_ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", re.S)
_SUB_BOX_SPLIT_RE = re.compile(r"<div\b[^>]*class=['\"][^'\"]*\bsub_box\b[^'\"]*['\"][^>]*>", re.I)
_HITS_RE = re.compile(r"<th[^>]*>\s*Hits:\s*</th>\s*<td[^>]*>(?P<value>.*?)</td>", re.I | re.S)
_FPS_RE = re.compile(r"<th[^>]*>\s*Frame\s*Rate:\s*</th>\s*<td[^>]*>(?P<value>.*?)</td>", re.I | re.S)
_LANG_CELL_RE = re.compile(r"<th[^>]*>\s*Idioma:\s*</th>\s*<td[^>]*>(?P<value>.*?)</td>", re.I | re.S)
_DESC_RE = re.compile(
    r"<td\b[^>]*class=['\"][^'\"]*\btd_desc\b[^'\"]*['\"][^>]*>(?P<value>.*?)</td>",
    re.I | re.S,
)
_DOWNLOAD_RE = re.compile(
    r"<a\b[^>]*class=['\"][^'\"]*\bsub_download\b[^'\"]*['\"][^>]*href=['\"](?P<href>[^'\"]+)['\"]",
    re.I | re.S,
)
_DOWNLOAD_REVERSED_RE = re.compile(
    r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*class=['\"][^'\"]*\bsub_download\b[^'\"]*['\"]",
    re.I | re.S,
)
_HEADER_RE = re.compile(
    r"<div\b[^>]*class=['\"][^'\"]*\bsub_header\b[^'\"]*['\"][^>]*>(?P<body>.*?)</div>",
    re.I | re.S,
)
_BOLD_RE = re.compile(r"<b\b[^>]*>(?P<body>.*?)</b>", re.I | re.S)
_ANCHOR_TEXT_RE = re.compile(r"<a\b[^>]*>(?P<body>.*?)</a>", re.I | re.S)
_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)")
_SEARCH_COUNT_RE = re.compile(r"<!--\s*pesquisas:\s*(?P<count>\d+)\s*-->", re.I)
_PAGER_RE = re.compile(r"\((?P<count>[\d.\s]+?)\s+encontradas\)", re.I)
_SXXEYY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\s*e0*(?P<episode>\d{1,3})\b", re.I)
# "S01E01" has no word boundary after the season number, so the season token
# ends at "not another digit" instead.
_SEASON_ONLY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})(?!\d)", re.I)
_RECAPTCHA_KEY_RES = (
    re.compile(r"g-recaptcha\b[^>]*\bdata-sitekey=[\"'](?P<key>[^\"']+)[\"']", re.I),
    re.compile(r"data-sitekey=[\"'](?P<key>[^\"']+)[\"'][^>]*\bg-recaptcha\b", re.I),
    re.compile(r"grecaptcha\.execute\([\"'](?P<key>[^\"']+)[\"']", re.I),
)
_RAR4_MAGIC = b"Rar!\x1a\x07\x00"
_RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"

# Release tags that are pure digits once the name is split on punctuation. They
# have to go before a bare number in an archive member can be read as an episode,
# or "Show.1080p.H.264.srt" would look like it names episode 264.
_RELEASE_NOISE_RE = re.compile(
    r"\b\d{3,4}[ip]\b"
    r"|\b[xh]\.?\s?26[45]\b"
    r"|\bhevc\b|\bavc\b|\bxvid\b|\bdivx\b|\bvp9\b|\bav1\b"
    r"|\b(?:e?ac3|ddp?|dts(?:\W?hd)?|aac|flac|truehd|opus)\W?\d(?:\W?\d)?\b"
    r"|\b\d{1,2}\W?bit\b"
    r"|\b(?:19|20)\d{2}\b"
    r"|\bmp3\b|\bmp4\b|\bx?\d{2,4}kbps\b"
    r"|\bh\W?26[45]\b",
    re.I,
)
_EXPLICIT_EPISODE_RE = re.compile(
    r"\bs0*(?P<season>\d{1,2})\s*[._\- ]?\s*e0*(?P<episode>\d{1,3})"
    r"(?P<extra>(?:\s*[-_. ]?\s*e0*\d{1,3})*)",
    re.I,
)
_EXPLICIT_XFORM_RE = re.compile(r"\b(?P<season>\d{1,2})x0*(?P<episode>\d{1,3})\b", re.I)
_EXPLICIT_EPISODE_ONLY_RE = re.compile(
    r"\b(?:e|ep|episode|episodio|epis\w?dio)\.?\s*0*(?P<episode>\d{1,3})\b", re.I
)
_EXTRA_EPISODE_RE = re.compile(r"e0*(?P<episode>\d{1,3})", re.I)
_SEASON_WORD_RE = re.compile(r"\b(?:s|season|temporada)\.?\s*0*(?P<season>\d{1,2})\b", re.I)
_BARE_NUMBER_RE = re.compile(r"(?<![0-9a-zA-Z])(?P<number>\d{1,4})(?![0-9a-zA-Z])")


class AuthenticationError(PermissionError):
    """Credentials were rejected, or the session is not an authenticated one."""


class CloudflareBlockedError(RuntimeError):
    """Cloudflare answered with a challenge that could not be cleared."""


class SearchLimitReached(RuntimeError):
    """The site's daily search counter is at the safe cap."""


class DownloadLimitExceeded(RuntimeError):
    """The account's daily download quota is spent."""


class IPAddressBlocked(RuntimeError):
    """The site says this IP is blocked."""


class HttpResponse:
    def __init__(self, status, body, headers=None, url=""):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})
        self.url = url or ""


class LegendasDivxProvider:
    def __init__(self):
        self._authenticated = False
        self._scraper = None
        self._scraper_initialized = False
        self._cookie_jar = None
        self._flaresolverr_cookies = {}
        self._flaresolverr_user_agent = ""

    # ----------------------------------------------------------------- hub API

    def search(self, video, languages, config):
        video = dict(video or {})
        if video.get("kind") not in {"movie", "episode"}:
            return []
        requested = _requested_languages(languages)
        if not requested:
            return []
        config = dict(config or {})
        self._ensure_authenticated(config)

        results = []
        seen = set()
        skip_wrong_fps = bool(config.get("skip_wrong_fps", False))
        for language_code in requested:
            language_hits = 0
            for search_url in build_search_urls(video, language_code):
                if not search_url:
                    continue
                response = self._get_search_response(search_url, config)
                _assert_search_available(response)
                rows = parse_search_results(response.body)
                rows.extend(self._load_more_pages(search_url, response.body, config))
                for item in rows:
                    if item["language"] != language_code:
                        continue
                    if skip_wrong_fps and _fps_mismatch(video.get("fps"), item.get("frame_rate")):
                        continue
                    key = (item["lid"], item["language"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(_candidate(video, item))
                    language_hits += 1
                if language_hits:
                    break
        return sorted(results, key=lambda row: (-row["score"], -_safe_int(row["display"].get("hits"), 0)))

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        page_link = payload.get("page_link")
        if not page_link:
            raise ValueError("legendasdivx download requires page_link")
        config = dict(config or {})
        self._ensure_authenticated(config)
        _sleep(config)
        response = self._download_get(page_link, config)
        if response.status in REDIRECT_STATUS_CODES:
            # Redirected to the login page: the PHP session expired under a reused
            # worker. Drop it, log in again, and retry once.
            self._reset_session()
            self._ensure_authenticated(config)
            _sleep(config)
            response = self._download_get(page_link, config)
        if response.status in REDIRECT_STATUS_CODES:
            raise AuthenticationError(
                "LegendasDivx download redirected to the login page, the session is not authenticated"
            )
        _raise_for_status(response, "LegendasDivx download")
        _assert_download_allowed(response)
        return _download_payload(response.body, payload)

    def select_archive_member(self, provider_payload, language, members, config):
        """Pick the archive member for the wanted episode.

        The host lists the archive (zip and rar alike) and calls this back, which
        is the only way a stdlib-only worker can resolve a season pack inside a
        rar. Tri-state: pin a member, defer to the host's own episode pick, or
        reject. Rejecting is what "no subtitle" looks like; handing back an
        arbitrary member is how a subtitle for the wrong episode gets written to
        the library and marks the episode done with no error anywhere.
        """
        del language, config
        member, decision = pick_archive_member(members, provider_payload or {})
        return {"member": member, "decision": decision}

    # ------------------------------------------------------------ orchestration

    def _download_get(self, page_link, config):
        return self._http_get(
            page_link, config=config, referer=f"{BASE_URL}/index.php", allow_redirects=False
        )

    def _get_search_response(self, search_url, config):
        _sleep(config)
        response = self._http_get(
            search_url, config=config, referer=f"{BASE_URL}/index.php", allow_redirects=False
        )
        if response.status in REDIRECT_STATUS_CODES:
            self._reset_session()
            self._ensure_authenticated(config)
            _sleep(config)
            response = self._http_get(
                search_url, config=config, referer=f"{BASE_URL}/index.php", allow_redirects=False
            )
        _raise_for_status(response, "LegendasDivx search")
        return response

    def _load_more_pages(self, search_url, first_body, config):
        total = _page_count(first_body)
        if total <= 1:
            return []
        rows = []
        for page in range(2, total + 1):
            _sleep(config)
            response = self._http_get(
                f"{search_url}&page={page}", config=config, referer=search_url, allow_redirects=False
            )
            if response.status in REDIRECT_STATUS_CODES:
                self._reset_session()
                self._ensure_authenticated(config)
                _sleep(config)
                response = self._http_get(
                    f"{search_url}&page={page}", config=config, referer=search_url, allow_redirects=False
                )
            _raise_for_status(response, "LegendasDivx search page")
            _assert_search_available(response)
            rows.extend(parse_search_results(response.body))
        return rows

    # ------------------------------------------------------------------- login

    def _ensure_authenticated(self, config):
        if self._authenticated:
            return
        username = str(config.get("username") or "").strip()
        password = str(config.get("password") or "")
        if not username or not password:
            raise AuthenticationError("LegendasDivx username and password are required")

        response = self._http_get(LOGIN_URL, config=config, referer=f"{BASE_URL}/index.php")
        _raise_for_status(response, "LegendasDivx login page")
        login_text = _decode_html(response.body)
        data = parse_login_inputs(login_text)
        data.update({"username": username, "password": password})
        data.setdefault("login", "Login")

        site_key = _captcha_site_key(login_text)
        if site_key or _captcha_required(login_text):
            token = self._captcha_response(site_key, LOGIN_URL, config)
            if not token:
                raise AuthenticationError(
                    "LegendasDivx asked for a captcha on the login form. Set captcha_solver_url "
                    "(plus captcha_solver_token if the solver needs one) or captcha_response in the "
                    "provider settings."
                )
            data["g-recaptcha-response"] = token
            data["recaptcha_response"] = token

        _sleep(config)
        response = self._http_post(
            LOGIN_URL, data, config=config, referer=LOGIN_URL, allow_redirects=False
        )
        if response.status >= 400:
            _raise_for_status(response, "LegendasDivx login")

        # The login POST answers 200 whether or not the credentials were accepted,
        # so the session cookies are what decides. phpBB leaves the anonymous user
        # id behind on a rejected login, and no session id at all when the board
        # refused to start a session.
        cookies = self.session_cookies()
        user_id = cookies.get(COOKIE_USER_ID)
        session_id = next((cookies.get(name) for name in COOKIE_SESSION_IDS if cookies.get(name)), None)
        if user_id == ANONYMOUS_USER_ID or not session_id:
            raise AuthenticationError(
                "LegendasDivx did not return an authenticated session, check your credentials"
            )
        self._authenticated = True

    def _reset_session(self):
        """Forget the login so the next call logs in again.

        The scraper itself is kept. Its Cloudflare clearance is unrelated to
        whether the phpBB session expired, and re-solving the challenge for every
        expired login would be a needless round of work against the site.
        """
        self._authenticated = False
        self._flaresolverr_cookies = {}
        for jar in (getattr(self._scraper, "cookies", None), self._cookie_jar):
            if jar is None:
                continue
            try:
                stale = [cookie for cookie in jar if _is_session_cookie(cookie.name)]
                for cookie in stale:
                    jar.clear(cookie.domain, cookie.path, cookie.name)
            except Exception:  # pragma: no cover, defensive against jar shapes
                continue

    def session_cookies(self):
        """Every cookie the current session holds, name to value."""
        cookies = {}
        scraper = self._scraper
        if scraper is not None:
            try:
                cookies.update({cookie.name: cookie.value for cookie in scraper.cookies})
            except Exception:  # pragma: no cover, defensive against jar shapes
                pass
        if self._cookie_jar is not None:
            for cookie in self._cookie_jar:
                cookies[cookie.name] = cookie.value
        cookies.update(self._flaresolverr_cookies)
        return cookies

    def _captcha_response(self, site_key, page_url, config):
        if config.get("captcha_response"):
            return str(config["captcha_response"])
        solver_url = str(config.get("captcha_solver_url") or "").strip()
        if not solver_url:
            return None
        payload = {"site_key": site_key or "", "site_url": page_url, "provider": PROVIDER_ID}
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        token = str(config.get("captcha_solver_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        timeout = max(
            1, _safe_int(config.get("captcha_solver_timeout_ms"), DEFAULT_CAPTCHA_SOLVER_TIMEOUT_MS) / 1000
        )
        request = urllib.request.Request(
            solver_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("legendasdivx captcha solver returned invalid JSON") from error
        for key in ("response", "token", "captcha_response", "gRecaptchaResponse", "g-recaptcha-response"):
            if parsed.get(key):
                return str(parsed[key])
        return None

    # -------------------------------------------------------------------- HTTP

    def _http_get(self, url, config=None, referer=None, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True):
        return self._request("GET", url, None, config, referer, timeout, allow_redirects)

    def _http_post(
        self, url, data, config=None, referer=None, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True
    ):
        return self._request("POST", url, data or {}, config, referer, timeout, allow_redirects)

    def _request(self, method, url, data, config, referer, timeout, allow_redirects):
        headers = _browser_headers(referer, self._flaresolverr_user_agent)
        scraper = self._get_scraper()
        if scraper is not None:
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            try:
                response = scraper.request(
                    method, url, data=data, headers=headers, timeout=timeout,
                    allow_redirects=allow_redirects,
                )
            except Exception as error:
                if _flaresolverr_url(config) and _is_cloudflare_exception(error):
                    return self._flaresolverr_get(url, config)
                raise
            # response.content is already decoded: urllib3 unwraps gzip, deflate and
            # brotli before requests hands it over. Decompressing it here on the
            # strength of the Content-Encoding header is a double decode, and that
            # is precisely the failure that was removed from the built-in provider.
            body = getattr(response, "content", None)
            if body is None:
                body = str(getattr(response, "text", "")).encode("utf-8")
            result = HttpResponse(
                getattr(response, "status_code", 0),
                body,
                _response_headers(getattr(response, "headers", None)),
                str(getattr(response, "url", url) or url),
            )
            if _is_cloudflare_challenge(result.status, result.headers, result.body):
                if _flaresolverr_url(config):
                    return self._flaresolverr_get(url, config)
                raise CloudflareBlockedError(
                    "LegendasDivx hit a Cloudflare block and no FlareSolverr URL is configured"
                )
            return result
        return self._raw_request(method, url, data, headers, timeout, allow_redirects, config)

    def _raw_request(self, method, url, data, headers, timeout, allow_redirects, config):
        """stdlib fallback for an environment without ai-cloudscraper installed.

        It keeps a cookie jar so the login session still survives across calls; it
        cannot clear a Cloudflare challenge, which is what FlareSolverr is for.
        """
        body = urllib.parse.urlencode(data or {}).encode("utf-8") if data is not None else None
        request_headers = dict(headers)
        if body is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        opener = self._get_opener(allow_redirects)
        try:
            with opener.open(request, timeout=timeout) as response:
                result = HttpResponse(
                    response.status, response.read(), _response_headers(response.headers), response.url
                )
        except urllib.error.HTTPError as error:
            result = HttpResponse(error.code, error.read(), _response_headers(error.headers), url)
        if _is_cloudflare_challenge(result.status, result.headers, result.body):
            if _flaresolverr_url(config):
                return self._flaresolverr_get(url, config)
            raise CloudflareBlockedError(
                "LegendasDivx hit a Cloudflare block and no FlareSolverr URL is configured"
            )
        return result

    def _get_opener(self, allow_redirects):
        if self._cookie_jar is None:
            self._cookie_jar = CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(self._cookie_jar)]
        if not allow_redirects:
            handlers.append(_NoRedirect())
        return urllib.request.build_opener(*handlers)

    def _flaresolverr_get(self, url, config):
        endpoint = _flaresolverr_url(config)
        if not endpoint:
            raise CloudflareBlockedError(
                "LegendasDivx hit a Cloudflare block and no FlareSolverr URL is configured"
            )
        timeout_ms = _flaresolverr_timeout_ms(config)
        payload = {"cmd": "request.get", "url": url, "maxTimeout": timeout_ms}
        cookies = self.session_cookies()
        if cookies:
            payload["cookies"] = [{"name": name, "value": value} for name, value in cookies.items()]
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_ms / 1000 + 2) as response:
                raw = response.read()
        except Exception as error:
            raise CloudflareBlockedError(f"LegendasDivx FlareSolverr request failed: {error}") from error
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CloudflareBlockedError("LegendasDivx FlareSolverr returned invalid JSON") from error
        if parsed.get("status") not in (None, "ok"):
            raise CloudflareBlockedError(
                f"LegendasDivx {parsed.get('message') or 'FlareSolverr did not solve the challenge'}"
            )
        solution = parsed.get("solution") or {}
        text = solution.get("response")
        if text is None:
            raise CloudflareBlockedError("LegendasDivx FlareSolverr response had no page body")
        body = text if isinstance(text, bytes) else str(text).encode("utf-8")
        status = _safe_int(solution.get("status"), 200)
        if _is_cloudflare_challenge(status, solution.get("headers") or {}, body):
            raise CloudflareBlockedError("LegendasDivx FlareSolverr response is still a Cloudflare block")
        self._store_flaresolverr_solution(solution)
        return HttpResponse(status, body, solution.get("headers") or {}, solution.get("url") or url)

    def _store_flaresolverr_solution(self, solution):
        user_agent = solution.get("userAgent")
        if user_agent:
            # Reuse the solved User-Agent: the clearance cookie is bound to it.
            self._flaresolverr_user_agent = user_agent
        for cookie in solution.get("cookies") or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            self._flaresolverr_cookies[name] = value
            if self._scraper is not None:
                try:
                    self._scraper.cookies.set(name, value, domain=".legendasdivx.pt")
                except Exception:  # pragma: no cover, defensive against jar shapes
                    pass

    def _get_scraper(self):
        if not self._scraper_initialized:
            self._scraper = _create_cloudscraper()
            self._scraper_initialized = True
        return self._scraper


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


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
        fallback = dict(options)
        fallback.pop("enable_cookie_persistence", None)
        return cloudscraper.create_scraper(**fallback)


def _is_session_cookie(name):
    lowered = str(name or "").lower()
    return any(lowered.startswith(prefix) for prefix in SESSION_COOKIE_PREFIXES)


def _browser_headers(referer=None, user_agent=""):
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": BASE_URL,
    }
    if referer:
        headers["Referer"] = referer
    return headers


# ----------------------------------------------------------------- URL building


def build_search_urls(video, language_code):
    if language_code not in LANGUAGES:
        return []
    kind = (video or {}).get("kind")
    if kind == "movie":
        return [url for url in [_build_movie_search_url(video, language_code)] if url]
    if kind == "episode":
        return _build_episode_search_urls(video, language_code)
    return []


def _build_movie_search_url(video, language_code):
    query = str((video or {}).get("imdb_id") or (video or {}).get("title") or "").strip()
    if not query:
        return ""
    params = [
        ("name", "Downloads"),
        ("file", "jz"),
        ("d_op", "search"),
        ("op", "_jz00"),
        ("query", query),
        ("form_cat", LANGUAGES[language_code]["filter_id"]),
        ("temporada", ""),
        ("episodio", ""),
        ("imdb", ""),
    ]
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


def _build_episode_search_urls(video, language_code):
    season = _safe_int((video or {}).get("season"), None)
    episode = _safe_int((video or {}).get("episode"), None)
    if season is None or episode is None:
        return []
    series_imdb_id = str((video or {}).get("series_imdb_id") or "").strip()
    if series_imdb_id:
        # The backend filters by series id here, which is why the episode branch
        # of derive_matches can claim series/season/episode without re-proving it.
        params = [
            ("name", "Downloads"),
            ("file", "jz"),
            ("d_op", "jz_00"),
            ("op", ""),
            ("query", ""),
            ("faz", "pesquisa_episodio"),
            ("idioma", LANGUAGES[language_code]["filter_id"]),
            ("temporada", str(season)),
            ("episodio", str(episode)),
            ("imdb", series_imdb_id.removeprefix("tt")),
        ]
        return [f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"]

    series = str((video or {}).get("series") or "").strip()
    if not series:
        return []
    # Precise first, then the season-pack shape the built-in falls back to.
    queries = [f'"{series}" S{season:02d}E{episode:02d}', f'"{series}" S{season:02d}']
    urls = []
    for query in queries:
        params = [
            ("name", "Downloads"),
            ("file", "jz"),
            ("d_op", "search"),
            ("op", "_jz00"),
            ("query", query.lower()),
            ("form_cat", LANGUAGES[language_code]["filter_id"]),
            ("temporada", str(season)),
            ("episodio", str(episode)),
            ("imdb", ""),
        ]
        urls.append(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
    return urls


# --------------------------------------------------------------------- parsing


def parse_login_inputs(body):
    values = {}
    for match in _INPUT_RE.finditer(_decode_html(body)):
        attrs = _attrs(match.group("attrs"))
        name = attrs.get("name")
        if name:
            values[name] = attrs.get("value", "")
    return values


def parse_search_results(body):
    text = _decode_html(body)
    rows = []
    for chunk in _SUB_BOX_SPLIT_RE.split(text)[1:]:
        item = _parse_sub_box(chunk)
        if item:
            rows.append(item)
    return rows


def _parse_sub_box(chunk):
    language = _language_from_chunk(chunk)
    if not language:
        return None
    description = _match_multiline_text(_DESC_RE, chunk)
    page_link = _download_link(chunk)
    if not description or not page_link:
        return None
    title, year, uploader = _parse_sub_header(chunk)
    return {
        "lid": _subtitle_id_from_url(page_link),
        "page_link": page_link,
        "language": language,
        "description": description,
        "title": title,
        "year": year,
        "hits": _safe_int(_match_text(_HITS_RE, chunk), 0),
        "frame_rate": _match_text(_FPS_RE, chunk),
        "uploader": uploader,
        "release_info": extract_release_info(title, year, description),
    }


def _parse_sub_header(chunk):
    """Title, year and uploader from the result header.

    The header states them; the description is free text an uploader typed. The
    built-in reads them here for exactly that reason, and inferring the title
    from the description instead is how an unrelated result claims a title match.
    """
    header = _HEADER_RE.search(chunk or "")
    if not header:
        return "", None, "anonymous"
    body = header.group("body")
    bold = _BOLD_RE.search(body)
    title = _strip_tags(bold.group("body")) if bold else ""
    year_match = _YEAR_RE.search(_strip_tags(body))
    year = _safe_int(year_match.group("year"), None) if year_match else None
    if title:
        # "The Matrix (1999)" inside the bold element: the year is not part of
        # the title, whichever side of the tag the site puts it on.
        title = _YEAR_RE.sub("", title).strip(TITLE_TRIM_CHARS).strip()
    anchor = _ANCHOR_TEXT_RE.search(body)
    uploader = (_strip_tags(anchor.group("body")) if anchor else "") or "anonymous"
    return title, year, uploader


# ------------------------------------------------------- release-name extraction
#
# clean_release_line and extract_release_info below are lifted verbatim from the
# built-in provider (custom_libs/subliminal_patch/providers/legendasdivx.py),
# whose author verified them against the live site. They are pure `re` and touch
# nothing outside this module, so copying them character for character is what
# makes the parity assertions in tests/test_legendasdivx.py meaningful: both
# implementations must produce the same release name for the same description.


def clean_release_line(text):
    # Separate glued keywords like versãoThe or releaseThe
    text = re.sub(r"(vers[aã]o|release|filme)([A-Z0-9])", r"\1 \2", text, flags=re.I)
    # Strip common Portuguese subtitle upload prefixes
    prefix_pattern = (
        r"^(legendas?\s*(anteriormente\s*)?(enviadas?\s*(por|pelo|do)?\s*[\w\d_]+\s*)?"
        r"|sincronizadas?|ressincronizadas?|sinc|sync|traduzidas?|ripadas?\s*(por\s*mim)?|ajustad[ao]s?|ajustei\s*(a\s*)?sincronia)?"
        r"\s*(do\s*dvd\.?|de\s*raiz\s*)?(para\s*(a|o|as|os)?\s*)?(vers[aã]o|release[s]?|filme|nomes?)?\s*[:\-–]?\s*"
    )
    return re.sub(prefix_pattern, "", text, flags=re.I).strip().strip("*").strip("`").strip()


def extract_release_info(title, year, desc):
    default_name = f"{title} ({year})" if year and title else (title or "")
    if not desc or desc.strip().lower() in (
        "não há descrição disponível",
        "nao ha descricao disponivel",
        "n/a",
        "none",
        "",
    ):
        return default_name

    lines = [line.strip().strip("*").strip("`") for line in desc.splitlines() if line.strip()]
    candidates = []
    release_re = re.compile(
        r"(2160p|1080p|720p|480p|4k|bluray|blu-ray|bdrip|brrip|web-dl|webdl|web-rip|webrip|web|dvdrip|dvd|hdtv|x264|x265|hevc|h\.264|h\.265|xvid|divx|remastered|proper|internal|repack)",
        re.I,
    )
    conversational_re = re.compile(
        r"^(legenda[s]?|ripada[s]?|enviada[s]?|postada[s]?|corrigido[s]?|feita[s]?|fiz\s|peguei\s|são\s|sao\s|não\s|nao\s|avisem|cumps|enjoy|obrigado|duração|duracao)",
        re.I,
    )

    for line in lines:
        cleaned = clean_release_line(line)
        if not cleaned:
            continue
        if release_re.search(cleaned):
            match = re.search(
                r"([\w\.\-_]+(?:2160p|1080p|720p|480p|4k|bluray|blu-ray|bdrip|brrip|web-dl|webdl|dvdrip|dvd|x264|x265|hevc|xvid|divx)[\w\.\-_]*)",
                cleaned,
                re.I,
            )
            if match and len(match.group(1)) > 10:
                candidates.append(match.group(1).strip("."))
            elif not conversational_re.search(cleaned) and len(cleaned) > 5:
                candidates.append(cleaned)
        elif title and title.lower() in cleaned.lower() and len(cleaned) > len(title) and not conversational_re.search(cleaned):
            candidates.append(cleaned)

    if candidates:
        def candidate_quality(cand):
            score = len(cand)
            if title and title.lower() in cand.lower():
                score += 100
            if "." in cand or "-" in cand:
                score += 50
            return score

        best = max(candidates, key=candidate_quality)
        return best

    return default_name


# ------------------------------------------------------------------ candidates


def _candidate(video, item):
    language = _language_payload(item["language"])
    matches = derive_matches(video, item)
    score = _score_from_matches(matches, item)
    release_info = item.get("release_info") or item["description"]
    suffix = "pt-br" if item["language"] == "por-BR" else language["alpha2"]
    filename = f"legendasdivx.{_slug(release_info)}.{suffix}.zip"
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "lid": item["lid"],
        "page_link": item["page_link"],
        "filename": filename,
        "language": item["language"],
        "release_info": release_info,
        "frame_rate": item.get("frame_rate"),
    }
    if (video or {}).get("kind") == "episode":
        # download() forwards these so select_archive_member can resolve a season
        # pack, and so the host's own picker has an episode to fall back on.
        payload["season"] = _safe_int(video.get("season"), None)
        payload["episode"] = _safe_int(video.get("episode"), None)
        payload["absolute_episode"] = _safe_int(video.get("absolute_episode"), None)
    return {
        "provider": PROVIDER_ID,
        "id": f"legendasdivx-{item['lid']}-{item['language']}",
        "language": language,
        "release_info": release_info,
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": item["page_link"],
        "display": {
            "source": "legendasdivx.pt",
            "release": release_info,
            "title": item.get("title") or "",
            "year": item.get("year"),
            "uploader": item.get("uploader") or "anonymous",
            "hits": item.get("hits", 0),
            "frame_rate": item.get("frame_rate"),
        },
        "provider_payload": payload,
    }


def derive_matches(video, item):
    video = video or {}
    description = item.get("description") or ""
    release_info = item.get("release_info") or ""
    header_title = item.get("title") or ""
    desc_tokens = set(_tokens(description))
    release_tokens = _release_tokens(description) | _release_tokens(release_info)
    matches = []

    if video.get("kind") == "movie":
        titles = [t for t in [video.get("title")] + list(video.get("alternative_titles") or []) if t]
        if any(_normalize(title) == _normalize(header_title) for title in titles) or any(
            _all_tokens_present(title, desc_tokens) for title in titles
        ):
            matches.append("title")
        if video.get("year") and (
            _safe_int(item.get("year"), None) == _safe_int(video.get("year"), None)
            or str(video.get("year")) in desc_tokens
        ):
            matches.append("year")
        # The movie search sends imdb='' , so nothing on the backend guarantees
        # this result is the right film. Claim the id only when the page carries
        # it: score.py expands a movie imdb_id match into title plus year, which
        # is most of the way to the default minimum score on its own.
        imdb_id = str(video.get("imdb_id") or "").lower()
        if imdb_id and imdb_id in description.lower():
            matches.append("imdb_id")
    elif video.get("kind") == "episode":
        if video.get("series_imdb_id"):
            # The search URL filtered on this series id, so the backend already
            # guaranteed the series, season and episode of every row it returned.
            matches.extend(["series", "series_imdb_id", "season", "episode"])
        else:
            names = [n for n in [video.get("series")] + list(video.get("alternative_series") or []) if n]
            if any(_normalize(name) == _normalize(header_title) for name in names) or any(
                _all_tokens_present(name, desc_tokens) for name in names
            ):
                matches.append("series")
            if _season_in_description(description, video.get("season")):
                matches.append("season")
            if _episode_in_description(description, video.get("season"), video.get("episode")):
                matches.append("episode")
        if video.get("year") and str(video.get("year")) in desc_tokens:
            matches.append("year")

    for key in ("source", "resolution", "video_codec", "audio_codec", "release_group"):
        value = video.get(key)
        if value and _contains_release_value(release_tokens, value):
            matches.append(key)
    if item.get("frame_rate") and video.get("fps") and not _fps_mismatch(video.get("fps"), item.get("frame_rate")):
        matches.append("fps")
    return _unique(matches)


def _score_from_matches(matches, item):
    score = 30
    weights = {
        "title": 20,
        "series": 20,
        "series_imdb_id": 20,
        "imdb_id": 20,
        "season": 10,
        "episode": 15,
        "year": 10,
        "release_group": 8,
        "source": 4,
        "resolution": 4,
        "video_codec": 3,
        "audio_codec": 3,
        "fps": 4,
    }
    for match in matches:
        score += weights.get(match, 0)
    hits = _safe_int(item.get("hits"), 0)
    if hits >= 100:
        score += 3
    elif hits >= 10:
        score += 1
    return min(score, 100)


# -------------------------------------------------------------------- download


def _download_payload(body, payload):
    payload = payload or {}
    if not body or not body.strip():
        raise ValueError("legendasdivx download returned an empty body")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+). The worker declares no archive
        # library and never shells out; the host lists the members and calls
        # select_archive_member so a season pack still resolves to the right episode.
        return {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
            "select_member": True,
            "season": payload.get("season"),
            "episode": payload.get("episode"),
        }
    if _looks_like_html(body):
        raise ValueError("legendasdivx download returned an HTML page instead of a subtitle")
    return _content_payload(_normalize_line_endings(body), _format_from_filename(payload.get("filename")))


def pick_archive_member(members, payload):
    """Resolve the archive member for the wanted episode.

    Returns (member, decision) where decision is pin, defer or reject.

    * No episode context (a movie, or a payload with nothing to match on): defer,
      because the host's own picker handles that case and this has nothing to add.
    * Episode context with a matching member: pin the best-scoring one.
    * Episode context with no matching member: reject. The built-in used to return
      an arbitrary member here, which silently wrote a subtitle for a different
      episode into the library and marked the episode done.
    """
    payload = payload or {}
    candidates = [
        name
        for name in (members or [])
        if not name.endswith("/")
        and _subtitle_extension(name)
        and not os.path.basename(name).startswith(".")
    ]
    if not candidates:
        return None, "reject"

    season = _safe_int(payload.get("season"), None)
    episode = _safe_int(payload.get("episode"), None)
    absolute = _safe_int(payload.get("absolute_episode"), None)
    if episode is None:
        return None, "defer"

    matching = [name for name in candidates if member_matches_episode(name, season, episode, absolute)]
    if not matching:
        return None, "reject"
    if len(matching) == 1:
        return matching[0], "pin"

    release_tokens = _release_tokens(payload.get("release_info"))

    def rank(name):
        score = 0
        if season is not None and re.search(rf"\bs0*{season}\s*e0*{episode}\b", _normalize_release(name), re.I):
            score += 120
        score += 4 * len(release_tokens & _release_tokens(name))
        return score

    # Sorted for the tie-break so "the best member" means something stable
    # whatever order the host listed the archive in.
    return max(sorted(matching), key=rank), "pin"


def member_matches_episode(name, season, episode, absolute=None):
    """False when an archive member's name contradicts the wanted episode.

    Matched against the member's full path inside the archive, not its basename:
    some packs put the episode in the directory and call every file the same
    thing, and matching the basename alone accepts every one of them.

    A name that states nothing about season or episode is not a contradiction,
    the same rule the built-in applies through guessit.
    """
    if episode is None:
        return True
    wanted = {episode}
    if absolute is not None:
        wanted.add(absolute)

    for reading_season, reading_episodes in _episode_readings(_normalize_release(name)) or [(None, None)]:
        if reading_episodes is None:
            # The name says nothing about numbering, which is not a contradiction.
            return True
        if reading_season is not None and season is not None and reading_season != season:
            # An explicit season is a claim about season-relative numbering, so
            # Show.S01E14 is not the subtitle for S02E01 just because that
            # episode's absolute number happens to be 14.
            continue
        if reading_episodes & wanted:
            return True
    return False


def _episode_readings(text):
    """Every (season, episodes) a normalised member name could be claiming.

    A list, not one merged pair, because a bare number is genuinely ambiguous and
    the readings must not cross-contaminate: packs from this site name members
    "103 - Pilot.srt" (season one, episode three) while anime packs name them
    "One Piece - 310" (an absolute number). Merging those would let the season
    from one reading veto the episode from the other, which rejects every anime
    download. An empty list means the name states nothing at all.
    """
    readings = []

    for match in _EXPLICIT_EPISODE_RE.finditer(text):
        episodes = {int(match.group("episode"))}
        for extra in _EXTRA_EPISODE_RE.finditer(match.group("extra") or ""):
            episodes.add(int(extra.group("episode")))
        readings.append((int(match.group("season")), episodes))
    for match in _EXPLICIT_XFORM_RE.finditer(text):
        readings.append((int(match.group("season")), {int(match.group("episode"))}))
    if readings:
        return readings

    season_match = _SEASON_WORD_RE.search(text)
    season_word = int(season_match.group("season")) if season_match else None
    for match in _EXPLICIT_EPISODE_ONLY_RE.finditer(text):
        readings.append((season_word, {int(match.group("episode"))}))
    if readings:
        return readings

    # No explicit spelling. Strip the release tags that are pure digits once the
    # name is split on punctuation, then read whatever bare numbers are left.
    for match in _BARE_NUMBER_RE.finditer(_RELEASE_NOISE_RE.sub(" ", text)):
        number = int(match.group("number"))
        if number <= 0:
            continue
        # As an episode or absolute number on its own,
        readings.append((None, {number}))
        if number >= 100:
            # and as the compact season-plus-episode this site uses in packs.
            head, tail = divmod(number, 100)
            if tail:
                readings.append((head, {tail}))
        if number >= 1000:
            head, tail = divmod(number, 1000)
            if tail:
                readings.append((head, {tail}))
    return readings


# ---------------------------------------------------------------------- guards


def _assert_search_available(response):
    text = _decode_html(response.body)
    count = _search_count(text)
    if count is not None and count >= SAFE_SEARCH_LIMIT:
        raise SearchLimitReached("LegendasDivx: the daily search limit for this account has been reached")
    if _is_ip_blocked(text):
        raise IPAddressBlocked("LegendasDivx says this IP address is blocked")


def _assert_download_allowed(response):
    text = _normalize(_decode_html(response.body[:4096]))
    if "limite de downloads" in text and "atingido" in text:
        raise DownloadLimitExceeded("LegendasDivx daily download limit reached")


def _raise_for_status(response, context):
    if response.status < 400:
        return
    text = _decode_html(response.body)
    if _is_ip_blocked(text):
        raise IPAddressBlocked(f"{context}: LegendasDivx says this IP address is blocked")
    if response.status in {401, 403} and not _is_cloudflare_challenge(
        response.status, response.headers, response.body
    ):
        raise AuthenticationError(f"{context}: HTTP {response.status}")
    raise RuntimeError(f"{context}: HTTP {response.status}")


def _is_ip_blocked(text):
    return "bloqueado" in _normalize(text)


def _is_cloudflare_challenge(status, headers, body):
    normalized = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    if "cf-mitigated" in normalized:
        return True
    if _safe_int(status, 0) not in CLOUDFLARE_STATUS_CODES:
        return False
    if "cloudflare" in normalized.get("server", "").lower():
        return True
    text = body.decode("utf-8", "ignore").lower() if isinstance(body, bytes) else str(body or "").lower()
    return any(marker in text for marker in CLOUDFLARE_BODY_MARKERS)


def _is_cloudflare_exception(error):
    name = type(error).__name__.lower()
    if "cloudflare" in name or "captcha" in name:
        return True
    return "cloudflare" in str(error).lower()


def _captcha_site_key(text):
    for pattern in _RECAPTCHA_KEY_RES:
        match = pattern.search(text or "")
        if match:
            return match.group("key")
    return None


def _captcha_required(text):
    lowered = (text or "").lower()
    return "g-recaptcha" in lowered or "grecaptcha" in lowered or "h-captcha" in lowered


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config):
    value = _safe_int((config or {}).get("flaresolverr_timeout_ms"), DEFAULT_FLARESOLVERR_TIMEOUT_MS)
    return max(5000, min(30000, value))


# --------------------------------------------------------------------- helpers


def _page_count(body):
    match = _PAGER_RE.search(_decode_html(body))
    if not match:
        return 1
    count = _safe_int(re.sub(r"[^\d]", "", match.group("count")), 0)
    if count <= 0:
        return 1
    return min(MAX_PAGES, (count // RESULTS_PER_PAGE) + 1)


def _search_count(text):
    match = _SEARCH_COUNT_RE.search(text or "")
    if not match:
        return None
    return _safe_int(match.group("count"), None)


def _language_from_chunk(chunk):
    cell = _LANG_CELL_RE.search(chunk or "")
    scope = _normalize(cell.group("value") if cell else chunk)
    if "brazil" in scope or "brasil" in scope:
        return "por-BR"
    if "portugal" in scope or "portuguese" in scope or "portugues" in scope:
        return "por"
    return None


def _download_link(chunk):
    match = _DOWNLOAD_RE.search(chunk or "") or _DOWNLOAD_REVERSED_RE.search(chunk or "")
    if not match:
        return ""
    href = html.unescape(match.group("href"))
    # The site emits the download anchor as a bare query string on some pages.
    if href.startswith("?"):
        href = f"/modules.php{href}"
    return urllib.parse.urljoin(BASE_URL + "/", href)


def _subtitle_id_from_url(url):
    parsed = urllib.parse.urlparse(url or "")
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("lid", "id", "subid"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    basename = os.path.basename(parsed.path)
    return basename or hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:12]


def _match_text(pattern, chunk):
    match = pattern.search(chunk or "")
    if not match:
        return ""
    return _strip_tags(match.group("value"))


def _match_multiline_text(pattern, chunk):
    match = pattern.search(chunk or "")
    if not match:
        return ""
    return _strip_tags_multiline(match.group("value"))


def _attrs(raw):
    attrs = {}
    for key, _quote, value in _ATTR_RE.findall(raw or ""):
        attrs[key.lower()] = html.unescape(value)
    return attrs


def _strip_tags(value):
    text = _TAG_RE.sub(" ", value or "")
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _strip_tags_multiline(value):
    """Tag-free text with the line structure intact, like BeautifulSoup get_text()."""
    text = _TAG_RE.sub(" ", _LINE_BREAK_RE.sub("\n", value or ""))
    lines = [_LINE_WS_RE.sub(" ", line).strip() for line in html.unescape(text).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _decode_html(body):
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    for encoding in ("utf-8", "iso-8859-15", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _response_headers(headers):
    if headers is None:
        return {}
    try:
        return {str(key): str(value) for key, value in headers.items()}
    except Exception:  # pragma: no cover, defensive against header shapes
        return {}


def _requested_languages(languages):
    requested = []
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        alpha3 = str(language.get("alpha3") or "").strip().lower()
        country = str(language.get("country_alpha2") or language.get("country") or "").upper()
        if alpha3 == "pob" or alpha3 == "por-br" or (alpha3 == "por" and country == "BR"):
            code = "por-BR"
        elif alpha3 == "por":
            code = "por"
        else:
            continue
        if code not in requested:
            requested.append(code)
    return requested


def _language_payload(code):
    # Brazilian Portuguese is alpha3 "por" plus country_alpha2 "BR". There is no
    # "por-BR" alpha3; inventing one breaks the host's Language construction.
    data = LANGUAGES[code]
    payload = {"alpha3": "por", "alpha2": data["alpha2"], "hi": False, "forced": False}
    if data["country_alpha2"]:
        payload["country_alpha2"] = data["country_alpha2"]
    return payload


def _fps_mismatch(video_fps, subtitle_fps):
    try:
        video_value = float(video_fps)
        subtitle_value = float(subtitle_fps)
    except (TypeError, ValueError):
        return False
    if subtitle_value <= 0:
        return False
    return abs(video_value - subtitle_value) > 0.02


def _sleep(config):
    delay_ms = _safe_int((config or {}).get("request_delay_ms"), 0)
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _is_rar_archive(body):
    return bool(body) and (body.startswith(_RAR4_MAGIC) or body.startswith(_RAR5_MAGIC))


def _looks_like_html(body):
    head = _normalize(_decode_html((body or b"")[:2048]))
    return "doctype html" in head or "html" in head.split()[:6]


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _content_payload(body, subtitle_format):
    # No encoding key. The host runs chardet through Subtitle.normalize(); a worker
    # guess, latin-1 above all, never fails to decode and only produces mojibake.
    subtitle_format = subtitle_format or "srt"
    return {
        "content_b64": base64.b64encode(body).decode("ascii"),
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "empty": False,
    }


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    if subtitle_format in {"sub", "smi", "mpl"}:
        return "text/plain"
    return "application/x-subrip"


def _normalize_line_endings(body):
    return (body or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _season_in_description(description, season):
    wanted = _safe_int(season, None)
    if wanted is None:
        return False
    return any(int(m.group("season")) == wanted for m in _SEASON_ONLY_RE.finditer(description or ""))


def _episode_in_description(description, season, episode):
    wanted_season = _safe_int(season, None)
    wanted_episode = _safe_int(episode, None)
    if wanted_season is None or wanted_episode is None:
        return False
    for match in _SXXEYY_RE.finditer(description or ""):
        if int(match.group("season")) == wanted_season and int(match.group("episode")) == wanted_episode:
            return True
    return False


def _contains_release_value(release_tokens, value):
    wanted = _release_tokens(value)
    return bool(wanted) and wanted.issubset(release_tokens)


def _all_tokens_present(value, candidate_tokens):
    tokens = _tokens(value)
    return bool(tokens) and all(token in candidate_tokens for token in tokens)


def _tokens(value):
    return [token for token in _normalize(value).split() if token]


def _release_tokens(value):
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    return {token for token in re.split(r"[^A-Za-z0-9]+", str(value or "").lower()) if token}


def _normalize_release(value):
    return " ".join(token for token in re.split(r"[^A-Za-z0-9]+", str(value or "").lower()) if token)


def _normalize(value):
    text = str(value or "")
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _slug(value):
    slug = "-".join(_tokens(value))[:80].strip("-")
    return slug or "subtitle"


def _safe_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _unique(values):
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
