"""AnimeTosho.xyz provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import json
import lzma
import re
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDER_ID = "animetosho_xyz"
FEED_URL = "https://feed.animetosho.xyz"
HTTP_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_COMPRESSED_SUBTITLE_BYTES = 2 * 1024 * 1024
MAX_SUBTITLE_BYTES = 8 * 1024 * 1024
DEFAULT_SEARCH_THRESHOLD = 5
MAX_SEARCH_THRESHOLD = 50
DEFAULT_REQUEST_DELAY_MS = 0
MAX_REQUEST_DELAY_MS = 5000
USER_AGENT = "BazarrProviderHub"
SUBTITLE_FORMATS = {
    "ass": "ass",
    "ssa": "ssa",
    "srt": "srt",
    "subrip": "srt",
    "vtt": "vtt",
    "webvtt": "vtt",
    "sub": "sub",
}
CONTENT_TYPES = {
    "ass": "text/x-ssa",
    "ssa": "text/x-ssa",
    "srt": "application/x-subrip",
    "sub": "text/plain",
    "vtt": "text/vtt",
}
ALPHA3_TO_ALPHA2 = {
    "ara": "ar", "deu": "de", "eng": "en", "fin": "fi", "fra": "fr",
    "heb": "he", "ind": "id", "ita": "it", "jpn": "ja", "pol": "pl",
    "por": "pt", "rus": "ru", "spa": "es", "swe": "sv", "tha": "th",
    "tur": "tr", "vie": "vi",
}
ALPHA2_TO_ALPHA3 = {value: key for key, value in ALPHA3_TO_ALPHA2.items()}
BIBLIOGRAPHIC_TO_CANONICAL = {
    "alb": "sqi", "chi": "zho", "cze": "ces", "dut": "nld", "fre": "fra",
    "ger": "deu", "gre": "ell", "rum": "ron", "slo": "slk", "wel": "cym",
}
SUPPORTED_LANGUAGES = set(ALPHA3_TO_ALPHA2) | {
    "sqi", "zho", "ces", "nld", "ell", "ron", "slk", "cym"
}
HI_MARKER_RE = re.compile(
    r"(?:^|[\s_.\-\[(])(?:cc|sdh|hi|hearing[\s_.\-]*impaired)(?:$|[\s_.\-)\]])",
    re.IGNORECASE,
)
FORCED_MARKER_RE = re.compile(r"\bsigns?\b", re.IGNORECASE)
XZ_MAGIC = b"\xfd7zXZ\x00"
SXXEYY_RE = re.compile(r"\bs0*(\d{1,3})\s*e0*(\d{1,4})\b", re.IGNORECASE)
X_MARKER_RE = re.compile(r"\b(\d{1,3})x(\d{1,4})\b", re.IGNORECASE)
EPISODE_RE = re.compile(r"(?:^|[^a-z0-9])e(?:pisode)?[ ._-]*0*(\d{1,4})(?:[^0-9]|$)", re.IGNORECASE)


def _redirect_origin(url):
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
    ):
        return None
    return parts.scheme.lower(), parts.hostname.lower(), 443 if port is None else port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS redirects only when the destination stays on the same origin."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        if _redirect_origin(request.full_url) != _redirect_origin(new_url):
            return None
        return super().redirect_request(request, response, code, message, headers, new_url)


class AnimeToshoXYZProvider:
    """Use the XYZ feed endpoint and validated subtitle attachment URLs."""

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, max_bytes=MAX_RESPONSE_BYTES):
        _validate_url(url, allow_feed=True)
        try:
            max_bytes = int(max_bytes)
        except (TypeError, ValueError):
            max_bytes = MAX_RESPONSE_BYTES
        max_bytes = max(1, min(max_bytes, MAX_RESPONSE_BYTES))
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        opener = urllib.request.build_opener(_SameOriginRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("AnimeTosho.xyz response exceeded the size limit")
        return body

    def search(self, video, languages, config):
        video = video if isinstance(video, dict) else {}
        if video.get("kind") != "episode":
            return []
        episode_id = _positive_id(_scalar(video.get("series_anidb_episode_id")))
        if episode_id is None:
            return []
        requested = _requested_languages(languages)
        if not requested:
            return []
        config = config if isinstance(config, dict) else {}
        threshold = _bounded_int(
            config.get("search_threshold", DEFAULT_SEARCH_THRESHOLD),
            1,
            MAX_SEARCH_THRESHOLD,
            DEFAULT_SEARCH_THRESHOLD,
        )
        try:
            entries = parse_series_entries(
                self._http_get(series_feed_url(episode_id)),
                episode_id,
                search_threshold=threshold,
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return []
            raise RuntimeError(f"AnimeTosho.xyz feed returned HTTP {error.code}") from error
        except (OSError, ValueError) as error:
            if isinstance(error, ValueError) and "size limit" in str(error):
                raise
            raise RuntimeError("AnimeTosho.xyz feed request failed") from error

        delay_ms = _bounded_int(
            config.get("request_delay_ms", DEFAULT_REQUEST_DELAY_MS),
            0,
            MAX_REQUEST_DELAY_MS,
            DEFAULT_REQUEST_DELAY_MS,
        )
        results = []
        seen = set()
        for entry in entries:
            entry_id = _positive_id(entry.get("id"))
            if entry_id is None:
                continue
            if delay_ms:
                time.sleep(delay_ms / 1000.0)
            try:
                detail = self._http_get(torrent_feed_url(entry_id))
                detail_data = _load_json(detail)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    continue
                raise RuntimeError(f"AnimeTosho.xyz torrent lookup returned HTTP {error.code}") from error
            except (OSError, ValueError):
                continue
            if not isinstance(detail_data, dict):
                continue
            for row in parse_torrent_subtitles(detail_data, entry, video):
                language = row["language"]
                if not _language_matches_request(language, requested):
                    continue
                result = _result(video, row)
                if result["id"] in seen:
                    continue
                seen.add(result["id"])
                results.append(result)
        results.sort(key=lambda item: item["score"], reverse=True)
        return results

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload if isinstance(provider_payload, dict) else {}
        if payload.get("provider") not in (None, PROVIDER_ID):
            raise ValueError("AnimeTosho.xyz payload has the wrong provider")
        url = _validate_url(payload.get("download_url"), allow_feed=False)
        codec = str(payload.get("format") or "srt").lower()
        if codec not in CONTENT_TYPES:
            raise ValueError("AnimeTosho.xyz subtitle format is unsupported")
        body = self._http_get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            max_bytes=MAX_COMPRESSED_SUBTITLE_BYTES,
        )
        if len(body) > MAX_COMPRESSED_SUBTITLE_BYTES:
            raise ValueError("AnimeTosho.xyz compressed subtitle exceeded the size limit")
        if not body.startswith(XZ_MAGIC):
            raise ValueError("AnimeTosho.xyz returned an unidentified XZ archive")
        content = _bounded_xz_decompress(body)
        try:
            content.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "latin-1"
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_type": CONTENT_TYPES[codec],
            "format": codec,
            "encoding": encoding,
            "empty": not bool(content),
        }


def series_feed_url(episode_id):
    value = _positive_id(episode_id)
    if value is None:
        raise ValueError("AnimeTosho.xyz requires a valid AniDB episode id")
    return f"{FEED_URL}/feed/json?{urllib.parse.urlencode({'eid': value})}"


def torrent_feed_url(entry_id):
    value = _positive_id(entry_id)
    if value is None:
        raise ValueError("AnimeTosho.xyz requires a valid torrent id")
    return f"{FEED_URL}/json?{urllib.parse.urlencode({'show': 'torrent', 'id': value})}"


def parse_series_entries(body, expected_episode_id, search_threshold=DEFAULT_SEARCH_THRESHOLD):
    try:
        data = _load_json(body)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    expected_episode_id = _positive_id(expected_episode_id)
    if expected_episode_id is None:
        return []
    threshold = _bounded_int(search_threshold, 1, MAX_SEARCH_THRESHOLD, DEFAULT_SEARCH_THRESHOLD)
    entries = [
        item for item in data
        if isinstance(item, dict)
        and item.get("status") == "complete"
        and _positive_id(item.get("id")) is not None
        and _positive_id(item.get("anidb_eid")) == expected_episode_id
    ]
    entries.sort(key=lambda item: _int_or_zero(item.get("timestamp")), reverse=True)
    return entries[:threshold]


def parse_torrent_subtitles(detail, entry, video=None):
    if not isinstance(detail, dict) or not isinstance(entry, dict):
        return []
    entry_id = _positive_id(entry.get("id"))
    if entry_id is None:
        return []
    torrent_name = str(detail.get("torrent_name") or detail.get("title") or entry.get("title") or "")
    release_info = str(entry.get("title") or torrent_name).strip()
    attachment_rows = []
    attachments = detail.get("attachments")
    files = detail.get("files")
    associations = {}
    if isinstance(files, list):
        for file_data in files:
            if not isinstance(file_data, dict):
                continue
            associated_filename = file_data.get("filename") or file_data.get("file_name")
            if not associated_filename:
                continue
            for attached in file_data.get("attachments") or []:
                if not isinstance(attached, dict):
                    continue
                attachment_id = _positive_id(attached.get("id"))
                if attachment_id is not None:
                    associations.setdefault(attachment_id, []).append(str(associated_filename))

    if isinstance(attachments, list) and attachments:
        for attachment in attachments:
            if not isinstance(attachment, dict):
                attachment_rows.append((attachment, None))
                continue
            attachment_id = _positive_id(attachment.get("id"))
            related_filenames = associations.get(attachment_id, [])
            if related_filenames:
                attachment_rows.extend((attachment, name) for name in dict.fromkeys(related_filenames))
            else:
                attachment_rows.append((attachment, None))
    elif isinstance(files, list):
        for file_data in files:
            if not isinstance(file_data, dict):
                continue
            attachment_rows.extend(
                (attachment, file_data.get("filename") or file_data.get("file_name"))
                for attachment in file_data.get("attachments") or []
            )
    rows = []
    for attachment, associated_filename in attachment_rows:
        if not isinstance(attachment, dict) or attachment.get("type") not in ("subtitle", 1, "1"):
            continue
        attachment_filename = str(attachment.get("filename") or attachment.get("file_name") or "")
        associated_filename = str(associated_filename or "")
        match_filename = _file_match_filename(associated_filename, attachment_filename)
        if not _file_matches_episode(video, match_filename):
            continue
        filename = attachment_filename
        info = attachment.get("info") if isinstance(attachment.get("info"), dict) else {}
        language = _language_payload(info)
        if language is None:
            continue
        codec = _format_from_attachment(info)
        url = _validate_url(attachment.get("url"), allow_feed=False, reject_invalid=True)
        if not codec or not url:
            continue
        track_name = str(info.get("name") or info.get("language") or "")
        language["forced"] = _as_bool(info.get("forced")) or bool(FORCED_MARKER_RE.search(track_name))
        language["hi"] = bool(HI_MARKER_RE.search(track_name))
        label = _track_label(track_name, language["alpha3"])
        row_release = release_info or torrent_name or str(entry_id)
        if label:
            row_release = f"[{label}] {row_release}"
        size = _int_or_zero(attachment.get("size"))
        rows.append(
            {
                "entry_id": entry_id,
                "attachment_id": _positive_id(attachment.get("id")),
                "download_url": url,
                "torrent_name": torrent_name,
                "release_info": row_release,
                "filename": filename or torrent_name or f"{entry_id}.{codec}",
                "language": language,
                "format": codec,
                "size_bytes": size,
            }
        )
    return rows


def _file_match_filename(associated_filename, attachment_filename):
    if associated_filename and _has_episode_marker(associated_filename):
        return associated_filename
    if attachment_filename and _has_episode_marker(attachment_filename):
        return attachment_filename
    return associated_filename or attachment_filename


def _has_episode_marker(filename):
    return bool(SXXEYY_RE.search(filename) or X_MARKER_RE.search(filename) or EPISODE_RE.search(filename))


def _file_matches_episode(video, filename):
    if not isinstance(video, dict) or video.get("kind") != "episode" or not filename:
        return True
    expected_episodes = {
        value for value in (
            _positive_id(_scalar(video.get("episode"))),
            _positive_id(_scalar(video.get("series_anidb_episode_no"))),
        ) if value is not None
    }
    if not expected_episodes:
        return True
    expected_season = _season_number(_scalar(video.get("season")))
    match = SXXEYY_RE.search(filename)
    if match:
        return (
            int(match.group(2)) in expected_episodes
            and (expected_season is None or int(match.group(1)) == expected_season)
        )
    match = X_MARKER_RE.search(filename)
    if match:
        return (
            int(match.group(2)) in expected_episodes
            and (expected_season is None or int(match.group(1)) == expected_season)
        )
    match = EPISODE_RE.search(filename)
    if match:
        return int(match.group(1)) in expected_episodes
    return True


def _result(video, row):
    language = dict(row["language"])
    identity = ":".join(
        str(value or "")
        for value in (
            row.get("entry_id"),
            row["download_url"],
            language["alpha3"],
            language.get("country_alpha2"),
            language.get("forced"),
            language.get("hi"),
        )
    )
    result_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    matches = ["series", "season", "episode"]
    score = 96
    return {
        "provider": PROVIDER_ID,
        "id": f"animetosho-xyz-{result_id}",
        "language": language,
        "release_info": row["release_info"],
        "filename": row["filename"],
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": bool(language["hi"]),
        "page_link": row["download_url"],
        "format": row["format"],
        "display": {
            "source": "AnimeTosho.xyz",
            "title": row["release_info"],
            "release": row.get("torrent_name") or row["filename"],
            "size_bytes": row.get("size_bytes", 0),
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "entry_id": row["entry_id"],
            "subtitle_id": row.get("attachment_id"),
            "download_url": row["download_url"],
            "format": row["format"],
            "language": language,
        },
    }


def _load_json(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("AnimeTosho.xyz response exceeded the size limit")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("AnimeTosho.xyz returned invalid JSON") from error


def _language_payload(info):
    code = str(info.get("language_code") or info.get("lang") or "eng").strip().lower().replace("_", "-")
    name = str(info.get("language") or info.get("name") or "")
    country = None
    if code in {"pt-br", "por-br"}:
        alpha3, country = "por", "BR"
    elif code in {"pt-pt", "por-pt"}:
        alpha3, country = "por", "PT"
    elif code in {"es-419", "spa-mx", "spa-la"}:
        alpha3, country = "spa", "MX"
    elif code in {"es-es", "spa-es"}:
        alpha3, country = "spa", "ES"
    else:
        base = code.split("-", 1)[0]
        alpha3 = ALPHA2_TO_ALPHA3.get(base, BIBLIOGRAPHIC_TO_CANONICAL.get(base, base))
    normalized_name = name.lower()
    if alpha3 == "por" and ("brazil" in normalized_name or "brasil" in normalized_name):
        country = "BR"
    elif alpha3 == "por" and "portugal" in normalized_name:
        country = "PT"
    elif alpha3 == "spa" and any(term in normalized_name for term in ("latin america", "latin american", "latam")):
        country = "MX"
    elif alpha3 == "spa" and any(term in normalized_name for term in ("spain", "castilian")):
        country = "ES"
    if alpha3 not in SUPPORTED_LANGUAGES:
        return None
    return {
        "alpha3": alpha3,
        "alpha2": ALPHA3_TO_ALPHA2.get(alpha3),
        "country_alpha2": country,
        "hi": False,
        "forced": False,
    }


def _requested_languages(languages):
    requested = []
    for item in languages or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("alpha3") or item.get("alpha2") or "").lower().replace("_", "-")
        language = _language_payload({"language_code": code})
        if language is None:
            continue
        country = item.get("country_alpha2") or item.get("country") or item.get("region")
        if country:
            language["country_alpha2"] = str(country).upper()
        language["forced"] = _optional_bool(item, "forced")
        language["hi"] = _optional_bool(item, "hi", "hearing_impaired")
        requested.append(language)
    return requested


def _language_matches_request(language, requested):
    for item in requested:
        if language["alpha3"] != item["alpha3"]:
            continue
        country = item.get("country_alpha2")
        if country and language.get("country_alpha2") != country:
            continue
        for flag in ("forced", "hi"):
            value = item.get(flag)
            if value is not None and bool(language.get(flag)) != value:
                break
        else:
            return True
    return False


def _format_from_attachment(info):
    codec = str(info.get("codec") or "srt").lower()
    return SUBTITLE_FORMATS.get(codec)


def _track_label(name, alpha3):
    label = str(name or "").strip().strip("[]() ")
    language_names = {
        "ara": "arabic", "deu": "german", "eng": "english", "fin": "finnish",
        "fra": "french", "heb": "hebrew", "ind": "indonesian", "ita": "italian",
        "jpn": "japanese", "pol": "polish", "por": "portuguese", "rus": "russian",
        "spa": "spanish", "swe": "swedish", "tha": "thai", "tur": "turkish",
        "vie": "vietnamese", "zho": "chinese",
    }
    if not label or label.lower().startswith(language_names.get(alpha3, "\0")):
        return None
    return label


def _validate_url(value, allow_feed, reject_invalid=False):
    if not isinstance(value, str) or not value:
        if reject_invalid:
            return None
        raise ValueError("AnimeTosho.xyz URL is missing")
    try:
        parts = urllib.parse.urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        if reject_invalid:
            return None
        raise ValueError("AnimeTosho.xyz URL is malformed") from None
    is_feed = host == "feed.animetosho.xyz"
    is_download = (
        host != "feed.animetosho.xyz"
        and (
            host == "animetosho.xyz"
            or host.endswith(".animetosho.xyz")
            or host == "animetosho.org"
            or host.endswith(".animetosho.org")
        )
    )
    valid = (
        parts.scheme == "https"
        and not parts.username
        and not parts.password
        and port in (None, 443)
        and not parts.fragment
        and ((allow_feed and is_feed) or is_download)
    )
    if not valid:
        if reject_invalid:
            return None
        raise ValueError("AnimeTosho.xyz URL is outside the provider endpoints")
    return value


def _optional_bool(mapping, key, fallback=None):
    if key in mapping:
        value = mapping.get(key)
    elif fallback and fallback in mapping:
        value = mapping.get(fallback)
    else:
        return None
    return _as_bool(value)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_xz_decompress(body):
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    chunks = []
    total = 0
    pending = body
    try:
        while True:
            chunk = decoder.decompress(pending, max_length=MAX_SUBTITLE_BYTES - total + 1)
            pending = b""
            total += len(chunk)
            if total > MAX_SUBTITLE_BYTES:
                raise ValueError("AnimeTosho.xyz subtitle exceeded the decompressed size limit")
            chunks.append(chunk)
            if decoder.eof:
                if decoder.unused_data:
                    raise ValueError("AnimeTosho.xyz subtitle contains trailing compressed data")
                return b"".join(chunks)
            if decoder.needs_input:
                raise ValueError("AnimeTosho.xyz subtitle XZ stream is truncated")
    except lzma.LZMAError as error:
        raise ValueError("AnimeTosho.xyz subtitle decompression failed") from error


def _positive_id(value):
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{1,18}", text):
        return None
    result = int(text)
    return result if result > 0 else None


def _season_number(value):
    if isinstance(value, bool):
        return None
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    return int(text)


def _scalar(value):
    if isinstance(value, (list, tuple)):
        return value[-1] if value else None
    return value


def _bounded_int(value, minimum, maximum, default):
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(number, maximum))


def _int_or_zero(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0
