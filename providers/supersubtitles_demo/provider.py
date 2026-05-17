import base64
import hashlib

from humanfriendly import format_size

PROVIDER_ID = "supersubtitles_demo"
_SUBTITLE_ID = "supersubtitles-demo-fixed"
_SRT_TEXT = """1
00:00:01,000 --> 00:00:03,000
SuperSubtitles demo subtitle from Provider Hub.
"""


def _language_payload(language):
    if isinstance(language, dict):
        payload = dict(language)
    else:
        payload = {"alpha3": str(language)}
    payload.setdefault("alpha3", payload.get("alpha2") or "eng")
    payload.setdefault("hi", False)
    payload.setdefault("forced", False)
    return payload


def _require_config(config):
    config = dict(config or {})
    base_url = str(config.get("base_url") or "").strip()
    username = str(config.get("username") or "").strip()
    api_token = str(config.get("api_token") or "").strip()
    search_mode = str(config.get("search_mode") or "balanced").strip()
    if not base_url:
        raise ValueError("base_url is required")
    if not username:
        raise ValueError("username is required")
    if not api_token:
        raise ValueError("api_token is required")
    if search_mode not in {"strict", "balanced", "broad"}:
        raise ValueError("search_mode must be strict, balanced, or broad")
    return {
        "base_url": base_url.rstrip("/"),
        "username": username,
        "search_mode": search_mode,
        "use_hash": bool(config.get("use_hash", True)),
    }


class SuperSubtitlesDemoProvider:
    def search(self, video, languages, config):
        public_config = _require_config(config)
        video = video or {}
        kind = video.get("kind") or "movie"
        language = next(
            (_language_payload(item) for item in languages or [] if _language_payload(item).get("alpha3") in {"eng", "hun"}),
            None,
        )
        if language is None:
            return []

        title = video.get("title") or video.get("series") or video.get("name") or "Demo Title"
        matches = ["title"] if kind == "movie" else ["series", "season", "episode"]
        matches.append("hash" if public_config["use_hash"] else "name")
        dependency_marker = format_size(1536, binary=True)
        return [
            {
                "provider": PROVIDER_ID,
                "id": _SUBTITLE_ID,
                "language": language,
                "release_info": f"SuperSubtitles.Demo.{public_config['search_mode']}.{title}",
                "filename": "supersubtitles-demo.srt",
                "matches": matches,
                "score": 100,
                "score_without_hash": 95,
                "score_out_of": 100,
                "hash_verifiable": public_config["use_hash"],
                "hearing_impaired_verifiable": True,
                "hearing_impaired": False,
                "display": {
                    "source": "supersubtitles-demo",
                    "base_url": public_config["base_url"],
                    "dependency": dependency_marker,
                },
                "provider_payload": {
                    "provider": PROVIDER_ID,
                    "schema": 1,
                    "subtitle_id": _SUBTITLE_ID,
                    "base_url": public_config["base_url"],
                    "search_mode": public_config["search_mode"],
                },
            }
        ]

    def download(self, provider_payload, language, config):
        del language
        public_config = _require_config(config)
        if (provider_payload or {}).get("subtitle_id") != _SUBTITLE_ID:
            raise ValueError("unknown SuperSubtitles demo subtitle")
        if (provider_payload or {}).get("base_url") != public_config["base_url"]:
            raise ValueError("base_url mismatch")
        content = _SRT_TEXT.encode("utf-8")
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_type": "application/x-subrip",
            "format": "srt",
            "encoding": "utf-8",
            "empty": False,
        }
