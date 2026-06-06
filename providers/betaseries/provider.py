"""BetaSeries provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "betaseries"
BASE_URL = "https://api.betaseries.com"
HTTP_TIMEOUT_SECONDS = 10
USER_AGENT = "Sub-Zero/2"
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
LANGUAGES = {
    "vo": {"alpha3": "eng", "alpha2": "en"},
    "vf": {"alpha3": "fra", "alpha2": "fr"},
}
_SXXEYY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\s*e0*(?P<episode>\d{1,3})\b", re.I)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


class BetaSeriesProvider:
    def search(self, video, languages, config):
        token = _token(config)
        video = dict(video or {})
        if video.get("kind") != "episode":
            return []
        requested = {_alpha3_for_language(language) for language in languages or []}
        requested = {language for language in requested if language in {"eng", "fra"}}
        if not requested:
            return []
        url, base_matches = build_search_url(video, token)
        if not url:
            return []
        payload = self._http_get_json(url, config=config)
        if handle_api_errors(payload) == "empty":
            return []
        subtitles = _subtitle_rows(payload)
        results = []
        seen = set()
        for row in subtitles:
            language = LANGUAGES.get(str(row.get("language") or "").lower())
            if not language or language["alpha3"] not in requested:
                continue
            if str(row.get("source")) == "seriessub":
                continue
            result = self._result(video, row, language, base_matches)
            key = (result["provider_payload"]["subtitle_id"], result["language"]["alpha3"])
            if key in seen:
                continue
            seen.add(key)
            results.append(result)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("download_url")
        if not url:
            raise ValueError("betaseries download requires download_url")
        try:
            body = self._http_get_bytes(url)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return _content_payload(b"", "srt", empty=True)
            raise
        return _download_payload(body, payload)

    def _http_get_json(self, url, timeout=HTTP_TIMEOUT_SECONDS, config=None):
        try:
            body = self._http_get_bytes(url, timeout=timeout, config=config)
        except urllib.error.HTTPError as error:
            payload = _decode_api_error(error)
            if payload is None:
                raise
            return payload
        return json.loads(body.decode("utf-8"))

    def _http_get_bytes(self, url, timeout=HTTP_TIMEOUT_SECONDS, config=None):
        del config
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": os.environ.get("SZ_USER_AGENT", USER_AGENT),
                "Accept": "application/json,*/*",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _result(self, video, row, language, base_matches):
        release = str(row.get("file") or "")
        matches = sorted(set(base_matches) | set(derive_matches(video, release)))
        score = 95 if "episode" in matches else 80
        subtitle_id = str(row.get("id"))
        filename = os.path.basename(urllib.parse.urlparse(str(row.get("url") or "")).path)
        if not filename:
            filename = f"betaseries.{_slug(release)}.{language['alpha2']}.srt"
        return {
            "provider": PROVIDER_ID,
            "id": f"betaseries-{subtitle_id}-{language['alpha3']}",
            "language": {
                "alpha3": language["alpha3"],
                "alpha2": language["alpha2"],
                "hi": False,
                "forced": False,
            },
            "release_info": release,
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": row.get("url"),
            "display": {
                "source": "betaseries",
                "release": release,
                "origin": row.get("source"),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": subtitle_id,
                "download_url": row.get("url"),
                "filename": filename,
                "release_group": video.get("release_group"),
                "season": _safe_int(video.get("season")),
                "episode": _safe_int(video.get("episode")),
            },
        }


def build_search_url(video, token):
    params = {"key": token, "v": 3.0, "subtitles": 1}
    if video.get("tvdb_id"):
        params["thetvdb_id"] = video["tvdb_id"]
        return f"{BASE_URL}/episodes/display?{urllib.parse.urlencode(params)}", {"tvdb_id"}
    if video.get("series_tvdb_id"):
        params["thetvdb_id"] = video["series_tvdb_id"]
        params["season"] = video.get("season")
        params["episode"] = video.get("episode")
        return f"{BASE_URL}/shows/episodes?{urllib.parse.urlencode(params)}", {"series_tvdb_id"}
    return None, set()


def _decode_api_error(error):
    """Return the BetaSeries JSON error body so handle_api_errors can run.

    BetaSeries reports documented API errors (no series found, invalid token)
    with HTTP 400 and a JSON body, which urllib raises as HTTPError before the
    body is read. Decode that body when it is a real API error and return None
    for genuine transport failures so they keep propagating.
    """
    try:
        body = error.read()
    except (OSError, ValueError):
        return None
    finally:
        error.close()
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("errors"), list):
        return payload
    return None


def handle_api_errors(payload):
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not errors:
        return None
    first = errors[0] if isinstance(errors, list) and errors else {}
    code = first.get("code")
    if code == 4001:
        return "empty"
    if code == 1001:
        raise ValueError("Invalid token provided")
    return "empty"


def _download_payload(body, payload=None):
    payload = payload or {}
    # Reject broken responses: a 200 with an empty stream or an HTML/error page
    # would otherwise be forwarded as if it were a usable subtitle.
    if not body or not body.strip():
        raise ValueError(f"betaseries empty download for subtitle {payload.get('subtitle_id')}")
    if _is_html_body(body):
        raise ValueError(f"betaseries returned an HTML/error page for subtitle {payload.get('subtitle_id')}")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host. When we can cheaply list a zip, pin the member matching the scored
        # release_group so the host downloads that release instead of guessing by episode;
        # otherwise (rar, or no release_group match) let the host pick the member by episode.
        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        member = _select_zip_member(body, payload)
        if member is not None:
            archive["member"] = member
        else:
            archive["episode"] = payload.get("episode")
        return archive
    # Direct, non-archive subtitle body.
    return _content_payload(_normalize_line_endings(body), _subtitle_extension(payload.get("filename")) or "srt")


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _select_zip_member(body, payload):
    # Name the zip member matching the scored release_group. Listing only, no extraction
    # or decoding: the host reads the named member and runs chardet. Returns None for rar
    # (not stdlib-listable), no release_group, or no match, so the caller falls back to
    # host-side episode selection.
    release_group = str((payload or {}).get("release_group") or "")
    if not release_group or not zipfile.is_zipfile(io.BytesIO(body)):
        return None
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = [
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and _subtitle_extension(name)
            and not os.path.basename(name).startswith(".")
        ]
    for name in names:
        if release_group in name:
            return name
    return None


def _is_html_body(body):
    if not body:
        return False
    head = body[:1024].lstrip().lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
        or head.startswith(b"<!--")
        or b"<body" in head
        or b"<head" in head
    )


def derive_matches(video, release):
    video = video or {}
    release_tokens = set(_tokens(release))
    matches = []
    series_tokens = _tokens(video.get("series"))
    if series_tokens and all(token in release_tokens for token in series_tokens):
        matches.append("series")
    season = _safe_int(video.get("season"))
    episode = _safe_int(video.get("episode"))
    if season is not None and _text_has_season(release, season):
        matches.append("season")
    if season is not None and episode is not None and _text_has_episode(release, season, episode):
        matches.append("episode")
    return matches


def _subtitle_rows(payload):
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("episode"), dict):
        subtitles = payload["episode"].get("subtitles")
        return subtitles if isinstance(subtitles, list) else []
    episodes = payload.get("episodes")
    if isinstance(episodes, list) and episodes and isinstance(episodes[0], dict):
        subtitles = episodes[0].get("subtitles")
        return subtitles if isinstance(subtitles, list) else []
    return []


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _alpha3_for_language(language):
    if isinstance(language, str):
        code = language
    elif isinstance(language, dict):
        code = language.get("alpha3") or language.get("alpha2") or ""
    else:
        code = ""
    code = str(code).lower()
    if code == "en":
        return "eng"
    if code == "fr":
        return "fra"
    return code


def _token(config):
    token = str((config or {}).get("token") or "").strip()
    if not token:
        raise ValueError("betaseries token must be specified")
    return token


def _normalize_line_endings(body):
    return (body or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _text_has_episode(text, season, episode):
    for match in _SXXEYY_RE.finditer(_normalize(text)):
        if _safe_int(match.group("season")) == season and _safe_int(match.group("episode")) == episode:
            return True
    return False


def _text_has_season(text, season):
    return bool(re.search(rf"\bs0*{season}\b", _normalize(text)))


def _content_payload(body, extension, empty=False):
    data = body or b""
    return {
        "content_b64": base64.b64encode(data).decode("ascii"),
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "format": (extension or "srt").lstrip(".").lower(),
        "empty": bool(empty),
    }


def _subtitle_extension(name):
    lower_name = str(name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower_name.endswith(extension):
            return extension.lstrip(".")
    return None


def _slug(value, max_length=80):
    slug = "-".join(_tokens(value))
    return (slug[:max_length].strip("-") or "subtitle")


def _tokens(value):
    normalized = _normalize(value)
    return [token for token in _NON_ALNUM_RE.split(normalized) if token]


def _normalize(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return _WS_RE.sub(" ", value.lower()).strip()


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
