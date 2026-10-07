"""Vladoon provider for the Bazarr+ Provider Hub catalog.

Vladoon is a Bulgarian subtitle site with a small JSON search API and ZIP
downloads. The service announced itself as ``vladoon.mooo.com`` when the
upstream provider was written; every request there is now redirected to the
canonical ``vladoon.com`` host, so this provider talks to the canonical
domain directly and skips the redirect hop.
"""

import base64
import hashlib
import io
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile


PROVIDER_ID = "vladoon"
BASE_URL = "https://vladoon.com/subs"
SEARCH_URL = BASE_URL + "/search-subtitles"
DOWNLOAD_URL = BASE_URL + "/download/{item_id}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 BazarrProviderHub"
)
HTTP_TIMEOUT_SECONDS = 20
DOWNLOAD_TIMEOUT_SECONDS = 30
# A search response is a small JSON document; a download is a zip the host
# caps at 32 MiB, so a body above that cap could never be accepted.
MAX_SEARCH_BYTES = 4 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
# The host rejects archives with more members than this guard, so a download
# above it could never be accepted either.
MAX_ARCHIVE_MEMBERS = 5000
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
LANGUAGE_ALPHA3 = "bul"
LANGUAGE_ALPHA2 = "bg"
# The site serves Bulgarian subtitles only and its API carries no
# hearing-impaired or forced flag, so the result language is fixed.
LANGUAGE = {
    "alpha3": LANGUAGE_ALPHA3,
    "alpha2": LANGUAGE_ALPHA2,
    "hi": False,
    "forced": False,
}

_EPISODE_TOKEN_RE = re.compile(r"\b[Ss](\d{1,2})[Ee](\d{1,2})\b")
_EPISODE_ALT_RE = re.compile(r"\b(\d{1,2})x(\d{1,2})\b")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)


def _coerce_text(value):
    """Collapse a video-metadata value to a single string.

    Subliminal occasionally serialises multi-value fields (notably
    ``audio_codec`` and ``source``) as a Python ``list`` inside the worker
    payload. Passing a list straight into ``dict.get`` or ``str.lower``
    raises ``TypeError: unhashable type: 'list'`` and crashes search.
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
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM_RE.sub(" ", folded.lower()).strip()


def _normalize_tokens(text):
    return [token for token in _normalize(_coerce_text(text)).split(" ") if token]


def _release_tokens(text):
    if not text:
        return set()
    # Treat any non-alphanumeric run as a separator. Lowercased for matching.
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


# Release-name match tables. Keys are the values bazarr/subliminal exposes on
# the Video object; the inner list is the set of synonymous tokens searched
# inside a release name. Matching is case-insensitive on tokenized text.
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


_ITEM_ID_RE = re.compile(r"^[0-9]+$")


def _valid_item_id(value):
    """True for a positive decimal item id, the only shape the site emits.

    The download URL is built from the id, so anything beyond plain digits
    is rejected rather than percent-encoded into the path.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(_ITEM_ID_RE.match(stripped)) and stripped.strip("0") != ""


def _member_basename(name):
    """Return an archive member's basename.

    Zip members use forward slashes; some producers emit backslashes.
    """
    return str(name).replace("\\", "/").rsplit("/", 1)[-1]


def _is_subtitle_member(name):
    """True when an archive member is a subtitle file worth extracting.

    Mirrors the built-in rule: directories and dot-prefixed members
    (``.DS_Store``, the AppleDouble ``._name.srt`` resource forks macOS zip
    adds) are not subtitles, and only the basename's extension decides.
    """
    base = _member_basename(name)
    if not base or base.startswith("."):
        return False
    return os.path.splitext(base)[1].lower() in SUBTITLE_EXTENSIONS


def _is_forced_member(name):
    """True when an archive member is tagged as a forced subtitle.

    Mirrors the built-in rule, boundary included: only the lowercased
    basename's final segment before the extension counts, so
    ``show.s01e01.forced.srt`` is forced while ``show.forced.1080p.srt``
    is not, and ``nonforced`` as the final segment is skipped too. Forced
    members are only for forced requests, and this provider never
    requests forced.
    """
    base = _member_basename(name).lower()
    return os.path.splitext(base)[0].endswith("forced")


def _episode_tuple(text):
    """Return ``(season, episode)`` from a release name, else ``None``."""
    if not text:
        return None
    match = _EPISODE_TOKEN_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = _EPISODE_ALT_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _episode_token(video):
    """Return the ``SxxExx`` search token for an episode video."""
    season = (video or {}).get("season")
    episode = (video or {}).get("episode")
    if season is None or episode is None:
        return None
    try:
        return "S%02dE%02d" % (int(season), int(episode))
    except (TypeError, ValueError):
        return None


def _search_query(video):
    """Return the single query string the site is asked to search."""
    video = video or {}
    if video.get("kind") == "episode":
        series = (_coerce_text(video.get("series")) or "").strip()
        token = _episode_token(video)
        if not series or not token:
            return None
        return f"{series} {token}"
    if video.get("kind") == "movie":
        title = (_coerce_text(video.get("title")) or "").strip()
        return title or None
    return None


def _release_names(item):
    releases = item.get("release_names") or []
    if isinstance(releases, str):
        return [releases]
    return [name for name in releases if isinstance(name, str) and name]


def _item_title(item):
    return item.get("original_title") or item.get("title") or ""


def _titles_match(video_text, item):
    """True when the item's title equals the video's title.

    The item's title fields are clean production titles, not release names,
    so the match is exact on the normalized token set (the same semantics
    upstream's guessit title matching applies). A ``Dune`` video must not
    title-match ``Dune: Part Two``; the IMDb id is the discriminator
    between same-title productions.
    """
    video_tokens = set(_normalize_tokens(video_text))
    if not video_tokens:
        return False
    for candidate in (item.get("original_title"), item.get("title")):
        candidate_tokens = set(_normalize_tokens(candidate))
        if candidate_tokens and candidate_tokens == video_tokens:
            return True
    return False


def _matches_movie(item, video):
    """True when a movie result item belongs to the requested movie.

    The site answers a title search with every production carrying that
    title (a ``Dune`` search returns the 2021, 1984 and sequel entries), so
    the IMDb id is the discriminator whenever both sides carry one.
    """
    if (video or {}).get("kind") != "movie":
        return True
    item_type = item.get("type")
    if item_type and item_type != "movie":
        return False
    video_imdb = (video or {}).get("imdb_id")
    item_imdb = item.get("imdb_id")
    if video_imdb and item_imdb:
        return video_imdb == item_imdb
    return True


def _matches_episode(item, video):
    """True when a tv result item covers the requested episode.

    An item covers the episode when its own season/episode fields name it,
    when one of its release names carries the episode token, or when the
    item is a season pack of the requested season.
    """
    if (video or {}).get("kind") != "episode":
        return True
    item_type = item.get("type")
    if item_type and item_type != "tv":
        return False
    series_imdb = (video or {}).get("series_imdb_id")
    item_imdb = item.get("imdb_id")
    if series_imdb and item_imdb and series_imdb != item_imdb:
        return False

    season = (video or {}).get("season")
    episode = (video or {}).get("episode")
    item_season = item.get("season")
    item_episode = item.get("episode")
    if item_season == season and item_episode == episode:
        return True
    for release in _release_names(item):
        if _episode_tuple(release) == (season, episode):
            return True
    if item.get("episode_type") == "season" and item_season == season:
        return True
    return False


def _parse_search_results(body):
    """Parse a search response body into a list of result item dicts.

    Items that are not dicts, or that carry no usable id, are dropped: the
    download URL is built from the id, so an item without one cannot
    produce a usable subtitle.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("Vladoon returned an unreadable search response") from error
    if not isinstance(payload, dict):
        raise ValueError("Vladoon search response is not a JSON object")
    results = payload.get("results")
    if results is None:
        return []
    if not isinstance(results, list):
        raise ValueError("Vladoon search results is not a list")
    # Items that are not dicts, or whose id is not a positive decimal
    # integer, are dropped: the download URL is built from the id, so an
    # item without a plain id cannot produce a safe, usable download.
    return [item for item in results if isinstance(item, dict) and _valid_item_id(item.get("id"))]


def _requested_language(languages):
    """Return the fixed result language when plain Bulgarian is requested.

    The site carries no hearing-impaired or forced variants, so a request
    for one of those variants is not satisfied by a plain result and gets
    no results rather than a mislabelled one.
    """
    for language in languages or []:
        if not isinstance(language, dict):
            continue
        if language.get("hi") or language.get("forced"):
            continue
        alpha3 = language.get("alpha3")
        alpha2 = language.get("alpha2")
        if alpha3 == LANGUAGE_ALPHA3 or alpha2 == LANGUAGE_ALPHA2:
            return dict(LANGUAGE)
    return None


def _derive_matches(video, item):
    """Compute the subliminal-shaped match list for one result item.

    Keys feed bazarr's downstream score calculation
    (``custom_libs/subliminal_patch/score.py``). Each key is added only when
    the item actually carries the matching signal.
    """
    video = video or {}
    matches = []
    kind = video.get("kind")
    releases = _release_names(item)

    if kind == "movie":
        if _titles_match(video.get("title"), item):
            matches.append("title")
        year = video.get("year")
        if year and any(str(year) in _release_tokens(release) for release in releases):
            matches.append("year")
        if video.get("imdb_id") and item.get("imdb_id") == video.get("imdb_id"):
            matches.append("imdb_id")
    elif kind == "episode":
        if _titles_match(video.get("series"), item):
            matches.append("series")
        try:
            season = int(video.get("season"))
            episode = int(video.get("episode"))
        except (TypeError, ValueError):
            season = episode = None
        if season is not None:
            for release in releases:
                tag = _episode_tuple(release)
                if tag is not None and tag[0] == season:
                    matches.append("season")
                    break
            if item.get("season") == season:
                matches.append("season")
        if season is not None and episode is not None:
            for release in releases:
                if _episode_tuple(release) == (season, episode):
                    matches.append("episode")
                    break
            if item.get("season") == season and item.get("episode") == episode:
                matches.append("episode")
        if video.get("series_imdb_id") and item.get("imdb_id") == video.get("series_imdb_id"):
            # The item's id is the series-level identity: emit the
            # series_imdb_id key, which the score contract credits with
            # series and year, not imdb_id, which claims season and
            # episode equivalence this comparison never established.
            matches.append("series_imdb_id")
        year = video.get("year")
        if year and any(str(year) in _release_tokens(release) for release in releases):
            matches.append("year")

    matches.extend(_release_attribute_matches(video, item, releases))

    # One entry per key, in a stable order.
    return list(dict.fromkeys(matches))


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
    segment and without the item's title tokens.
    """
    text = _without_release_group(release)
    return {token for token in _release_tokens(text) if token not in title_tokens}


def _release_attribute_matches(video, item, releases):
    """Release-level match keys, evaluated per release name.

    The upstream provider evaluates each release name independently and
    unions the resulting match keys. Pooling every release's tokens into
    one set would manufacture matches: a video release group ``FOO-BAR``
    must not match one release ending ``-FOO`` and another ending
    ``-BAR``. The keys are returned in a fixed order.
    """
    matched = set()
    title_tokens = set(_normalize_tokens(_item_title(item)))
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


def _compute_score(video, matches):
    """Heuristic score in [60, 100] from the derived match list.

    - 100: movie with a matching IMDb id, or title and year both matching.
    - 95: episode item naming the season and episode.
    - 90: movie title match, or an episode item covering the season.
    - 85: episode series match only.
    - 60: nothing beyond the site's own search relevance.
    """
    kind = (video or {}).get("kind")
    if kind == "movie":
        if "imdb_id" in matches or ("title" in matches and "year" in matches):
            return 100
        if "title" in matches:
            return 90
        return 60
    if kind == "episode":
        if "episode" in matches:
            return 95
        if "season" in matches:
            return 90
        if "series" in matches:
            return 85
        return 60
    return 60


def _result_filename(item):
    """Return the result filename from the item's first release name."""
    releases = _release_names(item)
    if releases:
        name = releases[0].strip()
        if name.lower().endswith(SUBTITLE_EXTENSIONS):
            return name
        return f"{name}.srt"
    return f"vladoon.{item.get('id')}.{LANGUAGE_ALPHA2}.srt"


def _sleep(config):
    delay = (config or {}).get("request_delay_ms")
    if not delay:
        return
    time.sleep(min(float(delay), 5000.0) / 1000.0)


class VladoonProvider:
    def search(self, video, languages, config):
        video = video or {}
        if _requested_language(languages) is None:
            return []
        query = _search_query(video)
        if not query:
            return []

        url = SEARCH_URL + "?" + urllib.parse.urlencode({"q": query})
        _sleep(config)
        body = self._http_get(url, timeout=HTTP_TIMEOUT_SECONDS)
        items = _parse_search_results(body)

        results = []
        for item in items:
            if not _matches_movie(item, video):
                continue
            if not _matches_episode(item, video):
                continue
            item_id = str(item.get("id"))
            download_url = DOWNLOAD_URL.format(item_id=item_id)
            releases = _release_names(item)
            release_info = "\n".join(releases) or _item_title(item)
            matches = _derive_matches(video, item)
            score = _compute_score(video, matches)
            # The payload carries the requested episode, not the item's: an
            # item can be a season pack covering the episode, and the
            # download must still pick the member for the requested episode.
            payload = {
                "provider": PROVIDER_ID,
                "schema": 1,
                "item_id": item_id,
                "kind": video.get("kind"),
            }
            if video.get("kind") == "episode":
                payload["season"] = video.get("season")
                payload["episode"] = video.get("episode")
            results.append(
                {
                    "provider": PROVIDER_ID,
                    "id": f"{PROVIDER_ID}-{item_id}",
                    "language": dict(LANGUAGE),
                    "release_info": release_info,
                    "filename": _result_filename(item),
                    "matches": matches,
                    "score": score,
                    "score_without_hash": score,
                    "score_out_of": 100,
                    "hash_verifiable": False,
                    "hearing_impaired_verifiable": False,
                    "hearing_impaired": False,
                    "page_link": download_url,
                    "display": {
                        "source": PROVIDER_ID,
                        "title": _item_title(item),
                        "uploader": item.get("uploaded_by"),
                        "download_url": download_url,
                    },
                    "provider_payload": payload,
                }
            )
        return results

    def download(self, provider_payload, language, config):
        del language  # the site serves one language only
        payload = dict(provider_payload or {})
        if payload.get("provider") not in (None, PROVIDER_ID):
            raise ValueError("Vladoon download payload belongs to another provider")
        item_id = payload.get("item_id")
        if not _valid_item_id(item_id):
            raise ValueError("Vladoon download requires a numeric item_id")
        # The URL is built from the item id alone so a tampered payload
        # cannot point the worker at a foreign host.
        url = DOWNLOAD_URL.format(item_id=str(item_id).strip())

        _sleep(config)
        body = self._http_get(
            url, timeout=DOWNLOAD_TIMEOUT_SECONDS, max_bytes=MAX_DOWNLOAD_BYTES
        )
        if not zipfile.is_zipfile(io.BytesIO(body)):
            raise ValueError(f"Vladoon download {url} is not an archive")

        archive = {
            "archive_b64": base64.b64encode(body).decode("ascii"),
            "archive_sha256": hashlib.sha256(body).hexdigest(),
        }
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            member_names = bundle.namelist()
            if len(member_names) > MAX_ARCHIVE_MEMBERS:
                # Mirror the host's member-count guard: an archive above it
                # would be rejected host-side anyway, so fail early with a
                # clear error instead of building a payload for nothing.
                raise ValueError(
                    f"Vladoon download {url} carries too many members"
                )
            names = [name for name in member_names if _is_subtitle_member(name)]
        if not names:
            raise ValueError(f"Vladoon download {url} carries no subtitle member")

        if payload.get("kind") == "episode":
            season = payload.get("season")
            episode = payload.get("episode")
            try:
                wanted = (int(season), int(episode))
            except (TypeError, ValueError):
                wanted = None
            # Forced-tagged members are only for forced requests, and this
            # provider never requests forced, so a normal episode request
            # must not receive them. Upstream's multi-member path filters
            # them, but its single-member shortcut (and the host's copy of
            # it) would still serve a forced-only archive, so the worker
            # rejects a forced-only archive itself.
            eligible = [name for name in names if not _is_forced_member(name)]
            if not eligible:
                raise ValueError(
                    f"Vladoon download {url} carries no non-forced subtitle member"
                )
            member = None
            if wanted is not None:
                for name in eligible:
                    # Only the member's own basename names its episode: a
                    # pack directory like ``S01E01-E07`` is a range marker
                    # for the whole archive, not the member's episode. The
                    # host reads a pinned member as-is, so the forced
                    # filtering above has to happen before any pin.
                    if _episode_tuple(_member_basename(name)) == wanted:
                        member = name
                        break
            if member is not None:
                archive["member"] = member
            else:
                # No eligible member names the episode with a searchable
                # token. Hand the archive to the host with the episode
                # number so its generic episode pick, which carries the
                # built-in matching rules, can pick looser member names.
                if wanted is not None and wanted[0] >= 0:
                    archive["season"] = wanted[0]
                if wanted is not None and wanted[1] >= 0:
                    archive["episode"] = wanted[1]
            return archive

        # A movie archive carries one member per release variant; the first
        # subtitle member in archive order is the pick, mirroring upstream's
        # first-subtitle rule.
        archive["member"] = names[0]
        return archive

    def _http_get(self, url, timeout=HTTP_TIMEOUT_SECONDS, max_bytes=MAX_SEARCH_BYTES):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, application/zip, */*",
                "Referer": BASE_URL + "/",
            },
        )
        # Let urllib errors surface: a network failure is not an empty
        # result and the scheduler must see the difference. The body is
        # bounded so a runaway response cannot buffer unbounded memory in
        # the worker (a download above the host's archive cap could never
        # be accepted anyway).
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"Vladoon response from {url} exceeds {max_bytes} bytes")
        return body
