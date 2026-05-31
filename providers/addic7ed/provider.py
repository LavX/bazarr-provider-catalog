"""Addic7ed provider for the Bazarr+ Provider Hub catalog."""

import base64
import datetime as _datetime
import hashlib
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.cookies import SimpleCookie

PROVIDER_ID = "addic7ed"
BASE_URL = "https://www.addic7ed.com"
DEFAULT_USER_AGENT = "BazarrProviderHub/1.0"
HTTP_TIMEOUT_SECONDS = 30
DOWNLOAD_CAP = 40
VIP_DOWNLOAD_CAP = 80
DOWNLOAD_WINDOW = _datetime.timedelta(hours=24)

LANGUAGE_NAMES = {
    "arabic": "ara",
    "azerbaijani": "aze",
    "bengali": "ben",
    "bosnian": "bos",
    "bulgarian": "bul",
    "catalan": "cat",
    "catala": "cat",
    "czech": "ces",
    "danish": "dan",
    "german": "deu",
    "greek": "ell",
    "english": "eng",
    "basque": "eus",
    "euskera": "eus",
    "persian": "fas",
    "farsi": "fas",
    "finnish": "fin",
    "french": "fra",
    "galician": "glg",
    "hebrew": "heb",
    "croatian": "hrv",
    "hungarian": "hun",
    "armenian": "hye",
    "indonesian": "ind",
    "italian": "ita",
    "japanese": "jpn",
    "korean": "kor",
    "macedonian": "mkd",
    "malay": "msa",
    "dutch": "nld",
    "norwegian": "nor",
    "polish": "pol",
    "portuguese": "por",
    "portuguese (brazilian)": "por-BR",
    "brazilian portuguese": "por-BR",
    "romanian": "ron",
    "russian": "rus",
    "slovak": "slk",
    "slovenian": "slv",
    "spanish": "spa",
    "albanian": "sqi",
    "serbian": "srp",
    "serbian latin": "srp",
    "serbian cyrillic": "srp",
    "swedish": "swe",
    "thai": "tha",
    "turkish": "tur",
    "ukrainian": "ukr",
    "vietnamese": "vie",
    "chinese": "zho",
}


class HttpResponse:
    def __init__(self, status, body, headers):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})


class Addic7edProvider:
    _download_times = []

    def __init__(self):
        self._authenticated = False
        self._session_cookies = {}

    def search(self, video, languages, config):
        video = video or {}
        requested = _requested_languages(languages)
        if not requested:
            return []
        config = dict(config or {})
        cookies = self._ensure_authenticated(config)
        if video.get("kind") == "movie":
            return self._search_movie(video, requested, config, cookies)
        return self._search_episode(video, requested, config, cookies)

    def download(self, provider_payload, language, config):
        del language
        payload = dict(provider_payload or {})
        download_link = payload.get("download_link")
        if not download_link:
            raise ValueError("addic7ed download requires download_link")
        config = dict(config or {})
        cookies = self._ensure_authenticated(config)
        self._check_download_cap(config)
        headers = self._headers(config)
        page_url = payload.get("page_url") or BASE_URL
        headers["Referer"] = page_url
        response = self._http_get(
            urllib.parse.urljoin(f"{BASE_URL}/", download_link),
            headers,
            cookies,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        _raise_for_status(response, "Addic7ed download")
        if "text/html" in _header(response.headers, "content-type").lower():
            raise RuntimeError("Addic7ed download limit exceeded")
        body = _normalize_line_endings(response.body)
        if not body:
            raise ValueError("addic7ed downloaded empty subtitle")
        self._record_download()
        return _content_payload(body, _format_from_filename(payload.get("filename") or download_link))

    def _search_episode(self, video, requested, config, cookies):
        series_names = [video.get("series")] + list(video.get("alternative_series") or [])
        series_names = [name for name in series_names if name]
        for series in series_names:
            show_id = self._get_show_id(series, video.get("year"), config, cookies)
            if not show_id:
                continue
            results = [
                item
                for item in self._query_episode(show_id, series, video, config, cookies)
                if item["language"]["alpha3"] in requested and item["episode"] == _int_or_none(video.get("episode"))
            ]
            if results:
                return [_candidate(item, video) for item in results]
        return []

    def _search_movie(self, video, requested, config, cookies):
        titles = [video.get("title")] + list(video.get("alternative_titles") or [])
        titles = [title for title in titles if title]
        for title in titles:
            movie_id = self._get_movie_id(title, video.get("year"), config, cookies)
            if not movie_id:
                continue
            results = [
                item
                for item in self._query_movie(movie_id, title, video, config, cookies)
                if item["language"]["alpha3"] in requested
            ]
            if results:
                return [_candidate(item, video) for item in results]
        return []

    def _get_show_id(self, series, year, config, cookies):
        response = self._http_get(f"{BASE_URL}/shows.php", self._headers(config), cookies, timeout=10)
        _raise_for_status(response, "Addic7ed shows lookup")
        show_ids = parse_show_ids(response.body)
        for key in _show_keys(series, year):
            if key in show_ids:
                return show_ids[key]
        return None

    def _get_movie_id(self, title, year, config, cookies):
        response = self._http_get(
            f"{BASE_URL}/search.php",
            self._headers(config),
            cookies,
            timeout=10,
            params={"search": title},
        )
        _raise_for_status(response, "Addic7ed movie lookup")
        return parse_movie_id(response.body, title, year)

    def _query_episode(self, show_id, series, video, config, cookies):
        headers = self._headers(config)
        headers["Referer"] = f"{BASE_URL}/show/{show_id}"
        headers["X-Requested-With"] = "XMLHttpRequest"
        season = _int_or_none(video.get("season"))
        response = self._http_get(
            f"{BASE_URL}/ajax_loadShow.php",
            headers,
            cookies,
            timeout=10,
            params={"show": str(show_id), "season": season, "langs": "|"},
        )
        _raise_for_status(response, "Addic7ed episode query")
        return parse_episode_rows(response.body, series, video)

    def _query_movie(self, movie_id, title, video, config, cookies):
        headers = self._headers(config)
        headers["Referer"] = BASE_URL
        headers["X-Requested-With"] = "XMLHttpRequest"
        response = self._http_get(f"{BASE_URL}/movie/{movie_id}", headers, cookies, timeout=10)
        _raise_for_status(response, "Addic7ed movie query")
        return parse_movie_rows(response.body, movie_id, title, video)

    def _ensure_authenticated(self, config):
        cookies = self._cookies(config)
        if self._authenticated:
            return cookies
        if cookies:
            response = self._http_get(
                f"{BASE_URL}/panel.php",
                self._headers(config),
                cookies,
                timeout=10,
                allow_redirects=False,
            )
            if response.status == 302:
                raise PermissionError("Addic7ed cookies are not valid anymore")
            _raise_for_status(response, "Addic7ed cookie check")
            self._authenticated = True
            return cookies
        username = str(config.get("username") or "").strip()
        password = str(config.get("password") or "")
        if not username or not password:
            raise PermissionError("Addic7ed requires username and password credentials or cookies")
        login_page = self._http_get(f"{BASE_URL}/login.php", self._headers(config), cookies, timeout=10)
        _raise_for_status(login_page, "Addic7ed login page")
        _store_response_cookies(self._session_cookies, login_page)
        if b"g-recaptcha" in login_page.body or b"grecaptcha" in login_page.body:
            raise PermissionError("Addic7ed login requires captcha solving; configure cookies instead")
        login_cookies = self._cookies(config)
        response = self._http_post(
            f"{BASE_URL}/dologin.php",
            {
                "username": username,
                "password": password,
                "Submit": "Log in",
                "url": "",
                "remember": "true",
            },
            self._headers(config, referer=f"{BASE_URL}/login.php"),
            login_cookies,
            timeout=10,
            allow_redirects=False,
        )
        if b"relax, slow down" in response.body.lower():
            raise RuntimeError("Addic7ed rate limit exceeded")
        if b"Wrong password" in response.body or b"doesn't exist" in response.body:
            raise PermissionError("Addic7ed username or password is invalid")
        if response.status != 302:
            raise PermissionError("Addic7ed login did not complete")
        _store_response_cookies(self._session_cookies, response)
        self._authenticated = True
        return self._cookies(config)

    def _headers(self, config, referer=BASE_URL):
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": referer,
            "User-Agent": str(config.get("user_agent") or "").strip() or DEFAULT_USER_AGENT,
        }

    def _cookies(self, config):
        cookies = dict(self._session_cookies)
        cookies.update(_parse_cookies(config))
        return cookies

    def _http_get(self, url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, params=None, allow_redirects=True):
        return _http_request(
            "GET",
            url,
            headers,
            cookies,
            timeout=timeout,
            params=params,
            allow_redirects=allow_redirects,
        )

    def _http_post(
        self,
        url,
        data,
        headers,
        cookies,
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=True,
    ):
        return _http_request(
            "POST",
            url,
            headers,
            cookies,
            data=data,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )

    def _check_download_cap(self, config):
        now = _datetime.datetime.now()
        self.__class__._download_times = [item for item in self.__class__._download_times if item + DOWNLOAD_WINDOW > now]
        cap = VIP_DOWNLOAD_CAP if config.get("vip") or config.get("is_vip") else DOWNLOAD_CAP
        if len(self.__class__._download_times) >= cap:
            raise RuntimeError(f"Addic7ed downloads per day exceeded ({cap})")

    def _record_download(self):
        self.__class__._download_times.append(_datetime.datetime.now())


def _http_request(
    method,
    url,
    headers,
    cookies,
    data=None,
    timeout=HTTP_TIMEOUT_SECONDS,
    params=None,
    allow_redirects=True,
):
    if params:
        delimiter = "&" if urllib.parse.urlsplit(url).query else "?"
        url = f"{url}{delimiter}{urllib.parse.urlencode(params)}"
    request_headers = dict(headers or {})
    if cookies:
        request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    opener = urllib.request.build_opener()
    if not allow_redirects:
        opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read(), dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read(), dict(exc.headers.items()))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Addic7ed request failed: {exc.reason}") from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _raise_for_status(response, context):
    if response.status == 304:
        raise RuntimeError("Addic7ed rate limit exceeded")
    if response.status >= 400:
        raise RuntimeError(f"{context} failed with HTTP {response.status}")


def _parse_cookies(config):
    value = str((config or {}).get("cookies") or "").strip()
    if not value:
        return {}
    cookie = SimpleCookie()
    cookie.load(value)
    return {key: morsel.value for key, morsel in cookie.items()}


def _store_response_cookies(target, response):
    cookie_header = _header(response.headers, "set-cookie")
    if not cookie_header:
        return
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    for key, morsel in cookie.items():
        target[key] = morsel.value


def _requested_languages(languages):
    return {str(item.get("alpha3")) for item in languages or [] if isinstance(item, dict) and item.get("alpha3")}


def parse_show_ids(body):
    root = _parse_html(body)
    show_ids = {}
    for link in root.descendants("a"):
        href = link.attrs.get("href", "")
        if not href.startswith(("/show/", "show/")):
            continue
        show_id = href.rstrip("/").split("/")[-1]
        if not show_id.isdigit():
            continue
        for key in _show_text_keys(link.text()):
            show_ids.setdefault(key, show_id)
    return show_ids


def parse_movie_id(body, title, year):
    root = _parse_html(body)
    wanted = _clean_key(title)
    for link in root.descendants("a"):
        href = link.attrs.get("href", "")
        if not href.startswith(("movie/", "/movie/")):
            continue
        movie_id = href.strip("/").split("/")[-1]
        match = re.search(r"(.+)\s\((\d{4})\)$", link.text())
        if not match:
            continue
        if _clean_key(match.group(1)) == wanted and str(year or "") == match.group(2):
            return movie_id
    return None


def parse_episode_rows(body, series, video):
    root = _parse_html(body)
    results = []
    for row in root.descendants("tr"):
        if "epeven" not in row.classes():
            continue
        cells = row.direct_children("td")
        if len(cells) < 10 or "%" in cells[5].text():
            continue
        language = _language_from_name(cells[3].text())
        if not language:
            continue
        season = _int_or_none(cells[0].text())
        episode = _int_or_none(cells[1].text())
        if season != _int_or_none(video.get("season")):
            continue
        page_link = urllib.parse.urljoin(f"{BASE_URL}/", cells[2].first_link() or "")
        download_link = _strip_leading_slash(cells[9].first_link() or "")
        if not page_link or not download_link:
            continue
        release_info = _normalize_release_info(cells[4].text())
        hi = bool(cells[6].text().strip())
        results.append(
            {
                "kind": "episode",
                "series": series,
                "season": season,
                "episode": episode,
                "title": cells[2].text(),
                "year": _int_or_none(video.get("year")),
                "release_info": release_info,
                "language": {"alpha3": language, "hi": hi, "forced": False},
                "download_link": download_link,
                "page_url": page_link,
                "uploader": None,
            }
        )
    return results


def parse_movie_rows(body, movie_id, title, video):
    root = _parse_html(body)
    results = []
    for table in root.descendants("table"):
        if "tabel95" not in table.classes():
            continue
        texts = [td.text() for td in table.descendants("td")]
        language = next((_language_from_name(text) for text in texts if _language_from_name(text)), None)
        if not language:
            continue
        if any("%" in text for text in texts if "completed" not in text.lower()):
            continue
        download_link = None
        for link in table.descendants("a"):
            if "download" in link.text().lower() or "download" in link.attrs.get("href", "").lower():
                download_link = _strip_leading_slash(link.attrs.get("href", ""))
                break
        if not download_link:
            continue
        version = ""
        for text in texts:
            if text.lower().startswith("version "):
                version = " ".join(text.split()[1:])
                version = version.split(",", 1)[0].strip()
                break
        uploader = None
        for td in table.descendants("td"):
            if "uploader" in td.classes():
                uploader = td.text()
                break
        hi = any((img.attrs.get("src") or "").endswith("hi.jpg") for img in table.descendants("img"))
        results.append(
            {
                "kind": "movie",
                "title": title,
                "year": _int_or_none(video.get("year")),
                "release_info": _normalize_release_info(version),
                "language": {"alpha3": language, "hi": hi, "forced": False},
                "download_link": download_link,
                "page_url": f"{BASE_URL}/movie/{movie_id}",
                "uploader": uploader,
            }
        )
    return results


def _candidate(item, video):
    matches = []
    if item["kind"] == "episode":
        matches.extend(["series", "season", "episode"])
        if item.get("year"):
            matches.append("year")
    else:
        matches.append("title")
        if item.get("year"):
            matches.append("year")
    release_group = _clean_key(video.get("release_group") or "")
    if release_group and release_group in _clean_key(item.get("release_info") or ""):
        matches.append("release_group")
        matches.append("source")
    score = min(100, 20 * len(matches))
    filename = _filename(item, video)
    return {
        "provider": PROVIDER_ID,
        "id": _result_id(item),
        "language": item["language"],
        "release_info": item["release_info"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": bool(item["language"].get("hi")),
        "page_link": item["page_url"],
        "display": {
            "source": "addic7ed.com",
            "release": item["release_info"],
            "uploader": item.get("uploader"),
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "page_url": item["page_url"],
            "download_link": item["download_link"],
            "release_info": item["release_info"],
            "filename": filename,
            "season": item.get("season"),
            "episode": item.get("episode"),
            "title": item.get("title"),
            "year": item.get("year"),
        },
    }


def _result_id(item):
    value = item["download_link"]
    if item.get("season") is not None and item.get("episode") is not None:
        value += f"-s{int(item['season']):02d}e{int(item['episode']):02d}"
    return value


def _filename(item, video):
    title = item.get("series") or item.get("title") or video.get("title") or "addic7ed"
    parts = [_clean_filename(title)]
    if item.get("season") is not None and item.get("episode") is not None:
        parts.append(f"S{int(item['season']):02d}E{int(item['episode']):02d}")
    if item.get("release_info"):
        parts.append(_clean_filename(item["release_info"]))
    return ".".join(part for part in parts if part) + ".srt"


class _Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = {key: value for key, value in (attrs or [])}
        self.children = []
        self.data = []

    def append(self, child):
        self.children.append(child)

    def classes(self):
        return str(self.attrs.get("class") or "").split()

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

    def first_link(self):
        links = self.descendants("a")
        return links[0].attrs.get("href") if links else None


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


def _show_text_keys(text):
    keys = {_clean_key(text)}
    match = re.search(r"(.+)\s\((\d{4})\)$", text)
    if match:
        keys.add(_clean_key(match.group(1)))
        keys.add(_clean_key(f"{match.group(1)} {match.group(2)}"))
    return {key for key in keys if key}


def _show_keys(series, year):
    keys = {_clean_key(series)}
    if year:
        keys.add(_clean_key(f"{series} {year}"))
    keys.add(_clean_key(str(series).replace(".", "")))
    keys.add(_clean_key(str(series).replace("&", "and")))
    keys.add(_clean_key(str(series).replace("and", "&")))
    return {key for key in keys if key}


def _language_from_name(value):
    return LANGUAGE_NAMES.get(" ".join(str(value or "").strip().lower().split()))


def _normalize_release_info(value):
    return ",".join(part.strip() for part in str(value or "").replace("+", ",").split(",") if part.strip())


def _clean_key(value):
    text = str(value or "").lower()
    text = re.sub(r"['.:(),/!?-]+", " ", text)
    return " ".join(text.split())


def _clean_filename(value):
    text = re.sub(r"[^A-Za-z0-9]+", ".", str(value or "")).strip(".")
    return text or "addic7ed"


def _strip_leading_slash(value):
    return str(value or "").lstrip("/")


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
