"""Whisper web-service provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

PROVIDER_ID = "whisperai"
PCM_MIME_TYPE = "application/octet-stream"
# Whisper /detect-language only inspects the start of the clip, so cap the
# detection extraction instead of decoding/uploading the whole file.
DETECT_SAMPLE_SECONDS = 30
# Languages OpenAI Whisper can transcribe. The alpha-2 keys and English
# labels are reproduced from the MIT-licensed whisper.tokenizer.LANGUAGES
# mapping in openai/whisper (whisper/tokenizer.py): Whisper emits exactly
# these codes and names.
WHISPER_LANGUAGES = {
    "en": "english",
    "zh": "chinese",
    "de": "german",
    "es": "spanish",
    "ru": "russian",
    "ko": "korean",
    "fr": "french",
    "ja": "japanese",
    "pt": "portuguese",
    "tr": "turkish",
    "pl": "polish",
    "ca": "catalan",
    "nl": "dutch",
    "ar": "arabic",
    "sv": "swedish",
    "it": "italian",
    "id": "indonesian",
    "hi": "hindi",
    "fi": "finnish",
    "vi": "vietnamese",
    "he": "hebrew",
    "uk": "ukrainian",
    "el": "greek",
    "ms": "malay",
    "cs": "czech",
    "ro": "romanian",
    "da": "danish",
    "hu": "hungarian",
    "ta": "tamil",
    "no": "norwegian",
    "th": "thai",
    "ur": "urdu",
    "hr": "croatian",
    "bg": "bulgarian",
    "lt": "lithuanian",
    "la": "latin",
    "mi": "maori",
    "ml": "malayalam",
    "cy": "welsh",
    "sk": "slovak",
    "te": "telugu",
    "fa": "persian",
    "lv": "latvian",
    "bn": "bengali",
    "sr": "serbian",
    "az": "azerbaijani",
    "sl": "slovenian",
    "kn": "kannada",
    "et": "estonian",
    "mk": "macedonian",
    "br": "breton",
    "eu": "basque",
    "is": "icelandic",
    "hy": "armenian",
    "ne": "nepali",
    "mn": "mongolian",
    "bs": "bosnian",
    "kk": "kazakh",
    "sq": "albanian",
    "sw": "swahili",
    "gl": "galician",
    "mr": "marathi",
    "pa": "punjabi",
    "si": "sinhala",
    "km": "khmer",
    "sn": "shona",
    "yo": "yoruba",
    "so": "somali",
    "af": "afrikaans",
    "oc": "occitan",
    "ka": "georgian",
    "be": "belarusian",
    "tg": "tajik",
    "sd": "sindhi",
    "gu": "gujarati",
    "am": "amharic",
    "yi": "yiddish",
    "lo": "lao",
    "uz": "uzbek",
    "fo": "faroese",
    "ht": "haitian creole",
    "ps": "pashto",
    "tk": "turkmen",
    "nn": "nynorsk",
    "mt": "maltese",
    "sa": "sanskrit",
    "lb": "luxembourgish",
    "my": "myanmar",
    "bo": "tibetan",
    "tl": "tagalog",
    "mg": "malagasy",
    "as": "assamese",
    "tt": "tatar",
    "haw": "hawaiian",
    "ln": "lingala",
    "ha": "hausa",
    "ba": "bashkir",
    "jw": "javanese",
    "su": "sundanese",
}

# ISO 639-3 (alpha-3) code for each Whisper language, from the ISO 639-3
# code tables (https://iso639-3.sil.org). Bazarr addresses subtitles by
# alpha-3 while Whisper speaks alpha-2, so we translate between the two.
WHISPER_ALPHA3 = {
    "en": "eng",
    "zh": "zho",
    "de": "deu",
    "es": "spa",
    "ru": "rus",
    "ko": "kor",
    "fr": "fra",
    "ja": "jpn",
    "pt": "por",
    "tr": "tur",
    "pl": "pol",
    "ca": "cat",
    "nl": "nld",
    "ar": "ara",
    "sv": "swe",
    "it": "ita",
    "id": "ind",
    "hi": "hin",
    "fi": "fin",
    "vi": "vie",
    "he": "heb",
    "uk": "ukr",
    "el": "ell",
    "ms": "msa",
    "cs": "ces",
    "ro": "ron",
    "da": "dan",
    "hu": "hun",
    "ta": "tam",
    "no": "nor",
    "th": "tha",
    "ur": "urd",
    "hr": "hrv",
    "bg": "bul",
    "lt": "lit",
    "la": "lat",
    "mi": "mri",
    "ml": "mal",
    "cy": "cym",
    "sk": "slk",
    "te": "tel",
    "fa": "fas",
    "lv": "lav",
    "bn": "ben",
    "sr": "srp",
    "az": "aze",
    "sl": "slv",
    "kn": "kan",
    "et": "est",
    "mk": "mkd",
    "br": "bre",
    "eu": "eus",
    "is": "isl",
    "hy": "hye",
    "ne": "nep",
    "mn": "mon",
    "bs": "bos",
    "kk": "kaz",
    "sq": "sqi",
    "sw": "swa",
    "gl": "glg",
    "mr": "mar",
    "pa": "pan",
    "si": "sin",
    "km": "khm",
    "sn": "sna",
    "yo": "yor",
    "so": "som",
    "af": "afr",
    "oc": "oci",
    "ka": "kat",
    "be": "bel",
    "tg": "tgk",
    "sd": "snd",
    "gu": "guj",
    "am": "amh",
    "yi": "yid",
    "lo": "lao",
    "uz": "uzb",
    "fo": "fao",
    "ht": "hat",
    "ps": "pus",
    "tk": "tuk",
    "nn": "nno",
    "mt": "mlt",
    "sa": "san",
    "lb": "ltz",
    "my": "mya",
    "bo": "bod",
    "tl": "tgl",
    "mg": "mlg",
    "as": "asm",
    "tt": "tat",
    "haw": "haw",
    "ln": "lin",
    "ha": "hau",
    "ba": "bak",
    "jw": "jav",
    "su": "sun",
}

ALPHA2_TO_ALPHA3 = dict(WHISPER_ALPHA3)
ALPHA3_TO_ALPHA2 = {alpha3: alpha2 for alpha2, alpha3 in WHISPER_ALPHA3.items()}
LANGUAGE_NAMES = {
    WHISPER_ALPHA3[alpha2]: name.title() for alpha2, name in WHISPER_LANGUAGES.items()
}
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
FFMPEG_LANGUAGE_CODES = {
    "ces": "cze",
    "deu": "ger",
    "fra": "fre",
    "nld": "dut",
    "ron": "rum",
    "zho": "chi",
}


class WhisperAIError(RuntimeError):
    """Raised when WhisperAI cannot process a request."""


class WhisperHttpClient:
    def __init__(self, config):
        config = _require_config(config)
        self.base_url = str(config["endpoint"]).rstrip("/")
        self.response_timeout = int(config["response_timeout_seconds"])

    def post_multipart(self, endpoint, params, files, timeout):
        url = self.base_url + "/" + str(endpoint).lstrip("/")
        query = urllib.parse.urlencode(params or {})
        if query:
            url = f"{url}?{query}"
        body, content_type = _multipart_body({}, files)
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": content_type,
                "User-Agent": "BazarrProviderHub/WhisperAI/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read(), response.headers.get("Content-Type", "")


def normalize_language(value):
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return None
    primary = raw.split("-", 1)[0]
    if len(primary) == 2:
        return ALPHA2_TO_ALPHA3.get(primary)
    return ALIASES.get(primary, primary if primary in ALPHA3_TO_ALPHA2 else None)


def alpha3_to_alpha2(value):
    return ALPHA3_TO_ALPHA2.get(normalize_language(value))


def alpha2_to_alpha3(value):
    return normalize_language(value)


def plan_transcription(video, language, detected_language=None):
    requested = normalize_language((language or {}).get("alpha3") if isinstance(language, dict) else language)
    if requested not in ALPHA3_TO_ALPHA2:
        return None

    audio_languages = _audio_languages(video)
    if not audio_languages and detected_language:
        audio_languages = [(detected_language, detected_language)]
    if not audio_languages:
        return None

    for original, normalized in audio_languages:
        if normalized == requested:
            return {
                "task": "transcribe",
                "input_language": normalized,
                "output_language": requested,
                "audio_stream_language": original if len(audio_languages) > 1 else None,
            }

    source = audio_languages[0]
    if requested == "eng":
        return {
            "task": "translate",
            "input_language": source[1],
            "output_language": "eng",
            "audio_stream_language": source[0] if len(audio_languages) > 1 else None,
        }
    return None


def extract_audio(path, ffmpeg_path, audio_stream_language=None, timeout_seconds=120, max_duration_seconds=None):
    command = [str(ffmpeg_path), "-nostdin", "-i", str(path)]
    if audio_stream_language:
        stream_index = _audio_stream_index(path, ffmpeg_path, audio_stream_language, timeout_seconds)
        command.extend(["-map", f"0:{stream_index}"])
    command.extend([
        "-vn",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
    ])
    if max_duration_seconds:
        # Cap extraction so a missing-audio-tag detection pass does not decode a
        # multi-hour file into memory; Whisper /detect-language only reads the head.
        command.extend(["-t", str(int(max_duration_seconds))])
    command.append("-")
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=int(timeout_seconds),
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise WhisperAIError(message or "ffmpeg failed to extract audio")
    return result.stdout


class WhisperAIProvider:
    def __init__(self, audio_runner=None, http_client_factory=None, path_exists=None):
        self.audio_runner = audio_runner or extract_audio
        self.http_client_factory = http_client_factory or WhisperHttpClient
        self.path_exists = path_exists or os.path.isfile

    def search(self, video, languages, config):
        config = _require_config(config)
        video = video or {}
        if video.get("kind") not in {"movie", "episode"}:
            return []
        path = _video_path(video)
        if not path or not self.path_exists(path):
            return []
        detected = None
        if not _audio_languages(video):
            detected = self._detect_language(path, config)
            if not detected:
                return []
        results = []
        for language in languages or []:
            payload = _language_payload(language)
            plan = plan_transcription(video, payload, detected_language=detected)
            if plan is None:
                continue
            results.append(_candidate(video, path, payload, plan))
        return results

    def download(self, provider_payload, language, config):
        del language
        config = _require_config(config)
        payload = dict(provider_payload or {})
        if payload.get("provider") != PROVIDER_ID:
            raise ValueError("WhisperAI payload belongs to a different provider")
        path = payload.get("path")
        task = payload.get("task")
        input_language = normalize_language(payload.get("input_language"))
        if not path or task not in {"transcribe", "translate"} or not input_language:
            raise ValueError("WhisperAI download requires path, task, and input_language")
        audio = self.audio_runner(
            path,
            config["ffmpeg_path"],
            payload.get("audio_stream_language"),
            int(config["transcription_timeout_seconds"]),
        )
        params = {
            "task": task,
            "language": alpha3_to_alpha2(input_language) or "en",
            "output": "srt",
            "encode": "false",
        }
        if _as_bool(config.get("pass_video_name")):
            params["video_file"] = path
        body, _content_type = self.http_client_factory(config).post_multipart(
            "/asr",
            params,
            {"audio_file": ("audio.raw", audio, PCM_MIME_TYPE)},
            timeout=int(config["transcription_timeout_seconds"]),
        )
        return _download_response(body or b"")

    def _detect_language(self, path, config):
        audio = self.audio_runner(
            path,
            config["ffmpeg_path"],
            None,
            int(config["response_timeout_seconds"]),
            max_duration_seconds=DETECT_SAMPLE_SECONDS,
        )
        params = {"encode": "false"}
        if _as_bool(config.get("pass_video_name")):
            params["video_file"] = path
        body, _content_type = self.http_client_factory(config).post_multipart(
            "/detect-language",
            params,
            {"audio_file": ("audio.raw", audio, PCM_MIME_TYPE)},
            timeout=int(config["response_timeout_seconds"]),
        )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhisperAIError("Whisper language detection returned invalid JSON") from exc
        return normalize_language(payload.get("language_code"))


def _candidate(video, path, requested_language, plan):
    output_language = plan["output_language"]
    input_language = plan["input_language"]
    release_info = (
        f"{plan['task']} {_language_name(input_language)} audio -> "
        f"{_language_name(output_language)} SRT"
    )
    digest = hashlib.sha1(
        f"{path}\0{plan['task']}\0{input_language}\0{output_language}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "provider": PROVIDER_ID,
        "id": f"whisperai-{digest}",
        "language": requested_language,
        "release_info": release_info,
        "filename": _filename(video, output_language),
        "matches": _matches(video),
        "score": 80,
        "score_without_hash": 80,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "display": {
            "source": "Whisper web service",
            "task": plan["task"],
            "input_language": input_language,
            "output_language": output_language,
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "path": path,
            "task": plan["task"],
            "input_language": input_language,
            "output_language": output_language,
            "audio_stream_language": plan.get("audio_stream_language"),
        },
    }


def _audio_languages(video):
    raw_languages = (video or {}).get("audio_languages") or []
    result = []
    for raw in raw_languages:
        normalized = normalize_language(raw)
        if normalized:
            result.append((str(raw), normalized))
    return result


def _language_payload(language):
    if isinstance(language, dict):
        payload = dict(language)
    else:
        payload = {"alpha3": str(language)}
    alpha3 = normalize_language(payload.get("alpha3") or payload.get("alpha2"))
    payload["alpha3"] = alpha3
    payload.setdefault("alpha2", ALPHA3_TO_ALPHA2.get(alpha3))
    payload["hi"] = False
    payload["forced"] = False
    return payload


def _require_config(config):
    config = dict(config or {})
    if not str(config.get("endpoint") or "").strip():
        raise ValueError("WhisperAI endpoint is required")
    if not str(config.get("ffmpeg_path") or "").strip():
        raise ValueError("WhisperAI ffmpeg_path is required")
    config["endpoint"] = str(config["endpoint"]).strip().rstrip("/")
    config["ffmpeg_path"] = str(config["ffmpeg_path"]).strip()
    config["response_timeout_seconds"] = _int_config(config, "response_timeout_seconds", 30)
    config["transcription_timeout_seconds"] = _int_config(config, "transcription_timeout_seconds", 600)
    config["pass_video_name"] = _as_bool(config.get("pass_video_name"))
    return config


def _int_config(config, key, default):
    try:
        value = int(config.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 86400))


def _video_path(video):
    for key in ("original_path", "path", "name"):
        value = (video or {}).get(key)
        if value:
            return str(value)
    return None


def _matches(video):
    if (video or {}).get("kind") == "episode":
        return ["series", "season", "episode"]
    return ["title"]


def _filename(video, output_language):
    path = _video_path(video) or (video or {}).get("title") or "whisperai"
    stem = os.path.splitext(os.path.basename(str(path)))[0] or "whisperai"
    return f"{stem}.{output_language}.srt"


def _language_name(alpha3):
    return LANGUAGE_NAMES.get(alpha3, alpha3 or "unknown")


def _ffmpeg_language_code(alpha3):
    normalized = normalize_language(alpha3) or str(alpha3 or "")
    return FFMPEG_LANGUAGE_CODES.get(normalized, normalized)


def _audio_stream_index(path, ffmpeg_path, audio_stream_language, timeout_seconds):
    ffprobe_path = _ffprobe_path(ffmpeg_path)
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index:stream_tags=language",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=int(timeout_seconds),
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise WhisperAIError(message or "ffprobe failed to inspect audio streams")
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WhisperAIError("ffprobe returned invalid audio stream JSON") from exc
    requested = normalize_language(audio_stream_language)
    for stream in payload.get("streams") or []:
        tags = stream.get("tags") if isinstance(stream, dict) else {}
        if normalize_language((tags or {}).get("language")) == requested:
            return int(stream["index"])
    raise WhisperAIError(f"no {_language_name(requested)} audio stream found")


def _ffprobe_path(ffmpeg_path):
    directory, filename = os.path.split(str(ffmpeg_path))
    executable = "ffprobe.exe" if filename.lower().endswith(".exe") else "ffprobe"
    return os.path.join(directory, executable) if directory else executable


def _download_response(content):
    return {
        "content_b64": base64.b64encode(content).decode("ascii") if content else "",
        "content_sha256": hashlib.sha256(content).hexdigest() if content else "",
        "content_type": "application/x-subrip",
        "format": "srt",
        "encoding": "utf-8" if content else None,
        "empty": not bool(content),
    }


def _multipart_body(params, files):
    boundary = "----BazarrProviderHub" + uuid.uuid4().hex
    chunks = []
    for name, value in (params or {}).items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, file_info in (files or {}).items():
        filename, content, content_type = file_info
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type or PCM_MIME_TYPE}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(content or b"")
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
