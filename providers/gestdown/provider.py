import base64
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDER_ID = "gestdown"
BASE_URL = "https://api.gestdown.info"
DEFAULT_TIMEOUT_SECONDS = 30
LOCKED_RETRY_ATTEMPTS = 3

SUPPORTED_LANGUAGES = [
    "ara",
    "aze",
    "ben",
    "bos",
    "bul",
    "cat",
    "ces",
    "dan",
    "deu",
    "ell",
    "eng",
    "eus",
    "fas",
    "fin",
    "fra",
    "glg",
    "heb",
    "hrv",
    "hun",
    "hye",
    "ind",
    "ita",
    "jpn",
    "kor",
    "mkd",
    "msa",
    "nld",
    "nor",
    "pol",
    "por",
    "ron",
    "rus",
    "slk",
    "slv",
    "spa",
    "sqi",
    "srp",
    "swe",
    "tha",
    "tur",
    "ukr",
    "vie",
    "zho",
]

_ALPHA2 = {
    "ara": "ar",
    "aze": "az",
    "ben": "bn",
    "bos": "bs",
    "bul": "bg",
    "cat": "ca",
    "ces": "cs",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "eus": "eu",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "glg": "gl",
    "heb": "he",
    "hrv": "hr",
    "hun": "hu",
    "hye": "hy",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kor": "ko",
    "mkd": "mk",
    "msa": "ms",
    "nld": "nl",
    "nor": "no",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "rus": "ru",
    "slk": "sk",
    "slv": "sl",
    "spa": "es",
    "sqi": "sq",
    "srp": "sr",
    "swe": "sv",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
    "zho": "zh",
}

_GESTDOWN_LANGUAGE_NAMES = {
    "ara": "Arabic",
    "aze": "Azerbaijani",
    "ben": "Bengali",
    "bos": "Bosnian",
    "bul": "Bulgarian",
    "cat": "Catalan",
    "ces": "Czech",
    "dan": "Danish",
    "deu": "German",
    "ell": "Greek",
    "eng": "English",
    "eus": "Basque",
    "fas": "Persian",
    "fin": "Finnish",
    "fra": "French",
    "glg": "Galician",
    "heb": "Hebrew",
    "hrv": "Croatian",
    "hun": "Hungarian",
    "hye": "Armenian",
    "ind": "Indonesian",
    "ita": "Italian",
    "jpn": "Japanese",
    "kor": "Korean",
    "mkd": "Macedonian",
    "msa": "Malay",
    "nld": "Dutch",
    "nor": "Norwegian",
    "pol": "Polish",
    "por": "Portuguese",
    "ron": "Romanian",
    "rus": "Russian",
    "slk": "Slovak",
    "slv": "Slovenian",
    "spa": "Spanish",
    "sqi": "Albanian",
    "srp": "Serbian",
    "swe": "Swedish",
    "tha": "Thai",
    "tur": "Turkish",
    "ukr": "Ukrainian",
    "vie": "Vietnamese",
    "zho": "Chinese",
}


def _json_loads(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return json.loads((body or b"{}").decode("utf-8"))


def _language_payload(language):
    payload = dict(language or {}) if isinstance(language, dict) else {"alpha3": str(language)}
    alpha3 = str(payload.get("alpha3") or "").lower()
    payload["alpha3"] = alpha3
    payload.setdefault("alpha2", _ALPHA2.get(alpha3, ""))
    payload.setdefault("hi", False)
    payload.setdefault("forced", False)
    return payload


def _clean_releases(version):
    return [item.strip() for item in str(version or "").split(",") if item.strip()]


def _absolute_url(uri):
    return urllib.parse.urljoin(BASE_URL, str(uri or ""))


def _normalise_match_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _timeout_seconds(config):
    raw = (config or {}).get("request_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    try:
        return max(1, min(int(raw), 120))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _locked_retry_delay_seconds(config):
    raw = (config or {}).get("locked_retry_delay_ms", 30000)
    try:
        return max(0, min(int(raw), 30000)) / 1000.0
    except (TypeError, ValueError):
        return 30.0


def parse_show_lookup(body):
    payload = _json_loads(body)
    shows = []
    for item in payload.get("shows") or []:
        show_id = item.get("id")
        if not show_id:
            continue
        shows.append(
            {
                "id": str(show_id),
                "name": item.get("name") or "",
                "tvdb_id": _int_or_none(item.get("tvDbId")),
                "tmdb_id": _int_or_none(item.get("tmdbId")),
                "slug": item.get("slug") or "",
            }
        )
    return shows


def parse_subtitle_results(body):
    payload = _json_loads(body)
    entries = []
    for item in payload.get("matchingSubtitles") or []:
        if not item.get("completed"):
            continue
        subtitle_id = item.get("subtitleId")
        download_uri = item.get("downloadUri")
        if not subtitle_id or not download_uri:
            continue
        releases = _clean_releases(item.get("version"))
        release_info = "\n".join(releases) if releases else str(subtitle_id)
        entries.append(
            {
                "subtitle_id": str(subtitle_id),
                "version": item.get("version") or "",
                "releases": releases,
                "release_info": release_info,
                "download_url": _absolute_url(download_uri),
                "language_name": item.get("language") or "",
                "hearing_impaired": bool(item.get("hearingImpaired")),
                "corrected": bool(item.get("corrected")),
                "hd": bool(item.get("hd")),
                "qualities": list(item.get("qualities") or []),
                "download_count": int(item.get("downloadCount") or 0),
                "source": item.get("source") or "Gestdown",
                "discovered": item.get("discovered") or "",
            }
        )
    return entries


def gestdown_language_name(language):
    payload = _language_payload(language)
    alpha3 = payload.get("alpha3")
    if alpha3 == "por":
        country = str(payload.get("country_alpha2") or payload.get("country") or "").upper()
        alpha2 = str(payload.get("alpha2") or "").lower()
        if country == "BR" or alpha2 == "br":
            return "Portuguese (Brazil)"
    return _GESTDOWN_LANGUAGE_NAMES.get(alpha3)


def show_lookup_url(tvdb_id):
    return f"{BASE_URL}/shows/external/tvdb/{int(tvdb_id)}"


def subtitles_url(show_id, season, episode, language_name):
    encoded_language = urllib.parse.quote(str(language_name), safe="")
    return f"{BASE_URL}/subtitles/get/{show_id}/{int(season)}/{int(episode)}/{encoded_language}"


def derive_matches(video, entry):
    video = video or {}
    matches = {"series", "season", "episode", "tvdb_id", "title"}
    release_group = _normalise_match_text(video.get("release_group"))
    if release_group:
        for release in entry.get("releases") or []:
            if release_group and release_group in _normalise_match_text(release):
                matches.add("release_group")
                break
    resolution = str(video.get("resolution") or "")
    if resolution and resolution in (entry.get("qualities") or []):
        matches.add("resolution")
    return sorted(matches)


def compute_score(video, entry):
    score = 75
    matches = set(derive_matches(video, entry))
    if "release_group" in matches:
        score += 10
    if "resolution" in matches:
        score += 10
    if entry.get("hearing_impaired"):
        score += 1
    return min(score, 100)


def _video_series_tvdb_id(video):
    video = video or {}
    for key in ("series_tvdb_id", "tvdb_id", "seriesTvdbId", "tvdbId"):
        value = _int_or_none(video.get(key))
        if value:
            return value
    return None


class GestdownProvider:
    def _http_get(self, url, timeout=DEFAULT_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, headers={"User-Agent": "Bazarr"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _get_with_locked_retry(self, url, config):
        timeout = _timeout_seconds(config)
        delay = _locked_retry_delay_seconds(config)
        for attempt in range(LOCKED_RETRY_ATTEMPTS):
            try:
                return self._http_get(url, timeout=timeout)
            except urllib.error.HTTPError as exc:
                if exc.code != 423 or attempt == LOCKED_RETRY_ATTEMPTS - 1:
                    raise
                if delay:
                    time.sleep(delay)
        return b""

    def search(self, video, languages, config):
        video = video or {}
        if video.get("kind") != "episode":
            return []
        season = _int_or_none(video.get("season"))
        episode = _int_or_none(video.get("episode"))
        tvdb_id = _video_series_tvdb_id(video)
        if not tvdb_id or not season or not episode:
            return []

        try:
            show_body = self._get_with_locked_retry(show_lookup_url(tvdb_id), config or {})
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise
        shows = parse_show_lookup(show_body)
        if not shows:
            return []

        results = []
        seen = set()
        for language in languages or []:
            language_payload = _language_payload(language)
            language_name = gestdown_language_name(language_payload)
            if not language_name:
                continue
            for show in shows:
                try:
                    body = self._get_with_locked_retry(
                        subtitles_url(show["id"], season, episode, language_name),
                        config or {},
                    )
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        continue
                    raise
                for entry in parse_subtitle_results(body):
                    key = (entry["subtitle_id"], language_payload["alpha3"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, language_payload, entry))
        return results

    def _result(self, video, language, entry):
        language_payload = _language_payload(language)
        language_payload["hi"] = bool(entry.get("hearing_impaired"))
        subtitle_id = entry["subtitle_id"]
        score = compute_score(video, entry)
        return {
            "provider": PROVIDER_ID,
            "id": f"gestdown-{subtitle_id}-{language_payload['alpha3']}",
            "language": language_payload,
            "release_info": entry.get("release_info") or subtitle_id,
            "filename": f"gestdown.{subtitle_id}.{language_payload.get('alpha2') or language_payload['alpha3']}.srt",
            "matches": derive_matches(video, entry),
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": True,
            "hearing_impaired": bool(entry.get("hearing_impaired")),
            "page_link": entry.get("download_url"),
            "display": {
                "source": entry.get("source") or "Gestdown",
                "release": entry.get("release_info"),
                "downloads": entry.get("download_count", 0),
                "corrected": bool(entry.get("corrected")),
                "hd": bool(entry.get("hd")),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": subtitle_id,
                "download_url": entry["download_url"],
                "language": language_payload["alpha3"],
                "release_info": entry.get("release_info"),
            },
        }

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        download_url = payload.get("download_url")
        if not download_url:
            raise ValueError("gestdown download requires download_url")
        content = self._get_with_locked_retry(download_url, config or {})
        if not content:
            raise ValueError("gestdown download returned an empty subtitle")
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_type": "application/x-subrip",
            "format": "srt",
            "encoding": "utf-8",
            "empty": False,
        }
