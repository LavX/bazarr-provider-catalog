"""Zimuku provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar

try:
    import py7zz
except ImportError:  # pragma: no cover, dependency is declared in provider.json
    py7zz = None

PROVIDER_ID = "zimuku"
BASE_URL = "https://srtku.com"
SEARCH_URL = f"{BASE_URL}/search"
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
SUPPORTED_LANGUAGES = {"eng", "zho", "zho-CN", "zho-TW"}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".sub", ".vtt")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_ITEM_RE = re.compile(
    r"<div\b(?=[^>]*\bclass=[\"'][^\"']*\bitem\b)[^>]*>(?P<body>.*?)(?=<div\b(?=[^>]*\bclass=[\"'][^\"']*\bitem\b)|</body>|</html>|\Z)",
    re.I | re.S,
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>(?P<text>.*?)</a>", re.I | re.S)
_ROW_RE = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)
_SXXEXX_RE = re.compile(r"\bs0*(?P<season>\d{1,2})e0*(?P<episode>\d{1,3})\b", re.I)
_X_EPISODE_RE = re.compile(r"\b0*(?P<season>\d{1,2})x0*(?P<episode>\d{1,3})\b", re.I)


@dataclass
class HttpResponse:
    status: int
    content: bytes
    headers: dict
    url: str


def string_to_hex(value):
    return "".join(hex(ord(char))[2:] for char in value or "")


def parse_yunsuo_challenge(body):
    text = _decode(body)
    location_match = re.search(
        r"self\.location\s*=\s*([\"'])(?P<prefix>.*?)\1\s*\+\s*stringToHex\(",
        text,
        re.I | re.S,
    )
    image_match = re.search(
        r"src\s*=\s*([\"'])data:image/(?P<mime>[^;\"']+);base64,(?P<image>.*?)\1",
        text,
        re.I | re.S,
    )
    if not location_match:
        return None
    return {
        "verify_prefix": html.unescape(location_match.group("prefix")),
        "image_b64": html.unescape(image_match.group("image")) if image_match else "",
        "image_mime": f"image/{image_match.group('mime')}" if image_match else "",
    }


def parse_search_results(body, video):
    video = video or {}
    rows = []
    for match in _ITEM_RE.finditer(_decode(body)):
        anchor = _first_anchor(match.group("body"))
        if not anchor:
            continue
        title = _strip_tags(anchor["text"])
        if not title:
            continue
        if video.get("kind") == "episode" and not _season_matches(title, video.get("season")):
            continue
        rows.append(
            {
                "title": title,
                "url": _absolute_url(anchor["href"]),
                "year": _result_year(title, video),
            }
        )
    return rows


def parse_episode_page(body, year=None):
    text = _decode(body)
    rows = []
    seen = set()
    for match in _ROW_RE.finditer(text):
        row = match.group("body")
        anchor = _first_anchor(row)
        if not anchor:
            continue
        release = _extract_name(_strip_tags(anchor["text"]))
        release = os.path.splitext(release)[0]
        if not release:
            continue
        for language in _languages_from_row(row, release):
            key = (anchor["href"], language)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "language": language,
                    "detail_url": _absolute_url(anchor["href"]),
                    "release_info": release,
                    "year": year,
                }
            )
    return rows


def extract_download(body, payload=None):
    payload = payload or {}
    if not body:
        return _content_payload(b"", _format_from_filename(payload.get("filename")), empty=True)
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            selected = select_subtitle_file(archive.namelist(), payload)
            return _content_payload(archive.read(selected), _subtitle_extension(selected) or "srt")
    if _is_rar_archive(body):
        files = _extract_rar_files(body)
        selected = select_subtitle_file([name for name, _data in files], payload)
        return _content_payload(dict(files)[selected], _subtitle_extension(selected) or "srt")
    return _content_payload(body, _format_from_filename(payload.get("filename")))


def select_subtitle_file(names, payload=None):
    payload = payload or {}
    candidates = [name for name in names if _subtitle_extension(name) and not _is_hidden_path(name)]
    if not candidates:
        raise ValueError("zimuku archive contains no supported subtitle files")
    language = _language_code(payload.get("language"))

    def score(name):
        normalized = _normalize(os.path.basename(name))
        value = 0
        if any(token in normalized for token in ("ass", "ssa", "srt")):
            value += 1
        if any(token in normalized for token in ("chs", "gb", "jian", "simplified")):
            value += 2
            if language in {"zho", "zho-CN"}:
                value += 6
        if any(token in normalized for token in ("cht", "big5", "fan", "traditional")):
            value += 2
            if language == "zho-TW":
                value += 6
        if any(token in normalized for token in ("eng", "english")):
            if language == "eng":
                value += 6
            if language.startswith("zho"):
                value += 2
        if any(token in normalized for token in ("bilingual", "chs eng", "cht eng", "zhong ying", "shuang yu")):
            value += 4
        return value

    return max(candidates, key=score)


def derive_matches(video, item):
    video = video or {}
    release = item.get("release_info") if isinstance(item, dict) else item
    normalized = _normalize(release)
    matches = []
    year = item.get("year") if isinstance(item, dict) else None
    if video.get("year") and year and int(video.get("year")) == int(year):
        matches.append("year")
    season, episode = _episode_markers(normalized)
    try:
        video_season = int(video.get("season"))
        video_episode = int(video.get("episode"))
    except (TypeError, ValueError):
        video_season = video_episode = None
    if video.get("kind") == "episode":
        series_tokens = _tokens(video.get("series"))
        if series_tokens and all(token in normalized.split() for token in series_tokens):
            matches.append("series")
        if video_season is not None and season == video_season:
            matches.append("season")
        if video_episode is not None and episode == video_episode:
            matches.append("episode")
        if not video.get("year") and {"series", "season", "episode"}.issubset(matches):
            matches.append("year")
    else:
        title_tokens = _tokens(video.get("title"))
        if title_tokens and all(token in normalized.split() for token in title_tokens):
            matches.append("title")
    if _matches_source(video.get("source"), normalized):
        matches.append("source")
    release_group = _coerce_text(video.get("release_group"))
    if release_group and _normalize_release_group(release_group) in _normalize_release_group(release):
        matches.append("release_group")
    return matches


def compute_score(video, item):
    matches = set(derive_matches(video, item))
    if video.get("kind") == "episode" and {"series", "season", "episode"}.issubset(matches):
        return 100
    if video.get("kind") == "movie" and "title" in matches:
        return 90
    return 70 if matches else 40


class ZimukuProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def search(self, video, languages, config):
        video = video or {}
        if video.get("kind") not in {"movie", "episode"}:
            return []
        requested = {_language_code(language) for language in languages or []}
        requested = {language for language in requested if language in SUPPORTED_LANGUAGES}
        if not requested:
            return []
        results = []
        seen = set()
        for query in _queries(video):
            search_url = f"{SEARCH_URL}?q={urllib.parse.quote_plus(query)}"
            search_response = self._bypass_get(search_url, config or {})
            for result_page in parse_search_results(search_response.content, video):
                _sleep(config)
                rows = parse_episode_page(self._bypass_get(result_page["url"], config or {}, referer=search_url).content, result_page["year"])
                for row in rows:
                    if not _requested(row["language"], requested):
                        continue
                    key = (row["detail_url"], row["language"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, row))
            if results:
                break
        return sorted(results, key=lambda result: result["score"], reverse=True)

    def download(self, provider_payload, language, config):
        provider_payload = dict(provider_payload or {})
        config = dict(config or {})
        detail_url = provider_payload.get("detail_url")
        if not detail_url:
            raise ValueError("zimuku detail_url missing from provider payload")
        selected_language = provider_payload.get("language") or _language_code(language)
        detail = self._bypass_get(detail_url, config)
        down_page_url = _download_page_url(detail.content, detail_url)
        down_page = self._bypass_get(down_page_url, config, referer=detail_url)
        file_url = _final_download_url(down_page.content, down_page_url)
        file_response = self._bypass_get(file_url, config, referer=detail_url)
        filename = _filename_from_headers(file_response.headers) or provider_payload.get("filename")
        payload = dict(provider_payload)
        payload.update({"filename": filename, "language": selected_language})
        return extract_download(file_response.content, payload)

    def _result(self, video, item):
        matches = derive_matches(video, item)
        score = compute_score(video, item)
        language_payload = _language_payload(item["language"])
        filename = f"zimuku.{_slug(item.get('release_info'))}.{item['language']}.zip"
        payload = {
            "provider": PROVIDER_ID,
            "schema": 1,
            "detail_url": item["detail_url"],
            "release_info": item["release_info"],
            "filename": filename,
            "language": item["language"],
            "year": item.get("year"),
        }
        return {
            "id": hashlib.sha1(f"{item['detail_url']}|{item['language']}".encode("utf-8")).hexdigest(),
            "provider": PROVIDER_ID,
            "language": language_payload,
            "release_info": item["release_info"],
            "title": item["release_info"],
            "score": score,
            "matches": matches,
            "hearing_impaired": False,
            "page_link": item["detail_url"],
            "provider_payload": payload,
            "display": item["release_info"],
        }

    def _bypass_get(self, url, config, referer=None):
        current_url = url
        for _attempt in range(4):
            response = self._http_get_response(current_url, referer=referer)
            challenge = parse_yunsuo_challenge(response.content)
            if response.status == 404 and challenge:
                code = self._solve_yunsuo_image(challenge, config or {})
                if not code:
                    raise ValueError("zimuku yunsuo captcha response required")
                self._set_cookie("srcurl", string_to_hex(response.url), response.url)
                verify_url = _absolute_url(f"{challenge['verify_prefix']}{string_to_hex(code)}")
                self._http_get_response(verify_url, referer=response.url, allow_redirects=False)
                current_url = url
                continue
            if response.status >= 400:
                raise RuntimeError(f"zimuku request failed with HTTP {response.status}: {current_url}")
            return response
        raise RuntimeError("zimuku yunsuo verification did not complete")

    def _solve_yunsuo_image(self, challenge, config):
        if config.get("captcha_response"):
            return str(config["captcha_response"])
        solver_url = _coerce_text(config.get("captcha_solver_url"))
        if not solver_url:
            return None
        timeout = max(1, int(config.get("captcha_solver_timeout_ms") or 30000) / 1000)
        payload = {
            "provider": PROVIDER_ID,
            "type": "image_to_text",
            "image_b64": challenge.get("image_b64") or "",
            "image_mime": challenge.get("image_mime") or "image/bmp",
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = _coerce_text(config.get("captcha_solver_token"))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = self._raw_request(solver_url, method="POST", data=json.dumps(payload).encode("utf-8"), headers=headers, timeout=timeout)
        try:
            response = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("zimuku captcha solver returned invalid JSON") from error
        for key in ("response", "text", "token", "captcha_response"):
            if response.get(key):
                return str(response[key])
        return None

    def _http_get_response(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None, allow_redirects=True):
        del allow_redirects
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResponse(
                    getattr(response, "status", 200),
                    response.read(),
                    dict(response.headers.items()),
                    response.geturl(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(error.code, error.read(), dict(error.headers.items()), error.geturl())

    def _raw_request(self, url, method="GET", data=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def _set_cookie(self, name, value, url):
        host = urllib.parse.urlparse(url).hostname or urllib.parse.urlparse(BASE_URL).hostname
        cookie = Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=host,
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        self._cookie_jar.set_cookie(cookie)


def _queries(video):
    title_key = "series" if video.get("kind") == "episode" else "title"
    titles = [video.get(title_key)]
    alt_key = "alternative_series" if video.get("kind") == "episode" else "alternative_titles"
    alternatives = video.get(alt_key) or []
    if isinstance(alternatives, str):
        titles.append(alternatives)
    elif isinstance(alternatives, (list, tuple)):
        titles.extend(alternatives)
    queries = []
    for title in titles:
        if not _coerce_text(title):
            continue
        if video.get("kind") == "episode" and video.get("season"):
            queries.append(f"{title}.S{int(video['season']):02d}")
        elif video.get("year"):
            queries.append(f"{title} {int(video['year'])}")
        else:
            queries.append(str(title))
    return queries


def _requested(language, requested):
    if language in requested:
        return True
    return language.startswith("zho") and "zho" in requested


def _language_code(language):
    if isinstance(language, str):
        return "zho-CN" if language == "zho-CN" else language
    if isinstance(language, dict):
        alpha3 = language.get("alpha3") or ""
        if alpha3 in {"zho-CN", "zho-TW"}:
            return alpha3
        if alpha3 == "zho" and (language.get("country") or "").upper() == "TW":
            return "zho-TW"
        if alpha3 == "zho" and (language.get("country") or "").upper() == "CN":
            return "zho-CN"
        return alpha3
    return ""


def _language_payload(language):
    if language == "zho-TW":
        return {"alpha3": "zho", "alpha2": "zh", "country": "TW"}
    if language == "zho-CN":
        return {"alpha3": "zho", "alpha2": "zh", "country": "CN"}
    if language == "zho":
        return {"alpha3": "zho", "alpha2": "zh"}
    return {"alpha3": "eng", "alpha2": "en"}


def _first_anchor(text):
    for match in _ANCHOR_RE.finditer(text or ""):
        tag = match.group(0)
        href = _attr(tag, "href")
        if href:
            return {"href": href, "text": match.group("text")}
    return None


def _season_matches(title, season):
    try:
        expected = int(season)
    except (TypeError, ValueError):
        return True
    actual = _season_from_title(title)
    return (actual or 1) == expected


def _season_from_title(title):
    match = re.search(r"第\s*(?P<season>[^季]+)\s*季", title or "")
    if not match:
        return None
    return _cn_to_int(match.group("season").strip())


def _cn_to_int(value):
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[1:], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return digits.get(value)


def _result_year(title, video):
    years = [int(item) for item in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", title or "")]
    if not years:
        return video.get("year")
    if video.get("kind") == "episode" and video.get("season"):
        return years[0] - int(video.get("season")) + 1
    return years[0]


def _languages_from_row(row, release):
    found = []
    searchable = f"{row} {release}".lower()
    for tag_match in _IMG_RE.finditer(row):
        tag = tag_match.group(0)
        src_alt = f"{_attr(tag, 'src') or ''} {_attr(tag, 'alt') or ''}".lower()
        if "hongkong" in src_alt or "繁" in src_alt or "cht" in src_alt:
            found.append("zho-TW")
        if "china" in src_alt or "jollyroger" in src_alt or "简" in src_alt or "chs" in src_alt:
            found.append("zho")
        if "english" in src_alt or "英文" in src_alt or re.search(r"\beng\b", src_alt):
            found.append("eng")
    if not found:
        if any(token in searchable for token in ("cht", "big5", "繁体", "繁體")):
            found.append("zho-TW")
        if any(token in searchable for token in ("chs", "gb", "简体", "簡體")):
            found.append("zho")
        if re.search(r"\beng(?:lish)?\b|英文", searchable):
            found.append("eng")
    deduped = []
    for language in found:
        if language not in deduped:
            deduped.append(language)
    return deduped


def _download_page_url(body, base_url):
    text = _decode(body)
    for match in _ANCHOR_RE.finditer(text):
        tag = match.group(0)
        if _attr(tag, "id") == "down1":
            return urllib.parse.urljoin(base_url, _attr(tag, "href"))
    raise ValueError("zimuku download page link was not found")


def _final_download_url(body, base_url):
    text = _decode(body)
    for match in _ANCHOR_RE.finditer(text):
        tag = match.group(0)
        if (_attr(tag, "rel") or "").lower() == "nofollow":
            return urllib.parse.urljoin(base_url, _attr(tag, "href"))
    raise ValueError("zimuku final download link was not found")


def _filename_from_headers(headers):
    value = ""
    for key, header_value in (headers or {}).items():
        if key.lower() == "content-disposition":
            value = header_value
            break
    match = re.search(r"filename\*?=(?:UTF-8''|[\"']?)(?P<name>[^\"';]+)", value or "", re.I)
    return urllib.parse.unquote(match.group("name")) if match else None


def _extract_name(name):
    name, suffix = os.path.splitext(name or "")
    chinese = [match.start(0) for match in re.finditer(r"[\u4e00-\u9fff]", name)]
    latin = [match.start(0) for match in re.finditer(r"[a-zA-Z0-9]", name)]
    if not latin:
        return ""
    first_latin, last_latin = latin[0], latin[-1]
    first_chinese = chinese[0] if chinese else -1
    last_chinese = chinese[-1] if chinese else -1
    if last_chinese < first_latin:
        cleaned = name[first_latin:]
    elif last_latin < first_chinese:
        cleaned = name[:first_chinese]
    else:
        best = (0, 0)
        start = None
        for index, char in enumerate(name):
            if re.match(r"[a-zA-Z0-9 ._\-\[\]()]+", char):
                if start is None:
                    start = index
            elif start is not None:
                if index - start > best[1] - best[0]:
                    best = (start, index)
                start = None
        if start is not None and len(name) - start > best[1] - best[0]:
            best = (start, len(name))
        cleaned = name[best[0]:best[1]]
    return cleaned.strip(" ._-") + suffix


def _absolute_url(value):
    value = html.unescape((value or "").strip())
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"{BASE_URL}{value}"
    return f"{BASE_URL}/{value.lstrip('/')}"


def _attr(tag, name):
    match = re.search(rf"\b{name}\s*=\s*([\"'])(?P<value>.*?)\1", tag or "", re.I | re.S)
    return html.unescape(match.group("value")) if match else None


def _strip_tags(value):
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _decode(body):
    if isinstance(body, str):
        return body
    return (body or b"").decode("utf-8", errors="replace")


def _coerce_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize(value):
    value = unicodedata.normalize("NFKD", _coerce_text(value)).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM_RE.sub(" ", value.lower()).strip()


def _tokens(value):
    return _normalize(value).split()


def _slug(value):
    slug = "-".join(_tokens(value))[:90]
    return slug or "subtitle"


def _episode_markers(normalized):
    for pattern in (_SXXEXX_RE, _X_EPISODE_RE):
        match = pattern.search(normalized or "")
        if match:
            return int(match.group("season")), int(match.group("episode"))
    return None, None


def _matches_source(value, normalized_text):
    if not _coerce_text(value):
        return False
    token = _normalize(value)
    aliases = {
        "blu ray": {"bluray", "brrip", "bdrip"},
        "bluray": {"bluray", "brrip", "bdrip"},
        "web": {"web", "webdl", "webrip"},
        "hdtv": {"hdtv"},
    }
    return token in normalized_text.split() or bool(aliases.get(token, set()) & set(normalized_text.split()))


def _normalize_release_group(value):
    return re.sub(r"[^a-z0-9]+", "", (_coerce_text(value) or "").lower())


def _subtitle_extension(name):
    lowered = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lowered.endswith(extension):
            return extension[1:]
    return None


def _format_from_filename(filename):
    return _subtitle_extension(filename or "") or "srt"


def _is_hidden_path(name):
    return any(part.startswith(".") for part in (name or "").split("/"))


def _is_rar_archive(body):
    return bool(body) and (
        body.startswith(b"Rar!\x1a\x07\x00")
        or body.startswith(b"Rar!\x1a\x07\x01\x00")
    )


def _extract_rar_files(body):
    if py7zz is None:
        raise RuntimeError("Zimuku RAR extraction requires bundled py7zz")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "zimuku.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _collect_extracted_subtitle_files(output_dir):
    files = []
    for root, _dirs, filenames in os.walk(output_dir):
        for filename in filenames:
            path = os.path.join(root, filename)
            rel = os.path.relpath(path, output_dir)
            if _subtitle_extension(rel) and not _is_hidden_path(rel):
                with open(path, "rb") as handle:
                    files.append((rel, handle.read()))
    if not files:
        raise ValueError("zimuku archive contains no supported subtitle files")
    return files


def _content_payload(content, subtitle_format="srt", empty=False):
    content = _normalize_line_endings(content or b"")
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format or "srt",
        "encoding": "utf-8",
        "empty": bool(empty),
    }


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    return "application/x-subrip"


def _normalize_line_endings(content):
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _decode_payload_text(payload):
    return base64.b64decode(payload["content_b64"]).decode("utf-8", errors="replace")


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms") or 0)
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000)
