import base64
import hashlib
import json
import os
from urllib import parse, request


PROVIDER_ID = "shooter"
SHOOTER_API_URL = "https://www.shooter.cn/api/subapi.php"
_LANGUAGE_TO_SHOOTER = {
    "eng": "eng",
    "zho": "chn",
}
_SUPPORTED_MEDIA = {"movie", "episode"}
_DEFAULT_TIMEOUT = 10


class ShooterHttpClient:
    def __init__(self, timeout=_DEFAULT_TIMEOUT):
        self.timeout = int(timeout)

    def post(self, url, params):
        query = parse.urlencode(params)
        target = f"{url}?{query}"
        req = request.Request(
            target,
            data=b"",
            method="POST",
            headers={
                "Accept": "application/json",
                "User-Agent": "BazarrProviderHub/1.0",
            },
        )
        with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
            return response.read()

    def get(self, url):
        parsed = parse.urlparse(str(url))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Shooter download_url must be http or https")
        req = request.Request(
            str(url),
            headers={"User-Agent": "BazarrProviderHub/1.0"},
        )
        with request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
            return response.read()


def _language_payload(language):
    payload = dict(language or {}) if isinstance(language, dict) else {"alpha3": str(language)}
    payload.setdefault("alpha3", payload.get("alpha2") or "eng")
    payload.setdefault("hi", False)
    payload.setdefault("forced", False)
    return payload


def _video_name(video):
    video = video or {}
    name = video.get("name") or video.get("original_path") or video.get("original_name")
    if name:
        return str(name)
    if video.get("kind") == "episode":
        series = video.get("series") or "episode"
        season = int(video.get("season") or 0)
        episode = int(video.get("episode") or 0)
        return f"{series}.S{season:02d}E{episode:02d}.mkv"
    title = video.get("title") or "movie"
    year = video.get("year")
    return f"{title}.{year}.mkv" if year else f"{title}.mkv"


def _shooter_hash(video):
    hashes = (video or {}).get("hashes") or {}
    value = hashes.get("shooter")
    value = str(value).strip() if value is not None else ""
    return value or None


def _candidate_id(download_url, filehash, language_code):
    digest = hashlib.sha1(f"{filehash}\0{language_code}\0{download_url}".encode("utf-8")).hexdigest()
    return f"shooter-{digest[:16]}"


def _filename_from_url(download_url, language_code):
    path = parse.urlparse(download_url).path
    basename = os.path.basename(path) or f"shooter.{language_code}.srt"
    if "." not in basename:
        return f"{basename}.{language_code}.srt"
    return basename


def _format_from_filename(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "srt"
    return ext if ext in {"srt", "ass", "ssa", "sub", "vtt"} else "srt"


def _parse_search_response(body):
    if body == b"\xff":
        return []
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Shooter response is not valid JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("Shooter response must be a list")

    links = []
    for group in payload:
        if not isinstance(group, dict):
            continue
        files = group.get("Files") or []
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            link = item.get("Link")
            if isinstance(link, str) and link.startswith(("http://", "https://")):
                links.append(link)
    return links


def _normalize_line_endings(body):
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _download_response(body, fmt):
    normalized = _normalize_line_endings(body)
    return {
        "content_b64": base64.b64encode(normalized).decode("ascii"),
        "content_sha256": hashlib.sha256(normalized).hexdigest(),
        "content_type": "application/x-subrip",
        "format": fmt or "srt",
        "encoding": None,
        "empty": False,
    }


class ShooterProvider:
    def __init__(self, http_client=None):
        self.http_client = http_client or ShooterHttpClient()

    def search(self, video, languages, config):
        del config
        video = video or {}
        if video.get("kind") not in _SUPPORTED_MEDIA:
            return []

        filehash = _shooter_hash(video)
        if not filehash:
            return []

        candidates = []
        for item in languages or []:
            language = _language_payload(item)
            alpha3 = language.get("alpha3")
            if language.get("hi") or language.get("forced"):
                continue
            shooter_language = _LANGUAGE_TO_SHOOTER.get(alpha3)
            if not shooter_language:
                continue
            params = {
                "filehash": filehash,
                "pathinfo": os.path.realpath(_video_name(video)),
                "format": "json",
                "lang": shooter_language,
            }
            body = self.http_client.post(SHOOTER_API_URL, params)
            for download_url in _parse_search_response(body):
                filename = _filename_from_url(download_url, alpha3)
                candidates.append(
                    {
                        "provider": PROVIDER_ID,
                        "id": _candidate_id(download_url, filehash, alpha3),
                        "language": language,
                        "release_info": filehash,
                        "filename": filename,
                        "matches": ["hash"],
                        "score": 100,
                        "score_without_hash": 0,
                        "score_out_of": 100,
                        "hash_verifiable": True,
                        "hearing_impaired_verifiable": False,
                        "hearing_impaired": bool(language.get("hi", False)),
                        "display": {
                            "source": "shooter.cn",
                            "shooter_hash": filehash,
                        },
                        "provider_payload": {
                            "provider": PROVIDER_ID,
                            "schema": 1,
                            "download_url": download_url,
                            "filehash": filehash,
                            "language": alpha3,
                            "format": _format_from_filename(filename),
                        },
                    }
                )
        return candidates

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        if payload.get("provider") != PROVIDER_ID:
            raise ValueError("Shooter provider payload has wrong provider")
        download_url = str(payload.get("download_url") or "").strip()
        if not download_url:
            raise ValueError("Shooter download_url is required")
        body = self.http_client.get(download_url)
        return _download_response(body, payload.get("format") or _format_from_filename(download_url))
