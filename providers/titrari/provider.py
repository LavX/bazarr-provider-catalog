"""Titrari.ro provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in manifest
    py7zz = None

PROVIDER_ID = "titrari"
BASE_URL = "https://www.titrari.ro"
HOME_URL = f"{BASE_URL}/"
HTTP_TIMEOUT_SECONDS = 15
DEFAULT_ADVANCED_SEARCH_PAGE = "numaicautamcaneiesepenas"
SUPPORTED_LANGUAGES = {"ron": "ro", "eng": "en"}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
LANGUAGE_SEARCH_CODES = {"ron": "1", "eng": "2"}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<h1>\s*<a\b[^>]*>(?P<title>.*?)</a>\s*</h1>", re.I | re.S)
_DOWNLOAD_RE = re.compile(r"href\s*=\s*['\"]?get\.php\?id=(?P<id>\d+)['\"]?", re.I)
_IMDB_RE = re.compile(r"(?:imdb\.com(?:%2F|/)title(?:%2F|/)tt|z5=)(?P<id>\d+)", re.I)
_COMMENT_RE = re.compile(r"<td\s+class\s*=\s*['\"]?comment['\"]?\s+width\s*=\s*['\"]?100%['\"]?>(?P<body>.*?)</td>", re.I | re.S)
_TRANSLATOR_RE = re.compile(r"Traducator:\s*<b>\s*<a\b[^>]*>(?P<value>.*?)</a>", re.I | re.S)
_UPLOADER_RE = re.compile(r"Uploader:\s*<b>\s*<a\b[^>]*>(?P<value>.*?)</a>", re.I | re.S)
_DOWNLOAD_COUNT_RE = re.compile(r"Descarcari:\s*(?P<count>\d+)", re.I)
_SEASON_TITLE_RE = re.compile(r"^(?P<title>.*?)\s+-\s+Sezonul\s+(?P<season>\d+)\s*(?:\(\d{4}\))?$", re.I)
_YEAR_RE = re.compile(r"\((?P<year>\d{4})\)")
_EPISODE_RE = re.compile(r"\b(?:episodul|episode|ep\.?)\s*0*(?P<episode>\d{1,3})\b", re.I)
_SXXEXX_RE = re.compile(r"\bs(?P<season>\d{1,2})\s*[._ -]?e(?P<episode>\d{1,3})\b", re.I)
_XX_RE = re.compile(r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b", re.I)
_S_SEPARATED_EPISODE_RE = re.compile(r"\bs(?P<season>\d{1,2})[._ -]+(?P<episode>\d{1,3})\b", re.I)
_EPISODE_RANGE_RE = re.compile(r"\b(?:episoadele|episodes?)\s+(?P<start>\d{1,3})\s*-\s*(?P<end>\d{1,3})\b", re.I)
_SIMPLE_RANGE_RE = re.compile(r"\b(?P<start>\d{1,3})\s*-\s*(?P<end>\d{1,3})\b")
_PACK_RE = re.compile(r"\b(?:complet|episoade|episodes|season\s+pack|sezonul\s+\d+\s+complet)\b", re.I)


def parse_advanced_search_page_param(body):
    text = _decode(body)
    for href in re.findall(r"href\s*=\s*['\"]?([^'\"\s>]+)", text, flags=re.I):
        if "page=" not in href:
            continue
        unescaped = html.unescape(href)
        if "cautare" not in unescaped.lower() and "cautamainaltaparte" not in unescaped.lower():
            continue
        parsed = urllib.parse.urlsplit(unescaped if "://" in unescaped else f"{BASE_URL}/{unescaped.lstrip('/')}")
        query = urllib.parse.parse_qs(parsed.query)
        page = (query.get("page") or [""])[0]
        if page:
            return page
    return DEFAULT_ADVANCED_SEARCH_PAGE


def build_search_url(video, language, page_param):
    video = video or {}
    alpha3 = _alpha3_for_language(language)
    params = {
        "page": page_param or DEFAULT_ADVANCED_SEARCH_PAGE,
        "z7": "",
        "z2": "",
        "z5": "",
        "z3": "-1",
        "z4": "-1",
        "z8": LANGUAGE_SEARCH_CODES.get(alpha3, "-1"),
        "z9": "All",
        "z11": _media_type_code(video.get("kind")),
        "z6": "0",
    }
    imdb_number = _imdb_number(video.get("series_imdb_id") if video.get("kind") == "episode" else video.get("imdb_id"))
    if imdb_number:
        params["z5"] = imdb_number
    else:
        params["z7"] = _coerce_text(video.get("series") if video.get("kind") == "episode" else video.get("title")) or ""
    return f"{BASE_URL}/index.php?{urllib.parse.urlencode(params)}"


def parse_search_results(body):
    text = _decode(body)
    rows = []
    seen = set()
    for part in re.split(r"(?=<tr><td\s+rowspan\s*=\s*['\"]?4['\"]?\s+class\s*=\s*['\"]?row1)", text, flags=re.I):
        if "get.php?id=" not in part:
            continue
        block = part.split('<tr><td class="row1, test"', 1)[0]
        download_match = _DOWNLOAD_RE.search(block)
        title_match = _TITLE_RE.search(block)
        if not download_match or not title_match:
            continue
        subtitle_id = download_match.group("id")
        if subtitle_id in seen:
            continue
        seen.add(subtitle_id)

        full_title = _strip_tags(title_match.group("title"))
        title, year, season = _parse_title(full_title)
        language = _language_from_block(block)
        if not language:
            continue
        comments = _comment_from_block(block)
        row = {
            "subtitle_id": subtitle_id,
            "title": title,
            "full_title": full_title,
            "year": year,
            "season": season,
            "episode": _episode_from_text(comments),
            "is_pack": _is_pack(comments),
            "language": language,
            "imdb_id": _imdb_from_block(block),
            "download_url": f"{BASE_URL}/get.php?id={subtitle_id}",
            "page_url": f"{BASE_URL}/index.php?page=cautamainaltaparte&z10={subtitle_id}",
            "uploader": _field_from_block(_UPLOADER_RE, block),
            "translator": _field_from_block(_TRANSLATOR_RE, block),
            "downloads": _downloads_from_block(block),
            "comments": comments,
        }
        if row["is_pack"]:
            row["episode"] = None
        rows.append(row)
    return rows


def derive_matches(video, row):
    video = video or {}
    row = row or {}
    matches = []
    kind = video.get("kind")
    comments = row.get("comments") or ""
    if kind == "movie":
        if _same_title(video.get("title"), row.get("title")):
            matches.append("title")
        if _same_int(video.get("year"), row.get("year")):
            matches.append("year")
        if _same_imdb(video.get("imdb_id"), row.get("imdb_id")):
            matches.append("imdb_id")
    elif kind == "episode":
        if _same_title(video.get("series"), row.get("title")):
            matches.append("series")
        if _same_int(video.get("season"), row.get("season")):
            matches.append("season")
        if _episode_matches(video, row):
            matches.append("episode")
        if _same_imdb(video.get("series_imdb_id"), row.get("imdb_id")):
            matches.append("series_imdb_id")
    if _release_group_matches(video.get("release_group"), comments):
        matches.append("release_group")
    if _token_in_text(video.get("resolution"), comments):
        matches.append("resolution")
    if _source_matches(video.get("source"), comments):
        matches.append("source")
    return matches


def extract_download(body, payload=None):
    payload = payload or {}
    filename = payload.get("filename") or ""
    if not body:
        return _content_payload(b"", _format_from_filename(filename), empty=True)
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    if _looks_like_html(body):
        raise ValueError("titrari download returned HTML instead of a subtitle file")
    return _content_payload(body, _format_from_filename(filename))


def select_subtitle_file(names, payload=None):
    payload = payload or {}
    candidates = [name for name in names if _is_supported_subtitle_name(name)]
    if not candidates:
        raise ValueError("titrari archive contains no supported subtitle files")
    try:
        episode = int(payload.get("episode"))
    except (TypeError, ValueError):
        episode = None
    if episode is None:
        return candidates[0]
    season = _safe_int(payload.get("season"))

    def score(name):
        normalized = _normalize(os.path.basename(name))
        value = _archive_episode_score(name, season, episode)
        if value <= 0:
            return 0
        if _token_in_text(payload.get("resolution"), normalized):
            value += 8
        if _source_matches(payload.get("source"), normalized):
            value += 8
        if _release_group_matches(payload.get("release_group"), normalized):
            value += 6
        return value

    single_candidate = _select_single_episode_candidate(candidates, score)
    if single_candidate:
        return single_candidate

    return _best_scored_episode_candidate(candidates, score)


class TitrariProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))
        self._advanced_page_param = None

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        config = dict(config or {})
        requested_languages = _requested_languages(languages)
        if not requested_languages:
            return []
        video = video or {}
        if video.get("kind") not in ("movie", "episode"):
            return []

        page_param = self._get_advanced_search_page_param(config)
        results = []
        seen = set()
        for language in requested_languages:
            _sleep(config)
            search_url = build_search_url(video, language, page_param)
            body = self._http_get(search_url, referer=HOME_URL)
            for row in parse_search_results(body):
                if row["language"] != language["alpha3"]:
                    continue
                if not _row_matches_video(video, row):
                    continue
                key = (row["subtitle_id"], language["alpha3"], language["hi"], language["forced"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(_result_from_row(video, row, language))
        return sorted(results, key=lambda item: (item["score"], item["provider_payload"].get("downloads", 0)), reverse=True)

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        download_url = payload.get("download_url")
        if not download_url:
            raise ValueError("titrari download requires download_url")
        body = self._http_get(download_url, referer=payload.get("page_url") or HOME_URL)
        return extract_download(body, payload)

    def _get_advanced_search_page_param(self, config):
        if self._advanced_page_param:
            return self._advanced_page_param
        _sleep(config)
        try:
            body = self._http_get(HOME_URL)
        except Exception:
            self._advanced_page_param = DEFAULT_ADVANCED_SEARCH_PAGE
            return self._advanced_page_param
        self._advanced_page_param = parse_advanced_search_page_param(body)
        return self._advanced_page_param


def _result_from_row(video, row, language):
    matches = derive_matches(video, row)
    score = _score(matches, row)
    alpha3 = language["alpha3"]
    alpha2 = language["alpha2"]
    filename = _filename_from_row(video, row, alpha2)
    release_info = row.get("comments") or row.get("full_title") or row.get("title") or "Titrari subtitle"
    return {
        "provider": PROVIDER_ID,
        "id": f"titrari-{row['subtitle_id']}-{alpha3}",
        "language": dict(language),
        "release_info": release_info,
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": bool(language.get("hi")),
        "page_link": row["page_url"],
        "display": {
            "source": "titrari.ro",
            "title": row.get("full_title") or row.get("title"),
            "uploader": row.get("uploader"),
            "translator": row.get("translator"),
            "downloads": row.get("downloads", 0),
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "subtitle_id": row["subtitle_id"],
            "download_url": row["download_url"],
            "page_url": row["page_url"],
            "filename": filename,
            "language": alpha3,
            "downloads": row.get("downloads", 0),
            "season": video.get("season") or row.get("season"),
            "episode": video.get("episode") or row.get("episode"),
            "release_group": video.get("release_group"),
            "resolution": video.get("resolution"),
            "source": video.get("source"),
        },
    }


def _row_matches_video(video, row):
    kind = (video or {}).get("kind")
    matches = set(derive_matches(video, row))
    if kind == "movie":
        if row.get("year") and video.get("year") and not _same_int(row.get("year"), video.get("year")):
            return False
        if video.get("imdb_id") and row.get("imdb_id") and not _same_imdb(video.get("imdb_id"), row.get("imdb_id")):
            return False
        return "title" in matches or "imdb_id" in matches
    if kind == "episode":
        if row.get("year") and video.get("year") and not _same_int(row.get("year"), video.get("year")):
            return False
        if video.get("series_imdb_id") and row.get("imdb_id") and not _same_imdb(video.get("series_imdb_id"), row.get("imdb_id")):
            return False
        if video.get("season") and row.get("season") and not _same_int(video.get("season"), row.get("season")):
            return False
        if row.get("episode") is not None and video.get("episode") and not _same_int(video.get("episode"), row.get("episode")):
            return False
        return "series" in matches or "series_imdb_id" in matches
    return False


def _score(matches, row):
    match_set = set(matches)
    score = 0
    for name, value in (
        ("title", 25),
        ("series", 25),
        ("year", 10),
        ("season", 10),
        ("episode", 18),
        ("imdb_id", 20),
        ("series_imdb_id", 20),
        ("release_group", 12),
        ("resolution", 6),
        ("source", 6),
    ):
        if name in match_set:
            score += value
    if row.get("is_pack"):
        score -= 4
    return max(0, min(100, score))


def _filename_from_row(video, row, alpha2):
    title = _slug(row.get("title") or "titrari")
    if (video or {}).get("kind") == "episode":
        season = _safe_int(video.get("season") or row.get("season")) or 1
        episode = _safe_int(video.get("episode") or row.get("episode")) or 0
        return f"titrari.{title}.s{season:02d}e{episode:02d}.{alpha2}.zip"
    year = row.get("year") or (video or {}).get("year") or ""
    return f"titrari.{title}.{year}.{alpha2}.zip"


def _requested_languages(languages):
    rows = []
    seen = set()
    for item in languages or []:
        alpha3 = _alpha3_for_language(item)
        if alpha3 not in SUPPORTED_LANGUAGES:
            continue
        if isinstance(item, dict):
            alpha2 = item.get("alpha2") or SUPPORTED_LANGUAGES[alpha3]
            hi = bool(item.get("hi", False))
            forced = bool(item.get("forced", False))
        else:
            alpha2 = SUPPORTED_LANGUAGES[alpha3]
            hi = False
            forced = False
        key = (alpha3, hi, forced)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"alpha3": alpha3, "alpha2": alpha2, "hi": hi, "forced": forced})
    return rows


def _alpha3_for_language(language):
    if isinstance(language, dict):
        value = language.get("alpha3") or ALPHA2_TO_ALPHA3.get(language.get("alpha2"))
    else:
        value = str(language or "")
    value = (value or "").lower()
    if value == "rum":
        return "ron"
    if value in ALPHA2_TO_ALPHA3:
        return ALPHA2_TO_ALPHA3[value]
    return value


def _media_type_code(kind):
    if kind == "movie":
        return "1"
    if kind == "episode":
        return "2"
    return "0"


def _parse_title(full_title):
    year = None
    year_matches = _YEAR_RE.findall(full_title or "")
    if year_matches:
        year = int(year_matches[-1])
    season = None
    title_without_year = _YEAR_RE.sub("", full_title or "").strip()
    season_match = _SEASON_TITLE_RE.match(full_title or "")
    if season_match:
        title = season_match.group("title").strip()
        season = int(season_match.group("season"))
    else:
        title = title_without_year
    return title, year, season


def _language_from_block(block):
    lowered = block.lower()
    if "[ romana ]" in lowered or "flags/1.gif" in lowered:
        return "ron"
    if "[ engleza ]" in lowered or "flags/2.gif" in lowered:
        return "eng"
    return None


def _imdb_from_block(block):
    match = _IMDB_RE.search(block or "")
    if not match:
        return None
    return f"tt{match.group('id')}"


def _comment_from_block(block):
    match = _COMMENT_RE.search(block or "")
    if match:
        return _strip_tags(match.group("body"))
    return ""


def _field_from_block(pattern, block):
    match = pattern.search(block or "")
    if not match:
        return ""
    return _strip_tags(match.group("value"))


def _downloads_from_block(block):
    text = _strip_tags(block or "")
    match = _DOWNLOAD_COUNT_RE.search(text)
    if not match:
        return 0
    return int(match.group("count"))


def _episode_from_text(text):
    text = text or ""
    match = _SXXEXX_RE.search(text)
    if match:
        return int(match.group("episode"))
    match = _EPISODE_RE.search(text)
    if match:
        return int(match.group("episode"))
    return None


def _is_pack(text):
    return bool(_PACK_RE.search(text or "") or _EPISODE_RANGE_RE.search(text or ""))


def _episode_matches(video, row):
    try:
        episode = int((video or {}).get("episode"))
    except (TypeError, ValueError):
        return False
    row_episode = row.get("episode")
    if row_episode is not None:
        return _same_int(episode, row_episode)
    if not row.get("is_pack"):
        return False
    comments = row.get("comments") or ""
    found_range = False
    for match in _EPISODE_RANGE_RE.finditer(comments):
        found_range = True
        if int(match.group("start")) <= episode <= int(match.group("end")):
            return True
    for match in _SIMPLE_RANGE_RE.finditer(comments):
        found_range = True
        if int(match.group("start")) <= episode <= int(match.group("end")):
            return True
    return not found_range


def _archive_episode_score(name, season, episode):
    basename = os.path.basename(name or "").lower()
    has_structured_episode = False
    best = 0
    for pattern in (_SXXEXX_RE, _XX_RE, _S_SEPARATED_EPISODE_RE):
        for match in pattern.finditer(basename):
            has_structured_episode = True
            if int(match.group("episode")) != episode:
                continue
            if season is None or int(match.group("season")) == season:
                best = max(best, 100)
    if best:
        return best
    if _EPISODE_RE.search(basename):
        for match in _EPISODE_RE.finditer(basename):
            if int(match.group("episode")) == episode:
                return 90
    return _archive_numeric_episode_score(basename, has_structured_episode, episode)


def _select_single_episode_candidate(candidates, score):
    if len(candidates) != 1:
        return None
    only = candidates[0]
    value = score(only)
    if value > 0 or not _archive_has_episode_hint(only):
        return only
    return None


def _best_scored_episode_candidate(candidates, score):
    selected = max(candidates, key=score)
    if score(selected) <= 0:
        raise ValueError("titrari archive does not contain the requested episode")
    return selected


def _archive_numeric_episode_score(basename, has_structured_episode, episode):
    if has_structured_episode:
        return 0
    if re.search(rf"(?<!\d)0*{episode}(?!\d)", _normalize(basename)):
        return 70
    return 0


def _archive_has_episode_hint(name):
    basename = os.path.basename(name or "").lower()
    if any(pattern.search(basename) for pattern in (_SXXEXX_RE, _XX_RE, _S_SEPARATED_EPISODE_RE, _EPISODE_RE)):
        return True
    return bool(re.search(r"(?<!\d)\d{1,3}(?!\d)", _normalize(basename)))


def _release_group_matches(release_group, text):
    release_group = _coerce_text(release_group)
    if not release_group:
        return False
    return release_group.lower() in (text or "").lower()


def _source_matches(source, text):
    source = _normalize(source)
    normalized = _normalize(text)
    if not source:
        return False
    if source in ("web", "webdl", "web dl", "web-dl"):
        return any(token in normalized for token in ("web dl", "web-dl", "webrip", "web rip", "web"))
    if source in ("bluray", "blu ray", "bdrip", "brrip"):
        return any(token in normalized for token in ("bluray", "blu ray", "bdrip", "brrip", "bd rip"))
    return source in normalized


def _token_in_text(token, text):
    token = _coerce_text(token)
    if not token:
        return False
    return token.lower() in (text or "").lower()


def _same_title(left, right):
    left_tokens = _normalize(left).split()
    right_tokens = _normalize(right).split()
    return bool(left_tokens and right_tokens and left_tokens == right_tokens)


def _same_imdb(left, right):
    return bool(_imdb_number(left) and _imdb_number(left) == _imdb_number(right))


def _same_int(left, right):
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _imdb_number(value):
    value = _coerce_text(value)
    if not value:
        return ""
    match = re.search(r"(\d+)", value)
    return match.group(1) if match else ""


def _content_payload(content, fmt, empty=False):
    content = content or b""
    fmt = fmt or "srt"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "encoding": "utf-8",
        "empty": bool(empty),
    }


def _content_type(fmt):
    if fmt == "srt":
        return "application/x-subrip"
    if fmt == "vtt":
        return "text/vtt"
    if fmt == "ass":
        return "text/x-ssa"
    if fmt == "ssa":
        return "text/x-ssa"
    return "application/octet-stream"


def _format_from_filename(filename):
    return _subtitle_extension(filename) or "srt"


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _is_supported_subtitle_name(name):
    base = os.path.basename(name or "")
    return bool(base and not base.startswith(".") and _subtitle_extension(base))


def _looks_like_html(body):
    sample = (body or b"").lstrip()[:1024].lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<body" in sample


def _is_rar_archive(body):
    return bool(body) and (body.startswith(b"Rar!\x1a\x07\x00") or body.startswith(b"Rar!\x1a\x07\x01\x00"))


def _extract_rar_files(body):
    errors = []
    if py7zz is not None:
        try:
            return _extract_rar_files_with_py7zz(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("unar"):
        try:
            return _extract_rar_files_with_unar(body)
        except Exception as error:
            errors.append(error)
    if shutil.which("7z") or shutil.which("7zz"):
        try:
            return _extract_rar_files_with_7z(body)
        except Exception as error:
            errors.append(error)
    if errors:
        details = "; ".join(f"{type(error).__name__}: {error}" for error in errors)
        raise RuntimeError(f"Titrari RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("Titrari RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("Titrari bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titrari.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("Titrari RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titrari.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [unar, "-quiet", "-o", output_dir, archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"unar failed to extract Titrari RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("Titrari RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "titrari.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run(
            [sevenzip, "x", "-y", f"-o{output_dir}", archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"7z failed to extract Titrari RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if not _is_supported_subtitle_name(rel):
                continue
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    if not files:
        raise ValueError("titrari archive contains no supported subtitle files")
    return files


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms", 0))
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)


def _decode(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    for encoding in ("utf-8", "iso-8859-2", "cp1250", "latin-1"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _strip_tags(fragment):
    text = _BR_RE.sub(" ", fragment or "")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _coerce_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _normalize(value):
    text = _coerce_text(value).lower()
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return _WS_RE.sub(" ", text).strip()


def _slug(value):
    normalized = _normalize(value)
    return re.sub(r"[^a-z0-9]+", ".", normalized).strip(".") or "subtitle"


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
