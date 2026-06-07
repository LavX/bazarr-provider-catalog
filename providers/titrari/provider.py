"""Titrari.ro provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import html
import io
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar

PROVIDER_ID = "titrari"
BASE_URL = "https://www.titrari.ro"
HOME_URL = f"{BASE_URL}/"
HTTP_TIMEOUT_SECONDS = 15
# Transport-level retry for transient network blips only. Mirrors upstream subliminal's
# RetryingSession/ProviderRetryMixin (~3 tries + backoff). Retries cover connection
# resets/DNS failures, timeouts, HTTP 5xx, and HTTP 429; everything else propagates on the
# first occurrence so a 404 or auth failure is never masked by a retry.
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 8.0
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
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
    # Reject broken responses up front: get.php can answer with an empty stream or an
    # HTML/error page that would otherwise look like a successful download.
    if not body or not body.strip():
        raise ValueError("titrari download returned an empty body")
    if _looks_like_html(body):
        raise ValueError("titrari download returned HTML instead of a subtitle file")
    if _is_archive_body(body):
        # Host-side extraction (Provider Hub v1.1+): hand the raw archive bytes back to
        # the host. A pack can hold several members for the same episode (different
        # release group / resolution / source); the host's episode-only pick cannot tell
        # them apart, so when we can list a zip we pin the member matching the fields the
        # result was scored on. Otherwise (rar, single member, or no clear winner) let the
        # host pick the member by episode.
        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        member = _select_zip_member(body, payload)
        if member is not None:
            archive["member"] = member
        else:
            archive["episode"] = payload.get("episode")
        return archive
    # Direct, non-archive subtitle body.
    return _content_payload(body, _format_from_filename(filename))


def _is_archive_body(body):
    return _is_rar_archive(body) or zipfile.is_zipfile(io.BytesIO(body or b""))


def _select_zip_member(body, payload):
    # Pin the zip member matching the scored release group / resolution / source. Listing
    # only, no extraction or decoding: the host reads the named member and runs chardet.
    # Returns None for rar (not stdlib-listable), a single member (nothing to
    # disambiguate), or when no field breaks the tie, so the caller falls back to
    # host-side episode selection.
    payload = payload or {}
    if _is_rar_archive(body) or not zipfile.is_zipfile(io.BytesIO(body)):
        return None
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = [
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and _subtitle_extension(name)
            and not name.rsplit("/", 1)[-1].startswith(".")
        ]
    if len(members) < 2:
        return None  # a lone member: the host's episode pick already lands here
    # Narrow to the requested episode first; the host can already do this, but a pack with
    # several release groups per episode leaves it guessing among the matches.
    season = _safe_int(payload.get("season"))
    episode = _safe_int(payload.get("episode"))
    pool = members
    if episode is not None:
        episode_pool = [name for name in members if _member_has_episode(name, season, episode)]
        if not episode_pool:
            # Episode requested but absent from every member: pinning a member from another
            # episode would hard-fail the host download, so defer to host episode selection.
            return None
        pool = episode_pool
    if len(pool) < 2:
        return None  # a single episode match: host episode selection already lands here
    # Break the tie with the same fields the result was scored on. Pin only a unique winner.
    best, best_score, tied = None, 0, False
    for name in pool:
        score = _member_match_score(name, payload)
        if score > best_score:
            best, best_score, tied = name, score, False
        elif score == best_score and best is not None:
            tied = True
    if best is None or best_score == 0 or tied:
        return None  # cannot confidently disambiguate; let the host pick by episode
    return best


def _member_has_episode(name, season, episode):
    # Match SxxExx and NxNN as delimited tokens so "720p" never reads as S07E20 and
    # "264" never reads as episode 264. When the request carries no season, accept any
    # NxNN season; otherwise pin the season so a different season's episode is not matched.
    text = name.lower()
    if season is None:
        return bool(
            re.search(rf"s\d{{1,2}}e0*{episode}(?!\d)", text)
            or re.search(rf"(?<!\d)\d{{1,2}}x0*{episode}(?!\d)", text)
        )
    return bool(
        re.search(rf"s0*{season}e0*{episode}(?!\d)", text)
        or re.search(rf"(?<!\d){season}x0*{episode}(?!\d)", text)
    )


def _member_match_score(name, payload):
    # release_group is the strongest release signal (mirrors search _score: rg 12 vs
    # resolution/source 6 each), so weight it above resolution and source combined.
    score = 0
    if _release_group_matches(payload.get("release_group"), name):
        score += 5
    if _token_in_text(payload.get("resolution"), name):
        score += 2
    if _source_matches(payload.get("source"), name):
        score += 2
    return score


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
        # Only the raw transport call is retried. Header/cookie handling, the opener, and
        # the returned bytes are untouched; FlareSolverr/throttle logic lives elsewhere.
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code not in RETRY_STATUS_CODES or attempt >= RETRY_MAX_ATTEMPTS:
                    raise
                _sleep_retry(attempt, _retry_after_seconds(error))
            except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
                # HTTPError is a URLError subclass but is handled above, so reaching here
                # means a genuine transport failure (refused/reset/DNS/timeout). A 4xx
                # other than 429 already raised as HTTPError and never lands here.
                if attempt >= RETRY_MAX_ATTEMPTS:
                    raise
                del error
                _sleep_retry(attempt, None)

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
    # Do not guess an encoding. The host runs chardet via Subtitle.normalize(); a worker
    # guess (especially a legacy codepage that never fails to decode) only reintroduces
    # mojibake. Leave encoding unset and let the host normalize.
    content = content or b""
    fmt = fmt or "srt"
    return {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "content_type": _content_type(fmt),
        "format": fmt,
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


def _looks_like_html(body):
    sample = (body or b"").lstrip()[:1024].lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<body" in sample


def _is_rar_archive(body):
    return bool(body) and (body.startswith(b"Rar!\x1a\x07\x00") or body.startswith(b"Rar!\x1a\x07\x01\x00"))


def _sleep(config):
    try:
        delay_ms = int((config or {}).get("request_delay_ms", 0))
    except (TypeError, ValueError):
        delay_ms = 0
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)


def _sleep_retry(attempt, retry_after):
    # Exponential backoff, capped. A Retry-After hint (429) wins when it is larger so we do
    # not hammer the host sooner than it asked. Uses the module-level time.sleep so tests
    # can monkeypatch it to a no-op.
    delay = min(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_CAP_SECONDS)
    if retry_after is not None:
        delay = min(max(delay, retry_after), RETRY_BACKOFF_CAP_SECONDS)
    if delay > 0:
        time.sleep(delay)


def _retry_after_seconds(error):
    # Honor a Retry-After header (delta-seconds form only) on a 429 if present.
    try:
        value = error.headers.get("Retry-After")
    except AttributeError:
        return None
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


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
