"""Napisy24 provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "napisy24"
API_URL = "https://napisy24.pl/run/CheckSubAgent.php"
DEFAULT_USERNAME = "subliminal"
DEFAULT_PASSWORD = "lanimilbus"
USER_AGENT = "Subliminal/2 BazarrProviderHub"
HTTP_TIMEOUT_SECONDS = 10
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt", ".txt")
LANGUAGE = {"alpha3": "pol", "alpha2": "pl", "hi": False, "forced": False}
_NON_ALNUM_RE = re.compile(r"[\W_]+")
_OPENSUBTITLES_HASH_READ_SIZE = 64 * 1024


class HttpResponse:
    def __init__(self, status, body, headers):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})


class Napisy24Provider:
    def search(self, video, languages, config):
        video = dict(video or {})
        if not _language_requested(languages):
            return []
        lookup = _lookup_inputs(video)
        if lookup is None:
            return []
        username, password = _credentials(config)
        data = _lookup_request_data(lookup, username, password)
        _sleep(config)
        response = self._http_post(
            API_URL,
            data,
            headers={
                "User-Agent": str((config or {}).get("user_agent") or USER_AGENT),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        _raise_for_status(response, "Napisy24 lookup")
        parsed = parse_response(response.body)
        if parsed["status"] == "login error":
            raise PermissionError("Napisy24 Login failed")
        if parsed["status"] in {"OK-0", "OK-1", "OK-3"}:
            return []
        if parsed["status"] != "OK-2":
            return []
        return [_candidate(video, lookup, parsed)]

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        archive_b64 = payload.get("archive_b64")
        if archive_b64:
            try:
                archive_body = base64.b64decode(archive_b64)
            except Exception as error:
                raise ValueError("napisy24 archive_b64 is invalid") from error
        else:
            lookup = _lookup_from_payload(payload)
            username, password = _credentials(config)
            response = self._http_post(
                API_URL,
                _lookup_request_data(lookup, username, password),
                headers={
                    "User-Agent": str((config or {}).get("user_agent") or USER_AGENT),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            _raise_for_status(response, "Napisy24 download lookup")
            parsed = parse_response(response.body)
            if parsed["status"] == "login error":
                raise PermissionError("Napisy24 Login failed")
            if parsed["status"] != "OK-2" or not parsed["archive"]:
                raise ValueError("napisy24 download lookup did not return an archive")
            archive_body = parsed["archive"]
        return extract_download(archive_body, payload.get("filename") or "")

    def _http_post(self, url, data, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=dict(headers or {}), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.status, response.read(), dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read(), dict(error.headers.items()))


def parse_response(body):
    metadata, archive = _split_response(body)
    metadata_text = metadata.decode("utf-8", errors="replace")
    if metadata_text.startswith("login error"):
        return {"status": "login error", "metadata": {}, "archive": b""}
    status = metadata_text[:4] if metadata_text.startswith("OK-") else metadata_text.split("|", 1)[0]
    fields = {}
    for part in metadata_text.split("|")[1:]:
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        fields[key] = value
    return {"status": status, "metadata": fields, "archive": archive}


def extract_download(archive_body, filename=""):
    if not archive_body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    stream = io.BytesIO(archive_body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = _first_subtitle_file(archive.namelist())
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    return _content_payload(archive_body, _format_from_filename(filename))


def _candidate(video, lookup, parsed):
    metadata = parsed["metadata"]
    napis_id = metadata.get("napisId") or lookup["hash"]
    imdb_id = _imdb_id(metadata.get("imdb"))
    matches = ["hash"]
    if imdb_id and _normalize_imdb(video.get("imdb_id")) == imdb_id:
        matches.append("imdb_id")
    filename = f"napisy24.{napis_id}.zip"
    score = 70 + (20 if "hash" in matches else 0) + (10 if "imdb_id" in matches else 0)
    return {
        "provider": PROVIDER_ID,
        "id": f"napisy24-{napis_id}",
        "language": dict(LANGUAGE),
        "release_info": "",
        "filename": filename,
        "matches": matches,
        "score": min(score, 100),
        "score_without_hash": 10 if "imdb_id" in matches else 0,
        "score_out_of": 100,
        "hash_verifiable": True,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": "https://napisy24.pl/",
        "display": {
            "source": "napisy24.pl",
            "napis_id": napis_id,
            "imdb_id": imdb_id,
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "napis_id": napis_id,
            "hash": lookup["hash"],
            "size": str(lookup["size"]),
            "name": os.path.basename(lookup["name"]),
            "imdb_id": imdb_id,
            "filename": filename,
            "language": "pol",
        },
    }


def _split_response(body):
    if not body:
        return b"", b""
    if b"||" not in body:
        return body, b""
    return body.split(b"||", 1)


def _lookup_inputs(video):
    hashes = video.get("hashes") or {}
    file_hash = str(
        hashes.get("napisy24")
        or hashes.get("opensubtitles")
        or hashes.get("opensubtitlescom")
        or ""
    ).strip()
    path = _existing_video_path(video)
    computed_size = None
    if not file_hash:
        computed = _compute_opensubtitles_hash(path)
        if not computed:
            return None
        file_hash, computed_size = computed
    size = video.get("size")
    if size in (None, "") and computed_size is not None:
        size = computed_size
    if size in (None, "") and path:
        size = os.path.getsize(path)
    name = video.get("name")
    if not name and path:
        name = path
    if size in (None, "") or not name:
        return None
    return {"hash": file_hash, "size": size, "name": str(name)}


def _lookup_from_payload(payload):
    file_hash = str((payload or {}).get("hash") or "").strip()
    size = (payload or {}).get("size")
    name = str((payload or {}).get("name") or (payload or {}).get("filename") or "").strip()
    if not file_hash or size in (None, "") or not name:
        raise ValueError("napisy24 download requires hash, size, and name")
    return {"hash": file_hash, "size": size, "name": name}


def _lookup_request_data(lookup, username, password):
    return {
        "postAction": "CheckSub",
        "ua": username,
        "ap": password,
        "fs": str(lookup["size"]),
        "fh": lookup["hash"],
        "fn": os.path.basename(lookup["name"]),
        "n24pref": "1",
    }


def _existing_video_path(video):
    for key in ("name", "original_path", "original_name"):
        value = str((video or {}).get(key) or "").strip()
        if value and os.path.isfile(value):
            return value
    return None


def _compute_opensubtitles_hash(path):
    if not path:
        return None
    size = os.path.getsize(path)
    read_size = _OPENSUBTITLES_HASH_READ_SIZE
    if size < read_size * 2:
        return None
    total = size
    with open(path, "rb") as handle:
        for offset in (0, size - read_size):
            handle.seek(offset)
            chunk = handle.read(read_size)
            usable = len(chunk) - (len(chunk) % 8)
            if usable:
                total += sum(struct.unpack(f"<{usable // 8}Q", chunk[:usable]))
    return f"{total & 0xFFFFFFFFFFFFFFFF:016x}", size


def _credentials(config):
    username = str((config or {}).get("username") or "").strip()
    password = str((config or {}).get("password") or "")
    if username and password:
        return username, password
    return DEFAULT_USERNAME, DEFAULT_PASSWORD


def _language_requested(languages):
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        if language.get("hi") or language.get("forced"):
            continue
        if str(language.get("alpha3") or "").lower() == "pol":
            return True
        if str(language.get("alpha2") or "").lower() == "pl":
            return True
    return False


def _imdb_id(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("tt"):
        digits = text[2:]
    else:
        digits = text
    if not digits.isdigit():
        return ""
    return f"tt{digits.zfill(7)}"


def _normalize_imdb(value):
    text = str(value or "").strip()
    if text.startswith("tt") and text[2:].isdigit():
        return f"tt{text[2:].zfill(7)}"
    return _imdb_id(text)


def _raise_for_status(response, context):
    if response.status >= 400:
        raise RuntimeError(f"{context}: HTTP {response.status}")


def _first_subtitle_file(names):
    subtitle_names = [name for name in names if _subtitle_extension(name) and not os.path.basename(name).startswith(".")]
    if subtitle_names:
        priority = {"srt": 0, "ass": 1, "ssa": 2, "vtt": 3, "sub": 4, "txt": 5}
        return sorted(
            subtitle_names,
            key=lambda name: (priority.get(_subtitle_extension(name), 99), name.lower()),
        )[0]
    if names:
        return names[0]
    raise ValueError("napisy24 archive contains no files")


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


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _normalize_line_endings(content):
    return (content or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _slug(value):
    text = html.unescape(str(value or ""))
    folded = _NON_ALNUM_RE.sub(" ", text.lower()).strip()
    return "-".join(token for token in folded.split() if token) or "subtitle"
