import base64
import hashlib

PROVIDER_ID = "smokehub"
_SUBTITLE_ID = "smokehub-fixed-eng"
_PROFILE_KEY = "profile_name"
_TOKEN_KEY = "api_token"
_SRT_TEXT = """1
00:00:01,000 --> 00:00:02,500
SmokeHub deterministic subtitle.
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


def _format_dependency_marker():
    try:
        from humanfriendly import format_size
    except ImportError as exc:
        raise RuntimeError("humanfriendly dependency is required") from exc
    return format_size(1536, binary=True)


def _require_config(config):
    config = dict(config or {})
    profile_name = str(config.get(_PROFILE_KEY) or "").strip()
    api_token = str(config.get(_TOKEN_KEY) or "").strip()
    if not profile_name:
        raise ValueError("SmokeHub profile_name is required")
    if not api_token:
        raise ValueError("SmokeHub api_token is required")
    return profile_name


class SmokeProvider:
    def search(self, video, languages, config):
        profile_name = _require_config(config)
        video = video or {}
        kind = video.get("kind") or "movie"
        if kind not in ("movie", "episode"):
            return []

        language = next(
            (_language_payload(item) for item in languages or [] if _language_payload(item).get("alpha3") == "eng"),
            None,
        )
        if language is None:
            return []

        title = video.get("title") or video.get("series") or video.get("name") or "Smoke Title"
        matches = ["title"] if kind == "movie" else ["series", "season", "episode"]
        dependency_marker = _format_dependency_marker()
        return [
            {
                "provider": PROVIDER_ID,
                "id": _SUBTITLE_ID,
                "language": language,
                "release_info": f"SmokeHub.{profile_name}.Fixed.{kind}.{title}",
                "filename": "smokehub.en.srt",
                "matches": matches,
                "score": 100,
                "score_without_hash": 100,
                "score_out_of": 100,
                "hash_verifiable": False,
                "hearing_impaired_verifiable": True,
                "hearing_impaired": False,
                "display": {
                    "source": "official-smoke",
                    "profile_name": profile_name,
                    "auth": "present",
                    "dependency": dependency_marker,
                },
                "provider_payload": {
                    "provider": PROVIDER_ID,
                    "schema": 1,
                    "subtitle_id": _SUBTITLE_ID,
                    "kind": kind,
                    "language": "eng",
                    "profile_name": profile_name,
                },
            }
        ]

    def download(self, provider_payload, language, config):
        del language
        profile_name = _require_config(config)
        if (provider_payload or {}).get("subtitle_id") != _SUBTITLE_ID:
            raise ValueError("unknown smoke subtitle")
        if (provider_payload or {}).get("profile_name") != profile_name:
            raise ValueError("SmokeHub profile_name mismatch")
        content = _SRT_TEXT.encode("utf-8")
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_type": "application/x-subrip",
            "format": "srt",
            "encoding": "utf-8",
            "empty": False,
        }
