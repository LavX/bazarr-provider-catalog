"""TVsubtitles.net provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile


PROVIDER_ID = "tvsubtitles"
BASE_URL = "https://www.tvsubtitles.net"
HTTP_TIMEOUT_SECONDS = 10
SUPPORTED_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

LANGUAGES = {
    "ara": {"alpha2": "ar"},
    "bul": {"alpha2": "bg"},
    "ces": {"alpha2": "cs"},
    "dan": {"alpha2": "da"},
    "deu": {"alpha2": "de"},
    "ell": {"alpha2": "el"},
    "eng": {"alpha2": "en"},
    "fin": {"alpha2": "fi"},
    "fra": {"alpha2": "fr"},
    "hun": {"alpha2": "hu"},
    "ita": {"alpha2": "it"},
    "jpn": {"alpha2": "ja"},
    "kor": {"alpha2": "ko"},
    "nld": {"alpha2": "nl"},
    "pol": {"alpha2": "pl"},
    "por": {"alpha2": "pt"},
    "ron": {"alpha2": "ro"},
    "rus": {"alpha2": "ru"},
    "spa": {"alpha2": "es"},
    "swe": {"alpha2": "sv"},
    "tur": {"alpha2": "tr"},
    "ukr": {"alpha2": "uk"},
    "zho": {"alpha2": "zh"},
}
ALPHA2_TO_ALPHA3 = {value["alpha2"]: key for key, value in LANGUAGES.items()}
FLAG_TO_LANGUAGE = {
    "br": ("por", "BR"),
    "cn": ("zho", None),
    "cz": ("ces", None),
    "gr": ("ell", None),
    "jp": ("jpn", None),
    "ua": ("ukr", None),
}
LANGUAGE_LABEL_TO_LANGUAGE = {
    "arabic": ("ara", None),
    "bulgarian": ("bul", None),
    "chinese": ("zho", None),
    "czech": ("ces", None),
    "danish": ("dan", None),
    "dutch": ("nld", None),
    "english": ("eng", None),
    "finnish": ("fin", None),
    "french": ("fra", None),
    "german": ("deu", None),
    "greek": ("ell", None),
    "hungarian": ("hun", None),
    "italian": ("ita", None),
    "japanese": ("jpn", None),
    "korean": ("kor", None),
    "polish": ("pol", None),
    "portuguese": ("por", None),
    "portuguese(br)": ("por", "BR"),
    "portuguese (br)": ("por", "BR"),
    "romanian": ("ron", None),
    "russian": ("rus", None),
    "spanish": ("spa", None),
    "swedish": ("swe", None),
    "turkish": ("tur", None),
    "ukrainian": ("ukr", None),
}

_SHOW_LINK_RE = re.compile(
    rb"<a\b[^>]*href=['\"]/?tvshow-(?P<id>\d+)\.html['\"][^>]*>(?P<title>.*?)</a>",
    re.I | re.S,
)
_SHOW_LABEL_RE = re.compile(
    r"^(?P<series>.+?)(?: \(?\d{4}\)?| \((?:US|UK)\))? \((?P<first_year>\d{4})-\d{4}\)$"
)
_ROW_RE = re.compile(rb"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_EPISODE_PAGE_RE = re.compile(rb"episode-(?P<id>\d+)\.html", re.I)
_EPISODE_NUMBER_RE = re.compile(rb"(\d+)\s*x\s*(\d+)", re.I)
_SUBTITLE_BLOCK_RE = re.compile(
    rb"<a\b[^>]*href=['\"]/?subtitle-(?P<id>\d+)\.html['\"][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
_SUBTITLE_LANGUAGE_HEADER_RE = re.compile(
    rb"<div\b[^>]*>\s*<span\b[^>]*>.*?<b>\s*(?P<label>[^<]+?)\s+subtitles\s*</b>.*?</span>\s*</div>",
    re.I | re.S,
)
_FLAG_RE = re.compile(rb"flags/(?P<flag>[a-z]{2})\.(?:gif|png)", re.I)
_H5_RE = re.compile(rb"<h5\b[^>]*>(?P<body>.*?)</h5>", re.I | re.S)
_RIP_RE = re.compile(rb"<p\b[^>]*title=['\"]rip['\"][^>]*>(?P<body>.*?)</p>", re.I | re.S)
_SCRIPT_VAR_RE = re.compile(rb"var\s+s(?P<num>\d+)\s*=\s*['\"](?P<value>[^'\"]+)['\"]", re.I)
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_BYTES_RE = re.compile(rb"\s+")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SRT_TIMECODE_RE = re.compile(
    rb"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}"
)


def parse_show_suggestions(body):
    rows = []
    for match in _SHOW_LINK_RE.finditer(body or b""):
        label = _strip_tags(match.group("title"))
        parsed = _parse_show_label(label)
        if not parsed:
            continue
        rows.append(
            {
                "show_id": _decode(match.group("id")),
                "series": parsed["series"],
                "first_year": parsed["first_year"],
                "title": label,
            }
        )
    return rows


def pick_show_id(suggestions, series, year=None):
    wanted = _normalize(series)
    wanted_year = _safe_int(year)
    for item in suggestions or []:
        if _normalize(item.get("series")) != wanted:
            continue
        if wanted_year is not None and item.get("first_year") != wanted_year:
            continue
        return item.get("show_id")
    return None


def parse_episode_ids(body):
    episode_ids = {}
    for row in _ROW_RE.finditer(body or b""):
        chunk = row.group("body")
        episode_match = _EPISODE_NUMBER_RE.search(_strip_tags_bytes(chunk))
        page_match = _EPISODE_PAGE_RE.search(chunk)
        if not episode_match or not page_match:
            continue
        episode = int(episode_match.group(2))
        episode_ids[episode] = _decode(page_match.group("id"))
    return episode_ids


def parse_episode_subtitles(body, series, season, episode, year=None):
    rows = []
    for match in _SUBTITLE_BLOCK_RE.finditer(body or b""):
        chunk = match.group("body")
        if b"subtitlen" not in chunk:
            continue
        flag_match = _FLAG_RE.search(chunk)
        language = _language_from_flag(_decode(flag_match.group("flag")) if flag_match else "")
        if not language:
            language = _language_from_previous_header(body, match.start())
        if language:
            h5_match = _H5_RE.search(chunk)
            rip_match = _RIP_RE.search(chunk)
            release = _strip_tags(h5_match.group("body")) if h5_match else ""
            rip = _strip_tags(rip_match.group("body")) if rip_match else ""
            subtitle_id = _decode(match.group("id"))
            alpha3, country = language
            rows.append(
                {
                    "subtitle_id": subtitle_id,
                    "language": alpha3,
                    "country": country,
                    "series": _coerce_text(series) or "",
                    "season": _safe_int(season),
                    "episode": _safe_int(episode),
                    "year": _safe_int(year),
                    "rip": rip or None,
                    "release": release or None,
                    "release_info": _release_info(rip, release),
                    "page_link": f"{BASE_URL}/subtitle-{subtitle_id}.html",
                }
            )
    return rows


def extract_download(body, payload=None):
    del payload
    if not body:
        raise ValueError("tvsubtitles download returned an empty body")
    stream = io.BytesIO(body)
    if not zipfile.is_zipfile(stream):
        raise ValueError("tvsubtitles download did not return a zip archive")
    # List the zip cheaply with stdlib zipfile to pick the single member, then hand the
    # raw archive bytes back to the host (Provider Hub v1.1+), which extracts the member
    # and detects encoding via Subtitle.normalize().
    with zipfile.ZipFile(stream) as archive:
        names = archive.namelist()
    if len(names) != 1:
        raise ValueError("tvsubtitles archive contains more than one file")
    member = names[0]
    if not _subtitle_extension(member):
        raise ValueError("tvsubtitles archive contains no supported subtitle file")
    return {
        "archive_b64": base64.b64encode(body).decode("ascii"),
        "archive_sha256": hashlib.sha256(body).hexdigest(),
        "member": member,
    }


class TvSubtitlesProvider:
    def _http_request(self, url, data=None, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer or f"{BASE_URL}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        request = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        return self._http_request(url, timeout=timeout, referer=referer)

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "episode":
            return []
        video = video or {}
        config = dict(config or {})
        requested = _requested_languages(languages)
        if not requested:
            return []
        season = _safe_int(video.get("season"))
        episode = _episode_number(video.get("episode"))
        if not season or not episode:
            return []
        results = []
        seen = set()
        for title in _candidate_titles(video):
            _sleep(config)
            post_body = urllib.parse.urlencode({"qs": title}).encode("ascii")
            suggestions = parse_show_suggestions(
                self._http_request(f"{BASE_URL}/search1.php", data=post_body)
            )
            show_id = pick_show_id(suggestions, title, video.get("year"))
            if not show_id:
                continue
            _sleep(config)
            episode_ids = parse_episode_ids(
                self._http_get(f"{BASE_URL}/tvshow-{show_id}-{season}.html")
            )
            episode_id = episode_ids.get(episode)
            if not episode_id:
                continue
            _sleep(config)
            rows = parse_episode_subtitles(
                self._http_get(f"{BASE_URL}/episode-{episode_id}.html"),
                series=title,
                season=season,
                episode=episode,
                year=video.get("year"),
            )
            for row in rows:
                if (row["language"], row.get("country")) not in requested:
                    continue
                key = (row["subtitle_id"], row["language"], row.get("country"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(_result(video, row))
            if results:
                return sorted(results, key=lambda item: item["score"], reverse=True)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        subtitle_id = payload.get("subtitle_id")
        if not subtitle_id:
            raise ValueError("tvsubtitles download requires subtitle_id")
        url = f"{BASE_URL}/download-{urllib.parse.quote(str(subtitle_id), safe='')}.html"
        body = self._http_get(url)
        if b"</script>" in body:
            direct_path = _script_download_path(body)
            if not direct_path:
                raise ValueError("tvsubtitles download script did not expose a file path")
            direct_path = urllib.parse.quote(direct_path.lstrip("/"), safe="/%")
            body = self._http_get(f"{BASE_URL}/{direct_path}", referer=url)
        return extract_download(body, payload)


def _result(video, row):
    alpha3 = row["language"]
    language = dict(LANGUAGES[alpha3])
    language["alpha3"] = alpha3
    language["hi"] = False
    language["forced"] = False
    if row.get("country"):
        language["country_alpha2"] = row["country"]
    matches = derive_matches(video, row)
    score = 95 if "episode" in matches else 80
    filename = (
        f"tvsubtitles.{_slug(row.get('series'))}."
        f"s{int(row.get('season') or 0):02d}e{int(row.get('episode') or 0):02d}."
        f"{_slug(row.get('release') or row.get('rip') or 'release')}.{language['alpha2']}.zip"
    )
    provider_payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "subtitle_id": row["subtitle_id"],
        "page_link": row["page_link"],
        "filename": filename,
        "language": alpha3,
        "series": row["series"],
        "season": row["season"],
        "episode": row["episode"],
    }
    if row.get("country"):
        provider_payload["country_alpha2"] = row["country"]
    return {
        "provider": PROVIDER_ID,
        "id": f"tvsubtitles-{row['subtitle_id']}-{alpha3}",
        "language": language,
        "release_info": row["release_info"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": row["page_link"],
        "display": {
            "source": "tvsubtitles.net",
            "series": row["series"],
            "release": row["release"],
            "rip": row["rip"],
        },
        "provider_payload": provider_payload,
    }


def derive_matches(video, row):
    video = video or {}
    matches = []
    if _title_matches(row.get("series"), [video.get("series")] + list(video.get("alternative_series") or [])):
        matches.append("series")
    if _safe_int(video.get("season")) == row.get("season"):
        matches.append("season")
    if _episode_number(video.get("episode")) == row.get("episode"):
        matches.append("episode")
    if video.get("year") and _safe_int(video.get("year")) == row.get("year"):
        matches.append("year")
    release_text = " ".join(str(row.get(field) or "") for field in ("rip", "release"))
    for field, match_name in (
        ("resolution", "resolution"),
        ("source", "source"),
        ("video_codec", "video_codec"),
        ("audio_codec", "audio_codec"),
        ("release_group", "release_group"),
    ):
        value = video.get(field)
        if value and _normalize(value) in _normalize(release_text):
            matches.append(match_name)
    return matches


def _parse_show_label(label):
    match = _SHOW_LABEL_RE.match(label or "")
    if not match:
        return None
    return {
        "series": match.group("series"),
        "first_year": int(match.group("first_year")),
    }


def _language_from_flag(flag):
    flag = (flag or "").lower()
    if flag in FLAG_TO_LANGUAGE:
        return FLAG_TO_LANGUAGE[flag]
    alpha3 = ALPHA2_TO_ALPHA3.get(flag)
    if alpha3:
        return alpha3, None
    return None


def _language_from_previous_header(body, position):
    language = None
    for match in _SUBTITLE_LANGUAGE_HEADER_RE.finditer((body or b"")[:position]):
        language = _language_from_label(_strip_tags(match.group("label")))
    return language


def _language_from_label(label):
    normalized = _WS_RE.sub(" ", html.unescape(_coerce_text(label) or "").lower()).strip()
    return LANGUAGE_LABEL_TO_LANGUAGE.get(normalized)


def _requested_languages(languages):
    requested = set()
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        alpha3 = (language.get("alpha3") or "").lower()
        alpha2 = (language.get("alpha2") or "").lower()
        country = language.get("country_alpha2") or language.get("country")
        country = country.upper() if isinstance(country, str) and country else None
        if alpha3 in LANGUAGES:
            requested.add((alpha3, country))
        elif alpha2 in ALPHA2_TO_ALPHA3:
            requested.add((ALPHA2_TO_ALPHA3[alpha2], country))
    return requested


def _candidate_titles(video):
    titles = []
    for title in [video.get("series")] + list(video.get("alternative_series") or []):
        title = _coerce_text(title)
        if title and title not in titles:
            titles.append(title)
    return titles


def _episode_number(value):
    if isinstance(value, list):
        values = [_safe_int(item) for item in value]
        values = [item for item in values if item is not None]
        return min(values) if values else None
    return _safe_int(value)


def _script_download_path(body):
    parts = []
    for match in _SCRIPT_VAR_RE.finditer(body or b""):
        parts.append((int(match.group("num")), _decode(match.group("value"))))
    if not parts:
        return None
    parts.sort(key=lambda item: item[0])
    return "".join(value for _index, value in parts)


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUPPORTED_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _title_matches(candidate, titles):
    candidate_norm = _normalize(candidate)
    if not candidate_norm:
        return False
    return any(_normalize(title) == candidate_norm for title in titles if title)


def _release_info(rip, release):
    return ", ".join(part for part in [rip, release] if part)


def _strip_tags(value):
    stripped = _strip_tags_bytes(value)
    return _WS_RE.sub(" ", html.unescape(_decode(stripped))).strip()


def _strip_tags_bytes(value):
    stripped = _TAG_RE.sub(b"", value or b"")
    return _WS_BYTES_RE.sub(b" ", stripped).strip()


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _slug(value):
    normalized = _normalize(value)
    return "-".join(part for part in normalized.split() if part) or "release"


def _coerce_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)
