"""CinemaZ provider for the Bazarr+ Provider Hub catalog."""

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

PROVIDER_ID = "cinemaz"
BASE_URL = "https://cinemaz.to/"
RULES_URL = urllib.parse.urljoin(BASE_URL, "rules")
DEFAULT_USER_AGENT = "BazarrProviderHub/1.0"
HTTP_TIMEOUT_SECONDS = 30
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
_SXXEXX_RE = re.compile(r"\bs(?P<season>\d{1,2})\s*[._ -]?e(?P<episode>\d{1,3})\b", re.I)

LANGUAGE_NAME_TO_ALPHA3 = {
    "abkhazian": "abk",
    "afar": "aar",
    "afrikaans": "afr",
    "akan": "aka",
    "albanian": "sqi",
    "amharic": "amh",
    "arabic": "ara",
    "aragonese": "arg",
    "armenian": "hye",
    "assamese": "asm",
    "avaric": "ava",
    "avestan": "ave",
    "aymara": "aym",
    "azerbaijani": "aze",
    "bambara": "bam",
    "bashkir": "bak",
    "basque": "eus",
    "belarusian": "bel",
    "bengali": "ben",
    "bihari languages": "bih",
    "bislama": "bis",
    "bokmal norwegian": "nor",
    "bokmal, norwegian": "nor",
    "bokmål, norwegian": "nor",
    "bosnian": "bos",
    "brazilian portuguese": "por-BR",
    "breton": "bre",
    "bulgarian": "bul",
    "burmese": "mya",
    "cantonese": "zho",
    "catalan": "cat",
    "central khmer": "khm",
    "chamorro": "cha",
    "chechen": "che",
    "chichewa": "nya",
    "chinese": "zho",
    "church slavic": "chu",
    "chuvash": "chv",
    "cornish": "cor",
    "corsican": "cos",
    "cree": "cre",
    "croatian": "hrv",
    "czech": "ces",
    "danish": "dan",
    "dhivehi": "div",
    "dutch": "nld",
    "dzongkha": "dzo",
    "english": "eng",
    "esperanto": "epo",
    "estonian": "est",
    "ewe": "ewe",
    "faroese": "fao",
    "fijian": "fij",
    "filipino": "fil",
    "finnish": "fin",
    "french": "fra",
    "fulah": "ful",
    "gaelic": "gla",
    "galician": "glg",
    "ganda": "lug",
    "georgian": "kat",
    "german": "deu",
    "greek": "ell",
    "guarani": "grn",
    "gujarati": "guj",
    "haitian": "hat",
    "hausa": "hau",
    "hebrew": "heb",
    "herero": "her",
    "hindi": "hin",
    "hiri motu": "hmo",
    "hungarian": "hun",
    "icelandic": "isl",
    "ido": "ido",
    "igbo": "ibo",
    "indonesian": "ind",
    "interlingua": "ina",
    "interlingue": "ile",
    "inuktitut": "iku",
    "inupiaq": "ipk",
    "irish": "gle",
    "italian": "ita",
    "japanese": "jpn",
    "javanese": "jav",
    "kalaallisut": "kal",
    "kannada": "kan",
    "kanuri": "kau",
    "kashmiri": "kas",
    "kazakh": "kaz",
    "kikuyu": "kik",
    "kinyarwanda": "kin",
    "kirghiz": "kir",
    "komi": "kom",
    "kongo": "kon",
    "korean": "kor",
    "kuanyama": "kua",
    "kurdish": "kur",
    "lao": "lao",
    "latin": "lat",
    "latvian": "lav",
    "limburgan": "lim",
    "lingala": "lin",
    "lithuanian": "lit",
    "luba-katanga": "lub",
    "luxembourgish": "ltz",
    "macedonian": "mkd",
    "malagasy": "mlg",
    "malay": "msa",
    "malayalam": "mal",
    "maltese": "mlt",
    "mandarin": "zho",
    "manx": "glv",
    "maori": "mri",
    "marathi": "mar",
    "marshallese": "mah",
    "mongolian": "mon",
    "moore": "mos",
    "nauru": "nau",
    "navajo": "nav",
    "ndebele north": "nde",
    "ndebele, north": "nde",
    "ndebele south": "nbl",
    "ndebele, south": "nbl",
    "ndonga": "ndo",
    "nepali": "nep",
    "northern sami": "sme",
    "norwegian": "nor",
    "norwegian nynorsk": "nno",
    "occitan": "oci",
    "occitan post 1500": "oci",
    "occitan (post 1500)": "oci",
    "ojibwa": "oji",
    "oriya": "ori",
    "oromo": "orm",
    "ossetian": "oss",
    "pali": "pli",
    "panjabi": "pan",
    "persian": "fas",
    "polish": "pol",
    "portuguese": "por",
    "pushto": "pus",
    "quechua": "que",
    "romanian": "ron",
    "romansh": "roh",
    "rundi": "run",
    "russian": "rus",
    "samoan": "smo",
    "sango": "sag",
    "sanskrit": "san",
    "sardinian": "srd",
    "serbian": "srp",
    "shona": "sna",
    "sichuan yi": "iii",
    "sindhi": "snd",
    "sinhala": "sin",
    "slovak": "slk",
    "slovenian": "slv",
    "somali": "som",
    "sotho southern": "sot",
    "sotho, southern": "sot",
    "spanish": "spa",
    "sundanese": "sun",
    "swahili": "swa",
    "swati": "ssw",
    "swedish": "swe",
    "tagalog": "tgl",
    "tahitian": "tah",
    "tajik": "tgk",
    "tamil": "tam",
    "tatar": "tat",
    "telugu": "tel",
    "thai": "tha",
    "tibetan": "bod",
    "tigrinya": "tir",
    "tongan": "ton",
    "tsonga": "tso",
    "tswana": "tsn",
    "turkish": "tur",
    "turkmen": "tuk",
    "twi": "twi",
    "uighur": "uig",
    "ukrainian": "ukr",
    "urdu": "urd",
    "uzbek": "uzb",
    "venda": "ven",
    "vietnamese": "vie",
    "volapuk": "vol",
    "volapük": "vol",
    "walloon": "wln",
    "welsh": "cym",
    "western frisian": "fry",
    "wolof": "wol",
    "xhosa": "xho",
    "yiddish": "yid",
    "yoruba": "yor",
    "zhuang": "zha",
    "zulu": "zul",
}


class HttpResponse:
    def __init__(self, status, body, headers):
        self.status = int(status)
        self.body = body or b""
        self.headers = dict(headers or {})


class CinemaZProvider:
    def __init__(self):
        self._cookies_verified = False

    def search(self, video, languages, config):
        config = dict(config or {})
        info_url = str((video or {}).get("info_url") or "")
        if not _is_cinemaz_url(info_url):
            return []
        cookies = _parse_cookies(config)
        requested = _requested_languages(languages)
        if not requested:
            return []
        self._ensure_cookies(cookies, config)
        response = self._http_get(info_url, self._headers(config), cookies, timeout=HTTP_TIMEOUT_SECONDS)
        if response.status == 404:
            return []
        _raise_for_status(response, "CinemaZ release page")
        release = parse_release_page(response.body, info_url)
        results = []
        for subtitle in release["subtitles"]:
            alpha3 = language_alpha3(subtitle["language"])
            if not alpha3 or (alpha3, False) not in requested:
                continue
            results.append(_candidate(release, subtitle, alpha3, video))
        return results

    def download(self, provider_payload, language, config):
        del language
        config = dict(config or {})
        cookies = _parse_cookies(config)
        payload = provider_payload or {}
        download_url = payload.get("download_url")
        if not download_url:
            raise ValueError("cinemaz download requires download_url")
        response = self._http_get(download_url, self._headers(config), cookies, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=False)
        _raise_for_status(response, "CinemaZ subtitle download")
        if _looks_like_html(response):
            raise PermissionError("CinemaZ subtitle download returned a login page")
        body, filename = extract_download(response.body, payload.get("filename") or download_url, payload)
        body = _normalize_line_endings(body)
        return _content_payload(body, _format_from_filename(filename))

    def _ensure_cookies(self, cookies, config):
        if self._cookies_verified:
            return
        headers = self._headers(config)
        headers["Referer"] = BASE_URL
        response = self._http_get(RULES_URL, headers, cookies, timeout=10, allow_redirects=False)
        if response.status in {302, 403, 404}:
            raise PermissionError("CinemaZ cookies are not valid anymore")
        _raise_for_status(response, "CinemaZ cookie validation")
        self._cookies_verified = True

    def _headers(self, config):
        user_agent = str((config or {}).get("user_agent") or "").strip() or DEFAULT_USER_AGENT
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": user_agent,
        }

    def _http_get(self, url, headers, cookies, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=True):
        request_headers = dict(headers or {})
        if cookies:
            request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        opener = urllib.request.build_opener()
        if not allow_redirects:
            opener = urllib.request.build_opener(_NoRedirectHandler)
        try:
            with opener.open(request, timeout=timeout) as response:
                return HttpResponse(response.status, response.read(), dict(response.headers.items()))
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, exc.read(), dict(exc.headers.items()))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CinemaZ request failed: {exc.reason}") from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_cookies(config):
    value = str((config or {}).get("cookies") or "").strip()
    if not value:
        raise ValueError("CinemaZ cookies are required")
    cookie = SimpleCookie()
    cookie.load(value)
    parsed = {key: morsel.value for key, morsel in cookie.items()}
    if not parsed:
        raise ValueError("CinemaZ cookies are required")
    return parsed


def _requested_languages(languages):
    requested = set()
    for item in languages or []:
        if not isinstance(item, dict):
            continue
        alpha3 = _language_id(item)
        if not alpha3:
            continue
        requested.add((alpha3, bool(item.get("hi"))))
    return requested


def _language_id(language):
    alpha3 = str(language.get("alpha3") or "").strip()
    country = str(language.get("country") or "").strip().upper()
    if alpha3 and country and "-" not in alpha3:
        return f"{alpha3}-{country}"
    return alpha3


def _is_cinemaz_url(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "cinemaz.to"


def _raise_for_status(response, context):
    if 300 <= response.status < 400:
        raise PermissionError(f"{context} redirected to login")
    if response.status >= 400:
        raise RuntimeError(f"{context} failed with HTTP {response.status}")


def language_alpha3(language_name):
    normalized = _normalize_language_name(language_name)
    if len(normalized) == 3 and normalized.isalpha():
        return normalized
    return LANGUAGE_NAME_TO_ALPHA3.get(normalized)


def _normalize_language_name(value):
    text = html.unescape(str(value or "")).strip().lower()
    text = " ".join(text.replace("\xa0", " ").split())
    text = text.replace("(", "").replace(")", "")
    return text


def _candidate(release, subtitle, alpha3, video):
    download_url = subtitle["download_url"]
    filename = subtitle.get("filename") or download_url.rstrip("/").split("/")[-1] or f"cinemaz-{alpha3}.srt"
    if not _subtitle_extension(filename) and subtitle.get("extension"):
        subtitle_id = _subtitle_id(download_url) or alpha3
        filename = f"cinemaz-{subtitle_id}.{alpha3}.{subtitle['extension']}"
    release_info = release["title"]
    return {
        "provider": PROVIDER_ID,
        "id": f"cinemaz-{filename}-{alpha3}",
        "language": {"alpha3": alpha3, "hi": False, "forced": False},
        "release_info": release_info,
        "filename": filename,
        "matches": ["hash"],
        "score": 100,
        "score_without_hash": 100,
        "score_out_of": 100,
        "hash_verifiable": True,
        "page_link": release["page_url"],
        "display": {
            "source": "cinemaz.to",
            "title": release_info,
            "uploader": subtitle.get("uploader"),
        },
        "provider_payload": {
            "provider": PROVIDER_ID,
            "schema": 1,
            "download_url": download_url,
            "filename": filename,
            "release_info": release_info,
            "page_url": release["page_url"],
            "kind": (video or {}).get("kind"),
            "season": (video or {}).get("season"),
            "episode": (video or {}).get("episode"),
        },
    }


def parse_release_page(body, page_url):
    root = _parse_html(body)
    table = _find_release_table(root)
    if table is None:
        return _parse_unit3d_release_page(root, page_url)
    rows = _release_rows(table)
    title_cell = rows.get("title")
    subtitles_cell = rows.get("subtitles")
    if title_cell is None:
        raise RuntimeError("CinemaZ release page did not include a title row")
    if subtitles_cell is None:
        return {"title": title_cell.text(), "page_url": page_url, "subtitles": []}
    subtitle_table = subtitles_cell.first_descendant("table")
    if subtitle_table is None:
        return {"title": title_cell.text(), "page_url": page_url, "subtitles": []}
    return {
        "title": title_cell.text(),
        "page_url": page_url,
        "subtitles": _subtitle_rows(subtitle_table, page_url),
    }


def _parse_unit3d_release_page(root, page_url):
    title = root.first_descendant("h1")
    if title is None:
        raise RuntimeError("Unexpected CinemaZ release page layout")
    for table in root.descendants("table"):
        subtitles = _subtitle_rows(table, page_url)
        if subtitles:
            return {"title": title.text(), "page_url": page_url, "subtitles": subtitles}
    return {"title": title.text(), "page_url": page_url, "subtitles": []}


def _find_release_table(root):
    for table in root.descendants("table"):
        rows = _release_rows(table)
        if "title" in rows and "subtitles" in rows:
            return table
    return None


def _release_rows(table):
    rows = {}
    tbody = table.first_child("tbody")
    if tbody is None:
        return rows
    for tr in tbody.children_named("tr"):
        cells = tr.children_named("td")
        if len(cells) < 2:
            continue
        label = cells[0].text().strip().lower()
        rows[label] = cells[1]
    return rows


def _subtitle_rows(table, page_url):
    headers = [cell.text().strip().lower() for cell in _header_cells(table)]
    if not headers:
        return []
    rows = []
    tbody = table.first_child("tbody")
    if tbody is None:
        return rows
    for tr in tbody.children_named("tr"):
        cells = tr.children_named("td")
        if len(cells) < len(headers):
            continue
        mapped = {headers[index]: cells[index] for index in range(len(headers))}
        language_cell = mapped.get("language")
        download_cell = mapped.get("download")
        if language_cell is None or download_cell is None:
            continue
        href = download_cell.first_link()
        if not href:
            continue
        uploader_cell = mapped.get("uploader")
        extension = _extension_from_cell(mapped.get("extension") or mapped.get("format") or mapped.get("type"))
        rows.append(
            {
                "language": language_cell.text(),
                "download_url": urllib.parse.urljoin(page_url, href),
                "uploader": uploader_cell.text() if uploader_cell is not None else None,
                "extension": extension,
            }
        )
    return rows


def _extension_from_cell(cell):
    if cell is None:
        return ""
    value = cell.text().strip().lower().lstrip(".")
    return value if f".{value}" in SUBTITLE_EXTENSIONS else ""


def _subtitle_id(download_url):
    parts = [part for part in urllib.parse.urlparse(download_url).path.split("/") if part]
    if "subtitles" in parts:
        index = parts.index("subtitles")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _header_cells(table):
    thead = table.first_child("thead")
    if thead is not None:
        headers = []
        for tr in thead.children_named("tr"):
            headers.extend(tr.children_named("th"))
        return headers
    first_row = table.first_descendant("tr")
    return first_row.children_named("th") if first_row else []


class _Node:
    def __init__(self, tag="", attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.data = []

    def append(self, child):
        self.children.append(child)

    def text(self):
        parts = list(self.data)
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def first_child(self, tag):
        for child in self.children:
            if child.tag == tag:
                return child
        return None

    def children_named(self, tag):
        return [child for child in self.children if child.tag == tag]

    def first_descendant(self, tag):
        for child in self.children:
            if child.tag == tag:
                return child
            found = child.first_descendant(tag)
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
        if link is None:
            return None
        return link.attrs.get("href")


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
        text = data.strip()
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
                raise ValueError("cinemaz archive contains no supported subtitle files")
            name = _best_archive_member(names, payload)
            return archive.read(name), name
    if _looks_like_rar(body, filename):
        files = _extract_rar_files(body)
        if not files:
            raise ValueError("cinemaz RAR archive contains no supported subtitle files")
        name = _best_archive_member([name for name, _content in files], payload)
        for file_name, content in files:
            if file_name == name:
                return content, file_name
    if not body:
        raise ValueError("cinemaz downloaded empty subtitle")
    return body, filename


def _best_archive_member(names, payload):
    episode = _int_or_none(payload.get("episode"))
    if episode is not None:
        season = _int_or_none(payload.get("season"))
        saw_episode_marker = False
        for name in names:
            match = _SXXEXX_RE.search(name)
            if not match:
                continue
            saw_episode_marker = True
            if int(match.group("episode")) != episode:
                continue
            if season is not None and int(match.group("season")) != season:
                continue
            return name
        if saw_episode_marker:
            raise ValueError("cinemaz archive does not contain the requested episode")
    names.sort(key=lambda name: (not name.lower().endswith(".srt"), len(name), name.lower()))
    return names[0]


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_html(response):
    content_type = str((response.headers or {}).get("content-type") or (response.headers or {}).get("Content-Type") or "").lower()
    sample = (response.body or b"").lstrip()[:2048].decode("utf-8", errors="ignore").lower()
    return "text/html" in content_type or sample.startswith("<!doctype html") or sample.startswith("<html")


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
    raise RuntimeError("CinemaZ RAR extraction requires py7zz, unar, or 7z")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("CinemaZ bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "cinemaz.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("CinemaZ RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "cinemaz.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([unar, "-quiet", "-o", output_dir, archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("unar failed to extract CinemaZ RAR")
        return _collect_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("CinemaZ RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "cinemaz.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        result = subprocess.run([sevenzip, "x", f"-o{output_dir}", "-y", archive_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise RuntimeError("7z failed to extract CinemaZ RAR")
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
        raise ValueError("cinemaz downloaded empty subtitle")
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
