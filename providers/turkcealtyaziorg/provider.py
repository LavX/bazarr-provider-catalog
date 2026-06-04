"""TurkceAltyazi.org provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from http.cookies import SimpleCookie

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

try:
    import py7zz
except ImportError:
    py7zz = None

PROVIDER_ID = "turkcealtyaziorg"
BASE_URL = "https://turkcealtyazi.org"
DOWNLOAD_URL = f"{BASE_URL}/ind"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT_SECONDS = 30
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 60000
MAX_FLARESOLVERR_TIMEOUT_MS = 60000
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
SUPPORTED_LANGUAGE_CODES = {"tur", "eng"}
_SXXEXX_RE = re.compile(r"\bs(?P<season>\d{1,2})\s*[._ -]?e(?P<episode>\d{1,3})\b", re.I)
_XX_RE = re.compile(r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b", re.I)
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)

CLASS_MAP = {
    "cps c1": "DVDRip",
    "cps c2": "HDRip",
    "cps c3": "TVRip",
    "rps r1": "HD",
    "rps r2": "DVDRip",
    "rps r3": "DVDScr",
    "rps r4": "R5",
    "rps r5": "CAM",
    "rps r6": "WEBRip",
    "rps r7": "BDRip",
    "rps r8": "WEB-DL",
    "rps r9": "HDRip",
    "rps r10": "HDTS",
    "rps r12": "BluRay",
    "rip1": "DVDRip",
    "rip2": "DVDScr",
    "rip3": "WEBRip",
    "rip4": "BDRip",
    "rip5": "BRRip",
    "rip6": "CAM",
    "rip7": "HD",
    "rip8": "R5",
    "rip9": "WEB-DL",
    "rip10": "HDRip",
    "rip11": "HDTS",
}
LANGUAGE_CLASSES = {
    "flagtr": "tur",
    "flagen": "eng",
    "flages": "spa",
    "flagfr": "fra",
    "flagger": "deu",
    "flagita": "ita",
}
ALPHA3_TO_ALPHA2 = {
    "tur": "tr",
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "deu": "de",
    "ita": "it",
}


class HttpResponse:
    def __init__(self, status, body, headers, url=""):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})
        self.url = url


class _MissingCloudscraper:
    @staticmethod
    def create_scraper(**kwargs):
        raise RuntimeError("TurkceAltyazi.org ai-cloudscraper dependency is not installed")


if cloudscraper is None:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = _MissingCloudscraper()


def _create_cloudscraper_session():
    kwargs = {
        "browser": {"custom": DEFAULT_USER_AGENT},
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


class TurkceAltyaziOrgProvider:
    def __init__(self):
        self._access_checked = False
        self._last_request_at = 0.0
        self._session = None

    def search(self, video, languages, config):
        video = video or {}
        imdb_id = _video_imdb_id(video)
        if not imdb_id:
            return []
        requested = _requested_languages(languages)
        if not requested:
            return []
        config = dict(config or {})
        cookies = _parse_cookies(config)
        self._ensure_access(config, cookies)
        search_url = f"{BASE_URL}/find.php?{urllib.parse.urlencode({'cat': 'sub', 'find': imdb_id})}"
        response = self._http_get(search_url, self._headers(config), cookies, timeout=HTTP_TIMEOUT_SECONDS, config=config)
        _raise_for_status(response, "TurkceAltyazi search")
        if _is_not_found(response.body):
            return []
        entries = parse_search_page(response.body, video)
        results = []
        for entry in entries:
            if entry["language"] not in requested:
                continue
            if video.get("kind") == "episode" and not _episode_entry_matches(entry, video):
                continue
            results.append(_candidate(entry, video))
        return results

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        page_url = payload.get("page_url")
        if not page_url:
            raise ValueError("turkcealtyaziorg download requires page_url")
        config = dict(config or {})
        cookies = _parse_cookies(config)
        headers = self._headers(config)
        page_response = self._http_get(page_url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, config=config)
        _raise_for_status(page_response, "TurkceAltyazi download page")
        form = parse_download_form(page_response.body)
        post_headers = dict(headers)
        post_headers["Referer"] = page_url
        archive_response = self._http_post(DOWNLOAD_URL, form, post_headers, cookies, timeout=10, config=config)
        _raise_for_status(archive_response, "TurkceAltyazi archive download")
        body, filename = extract_download(
            archive_response.body,
            payload.get("filename") or page_url,
            payload,
        )
        body = _normalize_line_endings(body)
        return _content_payload(body, _format_from_filename(filename))

    def _ensure_access(self, config, cookies):
        if self._access_checked:
            return
        response = self._http_get(
            BASE_URL,
            self._headers(config),
            cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
            config=config,
        )
        _raise_for_status(response, "TurkceAltyazi access check")
        self._access_checked = True

    def _headers(self, config):
        session_user_agent = ""
        if self._session is not None:
            session_user_agent = str(self._session.headers.get("User-Agent") or "").strip()
        user_agent = str((config or {}).get("user_agent") or "").strip() or session_user_agent or DEFAULT_USER_AGENT
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE_URL,
            "User-Agent": user_agent,
        }

    def _get_session(self):
        if self._session is None:
            self._session = _create_cloudscraper_session()
            self._session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
        return self._session

    def _http_get(self, url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True, config=None):
        return self._http_request(
            "GET",
            url,
            headers,
            cookies,
            timeout=timeout,
            allow_redirects=allow_redirects,
            config=config,
        )

    def _http_post(self, url, data, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, config=None):
        return self._http_request("POST", url, headers, cookies, data=data, timeout=timeout, config=config)

    def _http_request(
        self,
        method,
        url,
        headers,
        cookies,
        data=None,
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=True,
        config=None,
    ):
        self._apply_delay(config)
        response = self._send(method, url, headers, cookies, data, timeout, allow_redirects)
        if is_anubis_challenge(response.url, response.status) or _has_anubis_challenge_body(response.body):
            solved = solve_anubis_challenge(self._get_session(), response.url, url, timeout=timeout)
            if not solved:
                raise PermissionError("TurkceAltyazi Anubis challenge could not be solved")
            response = self._send(method, url, headers, cookies, data, timeout, allow_redirects)
        if _is_cloudflare_challenge(response) and _flaresolverr_url(config):
            solved_names = self._fallback_to_flaresolverr(url, config)
            retry_headers = dict(headers or {})
            retry_headers["User-Agent"] = self._get_session().headers.get("User-Agent", DEFAULT_USER_AGENT)
            # Drop any per-request cookies FlareSolverr just refreshed so the
            # stale configured values cannot mask the freshly solved session
            # cookies during the retry.
            retry_cookies = {
                name: value
                for name, value in (cookies or {}).items()
                if name not in solved_names
            }
            response = self._send(method, url, retry_headers, retry_cookies or None, data, timeout, allow_redirects)
        return response

    def _send(self, method, url, headers, cookies, data, timeout, allow_redirects):
        session = self._get_session()
        request_headers = dict(headers or {})
        request_cookies = cookies or None
        try:
            if method == "POST":
                response = session.post(
                    url,
                    data=data,
                    headers=request_headers,
                    cookies=request_cookies,
                    timeout=timeout,
                    allow_redirects=allow_redirects,
                )
            else:
                response = session.get(
                    url,
                    headers=request_headers,
                    cookies=request_cookies,
                    timeout=timeout,
                    allow_redirects=allow_redirects,
                )
        except Exception as exc:
            raise RuntimeError(f"TurkceAltyazi request failed: {exc}") from exc
        return _session_response(response)

    def _apply_delay(self, config):
        delay_ms = _config_int((config or {}).get("request_delay_ms")) or 0
        if delay_ms <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait_for = delay_ms / 1000 - elapsed
        if wait_for > 0:
            time.sleep(wait_for)
        self._last_request_at = time.monotonic()

    def _fallback_to_flaresolverr(self, url, config):
        endpoint = _flaresolverr_url(config)
        if not endpoint:
            return set()
        max_timeout = _flaresolverr_timeout_ms(config)
        payload = {"cmd": "request.get", "url": url, "maxTimeout": max_timeout}
        data = self._post_flaresolverr(endpoint, payload, timeout=max(max_timeout / 1000 + 5, 15))
        solution = data.get("solution") or {}
        return self._inject_solution(solution)

    def _post_flaresolverr(self, url, payload, timeout):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response_body = response.read()
        except urllib.error.URLError as exc:
            raise PermissionError(f"TurkceAltyazi FlareSolverr request failed: {exc.reason}") from exc
        try:
            data = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PermissionError("TurkceAltyazi FlareSolverr returned invalid JSON") from exc
        if data.get("status") == "error":
            raise PermissionError(f"TurkceAltyazi FlareSolverr error: {data.get('message') or 'unknown error'}")
        return data

    def _inject_solution(self, solution):
        session = self._get_session()
        user_agent = solution.get("userAgent")
        if user_agent:
            session.headers["User-Agent"] = user_agent
        injected = set()
        for cookie in solution.get("cookies") or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value is None:
                continue
            session.cookies.set(
                name,
                value,
                domain=cookie.get("domain") or ".turkcealtyazi.org",
                path=cookie.get("path") or "/",
            )
            injected.add(name)
        return injected


def _parse_cookies(config):
    value = str((config or {}).get("cookies") or "").strip()
    if not value:
        return {}
    cookie = SimpleCookie()
    cookie.load(value)
    return {key: morsel.value for key, morsel in cookie.items()}


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


def _session_response(response):
    body = getattr(response, "content", b"")
    if isinstance(body, str):
        body = body.encode("utf-8")
    headers = dict(getattr(response, "headers", {}) or {})
    return HttpResponse(getattr(response, "status_code", 200), body, headers, getattr(response, "url", ""))


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config):
    configured = _config_int((config or {}).get("flaresolverr_timeout_ms")) or DEFAULT_FLARESOLVERR_TIMEOUT_MS
    return max(1000, min(configured, MAX_FLARESOLVERR_TIMEOUT_MS))


def _config_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _requested_languages(languages):
    return {
        str(item.get("alpha3")).lower()
        for item in languages or []
        if isinstance(item, dict) and str(item.get("alpha3")).lower() in SUPPORTED_LANGUAGE_CODES
    }


def _video_imdb_id(video):
    value = video.get("series_imdb_id") if video.get("kind") == "episode" else video.get("imdb_id")
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text.startswith("tt"):
        text = text[2:]
    return text if text.isdigit() else None


def _raise_for_status(response, context):
    if _is_cloudflare_challenge(response):
        raise PermissionError(
            "TurkceAltyazi is presenting a Cloudflare challenge; configure FlareSolverr URL or matching cookies and User-Agent"
        )
    if response.status >= 400:
        raise RuntimeError(f"{context} failed with HTTP {response.status}")


def _is_cloudflare_challenge(response):
    headers = {str(key).lower(): str(value).lower() for key, value in (response.headers or {}).items()}
    body = (response.body or b"").decode("utf-8", "ignore").lower()
    if headers.get("cf-mitigated") == "challenge":
        return True
    markers = ("just a moment", "challenges.cloudflare.com", "challenge-platform", "_cf_chl_opt")
    return response.status in {403, 429, 503} and any(marker in body for marker in markers)


def _is_not_found(body):
    root = _parse_html(body)
    for meta in root.descendants("meta"):
        if meta.attrs.get("name") == "description" and "404 Error" in meta.attrs.get("content", ""):
            return True
    return False


def parse_search_page(body, video):
    root = _parse_html(body)
    entries = []
    seen = set()
    kind = (video or {}).get("kind")
    for node in root.descendants("div"):
        if not _looks_like_search_entry(node):
            continue
        if any(_looks_like_search_entry(child) for child in node.children if child.tag == "div"):
            continue
        classes = set(node.classes())
        if kind == "episode":
            season = int((video or {}).get("season") or 0)
            if "altsonsez1" in classes and f"sezon_{season}" not in classes:
                continue
        elif "altsonsez1" in classes:
            continue
        entry = _parse_entry(node, video)
        if entry is None or entry["page_url"] in seen:
            continue
        seen.add(entry["page_url"])
        entries.append(entry)
    return [entry for entry in entries if entry is not None]


def _looks_like_search_entry(node):
    return (
        node.first_descendant("div", {"alisim"}) is not None
        and node.first_descendant("div", {"aldil"}) is not None
        and node.first_descendant("div", {"ripdiv"}) is not None
    )


def _parse_entry(node, video):
    page_link = urllib.parse.urljoin(BASE_URL, node.first_link() or "")
    language = _entry_language(node)
    if not page_link or not language:
        return None
    season = None
    episode = None
    is_pack = False
    if (video or {}).get("kind") == "episode":
        alcd = node.first_descendant("div", {"alcd"})
        values = [item.text() for item in alcd.descendants("b")] if alcd else []
        if len(values) >= 2:
            season = _int_or_none(values[0])
            episode = _int_or_none(values[1])
            if episode is None:
                is_pack = True
                episode = _int_or_none((video or {}).get("episode"))
    ripdiv = node.first_descendant("div", {"ripdiv"})
    release_info = _release_info(ripdiv)
    uploader = _uploader(node)
    return {
        "page_url": page_link,
        "language": language,
        "release_info": release_info,
        "uploader": uploader,
        "hearing_impaired": bool(ripdiv and ripdiv.first_descendant("img", attrs={"src": "/images/isitme.png"})),
        "season": season,
        "episode": episode,
        "is_pack": is_pack,
    }


def _entry_language(node):
    aldil = node.first_descendant("div", {"aldil"})
    span = aldil.first_descendant("span") if aldil else None
    if span is None:
        return None
    for class_name in span.classes():
        if class_name in LANGUAGE_CLASSES:
            return LANGUAGE_CLASSES[class_name]
    return None


def _release_info(ripdiv):
    if ripdiv is None:
        return ""
    values = []
    for span in ripdiv.descendants("span"):
        mapped = CLASS_MAP.get(" ".join(span.classes()))
        if mapped:
            values.append(mapped)
    values.extend(item.strip() for item in ripdiv.own_text().split("/") if item.strip())
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return ",".join(seen)


def _uploader(node):
    container = node.first_descendant("div", {"alcevirmen"})
    if container is None:
        return None
    text = container.text().strip()
    if text:
        return text
    span = container.first_descendant("span")
    if span is not None:
        return CLASS_MAP.get(" ".join(span.classes()))
    return None


def _episode_entry_matches(entry, video):
    season = _int_or_none(video.get("season"))
    episode = _int_or_none(video.get("episode"))
    return entry.get("season") == season and (entry.get("is_pack") or entry.get("episode") == episode)


def _candidate(entry, video):
    matches = ["series", "season", "series_imdb_id"] if video.get("kind") == "episode" else ["imdb_id"]
    if video.get("kind") == "episode" and not entry.get("is_pack"):
        matches.append("episode")
    release_group = str(video.get("release_group") or "").lower()
    if release_group and release_group in entry["release_info"].lower():
        matches.append("release_group")
    score = min(100, 20 * len(matches))
    filename = entry["page_url"].rstrip("/").split("/")[-1] or "turkcealtyaziorg.zip"
    if not filename.lower().endswith((".zip", ".rar", ".srt")):
        filename = f"{filename}.zip"
    return {
        "provider": PROVIDER_ID,
        "id": _result_id(entry),
        "language": {
            "alpha3": entry["language"],
            "alpha2": ALPHA3_TO_ALPHA2.get(entry["language"], ""),
            "hi": bool(entry["hearing_impaired"]),
            "forced": False,
        },
        "release_info": entry["release_info"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": bool(entry["hearing_impaired"]),
        "page_link": entry["page_url"],
        "display": {
            "source": "turkcealtyazi.org",
            "release": entry["release_info"],
            "uploader": entry["uploader"],
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "page_url": entry["page_url"],
            "release_info": entry["release_info"],
            "filename": filename,
            "season": entry.get("season"),
            "episode": entry.get("episode"),
            "is_pack": bool(entry.get("is_pack")),
        },
    }


def _result_id(entry):
    value = entry["page_url"]
    if entry.get("season") is not None and entry.get("episode") is not None:
        value += f"-s{int(entry['season']):02d}e{int(entry['episode']):02d}"
    return value


def parse_download_form(body):
    root = _parse_html(body)
    values = {}
    for item in root.descendants("input"):
        name = item.attrs.get("name")
        if name in {"idid", "altid", "sidid"}:
            values[name] = item.attrs.get("value", "")
    missing = {"idid", "altid", "sidid"} - set(values)
    if missing:
        raise RuntimeError("TurkceAltyazi download page did not include required form fields")
    return {"idid": values["idid"], "altid": values["altid"], "sidid": values["sidid"]}


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.data = []

    def append(self, child):
        self.children.append(child)

    def classes(self):
        return str(self.attrs.get("class") or "").split()

    def text(self):
        parts = list(self.data)
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def own_text(self):
        return " ".join(" ".join(self.data).split())

    def first_descendant(self, tag, classes=None, attrs=None):
        classes = set(classes or [])
        attrs = dict(attrs or {})
        for child in self.children:
            if child.tag == tag and classes.issubset(set(child.classes())) and _attrs_match(child, attrs):
                return child
            found = child.first_descendant(tag, classes, attrs)
            if found is not None:
                return found
        return None

    def descendants(self, tag):
        found = []
        for child in self.children:
            if child.tag == tag:
                found.append(child)
            found.extend(child.descendants(tag))
        return found

    def first_link(self):
        link = self.first_descendant("a")
        return link.attrs.get("href") if link is not None else None


def _attrs_match(node, attrs):
    for key, value in attrs.items():
        if node.attrs.get(key) != value:
            return False
    return True


class _TreeBuilder(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), attrs)
        self.stack[-1].append(node)
        if tag.lower() not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        wanted = tag.lower()
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node.tag == wanted:
                return

    def handle_data(self, data):
        text = html.unescape(data).strip()
        if text:
            self.stack[-1].data.append(text)


def _parse_html(body):
    parser = _TreeBuilder()
    parser.feed((body or b"").decode("utf-8", "ignore") if isinstance(body, bytes) else str(body or ""))
    return parser.root


def extract_download(body, filename, payload=None):
    payload = payload or {}
    stream = io.BytesIO(body or b"")
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = [name for name in archive.namelist() if _subtitle_extension(name)]
            if not names:
                raise ValueError("turkcealtyaziorg archive contains no supported subtitle files")
            name = _best_archive_member(names, payload)
            return archive.read(name), name
    if _looks_like_rar(body, filename):
        files = _extract_rar_files(body)
        if not files:
            raise ValueError("turkcealtyaziorg RAR archive contains no supported subtitle files")
        name = _best_archive_member([name for name, _content in files], payload)
        for file_name, content in files:
            if file_name == name:
                return content, file_name
    if not body:
        raise ValueError("turkcealtyaziorg downloaded empty subtitle")
    return body, filename


def _best_archive_member(names, payload):
    episode = _int_or_none(payload.get("episode"))
    if episode is not None:
        season = _int_or_none(payload.get("season"))
        for name in names:
            if _archive_member_matches_episode(name, season, episode):
                return name
        if payload.get("is_pack"):
            raise ValueError("turkcealtyaziorg archive does not contain the requested episode")
    names.sort(key=lambda name: (not name.lower().endswith(".srt"), len(name), name.lower()))
    return names[0]


def _archive_member_matches_episode(name, season, episode):
    lowered = str(name or "").lower()
    saw_structured_episode = False
    for pattern in (_SXXEXX_RE, _XX_RE):
        for match in pattern.finditer(lowered):
            saw_structured_episode = True
            if int(match.group("episode")) != episode:
                continue
            return season is None or int(match.group("season")) == season
    if saw_structured_episode:
        return False
    return bool(re.search(rf"(?<![a-z0-9])e0*{episode}(?!\d)", lowered))


def _subtitle_extension(name):
    lower = str(name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower.endswith(extension):
            return extension.lstrip(".")
    return None


def _looks_like_rar(body, filename):
    lower = str(filename or "").lower()
    return lower.endswith(".rar") or (body or b"").startswith(b"Rar!\x1a\x07")


def _extract_rar_files(body):
    if py7zz is not None:
        try:
            return _extract_rar_files_with_py7zz(body)
        except Exception:
            pass
    if shutil.which("unar"):
        try:
            return _extract_rar_files_with_unar(body)
        except Exception:
            pass
    if shutil.which("7z") or shutil.which("7zz"):
        try:
            return _extract_rar_files_with_7z(body)
        except Exception:
            pass
    raise RuntimeError("TurkceAltyazi RAR extraction requires py7zz, unar, or 7z")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("TurkceAltyazi bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "turkcealtyazi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("TurkceAltyazi RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "turkcealtyazi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("unar failed to extract TurkceAltyazi RAR")
        return _collect_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("TurkceAltyazi RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "turkcealtyazi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", f"-o{output_dir}", "-y", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("7z failed to extract TurkceAltyazi RAR")
        return _collect_subtitle_files(output_dir)


def _collect_subtitle_files(directory):
    files = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            if not _subtitle_extension(name):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directory)
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    return files


def _format_from_filename(filename):
    return _subtitle_extension(urllib.parse.urlparse(str(filename)).path) or "srt"


def _normalize_line_endings(body):
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


# Turkish subtitles are usually saved as UTF-8, but legacy releases still ship
# as Windows-1254 or ISO-8859-9. latin-1 decodes every byte without error, so it
# must come last or Turkish characters are returned as mojibake (for example the
# cp1254 byte for "s" with cedilla would surface as "thorn").
_SUBTITLE_ENCODINGS = ("utf-8", "cp1254", "iso-8859-9", "latin-1")


def _detect_encoding(body):
    for encoding in _SUBTITLE_ENCODINGS:
        try:
            body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return encoding
    return "latin-1"


def _content_payload(body, fmt):
    if not body:
        raise ValueError("turkcealtyaziorg downloaded empty subtitle")
    encoding = _detect_encoding(body)
    return {
        "content_b64": base64.b64encode(body).decode("ascii"),
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(fmt):
    if fmt in {"ass", "ssa"}:
        return "text/x-ssa"
    if fmt == "vtt":
        return "text/vtt"
    if fmt == "sub":
        return "text/plain"
    return "application/x-subrip"
