"""Embedded subtitle extractor for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import json
import os
import re
import subprocess

PROVIDER_ID = "embeddedsubtitles"
DEFAULT_CODECS = ("ass", "subrip", "webvtt", "mov_text")
SUPPORTED_CODECS = set(DEFAULT_CODECS)
CONTENT_TYPES = {
    "ass": "text/x-ssa",
    "srt": "application/x-subrip",
    "ssa": "text/x-ssa",
    "vtt": "text/vtt",
}
LANGUAGE_DATA = [
    ("en", "eng"),
    ("zh", "zho"),
    ("de", "deu"),
    ("es", "spa"),
    ("ru", "rus"),
    ("ko", "kor"),
    ("fr", "fra"),
    ("ja", "jpn"),
    ("pt", "por"),
    ("tr", "tur"),
    ("pl", "pol"),
    ("ca", "cat"),
    ("nl", "nld"),
    ("ar", "ara"),
    ("sv", "swe"),
    ("it", "ita"),
    ("id", "ind"),
    ("hi", "hin"),
    ("fi", "fin"),
    ("vi", "vie"),
    ("he", "heb"),
    ("uk", "ukr"),
    ("el", "ell"),
    ("ms", "msa"),
    ("cs", "ces"),
    ("ro", "ron"),
    ("da", "dan"),
    ("hu", "hun"),
    ("ta", "tam"),
    ("no", "nor"),
    ("th", "tha"),
    ("ur", "urd"),
    ("hr", "hrv"),
    ("bg", "bul"),
    ("lt", "lit"),
    ("la", "lat"),
    ("mi", "mri"),
    ("ml", "mal"),
    ("cy", "cym"),
    ("sk", "slk"),
    ("te", "tel"),
    ("fa", "fas"),
    ("lv", "lav"),
    ("bn", "ben"),
    ("sr", "srp"),
    ("az", "aze"),
    ("sl", "slv"),
    ("kn", "kan"),
    ("et", "est"),
    ("mk", "mkd"),
    ("br", "bre"),
    ("eu", "eus"),
    ("is", "isl"),
    ("hy", "hye"),
    ("ne", "nep"),
    ("mn", "mon"),
    ("bs", "bos"),
    ("kk", "kaz"),
    ("sq", "sqi"),
    ("sw", "swa"),
    ("gl", "glg"),
    ("mr", "mar"),
    ("pa", "pan"),
    ("si", "sin"),
    ("km", "khm"),
    ("sn", "sna"),
    ("yo", "yor"),
    ("so", "som"),
    ("af", "afr"),
    ("oc", "oci"),
    ("ka", "kat"),
    ("be", "bel"),
    ("tg", "tgk"),
    ("sd", "snd"),
    ("gu", "guj"),
    ("am", "amh"),
    ("yi", "yid"),
    ("lo", "lao"),
    ("uz", "uzb"),
    ("fo", "fao"),
    ("ht", "hat"),
    ("ps", "pus"),
    ("tk", "tuk"),
    ("nn", "nno"),
    ("mt", "mlt"),
    ("sa", "san"),
    ("lb", "ltz"),
    ("my", "mya"),
    ("bo", "bod"),
    ("tl", "tgl"),
    ("mg", "mlg"),
    ("as", "asm"),
    ("tt", "tat"),
    ("haw", "haw"),
    ("ln", "lin"),
    ("ha", "hau"),
    ("ba", "bak"),
    ("jw", "jav"),
    ("su", "sun"),
]
ALPHA2_TO_ALPHA3 = {alpha2: alpha3 for alpha2, alpha3 in LANGUAGE_DATA}
ALPHA3_TO_ALPHA2 = {alpha3: alpha2 for alpha2, alpha3 in LANGUAGE_DATA}
ALIASES = {
    "fre": "fra",
    "ger": "deu",
    "dut": "nld",
    "cze": "ces",
    "rum": "ron",
    "gre": "ell",
    "chi": "zho",
    "may": "msa",
    "per": "fas",
    "alb": "sqi",
    "baq": "eus",
    "wel": "cym",
    "ice": "isl",
    "geo": "kat",
    "mac": "mkd",
    "bur": "mya",
    "tib": "bod",
}
FORCED_TITLE_RE = re.compile(r"\bforced\b", re.I)
HI_TITLE_RE = re.compile(r"\b(?:sdh|hi|hearing[ -]?impaired|cc)\b", re.I)


class EmbeddedSubtitleError(RuntimeError):
    """Raised when local subtitle probing or extraction fails."""


def parse_probe_streams(payload, config):
    config = dict(config or {})
    allowed_codecs = _included_codecs(config)
    streams = []
    for stream in (payload or {}).get("streams") or []:
        codec = str(stream.get("codec_name") or "").strip().lower()
        if codec not in allowed_codecs:
            continue
        language, display_language = _language_from_stream(stream, config)
        if language is None:
            continue
        disposition = stream.get("disposition") or {}
        title = ((stream.get("tags") or {}).get("title") or "").strip()
        forced = _as_bool(disposition.get("forced")) or _title_is_forced(title)
        hi = _as_bool(disposition.get("hearing_impaired")) or _title_is_hi(title)
        language = dict(language)
        language["forced"] = forced
        language["hi"] = hi
        index = _int_or_none(stream.get("index"))
        if index is None:
            continue
        fmt = _provider_format_for_codec(codec)
        streams.append(
            {
                "index": index,
                "codec": codec,
                "format": fmt,
                "language": language,
                "display_language": display_language,
                "title": title,
                "default": _as_bool(disposition.get("default")),
            }
        )
    return streams


def probe_media(path, config):
    config = dict(config or {})
    ffprobe = str(config.get("ffprobe_path") or "ffprobe")
    timeout = _timeout(config)
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title:stream_disposition=default,forced,hearing_impaired",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise EmbeddedSubtitleError("ffprobe timed out") from exc
    except OSError as exc:
        raise EmbeddedSubtitleError(f"ffprobe could not start: {ffprobe}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise EmbeddedSubtitleError(message or "ffprobe failed")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise EmbeddedSubtitleError("ffprobe returned invalid JSON") from exc


def extract_subtitle_stream(path, stream_index, fmt, config):
    config = dict(config or {})
    ffmpeg = str(config.get("ffmpeg_path") or "ffmpeg")
    timeout = _timeout(config, default=600)
    fmt = fmt if fmt in CONTENT_TYPES else "srt"
    codec = "ass" if fmt == "ass" else "webvtt" if fmt == "vtt" else "srt"
    muxer = _ffmpeg_muxer_for_format(fmt)
    command = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-map",
        f"0:{int(stream_index)}",
        "-c:s",
        codec,
        "-f",
        muxer,
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise EmbeddedSubtitleError("ffmpeg timed out") from exc
    except OSError as exc:
        raise EmbeddedSubtitleError(f"ffmpeg could not start: {ffmpeg}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise EmbeddedSubtitleError(message or "ffmpeg failed")
    return result.stdout


class EmbeddedSubtitlesProvider:
    def __init__(self, probe_runner=None, extract_runner=None, path_exists=None):
        self.probe_runner = probe_runner or probe_media
        self.extract_runner = extract_runner or extract_subtitle_stream
        self.path_exists = path_exists or os.path.isfile

    def search(self, video, languages, config):
        video = video or {}
        if video.get("kind") not in {"movie", "episode"}:
            return []
        path = _video_path(video)
        if not path or not self.path_exists(path):
            return []
        requested = [_language_payload(language) for language in languages or []]
        if not requested:
            return []
        try:
            streams = parse_probe_streams(self.probe_runner(path, config or {}), config or {})
        except EmbeddedSubtitleError:
            return []
        results = []
        for stream in streams:
            if not _language_requested(stream["language"], requested):
                continue
            results.append(_candidate(video, path, stream))
        return results

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        if payload.get("provider") != PROVIDER_ID:
            raise ValueError("EmbeddedSubtitles payload belongs to a different provider")
        path = payload.get("path")
        stream_index = _int_or_none(payload.get("stream_index"))
        if not path or stream_index is None:
            raise ValueError("EmbeddedSubtitles download requires path and stream_index")
        fmt = payload.get("format") or "srt"
        content = self.extract_runner(path, stream_index, fmt, config or {})
        return _download_response(content or b"", fmt)


def _included_codecs(config):
    raw = (config or {}).get("included_codecs")
    if raw is None or raw == "":
        return set(DEFAULT_CODECS)
    if isinstance(raw, str):
        parts = re.split(r"[\s,]+", raw)
    else:
        parts = list(raw or [])
    codecs = {str(part).strip().lower() for part in parts if str(part).strip()}
    return codecs & SUPPORTED_CODECS if codecs else set(DEFAULT_CODECS)


def _language_from_stream(stream, config):
    tags = stream.get("tags") or {}
    raw = str(tags.get("language") or "").strip()
    language = _language_from_tag(raw)
    if language is not None:
        return language, language["alpha3"]
    if _as_bool((config or {}).get("unknown_as_fallback")):
        fallback = _language_from_tag((config or {}).get("fallback_lang") or "eng")
        if fallback is not None:
            return fallback, f"{raw or 'und'} -> {fallback['alpha3']}"
    return None, raw


def _language_from_tag(tag):
    value = str(tag or "").strip().lower().replace("_", "-")
    if not value or value in {"und", "unknown"}:
        return None
    primary = value.split("-", 1)[0]
    alpha3 = ALIASES.get(primary, primary)
    if len(alpha3) == 2:
        alpha3 = ALPHA2_TO_ALPHA3.get(alpha3)
    if not alpha3:
        return None
    alpha3 = ALIASES.get(alpha3, alpha3)
    if len(alpha3) != 3:
        return None
    return {
        "alpha3": alpha3,
        "alpha2": ALPHA3_TO_ALPHA2.get(alpha3),
        "hi": False,
        "forced": False,
    }


def _language_payload(language):
    if isinstance(language, dict):
        payload = dict(language)
    else:
        payload = {"alpha3": str(language)}
    alpha3 = str(payload.get("alpha3") or "").lower()
    if not alpha3 and payload.get("alpha2"):
        alpha3 = ALPHA2_TO_ALPHA3.get(str(payload["alpha2"]).lower())
    alpha3 = ALIASES.get(alpha3, alpha3)
    payload["alpha3"] = alpha3
    payload.setdefault("alpha2", ALPHA3_TO_ALPHA2.get(alpha3))
    payload["hi"] = _as_bool(payload.get("hi"))
    payload["forced"] = _as_bool(payload.get("forced"))
    return payload


def _language_requested(stream_language, requested):
    stream_key = _language_key(stream_language)
    return any(_language_key(language) == stream_key for language in requested)


def _language_key(language):
    payload = _language_payload(language)
    return (payload.get("alpha3"), bool(payload.get("hi")), bool(payload.get("forced")))


def _title_is_forced(title):
    return bool(FORCED_TITLE_RE.search(title or ""))


def _title_is_hi(title):
    return bool(HI_TITLE_RE.search(title or ""))


def _ffmpeg_muxer_for_format(fmt):
    return "webvtt" if fmt == "vtt" else fmt


def _provider_format_for_codec(codec):
    if codec == "ass":
        return "ass"
    if codec == "webvtt":
        return "vtt"
    return "srt"


def _video_path(video):
    for key in ("original_path", "path", "name"):
        value = (video or {}).get(key)
        if value:
            return str(value)
    return None


def _matches(video):
    if (video or {}).get("kind") == "episode":
        return ["series", "season", "episode", "hash"]
    return ["title", "hash"]


def _candidate(video, path, stream):
    filename = _filename(video, stream)
    release_info = stream.get("title") or filename
    stream_id = hashlib.sha1(f"{path}\0{stream['index']}".encode("utf-8")).hexdigest()[:16]
    language = dict(stream["language"])
    return {
        "provider": PROVIDER_ID,
        "id": f"embeddedsubtitles-{stream_id}",
        "language": language,
        "release_info": release_info,
        "filename": filename,
        "matches": _matches(video),
        "score": 100,
        "score_without_hash": 80,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": bool(language.get("hi")),
        "display": {
            "source": "embedded",
            "codec": stream["codec"],
            "stream": stream["index"],
            "language": stream.get("display_language") or language.get("alpha3"),
            "default": stream.get("default", False),
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "path": path,
            "stream_index": stream["index"],
            "codec": stream["codec"],
            "format": stream["format"],
        },
    }


def _filename(video, stream):
    base = os.path.basename(_video_path(video) or "embedded")
    stem = os.path.splitext(base)[0] or "embedded"
    language = stream["language"]["alpha3"]
    flags = []
    if stream["language"].get("forced"):
        flags.append("forced")
    if stream["language"].get("hi"):
        flags.append("hi")
    suffix = ".".join([language] + flags + [stream["format"]])
    return f"{stem}.{suffix}"


def _download_response(content, fmt):
    fmt = fmt if fmt in CONTENT_TYPES else "srt"
    return {
        "content_b64": base64.b64encode(content).decode("ascii") if content else "",
        "content_sha256": hashlib.sha256(content).hexdigest() if content else "",
        "content_type": CONTENT_TYPES.get(fmt, "application/x-subrip"),
        "format": fmt,
        "encoding": "utf-8" if content else None,
        "empty": not bool(content),
    }


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _timeout(config, default=30):
    try:
        value = int((config or {}).get("timeout_seconds") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 3600))
