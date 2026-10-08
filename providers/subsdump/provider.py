"""SubsDump provider for the Bazarr+ Provider Hub catalog.

SubsDump is a self-hosted Subscene archive search and download service
(upstream project D3lphi3r/SubsDump, release v2.1.0). A Bazarr+ install
points this provider at its own SubsDump instance with a base URL and an
optional API key. This is a JSON API client, not a scraper, and it is
independent of the sub_scene plugin: the two never share result ids.
"""

import base64
import hashlib
import json
import re
import ssl
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


PROVIDER_ID = "subsdump"
HTTP_TIMEOUT_SECONDS = 20
DOWNLOAD_TIMEOUT_SECONDS = 30
USER_AGENT = "BazarrProviderHub/1.0 (+https://github.com/LavX/bazarr-provider-catalog)"
# A search response is a small JSON document; a download is a subtitle or a
# small archive, so a body above these caps could never be accepted.
MAX_SEARCH_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
# The service caps the release query parameter itself, so anything longer is
# truncated rather than sent.
RELEASE_PARAM_MAX_CHARS = 512
# Release strings persisted in the provider payload are capped at the same
# length the service itself accepts.
PAYLOAD_RELEASE_MAX_CHARS = 512
PER_PAGE = 100
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

# The language codes the service accepts as query parameter values. The
# Brazilian Portuguese variant is declared like the assrt country variants:
# an explicit catalog entry, but never an invented alpha3 in a result.
_LANGUAGE_CODES = {
    "ara": {"alpha2": "ar"},
    "aze": {"alpha2": "az"},
    "bel": {"alpha2": "be"},
    "ben": {"alpha2": "bn"},
    "bos": {"alpha2": "bs"},
    "bul": {"alpha2": "bg"},
    "cat": {"alpha2": "ca"},
    "ces": {"alpha2": "cs"},
    "dan": {"alpha2": "da"},
    "deu": {"alpha2": "de"},
    "ell": {"alpha2": "el"},
    "eng": {"alpha2": "en"},
    "epo": {"alpha2": "eo"},
    "est": {"alpha2": "et"},
    "eus": {"alpha2": "eu"},
    "fas": {"alpha2": "fa"},
    "fin": {"alpha2": "fi"},
    "fra": {"alpha2": "fr"},
    "heb": {"alpha2": "he"},
    "hin": {"alpha2": "hi"},
    "hrv": {"alpha2": "hr"},
    "hun": {"alpha2": "hu"},
    "hye": {"alpha2": "hy"},
    "ind": {"alpha2": "id"},
    "isl": {"alpha2": "is"},
    "ita": {"alpha2": "it"},
    "jpn": {"alpha2": "ja"},
    "kal": {"alpha2": "kl"},
    "kan": {"alpha2": "kn"},
    "kat": {"alpha2": "ka"},
    "khm": {"alpha2": "km"},
    "kin": {"alpha2": "rw"},
    "kor": {"alpha2": "ko"},
    "kur": {"alpha2": "ku"},
    "lav": {"alpha2": "lv"},
    "lit": {"alpha2": "lt"},
    "mal": {"alpha2": "ml"},
    "mkd": {"alpha2": "mk"},
    # Manipuri has no ISO 639-1 code, so the alpha3 value is carried as the
    # alpha2 as well, the same convention the isubtitles provider uses.
    "mni": {"alpha2": "mni"},
    "mon": {"alpha2": "mn"},
    "msa": {"alpha2": "ms"},
    "mya": {"alpha2": "my"},
    "nep": {"alpha2": "ne"},
    "nld": {"alpha2": "nl"},
    "nor": {"alpha2": "no"},
    "pan": {"alpha2": "pa"},
    "pol": {"alpha2": "pl"},
    "por": {"alpha2": "pt"},
    "por-BR": {"alpha2": "pt", "country": "BR"},
    "pus": {"alpha2": "ps"},
    "ron": {"alpha2": "ro"},
    "rus": {"alpha2": "ru"},
    "sin": {"alpha2": "si"},
    "slk": {"alpha2": "sk"},
    "slv": {"alpha2": "sl"},
    "som": {"alpha2": "so"},
    "spa": {"alpha2": "es"},
    "sqi": {"alpha2": "sq"},
    "srp": {"alpha2": "sr"},
    "sun": {"alpha2": "su"},
    "swa": {"alpha2": "sw"},
    "swe": {"alpha2": "sv"},
    "tam": {"alpha2": "ta"},
    "tel": {"alpha2": "te"},
    "tgl": {"alpha2": "tl"},
    "tha": {"alpha2": "th"},
    "tur": {"alpha2": "tr"},
    "ukr": {"alpha2": "uk"},
    "urd": {"alpha2": "ur"},
    "vie": {"alpha2": "vi"},
    "yor": {"alpha2": "yo"},
    "zho": {"alpha2": "zh"},
}
_ALPHA2_TO_CODE = {}
for _code, _meta in _LANGUAGE_CODES.items():
    if _code == "por-BR":
        continue
    _ALPHA2_TO_CODE.setdefault(_meta["alpha2"], _code)

_IMDB_ID_RE = re.compile(r"^tt\d{1,10}$")
# fullmatch anchors the whole pattern: a payload path with a trailing
# newline cannot match.
_CONTENT_PATH_RE = re.compile(r"/api/v1/subtitles/\d{1,19}/content\Z")
_MICRODVD_RE = re.compile(r"^\{\d+\}\{\d+\}")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


class AuthenticationError(ValueError):
    """The instance rejected the API key (HTTP 401/403)."""


class ConfigurationError(ValueError):
    """The base_url or its reply is not a usable SubsDump v1 instance."""


class ServiceUnavailable(RuntimeError):
    """The instance answered that it is not ready (HTTP 503)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    The Location header of a 3xx names a host of its own, and that host has
    no business receiving the API key, so no redirect is ever followed and
    the 3xx surfaces as a loud error instead.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _coerce_text(value):
    """Collapse a video-metadata value to a single string.

    The worker occasionally serialises multi-value fields (notably
    ``audio_codec`` and ``source``) as a Python list. Passing a list into
    ``str.lower`` or set building would crash the search, so lists are
    joined and everything else stringifies.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        joined = " ".join(str(item) for item in value if item not in (None, ""))
        return joined or None
    return str(value)


def _normalize(text):
    if not text:
        return ""
    # NFKD decomposition strips diacritics on Latin script while leaving
    # non-Latin codepoints intact so they survive normalization.
    decomposed = unicodedata.normalize("NFKD", str(text))
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_tokens(text):
    return [token for token in _normalize(_coerce_text(text)).split(" ") if token]


def _release_tokens(text):
    if not text:
        return set()
    return {chunk for chunk in re.split(r"[^A-Za-z0-9]+", str(text).lower()) if chunk}


def _has_token(release_tokens, candidates):
    """True if any synonym in ``candidates`` is present in the release.

    Each entry may be a single token (``"bluray"``) or a multi-word value
    (``"DTS-HD"``, ``"WEB-DL"``); multi-word entries are split into
    alphanumeric chunks and require every chunk to appear in the release.
    """
    for token in candidates:
        if not token:
            continue
        chunks = [chunk for chunk in re.split(r"[^A-Za-z0-9]+", str(token).lower()) if chunk]
        if not chunks:
            continue
        if all(chunk in release_tokens for chunk in chunks):
            return True
    return False


def _multi_token_present(release_tokens, value):
    """True if every alphanumeric chunk of ``value`` is in the release."""
    if not value:
        return False
    chunks = [chunk for chunk in re.split(r"[^A-Za-z0-9]+", str(value).lower()) if chunk]
    if not chunks:
        return False
    return all(chunk in release_tokens for chunk in chunks)


# Release-name match tables. Keys are the values bazarr exposes on the video
# object; the inner list is the set of synonymous tokens searched inside a
# release name. Matching is case-insensitive on tokenized text.
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
    "H.264": ["h264", "x264", "avc"],
    "H.265": ["h265", "x265", "hevc"],
    "DivX": ["divx"],
    "XviD": ["xvid"],
}
_AUDIO_CODEC_TOKENS = {
    "AAC": ["aac"],
    "DTS": ["dts"],
    "DTS-HD": ["dtshd", "dts-hd"],
    "FLAC": ["flac"],
    "MP3": ["mp3"],
    "TrueHD": ["truehd"],
}
# Dolby Digital is AC3 and Dolby Digital Plus is EAC3; the Plus track must
# not be claimed for a plain Dolby Digital request, so the two get ordered
# dedicated handling instead of plain synonym lists.
_AC3_TOKENS = ["ac3", "dd", "dolby digital"]
_EAC3_TOKENS = ["eac3", "ddp", "dolby digital plus"]
_DOLBY_DIGITAL_PLUS_TOKENS = {"dolby", "digital", "plus"}
_TRAILING_DIGITS_RE = re.compile(r"^(.*[a-z])\d{1,2}$")

# Release names abbreviate streaming services (``HMAX``, ``AMZN``), so the
# canonical names the video carries need their token synonyms.
_STREAMING_SERVICE_TOKENS = {
    "HBO Max": ["hmax", "hbo"],
    "Amazon Prime": ["amzn", "amazon", "prime"],
    "Netflix": ["nf", "netflix"],
    "Disney+": ["dsnp", "disney"],
    "Apple TV+": ["atvp", "apple tv"],
    "Hulu": ["hulu"],
    "Peacock": ["pcok", "peacock"],
    "Paramount+": ["pmtp", "paramount"],
    "Crunchyroll": ["crch", "crunchyroll"],
    "HIDIVE": ["hidive"],
}

_ATTRIBUTE_KEYS = (
    "source",
    "resolution",
    "video_codec",
    "audio_codec",
    "release_group",
    "streaming_service",
    "edition",
)
_GROUP_SEGMENT_RE = re.compile(r"-[^-\s]+$")


def _audio_tokens(release):
    """Release tokens for audio matching, with channel suffixes stripped.

    Release names run the channel count straight into the codec tag
    (``DDP5.1`` tokenizes as ``ddp5``), so audio matching also considers
    tokens with the trailing channel digits stripped. ``DD+`` would
    tokenize as plain ``dd`` (AC3) although it means Dolby Digital Plus, so
    it is rewritten to ``ddp`` before tokenizing.
    """
    text = re.sub(r"(?<![A-Za-z0-9])dd\+", "ddp", str(release or ""), flags=re.IGNORECASE)
    tokens = _release_tokens(text)
    stripped = set()
    for token in tokens:
        match = _TRAILING_DIGITS_RE.match(token)
        if match:
            stripped.add(match.group(1))
    return tokens | stripped


def _audio_codec_matches(audio_codec, release):
    """True when one release name carries the requested audio codec."""
    tokens = _audio_tokens(release)
    if audio_codec == "EAC3":
        return _has_token(tokens, _EAC3_TOKENS)
    if audio_codec == "AC3":
        if _DOLBY_DIGITAL_PLUS_TOKENS <= tokens:
            # Dolby Digital Plus is EAC3, not AC3.
            return False
        return _has_token(tokens, _AC3_TOKENS)
    token_list = _AUDIO_CODEC_TOKENS.get(audio_codec)
    if token_list:
        return _has_token(tokens, token_list)
    return _multi_token_present(tokens, audio_codec)


def _without_release_group(release):
    """Return a release name without its trailing release-group segment.

    Release names end with the release group after a hyphen
    (``...x264-CM``). Short group names collide with source synonyms: the
    group ``BD`` in ``...WEB-DL.x264-BD`` would otherwise read as a
    Blu-ray source, so source matching runs on the name without its
    trailing group segment.
    """
    return _GROUP_SEGMENT_RE.sub("", release)


def _source_tokens(release, title_tokens):
    """Release tokens for source matching, without title and group tokens.

    Release names lead with the production title, and a title word can
    collide with a source synonym (``The.Web.2024.1080p.BluRay`` is a
    Blu-ray release whose title word ``Web`` is not a source claim). So
    source matching runs on the release without its trailing group
    segment and without the record's title tokens.
    """
    text = _without_release_group(release)
    return {token for token in _release_tokens(text) if token not in title_tokens}


def _release_attribute_matches(video, media_title, releases):
    """Release-level match keys, evaluated per release name.

    Each release name is evaluated independently and the resulting match
    keys are unioned. Pooling every release's tokens into one set would
    manufacture matches: a video release group ``FOO-BAR`` must not match
    one release ending ``-FOO`` and another ending ``-BAR``. The keys are
    returned in a fixed order.
    """
    video = video or {}
    matched = set()
    title_tokens = set(_normalize_tokens(media_title))
    for release in releases:
        tokens = _release_tokens(release)
        source = _coerce_text(video.get("source"))
        if source:
            source_tokens = _source_tokens(release, title_tokens)
            token_list = _SOURCE_TOKENS.get(source)
            if (token_list and _has_token(source_tokens, token_list)) or (
                token_list is None and _multi_token_present(source_tokens, source)
            ):
                matched.add("source")
        resolution = _coerce_text(video.get("resolution"))
        if resolution and str(resolution).lower() in tokens:
            matched.add("resolution")
        video_codec = _coerce_text(video.get("video_codec"))
        if video_codec:
            token_list = _VIDEO_CODEC_TOKENS.get(video_codec)
            if (token_list and _has_token(tokens, token_list)) or (
                token_list is None and _multi_token_present(tokens, video_codec)
            ):
                matched.add("video_codec")
        audio_codec = _coerce_text(video.get("audio_codec"))
        if audio_codec and _audio_codec_matches(audio_codec, release):
            matched.add("audio_codec")
        release_group = _coerce_text(video.get("release_group"))
        if release_group and _multi_token_present(tokens, release_group):
            matched.add("release_group")
        streaming_service = _coerce_text(video.get("streaming_service"))
        if streaming_service:
            token_list = _STREAMING_SERVICE_TOKENS.get(streaming_service)
            if (token_list and _has_token(tokens, token_list)) or (
                token_list is None and _multi_token_present(tokens, streaming_service)
            ):
                matched.add("streaming_service")
        edition = _coerce_text(video.get("edition"))
        if edition and _multi_token_present(tokens, edition):
            matched.add("edition")
    return [key for key in _ATTRIBUTE_KEYS if key in matched]


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _base_url(config):
    """Return the validated base URL of the SubsDump instance."""
    base_url = str((config or {}).get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ConfigurationError("SubsDump base_url must be specified")
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ConfigurationError("SubsDump base_url must start with http:// or https://")
    # A scheme with no host ("http:///api/v1") used to validate and produced
    # a request against an empty host. Reading the port here also surfaces a
    # malformed one as a config-time error instead of a request-time one.
    try:
        parts.port
    except ValueError as error:
        raise ConfigurationError(f"SubsDump base_url carries a bad port: {error}") from error
    if not parts.netloc or not parts.hostname:
        raise ConfigurationError("SubsDump base_url must carry a host")
    # Credentials are rejected even when empty ("http://:@host"), which
    # urllib parses as username and password values.
    if parts.username is not None or parts.password is not None:
        raise ConfigurationError("SubsDump base_url must not carry user credentials")
    if parts.query:
        raise ConfigurationError("SubsDump base_url must not carry a query string")
    if parts.fragment:
        raise ConfigurationError("SubsDump base_url must not carry a fragment")
    return base_url


def _api_key(config):
    api_key = str((config or {}).get("api_key") or "").strip()
    return api_key or None


def _service_code(value):
    """Resolve a requested language to the code the service accepts, or None.

    Accepts the service's own alpha3 codes, plain alpha2 codes and country
    decorated forms such as ``por-BR`` or ``pt-BR``. The worker builds its
    requested languages from babelfish alpha3 codes (ISO 639-3), the same
    codes the service itself emits, so the ISO 639-2/B bibliographic
    aliases never arrive here and are deliberately not mapped: an unknown
    code resolves to None and the language is skipped, never guessed.
    Only ``por`` with ``BR`` is a distinct service code; every other
    regional suffix resolves to its base language.
    """
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if candidate in _LANGUAGE_CODES:
        return candidate
    lowered = candidate.lower()
    if lowered in _LANGUAGE_CODES:
        return lowered
    if lowered in _ALPHA2_TO_CODE:
        return _ALPHA2_TO_CODE[lowered]
    if "-" in candidate:
        base, _, suffix = candidate.partition("-")
        base = _ALPHA2_TO_CODE.get(base.lower(), base.lower())
        country = suffix.strip().upper()
        if base == "por" and country == "BR":
            return "por-BR"
        if base in _LANGUAGE_CODES:
            return base
    return None


def _requested_language_meta(language):
    """Resolve one requested language to a service code plus its hi flag."""
    if isinstance(language, str):
        code = language
        country = None
        hi = False
    elif isinstance(language, dict):
        # The service exposes no forced-only variant, so a forced-only
        # request is left unsatisfied instead of being answered with a
        # plain subtitle mislabelled as forced.
        if language.get("forced"):
            return None
        code = language.get("alpha3") or language.get("code") or language.get("alpha2")
        raw_country = language.get("country_alpha2") or language.get("country") or language.get("region")
        country = str(raw_country).upper() if raw_country else None
        hi = bool(language.get("hi"))
    else:
        return None
    code = _service_code(code)
    if not code:
        return None
    # Brazilian Portuguese is the one country the service distinguishes, so
    # a plain Portuguese request pinned to BR is that variant.
    if code == "por" and country == "BR":
        code = "por-BR"
    return {"code": code, "hi": hi}


def _requested_languages(languages):
    """The requested languages in service codes, deduplicated.

    The ``hi`` flag stays on the meta. The service does expose a
    hearing-impaired query filter, but the client exact-matches the
    record's own ``hearing_impaired`` flag instead, so the flag the record
    carries answers which requested variant a record can satisfy, and a
    record is never mislabelled.
    """
    requested = []
    seen = set()
    for language in languages or []:
        meta = _requested_language_meta(language)
        if not meta:
            continue
        key = (meta["code"], meta["hi"])
        if key in seen:
            continue
        seen.add(key)
        requested.append(meta)
    return requested


def _release_param(video):
    """The video's original filename, basename only, capped at 512 characters."""
    video = video or {}
    for key in ("original_name", "name", "original_path", "path"):
        value = _coerce_text(video.get(key))
        if not value:
            continue
        filename = str(value).replace("\\", "/").rsplit("/", 1)[-1].strip()
        if filename:
            return filename[:RELEASE_PARAM_MAX_CHARS]
    return None


def _valid_imdb_value(value):
    """The imdb id when it is a plain ``tt`` id, else None.

    The id becomes a path segment, so anything that is not a well-formed
    imdb id is treated as absent and the title search is used instead.
    """
    text = str(value or "").strip()
    return text if _IMDB_ID_RE.match(text) else None


def _search_request(video, code):
    """Return the (path, params) of the one search endpoint for a video.

    IMDb ids get the dedicated movie and episode endpoints; without one,
    the generic subtitle search runs on the title. The episode endpoint
    needs both numbers: a missing season or episode falls back to the
    generic search with whichever number the video carries.
    """
    video = video or {}
    kind = video.get("kind")
    if kind not in ("movie", "episode"):
        return None
    params = {"language": code, "per_page": PER_PAGE}
    release = _release_param(video)
    if release:
        params["release"] = release
    if kind == "movie":
        imdb_id = _valid_imdb_value(video.get("imdb_id"))
        if imdb_id:
            year = _safe_int(video.get("year"))
            if year is not None:
                params["year"] = year
            return f"/api/v1/movies/{urllib.parse.quote(imdb_id, safe='')}/subtitles", params
        title = _coerce_text(video.get("title"))
        if not title:
            return None
        params["title"] = title
        params["media_type"] = "movie"
        year = _safe_int(video.get("year"))
        if year is not None:
            params["year"] = year
        return "/api/v1/subtitles", params
    imdb_id = _valid_imdb_value(video.get("series_imdb_id"))
    season = _safe_int(video.get("season"))
    episode = _safe_int(video.get("episode"))
    if imdb_id and season is not None and season >= 0 and episode is not None and episode >= 0:
        path = f"/api/v1/series/{urllib.parse.quote(imdb_id, safe='')}/seasons/{season}/episodes/{episode}/subtitles"
        return path, params
    title = _coerce_text(video.get("series"))
    if not title:
        return None
    params["title"] = title
    params["media_type"] = "episode"
    if season is not None:
        params["season"] = season
    if episode is not None:
        params["episode"] = episode
    # No year: upstream sends none for episodes, and a series year could
    # empty out later seasons on a service that indexes per season.
    return "/api/v1/subtitles", params


def _valid_record(record, code):
    """True when a record satisfies the service's own response contract.

    Anything else is skipped silently: one bad row in a page must not sink
    the results around it. The links are checked against the record's own
    id, so a row carrying somebody else's paths cannot be downloaded.
    """
    if not isinstance(record, dict):
        return False
    record_id = record.get("id")
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id <= 0:
        return False
    media = record.get("media")
    if not isinstance(media, dict):
        return False
    title = media.get("title")
    if not isinstance(title, str) or not title.strip():
        return False
    language = record.get("language")
    if not isinstance(language, dict) or language.get("code") != code:
        return False
    releases = record.get("releases")
    if not isinstance(releases, list) or not all(isinstance(item, str) for item in releases):
        return False
    links = record.get("links")
    if not isinstance(links, dict):
        return False
    if links.get("page") != f"/subtitles/{record_id}":
        return False
    return links.get("content") == f"/api/v1/subtitles/{record_id}/content"


def _json_payload(body):
    """Decode a JSON response body into a dict, or fail loudly.

    The failure is a plain ValueError: one garbled 200 reply from a service
    that already passed the identity check is a failed call, and the
    identity path wraps it into a ConfigurationError where it belongs.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("SubsDump returned a malformed JSON response") from error
    if not isinstance(payload, dict):
        raise ValueError("SubsDump response is not a JSON object")
    return payload


def _parse_subtitles(body, code):
    """Parse a search response into the records valid for the requested code."""
    payload = _json_payload(body)
    # Only an explicit empty list is a legitimate empty search: a reply
    # without the field, or with it null, is broken and must be loud
    # instead of quietly reading as "no subtitles".
    if "subtitles" not in payload:
        raise ValueError("SubsDump search response carries no 'subtitles' field")
    subtitles = payload["subtitles"]
    if not isinstance(subtitles, list):
        raise ValueError("SubsDump search response 'subtitles' is not a list")
    return [record for record in subtitles if _valid_record(record, code)]


def _titles_match(video_text, record_title):
    """True when the record's title casefold-equals the video's title."""
    left = str(_coerce_text(video_text) or "").strip()
    right = str(_coerce_text(record_title) or "").strip()
    return bool(left) and left.casefold() == right.casefold()


def _names_other_season(video, record):
    """True when the record is episode-shaped for a season other than the video's.

    A record whose media carries a season cannot serve a video of another
    season even when the episode numbers coincide: a coinciding number must
    not carry home another season's subtitle as a candidate. A record with
    no season of its own (or a season pack that defers the episode pick)
    never conflicts, nor does a video without a season, nor a movie.
    """
    video = video or {}
    if video.get("kind") != "episode":
        return False
    season = _safe_int(video.get("season"))
    if season is None:
        return False
    media = record.get("media") or {}
    record_season = _safe_int(media.get("season"))
    if record_season is None:
        return False
    return record_season != season


def _derive_matches(video, record):
    """Compute the subliminal-shaped match list for one record.

    ``title``, ``series``, ``year``, ``season`` and ``episode`` come from the
    record's own media fields: a match is awarded only when the record
    actually carries the value and it equals the video's. Missing versus
    missing is not a match, and season 0 (specials) is a real season, so
    presence is tested against None, never truthiness.
    """
    video = video or {}
    media = record.get("media") or {}
    releases = record.get("releases") or []
    matches = []
    if video.get("kind") == "episode":
        if _titles_match(video.get("series"), media.get("title")):
            matches.append("series")
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        record_season = _safe_int(media.get("season"))
        record_episode = _safe_int(media.get("episode"))
        if season is not None and record_season is not None and record_season == season:
            matches.append("season")
        if (
            season is not None
            and episode is not None
            and record_episode is not None
            and record_episode == episode
        ):
            matches.append("episode")
    else:
        if _titles_match(video.get("title"), media.get("title")):
            matches.append("title")
        year = _safe_int(video.get("year"))
        record_year = _safe_int(media.get("year"))
        if year is not None and record_year is not None and record_year == year:
            matches.append("year")
    matches.extend(_release_attribute_matches(video, media.get("title"), releases))
    # One entry per key, in a stable order.
    return list(dict.fromkeys(matches))


def _compute_score(matches):
    """Heuristic score: a title or episode match is near-certain, else decent.

    The IMDb and episode endpoints already filtered by identity, and there
    is no hash lookup, so the score signals identity confidence only.
    """
    if "episode" in matches or "title" in matches:
        return 95
    return 80


def _language_block(code, hearing_impaired):
    """The result language: real alpha3 and country, never an invented code."""
    meta = _LANGUAGE_CODES[code]
    language = {
        "alpha3": "por" if code == "por-BR" else code,
        "alpha2": meta["alpha2"],
        "hi": hearing_impaired,
        "forced": False,
    }
    if meta.get("country"):
        language["country_alpha2"] = meta["country"]
    return language


def _result_filename(record_id, releases, alpha2):
    """Return the result filename from the record's first release name.

    The release source is capped like the payload copy, so a runaway
    release string cannot produce a runaway filename.
    """
    if releases:
        name = str(releases[0]).strip()[:PAYLOAD_RELEASE_MAX_CHARS]
        if name.lower().endswith(SUBTITLE_EXTENSIONS):
            return name
        return f"{name}.srt"
    return f"subsdump.{record_id}.{alpha2}.srt"


def _sniff_text(body, limit):
    """A lowercase text sample of the body, for sniffing only.

    The sample is what the format and HTML checks read; the body itself is
    always handed to the host unchanged. A UTF-16 body is decoded for the
    sniff alone: without the decode its interleaved NUL bytes hide every
    signature, and a UTF-16 subtitle reads as a bare srt.
    """
    sample = body[:limit]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return sample.decode("utf-16").lower()
        except UnicodeDecodeError:
            return ""
    return sample.lstrip(b"\xef\xbb\xbf \t\r\n").decode("utf-8", "replace").lower()


def _looks_like_html(body):
    """True when the bytes are an HTML page, judged structurally.

    After the byte-order mark and whitespace, any number of leading
    ``<!-- ... -->`` comments may precede the first markup, which must be a
    tag or doctype opening at the effective start. Literal ``<html>`` text
    inside a subtitle cue is not markup, so searching the whole sniff
    window for markup refused valid subtitles.
    """
    sample = _sniff_text(body, 512)
    while True:
        sample = sample.lstrip(" \t\r\n")
        if not sample.startswith("<!--"):
            return sample.startswith(("<!doctype", "<html", "<head", "<body"))
        end = sample.find("-->", 4)
        if end < 0:
            return False
        sample = sample[end + 3:]


def _is_rar_archive(body):
    return (body or b"").startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00"))


def _is_archive_body(body):
    """True when the bytes are a zip or rar archive, by magic alone."""
    if not body:
        return False
    if _is_rar_archive(body):
        return True
    return body.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def _normalize_line_endings(body):
    return (body or b"").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _subtitle_extension(name):
    lower_name = str(name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower_name.endswith(extension):
            return extension.lstrip(".")
    return None


def _detect_format(body, payload):
    """The subtitle's real format, read from the bytes before any filename.

    The content endpoint has no extension of its own, so the body is
    sniffed first; only a body that carries no known subtitle signature
    falls back to the extension of a release name. ``[Script Info]`` opens
    both SSA and ASS files: the styles section decides between them, and a
    ``.ssa`` release name settles it when the section sits outside the
    window. A UTF-16 body is decoded for the sniff alone, so a UTF-16
    subtitle reports its true format instead of a bare srt.
    """
    sample = _sniff_text(body, 4096)
    if sample.startswith("webvtt"):
        return "vtt"
    if "[script info]" in sample:
        if "[v4+ styles]" in sample:
            return "ass"
        if "[v4 styles]" in sample:
            return "ssa"
        if any(
            isinstance(release, str) and release.lower().endswith(".ssa")
            for release in (payload or {}).get("releases") or []
        ):
            return "ssa"
        return "ass"
    if "-->" in sample:
        return "srt"
    if _MICRODVD_RE.match(sample):
        return "sub"
    for release in (payload or {}).get("releases") or []:
        if isinstance(release, str):
            extension = _subtitle_extension(release)
            if extension:
                return extension
    return "srt"


def _content_type(subtitle_format):
    if subtitle_format in {"ass", "ssa"}:
        return "text/x-ssa"
    if subtitle_format == "vtt":
        return "text/vtt"
    if subtitle_format == "sub":
        return "text/plain"
    return "application/x-subrip"


def _content_payload(body, subtitle_format):
    # No encoding guess: the host runs chardet via Subtitle.normalize().
    return {
        "content_b64": base64.b64encode(body).decode("ascii"),
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "content_type": _content_type(subtitle_format),
        "format": subtitle_format,
        "empty": False,
    }


def _valid_content_path(path):
    return isinstance(path, str) and bool(_CONTENT_PATH_RE.fullmatch(path))


def _result(video, record, code, base_url):
    """Build one search candidate from a validated record."""
    record_id = record["id"]
    media = record["media"]
    releases = record["releases"]
    # The record carries the real hearing-impaired flag, so the candidate
    # reports it verbatim and the flag is verifiable.
    record_hi = bool(record.get("hearing_impaired"))
    matches = _derive_matches(video, record)
    score = _compute_score(matches)
    language = _language_block(code, record_hi)
    links = record["links"]
    payload = {
        "provider": PROVIDER_ID,
        "schema": 1,
        "subtitle_id": record_id,
        "content_path": links["content"],
        "page_path": links["page"],
        # The payload crosses the worker boundary and is persisted, so a
        # service reply with runaway release strings is capped here; the
        # result's own release_info keeps the full list.
        "releases": [str(release)[:PAYLOAD_RELEASE_MAX_CHARS] for release in releases],
        "language_code": code,
    }
    if (video or {}).get("kind") == "episode":
        # The requested episode, so the host can pick the archive member for
        # it even when the record covers a whole season.
        season = _safe_int(video.get("season"))
        episode = _safe_int(video.get("episode"))
        if season is not None:
            payload["season"] = season
        if episode is not None:
            payload["episode"] = episode
    return {
        "provider": PROVIDER_ID,
        "id": f"{PROVIDER_ID}-{record_id}-{code}",
        "language": language,
        "release_info": "\n".join(releases) or media["title"],
        "filename": _result_filename(record_id, releases, language["alpha2"]),
        "matches": matches,
        "score": score,
        "score_without_hash": score,
        "score_out_of": 100,
        "hash_verifiable": False,
        "hearing_impaired_verifiable": True,
        "hearing_impaired": record_hi,
        "page_link": base_url + links["page"],
        "display": {
            "source": PROVIDER_ID,
            "title": media["title"],
            "uploader": record.get("uploader"),
            "language_code": code,
        },
        "provider_payload": payload,
    }


class SubsDumpProvider:
    def __init__(self):
        # The (base_url, api_key) pair whose /api/v1/info already verified as
        # a SubsDump v1 service, so the check runs once per config per worker.
        self._verified = None

    def search(self, video, languages, config):
        base_url = _base_url(config)
        api_key = _api_key(config)
        requested = _requested_languages(languages)
        if not requested:
            return []
        video = dict(video or {})
        if video.get("kind") not in ("movie", "episode"):
            return []
        self._ensure_service(base_url, api_key)

        results = []
        seen = set()
        # One HTTP GET per search endpoint per language: the service codes
        # are the query parameter values, so one request covers every hi
        # variant of the same code.
        for code in dict.fromkeys(meta["code"] for meta in requested):
            request = _search_request(video, code)
            if request is None:
                continue
            path, params = request
            body = self._http_get(
                f"{base_url}{path}?{urllib.parse.urlencode(params)}", api_key=api_key
            )
            for record in _parse_subtitles(body, code):
                # A record that names another season cannot serve this
                # video, whatever episode number it carries.
                if _names_other_season(video, record):
                    continue
                # The candidate's hi reflects the record, never the request.
                record_hi = bool(record.get("hearing_impaired"))
                if not any(meta["code"] == code and meta["hi"] == record_hi for meta in requested):
                    continue
                key = (record["id"], code)
                if key in seen:
                    continue
                seen.add(key)
                results.append(_result(video, record, code, base_url))
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def download(self, provider_payload, language, config):
        del language  # the content path is per subtitle id, not per language
        base_url = _base_url(config)
        api_key = _api_key(config)
        payload = dict(provider_payload or {})
        if payload.get("provider") not in (None, PROVIDER_ID):
            raise ValueError("SubsDump download payload belongs to another provider")
        content_path = payload.get("content_path")
        # The URL is built from the validated content path alone, so a
        # tampered payload cannot point the worker at a foreign host.
        if not _valid_content_path(content_path):
            raise ValueError("SubsDump download requires a valid content path")
        self._ensure_service(base_url, api_key)
        body = self._http_get(
            f"{base_url}{content_path}",
            api_key=api_key,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            max_bytes=MAX_DOWNLOAD_BYTES,
        )
        if not body or not body.strip():
            raise ValueError(f"SubsDump returned an empty download for subtitle {payload.get('subtitle_id')}")
        # Archive magic is sniffed before the HTML check: an archive whose
        # early bytes carry markup-like text is an archive, not an HTML page.
        if _is_archive_body(body):
            # Archive mode: the Bazarr+ host lists the archive, picks the
            # member by episode, and detects the encoding itself. The worker
            # imports no extraction library and sets no encoding.
            archive = {
                "archive_b64": base64.b64encode(body).decode("ascii"),
                "archive_sha256": hashlib.sha256(body).hexdigest(),
                "episode": _safe_int(payload.get("episode")),
            }
            return archive
        if _looks_like_html(body):
            raise ValueError(
                f"SubsDump returned an HTML page instead of a subtitle for subtitle {payload.get('subtitle_id')}"
            )
        # Byte-level newline normalization rewrites the characters of a
        # UTF-16 stream (it splits character pairs), so a body with NUL
        # bytes is not text-normalized and is handed to the host untouched,
        # where chardet and normalize do the real decoding.
        if b"\x00" not in body:
            body = _normalize_line_endings(body)
        return _content_payload(body, _detect_format(body, payload))

    def _ensure_service(self, base_url, api_key):
        """Verify the base URL serves the SubsDump v1 API, once per config.

        A Bazarr+ install points this provider at its own instance, so a
        typo in the base URL must surface as a configuration error, not as
        an empty result set or a confusing downstream parse failure.
        """
        key = (base_url, api_key)
        if self._verified == key:
            return
        info_body = self._http_get(f"{base_url}/api/v1/info", api_key=api_key)
        try:
            payload = _json_payload(info_body)
        except ValueError as error:
            # Only the JSON failure becomes a configuration error here: an
            # auth or availability failure on the info endpoint keeps its
            # own type.
            raise ConfigurationError(
                f"The configured SubsDump base_url did not answer /api/v1/info "
                f"with usable JSON: {error}"
            ) from error
        name = payload.get("name")
        api_version = payload.get("api_version")
        if name != "SubsDump" or api_version != "v1":
            raise ConfigurationError(
                "The configured SubsDump base_url does not point at a SubsDump v1 "
                f"service: /api/v1/info reported name {name!r} and api_version {api_version!r}"
            )
        self._verified = key

    def _http_get(self, url, api_key=None, timeout=HTTP_TIMEOUT_SECONDS, max_bytes=MAX_SEARCH_BYTES):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, */*",
        }
        if api_key:
            headers["X-API-Key"] = api_key
        request = urllib.request.Request(url, headers=headers)
        # Network and HTTP failures are loud, never an empty result: the
        # scheduler must see the difference between "no subtitles" and
        # "service broken". Redirects are never followed: the Location
        # header of a 3xx names a host of its own, and that host has no
        # business receiving the API key. The body is bounded so a runaway
        # response cannot buffer unbounded memory in the worker.
        try:
            with _OPENER.open(request, timeout=timeout) as response:
                status = response.getcode()
                if status != 200:
                    raise RuntimeError(f"SubsDump request failed with status {status}")
                body = response.read(max_bytes + 1)
        except urllib.error.HTTPError as error:
            # The error holds the response body open, so it is closed before
            # the failure is raised on.
            try:
                if error.code in (401, 403):
                    raise AuthenticationError(
                        f"SubsDump rejected the API key (HTTP {error.code})"
                    ) from error
                if error.code == 503:
                    raise ServiceUnavailable(
                        f"SubsDump service is not ready (HTTP {error.code})"
                    ) from error
                raise RuntimeError(
                    f"SubsDump request failed with status {error.code}"
                ) from error
            finally:
                error.close()
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                # A TLS failure says the URL or its scheme is wrong for this
                # host, which reads as a settings problem.
                raise ConfigurationError(f"SubsDump request failed (TLS): {error.reason}") from error
            raise RuntimeError(f"SubsDump request failed: {error.reason}") from error
        if len(body) > max_bytes:
            raise ValueError(f"SubsDump response from {url} exceeds {max_bytes} bytes")
        return body

