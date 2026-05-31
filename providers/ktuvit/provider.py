"""Ktuvit provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookies import SimpleCookie

PROVIDER_ID = "ktuvit"
BASE_URL = "https://www.ktuvit.me"
LOGIN_URL = f"{BASE_URL}/Services/MembershipService.svc/Login"
SEARCH_URL = f"{BASE_URL}/Services/ContentProvider.svc/SearchPage_search"
MOVIE_INFO_URL = f"{BASE_URL}/MovieInfo.aspx?ID="
EPISODE_INFO_URL = f"{BASE_URL}/Services/GetModuleAjax.ashx"
REQUEST_DOWNLOAD_ID_URL = f"{BASE_URL}/Services/ContentProvider.svc/RequestSubtitleDownload"
DOWNLOAD_URL = f"{BASE_URL}/Services/DownloadFile.ashx?DownloadIdentifier="
TMDB_API_KEY = "a51ee051bcd762543373903de296e0a3"
DEFAULT_USER_AGENT = "BazarrProviderHub/1.0"
HTTP_TIMEOUT_SECONDS = 30
NO_SUBTITLE_TEXT = "\u05d0\u05d9\u05df \u05db\u05ea\u05d5\u05d1\u05d9\u05d5\u05ea"


class HttpResponse:
    def __init__(self, status, body, headers):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})


class KtuvitProvider:
    def __init__(self):
        self._authenticated = False
        self._cookies = {}

    def search(self, video, languages, config):
        video = video or {}
        if "heb" not in _requested_languages(languages):
            return []
        config = dict(config or {})
        cookies = self._ensure_authenticated(config)
        is_movie = video.get("kind") != "episode"
        titles = _titles(video, is_movie)
        for title in titles:
            imdb_id = _video_imdb_id(video, is_movie)
            if not imdb_id:
                imdb_id = self._search_imdb_id(title, video.get("year"), is_movie, cookies)
            if not imdb_id:
                continue
            results = self._query(title, video, imdb_id, is_movie, cookies)
            if results:
                return [_candidate(item, video, is_movie) for item in results]
        return []

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        ktuvit_id = payload.get("ktuvit_id")
        subtitle_id = payload.get("subtitle_id")
        if not ktuvit_id or not subtitle_id:
            raise ValueError("ktuvit download requires ktuvit_id and subtitle_id")
        config = dict(config or {})
        cookies = self._ensure_authenticated(config)
        request = {
            "FilmID": ktuvit_id,
            "SubtitleID": subtitle_id,
            "FontSize": 0,
            "FontColor": "",
            "PredefinedLayout": -1,
        }
        response = self._http_post(
            REQUEST_DOWNLOAD_ID_URL,
            {"request": request},
            self._headers(),
            cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        _raise_for_status(response, "Ktuvit download identifier")
        download_identifier = _parse_d_response(response, "DownloadIdentifier")
        file_response = self._http_get(
            f"{DOWNLOAD_URL}{urllib.parse.quote(str(download_identifier))}",
            self._headers(),
            cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        _raise_for_status(file_response, "Ktuvit download")
        body = _normalize_line_endings(file_response.body)
        if not body:
            raise ValueError("ktuvit downloaded empty subtitle")
        return _content_payload(body, _format_from_filename(payload.get("filename") or payload.get("release_info") or "ktuvit.srt"))

    def _ensure_authenticated(self, config):
        if self._authenticated:
            return dict(self._cookies)
        email = str(config.get("email") or "").strip()
        hashed_password = str(config.get("hashed_password") or "")
        if not email or not hashed_password:
            raise PermissionError("Ktuvit requires email and hashed_password")
        response = self._http_post(
            LOGIN_URL,
            {"request": {"Email": email, "Password": hashed_password}},
            self._headers(),
            dict(self._cookies),
            timeout=10,
        )
        _raise_for_status(response, "Ktuvit login")
        success = _parse_d_response(response, "IsSuccess", False)
        if not success:
            error_message = _parse_d_response(response, "ErrorMessage", "authentication failed")
            raise PermissionError(f"Ktuvit login failed: {error_message}")
        _store_response_cookies(self._cookies, response)
        if "Login" not in self._cookies:
            raise PermissionError("Ktuvit login did not return a Login cookie")
        self._authenticated = True
        return dict(self._cookies)

    def _query(self, title, video, imdb_id, is_movie, cookies):
        query = {
            "FilmName": title,
            "Actors": [],
            "Studios": [],
            "Directors": [],
            "Genres": [],
            "Countries": [],
            "Languages": [],
            "Year": video.get("year") or "",
            "Rating": [],
            "Page": 1,
            "SearchType": "0" if is_movie else "1",
            "WithSubsOnly": False,
        }
        response = self._http_post(SEARCH_URL, {"request": query}, self._headers(), cookies, timeout=10)
        _raise_for_status(response, "Ktuvit search")
        films = _parse_d_response(response, "Films", [])
        results = []
        for film in films:
            result_imdb_id = _imdb_from_link(film.get("IMDB_Link"))
            if result_imdb_id != imdb_id:
                continue
            ktuvit_id = str(film.get("ID") or "")
            if not ktuvit_id:
                continue
            if is_movie:
                subs = self._search_movie_subtitles(ktuvit_id, cookies)
            else:
                subs = self._search_episode_subtitles(
                    ktuvit_id,
                    video.get("season"),
                    video.get("episode"),
                    cookies,
                )
            for sub in subs:
                results.append(
                    {
                        "kind": "movie" if is_movie else "episode",
                        "title": title,
                        "series": title,
                        "year": video.get("year"),
                        "season": _int_or_none(video.get("season")),
                        "episode": _int_or_none(video.get("episode")),
                        "imdb_id": imdb_id,
                        "ktuvit_id": ktuvit_id,
                        "subtitle_id": sub["subtitle_id"],
                        "release_info": sub["release_info"],
                        "language": {"alpha3": "heb", "hi": False, "forced": False},
                        "page_url": f"{MOVIE_INFO_URL}{ktuvit_id}",
                    }
                )
        return results

    def _search_movie_subtitles(self, ktuvit_id, cookies):
        response = self._http_get(f"{MOVIE_INFO_URL}{urllib.parse.quote(str(ktuvit_id))}", self._headers(), cookies, timeout=10)
        _raise_for_status(response, "Ktuvit movie subtitles")
        return parse_movie_subtitles(response.body)

    def _search_episode_subtitles(self, ktuvit_id, season, episode, cookies):
        url = (
            f"{EPISODE_INFO_URL}?moduleName=SubtitlesList&SeriesID={urllib.parse.quote(str(ktuvit_id))}"
            f"&Season={int(season)}&Episode={int(episode)}"
        )
        response = self._http_get(url, self._headers(), cookies, timeout=10)
        _raise_for_status(response, "Ktuvit episode subtitles")
        return parse_episode_subtitles(response.body)

    def _search_imdb_id(self, title, year, is_movie, cookies):
        del cookies
        category = "movie" if is_movie else "tv"
        query = str(title or "").replace("'", "")
        params = {
            "api_key": TMDB_API_KEY,
            "query": query,
            "language": "en",
        }
        if year:
            params["year"] = str(year)
        search_url = f"http://api.tmdb.org/3/search/{category}?{urllib.parse.urlencode(params)}"
        response = self._http_get(search_url, self._headers(), {}, timeout=10)
        _raise_for_status(response, "TMDB search")
        results = json.loads(response.body.decode("utf-8", "ignore")).get("results") or []
        if not results:
            return None
        tmdb_id = results[0].get("id")
        if not tmdb_id:
            return None
        suffix = "" if is_movie else "/external_ids"
        detail_url = f"http://api.tmdb.org/3/{category}/{tmdb_id}{suffix}?{urllib.parse.urlencode({'api_key': TMDB_API_KEY, 'language': 'en'})}"
        detail_response = self._http_get(detail_url, self._headers(), {}, timeout=10)
        _raise_for_status(detail_response, "TMDB detail")
        imdb_id = json.loads(detail_response.body.decode("utf-8", "ignore")).get("imdb_id")
        return str(imdb_id) if imdb_id else None

    def _headers(self):
        return {
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-us,en;q=0.5",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "Pragma": "no-cache",
            "User-Agent": DEFAULT_USER_AGENT,
        }

    def _http_get(self, url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS):
        return _http_request("GET", url, headers, cookies, timeout=timeout)

    def _http_post(self, url, json_data, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS):
        return _http_request("POST", url, headers, cookies, json_data=json_data, timeout=timeout)


def _http_request(method, url, headers, cookies, json_data=None, timeout=HTTP_TIMEOUT_SECONDS):
    request_headers = dict(headers or {})
    if cookies:
        request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    body = None
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read(), dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read(), dict(exc.headers.items()))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ktuvit request failed: {exc.reason}") from exc


def _raise_for_status(response, context):
    if response.status >= 400:
        raise RuntimeError(f"{context} failed with HTTP {response.status}")


def _parse_d_response(response, field, default_value=None):
    outer = json.loads((response.body or b"{}").decode("utf-8", "ignore"))
    if "d" not in outer:
        raise ValueError("Ktuvit response did not include d payload")
    inner = json.loads(outer["d"])
    return inner.get(field, default_value)


def parse_movie_subtitles(body):
    root = _parse_html(body)
    subtitles = []
    for table in root.descendants("table"):
        if table.attrs.get("id") != "subtitlesList":
            continue
        for row in table.descendants("tr"):
            sub = _parse_subtitle_row(row, movie=True)
            if sub:
                subtitles.append(sub)
    return subtitles


def parse_episode_subtitles(body):
    if len(body or b"") < 10:
        return []
    root = _parse_html(body)
    first_row = next(iter(root.descendants("tr")), None)
    if first_row is not None:
        first_cell = next(iter(first_row.direct_children("td")), None)
        if first_cell is not None and first_cell.text() == NO_SUBTITLE_TEXT:
            return []
    subtitles = []
    for row in root.descendants("tr"):
        sub = _parse_subtitle_row(row, movie=False)
        if sub:
            subtitles.append(sub)
    return subtitles


def _parse_subtitle_row(row, movie):
    cells = row.direct_children("td")
    if len(cells) < 6:
        return None
    release_info = cells[0].text().strip()
    if not release_info:
        return None
    id_attr = "data-subtitle-id" if movie else "data-sub-id"
    subtitle_id = None
    for node in cells[5].descendants_any({"a", "input"}):
        if node.attrs.get(id_attr):
            subtitle_id = node.attrs[id_attr]
            break
    if not subtitle_id:
        return None
    return {"subtitle_id": subtitle_id, "release_info": release_info}


def _candidate(item, video, is_movie):
    matches = []
    if is_movie:
        matches.append("title")
    else:
        matches.extend(["series", "season", "episode", "series_imdb_id"])
    release_group = _clean_key(video.get("release_group") or "")
    if release_group and release_group in _clean_key(item.get("release_info") or ""):
        matches.append("release_group")
    score = min(100, 20 * len(matches))
    filename = _clean_filename(item["release_info"]) + ".srt"
    return {
        "provider": PROVIDER_ID,
        "id": str(item["subtitle_id"]),
        "language": item["language"],
        "release_info": item["release_info"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hearing_impaired_verifiable": False,
        "hearing_impaired": False,
        "page_link": item["page_url"],
        "display": {
            "source": "ktuvit.me",
            "release": item["release_info"],
            "uploader": None,
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "page_url": item["page_url"],
            "ktuvit_id": item["ktuvit_id"],
            "subtitle_id": item["subtitle_id"],
            "release_info": item["release_info"],
            "filename": filename,
            "season": item.get("season"),
            "episode": item.get("episode"),
            "imdb_id": item.get("imdb_id"),
        },
    }


class _Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = {key: value for key, value in (attrs or [])}
        self.children = []
        self.data = []

    def append(self, child):
        self.children.append(child)

    def text(self):
        parts = list(self.data)
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def direct_children(self, tag):
        return [child for child in self.children if child.tag == tag]

    def descendants(self, tag):
        found = []
        for child in self.children:
            if child.tag == tag:
                found.append(child)
            found.extend(child.descendants(tag))
        return found

    def descendants_any(self, tags):
        found = []
        for child in self.children:
            if child.tag in tags:
                found.append(child)
            found.extend(child.descendants_any(tags))
        return found


class _TreeBuilder(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), attrs)
        self.stack[-1].append(node)
        if tag.lower() not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        wanted = tag.lower()
        while len(self.stack) > 1:
            node = self.stack.pop()
            if node.tag == wanted:
                return

    def handle_data(self, data):
        text = html.unescape(data).strip()
        if text:
            self.stack[-1].data.append(text)


def _parse_html(body):
    parser = _TreeBuilder()
    parser.feed((body or b"").decode("utf-8", "ignore") if isinstance(body, bytes) else str(body or ""))
    return parser.root


def _store_response_cookies(target, response):
    header = _header(response.headers, "set-cookie")
    if not header:
        return
    cookie = SimpleCookie()
    cookie.load(header)
    for key, morsel in cookie.items():
        target[key] = morsel.value


def _requested_languages(languages):
    return {str(item.get("alpha3")) for item in languages or [] if isinstance(item, dict) and item.get("alpha3")}


def _titles(video, is_movie):
    if is_movie:
        values = [video.get("title")] + list(video.get("alternative_titles") or [])
    else:
        values = [video.get("series")] + list(video.get("alternative_series") or [])
    return [value for value in values if value]


def _video_imdb_id(video, is_movie):
    value = video.get("imdb_id") if is_movie else video.get("series_imdb_id")
    return str(value) if value else None


def _imdb_from_link(value):
    text = str(value or "").rstrip("/")
    return text.split("/")[-1] if text else None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _header(headers, name):
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _clean_key(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def _clean_filename(value):
    text = re.sub(r"[^A-Za-z0-9]+", ".", str(value or "")).strip(".")
    return text or "ktuvit"


def _format_from_filename(filename):
    lower = urllib.parse.urlparse(str(filename or "")).path.lower()
    for extension in (".srt", ".ass", ".ssa", ".vtt", ".sub"):
        if lower.endswith(extension):
            return extension.lstrip(".")
    return "srt"


def _normalize_line_endings(body):
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_payload(body, fmt):
    try:
        body.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = "latin-1"
    return {
        "content_b64": base64.b64encode(body).decode("ascii"),
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
        "encoding": encoding,
        "empty": False,
    }


def _content_type(fmt):
    if fmt in {"ass", "ssa"}:
        return "text/x-ssa"
    if fmt == "vtt":
        return "text/vtt"
    if fmt == "sub":
        return "text/plain"
    return "application/x-subrip"
