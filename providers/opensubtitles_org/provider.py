"""OpenSubtitles.org provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
import xmlrpc.client
import zlib
from dataclasses import dataclass

PROVIDER_ID = "opensubtitles_org"
LEGACY_PROVIDER_ID = "opensubtitles"
DEFAULT_SCRAPER_URL = "http://localhost:8000"
USER_AGENT = "Bazarr-OpenSubtitlesOrg-ProviderHub/1.0"
DEFAULT_TIMEOUT_SECONDS = 15
SCRAPER_TIMEOUT_SECONDS = 120
SUBTITLE_FORMAT = "srt"

DEFAULT_API_URL = "api.opensubtitles.org/xml-rpc"
VIP_API_URL = "vip-api.opensubtitles.org/xml-rpc"

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_EPISODE_TAG_RE = re.compile(r"\bS(?P<season>\d{1,2})E(?P<episode>\d{1,3})\b", re.I)

_ALPHA3_TO_ALPHA2 = {
    "ara": "ar",
    "bos": "bs",
    "bul": "bg",
    "cat": "ca",
    "ces": "cs",
    "dan": "da",
    "deu": "de",
    "ell": "el",
    "eng": "en",
    "est": "et",
    "eus": "eu",
    "fas": "fa",
    "fin": "fi",
    "fra": "fr",
    "glg": "gl",
    "heb": "he",
    "hrv": "hr",
    "hun": "hu",
    "ind": "id",
    "isl": "is",
    "ita": "it",
    "jpn": "ja",
    "kat": "ka",
    "kor": "ko",
    "lav": "lv",
    "lit": "lt",
    "mkd": "mk",
    "msa": "ms",
    "nld": "nl",
    "nor": "no",
    "pol": "pl",
    "por": "pt",
    "ron": "ro",
    "rus": "ru",
    "slk": "sk",
    "slv": "sl",
    "spa": "es",
    "sqi": "sq",
    "srp": "sr",
    "swe": "sv",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
    "zho": "zh",
}
_ALPHA2_TO_ALPHA3 = {value: key for key, value in _ALPHA3_TO_ALPHA2.items()}

_ALPHA3_TO_OPENSUBTITLES = {
    "ces": "cze",
    "deu": "ger",
    "ell": "gre",
    "eus": "baq",
    "fas": "per",
    "fra": "fre",
    "hye": "arm",
    "isl": "ice",
    "kat": "geo",
    "mkd": "mac",
    "msa": "may",
    "mya": "bur",
    "nld": "dut",
    "ron": "rum",
    "slk": "slo",
    "sqi": "alb",
    "zho": "chi",
}
_OPENSUBTITLES_TO_ALPHA3 = {
    value: key for key, value in _ALPHA3_TO_OPENSUBTITLES.items()
}
_OPENSUBTITLES_TO_ALPHA3.update(
    {
        "alb": "sqi",
        "arm": "hye",
        "baq": "eus",
        "bur": "mya",
        "chi": "zho",
        "cze": "ces",
        "dut": "nld",
        "fre": "fra",
        "geo": "kat",
        "ger": "deu",
        "gre": "ell",
        "ice": "isl",
        "mac": "mkd",
        "may": "msa",
        "per": "fas",
        "pob": "por",
        "rum": "ron",
        "slo": "slk",
        "spl": "spa",
        "zht": "zho",
    }
)

_SCRAPER_OPEN_TO_ALPHA2 = {
    "alb": "sq",
    "ara": "ar",
    "baq": "eu",
    "bos": "bs",
    "bul": "bg",
    "cat": "ca",
    "chi": "zh",
    "cze": "cs",
    "dan": "da",
    "dut": "nl",
    "eng": "en",
    "est": "et",
    "fin": "fi",
    "fre": "fr",
    "geo": "ka",
    "ger": "de",
    "glg": "gl",
    "gre": "el",
    "heb": "he",
    "hrv": "hr",
    "hun": "hu",
    "ice": "is",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kor": "ko",
    "lav": "lv",
    "lit": "lt",
    "mac": "mk",
    "may": "ms",
    "nor": "no",
    "per": "fa",
    "pol": "pl",
    "por": "pt",
    "pob": "pt",
    "rum": "ro",
    "rus": "ru",
    "slo": "sk",
    "slv": "sl",
    "spa": "es",
    "srp": "sr",
    "swe": "sv",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
}


class OpenSubtitlesError(RuntimeError):
    """Base class for OpenSubtitles.org failures."""


class Unauthorized(OpenSubtitlesError):
    """The XML-RPC API rejected credentials or the user agent."""


class DownloadLimitReached(OpenSubtitlesError):
    """The XML-RPC API download limit was reached."""


class RateLimited(OpenSubtitlesError):
    """The API or helper service asked the caller to slow down."""


class ServiceUnavailable(OpenSubtitlesError):
    """The upstream API or helper service is not currently usable."""


@dataclass(frozen=True)
class LanguageInfo:
    alpha3: str
    alpha2: str | None = None
    country_alpha2: str | None = None
    forced: bool = False
    hi: bool = False

    @property
    def key(self):
        return (self.alpha3, self.country_alpha2, self.forced, self.hi)

    def payload(self):
        payload = {
            "alpha3": self.alpha3,
            "forced": self.forced,
            "hi": self.hi,
        }
        if self.alpha2:
            payload["alpha2"] = self.alpha2
        if self.country_alpha2:
            payload["country_alpha2"] = self.country_alpha2
        return payload


@dataclass(frozen=True)
class SearchContext:
    kind: str
    query: list[str]
    imdb_id: str | None
    hash_value: str | None
    size: int | str | None
    season: int | None
    episode: int | None
    tag: str | None


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(item) for item in value if item not in (None, ""))
    return _WS_RE.sub(" ", str(value)).strip()


def _normalize(value):
    return _NON_ALNUM_RE.sub("", _clean_text(value).lower())


def _imdb_without_prefix(value):
    value = _clean_text(value)
    if value.lower().startswith("tt"):
        return value[2:]
    return value


def _normalize_imdb(value):
    stripped = _imdb_without_prefix(value)
    stripped = stripped.lstrip("0")
    return stripped or None


def _with_tt(value):
    value = _clean_text(value)
    if not value:
        return None
    return value if value.lower().startswith("tt") else f"tt{value}"


def _language_from_payload(payload):
    alpha3 = _clean_text((payload or {}).get("alpha3"))
    alpha2 = _clean_text((payload or {}).get("alpha2")) or None
    country = _clean_text((payload or {}).get("country_alpha2")) or None
    if not alpha3 and alpha2:
        alpha3 = _ALPHA2_TO_ALPHA3.get(alpha2.lower(), alpha2.lower())
    if not alpha3:
        return None
    alpha3 = alpha3.lower()
    return LanguageInfo(
        alpha3=alpha3,
        alpha2=alpha2.lower() if alpha2 else _ALPHA3_TO_ALPHA2.get(alpha3),
        country_alpha2=country.upper() if country else None,
        forced=_as_bool((payload or {}).get("forced")),
        hi=_as_bool((payload or {}).get("hi") or (payload or {}).get("hearing_impaired")),
    )


def _language_from_alpha2(alpha2, forced=False, hi=False):
    code = _clean_text(alpha2).lower()
    alpha3 = _ALPHA2_TO_ALPHA3.get(code)
    if not alpha3:
        return None
    return LanguageInfo(alpha3=alpha3, alpha2=code, forced=forced, hi=hi)


def _language_from_opensubtitles(code, forced=False, hi=False):
    value = _clean_text(code).lower()
    alpha3 = _OPENSUBTITLES_TO_ALPHA3.get(value, value)
    return LanguageInfo(
        alpha3=alpha3,
        alpha2=_ALPHA3_TO_ALPHA2.get(alpha3),
        country_alpha2="BR" if value == "pob" else None,
        forced=forced,
        hi=hi,
    )


def _opensubtitles_code(language):
    alpha3 = language.alpha3
    if alpha3 == "por" and language.country_alpha2 == "BR":
        return "pob"
    if alpha3 == "spa" and language.country_alpha2 == "MX":
        return "spl"
    return _ALPHA3_TO_OPENSUBTITLES.get(alpha3, alpha3)


def _requested_languages(languages):
    parsed = []
    for payload in languages or []:
        language = _language_from_payload(payload)
        if language:
            parsed.append(language)
    return parsed


def _requested_keys(languages):
    return {language.key for language in _requested_languages(languages)}


def _language_requested(language, languages):
    return language.key in _requested_keys(languages)


def _subtitle_language_codes(languages):
    codes = []
    seen = set()
    for language in _requested_languages(languages):
        code = _opensubtitles_code(language)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return sorted(codes)


def _scraper_language_codes(languages):
    codes = []
    seen = set()
    for language in _requested_languages(languages):
        open_code = _opensubtitles_code(language)
        code = language.alpha2 or _SCRAPER_OPEN_TO_ALPHA2.get(open_code) or language.alpha3
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _content_payload(content, format_=SUBTITLE_FORMAT, encoding=None):
    if isinstance(content, str):
        content = content.encode(encoding or "utf-8")
    digest = hashlib.sha256(content).hexdigest()
    payload = {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "content_sha256": digest,
        "empty": False,
        "format": format_,
    }
    if encoding:
        payload["encoding"] = encoding
    return payload


def build_search_context(video, config):
    video = video or {}
    kind = video.get("kind")
    query = []
    season = None
    episode = None
    if kind == "episode":
        series = _clean_text(video.get("series"))
        if series:
            query.append(series)
        season = _as_int(video.get("season"))
        episode_value = video.get("episode")
        if isinstance(episode_value, list):
            episode = min((_as_int(item) for item in episode_value if _as_int(item) is not None), default=None)
        else:
            episode = _as_int(episode_value)
        if season == 0:
            title = _clean_text(video.get("title") or video.get("episode_title"))
            if series and title:
                query = [f"{series} {title}"]
            season = None
            episode = None
        imdb_id = _clean_text(video.get("series_imdb_id")) or _clean_text(video.get("imdb_id")) or None
    elif kind == "movie":
        title = _clean_text(video.get("title"))
        if title:
            query.append(title)
        imdb_id = _clean_text(video.get("imdb_id")) or None
    else:
        imdb_id = None

    hashes = video.get("hashes") or {}
    return SearchContextWithTag(
        kind=kind,
        query=query,
        imdb_id=imdb_id,
        hash_value=hashes.get(LEGACY_PROVIDER_ID) or hashes.get(PROVIDER_ID),
        size=video.get("size"),
        season=season,
        episode=episode,
        tag=_clean_text(video.get("original_name")) or None,
        use_tag_search=_as_bool((config or {}).get("use_tag_search")),
    )


def build_xmlrpc_criteria(context, languages):
    criteria = []
    if context.hash_value and context.size:
        criteria.append(
            {
                "moviehash": str(context.hash_value),
                "moviebytesize": str(context.size),
            }
        )
    if getattr(context, "use_tag_search", False) and context.tag:
        criteria.append({"tag": context.tag})
    if context.imdb_id:
        imdb_criterion = {"imdbid": _imdb_without_prefix(context.imdb_id)}
        if context.season is not None and context.episode is not None:
            imdb_criterion["season"] = context.season
            imdb_criterion["episode"] = context.episode
        criteria.append(imdb_criterion)
    if not criteria:
        raise ValueError("opensubtitles.org needs a hash, tag, or imdb id search key")

    language_id = ",".join(_subtitle_language_codes(languages))
    if not language_id:
        raise ValueError("opensubtitles.org search requires at least one language")
    for criterion in criteria:
        criterion["sublanguageid"] = language_id
    return criteria


def _context_with_config(context, config):
    return SearchContextWithTag(
        kind=context.kind,
        query=context.query,
        imdb_id=context.imdb_id,
        hash_value=context.hash_value,
        size=context.size,
        season=context.season,
        episode=context.episode,
        tag=context.tag,
        use_tag_search=_as_bool((config or {}).get("use_tag_search")),
    )


@dataclass(frozen=True)
class SearchContextWithTag(SearchContext):
    use_tag_search: bool = False


def _score_result(result, imdb_id, query, year):
    if imdb_id and result.get("imdb_id") == imdb_id:
        return 10_000
    score = 0
    query_norm = _normalize(query)
    title_norm = _normalize(result.get("title"))
    if query_norm and title_norm:
        if query_norm == title_norm:
            score += 100
        elif query_norm in title_norm or title_norm in query_norm:
            score += 50
    if year and result.get("year") == year:
        score += 30
    try:
        score += min(int(result.get("subtitle_count") or 0), 200) / 100
    except (TypeError, ValueError):
        pass
    return score


def select_best_result(results, imdb_id, query, year):
    if not results:
        return None
    return max(results, key=lambda item: _score_result(item, imdb_id, query, year))


def _matches_for_video(video, movie_kind, movie_name, release_name, movie_year, movie_imdb_id, season, episode, hash_value):
    matches = set()
    kind = (video or {}).get("kind")
    if kind == "episode" and movie_kind == "episode":
        video_series = _clean_text(video.get("series"))
        if video_series and _normalize(video_series) in _normalize(movie_name):
            matches.add("series")
        if video.get("year") and movie_year and int(video.get("year")) == int(movie_year):
            matches.add("year")
        if season is not None and _as_int(video.get("season")) == season:
            matches.add("season")
        if episode is not None and _as_int(video.get("episode")) == episode:
            matches.add("episode")
        video_title = _clean_text(video.get("title") or video.get("episode_title"))
        if video_title and _normalize(video_title) in _normalize(movie_name):
            matches.add("title")
        tag_match = _EPISODE_TAG_RE.search(release_name or "")
        if tag_match:
            if _as_int(tag_match.group("season")) == _as_int(video.get("season")):
                matches.add("season")
            if _as_int(tag_match.group("episode")) == _as_int(video.get("episode")):
                matches.add("episode")
    elif kind == "movie" and movie_kind == "movie":
        if video.get("title") and _normalize(video.get("title")) == _normalize(movie_name):
            matches.add("title")
        if video.get("year") and movie_year and int(video.get("year")) == int(movie_year):
            matches.add("year")

    hashes = (video or {}).get("hashes") or {}
    if hash_value and hash_value in {hashes.get(LEGACY_PROVIDER_ID), hashes.get(PROVIDER_ID)}:
        matches.add("hash")

    target_imdb = (video or {}).get("series_imdb_id") if kind == "episode" else (video or {}).get("imdb_id")
    if _normalize_imdb(movie_imdb_id) and _normalize_imdb(target_imdb) == _normalize_imdb(movie_imdb_id):
        matches.add("imdb_id")
        if kind == "movie":
            matches.add("year")
    return sorted(matches)


def _wrong_fps(video, subtitle_fps):
    video_fps = _as_float((video or {}).get("fps"))
    sub_fps = _as_float(subtitle_fps)
    if not video_fps or not sub_fps:
        return False
    return abs(video_fps - sub_fps) > 0.02


def _candidate(
    *,
    subtitle_id,
    mode,
    language,
    hearing_impaired,
    page_link,
    movie_kind,
    movie_name,
    release_name,
    movie_year,
    movie_imdb_id,
    season,
    episode,
    filename,
    encoding,
    fps,
    matched_by,
    hash_value,
    uploader,
    download_count,
    download_url=None,
    video=None,
):
    subtitle_id = str(subtitle_id)
    matches = _matches_for_video(
        video or {},
        movie_kind,
        movie_name,
        release_name,
        movie_year,
        movie_imdb_id,
        season,
        episode,
        hash_value,
    )
    if matched_by == "tag":
        matches = sorted(set(matches) | {"hash"})

    provider_payload = {
        "provider": PROVIDER_ID,
        "legacy_provider_id": LEGACY_PROVIDER_ID,
        "schema": 1,
        "mode": mode,
        "subtitle_id": subtitle_id,
    }
    if download_url:
        provider_payload["download_url"] = download_url
    if page_link:
        provider_payload["page_link"] = page_link
    if filename:
        provider_payload["filename"] = filename
    if release_name:
        provider_payload["release_info"] = release_name
    if encoding:
        provider_payload["encoding"] = encoding

    return {
        "id": f"{PROVIDER_ID}-{mode}-{subtitle_id}",
        "provider": PROVIDER_ID,
        "language": language.payload(),
        "hearing_impaired": hearing_impaired,
        "hash_verifiable": True,
        "hearing_impaired_verifiable": True,
        "page_link": page_link,
        "release_info": release_name,
        "filename": filename,
        "uploader": uploader or "anonymous",
        "matches": matches,
        "provider_payload": provider_payload,
        "display": {
            "download_count": int(download_count or 0),
            "fps": fps,
            "matched_by": matched_by,
            "legacy_provider_id": LEGACY_PROVIDER_ID,
        },
    }


def check_xmlrpc_response(response):
    try:
        status = int(str(response.get("status", ""))[:3])
    except (AttributeError, TypeError, ValueError):
        raise ServiceUnavailable(f"empty OpenSubtitles.org response: {response!r}")
    if status == 200:
        return response
    if status in {401, 406, 414, 415}:
        raise Unauthorized(response.get("status"))
    if status == 407:
        raise DownloadLimitReached(response.get("status"))
    if status == 429:
        raise RateLimited(response.get("status"))
    if status in {402, 413}:
        raise OpenSubtitlesError(response.get("status"))
    if status in {503, 506}:
        raise ServiceUnavailable(response.get("status"))
    raise OpenSubtitlesError(response.get("status"))


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout, use_https):
        super().__init__()
        self.timeout = timeout
        self.use_https = use_https

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout):
        super().__init__(context=ssl.create_default_context())
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class OpenSubtitlesOrgProvider:
    def __init__(self):
        self._api_server = None
        self._api_token = None
        self._api_endpoint = None

    def search(self, video, languages, config):
        config = config or {}
        context = build_search_context(video, config)
        if _as_bool(config.get("use_web_scraper"), default=True):
            return self._search_scraper(video or {}, languages or [], config, context)
        return self._search_api(video or {}, languages or [], config, context)

    def download(self, provider_payload, language, config):
        del language
        payload = provider_payload or {}
        mode = payload.get("mode")
        if mode == "api" or not _as_bool((config or {}).get("use_web_scraper"), default=True):
            return self._download_api(payload, config or {})
        return self._download_scraper(payload, config or {})

    def parse_api_subtitles(self, api_items, video, languages, context, config):
        config = config or {}
        only_foreign = _as_bool(config.get("only_foreign"))
        also_foreign = _as_bool(config.get("also_foreign"))
        skip_wrong_fps = _as_bool(config.get("skip_wrong_fps"), default=True)
        candidates = []
        items = api_items.get("data", api_items) if isinstance(api_items, dict) else api_items
        for item in items or []:
            if not isinstance(item, dict):
                continue
            forced = _as_bool(item.get("SubForeignPartsOnly"))
            if only_foreign and not forced:
                continue
            if not only_foreign and not also_foreign and forced:
                continue
            language = _language_from_opensubtitles(
                item.get("SubLanguageID"),
                forced=(only_foreign or also_foreign) and forced,
                hi=_as_bool(item.get("SubHearingImpaired")),
            )
            if not _language_requested(language, languages):
                continue

            movie_kind = item.get("MovieKind") or context.kind
            movie_year = _as_int(item.get("MovieYear"))
            season = _as_int(item.get("SeriesSeason"))
            episode = _as_int(item.get("SeriesEpisode"))
            if context.kind == "episode":
                movie_imdb_id = _with_tt(item.get("SeriesIMDBParent") or context.imdb_id)
            else:
                movie_imdb_id = _with_tt(item.get("IDMovieImdb") or context.imdb_id)
            target_imdb = context.imdb_id
            if target_imdb and movie_imdb_id and _normalize_imdb(target_imdb) != _normalize_imdb(movie_imdb_id):
                continue

            fps = item.get("MovieFPS")
            if skip_wrong_fps and _wrong_fps(video, fps):
                matches = []
            else:
                matches = None

            candidate = _candidate(
                subtitle_id=item.get("IDSubtitleFile"),
                mode="api",
                language=language,
                hearing_impaired=language.hi,
                page_link=item.get("SubtitlesLink"),
                movie_kind=movie_kind,
                movie_name=item.get("MovieName") or "",
                release_name=item.get("MovieReleaseName") or "",
                movie_year=movie_year,
                movie_imdb_id=movie_imdb_id,
                season=season,
                episode=episode,
                filename=item.get("SubFileName") or "",
                encoding=item.get("SubEncoding") or None,
                fps=fps,
                matched_by=item.get("MatchedBy") or "query",
                hash_value=item.get("MovieHash") or context.hash_value,
                uploader=item.get("UserNickName") or "anonymous",
                download_count=_as_int(item.get("SubDownloadsCnt")) or 0,
                video=video,
            )
            if matches is not None:
                candidate["matches"] = matches
                candidate["display"]["wrong_fps"] = True
            candidates.append(candidate)
        return candidates

    def _search_api(self, video, languages, config, context):
        context = _context_with_config(context, config)
        criteria = build_xmlrpc_criteria(context, languages)
        server = self._ensure_api_session(config)
        response = check_xmlrpc_response(server.SearchSubtitles(self._api_token, criteria))
        return self.parse_api_subtitles(
            response.get("data") or [],
            video,
            languages,
            context,
            config,
        )

    def _search_scraper(self, video, languages, config, context):
        base_url = self._scraper_base_url(config)
        timeout = _as_int(config.get("timeout")) or DEFAULT_TIMEOUT_SECONDS
        self._http_get_json(f"{base_url}/health", timeout=timeout)

        search_query = context.query[0] if context.query else _clean_text(video.get("title") or video.get("series"))
        search_endpoint = "/api/v1/search/tv" if context.kind == "episode" else "/api/v1/search/movies"
        search_payload = {
            "query": search_query,
            "imdb_id": context.imdb_id,
            "year": video.get("year"),
            "kind": context.kind,
        }
        search_response = self._http_post_json(
            f"{base_url}{search_endpoint}",
            search_payload,
            timeout=SCRAPER_TIMEOUT_SECONDS,
        )
        best_result = select_best_result(
            search_response.get("results") or [],
            context.imdb_id,
            search_query,
            video.get("year"),
        )
        if not best_result or not best_result.get("url"):
            return []

        subtitles_payload = {
            "movie_url": best_result["url"],
            "languages": _scraper_language_codes(languages),
        }
        if context.kind == "episode" and context.season is not None:
            subtitles_payload["season"] = context.season
        if context.kind == "episode" and context.episode is not None:
            subtitles_payload["episode"] = context.episode
        subtitles_response = self._http_post_json(
            f"{base_url}/api/v1/subtitles",
            subtitles_payload,
            timeout=SCRAPER_TIMEOUT_SECONDS,
        )
        return self._parse_scraper_subtitles(
            subtitles_response.get("subtitles") or [],
            best_result,
            video,
            languages,
            context,
            config,
        )

    def _parse_scraper_subtitles(self, subtitles, search_result, video, languages, context, config):
        only_foreign = _as_bool((config or {}).get("only_foreign"))
        also_foreign = _as_bool((config or {}).get("also_foreign"))
        skip_wrong_fps = _as_bool((config or {}).get("skip_wrong_fps"), default=True)
        candidates = []
        for item in subtitles or []:
            forced = _as_bool(item.get("forced"))
            if only_foreign and not forced:
                continue
            if not only_foreign and not also_foreign and forced:
                continue
            language = _language_from_alpha2(
                item.get("language"),
                forced=(only_foreign or also_foreign) and forced,
                hi=_as_bool(item.get("hearing_impaired")),
            )
            if not language or not _language_requested(language, languages):
                continue
            fps = item.get("fps")
            if skip_wrong_fps and _wrong_fps(video, fps):
                override_matches = []
            else:
                override_matches = None

            movie_kind = "episode" if context.kind == "episode" else "movie"
            release_name = item.get("release_name") or ""
            series_title = search_result.get("title") or ""
            movie_name = f"\"{series_title}\" {release_name}" if movie_kind == "episode" else (series_title or release_name)
            movie_imdb_id = _with_tt(search_result.get("imdb_id") or context.imdb_id)
            candidate = _candidate(
                subtitle_id=item.get("subtitle_id"),
                mode="scraper",
                language=language,
                hearing_impaired=language.hi,
                page_link=item.get("download_url"),
                movie_kind=movie_kind,
                movie_name=movie_name,
                release_name=release_name,
                movie_year=search_result.get("year"),
                movie_imdb_id=movie_imdb_id,
                season=context.season if movie_kind == "episode" else None,
                episode=context.episode if movie_kind == "episode" else None,
                filename=item.get("filename") or "",
                encoding=None,
                fps=fps,
                matched_by="imdbid" if context.imdb_id else "query",
                hash_value=context.hash_value,
                uploader=item.get("uploader") or "anonymous",
                download_count=_as_int(item.get("download_count")) or 0,
                download_url=item.get("download_url"),
                video=video,
            )
            if override_matches is not None:
                candidate["matches"] = override_matches
                candidate["display"]["wrong_fps"] = True
            candidates.append(candidate)
        return candidates

    def _download_scraper(self, provider_payload, config):
        base_url = self._scraper_base_url(config)
        subtitle_id = str(provider_payload.get("subtitle_id") or "")
        if not subtitle_id:
            raise ValueError("opensubtitles.org scraper download requires subtitle_id")
        download_url = provider_payload.get("download_url") or provider_payload.get("page_link")
        response = self._http_post_json(
            f"{base_url}/api/v1/download/subtitle",
            {
                "subtitle_id": subtitle_id,
                "download_url": download_url or f"https://www.opensubtitles.org/en/subtitles/{subtitle_id}",
            },
            timeout=SCRAPER_TIMEOUT_SECONDS,
        )
        content = response.get("content")
        if not content:
            raise ServiceUnavailable("opensubtitles.org scraper returned no subtitle content")
        return _content_payload(base64.b64decode(content.encode("ascii"), validate=True))

    def _download_api(self, provider_payload, config):
        subtitle_id = str(provider_payload.get("subtitle_id") or "")
        if not subtitle_id:
            raise ValueError("opensubtitles.org API download requires subtitle_id")
        server = self._ensure_api_session(config)
        response = check_xmlrpc_response(server.DownloadSubtitles(self._api_token, [subtitle_id]))
        try:
            encoded = response["data"][0]["data"]
            content = zlib.decompress(base64.b64decode(encoded), 47)
        except (KeyError, IndexError, TypeError, ValueError, zlib.error) as exc:
            raise ServiceUnavailable("opensubtitles.org API returned an invalid download payload") from exc
        return _content_payload(content, encoding=provider_payload.get("encoding"))

    def _ensure_api_session(self, config):
        timeout = _as_int(config.get("timeout")) or DEFAULT_TIMEOUT_SECONDS
        endpoint = self._api_endpoint_url(config)
        if self._api_server is not None and self._api_token and self._api_endpoint in (None, endpoint):
            return self._api_server
        username = _clean_text(config.get("username"))
        password = _clean_text(config.get("password"))
        if bool(username) != bool(password):
            raise ValueError("opensubtitles.org username and password must be specified together")
        use_https = endpoint.startswith("https://")
        transport = _TimeoutSafeTransport(timeout) if use_https else _TimeoutTransport(timeout, use_https=False)
        server = xmlrpc.client.ServerProxy(endpoint, transport=transport, allow_none=True)
        response = check_xmlrpc_response(
            server.LogIn(username, password, "eng", config.get("user_agent") or USER_AGENT)
        )
        self._api_server = server
        self._api_token = response.get("token")
        self._api_endpoint = endpoint
        if not self._api_token:
            raise Unauthorized("opensubtitles.org login returned no token")
        return self._api_server

    def _api_endpoint_url(self, config):
        vip = _as_bool(config.get("is_vip", config.get("vip")))
        use_ssl = _as_bool(config.get("use_ssl", config.get("ssl")), default=True)
        host = VIP_API_URL if vip else DEFAULT_API_URL
        return f"{'https' if use_ssl else 'http'}://{host}"

    def _scraper_base_url(self, config):
        base_url = _clean_text((config or {}).get("scraper_service_url")) or DEFAULT_SCRAPER_URL
        if not base_url.startswith(("http://", "https://")):
            base_url = f"http://{base_url}"
        return base_url.rstrip("/")

    def _http_get_json(self, url, timeout=DEFAULT_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        return self._open_json(request, timeout)

    def _http_post_json(self, url, payload, timeout=SCRAPER_TIMEOUT_SECONDS):
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        return self._open_json(request, timeout)

    def _open_json(self, request, timeout):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 503}:
                retry_after = exc.headers.get("Retry-After")
                suffix = f", retry after {retry_after}s" if retry_after else ""
                raise RateLimited(f"opensubtitles.org helper busy{suffix}") from exc
            raise ServiceUnavailable(f"opensubtitles.org helper HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ServiceUnavailable(f"opensubtitles.org helper unavailable: {exc.reason}") from exc
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ServiceUnavailable("opensubtitles.org helper returned invalid JSON") from exc
