"""Titlovi provider for the Bazarr+ Provider Hub catalog."""

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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

try:
    import py7zz
except ImportError:
    py7zz = None

PROVIDER_ID = "titlovi"
API_BASE_URL = "https://kodi.titlovi.com/api/subtitles"
TOKEN_URL = f"{API_BASE_URL}/gettoken"
SEARCH_URL = f"{API_BASE_URL}/search"
HTTP_TIMEOUT_SECONDS = 15
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

LANGUAGES = {
    "bos": {"alpha3": "bos", "alpha2": "bs", "titlovi": "Bosanski"},
    "eng": {"alpha3": "eng", "alpha2": "en", "titlovi": "English"},
    "hrv": {"alpha3": "hrv", "alpha2": "hr", "titlovi": "Hrvatski"},
    "mkd": {"alpha3": "mkd", "alpha2": "mk", "titlovi": "Makedonski"},
    "slv": {"alpha3": "slv", "alpha2": "sl", "titlovi": "Slovenski"},
    "srp": {"alpha3": "srp", "alpha2": "sr", "titlovi": "Srpski"},
    "srp-Cyrl": {"alpha3": "srp", "alpha2": "sr", "script": "Cyrl", "titlovi": "Cirilica"},
}
LANGUAGE_BY_TITLOVI = {value["titlovi"]: value for value in LANGUAGES.values()}

_AKA_RE = re.compile(r"^(?P<title>.+?)(?:\s+[Aa][Kk][Aa]\s+(?P<alt>.+))?$")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


class HttpResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code = int(status_code or 0)
        self.body = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
        self.headers = headers or {}


class TitloviProvider:
    def __init__(self):
        self._login_token = None
        self._user_id = None

    def search(self, video, languages, config):
        _require_credentials(config)
        requested = _requested_languages(languages)
        if not requested:
            return []
        self._ensure_token(config)
        video = video or {}
        title = _query_title(video)
        if not title:
            return []
        params = {
            "query": fix_inconsistent_naming(title),
            "lang": "|".join(language["titlovi"] for language in requested),
            "token": self._login_token,
            "userid": self._user_id,
            "json": True,
        }
        if video.get("kind") == "episode":
            season = _safe_int(video.get("season"))
            if season is not None:
                params["season"] = season
        imdb_id = _coerce_text(video.get("imdb_id"))
        if imdb_id:
            params["imdbID"] = imdb_id

        results = []
        try:
            _sleep(config)
            response = self._search_page(params)
            results.extend(response.get("SubtitleResults") or [])
            pages = min(_safe_int(response.get("PagesAvailable")) or 1, 3)
            for page in range(2, pages + 1):
                page_params = dict(params)
                page_params["pg"] = page
                _sleep(config)
                response = self._search_page(page_params)
                page_results = response.get("SubtitleResults") or []
                if not page_results:
                    break
                results.extend(page_results)
        except RuntimeError as error:
            if "Too many requests" in str(error):
                raise
            pass
        except urllib.error.HTTPError as error:
            error.close()
            pass
        except (ValueError, urllib.error.URLError):
            pass
        return _sort_results(_filter_requested_results(_results_from_api(video, results), requested))

    def download(self, provider_payload, language, config):
        del language
        _require_credentials(config)
        self._ensure_token(config)
        payload = provider_payload or {}
        url = payload.get("download_url") or payload.get("url")
        if not url:
            raise ValueError("titlovi download requires download_url")
        _sleep(config)
        response = self._http_get(url)
        if response.status_code == 429:
            raise RuntimeError("Too many requests")
        _raise_for_status(response, url)
        return extract_download(response.body, payload)

    def _ensure_token(self, config):
        if self._login_token and self._user_id:
            return
        response = self._http_post(
            TOKEN_URL,
            params={
                "username": config.get("username"),
                "password": config.get("password"),
                "json": True,
            },
        )
        if response.status_code == 401:
            raise PermissionError("Titlovi login failed")
        _raise_for_status(response, TOKEN_URL)
        payload = _json_body(response.body, "titlovi login did not return JSON")
        self._login_token = _coerce_text(payload.get("Token"))
        self._user_id = _coerce_text(payload.get("UserId"))
        if not self._login_token or not self._user_id:
            raise PermissionError("Titlovi login did not return token and user id")

    def _search_page(self, params):
        response = self._http_get(SEARCH_URL, params=params)
        if response.status_code == 429:
            raise RuntimeError("Too many requests")
        _raise_for_status(response, SEARCH_URL)
        return _json_body(response.body, "titlovi search did not return JSON")

    def _http_get(self, url, params=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        if params:
            query = urllib.parse.urlencode(params)
            separator = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{separator}{query}"
        request = urllib.request.Request(url, headers=_headers(headers))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.getcode(), response.read(), dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            return _http_error_response(error)

    def _http_post(self, url, params=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        query = urllib.parse.urlencode(params or {})
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        request = urllib.request.Request(f"{url}{separator}{query}", data=b"", headers=_headers(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.getcode(), response.read(), dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            return _http_error_response(error)


def _results_from_api(video, api_results):
    results = []
    wanted_episode = _safe_int((video or {}).get("episode"))
    wanted_season = _safe_int((video or {}).get("season"))
    is_episode = (video or {}).get("kind") == "episode"
    seen = set()
    for item in api_results:
        if not isinstance(item, dict):
            continue
        language = LANGUAGE_BY_TITLOVI.get(_coerce_text(item.get("Lang")))
        if not language:
            continue
        season = _safe_int(item.get("Season"))
        episode = _safe_int(item.get("Episode"))
        if is_episode:
            if wanted_season is not None and season != wanted_season:
                continue
            if wanted_episode is not None and episode not in {wanted_episode, 0}:
                continue
        subtitle_id = _coerce_text(item.get("Id"))
        if not subtitle_id or subtitle_id in seen:
            continue
        seen.add(subtitle_id)
        title, alt_title = _split_title(item.get("Title"))
        release = _coerce_text(item.get("Release"))
        matches = derive_matches(video, title, alt_title, release, season, episode, _safe_int(item.get("Year")))
        downloads = _safe_int(item.get("DownloadCount")) or 0
        rating = _float(item.get("Rating")) or 0.0
        is_pack = is_episode and episode == 0
        language_payload = _language_payload(language)
        score = min(100, 40 + len(matches) * 10 + int(rating * 3) + min(downloads, 250) // 25)
        if is_pack:
            score = max(0, score - 20)
        filename = f"titlovi.{subtitle_id}.{language_payload['alpha3']}.zip"
        results.append(
            {
                "provider": PROVIDER_ID,
                "id": f"titlovi-{subtitle_id}-{language_payload['alpha3']}",
                "language": language_payload,
                "release_info": release,
                "filename": filename,
                "matches": matches,
                "score": score,
                "score_without_hash": score,
                "score_out_of": 100,
                "hash_verifiable": False,
                "hearing_impaired_verifiable": False,
                "hearing_impaired": False,
                "page_link": _coerce_text(item.get("Link")),
                "display": {
                    "source": "titlovi.com",
                    "title": title,
                    "alternate_title": alt_title,
                    "release": release,
                    "rating": rating,
                    "downloads": downloads,
                },
                "provider_payload": {
                    "provider": PROVIDER_ID,
                    "schema": 1,
                    "subtitle_id": subtitle_id,
                    "download_url": _coerce_text(item.get("Link")),
                    "filename": filename,
                    "language": language_payload["alpha3"],
                    "script": language_payload.get("script"),
                    "season": wanted_season if is_episode else None,
                    "episode": wanted_episode if is_episode else None,
                    "is_pack": is_pack,
                    "release_info": release,
                },
            }
        )
    return results


def _filter_requested_results(results, requested):
    requested_keys = {(item["alpha3"], item.get("script")) for item in requested}
    return [
        item
        for item in results
        if (item["language"]["alpha3"], item["language"].get("script")) in requested_keys
    ]


def extract_download(body, payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", "srt", empty=True)
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    if _looks_like_html(body):
        raise ValueError("titlovi download did not return a supported subtitle file")
    subtitle_format = _direct_subtitle_format(body, payload)
    if not subtitle_format:
        raise ValueError("titlovi download did not return a supported subtitle file")
    return _content_payload(body, subtitle_format)


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("titlovi archive contains no supported subtitle files")
    if (payload or {}).get("is_pack"):
        selected = _select_pack_member(candidates, payload)
        if selected:
            return selected
    language = (payload or {}).get("language")
    if _language_alpha3(language) == "srp" and len(candidates) > 1:
        selected = _select_serbian_script_member(candidates, payload)
        if selected:
            return selected
    return candidates[0]


def _select_pack_member(candidates, payload):
    season = _safe_int((payload or {}).get("season"))
    episode = _safe_int((payload or {}).get("episode"))
    if season is None or episode is None:
        return None
    format_one = f"{season:02d}x{episode:02d}"
    format_two = f"s{season:02d}e{episode:02d}"
    for name in candidates:
        lowered = name.lower()
        if format_one in lowered or format_two in lowered:
            return name
    return None


def _select_serbian_script_member(candidates, payload):
    wants_cyrillic = (payload or {}).get("script") == "Cyrl"
    cyrillic = []
    latin = []
    for name in candidates:
        lowered = name.lower()
        if ".cyr" in lowered or ".cir" in lowered or "cyr)" in lowered:
            cyrillic.append(name)
        else:
            latin.append(name)
    if wants_cyrillic and cyrillic:
        return cyrillic[0]
    if not wants_cyrillic and latin:
        return latin[0]
    return None


def derive_matches(video, title, alt_title, release, season=None, episode=None, year=None):
    video = video or {}
    release_tokens = set(_tokens(release))
    matches = []
    if video.get("kind") == "episode":
        wanted_series = _normalize(fix_inconsistent_naming(video.get("series")))
        if wanted_series and wanted_series in {_normalize(title), _normalize(alt_title)}:
            matches.append("series")
        if video.get("year") and (year is None or _safe_int(video.get("year")) == year):
            matches.append("year")
        if _safe_int(video.get("season")) is not None and _safe_int(video.get("season")) == season:
            matches.append("season")
        wanted_episode = _safe_int(video.get("episode"))
        if wanted_episode is not None and wanted_episode in {episode, 0}:
            matches.append("episode")
    else:
        wanted_title = _normalize(fix_inconsistent_naming(video.get("title")))
        if wanted_title and wanted_title in {_normalize(title), _normalize(alt_title)}:
            matches.append("title")
        if video.get("year") and _safe_int(video.get("year")) == year:
            matches.append("year")
    resolution = _coerce_text(video.get("resolution")).lower()
    if resolution and resolution in release.lower():
        matches.append("resolution")
    source_tokens = _tokens(video.get("source"))
    if source_tokens and all(token in release_tokens for token in source_tokens):
        matches.append("source")
    release_group = _normalize(video.get("release_group"))
    if release_group and release_group in release_tokens:
        matches.append("release_group")
    return matches


def fix_inconsistent_naming(title):
    mapping = {
        "DC's Legends of Tomorrow": "Legends of Tomorrow",
        "Marvel's Jessica Jones": "Jessica Jones",
    }
    return mapping.get(_coerce_text(title), _coerce_text(title))


def _requested_languages(languages):
    requested = []
    seen = set()
    for language in languages or []:
        item = _language_for_request(language)
        if not item:
            continue
        key = (item["titlovi"], item.get("script"))
        if key in seen:
            continue
        seen.add(key)
        requested.append(item)
    return requested


def _language_for_request(language):
    if not isinstance(language, dict):
        return None
    alpha3 = _language_alpha3(language)
    script = language.get("script")
    if alpha3 == "srp" and script == "Cyrl":
        return LANGUAGES["srp-Cyrl"]
    return LANGUAGES.get(alpha3)


def _language_payload(language):
    payload = {
        "alpha3": language["alpha3"],
        "alpha2": language["alpha2"],
        "hi": False,
        "forced": False,
    }
    if language.get("script"):
        payload["script"] = language["script"]
    return payload


def _language_alpha3(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        alpha2 = (language.get("alpha2") or "").lower()
    else:
        alpha3 = str(language or "").lower()
        alpha2 = ""
    if alpha3 in LANGUAGES:
        return alpha3
    alpha2_map = {"bs": "bos", "en": "eng", "hr": "hrv", "mk": "mkd", "sl": "slv", "sr": "srp"}
    return alpha2_map.get(alpha2, alpha3)


def _require_credentials(config):
    config = config or {}
    if not config.get("username") or not config.get("password"):
        raise PermissionError("Titlovi username and password are required")


def _headers(extra=None):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
    }
    headers.update(extra or {})
    return headers


def _http_error_headers(error):
    headers = getattr(error, "headers", None)
    return dict(headers.items()) if headers else {}


def _http_error_response(error):
    try:
        body = error.read()
    finally:
        error.close()
    return HttpResponse(error.code, body, _http_error_headers(error))


def _json_body(body, message):
    try:
        return json.loads(_decode(body))
    except json.JSONDecodeError as exc:
        raise ValueError(message) from exc


def _raise_for_status(response, url):
    if response.status_code >= 400:
        raise urllib.error.HTTPError(url, response.status_code, f"HTTP {response.status_code}", response.headers, None)


def _sort_results(results):
    return sorted(results, key=lambda item: (item["score"], item["display"].get("downloads", 0)), reverse=True)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _split_title(value):
    title = _coerce_text(value)
    match = _AKA_RE.search(title)
    if not match:
        return title, ""
    return _coerce_text(match.group("title")), _coerce_text(match.group("alt"))


def _query_title(video):
    video = video or {}
    if video.get("kind") == "episode":
        return _coerce_text(video.get("series"))
    return _coerce_text(video.get("title"))


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
        raise RuntimeError(f"Titlovi RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Titlovi RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Titlovi bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titlovi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Titlovi RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titlovi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract Titlovi RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Titlovi RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titlovi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", "-y", f"-o{output_dir}", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract Titlovi RAR: {message}")
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
        raise ValueError("titlovi archive contains no supported subtitle files")
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
