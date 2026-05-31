"""TurkceAltyazi.org provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from http.cookies import SimpleCookie

try:
    import py7zz
except ImportError:
    py7zz = None

PROVIDER_ID = "turkcealtyaziorg"
BASE_URL = "https://turkcealtyazi.org"
DOWNLOAD_URL = f"{BASE_URL}/ind"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT_SECONDS = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
SUPPORTED_LANGUAGE_CODES = {"tur", "eng"}
_SXXEXX_RE = re.compile(r"\bs(?P<season>\d{1,2})\s*[._ -]?e(?P<episode>\d{1,3})\b", re.I)
_XX_RE = re.compile(r"\b(?P<season>\d{1,2})x(?P<episode>\d{1,3})\b", re.I)

CLASS_MAP = {
    "cps c1": "DVDRip",
    "cps c2": "HDRip",
    "cps c3": "TVRip",
    "rps r1": "HD",
    "rps r2": "DVDRip",
    "rps r3": "DVDScr",
    "rps r4": "R5",
    "rps r5": "CAM",
    "rps r6": "WEBRip",
    "rps r7": "BDRip",
    "rps r8": "WEB-DL",
    "rps r9": "HDRip",
    "rps r10": "HDTS",
    "rps r12": "BluRay",
    "rip1": "DVDRip",
    "rip2": "DVDScr",
    "rip3": "WEBRip",
    "rip4": "BDRip",
    "rip5": "BRRip",
    "rip6": "CAM",
    "rip7": "HD",
    "rip8": "R5",
    "rip9": "WEB-DL",
    "rip10": "HDRip",
    "rip11": "HDTS",
}
LANGUAGE_CLASSES = {
    "flagtr": "tur",
    "flagen": "eng",
    "flages": "spa",
    "flagfr": "fra",
    "flagger": "deu",
    "flagita": "ita",
}


class HttpResponse:
    def __init__(self, status, body, headers):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})


class TurkceAltyaziOrgProvider:
    def __init__(self):
        self._access_checked = False

    def search(self, video, languages, config):
        video = video or {}
        imdb_id = _video_imdb_id(video)
        if not imdb_id:
            return []
        requested = _requested_languages(languages)
        if not requested:
            return []
        config = dict(config or {})
        cookies = _parse_cookies(config)
        self._ensure_access(config, cookies)
        search_url = f"{BASE_URL}/find.php?{urllib.parse.urlencode({'cat': 'sub', 'find': imdb_id})}"
        response = self._http_get(search_url, self._headers(config), cookies, timeout=HTTP_TIMEOUT_SECONDS)
        _raise_for_status(response, "TurkceAltyazi search")
        if _is_not_found(response.body):
            return []
        entries = parse_search_page(response.body, video)
        results = []
        for entry in entries:
            if entry["language"] not in requested:
                continue
            if video.get("kind") == "episode" and not _episode_entry_matches(entry, video):
                continue
            results.append(_candidate(entry, video))
        return results

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        page_url = payload.get("page_url")
        if not page_url:
            raise ValueError("turkcealtyaziorg download requires page_url")
        config = dict(config or {})
        cookies = _parse_cookies(config)
        headers = self._headers(config)
        page_response = self._http_get(page_url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS)
        _raise_for_status(page_response, "TurkceAltyazi download page")
        form = parse_download_form(page_response.body)
        post_headers = dict(headers)
        post_headers["Referer"] = page_url
        archive_response = self._http_post(DOWNLOAD_URL, form, post_headers, cookies, timeout=10)
        _raise_for_status(archive_response, "TurkceAltyazi archive download")
        body, filename = extract_download(
            archive_response.body,
            payload.get("filename") or page_url,
            payload,
        )
        body = _normalize_line_endings(body)
        return _content_payload(body, _format_from_filename(filename))

    def _ensure_access(self, config, cookies):
        if self._access_checked:
            return
        response = self._http_get(BASE_URL, self._headers(config), cookies, timeout=10, allow_redirects=False)
        _raise_for_status(response, "TurkceAltyazi access check")
        self._access_checked = True

    def _headers(self, config):
        user_agent = str((config or {}).get("user_agent") or "").strip() or DEFAULT_USER_AGENT
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE_URL,
            "User-Agent": user_agent,
        }

    def _http_get(self, url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True):
        return _http_request("GET", url, headers, cookies, timeout=timeout, allow_redirects=allow_redirects)

    def _http_post(self, url, data, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS):
        return _http_request("POST", url, headers, cookies, data=data, timeout=timeout)


def _http_request(method, url, headers, cookies, data=None, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True):
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
        raise RuntimeError(f"TurkceAltyazi request failed: {exc.reason}") from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_cookies(config):
    value = str((config or {}).get("cookies") or "").strip()
    if not value:
        return {}
    cookie = SimpleCookie()
    cookie.load(value)
    return {key: morsel.value for key, morsel in cookie.items()}


def _requested_languages(languages):
    return {
        str(item.get("alpha3")).lower()
        for item in languages or []
        if isinstance(item, dict) and str(item.get("alpha3")).lower() in SUPPORTED_LANGUAGE_CODES
    }


def _video_imdb_id(video):
    value = video.get("series_imdb_id") if video.get("kind") == "episode" else video.get("imdb_id")
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text.startswith("tt"):
        text = text[2:]
    text = text.lstrip("0") or "0"
    return text if text.isdigit() else None


def _raise_for_status(response, context):
    if _is_cloudflare_challenge(response):
        raise PermissionError("TurkceAltyazi is presenting a Cloudflare challenge; configure matching cookies and User-Agent")
    if response.status >= 400:
        raise RuntimeError(f"{context} failed with HTTP {response.status}")


def _is_cloudflare_challenge(response):
    headers = {str(key).lower(): str(value).lower() for key, value in (response.headers or {}).items()}
    body = (response.body or b"").decode("utf-8", "ignore").lower()
    return response.status == 403 and (
        headers.get("cf-mitigated") == "challenge"
        or "just a moment" in body
        or "challenges.cloudflare.com" in body
    )


def _is_not_found(body):
    root = _parse_html(body)
    for meta in root.descendants("meta"):
        if meta.attrs.get("name") == "description" and "404 Error" in meta.attrs.get("content", ""):
            return True
    return False


def parse_search_page(body, video):
    root = _parse_html(body)
    entries = []
    kind = (video or {}).get("kind")
    for node in root.descendants("div"):
        classes = set(node.classes())
        if kind == "episode":
            season = int((video or {}).get("season") or 0)
            if "altsonsez1" not in classes or f"sezon_{season}" not in classes:
                continue
        elif "altsonsez2" not in classes:
            continue
        entries.append(_parse_entry(node, video))
    return [entry for entry in entries if entry is not None]


def _parse_entry(node, video):
    page_link = urllib.parse.urljoin(BASE_URL, node.first_link() or "")
    language = _entry_language(node)
    if not page_link or not language:
        return None
    season = None
    episode = None
    is_pack = False
    if (video or {}).get("kind") == "episode":
        alcd = node.first_descendant("div", {"alcd"})
        values = [item.text() for item in alcd.descendants("b")] if alcd else []
        if len(values) >= 2:
            season = _int_or_none(values[0])
            episode = _int_or_none(values[1])
            if episode is None:
                is_pack = True
                episode = _int_or_none((video or {}).get("episode"))
    ripdiv = node.first_descendant("div", {"ripdiv"})
    release_info = _release_info(ripdiv)
    uploader = _uploader(node)
    return {
        "page_url": page_link,
        "language": language,
        "release_info": release_info,
        "uploader": uploader,
        "hearing_impaired": bool(ripdiv and ripdiv.first_descendant("img", attrs={"src": "/images/isitme.png"})),
        "season": season,
        "episode": episode,
        "is_pack": is_pack,
    }


def _entry_language(node):
    aldil = node.first_descendant("div", {"aldil"})
    span = aldil.first_descendant("span") if aldil else None
    if span is None:
        return None
    for class_name in span.classes():
        if class_name in LANGUAGE_CLASSES:
            return LANGUAGE_CLASSES[class_name]
    return None


def _release_info(ripdiv):
    if ripdiv is None:
        return ""
    values = []
    for span in ripdiv.descendants("span"):
        mapped = CLASS_MAP.get(" ".join(span.classes()))
        if mapped:
            values.append(mapped)
    values.extend(item.strip() for item in ripdiv.own_text().split("/") if item.strip())
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return ",".join(seen)


def _uploader(node):
    container = node.first_descendant("div", {"alcevirmen"})
    if container is None:
        return None
    text = container.text().strip()
    if text:
        return text
    span = container.first_descendant("span")
    if span is not None:
        return CLASS_MAP.get(" ".join(span.classes()))
    return None


def _episode_entry_matches(entry, video):
    season = _int_or_none(video.get("season"))
    episode = _int_or_none(video.get("episode"))
    return entry.get("season") == season and (entry.get("is_pack") or entry.get("episode") == episode)


def _candidate(entry, video):
    matches = ["series", "season", "series_imdb_id"] if video.get("kind") == "episode" else ["imdb_id"]
    if video.get("kind") == "episode" and not entry.get("is_pack"):
        matches.append("episode")
    release_group = str(video.get("release_group") or "").lower()
    if release_group and release_group in entry["release_info"].lower():
        matches.append("release_group")
    score = min(100, 20 * len(matches))
    filename = entry["page_url"].rstrip("/").split("/")[-1] or "turkcealtyaziorg.zip"
    if not filename.lower().endswith((".zip", ".rar", ".srt")):
        filename = f"{filename}.zip"
    return {
        "provider": PROVIDER_ID,
        "id": _result_id(entry),
        "language": {"alpha3": entry["language"], "hi": bool(entry["hearing_impaired"]), "forced": False},
        "release_info": entry["release_info"],
        "filename": filename,
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": bool(entry["hearing_impaired"]),
        "page_link": entry["page_url"],
        "display": {
            "source": "turkcealtyazi.org",
            "release": entry["release_info"],
            "uploader": entry["uploader"],
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "page_url": entry["page_url"],
            "release_info": entry["release_info"],
            "filename": filename,
            "season": entry.get("season"),
            "episode": entry.get("episode"),
            "is_pack": bool(entry.get("is_pack")),
        },
    }


def _result_id(entry):
    value = entry["page_url"]
    if entry.get("season") is not None and entry.get("episode") is not None:
        value += f"-s{int(entry['season']):02d}e{int(entry['episode']):02d}"
    return value


def parse_download_form(body):
    root = _parse_html(body)
    values = {}
    for item in root.descendants("input"):
        name = item.attrs.get("name")
        if name in {"idid", "altid", "sidid"}:
            values[name] = item.attrs.get("value", "")
    missing = {"idid", "altid", "sidid"} - set(values)
    if missing:
        raise RuntimeError("TurkceAltyazi download page did not include required form fields")
    return {"idid": values["idid"], "altid": values["altid"], "sidid": values["sidid"]}


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
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

    def own_text(self):
        return " ".join(" ".join(self.data).split())

    def first_descendant(self, tag, classes=None, attrs=None):
        classes = set(classes or [])
        attrs = dict(attrs or {})
        for child in self.children:
            if child.tag == tag and classes.issubset(set(child.classes())) and _attrs_match(child, attrs):
                return child
            found = child.first_descendant(tag, classes, attrs)
            if found is not None:
                return found
        return None

    def descendants(self, tag):
        found = []
        for child in self.children:
            if child.tag == tag:
                found.append(child)
            found.extend(child.descendants(tag))
        return found

    def first_link(self):
        link = self.first_descendant("a")
        return link.attrs.get("href") if link is not None else None


def _attrs_match(node, attrs):
    for key, value in attrs.items():
        if node.attrs.get(key) != value:
            return False
    return True


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


def extract_download(body, filename, payload=None):
    payload = payload or {}
    stream = io.BytesIO(body or b"")
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = [name for name in archive.namelist() if _subtitle_extension(name)]
            if not names:
                raise ValueError("turkcealtyaziorg archive contains no supported subtitle files")
            name = _best_archive_member(names, payload)
            return archive.read(name), name
    if _looks_like_rar(body, filename):
        files = _extract_rar_files(body)
        if not files:
            raise ValueError("turkcealtyaziorg RAR archive contains no supported subtitle files")
        name = _best_archive_member([name for name, _content in files], payload)
        for file_name, content in files:
            if file_name == name:
                return content, file_name
    if not body:
        raise ValueError("turkcealtyaziorg downloaded empty subtitle")
    return body, filename


def _best_archive_member(names, payload):
    episode = _int_or_none(payload.get("episode"))
    if episode is not None:
        season = _int_or_none(payload.get("season"))
        for name in names:
            if _archive_member_matches_episode(name, season, episode):
                return name
        if payload.get("is_pack"):
            raise ValueError("turkcealtyaziorg archive does not contain the requested episode")
    names.sort(key=lambda name: (not name.lower().endswith(".srt"), len(name), name.lower()))
    return names[0]


def _archive_member_matches_episode(name, season, episode):
    lowered = str(name or "").lower()
    for pattern in (_SXXEXX_RE, _XX_RE):
        for match in pattern.finditer(lowered):
            if int(match.group("episode")) != episode:
                continue
            return season is None or int(match.group("season")) == season
    token = f"e{episode:02d}"
    return token in lowered


def _subtitle_extension(name):
    lower = str(name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower.endswith(extension):
            return extension.lstrip(".")
    return None


def _looks_like_rar(body, filename):
    lower = str(filename or "").lower()
    return lower.endswith(".rar") or (body or b"").startswith(b"Rar!\x1a\x07")


def _extract_rar_files(body):
    if py7zz is not None:
        try:
            return _extract_rar_files_with_py7zz(body)
        except Exception:
            pass
    if shutil.which("unar"):
        try:
            return _extract_rar_files_with_unar(body)
        except Exception:
            pass
    if shutil.which("7z") or shutil.which("7zz"):
        try:
            return _extract_rar_files_with_7z(body)
        except Exception:
            pass
    raise RuntimeError("TurkceAltyazi RAR extraction requires py7zz, unar, or 7z")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("TurkceAltyazi bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "turkcealtyazi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("TurkceAltyazi RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "turkcealtyazi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("unar failed to extract TurkceAltyazi RAR")
        return _collect_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("TurkceAltyazi RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "turkcealtyazi.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", f"-o{output_dir}", "-y", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("7z failed to extract TurkceAltyazi RAR")
        return _collect_subtitle_files(output_dir)


def _collect_subtitle_files(directory):
    files = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            if not _subtitle_extension(name):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, directory)
            with open(path, "rb") as handle:
                files.append((rel, handle.read()))
    return files


def _format_from_filename(filename):
    return _subtitle_extension(urllib.parse.urlparse(str(filename)).path) or "srt"


def _normalize_line_endings(body):
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_payload(body, fmt):
    if not body:
        raise ValueError("turkcealtyaziorg downloaded empty subtitle")
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
