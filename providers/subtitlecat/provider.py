"""SubtitleCat provider for the Bazarr+ Provider Hub catalog.

This module is loaded by the hub worker in an isolated process. It uses only
the Python standard library; no third-party imports are permitted here.
"""

import base64 as _base64
import hashlib as _hashlib
import re
import time
import unicodedata
import urllib.parse
import urllib.request

PROVIDER_ID = "subtitlecat"
BASE_URL = "https://www.subtitlecat.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 15
MAX_CANDIDATES_PER_QUERY = 10


def build_queries(video):
    """Return the ordered list of search queries to try for the given video.

    The first entry is the precise query (title + year for movies, series +
    SxxExx for episodes). When the precise form has additional signal beyond
    the loose form, a single fallback loose query is appended.
    """
    video = video or {}
    kind = video.get("kind")
    if kind == "movie":
        title = (video.get("title") or "").strip()
        if not title:
            return []
        year = video.get("year")
        if year:
            return [f"{title} {year}", title]
        return [title]
    if kind == "episode":
        series = (video.get("series") or "").strip()
        season = video.get("season")
        episode = video.get("episode")
        if not series or season is None or episode is None:
            return []
        try:
            tag = f"S{int(season):02d}E{int(episode):02d}"
        except (TypeError, ValueError):
            return []
        return [f"{series} {tag}", series]
    return []


_DETAIL_LINK_RE = re.compile(
    rb'<a[^>]+href="(/?subs/(\d+)/([^"]+\.html))"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(rb"<[^>]+>")
_WHITESPACE_RE = re.compile(rb"\s+")


def _strip_tags(text_bytes):
    return (
        _WHITESPACE_RE.sub(b" ", _TAG_RE.sub(b"", text_bytes))
        .strip()
        .decode("utf-8", errors="replace")
    )


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(text):
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", ascii_text.lower()).strip()


def _normalize_tokens(text):
    return [token for token in _normalize(text).split(" ") if token]


def compute_score(video, candidate_title):
    """Heuristic score in [60, 100] for a candidate result.

    - 100: movie title + year both present in candidate.
    - 95:  episode series + SxxExx tag both present in candidate.
    - 90:  movie title present, no year.
    - 85:  episode series present, no SxxExx tag.
    - 60:  candidate looks unrelated.
    """
    candidate_norm_compact = _normalize(candidate_title).replace(" ", "")
    candidate_tokens = set(_normalize_tokens(candidate_title))
    kind = (video or {}).get("kind")

    if kind == "movie":
        title_tokens = _normalize_tokens(video.get("title"))
        if title_tokens and all(t in candidate_tokens for t in title_tokens):
            year = video.get("year")
            if year and str(year) in candidate_tokens:
                return 100
            return 90
        return 60

    if kind == "episode":
        series_tokens = _normalize_tokens(video.get("series"))
        if series_tokens and all(t in candidate_tokens for t in series_tokens):
            try:
                tag = f"s{int(video.get('season')):02d}e{int(video.get('episode')):02d}"
            except (TypeError, ValueError):
                tag = None
            if tag and tag in candidate_norm_compact:
                return 95
            return 85
        return 60

    return 60


# Release-name match tables. Keys are the values bazarr/subliminal exposes on
# the Video object; the inner list is the set of synonymous tokens we'll look
# for inside a release title. Matching is case-insensitive on tokenized text.
_SOURCE_TOKENS = {
    "Blu-ray": ["bluray", "blueray", "brrip", "bdrip", "bd"],
    "Web": ["web", "webrip", "webdl", "web-dl"],
    "WEB-DL": ["webdl", "web-dl", "web"],
    "WEBRip": ["webrip", "web-rip", "web"],
    "HDTV": ["hdtv"],
    "DVD": ["dvd", "dvdrip"],
    "TS": ["ts", "telesync"],
    "CAM": ["cam", "camrip"],
    "HDRip": ["hdrip"],
}
_VIDEO_CODEC_TOKENS = {
    "H.264": ["h264", "x264"],
    "H.265": ["h265", "x265", "hevc"],
    "DivX": ["divx"],
    "XviD": ["xvid"],
}
_AUDIO_CODEC_TOKENS = {
    "AC3": ["ac3", "dd"],
    "EAC3": ["eac3", "ddp", "dd+"],
    "AAC": ["aac"],
    "DTS": ["dts"],
    "DTS-HD": ["dtshd", "dts-hd"],
    "FLAC": ["flac"],
    "MP3": ["mp3"],
    "TrueHD": ["truehd"],
}


def _release_tokens(text):
    if not text:
        return set()
    # Treat any non-alphanumeric run as a separator. Lowercased for matching.
    return {chunk for chunk in re.split(r"[^A-Za-z0-9]+", str(text).lower()) if chunk}


def _has_token(release_tokens, candidates):
    return any(token.lower() in release_tokens for token in candidates)


def derive_matches(video, candidate_title):
    """Compute the subliminal-shaped match set for a candidate.

    These keys feed into bazarr's downstream score calculation
    (``custom_libs/subliminal_patch/score.py``). Movie weights total ~180 and
    episode weights total ~360 (excluding hash). Returning more keys lifts
    the displayed score; the function only adds a key if the video metadata
    actually appears in the candidate's release name.
    """
    if not video:
        return []
    candidate_norm = _normalize(candidate_title)
    candidate_compact = candidate_norm.replace(" ", "")
    candidate_tokens = set(_normalize_tokens(candidate_title))
    candidate_release_tokens = _release_tokens(candidate_title)
    matches = []
    kind = video.get("kind")

    if kind == "movie":
        title_tokens = _normalize_tokens(video.get("title"))
        if title_tokens and all(t in candidate_tokens for t in title_tokens):
            matches.append("title")
        year = video.get("year")
        if year and str(year) in candidate_tokens:
            matches.append("year")
    elif kind == "episode":
        series_tokens = _normalize_tokens(video.get("series"))
        if series_tokens and all(t in candidate_tokens for t in series_tokens):
            matches.append("series")
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            season = episode = None
        if season is not None and f"s{season:02d}" in candidate_compact:
            matches.append("season")
        if (
            season is not None
            and episode is not None
            and f"s{season:02d}e{episode:02d}" in candidate_compact
        ):
            matches.append("episode")
        year = video.get("year")
        if year and str(year) in candidate_tokens:
            matches.append("year")
        episode_title_tokens = _normalize_tokens(video.get("episode_title"))
        if (
            episode_title_tokens
            and len(episode_title_tokens) > 1
            and all(t in candidate_tokens for t in episode_title_tokens)
        ):
            matches.append("title")

    # Release-name matches (apply to both movies and episodes)
    source = video.get("source")
    if source:
        token_list = _SOURCE_TOKENS.get(source, [source])
        if _has_token(candidate_release_tokens, token_list):
            matches.append("source")

    resolution = video.get("resolution")
    if resolution and str(resolution).lower() in candidate_release_tokens:
        matches.append("resolution")

    video_codec = video.get("video_codec")
    if video_codec:
        token_list = _VIDEO_CODEC_TOKENS.get(video_codec, [video_codec])
        if _has_token(candidate_release_tokens, token_list):
            matches.append("video_codec")

    audio_codec = video.get("audio_codec")
    if audio_codec:
        token_list = _AUDIO_CODEC_TOKENS.get(audio_codec, [audio_codec])
        if _has_token(candidate_release_tokens, token_list):
            matches.append("audio_codec")

    release_group = video.get("release_group")
    if release_group and str(release_group).lower() in candidate_release_tokens:
        matches.append("release_group")

    streaming_service = video.get("streaming_service")
    if streaming_service and str(streaming_service).lower() in candidate_release_tokens:
        matches.append("streaming_service")

    edition = video.get("edition")
    if edition and any(
        token.lower() in candidate_release_tokens
        for token in str(edition).split()
        if token
    ):
        matches.append("edition")

    return matches


_DOWNLOAD_RE = re.compile(
    rb'<a[^>]+id="download_([a-z]{2,3})"[^>]+href="(/?subs/\d+/[^"]+-([a-z]{2,3})\.srt)"',
    re.IGNORECASE,
)
_ORIG_FILENAME_RE = re.compile(
    rb"([A-Za-z0-9_\.\-]+?)\.([A-Za-z]+)-orig\.srt", re.IGNORECASE
)

_LANGUAGE_NAME_TO_ALPHA2 = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "polish": "pl",
    "russian": "ru",
    "turkish": "tr",
    "arabic": "ar",
    "hindi": "hi",
    "indonesian": "id",
    "dutch": "nl",
    "chinese": "zh",
    "japanese": "ja",
    "korean": "ko",
    "czech": "cs",
    "greek": "el",
    "hungarian": "hu",
    "romanian": "ro",
    "bulgarian": "bg",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "ukrainian": "uk",
    "slovak": "sk",
    "croatian": "hr",
    "serbian": "sr",
    "slovenian": "sl",
    "lithuanian": "lt",
    "latvian": "lv",
    "estonian": "et",
    "vietnamese": "vi",
    "thai": "th",
    "malay": "ms",
    "filipino": "tl",
    "hebrew": "he",
    "persian": "fa",
    "urdu": "ur",
    "bengali": "bn",
    "tamil": "ta",
    "telugu": "te",
    "marathi": "mr",
    "kannada": "kn",
    "malayalam": "ml",
    "sinhala": "si",
    "georgian": "ka",
    "armenian": "hy",
    "azerbaijani": "az",
    "kazakh": "kk",
    "uzbek": "uz",
}


def _detect_source_language(html_bytes):
    """Best-effort detection of the original-language tag from the page."""
    match = _ORIG_FILENAME_RE.search(html_bytes)
    if not match:
        return None
    candidate = match.group(2).decode("ascii", errors="replace").lower()
    return _LANGUAGE_NAME_TO_ALPHA2.get(candidate)


def _safe_url(path):
    """Encode unsafe characters in a path so urllib can fetch it.

    Detail-page hrefs sometimes embed raw spaces, parentheses, and brackets
    that urllib refuses. We percent-encode every char except the small set
    that is safe in a URL path / query.
    """
    return urllib.parse.quote(path, safe="/-_.~()%")


def parse_detail_languages(html_bytes):
    """Return ``(source_alpha2, {alpha2: absolute_download_url})``.

    Only entries with a real Download anchor are returned. Translate-only
    languages (rendered as ``<button>``) are skipped — subtitlecat translates
    them via client-side JS, which the worker cannot replicate.
    """
    if not html_bytes:
        return (None, {})
    downloads = {}
    for match in _DOWNLOAD_RE.finditer(html_bytes):
        code = match.group(1).decode("ascii", errors="replace").lower()
        url_suffix = match.group(2).decode("utf-8", errors="replace")
        path = url_suffix.lstrip("/")
        downloads[code] = f"{BASE_URL}/{_safe_url(path)}"
    return (_detect_source_language(html_bytes), downloads)


def parse_search_results(html_bytes):
    """Return a list of {detail_id, detail_url, title} dicts.

    Only the first occurrence of each detail_id is kept. The order of the
    response reflects the order in which subtitlecat presents results, which
    is its own relevance ranking. Anchors may be either relative
    (``href="subs/..."``) or absolute (``href="/subs/..."``); both are
    accepted and normalized to absolute URLs.
    """
    if not html_bytes:
        return []
    seen = set()
    results = []
    for match in _DETAIL_LINK_RE.finditer(html_bytes):
        relative_url = match.group(1).decode("ascii", errors="replace")
        detail_id = match.group(2).decode("ascii", errors="replace")
        title = _strip_tags(match.group(4))
        if not title or detail_id in seen:
            continue
        seen.add(detail_id)
        # Normalize to an absolute URL regardless of whether the source had a
        # leading slash on the href. Detail-page hrefs may contain unencoded
        # spaces or parentheses from raw release titles; sanitize them here.
        path = relative_url.lstrip("/")
        results.append(
            {
                "detail_id": detail_id,
                "detail_url": f"{BASE_URL}/{_safe_url(path)}",
                "title": title,
            }
        )
    return results


_ALPHA3_TO_ALPHA2 = {
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "deu": "de",
    "ita": "it",
    "por": "pt",
    "pol": "pl",
    "rus": "ru",
    "tur": "tr",
    "ara": "ar",
    "hin": "hi",
    "ind": "id",
    "nld": "nl",
    "zho": "zh",
    "jpn": "ja",
    "kor": "ko",
    "ces": "cs",
    "ell": "el",
    "hun": "hu",
    "ron": "ro",
    "bul": "bg",
    "swe": "sv",
    "dan": "da",
    "nor": "no",
    "fin": "fi",
    "ukr": "uk",
    "slk": "sk",
    "hrv": "hr",
    "srp": "sr",
    "slv": "sl",
    "lit": "lt",
    "lav": "lv",
    "est": "et",
    "vie": "vi",
    "tha": "th",
    "msa": "ms",
    "fil": "tl",
    "heb": "he",
    "fas": "fa",
    "urd": "ur",
    "ben": "bn",
    "tam": "ta",
    "tel": "te",
    "mar": "mr",
    "kan": "kn",
    "mal": "ml",
    "sin": "si",
    "kat": "ka",
    "hye": "hy",
    "aze": "az",
    "kaz": "kk",
    "uzb": "uz",
}
_ALPHA2_TO_ALPHA3 = {v: k for k, v in _ALPHA3_TO_ALPHA2.items()}


def _alpha2_for(language):
    if not isinstance(language, dict):
        return None
    alpha2 = (language.get("alpha2") or "").lower()
    if alpha2:
        return alpha2
    alpha3 = (language.get("alpha3") or "").lower()
    return _ALPHA3_TO_ALPHA2.get(alpha3)


def _alpha3_for(alpha2):
    return _ALPHA2_TO_ALPHA3.get(alpha2)


def _sleep(config):
    delay_ms = (config or {}).get("request_delay_ms", 0) or 0
    if delay_ms > 0:
        time.sleep(min(delay_ms, 5000) / 1000.0)


def _matches_for(video):
    # Kept only for backwards compatibility in case external code imports it.
    # search() now uses derive_matches(video, candidate_title) for the per-
    # candidate set so that release-name attributes can contribute to the
    # downstream subliminal score.
    kind = (video or {}).get("kind")
    if kind == "movie":
        if video.get("year"):
            return ["title", "year"]
        return ["title"]
    if kind == "episode":
        return ["series", "season", "episode"]
    return []


class SubtitlecatProvider:
    """Provider Hub V1 plugin for subtitlecat.com.

    The hub worker instantiates the class with no arguments and calls
    ``search(video, languages, config)`` followed by ``download(payload,
    language, config)`` for each chosen result. All HTTP is funneled through
    :py:meth:`_http_get` so tests can monkeypatch it without touching urllib.
    """

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def search(self, video, languages, config):
        config = dict(config or {})
        requested_alpha2 = set()
        for lang in languages or []:
            code = _alpha2_for(lang)
            if code:
                requested_alpha2.add(code)
        if not requested_alpha2:
            return []

        queries = build_queries(video)
        if not queries:
            return []

        candidates = []
        seen_ids = set()
        for query in queries:
            url = (
                f"{BASE_URL}/index.php?search="
                + urllib.parse.quote(query, safe="")
            )
            _sleep(config)
            html = self._http_get(url)
            page_results = parse_search_results(html)[:MAX_CANDIDATES_PER_QUERY]
            for entry in page_results:
                if entry["detail_id"] in seen_ids:
                    continue
                seen_ids.add(entry["detail_id"])
                candidates.append(entry)
            if candidates:
                # Precise query already produced candidates; skip the loose
                # fallback entirely.
                break

        if not candidates:
            return []

        include_mt = config.get("include_machine_translated", True)
        results = []
        for candidate in candidates:
            _sleep(config)
            detail_html = self._http_get(candidate["detail_url"])
            source_alpha2, downloads = parse_detail_languages(detail_html)
            for alpha2, srt_url in downloads.items():
                if alpha2 not in requested_alpha2:
                    continue
                if (
                    not include_mt
                    and source_alpha2
                    and alpha2 != source_alpha2
                ):
                    continue
                alpha3 = _alpha3_for(alpha2)
                if not alpha3:
                    continue
                score = compute_score(video, candidate["title"])
                results.append(
                    {
                        "provider": PROVIDER_ID,
                        "id": f"subtitlecat-{candidate['detail_id']}-{alpha3}",
                        "language": {
                            "alpha3": alpha3,
                            "alpha2": alpha2,
                            "hi": False,
                            "forced": False,
                        },
                        "release_info": candidate["title"],
                        "filename": (
                            f"subtitlecat.{candidate['detail_id']}.{alpha2}.srt"
                        ),
                        "matches": derive_matches(video, candidate["title"]),
                        "score": score,
                        "score_without_hash": score,
                        "score_out_of": 100,
                        "hash_verifiable": False,
                        "hearing_impaired_verifiable": False,
                        "hearing_impaired": False,
                        "display": {
                            "source": "subtitlecat",
                            "title": candidate["title"],
                            "detail_url": candidate["detail_url"],
                        },
                        "provider_payload": {
                            "provider": PROVIDER_ID,
                            "schema": 1,
                            "subtitle_url": srt_url,
                            "detail_id": candidate["detail_id"],
                            "language": alpha3,
                        },
                    }
                )
        return results

    def download(self, provider_payload, language, config):
        del language, config  # unused
        url = (provider_payload or {}).get("subtitle_url")
        if not url:
            raise ValueError("subtitlecat download requires subtitle_url")
        body = self._http_get(url)
        if not body:
            return {
                "content_b64": "",
                "content_sha256": "",
                "content_type": "application/x-subrip",
                "format": "srt",
                "encoding": "utf-8",
                "empty": True,
            }
        try:
            body.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "latin-1"
        return {
            "content_b64": _base64.b64encode(body).decode("ascii"),
            "content_sha256": _hashlib.sha256(body).hexdigest(),
            "content_type": "application/x-subrip",
            "format": "srt",
            "encoding": encoding,
            "empty": False,
        }
