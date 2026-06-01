"""NapiProjekt provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
import html
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from html.parser import HTMLParser

try:
    import cloudscraper
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    cloudscraper = None

PROVIDER_ID = "napiprojekt"
HASH_DOWNLOAD_URL = "https://napiprojekt.pl/unit_napisy/dl.php"
CATALOG_SEARCH_URL = "https://www.napiprojekt.pl/ajax/search_catalog.php"
CATALOG_BASE_URL = "https://www.napiprojekt.pl"
HTTP_TIMEOUT_SECONDS = 15
DEFAULT_FLARESOLVERR_TIMEOUT_MS = 25000
MAX_FLARESOLVERR_TIMEOUT_MS = 25000
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
LANGUAGE = {
    "alpha3": "pol",
    "alpha2": "pl",
    "hi": False,
    "forced": False,
}
_MACHINE_AUTHORS = {
    "brak",
    "automat",
    "si",
    "robot",
    "maszynowe",
    "tlumaczenie maszynowe",
    "tłumaczenie maszynowe",
    "chat" + "gpt",
    "a" + "i",
}
_TITLE_RE = re.compile(r"[\W_]+", re.UNICODE)
_IMDB_RE = re.compile(r"imdb\.com/title/(?P<id>tt\d+)", re.I)
_META_REFRESH_RE = re.compile(
    r"""<meta\s+http-equiv=["']refresh["']\s+content=["'](?P<delay>\d+);\s*url=(?P<url>[^"']+)["']""",
    re.I,
)
_ANUBIS_CHALLENGE_RE = re.compile(
    r"""<script\s+id=["']anubis_challenge["'][^>]*>\s*(?P<json>.*?)\s*</script>""",
    re.I | re.S,
)


class CloudflareBlockedError(RuntimeError):
    """Raised when NapiProjekt presents an unresolved Cloudflare challenge."""


class _MissingCloudscraper:
    def create_scraper(self, **kwargs):
        del kwargs
        raise CloudflareBlockedError("napiprojekt ai-cloudscraper dependency is not installed")


if cloudscraper is None:  # pragma: no cover
    cloudscraper = _MissingCloudscraper()


def _create_cloudscraper():
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


def get_subhash(video_hash):
    if not isinstance(video_hash, str) or len(video_hash) < 32:
        raise ValueError("napiprojekt hash must be a 32 character hex string")
    steps = ((0xE, 2, 0), (0x3, 2, 0xD), (0x6, 5, 0x10), (0x8, 4, 0xB), (0x2, 3, 0x5))
    chars = []
    for index, multiplier, offset in steps:
        start = offset + int(video_hash[index], 16)
        value = int(video_hash[start : start + 2], 16)
        chars.append(f"{value * multiplier:x}"[-1])
    return "".join(chars)


def hash_download_url(video_hash, alpha2="pl"):
    params = {
        "v": "dreambox",
        "kolejka": "false",
        "nick": "",
        "pass": "",
        "napios": "Linux",
        "l": (alpha2 or "pl").upper(),
        "f": video_hash,
        "t": get_subhash(video_hash),
    }
    return f"{HASH_DOWNLOAD_URL}?{urllib.parse.urlencode(params)}"


def parse_catalog_search(body):
    parser = _CatalogSearchParser()
    parser.feed(_decode(body))
    return parser.entries


def select_catalog_title(video, entries):
    video = video or {}
    imdb_id = video.get("series_imdb_id") if video.get("kind") == "episode" else video.get("imdb_id")
    for entry in entries:
        if imdb_id and entry.get("imdb_id") == imdb_id:
            matches = ["series_imdb_id", "series", "year"] if video.get("kind") == "episode" else ["imdb_id", "title", "year"]
            return {**entry, "matches": matches}

    for entry in entries:
        title = entry.get("title") or ""
        matches = []
        if video.get("kind") == "episode":
            if _same_title(title, video.get("series")):
                matches.append("series")
        elif _same_title(title, video.get("title")):
            matches.append("title")
        if video.get("year") and str(video.get("year")) in title:
            matches.append("year")
        if matches:
            return {**entry, "matches": matches}
    if entries:
        return {**entries[0], "matches": []}
    return None


def catalog_subtitles_url(slug, video):
    suffix = ""
    if (video or {}).get("kind") == "episode":
        try:
            suffix = f"-s{int(video.get('season')):02d}e{int(video.get('episode')):02d}"
        except (TypeError, ValueError):
            suffix = ""
    return f"{CATALOG_BASE_URL}/napisy1,7,0-dla-{slug}{suffix}"


def parse_subtitle_rows(body, matches, only_authors=False, only_real_names=False):
    parser = _SubtitleRowsParser()
    parser.feed(_decode(body))
    rows = []
    for raw in parser.rows:
        author = raw.get("author") or _extract_label(raw.get("title_attr"), "Autor") or ""
        author = _clean_text(author)
        if only_authors and (not author or _is_machine_author(author)):
            continue
        if only_real_names and not _looks_like_real_name(author):
            continue
        resolution = _extract_label(raw.get("title_attr"), "Video rozdzielczość")
        fps = _extract_label(raw.get("title_attr"), "Video FPS")
        release_info = " | ".join(
            part
            for part in (
                f"Autor: {author}" if author else "Autor:",
                resolution or "",
                fps or "",
                raw.get("size") or "",
                raw.get("added") or "",
                raw.get("length") or "",
            )
            if part
        )
        rows.append(
            {
                "hash": raw["hash"],
                "author": author,
                "resolution": resolution or "",
                "fps": fps or "",
                "size": raw.get("size") or "",
                "length": raw.get("length") or "",
                "added": raw.get("added") or "",
                "release_info": release_info,
                "matches": list(matches or []),
            }
        )
    return rows


def is_cloudflare_challenge(status_code, headers, body):
    headers = {str(key).lower(): str(value).lower() for key, value in (headers or {}).items()}
    sample = _decode(body[:4096] if isinstance(body, bytes) else body).lower()
    return (
        int(status_code or 0) in {403, 503}
        and (
            headers.get("cf-mitigated") == "challenge"
            or "just a moment" in sample
            or "/cdn-cgi/challenge-platform/" in sample
        )
    )


class NapiProjektProvider:
    def __init__(self):
        self._state = {}
        self._content_cache = {}

    def _http_get(self, url, config=None, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        return _cloudflare_request("GET", url, config=config, state=self._state, timeout=timeout, referer=referer)

    def _http_post(self, url, data, config=None, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        return _cloudflare_request("POST", url, data=data, config=config, state=self._state, timeout=timeout, referer=referer)

    def search(self, video, languages, config):
        if "pol" not in {_alpha3_for_language(language) for language in languages or []}:
            return []
        video = video or {}
        config = dict(config or {})
        results = []
        if not (config.get("only_authors") or config.get("only_real_names")):
            hash_result = self._hash_search(video, config)
            if hash_result:
                results.append(hash_result)
        try:
            results.extend(self._catalog_search(video, config))
        except CloudflareBlockedError:
            if not results:
                raise
        return _dedupe_results(results)

    def _hash_search(self, video, config):
        video_hash = ((video or {}).get("hashes") or {}).get("napiprojekt")
        if not video_hash:
            return None
        body = self._http_get(hash_download_url(video_hash, "pl"), config=config)
        if _is_not_found(body):
            return None
        self._content_cache[video_hash] = body
        payload = _payload_for_hash(video_hash, "hash")
        return _candidate(
            payload,
            release_info=f"NapiProjekt hash {video_hash}",
            matches=["hash"],
            score=100,
        )

    def _catalog_search(self, video, config):
        query = video.get("series") if video.get("kind") == "episode" else video.get("title")
        if not query:
            return []
        data = {
            "queryString": str(query),
            "queryKind": "1" if video.get("kind") == "episode" else "2",
            "queryYear": str(video.get("year") or ""),
            "associate": "",
        }
        search_body = self._http_post(CATALOG_SEARCH_URL, data, config=config, referer=CATALOG_BASE_URL + "/")
        selected = select_catalog_title(video, parse_catalog_search(search_body))
        if not selected:
            return []
        page_url = catalog_subtitles_url(selected["slug"], video)
        matches = _catalog_page_matches(video, selected.get("matches"))
        rows = parse_subtitle_rows(
            self._http_get(page_url, config=config, referer=CATALOG_SEARCH_URL),
            matches,
            only_authors=bool(config.get("only_authors")),
            only_real_names=bool(config.get("only_real_names")),
        )
        candidates = []
        for row in rows:
            payload = _payload_for_hash(row["hash"], "catalog")
            candidates.append(
                _candidate(
                    payload,
                    release_info=row["release_info"],
                    matches=row.get("matches"),
                    score=_score_for_matches(row.get("matches")),
                    display={
                        "source": "NapiProjekt",
                        "title": selected.get("title"),
                        "release": row["release_info"],
                        "author": row.get("author"),
                    },
                )
            )
        return candidates

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        video_hash = payload.get("hash")
        if not video_hash:
            raise ValueError("napiprojekt download requires hash")
        body = self._content_cache.get(video_hash)
        if body is None:
            body = self._http_get(hash_download_url(video_hash, "pl"), config=config or {})
        if _is_not_found(body):
            raise ValueError("napiprojekt returned no subtitle for hash")
        return _content_payload(body, payload.get("format") or "txt")


class _CatalogSearchParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self._entry = None
        self._link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if tag == "div" and "greyBoxCatcher" in classes:
            self._entry = {}
        if self._entry is not None and tag == "a":
            self._link = {"href": attrs.get("href") or "", "classes": classes, "text": []}

    def handle_data(self, data):
        if self._link is not None:
            self._link["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._entry is not None and self._link is not None:
            href = self._link["href"]
            imdb_match = _IMDB_RE.search(href)
            if imdb_match:
                self._entry["imdb_id"] = imdb_match.group("id")
            if "movieTitleCat" in self._link["classes"]:
                self._entry["title"] = _clean_text("".join(self._link["text"]))
                self._entry["slug"] = href[len("napisy-") :] if href.startswith("napisy-") else href
            self._link = None
        if tag == "div" and self._entry is not None:
            if self._entry.get("slug"):
                self.entries.append(self._entry)
            self._entry = None


class _SubtitleRowsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._p = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "title" in attrs:
            self._row = {"title_attr": attrs.get("title") or "", "paragraphs": []}
        if self._row is not None and tag == "a":
            classes = set((attrs.get("class") or "").split())
            href = attrs.get("href") or ""
            if "tableA" in classes and href.startswith("napiprojekt:"):
                self._row["hash"] = href[len("napiprojekt:") :]
        if self._row is not None and tag == "p":
            self._p = []

    def handle_data(self, data):
        if self._p is not None:
            self._p.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self._row is not None and self._p is not None:
            self._row["paragraphs"].append(_clean_text("".join(self._p)))
            self._p = None
        if tag == "tr" and self._row is not None:
            paragraphs = self._row.get("paragraphs") or []
            self._row["size"] = paragraphs[1] if len(paragraphs) > 1 else ""
            self._row["length"] = paragraphs[3] if len(paragraphs) > 3 else ""
            self._row["author"] = paragraphs[4] if len(paragraphs) > 4 else ""
            self._row["added"] = paragraphs[5] if len(paragraphs) > 5 else ""
            if self._row.get("hash"):
                self.rows.append(self._row)
            self._row = None


def _cloudflare_request(method, url, data=None, config=None, state=None, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
    config = config or {}
    scraper = _get_cloudscraper(state)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "pl,en-US;q=0.9,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    if method == "POST":
        response = scraper.post(url, data=data or {}, headers=headers, timeout=timeout)
    else:
        response = scraper.get(url, headers=headers, timeout=timeout)
    body = response.content
    if is_anubis_challenge(getattr(response, "url", ""), getattr(response, "status_code", 0)):
        solved = solve_anubis_challenge(scraper, response.url, url, timeout=timeout)
        if not solved:
            raise CloudflareBlockedError("napiprojekt Anubis challenge could not be solved")
        if method == "POST":
            response = scraper.post(url, data=data or {}, headers=headers, timeout=timeout)
        else:
            response = scraper.get(url, headers=headers, timeout=timeout)
        body = response.content
    if is_cloudflare_challenge(response.status_code, response.headers, body):
        if _flaresolverr_url(config):
            return _flaresolverr_request(method, url, data=data, config=config, state=state, referer=referer)
        raise CloudflareBlockedError("napiprojekt hit a Cloudflare challenge and no FlareSolverr URL is configured")
    response.raise_for_status()
    return body


def _get_cloudscraper(state):
    state = state if isinstance(state, dict) else {}
    scraper = state.get("cloudscraper")
    if scraper is None:
        scraper = _create_cloudscraper()
        state["cloudscraper"] = scraper
    return scraper


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
        digest = _hashlib.sha256(f"{random_data}{nonce}".encode("utf-8")).hexdigest()
        if digest.startswith(prefix):
            return nonce, digest
        nonce += 1


def _solve_preact(random_data, difficulty):
    return _hashlib.sha256(random_data.encode("utf-8")).hexdigest(), difficulty * 0.125


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


def _flaresolverr_request(method, url, data=None, config=None, state=None, referer=None):
    del state
    payload = {
        "cmd": "request.post" if method == "POST" else "request.get",
        "url": url,
        "maxTimeout": _flaresolverr_timeout_ms(config),
    }
    headers = {}
    if referer:
        headers["Referer"] = referer
    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        payload["postData"] = urllib.parse.urlencode(data or {})
    if headers:
        payload["headers"] = headers
    request = urllib.request.Request(
        _flaresolverr_url(config),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=max(HTTP_TIMEOUT_SECONDS, payload["maxTimeout"] / 1000.0 + 5)) as response:
        response_body = response.read()
    try:
        parsed = json.loads(response_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CloudflareBlockedError("napiprojekt FlareSolverr returned invalid JSON") from exc
    if parsed.get("status") != "ok":
        raise CloudflareBlockedError(f"napiprojekt {parsed.get('message') or 'FlareSolverr request failed'}")
    solution = parsed.get("solution") or {}
    body = (solution.get("response") or "").encode("utf-8")
    if not body:
        raise CloudflareBlockedError("napiprojekt FlareSolverr response had no page body")
    if is_cloudflare_challenge(403, {}, body):
        raise CloudflareBlockedError("napiprojekt FlareSolverr response is still a Cloudflare challenge")
    return body


def _flaresolverr_url(config):
    return str((config or {}).get("flaresolverr_url") or "").strip()


def _flaresolverr_timeout_ms(config):
    try:
        timeout = int((config or {}).get("flaresolverr_timeout_ms") or DEFAULT_FLARESOLVERR_TIMEOUT_MS)
    except (TypeError, ValueError):
        timeout = DEFAULT_FLARESOLVERR_TIMEOUT_MS
    return min(max(timeout, 5000), MAX_FLARESOLVERR_TIMEOUT_MS)


def _candidate(payload, release_info, matches, score, display=None):
    video_hash = payload["hash"]
    return {
        "provider": PROVIDER_ID,
        "id": _stable_id(video_hash, payload.get("source")),
        "language": dict(LANGUAGE),
        "release_info": release_info,
        "filename": f"{video_hash}.txt",
        "matches": list(matches or []),
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": payload.get("source") == "hash",
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": hash_download_url(video_hash, "pl"),
        "display": display or {"source": "NapiProjekt", "title": release_info, "release": release_info},
        "provider_payload": payload,
    }


def _payload_for_hash(video_hash, source):
    return {
        "provider": PROVIDER_ID,
        "schema": 1,
        "source": source,
        "hash": video_hash,
        "language": "pol",
        "format": "txt",
    }


def _content_payload(body, fmt):
    if not body:
        return {
            "content_b64": "",
            "content_sha256": "",
            "content_type": "text/plain",
            "format": fmt,
            "encoding": "cp1250",
            "empty": True,
        }
    encoding = _detect_subtitle_encoding(body)
    return {
        "content_b64": _base64.b64encode(body).decode("ascii"),
        "content_sha256": _hashlib.sha256(body).hexdigest(),
        "content_type": "text/plain",
        "format": fmt,
        "encoding": encoding,
        "empty": False,
    }


def _score_for_matches(matches):
    match_set = set(matches or [])
    if "hash" in match_set:
        return 100
    if "imdb_id" in match_set or "series_imdb_id" in match_set:
        return 95
    if {"season", "episode"}.issubset(match_set):
        return 92
    if "title" in match_set or "series" in match_set:
        return 88
    return 70


def _catalog_page_matches(video, matches):
    match_list = list(matches or [])
    if (video or {}).get("kind") != "episode":
        return match_list
    try:
        int(video.get("season"))
        int(video.get("episode"))
    except (TypeError, ValueError):
        return match_list
    for item in ("season", "episode"):
        if item not in match_list:
            match_list.append(item)
    return match_list


def _detect_subtitle_encoding(body):
    body = bytes(body or b"")
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1250"
    return "utf-8"


def _dedupe_results(results):
    deduped = []
    seen = set()
    for item in results:
        video_hash = item.get("provider_payload", {}).get("hash")
        if not video_hash or video_hash in seen:
            continue
        seen.add(video_hash)
        deduped.append(item)
    deduped.sort(key=lambda item: item["score"], reverse=True)
    return deduped


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    if alpha3:
        return alpha3
    return "pol" if (language.get("alpha2") or "").lower() == "pl" else None


def _is_not_found(body):
    return bytes(body or b"")[:4] == b"NPc0"


def _extract_label(value, label):
    value = html.unescape(value or "")
    pattern = re.compile(rf"<b>\s*{re.escape(label)}\s*:\s*</b>\s*(?P<value>.*?)(?:<|\(|$)", re.I | re.S)
    match = pattern.search(value)
    return _clean_text(match.group("value")) if match else ""


def _is_machine_author(author):
    normalized = _normalize(author)
    return normalized in _MACHINE_AUTHORS


def _looks_like_real_name(author):
    author = _clean_text(author)
    if re.match(r"^[^\W\d_]+(?:[-'][^\W\d_]+)?\s+[^\W\d_]+", author, re.UNICODE):
        return True
    return len(re.findall(r"[A-ZĄĆĘŁŃÓŚŹŻ]", author)) >= 2 and re.search(r"[a-ząćęłńóśźż]", author) is not None


def _same_title(left, right):
    return _normalize_title(left) == _normalize_title(right)


def _normalize_title(value):
    value = re.sub(r"\(\d{4}\)", "", str(value or ""))
    return _normalize(value)


def _normalize(value):
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _TITLE_RE.sub(" ", folded.lower()).strip()


def _clean_text(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _decode(body):
    if isinstance(body, str):
        return body
    for encoding in ("utf-8", "cp1250", "latin-1"):
        try:
            return bytes(body or b"").decode(encoding)
        except UnicodeDecodeError:
            continue
    return bytes(body or b"").decode("utf-8", errors="replace")


def _stable_id(video_hash, source):
    digest = _hashlib.sha1(f"{video_hash}:{source}".encode("utf-8")).hexdigest()[:16]
    return f"napiprojekt-{digest}"
