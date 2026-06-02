"""SubF2M provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "subf2m"
BASE_URL = "https://subf2m.co"
HTTP_TIMEOUT_SECONDS = 15
MAX_TITLE_PATHS = 3
MAX_RESULTS = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

SEASON_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
}

LANGUAGES = {
    "ara": {"alpha2": "ar", "path": "arabic", "name": "Arabic"},
    "ben": {"alpha2": "bn", "path": "bengali", "name": "Bengali"},
    "bul": {"alpha2": "bg", "path": "bulgarian", "name": "Bulgarian"},
    "ces": {"alpha2": "cs", "path": "czech", "name": "Czech"},
    "dan": {"alpha2": "da", "path": "danish", "name": "Danish"},
    "deu": {"alpha2": "de", "path": "german", "name": "German"},
    "ell": {"alpha2": "el", "path": "greek", "name": "Greek"},
    "eng": {"alpha2": "en", "path": "english", "name": "English"},
    "fas": {"alpha2": "fa", "path": "farsi_persian", "name": "Farsi/Persian"},
    "fin": {"alpha2": "fi", "path": "finnish", "name": "Finnish"},
    "fra": {"alpha2": "fr", "path": "french", "name": "French"},
    "heb": {"alpha2": "he", "path": "hebrew", "name": "Hebrew"},
    "hrv": {"alpha2": "hr", "path": "croatian", "name": "Croatian"},
    "hun": {"alpha2": "hu", "path": "hungarian", "name": "Hungarian"},
    "ind": {"alpha2": "id", "path": "indonesian", "name": "Indonesian"},
    "isl": {"alpha2": "is", "path": "icelandic", "name": "Icelandic"},
    "ita": {"alpha2": "it", "path": "italian", "name": "Italian"},
    "jpn": {"alpha2": "ja", "path": "japanese", "name": "Japanese"},
    "mkd": {"alpha2": "mk", "path": "macedonian", "name": "Macedonian"},
    "msa": {"alpha2": "ms", "path": "malay", "name": "Malay"},
    "nld": {"alpha2": "nl", "path": "dutch", "name": "Dutch"},
    "nor": {"alpha2": "no", "path": "norwegian", "name": "Norwegian"},
    "pol": {"alpha2": "pl", "path": "polish", "name": "Polish"},
    "por": {"alpha2": "pt", "path": "portuguese", "name": "Portuguese"},
    "ron": {"alpha2": "ro", "path": "romanian", "name": "Romanian"},
    "rus": {"alpha2": "ru", "path": "russian", "name": "Russian"},
    "spa": {"alpha2": "es", "path": "spanish", "name": "Spanish"},
    "srp": {"alpha2": "sr", "path": "serbian", "name": "Serbian"},
    "swe": {"alpha2": "sv", "path": "swedish", "name": "Swedish"},
    "tha": {"alpha2": "th", "path": "thai", "name": "Thai"},
    "tur": {"alpha2": "tr", "path": "turkish", "name": "Turkish"},
    "vie": {"alpha2": "vi", "path": "vietnamese", "name": "Vietnamese"},
}

ALIAS_ALPHA3 = {
    "cze": "ces",
    "dut": "nld",
    "fre": "fra",
    "ger": "deu",
    "gre": "ell",
    "ice": "isl",
    "may": "msa",
    "per": "fas",
    "rum": "ron",
}

_SEARCH_LINK_RE = re.compile(
    rb"<div\b[^>]*class=['\"][^'\"]*\btitle\b[^'\"]*['\"][^>]*>\s*"
    rb"<a\b[^>]*href=['\"](?P<href>/subtitles/[^'\"]+)['\"][^>]*>(?P<title>.*?)</a>",
    re.I | re.S,
)
_ITEM_START_RE = re.compile(
    rb"<li\b[^>]*class=['\"][^'\"]*\bitem\b[^'\"]*['\"][^>]*>",
    re.I | re.S,
)
_SCROLLLIST_RE = re.compile(
    rb"<ul\b[^>]*class=['\"][^'\"]*\bscrolllist\b[^'\"]*['\"][^>]*>(?P<body>.*?)</ul>",
    re.I | re.S,
)
_LI_RE = re.compile(rb"<li\b[^>]*>(?P<body>.*?)</li>", re.I | re.S)
_COMMENT_RE = re.compile(
    rb"<div\b[^>]*class=['\"][^'\"]*\bcomment-col\b[^'\"]*['\"][^>]*>.*?<p\b[^>]*>(?P<body>.*?)</p>",
    re.I | re.S,
)
_DOWNLOAD_LINK_RE = re.compile(
    rb"<a\b(?=[^>]*class=['\"][^'\"]*\bdownload\b)(?=[^>]*href=['\"](?P<href>[^'\"]+)['\"])[^>]*>",
    re.I | re.S,
)
_DOWNLOAD_BUTTON_RE = re.compile(
    rb"<a\b(?=[^>]*id=['\"]downloadButton['\"])(?=[^>]*href=['\"](?P<href>[^'\"]+)['\"])[^>]*>",
    re.I | re.S,
)
_IMDB_RE = re.compile(rb"imdb\.com/title/(?P<imdb>tt\d+)", re.I)
_YEAR_RE = re.compile(r"\((\d{4})\)")
_SXXEYY_RE = re.compile(r"\bs0*(?P<season>\d{1,2})\s*e0*(?P<episode>\d{1,3})\b", re.I)
_XX_YY_RE = re.compile(r"\b0*(?P<season>\d{1,2})x0*(?P<episode>\d{1,3})\b", re.I)
_SPECIAL_EPISODE_RE = re.compile(
    r"\b(?:season|s)\s*0*(?P<season>\d{1,2})\s*[-]\s*0*(?P<episode>\d{1,3})\b",
    re.I,
)
_SEASON_NUMBER_RE = re.compile(r"\b(?:season|series|s)\s*0*(?P<season>\d{1,2})\b", re.I)
_COMPLETE_SEASON_RE = re.compile(
    r"\bcomplete[\W_]+(?:season|series)[\W_]+0*(?P<season>\d{1,2})\b|\bs0*(?P<s_only>\d{1,2})\b",
    re.I,
)
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_BYTES_RE = re.compile(rb"\s+")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def parse_search_results(body):
    rows = []
    seen = set()
    for match in _SEARCH_LINK_RE.finditer(body or b""):
        path = html.unescape(_decode(match.group("href")))
        if path in seen:
            continue
        seen.add(path)
        title = _strip_tags(match.group("title"))
        rows.append(
            {
                "path": path,
                "url": urllib.parse.urljoin(BASE_URL, path),
                "title": title,
                "year": _year_from_text(title),
                "season": _season_from_title(title),
                "index": len(rows),
            }
        )
    return rows


def rank_movie_paths(video, rows):
    wanted_title = _coerce_text((video or {}).get("title"))
    wanted_year = _safe_int((video or {}).get("year"))
    wanted_tokens = set(_tokens(wanted_title))
    ranked = []
    for row in rows or []:
        if wanted_year is not None and row.get("year") is not None and row["year"] != wanted_year:
            continue
        title = _title_without_year(row.get("title"))
        tokens = set(_tokens(title))
        score = _similarity_score(wanted_title, title)
        if wanted_tokens and all(token in tokens for token in wanted_tokens):
            score += 40
        if wanted_year is not None and row.get("year") == wanted_year:
            score += 30
        if score > 0:
            ranked.append((score, row.get("index", 0), row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [row for _score, _index, row in ranked[:MAX_TITLE_PATHS]]


def rank_episode_paths(video, rows):
    wanted_series = _coerce_text((video or {}).get("series"))
    wanted_year = _safe_int((video or {}).get("year"))
    wanted_season = _safe_int((video or {}).get("season"))
    wanted_tokens = set(_tokens(wanted_series))
    ranked = []
    for row in rows or []:
        if wanted_year is not None and row.get("year") is not None and row["year"] != wanted_year:
            continue
        title = _series_title_without_season(row.get("title"))
        tokens = set(_tokens(title))
        score = _similarity_score(wanted_series, title)
        if wanted_tokens and all(token in tokens for token in wanted_tokens):
            score += 50
        if wanted_season is not None and row.get("season") == wanted_season:
            score += 35
        if wanted_year is not None and row.get("year") == wanted_year:
            score += 15
        if score > 0:
            ranked.append((score, row.get("index", 0), row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [row for _score, _index, row in ranked[:MAX_TITLE_PATHS]]


def parse_subtitle_page(body, alpha3, video):
    if not _imdb_matches(body, video):
        return []
    rows = []
    for item in _iter_item_blocks(body or b""):
        row = _parse_item(item, alpha3, video)
        if row is not None:
            rows.append(row)
    return rows


def parse_download_url(body, page_url):
    match = _DOWNLOAD_BUTTON_RE.search(body or b"")
    if not match:
        raise ValueError(f"subf2m download button was not found: {page_url}")
    return urllib.parse.urljoin(page_url, html.unescape(_decode(match.group("href"))))


class SubF2MProvider:
    def search(self, video, languages, config):
        video = dict(video or {})
        if video.get("kind") not in {"movie", "episode"}:
            return []
        requested = _requested_languages(languages)
        if not requested:
            return []
        results = []
        seen = set()
        for query in build_queries(video):
            _sleep(config)
            search_url = f"{BASE_URL}/subtitles/searchbytitle?query={urllib.parse.quote(query, safe='')}&l="
            search_body = self._http_get(search_url, config=config)
            rows = parse_search_results(search_body)
            paths = rank_episode_paths(video, rows) if video.get("kind") == "episode" else rank_movie_paths(video, rows)
            for path in paths:
                for language in requested:
                    _sleep(config)
                    page_url = f"{BASE_URL}{path['path']}/{language['path']}"
                    try:
                        page_body = self._http_get(page_url, referer=search_url, config=config)
                    except urllib.error.HTTPError as error:
                        if error.code in {403, 404}:
                            continue
                        raise
                    for row in parse_subtitle_page(page_body, language["alpha3"], video):
                        if not _row_matches_language(row, language):
                            continue
                        key = (row["subtitle_id"], row["language"])
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(self._result(video, path, row, language))
                        if len(results) >= MAX_RESULTS:
                            return _sort_results(results)
                if results:
                    return _sort_results(results)
            if results:
                return _sort_results(results)
        return _sort_results(results)

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        page_url = payload.get("page_url")
        if not page_url:
            raise ValueError("subf2m download requires page_url")
        _sleep(config)
        detail_body = self._http_get(page_url, referer=BASE_URL, config=config)
        download_url = parse_download_url(detail_body, page_url)
        _sleep(config)
        body = self._http_get(download_url, referer=page_url, config=config)
        payload.setdefault("download_url", download_url)
        return extract_download(body, payload)

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None, config=None):
        config = dict(config or {})
        headers = {
            "User-Agent": _user_agent(config),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if referer:
            headers["Referer"] = referer
        context = None
        if config.get("verify_ssl") is False:
            context = ssl._create_unverified_context()
        request = urllib.request.Request(url, headers=headers)
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in {404, 503} or attempt == 2:
                    raise
                time.sleep(3)
        if last_error is not None:
            raise last_error
        return b""

    def _result(self, video, path, row, language):
        candidate = f"{path.get('title') or ''} {row['release_info']}"
        matches = derive_matches(video, candidate, imdb_matched=row.get("imdb_matched"), season_pack=row.get("season_pack"))
        score = 95 if "episode" in matches or "year" in matches else 85
        filename = f"subf2m.{_slug(row['release_info'])}.{language['alpha2']}.{row['subtitle_id']}.zip"
        hearing_impaired = bool(row.get("hearing_impaired"))
        forced = bool(row.get("forced"))
        return {
            "provider": PROVIDER_ID,
            "id": f"subf2m-{row['subtitle_id']}-{row['language']}",
            "language": {
                "alpha3": row["language"],
                "alpha2": language["alpha2"],
                "hi": hearing_impaired,
                "forced": forced,
            },
            "release_info": row["release_info"],
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": True,
            "hearing_impaired": hearing_impaired,
            "page_link": row["page_url"],
            "display": {
                "source": "subf2m",
                "title": path.get("title"),
                "release": row["release_info"],
                "comment": row.get("comment"),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": row["subtitle_id"],
                "page_url": row["page_url"],
                "filename": filename,
                "language": row["language"],
                "language_path": language["path"],
                "hi": hearing_impaired,
                "forced": forced,
                "season": _safe_int(video.get("season")),
                "episode": _safe_int(video.get("episode")),
            },
        }


def build_queries(video):
    video = video or {}
    if video.get("kind") == "episode":
        series = _coerce_text(video.get("series"))
        return [series] if series else []
    title = _coerce_text(video.get("title"))
    if not title:
        return []
    queries = [title]
    if ":" in title:
        queries.append(title.split(":", 1)[0].strip())
    return _dedupe(queries)


def derive_matches(video, candidate_title, imdb_matched=False, season_pack=False):
    video = video or {}
    candidate_tokens = set(_tokens(candidate_title))
    matches = []
    if video.get("kind") == "episode":
        series_tokens = _tokens(video.get("series"))
        if series_tokens and all(token in candidate_tokens for token in series_tokens):
            matches.append("series")
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        if season is not None and (season_pack or _text_has_season(candidate_title, season)):
            matches.append("season")
        if episode is not None and _text_has_episode(candidate_title, season, episode):
            matches.append("episode")
        year = _safe_int(video.get("year"))
        if year is not None and str(year) in candidate_tokens:
            matches.append("year")
    else:
        title_tokens = _tokens(video.get("title"))
        if title_tokens and all(token in candidate_tokens for token in title_tokens):
            matches.append("title")
        year = _safe_int(video.get("year"))
        if year is not None and str(year) in candidate_tokens:
            matches.append("year")
    if imdb_matched:
        matches.append("imdb_id")
    return matches


def extract_download(body, payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", "srt", empty=True)
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    extension = _subtitle_extension(payload.get("filename", "")) or "srt"
    return _content_payload(body, extension)


def select_subtitle_file(names, payload=None):
    payload = payload or {}
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("subf2m archive contains no supported subtitle files")
    season = _safe_int(payload.get("season"))
    episode = _safe_int(payload.get("episode"))
    if episode is not None:
        scored = [(_episode_file_score(name, season, episode), index, name) for index, name in enumerate(candidates)]
        best_score, _index, best_name = max(scored, key=lambda item: (item[0], -item[1]))
        if best_score > 0:
            return best_name
    return sorted(candidates, key=_file_preference_key)[0]


def _parse_item(item, alpha3, video):
    releases = []
    for list_match in _SCROLLLIST_RE.finditer(item or b""):
        for release_match in _LI_RE.finditer(list_match.group("body")):
            text = _strip_tags(release_match.group("body"))
            if text:
                releases.append(text)
    release_text = "\n".join(part for part in releases if part)
    comment_match = _COMMENT_RE.search(item or b"")
    comment = _strip_tags(comment_match.group("body")) if comment_match else ""
    if comment:
        releases.append(comment)
    release_info = "\n".join(part for part in releases if part)
    if not release_info:
        return None
    link_match = _DOWNLOAD_LINK_RE.search(item or b"")
    if not link_match:
        return None
    if video and video.get("kind") == "episode" and not _release_matches_episode(release_text, video):
        return None
    page_path = html.unescape(_decode(link_match.group("href")))
    page_url = urllib.parse.urljoin(BASE_URL, page_path)
    subtitle_id = page_url.rstrip("/").split("/")[-1]
    return {
        "subtitle_id": subtitle_id,
        "language": alpha3,
        "release_info": release_info,
        "comment": comment,
        "page_url": page_url,
        "forced": _looks_forced(release_info),
        "hearing_impaired": _looks_hearing_impaired(release_info),
        "imdb_matched": _expected_imdb(video) is not None,
        "season_pack": _release_is_season_pack(release_text, _safe_int((video or {}).get("season"))),
    }


def _iter_item_blocks(body):
    starts = list(_ITEM_START_RE.finditer(body or b""))
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body or b"")
        yield body[match.end():end]


def _release_matches_episode(release_info, video):
    season = _safe_int((video or {}).get("season"))
    episode = _safe_int((video or {}).get("episode"))
    if season is None or episode is None:
        return False
    for parsed_season, parsed_episode in _episode_pairs(release_info):
        if parsed_season == season and parsed_episode == episode:
            return True
    return _release_is_season_pack(release_info, season)


def _release_is_season_pack(text, season):
    if season is None:
        return False
    normalized = _normalize(text)
    if "complete series" in normalized and f"s{season:02d}" not in normalized:
        return True
    for match in _COMPLETE_SEASON_RE.finditer(normalized):
        value = match.group("season") or match.group("s_only")
        if _safe_int(value) == season:
            return True
    return False


def _episode_pairs(text):
    normalized = _normalize(text)
    pairs = []
    for regex in (_SXXEYY_RE, _XX_YY_RE, _SPECIAL_EPISODE_RE):
        for match in regex.finditer(normalized):
            season = _safe_int(match.group("season"))
            episode = _safe_int(match.group("episode"))
            if season is not None and episode is not None:
                pairs.append((season, episode))
    return pairs


def _imdb_matches(body, video):
    expected = _expected_imdb(video)
    match = _IMDB_RE.search(body or b"")
    parsed = _decode(match.group("imdb")) if match else None
    if expected is None or parsed is None:
        return True
    return parsed == expected


def _expected_imdb(video):
    video = video or {}
    return video.get("series_imdb_id") or video.get("imdb_id")


def _requested_languages(languages):
    requested = []
    seen = set()
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 not in LANGUAGES:
            continue
        meta = dict(LANGUAGES[alpha3])
        if alpha3 == "por" and _is_brazilian_portuguese(language):
            meta["path"] = "brazillian-portuguese"
        meta["alpha3"] = alpha3
        if isinstance(language, dict):
            meta["hi"] = _optional_bool(language.get("hi"))
            meta["forced"] = _optional_bool(language.get("forced"))
        key = (alpha3, meta["path"])
        if key in seen:
            continue
        seen.add(key)
        requested.append(meta)
    return requested


def _alpha3_for_language(language):
    if isinstance(language, str):
        code = language
    elif isinstance(language, dict):
        code = language.get("alpha3") or language.get("alpha2") or ""
    else:
        code = ""
    code = str(code).lower()
    if len(code) == 2:
        for alpha3, meta in LANGUAGES.items():
            if meta["alpha2"] == code:
                return alpha3
    return ALIAS_ALPHA3.get(code, code)


def _is_brazilian_portuguese(language):
    if not isinstance(language, dict):
        return False
    value = language.get("country") or language.get("region") or language.get("alpha2")
    return str(value or "").upper() in {"BR", "PT-BR"}


def _row_matches_language(row, language):
    requested_hi = language.get("hi")
    requested_forced = language.get("forced")
    if requested_hi is not None and bool(row.get("hearing_impaired")) != requested_hi:
        return False
    if requested_forced is not None and bool(row.get("forced")) != requested_forced:
        return False
    return True


def _looks_forced(value):
    normalized = _normalize(value)
    if re.search(r"\bforced\b", normalized):
        return True
    return bool(
        re.search(r"\bforeign(?:[\W_]+parts)?[\W_]+only\b", normalized)
        or re.search(r"\b(?:sign|signs|songs)[\W_]+(?:and[\W_]+songs[\W_]+)?(?:only|parts)\b", normalized)
    )


def _looks_hearing_impaired(value):
    tokens = set(_tokens(value))
    if tokens & {"hi", "sdh"}:
        return True
    normalized = _normalize(value)
    return bool(re.search(r"\bhearing[\W_]+impaired\b", normalized))


def _optional_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _user_agent(config):
    value = _coerce_text((config or {}).get("user_agent"))
    return value or DEFAULT_USER_AGENT


def _sleep(config):
    try:
        delay = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay = 0
    if delay > 0:
        time.sleep(min(delay, 5000) / 1000.0)


def _sort_results(results):
    return sorted(results, key=lambda item: item["score"], reverse=True)


def _title_without_year(value):
    return _YEAR_RE.sub("", _coerce_text(value)).strip()


def _series_title_without_season(value):
    title = _title_without_year(value)
    title = re.sub(r"\s*[-(]\s*(?:\w+\s+)?(?:season|series)\s*\d*\)?\s*$", "", title, flags=re.I)
    return title.strip()


def _season_from_title(value):
    text = _normalize(_title_without_year(value))
    for word, number in SEASON_WORDS.items():
        if re.search(rf"\b{word}\s+(?:season|series)\b", text):
            return number
    match = _SEASON_NUMBER_RE.search(text)
    if match:
        return _safe_int(match.group("season"))
    return None


def _year_from_text(value):
    match = _YEAR_RE.search(_coerce_text(value))
    return _safe_int(match.group(1)) if match else None


def _text_has_season(text, season):
    return any(parsed_season == season for parsed_season, _episode in _episode_pairs(text)) or _release_is_season_pack(text, season)


def _text_has_episode(text, season, episode):
    return any(parsed_season == season and parsed_episode == episode for parsed_season, parsed_episode in _episode_pairs(text))


def _episode_file_score(name, season, episode):
    lower_name = _normalize(os.path.basename(name))
    score = 0
    for parsed_season, parsed_episode in _episode_pairs(lower_name):
        if parsed_episode == episode and (season is None or parsed_season == season):
            score += 100
    if "hi" in _tokens(lower_name) or "sdh" in _tokens(lower_name):
        score -= 5
    return score


def _file_preference_key(name):
    basename = os.path.basename(name).lower()
    penalty = 1 if re.search(r"\b(?:hi|sdh|hearing)\b", basename) else 0
    return (penalty, basename)


def _slug(value, max_length=80):
    slug = "-".join(_tokens(value))
    return (slug[:max_length].strip("-") or "subtitle")


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


def _similarity_score(left, right):
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return 0
    if left_norm == right_norm:
        return 100
    left_tokens = set(_tokens(left_norm))
    right_tokens = set(_tokens(right_norm))
    if not left_tokens or not right_tokens:
        return 0
    overlap = len(left_tokens & right_tokens)
    return int(80 * overlap / max(len(left_tokens), len(right_tokens)))


def _tokens(value):
    normalized = _normalize(value)
    return [token for token in _NON_ALNUM_RE.split(normalized) if token]


def _normalize(value):
    value = unicodedata.normalize("NFKD", _coerce_text(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return _WS_RE.sub(" ", value.lower()).strip()


def _strip_tags(value):
    text = _TAG_RE.sub(b" ", value or b"")
    text = html.unescape(_decode(_WS_BYTES_RE.sub(b" ", text)).strip())
    return _WS_RE.sub(" ", text).strip()


def _decode(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _coerce_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values):
    seen = set()
    output = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output
