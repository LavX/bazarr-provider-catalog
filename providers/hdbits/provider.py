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
BLOCKED_TOKENS = frozenset({"extra", "extras", "commentary", "lyrics"})
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
    "br": ("por", "BR"),
    "gr": ("ell", None),
    "uk": ("eng", None),
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
        imdb_id = str(video.get("imdb_id") or video.get("imdb") or "").strip().removeprefix("tt")
        if not imdb_id:
            return {}, [], None
        return {"imdb": {"id": imdb_id}}, ["imdb_id", "title", "year"], None
    if kind == "episode":
        tvdb_id = video.get("series_tvdb_id") or video.get("tvdb_id") or video.get("tvdb")
        if tvdb_id in (None, "", 0):
            return {}, [], None
        lookup = {"tvdb": {"id": tvdb_id, "season": video.get("season")}}
        return lookup, ["tvdb_id", "imdb_id", "series", "title", "season", "episode"], video.get("episode")
    return {}, [], None


def hdbits_language(code):
    """Resolve an HDBits language code to ``(alpha3, country_alpha2)``."""
    normalized = str(code or "").lower().strip()
    special = SPECIAL_HDBITS_LANGUAGE.get(normalized)
    if special is not None:
        return special
    alpha3 = ALPHA2_TO_ALPHA3.get(normalized)
    if alpha3:
        return (alpha3, None)
    return (None, None)


def hdbits_language_to_alpha3(code):
    return hdbits_language(code)[0]


def _requested_alpha3(languages):
    return {key[0] for key in _requested_variant_map(languages)}


def _requested_variant_map(languages):
    if isinstance(languages, dict):
        return {_variant_key(key): set(value) for key, value in languages.items()}
    requested = {}
    for language in languages or []:
        key = _language_key(language)
        if key[0]:
            forced = bool(language.get("forced")) if isinstance(language, dict) else False
            hi = bool(language.get("hi")) if isinstance(language, dict) else False
            requested.setdefault(key, set()).add((hi, forced))
    return requested


def _variant_key(key):
    if isinstance(key, tuple):
        alpha3 = str(key[0]).lower() if key[0] else None
        country = str(key[1]).upper() if len(key) > 1 and key[1] else None
        return (alpha3, country)
    return (str(key).lower(), None)


def _language_key(language):
    if isinstance(language, dict):
        alpha3 = language.get("alpha3")
        alpha2 = language.get("alpha2")
        country = language.get("country_alpha2") or language.get("country")
    else:
        alpha3 = str(language)
        alpha2 = None
        country = None
    if not alpha3 and alpha2:
        alpha3 = ALPHA2_TO_ALPHA3.get(str(alpha2).lower())
    if not alpha3:
        return (None, None)
    return (str(alpha3).lower(), str(country).upper() if country else None)


def _language_alpha3(language):
    return _language_key(language)[0]


def _is_allowed(row):
    tokens = set(_tokens(f"{row.get('title') or ''} {row.get('filename') or ''}"))
    return not (tokens & BLOCKED_TOKENS)


def _subtitle_flags(row, language=None):
    tokens = set(_tokens(f"{row.get('title') or ''} {row.get('filename') or ''}"))
    forced = "forced" in tokens
    hi_tokens = {"sdh"}
    # For Hindi rows the "hi" token is the language code, not an accessibility flag.
    if language != "hin":
        hi_tokens.add("hi")
    hearing_impaired = bool(hi_tokens & tokens) or {"hearing", "impaired"}.issubset(tokens)
    return hearing_impaired, forced


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
    requested = _requested_variant_map(requested_alpha3)
    for row in rows or []:
        filename = str(row.get("filename") or "")
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            continue
        if not _is_allowed(row):
            continue
        language, country = hdbits_language(row.get("language"))
        key = (language, country)
        if not language or key not in requested:
            continue
        hearing_impaired, forced = _subtitle_flags(row, language)
        if (hearing_impaired, forced) not in requested[key]:
            continue
        if episode is not None:
            explicit_episodes = _episode_numbers(f"{row.get('title') or ''} {filename}")
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
                "country_alpha2": country,
                "release_info": release_info,
                "filename": filename,
                "matches": derive_matches(video, release_info, base_matches),
                "hearing_impaired": hearing_impaired,
                "forced": forced,
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
        "encoding": _detect_encoding(content),
        "empty": bool(empty),
    }


def _detect_encoding(content):
    for encoding in ("utf-8", "cp1250", "latin-1"):
        try:
            (content or b"").decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


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
        requested = _requested_variant_map(languages)
        if not requested:
            return []

        lookup, base_matches, episode = build_lookup(video)
        if not lookup:
            return []

        auth = {"username": username, "passkey": passkey}
        torrents = self._post_json(TORRENTS_URL, {**auth, **lookup})
        torrent_ids = [item.get("id") for item in _api_data(torrents, "HDBits torrent lookup") if item.get("id") is not None]
        results = []
        for torrent_id in torrent_ids:
            _delay(config)
            subtitles = self._post_json(SUBTITLES_URL, {**auth, "torrent_id": torrent_id})
            rows = parse_subtitles(
                _api_data(subtitles, "HDBits subtitles lookup"),
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
        if not body:
            raise RuntimeError("hdbits download returned an empty response")
        return extract_download(body, payload)

    def _result(self, video, row, torrent_id, episode):
        language = row["language"]
        alpha2 = ALPHA3_TO_ALPHA2.get(language, language[:2])
        country = row.get("country_alpha2")
        matches = row["matches"]
        score = min(100, 70 + len(matches) * 5)
        language_payload = {
            "alpha3": language,
            "alpha2": alpha2,
            "hi": row.get("hearing_impaired", False),
            "forced": row.get("forced", False),
        }
        if country:
            language_payload["country_alpha2"] = country
        return {
            "provider": PROVIDER_ID,
            "id": f"hdbits-{row['subtitle_id']}",
            "language": language_payload,
            "release_info": row["release_info"],
            "filename": row["filename"],
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": True,
            "hearing_impaired": row.get("hearing_impaired", False),
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
                "country_alpha2": country,
                "hi": row.get("hearing_impaired", False),
                "forced": row.get("forced", False),
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
        raise RuntimeError("hdbits download returned an empty response")
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
        return _best_language_candidate(candidates, payload)

    hints = _language_hints((payload or {}).get("language"))

    def episode_score(name):
        normalized = _normalize(os.path.basename(name))
        if season is not None and re.search(rf"\bs0*{season}e0*{episode}\b", normalized):
            return 100
        if re.search(rf"\be0*{episode}\b", normalized):
            return 90
        # Loose fallback: a standalone episode-number token (for example
        # "Show 1 en srt"). It must be its own token so the season digits in
        # "s01e02" never satisfy an S01E01 request.
        if re.search(rf"(?:^|\s)0*{episode}(?:\s|$)", normalized):
            return 80
        return 0

    def score(name):
        value = episode_score(name)
        if hints & set(_tokens(name)):
            value += 20
        return value

    # Only keep files that actually carry the requested episode so a season pack
    # missing that episode raises instead of returning an arbitrary wrong file.
    matching = [name for name in candidates if episode_score(name) > 0]
    if not matching:
        raise ValueError(
            f"hdbits archive does not contain the requested episode {episode}"
        )

    return max(matching, key=score)


def _best_language_candidate(candidates, payload):
    hints = _language_hints((payload or {}).get("language"))
    if not hints:
        return candidates[0]
    for candidate in candidates:
        if hints & set(_tokens(candidate)):
            return candidate
    return candidates[0]


def _language_hints(language):
    alpha3 = str(language or "").lower()
    if not alpha3:
        return set()
    hints = {alpha3}
    alpha2 = ALPHA3_TO_ALPHA2.get(alpha3)
    if alpha2:
        hints.add(alpha2)
    hints.update(code for code, (mapped, _country) in SPECIAL_HDBITS_LANGUAGE.items() if mapped == alpha3)
    return hints


def _api_data(payload, context):
    if not isinstance(payload, dict):
        raise ValueError(f"{context} did not return a JSON object")
    if "data" in payload:
        return payload.get("data") or []
    message = payload.get("message") or payload.get("error") or payload.get("status_message")
    status = payload.get("status")
    if message or status not in (None, 0, 1, "0", "1", "ok", "success"):
        detail = message or status or "missing data"
        raise ValueError(f"{context} failed: {detail}")
    return []


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
