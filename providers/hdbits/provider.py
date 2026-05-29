"""HDBits provider for the Bazarr+ Provider Hub catalog."""

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
import urllib.parse
import urllib.request
import zipfile

try:
    import py7zz
except ImportError:
    py7zz = None


PROVIDER_ID = "hdbits"
TORRENTS_URL = "https://hdbits.org/api/torrents"
SUBTITLES_URL = "https://hdbits.org/api/subtitles"
DOWNLOAD_URL = "https://hdbits.org/getdox.php"
USER_AGENT = "BazarrProviderHub"
HTTP_TIMEOUT_SECONDS = 15
ALLOWED_EXTENSIONS = (".ass", ".srt", ".ssa", ".vtt", ".zip", ".rar")
SUBTITLE_EXTENSIONS = (".ass", ".srt", ".ssa", ".vtt")
BLOCKED_RE = re.compile(r"extra|commentary|lyrics|forced", re.I)
EPISODE_TAG_RE = re.compile(r"\bs\d{1,3}e(\d{1,3})\b", re.I)
LOOSE_EPISODE_RE = re.compile(r"(?:^|[^a-z0-9])e(\d{1,3})(?:[^a-z0-9]|$)", re.I)


ALPHA2_TO_ALPHA3 = {
    "aa": "aar",
    "ab": "abk",
    "ae": "ave",
    "af": "afr",
    "ak": "aka",
    "am": "amh",
    "an": "arg",
    "ar": "ara",
    "as": "asm",
    "av": "ava",
    "ay": "aym",
    "az": "aze",
    "ba": "bak",
    "be": "bel",
    "bg": "bul",
    "bh": "bih",
    "bi": "bis",
    "bm": "bam",
    "bn": "ben",
    "bo": "bod",
    "bs": "bos",
    "ca": "cat",
    "ce": "che",
    "ch": "cha",
    "co": "cos",
    "cr": "cre",
    "cs": "ces",
    "cu": "chu",
    "cv": "chv",
    "cy": "cym",
    "da": "dan",
    "de": "deu",
    "dv": "div",
    "dz": "dzo",
    "ee": "ewe",
    "el": "ell",
    "en": "eng",
    "eo": "epo",
    "es": "spa",
    "et": "est",
    "eu": "eus",
    "fa": "fas",
    "ff": "ful",
    "fi": "fin",
    "fj": "fij",
    "fo": "fao",
    "fr": "fra",
    "fy": "fry",
    "ga": "gle",
    "gd": "gla",
    "gl": "glg",
    "gn": "grn",
    "gu": "guj",
    "gv": "glv",
    "ha": "hau",
    "he": "heb",
    "hi": "hin",
    "ho": "hmo",
    "hr": "hrv",
    "ht": "hat",
    "hu": "hun",
    "hy": "hye",
    "hz": "her",
    "ia": "ina",
    "id": "ind",
    "ie": "ile",
    "ig": "ibo",
    "ii": "iii",
    "ik": "ipk",
    "io": "ido",
    "is": "isl",
    "it": "ita",
    "iu": "iku",
    "ja": "jpn",
    "jv": "jav",
    "ka": "kat",
    "kg": "kon",
    "ki": "kik",
    "kj": "kua",
    "kk": "kaz",
    "kl": "kal",
    "km": "khm",
    "kn": "kan",
    "ko": "kor",
    "kr": "kau",
    "ks": "kas",
    "ku": "kur",
    "kv": "kom",
    "kw": "cor",
    "ky": "kir",
    "la": "lat",
    "lb": "ltz",
    "lg": "lug",
    "li": "lim",
    "ln": "lin",
    "lo": "lao",
    "lt": "lit",
    "lu": "lub",
    "lv": "lav",
    "mg": "mlg",
    "mh": "mah",
    "mi": "mri",
    "mk": "mkd",
    "ml": "mal",
    "mn": "mon",
    "mr": "mar",
    "ms": "msa",
    "mt": "mlt",
    "my": "mya",
    "na": "nau",
    "nb": "nob",
    "nd": "nde",
    "ne": "nep",
    "ng": "ndo",
    "nl": "nld",
    "nn": "nno",
    "no": "nor",
    "nr": "nbl",
    "nv": "nav",
    "ny": "nya",
    "oc": "oci",
    "oj": "oji",
    "om": "orm",
    "or": "ori",
    "os": "oss",
    "pa": "pan",
    "pi": "pli",
    "pl": "pol",
    "ps": "pus",
    "pt": "por",
    "qu": "que",
    "rm": "roh",
    "rn": "run",
    "ro": "ron",
    "ru": "rus",
    "rw": "kin",
    "sa": "san",
    "sc": "srd",
    "sd": "snd",
    "se": "sme",
    "sg": "sag",
    "si": "sin",
    "sk": "slk",
    "sl": "slv",
    "sm": "smo",
    "sn": "sna",
    "so": "som",
    "sq": "sqi",
    "sr": "srp",
    "ss": "ssw",
    "st": "sot",
    "su": "sun",
    "sv": "swe",
    "sw": "swa",
    "ta": "tam",
    "te": "tel",
    "tg": "tgk",
    "th": "tha",
    "ti": "tir",
    "tk": "tuk",
    "tl": "fil",
    "tn": "tsn",
    "to": "ton",
    "tr": "tur",
    "ts": "tso",
    "tt": "tat",
    "tw": "twi",
    "ty": "tah",
    "ug": "uig",
    "uk": "ukr",
    "ur": "urd",
    "uz": "uzb",
    "ve": "ven",
    "vi": "vie",
    "vo": "vol",
    "wa": "wln",
    "wo": "wol",
    "xh": "xho",
    "yi": "yid",
    "yo": "yor",
    "za": "zha",
    "zh": "zho",
    "zu": "zul",
}
ALPHA3_TO_ALPHA2 = {value: key for key, value in ALPHA2_TO_ALPHA3.items()}
ALPHA3_TO_ALPHA2.update({"eng": "en", "ell": "el", "por": "pt"})
HDBITS_LANGUAGES = sorted(set(ALPHA2_TO_ALPHA3.values()) | {"eng", "ell", "por"})

SPECIAL_HDBITS_LANGUAGE = {
    "br": "por",
    "gr": "ell",
    "uk": "eng",
}

SOURCE_TOKENS = {
    "Blu-ray": ["bluray", "blueray", "brrip", "bdrip", "bd"],
    "Web": ["web", "webrip", "webdl", "web-dl"],
    "WEB-DL": ["webdl", "web-dl", "web"],
    "WEBRip": ["webrip", "web-rip", "web"],
    "HDTV": ["hdtv"],
    "DVD": ["dvd", "dvdrip"],
}
VIDEO_CODEC_TOKENS = {
    "H.264": ["h264", "x264"],
    "H.265": ["h265", "x265", "hevc"],
    "DivX": ["divx"],
    "XviD": ["xvid"],
}
LONG_FIELD_TOKENS = {
    "audio_codec": ("audio_codec",),
    "release_group": ("release_group",),
    "streaming_service": ("streaming_service",),
    "edition": ("edition",),
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
    return [item for item in _normalize(value).split(" ") if item]


def _release_tokens(value):
    return set(_tokens(value))


def _has_any_token(release_tokens, candidates):
    for candidate in candidates:
        chunks = _tokens(candidate)
        if chunks and all(chunk in release_tokens for chunk in chunks):
            return True
    return False


def _field_present(release_tokens, value):
    chunks = _tokens(value)
    return bool(chunks) and all(chunk in release_tokens for chunk in chunks)


def _ordered_unique(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def build_lookup(video):
    video = video or {}
    kind = video.get("kind")
    if kind == "movie":
        imdb_id = str(video.get("imdb_id") or video.get("imdb") or "").strip()
        return {"imdb": {"id": imdb_id.removeprefix("tt")}}, ["imdb_id", "title", "year"], None
    if kind == "episode":
        tvdb_id = video.get("series_tvdb_id") or video.get("tvdb_id") or video.get("tvdb")
        lookup = {"tvdb": {"id": tvdb_id, "season": video.get("season")}}
        return lookup, ["tvdb_id", "imdb_id", "series", "title", "season", "episode"], video.get("episode")
    return {}, [], None


def hdbits_language_to_alpha3(code):
    normalized = str(code or "").lower().strip()
    return SPECIAL_HDBITS_LANGUAGE.get(normalized) or ALPHA2_TO_ALPHA3.get(normalized)


def _requested_alpha3(languages):
    requested = set()
    for language in languages or []:
        if isinstance(language, dict):
            alpha3 = language.get("alpha3")
            alpha2 = language.get("alpha2")
        else:
            alpha3 = str(language)
            alpha2 = None
        if alpha3:
            requested.add(str(alpha3).lower())
        elif alpha2:
            converted = ALPHA2_TO_ALPHA3.get(str(alpha2).lower())
            if converted:
                requested.add(converted)
    return requested


def _is_allowed(row):
    text = f"{row.get('title') or ''} {row.get('filename') or ''}"
    return BLOCKED_RE.search(text) is None


def _subtitle_extension(filename):
    lowered = (filename or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    extension = _subtitle_extension(filename)
    if extension:
        return extension
    return "srt"


def _episode_numbers(title):
    normalized = _normalize(title)
    episodes = {int(match.group(1)) for match in EPISODE_TAG_RE.finditer(normalized)}
    if episodes:
        return episodes
    return {int(match.group(1)) for match in LOOSE_EPISODE_RE.finditer(normalized)}


def derive_matches(video, release_info, base_matches=None):
    matches = list(base_matches or [])
    release = release_info or ""
    release_tokens = _release_tokens(release)

    source = _coerce_text((video or {}).get("source"))
    if source:
        source_tokens = SOURCE_TOKENS.get(source)
        if source_tokens and _has_any_token(release_tokens, source_tokens):
            matches.append("source")
        elif source_tokens is None and _field_present(release_tokens, source):
            matches.append("source")

    resolution = _coerce_text((video or {}).get("resolution"))
    if resolution and resolution.lower() in release_tokens:
        matches.append("resolution")

    video_codec = _coerce_text((video or {}).get("video_codec"))
    if video_codec:
        codec_tokens = VIDEO_CODEC_TOKENS.get(video_codec)
        if codec_tokens and _has_any_token(release_tokens, codec_tokens):
            matches.append("video_codec")
        elif codec_tokens is None and _field_present(release_tokens, video_codec):
            matches.append("video_codec")

    for field_names in LONG_FIELD_TOKENS.values():
        field_name = field_names[0]
        value = _coerce_text((video or {}).get(field_name))
        if value and _field_present(release_tokens, value):
            matches.append(field_name)

    return _ordered_unique(matches)


def parse_subtitles(rows, requested_alpha3, video, base_matches, episode=None):
    parsed = []
    requested = {str(item).lower() for item in requested_alpha3 or []}
    for row in rows or []:
        filename = str(row.get("filename") or "")
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            continue
        if not _is_allowed(row):
            continue
        language = hdbits_language_to_alpha3(row.get("language"))
        if not language or language not in requested:
            continue
        if episode is not None:
            explicit_episodes = _episode_numbers(row.get("title") or filename)
            try:
                wanted_episode = int(episode)
            except (TypeError, ValueError):
                wanted_episode = None
            if explicit_episodes and wanted_episode not in explicit_episodes:
                continue
        release_info = str(row.get("title") or filename)
        subtitle_id = row.get("id")
        parsed.append(
            {
                "subtitle_id": subtitle_id,
                "language": language,
                "release_info": release_info,
                "filename": filename,
                "matches": derive_matches(video, release_info, base_matches),
            }
        )
    return parsed


def _require_config(config):
    config = dict(config or {})
    username = str(config.get("username") or "").strip()
    passkey = str(config.get("passkey") or "").strip()
    if not username:
        raise ValueError("hdbits username is required")
    if not passkey:
        raise ValueError("hdbits passkey is required")
    return username, passkey


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


def _delay(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


class HDBitsProvider:
    def search(self, video, languages, config):
        username, passkey = _require_config(config)
        requested = _requested_alpha3(languages)
        if not requested:
            return []

        lookup, base_matches, episode = build_lookup(video)
        if not lookup:
            return []

        auth = {"username": username, "passkey": passkey}
        torrents = self._post_json(TORRENTS_URL, {**auth, **lookup})
        torrent_ids = [item.get("id") for item in torrents.get("data", []) if item.get("id") is not None]
        results = []
        for torrent_id in torrent_ids:
            _delay(config)
            subtitles = self._post_json(SUBTITLES_URL, {**auth, "torrent_id": torrent_id})
            rows = parse_subtitles(
                subtitles.get("data", []),
                requested_alpha3=requested,
                video=video,
                base_matches=base_matches,
                episode=episode,
            )
            for row in rows:
                results.append(self._result(video, row, torrent_id, episode))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language
        _username, passkey = _require_config(config)
        payload = provider_payload or {}
        subtitle_id = payload.get("subtitle_id")
        if subtitle_id is None:
            raise ValueError("hdbits download requires subtitle_id")
        query = urllib.parse.urlencode({"id": subtitle_id, "passkey": passkey})
        body = self._http_get(f"{DOWNLOAD_URL}?{query}")
        return extract_download(body, payload)

    def _result(self, video, row, torrent_id, episode):
        language = row["language"]
        alpha2 = ALPHA3_TO_ALPHA2.get(language, language[:2])
        matches = row["matches"]
        score = min(100, 70 + len(matches) * 5)
        return {
            "provider": PROVIDER_ID,
            "id": f"hdbits-{row['subtitle_id']}",
            "language": {
                "alpha3": language,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": row["release_info"],
            "filename": row["filename"],
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": True,
            "hearing_impaired": False,
            "page_link": "https://hdbits.org/",
            "display": {
                "source": "hdbits",
                "release": row["release_info"],
                "torrent_id": torrent_id,
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": row["subtitle_id"],
                "torrent_id": torrent_id,
                "filename": row["filename"],
                "season": (video or {}).get("season"),
                "episode": episode,
                "language": language,
            },
        }

    def _post_json(self, url, payload, timeout=HTTP_TIMEOUT_SECONDS):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("hdbits API did not return JSON") from exc

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()


def extract_download(body, payload):
    payload = payload or {}
    filename = payload.get("filename") or ""
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    lowered = filename.lower()
    if lowered.endswith(".rar") or _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if lowered.endswith(".zip") or zipfile.is_zipfile(stream):
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    return _content_payload(body, _format_from_filename(filename))


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("hdbits archive contains no supported subtitle files")
    try:
        season = int((payload or {}).get("season"))
    except (TypeError, ValueError):
        season = None
    try:
        episode = int((payload or {}).get("episode"))
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

    return max(candidates, key=score)


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
        raise RuntimeError(f"HDBits RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("HDBits RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("HDBits bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "hdbits.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("HDBits RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "hdbits.rar")
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
            raise RuntimeError(f"unar failed to extract HDBits RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("HDBits RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "hdbits.rar")
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
            raise RuntimeError(f"7z failed to extract HDBits RAR: {message}")
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
        raise ValueError("hdbits archive contains no supported subtitle files")
    return files
