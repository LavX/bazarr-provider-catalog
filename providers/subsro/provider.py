"""Subs.ro provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
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

try:
    import py7zz
except ImportError:
    py7zz = None

PROVIDER_ID = "subsro"
BASE_API_URL = "https://api.subs.ro/v1.0"
USER_AGENT = "BazarrProviderHub/1.0 (+https://github.com/LavX/bazarr-provider-catalog)"
HTTP_TIMEOUT_SECONDS = 15
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
ARCHIVE_EXTENSIONS = (".zip", ".rar")
LANGUAGE_TO_API = {"ron": "ro", "eng": "en"}
API_TO_LANGUAGE = {value: key for key, value in LANGUAGE_TO_API.items()}
ALPHA3_TO_ALPHA2 = {"ron": "ro", "eng": "en"}
SEASON_RE = re.compile(r"[Ss]ezon(?:ul)?\s*(\d{1,2})|[Ss](\d{1,2})[Ee]\d+")
EPISODE_RE = re.compile(r"[Ss]\d{1,2}[Ee](\d{1,3})|[Ee](\d{1,3})")
NON_ALNUM_RE = re.compile(r"[\W_]+")


class RateLimited(RuntimeError):
    pass


def parse_api_keys(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def auth_headers(api_key):
    return {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Subs-Api-Key": api_key,
    }


def api_language_code(language):
    return LANGUAGE_TO_API.get(_alpha3_for_language(language))


def _alpha3_for_language(language):
    if isinstance(language, dict):
        return language.get("alpha3")
    return str(language) if language else None


def _language_payload(alpha3):
    return {
        "alpha3": alpha3,
        "alpha2": ALPHA3_TO_ALPHA2.get(alpha3),
        "hi": False,
        "forced": False,
    }


def imdb_search_values(imdb_id):
    normalized = _normalize_imdb_id(imdb_id)
    if not normalized:
        return []
    values = [normalized]
    if normalized.startswith("tt"):
        legacy = normalized[2:]
        if legacy:
            values.append(legacy)
    return values


def _normalize_imdb_id(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("tt"):
        return text
    if text.isdigit():
        return f"tt{text}"
    return text


def _search_imdb_id(video):
    video = video or {}
    if video.get("kind") == "episode":
        return video.get("series_imdb_id") or video.get("imdb_id")
    return video.get("imdb_id")


def parse_season(title, release_info):
    text = f"{title or ''} {release_info or ''}"
    match = SEASON_RE.search(text)
    if not match:
        return None
    value = next((group for group in match.groups() if group is not None), None)
    return int(value) if value is not None else None


def parse_episode(text):
    match = EPISODE_RE.search(text or "")
    if not match:
        return None
    value = next((group for group in match.groups() if group is not None), None)
    return int(value) if value is not None else None


def _clean_tokens(value):
    return set(filter(None, NON_ALNUM_RE.sub(" ", str(value or "").lower()).split()))


def _contains_title(candidate, title):
    title_tokens = _clean_tokens(title)
    if not title_tokens:
        return False
    candidate_tokens = _clean_tokens(candidate)
    return title_tokens.issubset(candidate_tokens)


def derive_matches(video, item):
    video = video or {}
    title = item.get("title") or ""
    release_info = item.get("description") or ""
    candidate = f"{title} {release_info}"
    item_imdb = _normalize_imdb_id(item.get("imdbid") or item.get("imdb_id") or item.get("searched_imdb_id"))
    matches = []

    def add(match):
        if match not in matches:
            matches.append(match)

    if video.get("kind") == "episode":
        if video.get("series"):
            add("series")
        video_imdb = _normalize_imdb_id(video.get("series_imdb_id") or video.get("imdb_id"))
        if video_imdb and item_imdb == video_imdb:
            add("imdb_id")
        season = item.get("season")
        if season is not None and _int_or_none(video.get("season")) == season:
            add("season")
        episode = parse_episode(candidate)
        if episode is not None and _int_or_none(video.get("episode")) == episode:
            add("episode")
        elif "imdb_id" in matches and "season" in matches:
            add("episode")
        return matches

    if video.get("title") and (_contains_title(candidate, video.get("title")) or title):
        add("title")
    video_imdb = _normalize_imdb_id(video.get("imdb_id"))
    if video_imdb and item_imdb == video_imdb:
        add("imdb_id")
    if _int_or_none(video.get("year")) and _int_or_none(item.get("year")) == _int_or_none(video.get("year")):
        add("year")
    return matches


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_score(matches):
    weights = {
        "title": 30,
        "series": 30,
        "imdb_id": 35,
        "year": 15,
        "season": 15,
        "episode": 20,
    }
    return min(100, sum(weights.get(match, 0) for match in matches))


def _sleep(config):
    delay = _int_or_none((config or {}).get("request_delay_ms")) or 0
    if delay > 0:
        time.sleep(min(delay, 5000) / 1000.0)


def _api_url(path):
    if str(path).startswith(("http://", "https://")):
        return str(path)
    return f"{BASE_API_URL}/{str(path).lstrip('/')}"


class SubsRoProvider:
    def search(self, video, languages, config):
        config = dict(config or {})
        self._require_api_keys(config)
        requested = []
        for language in languages or []:
            alpha3 = _alpha3_for_language(language)
            if alpha3 in LANGUAGE_TO_API and alpha3 not in requested:
                requested.append(alpha3)
        if not requested:
            return []
        imdb_values = imdb_search_values(_search_imdb_id(video))
        if not imdb_values:
            return []

        results = []
        seen = set()
        for alpha3 in requested:
            language_results = []
            params = {"language": LANGUAGE_TO_API[alpha3]}
            for imdb_value in imdb_values:
                _sleep(config)
                data = self._request_json(
                    _api_url(f"search/imdbid/{urllib.parse.quote(imdb_value, safe='')}"),
                    params,
                    config,
                )
                for item in data.get("items") or []:
                    result = self._result(video, item, alpha3, imdb_value)
                    if result is None:
                        continue
                    key = (result["provider_payload"]["subtitle_id"], alpha3)
                    if key in seen:
                        continue
                    seen.add(key)
                    language_results.append(result)
                if language_results:
                    break
            results.extend(language_results)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, item, alpha3, searched_imdb_id):
        item = dict(item or {})
        item.setdefault("searched_imdb_id", searched_imdb_id)
        subtitle_id = item.get("id")
        title = item.get("title")
        if not subtitle_id or not title:
            return None
        item_language = API_TO_LANGUAGE.get(str(item.get("language") or "").lower())
        if item_language and item_language != alpha3:
            return None
        download_url = item.get("downloadLink") or _api_url(f"subtitle/{subtitle_id}/download")
        release_info = item.get("description") or title
        season = parse_season(title, release_info)
        if season is not None:
            item["season"] = season
        episode = _int_or_none((video or {}).get("episode")) if (video or {}).get("kind") == "episode" else None
        matches = derive_matches(video, item)
        score = compute_score(matches)
        alpha2 = ALPHA3_TO_ALPHA2.get(alpha3)
        fmt = _format_from_url(download_url, "zip")
        filename = f"subsro.{subtitle_id}.{alpha2}.{fmt}"
        return {
            "provider": PROVIDER_ID,
            "id": f"subsro-{subtitle_id}-{alpha3}",
            "language": _language_payload(alpha3),
            "release_info": release_info,
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item.get("link") or download_url,
            "display": {
                "source": "subs.ro",
                "title": title,
                "release": release_info,
                "year": item.get("year"),
                "translator": item.get("translator"),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": subtitle_id,
                "download_url": download_url,
                "filename": filename,
                "language": alpha3,
                "format": fmt,
                "season": season,
                "episode": episode,
                "release_info": release_info,
            },
        }

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        subtitle_id = payload.get("subtitle_id")
        download_url = payload.get("download_url")
        if not download_url and subtitle_id:
            download_url = _api_url(f"subtitle/{subtitle_id}/download")
        if not download_url:
            raise ValueError("subsro download requires download_url or subtitle_id")
        body = self._request_bytes(download_url, dict(config or {}))
        filename = payload.get("filename") or urllib.parse.urlparse(download_url).path.rsplit("/", 1)[-1]
        content, fmt = extract_download(body, filename, payload)
        return _content_payload(content, fmt)

    def _request_json(self, url, params, config):
        api_keys = self._require_api_keys(config)
        errors = []
        for api_key in api_keys:
            try:
                data = self._http_get_json(url, params, api_key, timeout=HTTP_TIMEOUT_SECONDS)
            except RateLimited as error:
                errors.append(error)
                continue
            status = _int_or_none(data.get("status")) if isinstance(data, dict) else None
            if status == 429:
                errors.append(RateLimited(str(data.get("message") or "rate limited")))
                continue
            if status in (401, 403):
                raise ValueError(f"Subs.ro api_key rejected with status {status}")
            if status is not None and status >= 500:
                raise RuntimeError(f"Subs.ro server error {status}")
            return data
        if errors:
            raise RuntimeError("Subs.ro rate limit reached for all api keys") from errors[-1]
        raise ValueError("Subs.ro api_key is required")

    def _request_bytes(self, url, config):
        api_keys = self._require_api_keys(config)
        errors = []
        for api_key in api_keys:
            try:
                return self._http_get_bytes(url, api_key, timeout=HTTP_TIMEOUT_SECONDS)
            except RateLimited as error:
                errors.append(error)
                continue
        if errors:
            raise RuntimeError("Subs.ro rate limit reached for all api keys") from errors[-1]
        raise ValueError("Subs.ro api_key is required")

    def _require_api_keys(self, config):
        api_keys = parse_api_keys((config or {}).get("api_key"))
        if not api_keys:
            raise ValueError("Subs.ro api_key is required")
        return api_keys

    def _http_get_json(self, url, params, api_key, timeout=HTTP_TIMEOUT_SECONDS):
        full_url = _url_with_params(url, params)
        body = self._http_request(full_url, api_key, timeout=timeout)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Subs.ro API returned invalid JSON") from exc

    def _http_get_bytes(self, url, api_key, timeout=HTTP_TIMEOUT_SECONDS):
        return self._http_request(url, api_key, timeout=timeout)

    def _http_request(self, url, api_key, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, headers=auth_headers(api_key))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code == 429:
                raise RateLimited("Subs.ro rate limited api key") from exc
            if exc.code in (401, 403):
                message = _error_message(body) or f"status {exc.code}"
                raise ValueError(f"Subs.ro api_key rejected: {message}") from exc
            if exc.code >= 500:
                raise RuntimeError(f"Subs.ro server error {exc.code}") from exc
            raise RuntimeError(f"Subs.ro request failed with status {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Subs.ro request failed: {exc.reason}") from exc


def _url_with_params(url, params):
    if not params:
        return url
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode(params)}"


def _error_message(body):
    try:
        data = json.loads((body or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data.get("message") if isinstance(data, dict) else None


def extract_download(body, filename="", payload=None):
    payload = payload or {}
    if not body:
        raise ValueError("subsro downloaded empty subtitle")
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return dict(files)[selected], _subtitle_extension(selected) or "srt"
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return archive.read(selected), _subtitle_extension(selected) or "srt"
    return body, _format_from_filename(filename)


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("subsro archive contains no supported subtitle files")
    episode = _int_or_none((payload or {}).get("episode"))
    if episode is None:
        return sorted(candidates, key=_archive_preference_key)[0]
    return max(candidates, key=lambda name: (_episode_file_score(name, episode), -_archive_preference_key(name)[0], -len(name)))


def _archive_preference_key(name):
    extension = _subtitle_extension(name) or ""
    return (0 if extension == "srt" else 1, len(name), name.lower())


def _episode_file_score(name, episode):
    normalized = NON_ALNUM_RE.sub(" ", os.path.basename(name).lower())
    if re.search(rf"\bs\d+e0*{episode}\b", normalized):
        return 100
    if re.search(rf"\be0*{episode}\b", normalized):
        return 90
    if re.search(rf"(^|[^0-9])0*{episode}([^0-9]|$)", normalized):
        return 80
    return 0


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
        raise RuntimeError(f"Subs.ro RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Subs.ro RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Subs.ro bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subsro.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Subs.ro RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subsro.rar")
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
            raise RuntimeError(f"unar failed to extract Subs.ro RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Subs.ro RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subsro.rar")
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
            raise RuntimeError(f"7z failed to extract Subs.ro RAR: {message}")
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
        raise ValueError("subsro archive contains no supported subtitle files")
    return files


def _is_rar_archive(body):
    return (body or b"").startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00"))


def _subtitle_extension(name):
    lower = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower.endswith(extension):
            return extension.lstrip(".")
    return None


def _format_from_filename(filename):
    extension = _subtitle_extension(filename)
    return extension or "srt"


def _format_from_url(url, fallback=None):
    suffix = urllib.parse.urlparse(url or "").path.rsplit(".", 1)[-1].lower()
    if suffix in {item.lstrip(".") for item in SUBTITLE_EXTENSIONS + ARCHIVE_EXTENSIONS}:
        return suffix
    return fallback or "zip"


def _content_payload(body, fmt):
    if not body:
        raise ValueError("subsro downloaded empty subtitle")
    try:
        body.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = "latin-1"
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
