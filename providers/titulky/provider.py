"""Titulky.com provider for the Bazarr+ Provider Hub catalog."""

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

try:
    import py7zz
except ImportError:
    py7zz = None

PROVIDER_ID = "titulky"
BASE_URL = "https://premium.titulky.com"
DOWNLOAD_URL = f"{BASE_URL}/download.php?id="
HTTP_TIMEOUT_SECONDS = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
LANGUAGES = {
    "ces": {"alpha3": "ces", "alpha2": "cs", "flag": "flag-CZ"},
    "slk": {"alpha3": "slk", "alpha2": "sk", "flag": "flag-SK"},
}

_FORM_RE = re.compile(rb'<form\b[^>]*class=["\'][^"\']*\bcloudForm\b[^"\']*["\'][^>]*>(?P<body>.*?)</form>', re.I | re.S)
_ROW_RE = re.compile(rb'<div\b[^>]*class=["\'](?P<class>[^"\']*\brow\b[^"\']*)["\'][^>]*>(?P<body>.*?)</div>', re.I | re.S)
_H5_RE = re.compile(rb"<h5\b[^>]*>(?P<value>.*?)</h5>", re.I | re.S)
_ANCHOR_RE = re.compile(rb'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>', re.I | re.S)
_SPAN_RE = re.compile(rb"<span\b[^>]*>(?P<value>.*?)</span>", re.I | re.S)
_ID_RE = re.compile(r"id=(\d+)")
_FPS_RE = re.compile(
    rb'<div\b[^>]*class=["\'][^"\']*\bulozil\b[^"\']*["\'][^>]*>.*?Movieroll\.png.*?(?P<fps>\d+(?:[,.]\d+)?)\s*FPS',
    re.I | re.S,
)
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_BYTES_RE = re.compile(rb"\s+")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


class HttpResponse:
    def __init__(self, status_code, body, headers=None, url=None):
        self.status_code = int(status_code or 0)
        self.body = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
        self.headers = headers or {}
        self.url = url or ""


class TitulkyProvider:
    def __init__(self):
        self._logged_in = False
        self._cookies = {}

    def search(self, video, languages, config):
        config = _validated_config(config)
        requested = _requested_languages(languages)
        if not requested:
            return []
        self._ensure_logged_in(config)
        video = video or {}
        imdb_id = _imdb_id(video)
        if not imdb_id:
            return []
        season = _safe_int(video.get("season")) if video.get("kind") == "episode" else 0
        episode = _safe_int(video.get("episode")) if video.get("kind") == "episode" else 0
        if season is None or episode is None:
            return []
        url = build_url({"action": "serial", "step": season, "id": imdb_id[2:]})
        _sleep(config)
        html_body = self._fetch_page(url, allow_redirects=True)
        rows = parse_browse_page(html_body, requested, episode, config)
        results = []
        for row in rows:
            fps = self._retrieve_subtitles_fps(row["subtitle_id"], config) if config["skip_wrong_fps"] else None
            results.append(_result_from_row(video, row, fps, config))
        return _sort_results(results)

    def download(self, provider_payload, language, config):
        del language
        config = _validated_config(config)
        self._ensure_logged_in(config)
        payload = provider_payload or {}
        url = payload.get("download_url") or payload.get("url")
        if not url:
            raise ValueError("titulky download requires download_url")
        _sleep(config)
        response = self._http_get(url, headers={"Referer": payload.get("page_link") or BASE_URL})
        if response.status_code == 429:
            raise RuntimeError("Too many requests")
        _raise_for_status(response, url)
        return extract_download(response.body, payload)

    def _ensure_logged_in(self, config):
        if self._logged_in:
            return
        response = self._http_post(
            BASE_URL,
            data={"LoginName": config["username"], "LoginPassword": config["password"]},
            headers={"Referer": BASE_URL},
        )
        location = _header(response.headers, "location")
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        msg_type = (parsed.get("msg_type") or [""])[0].lower()
        message = (parsed.get("msg") or [""])[0].lower()
        if response.status_code == 302 and msg_type == "i":
            if "omezen" in _normalize(message):
                raise PermissionError("Titulky VIP account is required")
            self._logged_in = True
            return
        raise PermissionError("Titulky login failed")

    def _retrieve_subtitles_fps(self, subtitle_id, config):
        _sleep(config)
        html_body = self._fetch_page(build_url({"action": "detail", "id": subtitle_id}), allow_redirects=True)
        return parse_fps(html_body)

    def _fetch_page(self, url, allow_redirects=False):
        response = self._http_get(url, allow_redirects=allow_redirects)
        if response.status_code == 429:
            raise RuntimeError("Too many requests")
        _raise_for_status(response, url)
        if not response.body:
            raise RuntimeError("Titulky returned an empty response")
        return response.body

    def _http_get(self, url, headers=None, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=False):
        opener = urllib.request.build_opener() if allow_redirects else urllib.request.build_opener(_NoRedirectHandler)
        request = urllib.request.Request(url, headers=self._headers(headers))
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            result = _http_error_response(error, url)
            self._store_cookies(result.headers)
            return result
        with response:
            result = HttpResponse(response.getcode(), response.read(), dict(response.headers.items()), response.geturl())
            self._store_cookies(result.headers)
            return result

    def _http_post(self, url, data=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        encoded = urllib.parse.urlencode(data or {}).encode("utf-8")
        merged_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        merged_headers.update(headers or {})
        request = urllib.request.Request(url, data=encoded, headers=self._headers(merged_headers))
        opener = urllib.request.build_opener(_NoRedirectHandler)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            result = _http_error_response(error, url)
            self._store_cookies(result.headers)
            return result
        with response:
            result = HttpResponse(response.getcode(), response.read(), dict(response.headers.items()), response.geturl())
            self._store_cookies(result.headers)
            return result

    def _headers(self, extra=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "sk,cs,en;q=0.7",
            "Connection": "keep-alive",
        }
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in sorted(self._cookies.items()))
        headers.update(extra or {})
        return headers

    def _store_cookies(self, headers):
        for key, value in (headers or {}).items():
            if key.lower() != "set-cookie":
                continue
            cookie = value.split(";", 1)[0]
            if "=" not in cookie:
                continue
            name, cookie_value = cookie.split("=", 1)
            self._cookies[name.strip()] = cookie_value.strip()


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_url(params):
    return f"{BASE_URL}/?{urllib.parse.urlencode(params).replace('+', '+')}"


def parse_browse_page(body, languages, wanted_episode, config):
    form_match = _FORM_RE.search(body or b"")
    if not form_match:
        return []
    wanted = {_language_key(language) for language in languages}
    rows = []
    current_episode = None
    for match in _ROW_RE.finditer(form_match.group("body")):
        classes = _decode(match.group("class")).split()
        row = match.group("body")
        number_match = _H5_RE.search(row)
        if number_match:
            current_episode = _safe_int(_strip_tags(number_match.group("value")).rstrip("."))
            continue
        if current_episode != wanted_episode or not {"pbl0", "pbl1"}.intersection(classes):
            continue
        anchor = _ANCHOR_RE.search(row)
        if not anchor:
            continue
        language = _language_from_row(row)
        if not language or _language_key(language) not in wanted:
            continue
        approved = "pbl1" in classes
        if config.get("approved_only") and not approved:
            continue
        href = html.unescape(_decode(anchor.group("href")))
        details_link = urllib.parse.urljoin(BASE_URL + "/", href.lstrip("/"))
        id_match = _ID_RE.search(details_link)
        if not id_match:
            continue
        release = _strip_tags(anchor.group("label"))
        if release == "???":
            release = ""
        subtitle_id = id_match.group(1)
        rows.append(
            {
                "subtitle_id": subtitle_id,
                "episode": current_episode,
                "release_info": release,
                "language": language,
                "approved": approved,
                "uploader": _uploader_from_row(row),
                "details_link": details_link,
                "download_url": f"{DOWNLOAD_URL}{subtitle_id}",
            }
        )
    return rows


def parse_fps(body):
    match = _FPS_RE.search(body or b"")
    if not match:
        return None
    return _float(_decode(match.group("fps")).replace(",", "."))


def extract_download(body, payload=None):
    payload = payload or {}
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body or b"")
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
            candidates = [name for name in names if _subtitle_extension(name)]
            if not candidates and len(names) == 1:
                raise RuntimeError("Titulky download limit has been exceeded")
            selected = select_subtitle_file(names, payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    if _looks_like_html(body):
        raise ValueError("titulky download did not return a supported subtitle file")
    subtitle_format = _direct_subtitle_format(body, payload)
    if not subtitle_format:
        raise ValueError("titulky download did not return a supported subtitle file")
    return _content_payload(body, subtitle_format)


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("titulky archive contains no supported subtitle files")
    release_tokens = set(_tokens((payload or {}).get("release_info")))

    def score(name):
        name_tokens = set(_tokens(name))
        value = len(release_tokens.intersection(name_tokens))
        if "forced" in name_tokens:
            value -= 5
        return value

    return max(candidates, key=score)


def _result_from_row(video, row, fps, config):
    video = video or {}
    matches = derive_matches(video, row, fps, config)
    score = 1 if not matches and config.get("skip_wrong_fps") else min(100, 45 + len(matches) * 10 + (10 if row["approved"] else 0))
    language = row["language"]
    filename = f"titulky.{row['subtitle_id']}.{language['alpha2']}.zip"
    return {
        "provider": PROVIDER_ID,
        "id": f"titulky-{row['subtitle_id']}-{language['alpha3']}",
        "language": {"alpha3": language["alpha3"], "alpha2": language["alpha2"], "hi": False, "forced": False},
        "release_info": row["release_info"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": row["details_link"],
        "display": {
            "source": "titulky.com",
            "release": row["release_info"],
            "uploader": row["uploader"],
            "approved": row["approved"],
            "fps": fps,
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "download_url": row["download_url"],
            "page_link": row["details_link"],
            "filename": filename,
            "release_info": row["release_info"],
            "language": language["alpha3"],
            "fps": fps,
        },
    }


def derive_matches(video, row, fps, config):
    if config.get("skip_wrong_fps") and video.get("fps") and fps and not _framerate_equal(video.get("fps"), fps):
        return []
    matches = []
    if video.get("kind") == "episode":
        if video.get("series_imdb_id") and video.get("series_imdb_id") == row.get("imdb_id", video.get("series_imdb_id")):
            matches.extend(["series_imdb_id", "series", "year"])
        if _safe_int(video.get("season")) is not None:
            matches.append("season")
        if _safe_int(video.get("episode")) == row.get("episode"):
            matches.append("episode")
    else:
        if video.get("imdb_id"):
            matches.extend(["imdb_id", "title", "year"])
    release_tokens = set(_tokens(row.get("release_info")))
    resolution = _coerce_text(video.get("resolution")).lower()
    if resolution and resolution in row.get("release_info", "").lower():
        matches.append("resolution")
    source_tokens = _tokens(video.get("source"))
    if source_tokens and all(token in release_tokens for token in source_tokens):
        matches.append("source")
    release_group = _normalize(video.get("release_group"))
    if release_group and release_group in release_tokens:
        matches.append("release_group")
    return matches


def _validated_config(config):
    config = dict(config or {})
    if not config.get("username") or not config.get("password"):
        raise PermissionError("Titulky username and password are required")
    for key in ("approved_only", "skip_wrong_fps"):
        if key not in config:
            config[key] = False
        if type(config[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    return config


def _requested_languages(languages):
    requested = []
    seen = set()
    for language in languages or []:
        item = _language_for_request(language)
        if not item or item["alpha3"] in seen:
            continue
        seen.add(item["alpha3"])
        requested.append(item)
    return requested


def _language_for_request(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    alpha2 = (language.get("alpha2") or "").lower()
    if alpha3 in LANGUAGES:
        return LANGUAGES[alpha3]
    if alpha2 == "cs":
        return LANGUAGES["ces"]
    if alpha2 == "sk":
        return LANGUAGES["slk"]
    return None


def _language_from_row(row):
    lowered = row.lower()
    if b"flag-cz" in lowered and b"flag-sk" not in lowered:
        return LANGUAGES["ces"]
    if b"flag-sk" in lowered and b"flag-cz" not in lowered:
        return LANGUAGES["slk"]
    return None


def _language_key(language):
    return language.get("alpha3")


def _uploader_from_row(row):
    spans = [_strip_tags(match.group("value")) for match in _SPAN_RE.finditer(row or b"")]
    return spans[-1] if spans else ""


def _imdb_id(video):
    imdb_id = _coerce_text(video.get("series_imdb_id") if video.get("kind") == "episode" else video.get("imdb_id"))
    return imdb_id if imdb_id.startswith("tt") else ""


def _framerate_equal(first, second):
    try:
        first = float(first)
        second = float(second)
    except (TypeError, ValueError):
        return True
    if first == second:
        return True
    equivalent_fps = ({23.976, 23.98, 24.0},)
    return any(first in values and second in values for values in equivalent_fps)


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
        raise RuntimeError(f"Titulky RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Titulky RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Titulky bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titulky.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Titulky RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titulky.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract Titulky RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Titulky RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titulky.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", "-y", f"-o{output_dir}", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract Titulky RAR: {message}")
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
        raise ValueError("titulky archive contains no supported subtitle files")
    return files


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


def _direct_subtitle_format(body, payload):
    subtitle_format = _subtitle_extension((payload or {}).get("filename", ""))
    if subtitle_format:
        return subtitle_format
    sample = _decode((body or b"")[:4096]).lstrip()
    if sample.upper().startswith("WEBVTT"):
        return "vtt"
    if "[Script Info]" in sample or "[Events]" in sample:
        return "ass"
    if "-->" in sample:
        return "srt"
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


def _http_error_response(error, url):
    headers = dict(error.headers.items()) if getattr(error, "headers", None) else {}
    try:
        body = error.read()
    finally:
        error.close()
    return HttpResponse(error.code, body, headers, url)


def _raise_for_status(response, url):
    if response.status_code >= 400:
        raise urllib.error.HTTPError(url, response.status_code, f"HTTP {response.status_code}", response.headers, None)


def _sort_results(results):
    return sorted(results, key=lambda item: item["score"], reverse=True)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _header(headers, name):
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            return value
    return ""


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
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return html.unescape(str(value)).strip()


def _safe_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decode(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
