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
        title = (_coerce_text(video.get("title")) or "").strip()
        if not title:
            return []
        year = video.get("year")
        if year:
            return [f"{title} {year}", title]
        return [title]
    if kind == "episode":
        series = (_coerce_text(video.get("series")) or "").strip()
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


_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def _normalize(text):
    if not text:
        return ""
    # NFKD decomposition strips diacritics on Latin script while leaving CJK
    # and other non-Latin codepoints intact, so they survive normalization
    # and contribute to title/series matching.
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_tokens(text):
    return [token for token in _normalize(_coerce_text(text)).split(" ") if token]


def _coerce_text(value):
    """Collapse a video-metadata value to a single hashable string.

    Subliminal occasionally serialises multi-value fields (notably
    ``audio_codec`` and ``source``) as a Python ``list`` inside the worker
    payload. Passing a list straight into ``dict.get`` or ``str.lower``
    raises ``TypeError: unhashable type: 'list'`` and crashes search.
    Strings pass through unchanged; lists/tuples are space-joined; anything
    else falls back to ``str()``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(v) for v in value if v not in (None, ""))
        return joined or None
    return str(value)


def _season_tag_in(candidate_norm, season):
    """True when ``candidate_norm`` contains a ``s<season>`` tag.

    Operates on the *spaced* normalized form (e.g. ``"breaking bad s01e02
    1080p"``). Accepts ``s1``/``s01``/``s001``; rejects ``s12`` when looking
    for season 1 (next char is a digit). Matching either ``e`` or a word
    boundary after the season number lets ``s01`` inside ``s01e02`` count
    while still excluding the ``s12``/``s10`` case.
    """
    pattern = rf"\bs0*{int(season)}(?=e|\W|$)"
    return re.search(pattern, candidate_norm) is not None


def _episode_tag_in(candidate_norm, season, episode):
    """True when ``candidate_norm`` contains ``s<season>e<episode>``.

    Operates on the spaced normalized form so that a real word boundary
    follows the episode number. This rejects spurious prefix matches: a
    request for episode 2 must not match a candidate tagged ``S01E20``.
    Padding is optional on both numbers (``s1e2`` and ``s01e02`` both
    match).
    """
    pattern = rf"\bs0*{int(season)}e0*{int(episode)}\b"
    return re.search(pattern, candidate_norm) is not None


def compute_score(video, candidate_title):
    """Heuristic score in [60, 100] for a candidate result.

    - 100: movie title + year both present in candidate.
    - 95:  episode series + SxxExx tag both present in candidate.
    - 90:  movie title present, no year.
    - 85:  episode series present, no SxxExx tag.
    - 60:  candidate looks unrelated.
    """
    candidate_norm = _normalize(candidate_title)
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
                season = int(video.get("season"))
                episode = int(video.get("episode"))
            except (TypeError, ValueError):
                season = episode = None
            if (
                season is not None
                and episode is not None
                and _episode_tag_in(candidate_norm, season, episode)
            ):
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
    """Return True if any synonym in ``candidates`` is present in the release.

    Each entry may be a single token (``"bluray"``) or a multi-word value
    (``"DTS-HD"``, ``"WEB-DL"``); multi-word entries are split into
    alphanumeric chunks and require every chunk to appear in the release.
    """
    for token in candidates:
        if not token:
            continue
        chunks = [c for c in re.split(r"[^A-Za-z0-9]+", str(token).lower()) if c]
        if not chunks:
            continue
        if all(chunk in release_tokens for chunk in chunks):
            return True
    return False


def _multi_token_present(release_tokens, value):
    """Return True if every alphanumeric chunk of ``value`` is in the release.

    Used as a fallback for video metadata values that arrive as multi-word
    strings (e.g. ``"DTS-HD MA"``, ``"Blu-ray Remux"``, release groups with
    dots/dashes). The single-token form misses these; this splits ``value``
    on any non-alphanumeric run and requires every chunk to appear.
    """
    if not value:
        return False
    chunks = [c for c in re.split(r"[^A-Za-z0-9]+", str(value).lower()) if c]
    if not chunks:
        return False
    return all(chunk in release_tokens for chunk in chunks)


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
        if season is not None and _season_tag_in(candidate_norm, season):
            matches.append("season")
        if (
            season is not None
            and episode is not None
            and _episode_tag_in(candidate_norm, season, episode)
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

    # Release-name matches (apply to both movies and episodes). Each field
    # is coerced to a single string up front because bazarr/subliminal may
    # hand us list-valued metadata (e.g. multiple audio codecs).
    source = _coerce_text(video.get("source"))
    if source:
        token_list = _SOURCE_TOKENS.get(source)
        if token_list and _has_token(candidate_release_tokens, token_list):
            matches.append("source")
        elif token_list is None and _multi_token_present(
            candidate_release_tokens, source
        ):
            matches.append("source")

    resolution = _coerce_text(video.get("resolution"))
    if resolution and resolution.lower() in candidate_release_tokens:
        matches.append("resolution")

    video_codec = _coerce_text(video.get("video_codec"))
    if video_codec:
        token_list = _VIDEO_CODEC_TOKENS.get(video_codec)
        if token_list and _has_token(candidate_release_tokens, token_list):
            matches.append("video_codec")
        elif token_list is None and _multi_token_present(
            candidate_release_tokens, video_codec
        ):
            matches.append("video_codec")

    audio_codec = _coerce_text(video.get("audio_codec"))
    if audio_codec:
        token_list = _AUDIO_CODEC_TOKENS.get(audio_codec)
        if token_list and _has_token(candidate_release_tokens, token_list):
            matches.append("audio_codec")
        elif token_list is None and _multi_token_present(
            candidate_release_tokens, audio_codec
        ):
            matches.append("audio_codec")

    release_group = _coerce_text(video.get("release_group"))
    if release_group and _multi_token_present(
        candidate_release_tokens, release_group
    ):
        matches.append("release_group")

    streaming_service = _coerce_text(video.get("streaming_service"))
    if streaming_service and _multi_token_present(
        candidate_release_tokens, streaming_service
    ):
        matches.append("streaming_service")

    edition = _coerce_text(video.get("edition"))
    if edition and _multi_token_present(candidate_release_tokens, edition):
        matches.append("edition")

    return matches


_DOWNLOAD_RE = re.compile(
    rb'<a[^>]+id="download_([a-z]{2,3}(?:-[a-z0-9]{2,4})?)"'
    rb'[^>]+href="(/?subs/\d+/[^"]+-([a-z]{2,3}(?:-[a-z0-9]{2,4})?)\.srt)"',
    re.IGNORECASE,
)
_ORIG_FILENAME_RE = re.compile(
    rb"([A-Za-z0-9_\.\-]+?)\.([A-Za-z]+)-orig\.srt", re.IGNORECASE
)
# Loose fallback for ``...-orig.srt`` URLs that do not embed a named-language
# token. We extract the trailing 2-3 letter language code from the path so
# the source language can still be detected and the machine-translation
# filter is not silently bypassed.
_ORIG_CODE_RE = re.compile(
    # Case-sensitive on purpose: the base language code is always lowercase
    # in subtitlecat URLs, while the regional suffix may be upper/lowercase
    # (``en``, ``zh-CN``). Forcing lowercase on the base keeps the pattern
    # from greedily swallowing a filename stem like ``Foo-iw`` and treating
    # it as a single code.
    rb"(?:^|[/_.\-])([a-z]{2,3}(?:-[A-Za-z0-9]{2,4})?)-orig\.srt"
)

# Subtitlecat occasionally serves download anchors with deprecated alpha2
# codes or 3-letter alpha3 ids that do not match the canonical 2-letter set
# bazarr produces from ``Language.alpha2``. Map them so requested ``he``
# matches anchors emitted as ``iw``, ``tl`` matches ``fil``, etc.
_DOWNLOAD_ID_ALIASES = {
    "iw": "he",   # deprecated Hebrew
    "in": "id",   # deprecated Indonesian
    "ji": "yi",   # deprecated Yiddish
    "fil": "tl",  # bazarr canonical for Filipino is "tl"
    # ISO 639-2/B bibliographic codes — the rest of the file uses 639-2/T,
    # but subtitlecat sometimes emits these older 3-letter aliases.
    "alb": "sq",
    "arm": "hy",
    "baq": "eu",
    "bur": "my",
    "chi": "zh",
    "cze": "cs",
    "dut": "nl",
    "fre": "fr",
    "geo": "ka",
    "ger": "de",
    "gre": "el",
    "ice": "is",
    "mac": "mk",
    "may": "ms",
    "per": "fa",
    "rum": "ro",
    "slo": "sk",
    "tib": "bo",
    "wel": "cy",
}

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


def _canonical_alpha2(code):
    """Map a subtitlecat language id to a canonical bazarr alpha2.

    Handles three forms:

    - Regional tags such as ``zh-CN`` are stripped to their base (``zh``).
    - Deprecated 2-letter codes (``iw``, ``in``, ``ji``) and Filipino's
      ``fil`` alpha3 are remapped via :data:`_DOWNLOAD_ID_ALIASES`.
    - Other alpha3 codes (``fre``, ``ger``, ``dut``...) resolve through
      :data:`_ALPHA3_TO_ALPHA2`.

    Anything that doesn't match a known mapping is returned lowercased,
    unchanged — search() will then compare against the requested set as-is.
    """
    code = (code or "").lower()
    base = code.split("-", 1)[0]
    if base in _DOWNLOAD_ID_ALIASES:
        return _DOWNLOAD_ID_ALIASES[base]
    if len(base) == 3 and base in _ALPHA3_TO_ALPHA2:
        return _ALPHA3_TO_ALPHA2[base]
    return base


def _detect_source_language(html_bytes):
    """Best-effort detection of the original-language tag from the page.

    Two patterns are tried, in order:

    1. ``X.English-orig.srt`` form — extract the named language and map it
       through :data:`_LANGUAGE_NAME_TO_ALPHA2`.
    2. ``X-en-orig.srt`` / ``X-zh-CN-orig.srt`` form — the trailing
       language code is canonicalised via :func:`_canonical_alpha2`.

    When neither pattern hits, returns ``None`` (callers then skip the
    machine-translation filter for that page).
    """
    match = _ORIG_FILENAME_RE.search(html_bytes)
    if match:
        candidate = match.group(2).decode("ascii", errors="replace").lower()
        named = _LANGUAGE_NAME_TO_ALPHA2.get(candidate)
        if named:
            return named
    match = _ORIG_CODE_RE.search(html_bytes)
    if match:
        raw = match.group(1).decode("ascii", errors="replace").lower()
        return _canonical_alpha2(raw)
    return None


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
        # Canonicalise the anchor id so it lines up with the alpha2 set
        # bazarr produces from ``Language.alpha2``. This collapses regional
        # tags (``zh-CN`` -> ``zh``), remaps deprecated codes
        # (``iw`` -> ``he``), and folds alpha3 ids (``fil`` -> ``tl``).
        base = _canonical_alpha2(code)
        downloads.setdefault(base, f"{BASE_URL}/{_safe_url(path)}")
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

        include_mt = config.get("include_machine_translated", True)
        results = []
        seen_ids = set()
        for query in queries:
            url = (
                f"{BASE_URL}/index.php?search="
                + urllib.parse.quote(query, safe="")
            )
            _sleep(config)
            html = self._http_get(url)
            page_results = parse_search_results(html)[:MAX_CANDIDATES_PER_QUERY]
            new_candidates = []
            for entry in page_results:
                if entry["detail_id"] in seen_ids:
                    continue
                seen_ids.add(entry["detail_id"])
                new_candidates.append(entry)
            for candidate in new_candidates:
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
                            "page_link": candidate["detail_url"],
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
            # Fall back to the loose query only if the precise one produced
            # no usable results after language and machine-translation
            # filtering — not just because it returned zero search rows.
            if results:
                break
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
