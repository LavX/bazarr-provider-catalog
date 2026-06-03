"""Pipocas.tv provider for the Bazarr+ Provider Hub catalog."""

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
import urllib.parse
import urllib.request
import zipfile

try:
    import py7zz
except ImportError:
    py7zz = None

PROVIDER_ID = "pipocas"
BASE_URL = "https://pipocas.tv"
LOGIN_URL = f"{BASE_URL}/login"
SEARCH_URL = f"{BASE_URL}/legendas"
DOWNLOAD_URL = f"{BASE_URL}/legendas/download/{{id}}"
HTTP_TIMEOUT_SECONDS = 15
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

LANGUAGES = {
    "eng": {"alpha3": "eng", "alpha2": "en", "site": "ingles"},
    "por": {"alpha3": "por", "alpha2": "pt", "site": "portugues"},
    "por-BR": {"alpha3": "por-BR", "alpha2": "pt", "country": "BR", "site": "brasileiro"},
    "spa": {"alpha3": "spa", "alpha2": "es", "site": "espanhol"},
}

_TOKEN_RE = re.compile(rb'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', re.I)
_SEARCH_LINK_RE = re.compile(
    rb'<a\b(?=[^>]*class=["\'][^"\']*\btext-dark\b[^"\']*\bno-decoration\b[^"\']*["\'])'
    rb'[^>]*href=["\'](?P<href>[^"\']*/legendas/info/[^"\']+)["\']',
    re.I | re.S,
)
_TITLE_RE = re.compile(
    rb'<h3\b[^>]*class=["\'][^"\']*\btitle\b[^"\']*["\'][^>]*>.*?'
    rb'<span\b[^>]*class=["\'][^"\']*\bfont-normal\b[^"\']*["\'][^>]*>(?P<value>.*?)</span>',
    re.I | re.S,
)
_DOWNLOAD_RE = re.compile(rb'href=["\'](?P<href>[^"\']*/legendas/download/(?P<id>[^"\'/?#]+)[^"\']*)["\']', re.I)
_HITS_RE = re.compile(
    rb'<span\b[^>]*class=["\'][^"\']*\bhits\b[^"\']*\bhits-pd\b[^"\']*["\'][^>]*>\s*'
    rb'<div[^>]*>(?P<value>.*?)</div>',
    re.I | re.S,
)
_UPLOADER_RE = re.compile(
    rb'<span\b[^>]*style=["\'][^"\']*color\s*:\s*#[0-9a-f]{3,6}[^"\']*["\'][^>]*>(?P<value>.*?)</span>',
    re.I | re.S,
)
_RATING_RE = re.compile(
    rb'<h2\b[^>]*class=["\'][^"\']*\bmt-3\b[^"\']*\btext-center\b[^"\']*["\'][^>]*>'
    rb'\s*(?P<value>\d+)\s*/\s*\d+',
    re.I | re.S,
)
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_BYTES_RE = re.compile(rb"\s+")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


class HttpResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code = int(status_code or 0)
        self.body = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
        self.headers = headers or {}


class PipocasProvider:
    def __init__(self):
        self._authenticated = False
        self._cookies = {}

    def search(self, video, languages, config):
        requested = [_language_for_request(language) for language in languages or []]
        requested = [language for language in requested if language]
        if not requested:
            return []
        _require_credentials(config)
        self._ensure_authenticated(config)

        results = []
        seen = set()
        query = build_search_query(video)
        if not query:
            return []
        for language in requested:
            _sleep(config)
            response = self._http_get(
                SEARCH_URL,
                params={"t": "rel", "l": language["site"], "page": 1, "s": query},
            )
            _raise_for_status(response, SEARCH_URL)
            if _requires_account(response.body):
                raise PermissionError("Pipocas login is required for search")
            for detail_url in parse_search_results(response.body):
                _sleep(config)
                detail = self._parse_detail_page(video, detail_url, language)
                if not detail:
                    continue
                key = (detail["provider_payload"]["sub_id"], detail["language"]["alpha3"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(detail)
        return _sort_results(results)

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        url = payload.get("download_url") or payload.get("url")
        if not url and payload.get("sub_id"):
            url = DOWNLOAD_URL.format(id=payload["sub_id"])
        if not url:
            raise ValueError("pipocas download requires download_url or sub_id")
        _require_credentials(config)
        self._ensure_authenticated(config)
        response = self._http_get(url)
        _raise_for_status(response, url)
        if _requires_account(response.body):
            raise PermissionError("Pipocas login is required for download")
        if not response.body:
            raise RuntimeError("Pipocas download returned an empty response")
        return extract_download(response.body, payload)

    def _parse_detail_page(self, video, url, language):
        response = self._http_get(_absolute_url(url))
        _raise_for_status(response, url)
        item = parse_detail_page(response.body, _absolute_url(url))
        if not item:
            return None
        matches = derive_matches(video, item["release_info"])
        language_payload = _language_payload(language)
        filename = f"pipocas.{_slug(item['release_info'])}.{language_payload['alpha2']}.zip"
        score = min(100, 45 + len(matches) * 10 + item["score_stars"] * 3 + min(item["hits"], 300) // 30)
        return {
            "provider": PROVIDER_ID,
            "id": f"pipocas-{item['sub_id']}-{language_payload['alpha3']}",
            "language": language_payload,
            "release_info": item["release_info"],
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["download_url"],
            "display": {
                "source": "pipocas.tv",
                "release": item["release_info"],
                "hits": item["hits"],
                "uploader": item["uploader"],
                "rating": item["score_stars"],
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "sub_id": item["sub_id"],
                "download_url": item["download_url"],
                "detail_url": item["detail_url"],
                "filename": filename,
                "release_info": item["release_info"],
                "season": (video or {}).get("season"),
                "episode": (video or {}).get("episode"),
                "language": language_payload["alpha3"],
            },
        }

    def _ensure_authenticated(self, config):
        if self._authenticated:
            return
        login_page = self._http_get(LOGIN_URL)
        _raise_for_status(login_page, LOGIN_URL)
        token_match = _TOKEN_RE.search(login_page.body)
        if not token_match:
            raise RuntimeError("Pipocas login page did not expose a CSRF token")
        token = _decode(token_match.group(1))
        response = self._http_post(
            LOGIN_URL,
            {
                "username": config.get("username"),
                "password": config.get("password"),
                "_token": token,
            },
        )
        _raise_for_status(response, LOGIN_URL)
        if _requires_account(response.body):
            raise PermissionError("Pipocas login failed, check username and password")
        self._authenticated = True

    def _http_get(self, url, headers=None, timeout=HTTP_TIMEOUT_SECONDS, params=None):
        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{query}"
        request = urllib.request.Request(url, headers=self._headers(headers))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            result = HttpResponse(response.getcode(), body, response.headers)
            self._store_cookies(result.headers)
            return result

    def _http_post(self, url, data, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        encoded = urllib.parse.urlencode(data).encode("utf-8")
        merged_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        merged_headers.update(headers or {})
        request = urllib.request.Request(url, data=encoded, headers=self._headers(merged_headers))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            result = HttpResponse(response.getcode(), body, response.headers)
            self._store_cookies(result.headers)
            return result

    def _headers(self, extra=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": BASE_URL,
            "Referer": BASE_URL,
        }
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in sorted(self._cookies.items()))
        headers.update(extra or {})
        return headers

    def _store_cookies(self, headers):
        for value in _header_values(headers, "set-cookie"):
            cookie = value.split(";", 1)[0]
            if "=" not in cookie:
                continue
            name, cookie_value = cookie.split("=", 1)
            self._cookies[name.strip()] = cookie_value.strip()


def build_search_query(video):
    video = video or {}
    if video.get("kind") == "episode":
        series = _coerce_text(video.get("series"))
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        if series and season is not None and episode is not None:
            return f"{series} S{season:02d}E{episode:02d}"
        return series
    title = _coerce_text(video.get("title"))
    if title:
        return title
    name = _coerce_text(video.get("name"))
    return os.path.splitext(os.path.basename(name))[0] if name else ""


def parse_search_results(body):
    urls = []
    seen = set()
    for match in _SEARCH_LINK_RE.finditer(body or b""):
        url = _absolute_url(html.unescape(_decode(match.group("href"))))
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def parse_detail_page(body, detail_url):
    release = _match_text(_TITLE_RE, body)
    sub_match = _DOWNLOAD_RE.search(body or b"")
    if not release or not sub_match:
        return None
    sub_id = _decode(sub_match.group("id"))
    hits = _safe_int(_match_text(_HITS_RE, body)) or 0
    rating = _safe_int(_match_text(_RATING_RE, body)) or 0
    hit_factor = min(hits / 100.0, 5.0) if rating else 0
    score_stars = round((rating + hit_factor) / 2.0)
    return {
        "sub_id": sub_id,
        "release_info": release,
        "hits": hits,
        "uploader": _match_text(_UPLOADER_RE, body) or "pipocas-bot",
        "score_stars": score_stars,
        "detail_url": detail_url,
        "download_url": _absolute_url(html.unescape(_decode(sub_match.group("href")))),
    }


def derive_matches(video, release):
    video = video or {}
    release_tokens = set(_tokens(release))
    matches = []
    if video.get("kind") == "episode":
        series_tokens = _tokens(video.get("series"))
        if series_tokens and all(token in release_tokens for token in series_tokens):
            matches.append("series")
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        if season is not None and _has_season(release, season):
            matches.append("season")
        if episode is not None and _has_episode(release, episode):
            matches.append("episode")
    else:
        for title in [video.get("title")] + list(video.get("alternative_titles") or []):
            title_tokens = _tokens(title)
            if title_tokens and all(token in release_tokens for token in title_tokens):
                matches.append("title")
                break
        year = video.get("year")
        if year and str(year) in release_tokens:
            matches.append("year")
    resolution = _coerce_text(video.get("resolution")).lower()
    if resolution and resolution in release.lower():
        matches.append("resolution")
    source_tokens = _tokens(video.get("source"))
    if source_tokens and all(token in release_tokens for token in source_tokens):
        matches.append("source")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _normalize(release_group) in release_tokens:
        matches.append("release_group")
    return matches


def extract_download(body, payload=None):
    payload = payload or {}
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    subtitle_format = _subtitle_extension(payload.get("filename", ""))
    if not subtitle_format or _looks_like_html(body):
        raise ValueError("pipocas download did not return a supported subtitle file")
    return _content_payload(body, subtitle_format)


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("pipocas archive contains no supported subtitle files")
    season = _safe_int((payload or {}).get("season"))
    episode = _safe_int((payload or {}).get("episode"))
    release_tokens = set(_tokens((payload or {}).get("release_info")))

    def score(name):
        normalized_name = _normalize(os.path.basename(name))
        name_tokens = set(_tokens(name))
        value = 0
        if season is not None and re.search(rf"\bs0*{season}\b", normalized_name):
            value += 30
        if episode is not None:
            if re.search(rf"\bs\d*e0*{episode}\b", normalized_name):
                value += 80
            elif re.search(rf"\be0*{episode}\b", normalized_name):
                value += 60
            elif re.search(rf"(^|[^0-9])0*{episode}([^0-9]|$)", normalized_name):
                value += 40
        value += len(release_tokens.intersection(name_tokens))
        if "hi" in name_tokens or "sdh" in name_tokens:
            value -= 5
        return value

    return max(candidates, key=score)


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
        raise RuntimeError(f"Pipocas RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Pipocas RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Pipocas bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "pipocas.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Pipocas RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "pipocas.rar")
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
            raise RuntimeError(f"unar failed to extract Pipocas RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Pipocas RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "pipocas.rar")
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
            raise RuntimeError(f"7z failed to extract Pipocas RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if not _subtitle_extension(rel):
                continue
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    if not files:
        raise ValueError("pipocas archive contains no supported subtitle files")
    return files


def _language_for_request(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").strip()
    country = (language.get("country") or "").upper()
    if not country and isinstance(language.get("country_alpha2"), str):
        country = language["country_alpha2"].upper()
    if not country and isinstance(language.get("country_code"), str):
        country = language["country_code"].upper()
    if alpha3 == "por" and country == "BR":
        return LANGUAGES["por-BR"]
    if alpha3 in LANGUAGES:
        return LANGUAGES[alpha3]
    if alpha3 in {"eng-US", "eng-GB"}:
        return LANGUAGES["eng"]
    if alpha3 in {"spa-ES", "spa-MX"}:
        return LANGUAGES["spa"]
    if alpha3 == "por-PT":
        return LANGUAGES["por"]
    alpha2 = (language.get("alpha2") or "").lower()
    if alpha2 == "en":
        return LANGUAGES["eng"]
    if alpha2 == "es":
        return LANGUAGES["spa"]
    if alpha2 == "pt":
        return LANGUAGES["por-BR"] if country == "BR" else LANGUAGES["por"]
    return None


def _language_payload(language):
    payload = {
        "alpha3": language["alpha3"],
        "alpha2": language["alpha2"],
    }
    if language.get("country"):
        payload["country"] = language["country"]
    payload["hi"] = False
    payload["forced"] = False
    return payload


def _require_credentials(config):
    config = config or {}
    username = config.get("username")
    password = config.get("password")
    if not username or not password:
        raise PermissionError("Pipocas username and password are required")


def _header_values(headers, name):
    wanted = name.lower()
    if not headers:
        return []
    if hasattr(headers, "get_all"):
        return [str(value) for value in (headers.get_all(name) or headers.get_all(wanted) or [])]
    if isinstance(headers, dict):
        values = []
        for key, value in headers.items():
            if str(key).lower() == wanted:
                if isinstance(value, (list, tuple)):
                    values.extend(str(item) for item in value)
                else:
                    values.append(str(value))
        return values
    values = []
    for key, value in headers:
        if str(key).lower() == wanted:
            values.append(str(value))
    return values


def _raise_for_status(response, url):
    if response.status_code >= 400:
        raise urllib.error.HTTPError(url, response.status_code, f"HTTP {response.status_code}", response.headers, None)


def _requires_account(body):
    return b"Cria uma conta" in (body or b"")


def _sort_results(results):
    return sorted(results, key=lambda item: (item["score"], item["display"].get("hits", 0)), reverse=True)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _content_payload(content, subtitle_format):
    content = _fix_line_endings(content)
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
    return "application/x-subrip"


def _fix_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _looks_like_html(body):
    sample = (body or b"")[:512].lstrip().lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<body" in sample


def _has_season(release, season):
    normalized = _normalize(release)
    return bool(re.search(rf"\bs0*{season}\b", normalized) or re.search(rf"\bs0*{season}e\d+\b", normalized))


def _has_episode(release, episode):
    normalized = _normalize(release)
    return bool(re.search(rf"\bs\d*e0*{episode}\b", normalized) or re.search(rf"\be0*{episode}\b", normalized))


def _match_text(pattern, body):
    match = pattern.search(body or b"")
    return _strip_tags(match.group("value")) if match else ""


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", value or "")


def _strip_tags(value):
    stripped = _TAG_RE.sub(b"", value or b"")
    stripped = _WS_BYTES_RE.sub(b" ", stripped).strip()
    return _WS_RE.sub(" ", html.unescape(_decode(stripped))).strip()


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _coerce_text(value):
    return "" if value is None else str(value).strip()


def _safe_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _slug(value):
    return "-".join(_tokens(value)) or "release"


def _decode(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
