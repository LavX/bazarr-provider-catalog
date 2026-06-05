"""Prijevodi-Online provider for the Bazarr+ Provider Hub catalog."""

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
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in manifest
    py7zz = None

PROVIDER_ID = "prijevodionline"
BASE_URL = "https://www.prijevodi-online.org"
HTTP_TIMEOUT_SECONDS = 10
SUPPORTED_LANGUAGES = {
    "hrv": "hr",
    "srp": "sr",
    "cnr": "me",
    "hbs": "sh",
}
ALPHA2_TO_ALPHA3 = {
    "hr": "hrv",
    "sr": "srp",
    "me": "cnr",
    "cg": "cnr",
    "sh": "hbs",
}
LANGUAGE_BY_SUFFIX = {
    "hr": "hrv",
    "sr": "srp",
    "cg": "cnr",
}
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ssa", ".ass", ".vtt")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_SERIES_ROW_RE = re.compile(r"<tr\b[^>]*id=['\"]serija-(?P<id>\d+)['\"][^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_ANCHOR_RE = re.compile(r"<a\b[^>]*href=['\"](?P<href>[^'\"]+)['\"][^>]*>(?P<body>.*?)</a>", re.I | re.S)
_TITLE_ATTR_RE = re.compile(r"\btitle=['\"](?P<title>[^'\"]+)['\"]", re.I)
_KEY_RE = re.compile(r"epizode\.key\s*=\s*['\"](?P<key>[0-9a-fA-F]{32})['\"]")
_SEASON_RE = re.compile(r"<h3\b[^>]*id=['\"]sezona-(?P<season>\d+)['\"][^>]*>.*?</h3>", re.I | re.S)
_EPISODE_DIV_RE = re.compile(r"<div\b[^>]*id=['\"]epizoda-(?P<id>\d+)['\"][^>]*>(?P<body>.*?)(?=<div\b[^>]*id=['\"]epizoda-\d+['\"]|<h3\b[^>]*id=['\"]sezona-\d+['\"]|</div>\s*</div>|\Z)", re.I | re.S)
_LIST_ITEM_RE = re.compile(r"<li\b[^>]*class=['\"][^'\"]*\b(?P<class>broj|naziv)\b[^'\"]*['\"][^>]*>(?P<body>.*?)</li>", re.I | re.S)
_SUB_ROW_RE = re.compile(r"<tr\b[^>]*id=['\"]prijevod-(?P<id>\d+)['\"][^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_OPIS_RE_TEMPLATE = r"<tr\b[^>]*id=['\"]prijevod-opis-{subtitle_id}['\"][^>]*>(?P<body>.*?)</tr>"
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(?P<body>.*?)</t[dh]>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SXXEXX_RE = re.compile(r"\b(?:s(?P<s1>\d{1,2})e(?P<e1>\d{1,3})|(?P<s2>\d{1,2})x(?P<e2>\d{1,3}))\b", re.I)


def parse_series_index(body):
    text = _decode_html(body)
    rows = []
    for row_match in _SERIES_ROW_RE.finditer(text):
        row = row_match.group("body")
        anchor = _ANCHOR_RE.search(row)
        if not anchor:
            continue
        href = html.unescape(anchor.group("href"))
        parsed = urllib.parse.urlparse(href)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 4 or parts[0:2] != ["serije", "view"]:
            continue
        title = _title_from_anchor(anchor.group(0), anchor.group("body"))
        rows.append(
            {
                "series_id": row_match.group("id"),
                "title": title,
                "slug": parts[3],
                "url": _absolute_url(href),
            }
        )
    return rows


def parse_series_page(body):
    text = _decode_html(body)
    key_match = None
    for match in _KEY_RE.finditer(text):
        key_match = match
    key = key_match.group("key") if key_match else ""
    episodes = {}
    season_matches = list(_SEASON_RE.finditer(text))
    for index, season_match in enumerate(season_matches):
        season = int(season_match.group("season"))
        start = season_match.end()
        end = season_matches[index + 1].start() if index + 1 < len(season_matches) else len(text)
        section = text[start:end]
        for episode_match in _EPISODE_DIV_RE.finditer(section):
            fields = _episode_fields(episode_match.group("body"))
            if fields.get("number") is None:
                continue
            episodes[(season, fields["number"])] = {
                "episode_id": episode_match.group("id"),
                "title": fields.get("title") or "",
            }
    return {"key": key, "episodes": episodes}


def parse_subtitle_rows(body):
    text = _decode_html(body)
    rows = []
    for row_match in _SUB_ROW_RE.finditer(text):
        subtitle_id = row_match.group("id")
        row = row_match.group("body")
        anchor = _ANCHOR_RE.search(row)
        if not anchor:
            continue
        href = html.unescape(anchor.group("href"))
        language = _language_from_href(href)
        if not language:
            continue
        cells = [match.group("body") for match in _CELL_RE.finditer(row)]
        status = _strip_tags(cells[2]) if len(cells) > 2 else ""
        releases = _releases_for_subtitle(text, subtitle_id)
        rows.append(
            {
                "subtitle_id": subtitle_id,
                "language": language,
                "url": _absolute_url(href),
                "filename": _strip_tags(anchor.group("body")),
                "verified": _is_verified_status(status),
                "releases": releases,
            }
        )
    return rows


def derive_matches(video, item):
    matches = []
    if _series_matches((video or {}).get("series"), item.get("series")):
        matches.append("series")
    try:
        if int((video or {}).get("season")) == int(item.get("season")):
            matches.append("season")
        if int((video or {}).get("episode")) == int(item.get("episode")):
            matches.append("episode")
    except (TypeError, ValueError):
        pass
    releases = [_normalize_release(value) for value in item.get("releases") or []]
    release_group = _normalize_release((video or {}).get("release_group"))
    if release_group and any(release_group in release for release in releases):
        matches.append("release_group")
    source = _normalize_release((video or {}).get("source"))
    if source and any(source in release for release in releases):
        matches.append("source")
    resolution = _normalize_release((video or {}).get("resolution"))
    if resolution and any(resolution in release for release in releases):
        matches.append("resolution")
    return matches


class PrijevodiOnlineProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,sr;q=0.8,en-US;q=0.7,en;q=0.6",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def _http_post(self, url, data, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,sr;q=0.8,en-US;q=0.7,en;q=0.6",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "episode":
            return []
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            return []
        requested = _requested_languages(languages)
        if not requested or not (video or {}).get("series"):
            return []

        config = dict(config or {})
        titles = _series_titles(video)
        for series_title in titles:
            _sleep(config)
            series = self._find_series(series_title)
            if not series:
                continue
            _sleep(config)
            series_body = self._http_get(series["url"], referer=_index_url(series_title))
            page = parse_series_page(series_body)
            episode_info = page["episodes"].get((season, episode))
            if not episode_info:
                continue
            subtitles_url = f"{BASE_URL}/prijevod/get/{episode_info['episode_id']}"
            _sleep(config)
            subtitle_rows = parse_subtitle_rows(
                self._http_post(
                    subtitles_url,
                    {"key": page.get("key") or ""},
                    referer=series["url"],
                )
            )
            results = []
            seen = set()
            for row in subtitle_rows:
                output_language = _output_language(row["language"], requested)
                if not output_language:
                    continue
                merged = {
                    **row,
                    "series": series["title"],
                    "season": season,
                    "episode": episode,
                    "episode_id": episode_info["episode_id"],
                    "episode_title": episode_info.get("title") or "",
                    "language": output_language,
                    "source_language": row["language"],
                }
                key = (merged["subtitle_id"], merged["language"])
                if key in seen:
                    continue
                seen.add(key)
                results.append(self._result(video, merged))
            if results:
                return sorted(results, key=lambda item: item["score"], reverse=True)
        return []

    def _find_series(self, title):
        rows = parse_series_index(self._http_get(_index_url(title), referer=BASE_URL + "/serije"))
        wanted = _normalize(title)
        # The site sometimes drops possessive apostrophes (e.g. "Da Vincis
        # Demons" vs "Da Vinci's Demons"), which _normalize turns into a stray
        # word boundary. Comparing the squashed form too keeps those matches.
        wanted_squashed = wanted.replace(" ", "")
        for row in rows:
            candidate = _normalize(row["title"])
            if candidate == wanted or candidate.replace(" ", "") == wanted_squashed:
                return row
        return None

    def _result(self, video, item):
        alpha3 = item["language"]
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        matches = derive_matches(video, item)
        release_info = ", ".join(item.get("releases") or []) or item.get("filename") or ""
        score = 45
        score += 20 if "series" in matches else 0
        score += 15 if "season" in matches else 0
        score += 15 if "episode" in matches else 0
        score += 10 if item.get("verified") else 0
        score += 5 if "release_group" in matches else 0
        filename = (
            f"prijevodionline.{_slug(item.get('series'))}."
            f"s{int(item.get('season')):02d}e{int(item.get('episode')):02d}."
            f"{alpha2}.zip"
        )
        return {
            "provider": PROVIDER_ID,
            "id": f"prijevodionline-{item['subtitle_id']}-{alpha3}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": release_info,
            "filename": filename,
            "matches": matches,
            "score": min(score, 100),
            "score_without_hash": min(score, 100),
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["url"],
            "display": {
                "source": "prijevodi-online.org",
                "title": item.get("series"),
                "release": release_info,
                "verified": bool(item.get("verified")),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": item["subtitle_id"],
                "episode_id": item["episode_id"],
                "url": item["url"],
                "filename": filename,
                "season": item.get("season"),
                "episode": item.get("episode"),
                "language": alpha3,
                "source_language": item.get("source_language"),
                "releases": list(item.get("releases") or []),
            },
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = dict(provider_payload or {})
        url = payload.get("url")
        if not url:
            raise ValueError("prijevodionline download requires url")
        body = self._http_get(url, timeout=30)
        return extract_download(body, payload.get("filename", ""), payload)


def extract_download(body, filename="", payload=None):
    payload = payload or {}
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
    return _content_payload(body, _format_from_filename(filename))


def select_subtitle_file(names, payload):
    candidates = [name for name in names if _subtitle_extension(name)]
    if not candidates:
        raise ValueError("prijevodionline archive contains no supported subtitle files")
    try:
        season = int((payload or {}).get("season"))
        episode = int((payload or {}).get("episode"))
    except (TypeError, ValueError):
        season = episode = None
    releases = [_normalize_release(value) for value in (payload or {}).get("releases") or []]

    def score(index_name):
        index, name = index_name
        del index
        base = os.path.basename(name)
        normalized = _normalize_release(base)
        value = 0
        if season is not None and episode is not None:
            # Match episode tokens as standalone numbers so a single-digit
            # episode (e1/1x1) never matches a longer one (e10/1x10) via a
            # trailing-digit substring.
            tokens = _episode_tokens(base)
            if (season, episode) in tokens:
                value += 100
            elif any(token_episode == episode for _season, token_episode in tokens):
                value += 80
        if releases and any(release and release in normalized for release in releases):
            value += 30
        return value

    return max(enumerate(candidates), key=score)[1]


def _episode_tokens(name):
    # Parse standalone SxxExx / NxNN markers so episode matching never relies
    # on substring containment (which would let e1 match e10 or 1x1 match 1x10).
    tokens = []
    for match in _SXXEXX_RE.finditer(name or ""):
        if match.group("s1") is not None:
            season_value = int(match.group("s1"))
            episode_value = int(match.group("e1"))
        else:
            season_value = int(match.group("s2"))
            episode_value = int(match.group("e2"))
        tokens.append((season_value, episode_value))
    return tokens


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
        raise RuntimeError(f"PrijevodiOnline RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("PrijevodiOnline RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("PrijevodiOnline bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "prijevodionline.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("PrijevodiOnline RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "prijevodionline.rar")
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
            raise RuntimeError(f"unar failed to extract PrijevodiOnline RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("PrijevodiOnline RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "prijevodionline.rar")
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
            raise RuntimeError(f"7z failed to extract PrijevodiOnline RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if not _subtitle_extension(rel):
                continue
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    if not files:
        raise ValueError("prijevodionline archive contains no supported subtitle files")
    return files


def _episode_fields(body):
    fields = {}
    for match in _LIST_ITEM_RE.finditer(body or ""):
        class_name = match.group("class").lower()
        text = _strip_tags(match.group("body"))
        if class_name == "broj":
            try:
                fields["number"] = int(text.rstrip("."))
            except ValueError:
                pass
        elif class_name == "naziv":
            anchor = _ANCHOR_RE.search(match.group("body"))
            fields["title"] = _strip_tags(anchor.group("body") if anchor else match.group("body"))
    return fields


def _releases_for_subtitle(text, subtitle_id):
    pattern = re.compile(_OPIS_RE_TEMPLATE.format(subtitle_id=re.escape(str(subtitle_id))), re.I | re.S)
    match = pattern.search(text or "")
    if not match:
        return []
    cells = [cell.group("body") for cell in _CELL_RE.finditer(match.group("body"))]
    release_text = _strip_tags(cells[-1] if cells else match.group("body"))
    return [part.strip() for part in release_text.split("/") if part.strip()]


def _language_from_href(href):
    slug = os.path.basename(urllib.parse.urlparse(href or "").path).lower()
    for segment in reversed([part for part in slug.split("-") if part]):
        if segment in LANGUAGE_BY_SUFFIX:
            return LANGUAGE_BY_SUFFIX[segment]
    return None


def _requested_languages(languages):
    requested = set()
    for language in languages or []:
        alpha3 = _alpha3_for_language(language)
        if alpha3 in SUPPORTED_LANGUAGES:
            requested.add(alpha3)
    return requested


def _output_language(row_language, requested):
    if row_language in requested:
        return row_language
    if "hbs" in requested and row_language in {"hrv", "srp", "cnr"}:
        return "hbs"
    return None


def _series_titles(video):
    titles = []
    values = [video.get("series")]
    values.extend(video.get("alternative_series") or [])
    for value in values:
        value = str(value or "").strip()
        if value and value not in titles:
            titles.append(value)
    return titles


def _index_url(title):
    title = str(title or "").strip()
    folded = _ascii_fold(title).lower()
    first = folded[:1]
    letter = first if first.isalpha() else "num"
    return f"{BASE_URL}/serije/index/{letter}"


def _title_from_anchor(anchor_html, body):
    title_match = _TITLE_ATTR_RE.search(anchor_html or "")
    if title_match:
        return html.unescape(title_match.group("title")).strip()
    return _strip_tags(body)


def _series_matches(wanted, candidate):
    wanted_tokens = _tokens(wanted)
    candidate_tokens = set(_tokens(candidate))
    return bool(wanted_tokens) and all(token in candidate_tokens for token in wanted_tokens)


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", value)


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


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
    encoding = "utf-8"
    for candidate in ("utf-8", "cp1250", "latin-1"):
        try:
            content.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
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


def _alpha3_for_language(language):
    if isinstance(language, dict):
        alpha3 = (language.get("alpha3") or "").lower()
        if alpha3:
            return alpha3
        return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())
    value = str(language or "").lower()
    return ALPHA2_TO_ALPHA3.get(value, value)


def _is_verified_status(status):
    return _normalize(status) == "provjereno"


def _ascii_fold(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return normalized.encode("ascii", "ignore").decode("ascii")


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(int(delay_ms), 5000) / 1000.0)


def _strip_tags(value):
    stripped = _TAG_RE.sub("", value or "")
    return _WS_RE.sub(" ", html.unescape(stripped)).strip()


def _decode_html(body):
    if isinstance(body, str):
        return body
    raw = body or b""
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _slug(value):
    return "-".join(_tokens(value)) or "release"


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_release(value):
    return _normalize(value).replace(" ", "")
