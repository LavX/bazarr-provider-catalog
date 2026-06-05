"""Jimaku provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
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


PROVIDER_ID = "jimaku"
BASE_URL = "https://jimaku.cc/api"
USER_AGENT = "BazarrProviderHub"
HTTP_TIMEOUT_SECONDS = 15
RATE_LIMIT_RETRIES = 3
CORRUPT_FILE_SIZE_THRESHOLD = 500
ACCEPTED_ARCHIVE_EXTENSIONS = (".zip", ".rar")
UNHANDLED_ARCHIVE_EXTENSIONS = (".7z",)
SUBTITLE_EXTENSIONS = (".ass", ".srt", ".ssa", ".sub", ".vtt")
SUPPORTED_ALPHA3 = "jpn"
SUPPORTED_ALPHA2 = "ja"
AI_RE = re.compile(r"(?<![a-z])[\[\(]?whisper(?:ai)?[\]\)]?(?![a-z])", re.I)

LANGUAGE_SQUASH = {
    "jp": "jpn",
    "jap": "jpn",
    "ja": "jpn",
    "chs": "zho",
    "cht": "zho",
    "zhi": "zho",
    "cn": "zho",
    "en": "eng",
    "eng": "eng",
    "sc": "srd",
    "sdh": "jpn",
    "cc": "jpn",
}


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", (_coerce_text(value) or "").lower()).strip()


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _contains_all_tokens(haystack, needle):
    haystack_tokens = set(_tokens(haystack))
    needle_tokens = _tokens(needle)
    return bool(needle_tokens) and all(token in haystack_tokens for token in needle_tokens)


def api_path(path, params=None):
    params = params or {}
    ordered = urllib.parse.urlencode(
        sorted(
            (key, _query_value(value))
            for key, value in params.items()
            if value is not None
        )
    )
    return f"{path}?{ordered}" if ordered else path


def _query_value(value):
    if isinstance(value, bool):
        return str(value).lower()
    return value


def _tmdb_param(video):
    video = video or {}
    tmdb_id = video.get("tmdb_id")
    if not tmdb_id:
        return None
    tmdb_value = str(tmdb_id)
    if tmdb_value.startswith(("movie:", "tv:")):
        return tmdb_value
    kind = video.get("kind")
    prefix = "movie" if kind == "movie" else "tv"
    return f"{prefix}:{tmdb_value}"


def build_entry_search_params(video, enable_name_search_fallback):
    video = video or {}
    anilist_id = video.get("anilist_id")
    if anilist_id:
        return {"anilist_id": anilist_id}

    tmdb = _tmdb_param(video)
    if tmdb:
        return {"tmdb_id": tmdb}

    kind = video.get("kind")
    if kind == "movie":
        title = (_coerce_text(video.get("title")) or "").strip().lower()
        return {"query": title} if title else None

    if kind == "episode" and enable_name_search_fallback:
        series = (_coerce_text(video.get("series")) or "").strip().lower()
        if not series:
            return None
        try:
            season = int(video.get("season") or 1)
        except (TypeError, ValueError):
            season = 1
        if season > 1:
            series = f"{series} {season}"
        return {"query": series}

    return None


def detect_subtitle_languages(filename):
    default = [SUPPORTED_ALPHA3]
    filename = filename or ""
    dot_parts = filename.split(".")
    bracket_parts = re.split(r"[\[\]\(\)]+", filename)
    candidate_group = ""
    if len(dot_parts) > 2:
        candidate_group = dot_parts[-2]
    elif len(bracket_parts) > 2:
        candidate_group = bracket_parts[-2]
    if not candidate_group:
        return default

    candidates = [item for item in re.split(r"[,\-+& ]+", candidate_group) if item]
    if any(re.search(r"\d", item) for item in candidates):
        return default
    if any(len(item) >= 5 for item in candidates):
        return default

    languages = []
    index = 0
    while index < len(candidates):
        candidate = candidates[index].lower()
        index += 1
        if candidate in {"ass", "srt", "ssa", "sub", "vtt", "zip", "rar"}:
            continue
        if len(candidate) == 4:
            candidates.append(candidate[:2])
            candidates.append(candidate[2:])
            continue
        if any(char in candidate for char in "[]()"):
            candidate = re.split(r"[\[\]\(\)]+", candidate)[0]
        alpha3 = LANGUAGE_SQUASH.get(candidate)
        if alpha3 is None and len(candidate) == 3:
            alpha3 = candidate
        if alpha3 and alpha3 not in languages:
            languages.append(alpha3)

    if not languages:
        return default
    if len(languages) > 1:
        return languages if SUPPORTED_ALPHA3 in languages else default
    return languages


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _is_archive(name):
    return (name or "").lower().endswith(ACCEPTED_ARCHIVE_EXTENSIONS)


def _is_unhandled_archive(name):
    return (name or "").lower().endswith(UNHANDLED_ARCHIVE_EXTENSIONS)


def filter_file_entries(files, enable_archives_download, enable_ai_subs, only_archives=False):
    files = [item for item in files or [] if not _is_unhandled_archive(item.get("name"))]
    archive_entries = [item for item in files if _is_archive(item.get("name"))]
    subtitle_entries = [
        item
        for item in files
        if not _is_archive(item.get("name")) and _subtitle_extension(item.get("name"))
    ]
    has_only_archives = bool(archive_entries) and not subtitle_entries
    if only_archives:
        files = archive_entries

    rows = []
    for item in files:
        filename = item.get("name") or ""
        is_archive = _is_archive(filename)
        if is_archive and not has_only_archives and not enable_archives_download:
            continue
        if not is_archive and not _subtitle_extension(filename):
            continue
        if not enable_ai_subs and AI_RE.search(filename):
            continue
        languages = detect_subtitle_languages(filename)
        if SUPPORTED_ALPHA3 not in languages:
            continue
        if len(languages) > 1:
            continue
        try:
            size = int(item.get("size", CORRUPT_FILE_SIZE_THRESHOLD))
        except (TypeError, ValueError):
            size = CORRUPT_FILE_SIZE_THRESHOLD
        if size < CORRUPT_FILE_SIZE_THRESHOLD:
            continue
        rows.append(item)
    return rows


def _require_config(config):
    api_key = str((config or {}).get("api_key") or "").strip()
    if not api_key:
        raise ValueError("jimaku api_key is required")
    return api_key


def _bool_config(config, key, default=False):
    value = (config or {}).get(key, default)
    return bool(value)


def _requested_japanese(languages):
    for language in languages or []:
        if isinstance(language, dict):
            alpha3 = str(language.get("alpha3") or "").lower()
            alpha2 = str(language.get("alpha2") or "").lower()
        else:
            alpha3 = str(language).lower()
            alpha2 = ""
        if alpha3 == SUPPORTED_ALPHA3 or alpha2 == SUPPORTED_ALPHA2:
            return {
                "alpha3": SUPPORTED_ALPHA3,
                "alpha2": SUPPORTED_ALPHA2,
                "hi": False,
                "forced": False,
            }
    return None


def _episode_offset_candidate(video):
    if (video or {}).get("kind") != "episode":
        return None
    try:
        season = int(video.get("season") or 1)
        episode = int(video.get("episode"))
        offset = int(video.get("series_anidb_season_episode_offset"))
    except (TypeError, ValueError):
        return None
    if season <= 1:
        return None
    offset = abs(offset)
    if offset and episode < offset:
        return episode + offset
    return None


def derive_matches(video, entry, filename, episode):
    video = video or {}
    entry = entry or {}
    filename = filename or ""
    names = " ".join(
        str(item)
        for item in (entry.get("name"), entry.get("english_name"), entry.get("japanese_name"))
        if item
    )
    matches = []
    if video.get("kind") == "movie":
        if _contains_all_tokens(names, video.get("title")) or _contains_all_tokens(filename, video.get("title")):
            matches.append("title")
    elif video.get("kind") == "episode":
        if _contains_all_tokens(names, video.get("series")) or _contains_all_tokens(filename, video.get("series")):
            matches.append("series")
        if video.get("season") is not None:
            matches.append("season")
        if episode is not None:
            matches.append("episode")
    year = video.get("year")
    if year and str(year) in _tokens(filename):
        matches.append("year")
    matches.append("movie" if video.get("kind") == "movie" else "episode")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _contains_all_tokens(filename, release_group):
        matches.append("release_group")
    if filename.lower().endswith(".srt"):
        matches.append("audio_codec")
    return list(dict.fromkeys(matches))


def compute_score(matches):
    matches = set(matches or [])
    score = 60
    if "title" in matches or "series" in matches:
        score += 20
    if "year" in matches:
        score += 10
    if "season" in matches:
        score += 5
    if "episode" in matches:
        score += 5
    if "audio_codec" in matches:
        score += 3
    return min(score, 100)


class JimakuProvider:
    def search(self, video, languages, config):
        _require_config(config)
        language = _requested_japanese(languages)
        if language is None:
            return []

        enable_name_search_fallback = _bool_config(config, "enable_name_search_fallback", True)
        enable_archives_download = _bool_config(config, "enable_archives_download", False)
        enable_ai_subs = _bool_config(config, "enable_ai_subs", False)

        params = build_entry_search_params(video, enable_name_search_fallback)
        if not params:
            return []

        entry_rows = self._search_entries(params, config)
        if not entry_rows:
            return []
        entry = entry_rows[0]
        is_movie_entry = bool((entry.get("flags") or {}).get("movie"))

        files, effective_episode, only_archives = self._files_for_entry(
            entry.get("id"),
            video,
            is_movie_entry,
            config,
        )
        rows = filter_file_entries(
            files,
            enable_archives_download=enable_archives_download,
            enable_ai_subs=enable_ai_subs,
            only_archives=only_archives,
        )
        results = [
            self._result(video, entry, item, language, effective_episode)
            for item in rows
        ]
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        _require_config(config)
        payload = provider_payload or {}
        url = payload.get("url")
        if not url:
            raise ValueError("jimaku download requires url")
        body = self._http_get(url, config=config)
        if payload.get("is_archive"):
            return extract_download(body, payload)
        return _content_payload(body, _format_from_filename(payload.get("filename")))

    def _search_entries(self, params, config):
        attempts = [dict(params)]
        if "query" in params:
            live_action_params = dict(params)
            live_action_params["anime"] = False
            params["anime"] = True
            attempts = [dict(params), live_action_params]
        for params_for_attempt in attempts:
            rows = self._get_json("entries/search", params_for_attempt, config=config)
            if rows:
                return rows
        return []

    def _files_for_entry(self, entry_id, video, is_movie_entry, config):
        path = f"entries/{entry_id}/files"
        if (video or {}).get("kind") != "episode" or is_movie_entry:
            return self._get_json(path, config=config), None, False

        episode = video.get("episode")
        adjusted = _episode_offset_candidate(video)
        if adjusted is not None:
            rows = self._get_json(path, {"episode": adjusted}, config=config)
            if rows:
                return rows, adjusted, False

        rows = self._get_json(path, {"episode": episode}, config=config)
        if rows:
            return rows, episode, False
        rows = self._get_json(path, config=config)
        return rows, episode, True

    def _result(self, video, entry, item, language, episode):
        filename = item.get("name") or ""
        matches = derive_matches(video, entry, filename, episode)
        score = compute_score(matches)
        return {
            "provider": PROVIDER_ID,
            "id": f"jimaku-{entry.get('id')}-{_hash_id(item.get('url') or filename)}",
            "language": language,
            "release_info": filename,
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item.get("url"),
            "display": {
                "source": "jimaku",
                "entry": entry.get("name") or entry.get("english_name"),
                "release": filename,
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "url": item.get("url"),
                "filename": filename,
                "is_archive": _is_archive(filename),
                "entry_id": entry.get("id"),
                "episode": episode,
                "video": {
                    "kind": (video or {}).get("kind"),
                    "season": (video or {}).get("season"),
                    "episode": episode,
                    "title": (video or {}).get("title"),
                    "series": (video or {}).get("series"),
                },
            },
        }

    def _get_json(self, path, params=None, config=None):
        body = self._http_get(api_path(path, params), config=config, api=True)
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("jimaku API did not return JSON") from exc
        if isinstance(data, dict) and "error" in data:
            if data.get("code") == 7 or data.get("error") == "unauthorized":
                raise PermissionError("jimaku API key is unauthorized")
            raise RuntimeError(f"jimaku API returned error: {data.get('error')}")
        return data

    def _http_get(self, url, config=None, api=False):
        api_key = _require_config(config)
        full_url = urllib.parse.urljoin(f"{BASE_URL}/", url) if api else url
        headers = {
            "Authorization": api_key,
            "User-Agent": USER_AGENT,
        }
        request = urllib.request.Request(full_url, headers=headers)
        for attempt in range(RATE_LIMIT_RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < RATE_LIMIT_RETRIES - 1:
                    delay = _rate_limit_delay(error)
                    time.sleep(delay)
                    continue
                if error.code == 401:
                    raise PermissionError("jimaku API key is unauthorized") from error
                raise
        raise RuntimeError("jimaku rate limit retry loop exited unexpectedly")


def _rate_limit_delay(error):
    value = error.headers.get("x-ratelimit-reset-after") if error.headers else None
    try:
        return min(max(float(value), 0.0), 5.0)
    except (TypeError, ValueError):
        return 5.0


def _hash_id(value):
    return _hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:12]


def _format_from_filename(filename):
    return _subtitle_extension(filename) or "srt"


def _content_payload(content, subtitle_format, empty=False):
    content = content or b""
    return {
        "content_b64": _base64.b64encode(content).decode("ascii"),
        "content_sha256": _hashlib.sha256(content).hexdigest(),
        "content_type": "application/x-subrip" if subtitle_format == "srt" else "text/plain",
        "format": subtitle_format,
        "encoding": "utf-8",
        "empty": bool(empty),
    }


def extract_download(body, payload):
    payload = payload or {}
    filename = payload.get("filename") or ""
    lowered = filename.lower()
    if lowered.endswith(".rar") or _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload.get("video") or {})
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body or b"")
    if lowered.endswith(".zip") or zipfile.is_zipfile(stream):
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload.get("video") or {})
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    return _content_payload(body or b"", _format_from_filename(filename))


def select_subtitle_file(names, video):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("jimaku archive contains no supported subtitle files")
    try:
        season = int((video or {}).get("season"))
    except (TypeError, ValueError):
        season = None
    try:
        episode = int((video or {}).get("episode"))
    except (TypeError, ValueError):
        episode = None
    if episode is None:
        return candidates[0]

    def score(name):
        normalized = _normalize(os.path.basename(name))
        if season is not None and re.search(rf"\bs0*{season}e0*{episode}\b", normalized):
            return 100
        if re.search(rf"\be0*{episode}\b", normalized):
            return 90
        if re.search(rf"(^|[^0-9])0*{episode}([^0-9]|$)", normalized):
            return 80
        return 0

    best = max(candidates, key=score)
    if score(best) == 0:
        raise ValueError(
            f"jimaku archive does not contain a subtitle for episode {episode}"
        )
    return best


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


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
        raise RuntimeError(f"Jimaku RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Jimaku RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Jimaku bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "jimaku.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Jimaku RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "jimaku.rar")
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
            raise RuntimeError(f"unar failed to extract Jimaku RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Jimaku RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "jimaku.rar")
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
            raise RuntimeError(f"7z failed to extract Jimaku RAR: {message}")
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
        raise ValueError("jimaku archive contains no supported subtitle files")
    return files
