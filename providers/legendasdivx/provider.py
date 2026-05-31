"""LegendasDivx provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookies import SimpleCookie

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in the manifest
    py7zz = None

PROVIDER_ID = "legendasdivx"
BASE_URL = "https://www.legendasdivx.pt"
LOGIN_URL = f"{BASE_URL}/forum/ucp.php?mode=login"
SEARCH_URL = f"{BASE_URL}/modules.php"
HTTP_TIMEOUT_SECONDS = 30
SAFE_SEARCH_LIMIT = 145
MAX_PAGES = 6
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

LANGUAGES = {
    "por": {"alpha2": "pt", "filter_id": "28"},
    "por-BR": {"alpha2": "pt", "country": "BR", "filter_id": "29"},
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_INPUT_RE = re.compile(r"<input\b(?P<attrs>[^>]*)>", re.I | re.S)
_ATTR_RE = re.compile(r"([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", re.S)
_SUB_BOX_SPLIT_RE = re.compile(r"<div\b[^>]*class=['\"][^'\"]*\bsub_box\b[^'\"]*['\"][^>]*>", re.I)
_HITS_RE = re.compile(r"<th[^>]*>\s*Hits:\s*</th>\s*<td[^>]*>(?P<value>.*?)</td>", re.I | re.S)
_FPS_RE = re.compile(r"<th[^>]*>\s*Frame\s*Rate:\s*</th>\s*<td[^>]*>(?P<value>.*?)</td>", re.I | re.S)
_DESC_RE = re.compile(
    r"<td\b[^>]*class=['\"][^'\"]*\btd_desc\b[^'\"]*\bbrd_up\b[^'\"]*['\"][^>]*>(?P<value>.*?)</td>",
    re.I | re.S,
)
_DOWNLOAD_RE = re.compile(
    r"<a\b(?=[^>]*class=['\"][^'\"]*\bsub_download\b)(?=[^>]*href=['\"](?P<href>[^'\"]+)['\"])[^>]*>",
    re.I | re.S,
)
_HEADER_RE = re.compile(r"<div\b[^>]*class=['\"][^'\"]*\bsub_header\b[^'\"]*['\"][^>]*>(?P<body>.*?)</div>", re.I | re.S)
_ANCHOR_TEXT_RE = re.compile(r"<a\b[^>]*>(?P<body>.*?)</a>", re.I | re.S)
_SEARCH_COUNT_RE = re.compile(r"<!--\s*pesquisas:\s*(?P<count>\d+)\s*-->", re.I)
_PAGER_RE = re.compile(r"\((?P<count>\d+)\s+encontradas\)", re.I)
_SXXEYY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\s*e0*(?P<episode>\d{1,3})\b", re.I)
_SEASON_ONLY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\b", re.I)
_RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
_RAR4_MAGIC = b"Rar!\x1a\x07\x00"


class HttpResponse:
    def __init__(self, status, body, headers):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})


class LegendasDivxProvider:
    def __init__(self):
        self._authenticated = False
        self._cookies = {}

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
            for search_url in build_search_urls(video, language_code):
                response = self._get_search_response(search_url, config)
                _assert_search_available(response)
                page_rows = parse_search_results(response.body)
                page_rows.extend(self._load_more_pages(search_url, response.body, config))
                for item in page_rows:
                    if item["language"] != language_code:
                        continue
                    if skip_wrong_fps and _fps_mismatch(video.get("fps"), item.get("frame_rate")):
                        continue
                    key = (item["lid"], item["language"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(_candidate(video, item))
                if results:
                    break
        return sorted(results, key=lambda item: (-item["score"], -int(item["display"].get("hits", 0))))

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        page_link = payload.get("page_link")
        if not page_link:
            raise ValueError("legendasdivx download requires page_link")
        config = dict(config or {})
        self._ensure_authenticated(config)
        _sleep(config)
        response = self._http_get(
            page_link,
            self._headers(config, referer=BASE_URL),
            self._cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        _raise_for_status(response, "LegendasDivx download")
        text = _normalize(_decode_html(response.body))
        if "limite de downloads" in text and "atingido" in text:
            raise RuntimeError("LegendasDivx daily download limit reached")
        return extract_download(response.body, payload.get("filename", ""), payload)

    def _load_more_pages(self, search_url, first_body, config):
        total = _page_count(first_body)
        if total <= 1:
            return []
        rows = []
        for page in range(2, min(total, MAX_PAGES) + 1):
            _sleep(config)
            response = self._http_get(
                f"{search_url}&page={page}",
                self._headers(config, referer=search_url),
                self._cookies,
                timeout=HTTP_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if response.status == 302:
                self._authenticated = False
                self._ensure_authenticated(config)
                response = self._http_get(
                    f"{search_url}&page={page}",
                    self._headers(config, referer=search_url),
                    self._cookies,
                    timeout=HTTP_TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            _raise_for_status(response, "LegendasDivx search page")
            _assert_search_available(response)
            rows.extend(parse_search_results(response.body))
        return rows

    def _get_search_response(self, search_url, config):
        _sleep(config)
        response = self._http_get(
            search_url,
            self._headers(config, referer=f"{BASE_URL}/index.php"),
            self._cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if response.status == 302:
            self._authenticated = False
            self._ensure_authenticated(config)
            _sleep(config)
            response = self._http_get(
                search_url,
                self._headers(config, referer=f"{BASE_URL}/index.php"),
                self._cookies,
                timeout=HTTP_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        _raise_for_status(response, "LegendasDivx search")
        return response

    def _ensure_authenticated(self, config):
        if self._authenticated:
            return
        username = str(config.get("username") or "").strip()
        password = str(config.get("password") or "")
        if not username or not password:
            raise PermissionError("LegendasDivx username and password are required")

        response = self._http_get(
            LOGIN_URL,
            self._headers(config),
            self._cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        _raise_for_status(response, "LegendasDivx login page")
        self._store_response_cookies(response)
        data = parse_login_inputs(response.body)
        data.update({"username": username, "password": password})
        data.setdefault("login", "Login")
        _sleep(config)
        response = self._http_post(
            LOGIN_URL,
            data,
            self._headers(config, referer=LOGIN_URL),
            self._cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if response.status >= 400:
            _raise_for_status(response, "LegendasDivx login")
        self._store_response_cookies(response)
        body_text = _normalize(_decode_html(response.body))
        if "incorrect" in body_text or "password" in body_text and "username" in body_text and response.status == 200:
            raise PermissionError("LegendasDivx login failed")
        self._authenticated = True

    def _headers(self, config, referer=None):
        headers = {
            "User-Agent": str(config.get("user_agent") or USER_AGENT),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": BASE_URL,
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def _http_get(self, url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True):
        request = urllib.request.Request(url, headers=_headers_with_cookies(headers, cookies), method="GET")
        opener = urllib.request.build_opener() if allow_redirects else urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:
                body = response.read()
                result = HttpResponse(response.status, body, dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            result = HttpResponse(error.code, error.read(), dict(error.headers.items()))
        self._store_response_cookies(result)
        return result

    def _http_post(
        self,
        url,
        data,
        headers,
        cookies,
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=True,
    ):
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        request_headers = dict(headers or {})
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            url,
            data=body,
            headers=_headers_with_cookies(request_headers, cookies),
            method="POST",
        )
        opener = urllib.request.build_opener() if allow_redirects else urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:
                result = HttpResponse(response.status, response.read(), dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            result = HttpResponse(error.code, error.read(), dict(error.headers.items()))
        self._store_response_cookies(result)
        return result

    def _store_response_cookies(self, response):
        for value in _header_values(response.headers, "set-cookie"):
            cookie = SimpleCookie()
            try:
                cookie.load(value)
            except Exception:
                continue
            for key, morsel in cookie.items():
                if morsel.value is not None:
                    self._cookies[key] = morsel.value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def build_search_urls(video, language_code):
    if language_code not in LANGUAGES:
        return []
    if (video or {}).get("kind") == "movie":
        return [_build_movie_search_url(video, language_code)]
    if (video or {}).get("kind") == "episode":
        return _build_episode_search_urls(video, language_code)
    return []


def parse_login_inputs(body):
    values = {}
    text = _decode_html(body)
    for match in _INPUT_RE.finditer(text):
        attrs = _attrs(match.group("attrs"))
        name = attrs.get("name")
        if name:
            values[name] = attrs.get("value", "")
    return values


def parse_search_results(body):
    text = _decode_html(body)
    chunks = _SUB_BOX_SPLIT_RE.split(text)
    rows = []
    for chunk in chunks[1:]:
        item = _parse_sub_box(chunk)
        if item:
            rows.append(item)
    return rows


def extract_download(body, filename="", payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    return _content_payload(body, _format_from_filename(filename))


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name) and not os.path.basename(name).startswith(".")]
    if not candidates:
        raise ValueError("legendasdivx archive contains no supported subtitle files")
    release_info = _normalize_release((payload or {}).get("release_info"))
    try:
        season = int((payload or {}).get("season"))
        episode = int((payload or {}).get("episode"))
    except (TypeError, ValueError):
        season = episode = None

    def score(name):
        normalized = _normalize_release(os.path.basename(name))
        value = 0
        if season is not None and episode is not None:
            if re.search(rf"\bs0*{season}e0*{episode}\b", normalized):
                value += 120
            elif re.search(rf"\be0*{episode}\b", normalized):
                value += 80
        if release_info:
            release_tokens = [token for token in release_info.split() if len(token) > 1]
            value += sum(4 for token in release_tokens if token in normalized)
        return value

    return max(candidates, key=score)


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
    try:
        season = int((video or {}).get("season"))
        episode = int((video or {}).get("episode"))
    except (TypeError, ValueError):
        return []
    series_imdb_id = str((video or {}).get("series_imdb_id") or "").strip()
    if series_imdb_id:
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


def _parse_sub_box(chunk):
    language = _language_from_chunk(chunk)
    if not language:
        return None
    description = _match_text(_DESC_RE, chunk)
    page_link = _download_link(chunk)
    if not description or not page_link:
        return None
    return {
        "lid": _subtitle_id_from_url(page_link),
        "page_link": page_link,
        "language": language,
        "description": description,
        "hits": _safe_int(_match_text(_HITS_RE, chunk), 0),
        "frame_rate": _match_text(_FPS_RE, chunk),
        "uploader": _uploader_from_chunk(chunk),
    }


def _candidate(video, item):
    language = _language_payload(item["language"])
    matches = derive_matches(video, item)
    score = _score_from_matches(matches, item)
    release_info = item["description"]
    filename = f"legendasdivx.{_slug(release_info)}.{language['alpha2']}.zip"
    if item["language"] == "por-BR":
        filename = f"legendasdivx.{_slug(release_info)}.pt-br.zip"
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
        payload["season"] = _safe_int(video.get("season"), None)
        payload["episode"] = _safe_int(video.get("episode"), None)
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
            "uploader": item.get("uploader") or "anonymous",
            "hits": item.get("hits", 0),
            "frame_rate": item.get("frame_rate"),
        },
        "provider_payload": payload,
    }


def derive_matches(video, item):
    video = video or {}
    description = item.get("description") or ""
    desc_tokens = set(_tokens(description))
    release_tokens = _release_tokens(description)
    matches = []
    if video.get("kind") == "movie":
        titles = [video.get("title")] + list(video.get("alternative_titles") or [])
        if any(_all_tokens_present(title, desc_tokens) for title in titles if title):
            matches.append("title")
        if video.get("year") and str(video.get("year")) in desc_tokens:
            matches.append("year")
    elif video.get("kind") == "episode":
        if video.get("series_imdb_id"):
            matches.extend(["series", "series_imdb_id", "season", "episode"])
        else:
            if _all_tokens_present(video.get("series"), desc_tokens):
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
    if item.get("frame_rate") and not _fps_mismatch(video.get("fps"), item.get("frame_rate")):
        matches.append("fps")
    return _unique(matches)


def _score_from_matches(matches, item):
    score = 30
    weights = {
        "title": 20,
        "series": 20,
        "series_imdb_id": 20,
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


def _requested_languages(languages):
    requested = []
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        alpha3 = str(language.get("alpha3") or "").strip()
        country = str(language.get("country") or "").upper()
        if alpha3 == "por-BR" or (alpha3 == "por" and country == "BR"):
            code = "por-BR"
        elif alpha3 == "pob":
            code = "por-BR"
        elif alpha3 == "por":
            code = "por"
        else:
            continue
        if code not in requested:
            requested.append(code)
    return requested


def _language_payload(code):
    data = LANGUAGES[code]
    payload = {"alpha3": code, "alpha2": data["alpha2"], "hi": False, "forced": False}
    if data.get("country"):
        payload["country"] = data["country"]
    return payload


def _assert_search_available(response):
    text = _decode_html(response.body)
    count = _search_count(text)
    if count is not None and count >= SAFE_SEARCH_LIMIT:
        raise RuntimeError("LegendasDivx search limit reached")
    normalized = _normalize(text)
    if "ip" in normalized and "bloqueado" in normalized:
        raise RuntimeError("LegendasDivx IP address is blocked")


def _raise_for_status(response, context):
    if response.status >= 400:
        text = _decode_html(response.body)
        normalized = _normalize(text)
        if "bloqueado" in normalized:
            raise RuntimeError(f"{context}: IP address is blocked")
        raise RuntimeError(f"{context}: HTTP {response.status}")


def _page_count(body):
    text = _decode_html(body)
    match = _PAGER_RE.search(text)
    if not match:
        return 1
    count = _safe_int(match.group("count"), 0)
    if count <= 0:
        return 1
    return min(MAX_PAGES, (count // 10) + 1)


def _search_count(text):
    match = _SEARCH_COUNT_RE.search(text or "")
    if not match:
        return None
    return _safe_int(match.group("count"), None)


def _language_from_chunk(chunk):
    normalized = _normalize(chunk)
    if "brazil" in normalized:
        return "por-BR"
    if "portugal" in normalized or "portuguese" in normalized:
        return "por"
    return None


def _download_link(chunk):
    match = _DOWNLOAD_RE.search(chunk or "")
    if not match:
        return ""
    return urllib.parse.urljoin(BASE_URL + "/", html.unescape(match.group("href")))


def _subtitle_id_from_url(url):
    parsed = urllib.parse.urlparse(url or "")
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("lid", "id", "subid"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    basename = os.path.basename(parsed.path)
    return basename or hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:12]


def _uploader_from_chunk(chunk):
    header = _HEADER_RE.search(chunk or "")
    if not header:
        return "anonymous"
    anchor = _ANCHOR_TEXT_RE.search(header.group("body"))
    if not anchor:
        return "anonymous"
    return _strip_tags(anchor.group("body")) or "anonymous"


def _match_text(pattern, chunk):
    match = pattern.search(chunk or "")
    if not match:
        return ""
    return _strip_tags(match.group("value"))


def _attrs(raw):
    attrs = {}
    for key, _quote, value in _ATTR_RE.findall(raw or ""):
        attrs[key.lower()] = html.unescape(value)
    return attrs


def _strip_tags(value):
    text = _TAG_RE.sub(" ", value or "")
    return _WS_RE.sub(" ", html.unescape(text)).strip()


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


def _headers_with_cookies(headers, cookies):
    merged = dict(headers or {})
    if cookies:
        merged["Cookie"] = "; ".join(f"{key}={value}" for key, value in sorted(cookies.items()))
    return merged


def _header_values(headers, key):
    wanted = key.lower()
    for header, value in (headers or {}).items():
        if header.lower() == wanted and value:
            if isinstance(value, (list, tuple)):
                yield from value
            else:
                yield value


def _fps_mismatch(video_fps, subtitle_fps):
    try:
        video_value = float(video_fps)
        subtitle_value = float(subtitle_fps)
    except (TypeError, ValueError):
        return False
    return abs(video_value - subtitle_value) > 0.02


def _sleep(config):
    delay_ms = _safe_int((config or {}).get("request_delay_ms"), 0)
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _extract_rar_files(body):
    errors = []
    if py7zz is not None:
        try:
            return _extract_rar_files_with_py7zz(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("unar"):
        try:
            return _extract_rar_files_with_unar(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("7z") or shutil.which("7zz"):
        try:
            return _extract_rar_files_with_7z(body)
        except Exception as error:
            errors.append(error)
    if errors:
        details = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        raise RuntimeError(f"LegendasDivx RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("LegendasDivx RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("LegendasDivx bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "legendasdivx.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("LegendasDivx RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "legendasdivx.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [unar, "-quiet", "-o", output_dir, archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract LegendasDivx RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("LegendasDivx RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "legendasdivx.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [sevenzip, "x", "-y", f"-o{output_dir}", archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract LegendasDivx RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            relative = os.path.relpath(path, output_dir)
            if not _subtitle_extension(relative):
                continue
            with open(path, "rb") as handle:
                files.append((relative, handle.read()))
    if not files:
        raise ValueError("legendasdivx archive contains no supported subtitle files")
    return files


def _is_rar_archive(body):
    return bool(body) and (body.startswith(_RAR4_MAGIC) or body.startswith(_RAR5_MAGIC))


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


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
    content = _normalize_line_endings(content or b"")
    encoding = "utf-8"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    if subtitle_format == "sub":
        return "text/plain"
    return "application/x-subrip"


def _normalize_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _season_in_description(description, season):
    try:
        wanted = int(season)
    except (TypeError, ValueError):
        return False
    for match in _SEASON_ONLY_RE.finditer(description or ""):
        if int(match.group("season")) == wanted:
            return True
    return False


def _episode_in_description(description, season, episode):
    try:
        wanted_season = int(season)
        wanted_episode = int(episode)
    except (TypeError, ValueError):
        return False
    for match in _SXXEYY_RE.finditer(description or ""):
        if int(match.group("season")) == wanted_season and int(match.group("episode")) == wanted_episode:
            return True
    return False


def _contains_release_value(release_tokens, value):
    wanted = _release_tokens(value)
    return bool(wanted) and all(token in release_tokens for token in wanted)


def _all_tokens_present(value, candidate_tokens):
    tokens = _tokens(value)
    return bool(tokens) and all(token in candidate_tokens for token in tokens)


def _tokens(value):
    return [token for token in _normalize(value).split() if token]


def _release_tokens(value):
    return {token for token in re.split(r"[^A-Za-z0-9]+", str(value or "").lower()) if token}


def _normalize_release(value):
    return " ".join(_release_tokens(value))


def _normalize(value):
    text = str(value or "")
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _slug(value):
    slug = "-".join(_tokens(value))[:80].strip("-")
    return slug or "subtitle"


def _safe_int(value, default):
    try:
        return int(value)
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
