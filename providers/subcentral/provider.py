"""SubCentral.de provider for the Bazarr+ Provider Hub catalog."""

import base64 as _base64
import hashlib as _hashlib
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

PROVIDER_ID = "subcentral"
BASE_URL = "https://www.subcentral.de"
HOME_URL = f"{BASE_URL}/"
HTTP_TIMEOUT_SECONDS = 15
MAX_BOARDS_PER_SEARCH = 3
MAX_THREADS_PER_BOARD = 4
SUPPORTED_LANGUAGES = {"deu": "de", "eng": "en"}
ALPHA2_TO_ALPHA3 = {value: key for key, value in SUPPORTED_LANGUAGES.items()}
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt")
ARCHIVE_EXTENSIONS = (".zip", ".rar")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)

_OPTION_RE = re.compile(rb"<option\b[^>]*value=['\"]?(?P<id>\d+)['\"]?[^>]*>(?P<title>.*?)</option>", re.I | re.S)
_THREAD_RE = re.compile(
    rb"<a\b[^>]*href=['\"](?P<href>index\.php\?page=Thread&amp;threadID=(?P<id>\d+)[^'\"]*)['\"][^>]*>(?P<title>.*?)</a>",
    re.I | re.S,
)
_SECURITY_TOKEN_RE = re.compile(rb"SECURITY_TOKEN\s*=\s*['\"](?P<token>[a-f0-9A-Za-z_-]+)['\"]", re.I)
_SID_RE = re.compile(rb"SID_ARG_2ND\s*=\s*['\"](?:&s=(?P<sid>[^'\"]*))?['\"]", re.I)
_THANK_POST_RE = re.compile(rb"thankPostButton(?P<post_id>\d+)|postID=(?P<href_post_id>\d+)", re.I)
_POST_TEXT_RE = re.compile(rb"<postText><!\[CDATA\[(?P<body>.*?)\]\]></postText>", re.I | re.S)
_GROUP_RE = re.compile(rb'<div\s+id=["\']a\d+["\'][^>]*>(?P<body>.*?)(?=<div\s+id=["\']a\d+["\']|<!-- \[Hier|<br>\s*<!--|\Z)', re.I | re.S)
_ROW_RE = re.compile(rb"<tr\b[^>]*class=['\"]aktiv['\"][^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_RELEASE_RE = re.compile(rb"<td\b[^>]*class=['\"]release['\"][^>]*>(?P<body>.*?)</td>", re.I | re.S)
_ATTACHMENT_RE = re.compile(
    rb"<a\b[^>]*href=['\"](?P<href>index\.php\?page=Attachment(?:&amp;|&)attachmentID=(?P<id>\d+)(?:&amp;|&)h=(?P<hash>[a-f0-9]+))['\"][^>]*>(?P<label>.*?)</a>",
    re.I | re.S,
)
_TAG_RE = re.compile(rb"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_WS_BYTES_RE = re.compile(rb"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_SEASON_RE = re.compile(r"(?:Staffel|Season|S)\s*0*(\d{1,2})\b", re.I)
_EPISODE_RE = re.compile(r"\bE0*(\d{1,3})\b", re.I)
_SUBTITLE_THREAD_RE = re.compile(r"\b(?:DE|VO|EN)-Subs\s*:", re.I)


def parse_series_options(body):
    rows = []
    for match in _OPTION_RE.finditer(body or b""):
        title = _strip_tags(match.group("title"))
        board_id = _decode(match.group("id"))
        if not title or not board_id:
            continue
        rows.append(
            {
                "title": title,
                "board_id": board_id,
                "url": f"{BASE_URL}/index.php?page=Board&boardID={board_id}",
            }
        )
    return rows


def parse_board_threads(body, series_title):
    rows = []
    seen = set()
    for match in _THREAD_RE.finditer(body or b""):
        thread_id = _decode(match.group("id"))
        if thread_id in seen:
            continue
        title = _strip_tags(match.group("title"))
        if not title or not _SUBTITLE_THREAD_RE.search(title):
            continue
        seen.add(thread_id)
        rows.append(
            {
                "thread_id": thread_id,
                "series_title": series_title,
                "title": title,
                "season": _season_from_text(title),
                "url": f"{BASE_URL}/index.php?page=Thread&threadID={thread_id}",
            }
        )
    return rows


def parse_thread_gate(body):
    token_match = _SECURITY_TOKEN_RE.search(body or b"")
    sid_match = _SID_RE.search(body or b"")
    post_id = None
    fallback_post_id = None
    for match in _THANK_POST_RE.finditer(body or b""):
        if match.group("post_id"):
            post_id = _decode(match.group("post_id"))
            break
        if fallback_post_id is None:
            fallback_post_id = _decode(match.group("href_post_id"))
    if post_id is None:
        post_id = fallback_post_id
    if not token_match or not sid_match or not post_id:
        raise ValueError("subcentral thank gate was not found")
    token = _decode(token_match.group("token"))
    sid = html.unescape(_decode(sid_match.group("sid")))
    sid_suffix = f"&s={sid}" if sid else ""
    return {
        "post_id": post_id,
        "token": token,
        "sid": sid,
        "thank_url": f"{BASE_URL}/index.php?action=Thank&output=xml&postID={post_id}&t={token}{sid_suffix}",
    }


def parse_revealed_attachments(body):
    post_texts = [match.group("body") for match in _POST_TEXT_RE.finditer(body or b"")]
    html_blocks = post_texts or [body or b""]
    rows = []
    for post_text in html_blocks:
        for group_match in _GROUP_RE.finditer(post_text):
            group = group_match.group("body")
            language = _language_from_group(group)
            if not language:
                continue
            rows.extend(_attachments_from_group(group, language))
    return rows


def derive_matches(video, candidate_title):
    if not video:
        return []
    matches = []
    candidate_tokens = set(_tokens(candidate_title))
    series_tokens = _tokens(video.get("series"))
    if series_tokens and all(token in candidate_tokens for token in series_tokens):
        matches.append("series")
    try:
        season = int(video.get("season"))
        episode = int(video.get("episode"))
    except (TypeError, ValueError):
        season = episode = None
    if season is not None and (f"s{season:02d}" in _normalize(candidate_title) or f"staffel {season}" in _normalize(candidate_title)):
        matches.append("season")
    normalized_title = _normalize(candidate_title)
    if episode is not None and (
        re.search(rf"\bs\d+e0*{episode}\b", normalized_title)
        or re.search(rf"\be0*{episode}\b", normalized_title)
    ):
        matches.append("episode")
    return matches


class SubCentralProvider:
    def __init__(self):
        self._cookie_jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        with self._opener.open(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        if (video or {}).get("kind") != "episode":
            return []
        config = dict(config or {})
        requested = {_alpha3_for_language(lang) for lang in languages or []}
        requested = {lang for lang in requested if lang in SUPPORTED_LANGUAGES}
        if not requested:
            return []
        try:
            target_episode = int(video.get("episode"))
        except (TypeError, ValueError):
            return []
        _sleep(config)
        boards = _rank_boards(video, parse_series_options(self._http_get(HOME_URL)))
        results = []
        seen = set()
        for board in boards[:MAX_BOARDS_PER_SEARCH]:
            _sleep(config)
            threads = _rank_threads(
                video,
                parse_board_threads(self._http_get(board["url"], referer=HOME_URL), board["title"]),
            )
            for thread in threads[:MAX_THREADS_PER_BOARD]:
                _sleep(config)
                thread_body = self._http_get(thread["url"], referer=board["url"])
                revealed = parse_revealed_attachments(thread_body)
                if not revealed:
                    try:
                        gate = parse_thread_gate(thread_body)
                    except ValueError:
                        continue
                    _sleep(config)
                    revealed = parse_revealed_attachments(
                        self._http_get(gate["thank_url"], referer=thread["url"])
                    )
                for attachment in revealed:
                    if attachment["language"] not in requested:
                        continue
                    try:
                        attachment_episode = int(attachment["episode"])
                    except (TypeError, ValueError):
                        continue
                    if attachment_episode != target_episode:
                        continue
                    merged = dict(attachment)
                    merged.update(
                        {
                            "series_title": board["title"],
                            "thread_title": thread["title"],
                            "thread_url": thread["url"],
                            "season": thread.get("season") or video.get("season"),
                        }
                    )
                    key = (merged["attachment_id"], merged["language"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(self._result(video, merged))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, item):
        alpha3 = item["language"]
        alpha2 = SUPPORTED_LANGUAGES[alpha3]
        candidate_title = (
            f"{item.get('series_title', '')} {item.get('thread_title', '')} "
            f"S{int(item.get('season') or 1):02d}E{int(item['episode']):02d} {item.get('release_group', '')}"
        )
        matches = derive_matches(video, candidate_title)
        score = 95 if "episode" in matches else 80
        filename = (
            f"subcentral.{_slug(item.get('series_title'))}."
            f"s{int(item.get('season') or 1):02d}e{int(item['episode']):02d}."
            f"{item.get('release_group', 'release')}.{alpha2}.rar"
        )
        return {
            "provider": PROVIDER_ID,
            "id": f"subcentral-{item['attachment_id']}-{alpha3}",
            "language": {
                "alpha3": alpha3,
                "alpha2": alpha2,
                "hi": False,
                "forced": False,
            },
            "release_info": item["release_info"],
            "filename": filename,
            "matches": matches,
            "score": score,
            "score_without_hash": score,
            "score_out_of": 100,
            "hash_verifiable": False,
            "hearing_impaired_verifiable": False,
            "hearing_impaired": False,
            "page_link": item["thread_url"],
            "display": {
                "source": "subcentral",
                "title": item.get("series_title"),
                "release": item["release_info"],
                "thread_url": item["thread_url"],
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "attachment_id": item["attachment_id"],
                "url": item["url"],
                "thread_url": item["thread_url"],
                "filename": filename,
                "season": item.get("season"),
                "episode": item["episode"],
                "language": alpha3,
            },
        }

    def download(self, provider_payload, language, config):
        del language, config
        payload = provider_payload or {}
        url = payload.get("url")
        if not url:
            raise ValueError("subcentral download requires url")
        body = self._http_get(url, referer=payload.get("thread_url"))
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
        raise ValueError("subcentral archive contains no supported subtitle files")
    try:
        episode = int((payload or {}).get("episode"))
    except (TypeError, ValueError):
        episode = None
    if episode is None:
        return candidates[0]

    def score(name):
        normalized = _normalize(os.path.basename(name))
        if re.search(rf"\bs\d+e0*{episode}\b", normalized):
            return 100
        if re.search(rf"\be0*{episode}\b", normalized):
            return 90
        if re.search(rf"(^|[^0-9])0*{episode}([^0-9]|$)", normalized):
            return 80
        return 0

    return max(candidates, key=score)


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
        raise RuntimeError(f"SubCentral RAR extraction failed: {details}") from errors[-1]
    raise RuntimeError("SubCentral RAR extraction requires bundled py7zz")


def _extract_rar_files_with_py7zz(body):
    if py7zz is None:
        raise RuntimeError("SubCentral bundled py7zz extractor is unavailable")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subcentral.rar")
        output_dir = os.path.join(temp_dir, "out")
        os.mkdir(output_dir)
        with open(archive_path, "wb") as handle:
            handle.write(body)
        py7zz.extract_archive(archive_path, output_dir)
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_unar(body):
    unar = shutil.which("unar")
    if not unar:
        raise RuntimeError("SubCentral RAR fallback requires unar")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subcentral.rar")
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
            raise RuntimeError(f"unar failed to extract SubCentral RAR: {message}")
        return _collect_extracted_subtitle_files(output_dir)


def _extract_rar_files_with_7z(body):
    sevenzip = shutil.which("7z") or shutil.which("7zz")
    if not sevenzip:
        raise RuntimeError("SubCentral RAR fallback requires 7z")
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = os.path.join(temp_dir, "subcentral.rar")
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
            raise RuntimeError(f"7z failed to extract SubCentral RAR: {message}")
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
        raise ValueError("subcentral archive contains no supported subtitle files")
    return files


def _attachments_from_group(group, language):
    rows = []
    for row_match in _ROW_RE.finditer(group):
        row = row_match.group("body")
        release_match = _RELEASE_RE.search(row)
        if not release_match:
            continue
        release = _strip_tags(release_match.group("body"))
        episode = _episode_from_text(release)
        if episode is None:
            continue
        for attachment_match in _ATTACHMENT_RE.finditer(row):
            label = _strip_tags(attachment_match.group("label"))
            url = _absolute_url(html.unescape(_decode(attachment_match.group("href"))))
            attachment_id = _decode(attachment_match.group("id"))
            rows.append(
                {
                    "attachment_id": attachment_id,
                    "url": url,
                    "language": language,
                    "episode": episode,
                    "episode_title": release,
                    "release_group": label,
                    "release_info": f"{release} {label}".strip(),
                }
            )
    return rows


def _language_from_group(group):
    lowered = group.lower()
    if b"flags/uk.png" in lowered or b"vo-subs" in lowered:
        return "eng"
    if b"flags/de.png" in lowered or b"de-subs" in lowered:
        return "deu"
    return None


def _rank_boards(video, boards):
    ranked = []
    wanted = set(_tokens((video or {}).get("series")))
    for board in boards:
        tokens = set(_tokens(board["title"]))
        if wanted and wanted.issubset(tokens):
            ranked.append((board, 100))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return [board for board, _score in ranked]


def _rank_threads(video, threads):
    try:
        season = int((video or {}).get("season"))
    except (TypeError, ValueError):
        season = None
    ranked = []
    for thread in threads:
        score = 80
        if season is not None and thread.get("season") == season:
            score = 100
        ranked.append((thread, score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return [thread for thread, _score in ranked]


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
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "latin-1"
    return {
        "content_b64": _base64.b64encode(content).decode("ascii"),
        "content_sha256": _hashlib.sha256(content).hexdigest(),
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
    return "application/x-subrip"


def _season_from_text(text):
    match = _SEASON_RE.search(text or "")
    return int(match.group(1)) if match else None


def _episode_from_text(text):
    match = _EPISODE_RE.search(text or "")
    return int(match.group(1)) if match else None


def _alpha3_for_language(language):
    if not isinstance(language, dict):
        return None
    alpha3 = (language.get("alpha3") or "").lower()
    if alpha3:
        return alpha3
    return ALPHA2_TO_ALPHA3.get((language.get("alpha2") or "").lower())


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _slug(value):
    return "-".join(_tokens(value)) or "release"


def _absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", value)


def _strip_tags(value):
    stripped = _TAG_RE.sub(b"", value or b"")
    stripped = _WS_BYTES_RE.sub(b" ", stripped).strip()
    return _WS_RE.sub(" ", html.unescape(_decode(stripped))).strip()


def _tokens(value):
    return [token for token in _normalize(value).split(" ") if token]


def _normalize(value):
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _decode(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
