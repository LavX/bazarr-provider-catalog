"""OpenSubtitles.com provider for the Bazarr+ Provider Hub catalog."""

import base64
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

PROVIDER_ID = "opensubtitlescom"
DEFAULT_HOST = "api.opensubtitles.com"
USER_AGENT = "BazarrProviderHub/1.0"
HTTP_TIMEOUT_SECONDS = 30
TOKEN_TTL_SECONDS = 12 * 60 * 60
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

ALPHA3_TO_API = {
    "abk": "ab",
    "afr": "af",
    "amh": "am",
    "ara": "ar",
    "arg": "an",
    "asm": "as",
    "aze": "az-az",
    "hye": "hy",
    "ast": "at",
    "eus": "eu",
    "bel": "be",
    "ben": "bn",
    "bos": "bs",
    "bre": "br",
    "bul": "bg",
    "mya": "my",
    "cat": "ca",
    "zho": "zh-CN",
    "ces": "cs",
    "cym": "cy",
    "dan": "da",
    "nld": "nl",
    "eng": "en",
    "epo": "eo",
    "est": "et",
    "fin": "fi",
    "fra": "fr",
    "gla": "gd",
    "gle": "ga",
    "kat": "ka",
    "deu": "de",
    "glg": "gl",
    "ell": "el",
    "heb": "he",
    "hin": "hi",
    "hrv": "hr",
    "hun": "hu",
    "ibo": "ig",
    "ina": "ia",
    "isl": "is",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kan": "kn",
    "kaz": "kk",
    "khm": "km",
    "kor": "ko",
    "kur": "ku",
    "lav": "lv",
    "lit": "lt",
    "ltz": "lb",
    "mkd": "mk",
    "mal": "ml",
    "mar": "mr",
    "msa": "ms",
    "mni": "ma",
    "mon": "mn",
    "nav": "nv",
    "nep": "ne",
    "nor": "no",
    "oci": "oc",
    "ori": "or",
    "fas": "fa",
    "pol": "pl",
    "por": "pt-PT",
    "pus": "ps",
    "rus": "ru",
    "sat": "sx",
    "srp": "sr",
    "snd": "sd",
    "sin": "si",
    "slk": "sk",
    "slv": "sl",
    "sme": "se",
    "som": "so",
    "spa": "es",
    "sqi": "sq",
    "swa": "sw",
    "swe": "sv",
    "syr": "sy",
    "tam": "ta",
    "tat": "tt",
    "tel": "te",
    "tet": "tm-td",
    "tgl": "tl",
    "tha": "th",
    "tuk": "tk",
    "tur": "tr",
    "ukr": "uk",
    "urd": "ur",
    "uzb": "uz",
    "vie": "vi",
    "ron": "ro",
}
API_TO_ALPHA3 = {value.lower(): key for key, value in ALPHA3_TO_API.items()}
API_TO_ALPHA3.update({"ea": "spa", "me": "srp", "pt-br": "por", "pt-pt": "por", "zh-cn": "zho", "zh-tw": "zho", "ze": "zho"})
NON_ALNUM_RE = re.compile(r"[\W_]+")


class RateLimited(RuntimeError):
    pass


class DownloadLimitExceeded(RuntimeError):
    pass


class AuthenticationRequired(ValueError):
    pass


def sanitize_external_id(external_id):
    if external_id is None:
        return None
    value = str(external_id).strip().lower()
    if value.startswith("tt"):
        value = value[2:]
    value = value.lstrip("0") or "0"
    return int(value)


def api_language_code(language):
    if not language:
        return None
    alpha3 = str(language.get("alpha3") if isinstance(language, dict) else language or "").strip()
    alpha2 = language.get("alpha2") if isinstance(language, dict) else None
    country = (language.get("country") if isinstance(language, dict) else None) or ""
    if not country and isinstance(language, dict):
        country = language.get("country_alpha2") or ""
    if not country and "-" in alpha3:
        alpha3, country = alpha3.split("-", 1)
    alpha3 = alpha3.lower()
    country = str(country).upper()
    if alpha3 == "por" and country.upper() == "BR":
        return "pt-BR"
    if alpha3 == "spa" and country.upper() == "MX":
        return "ea"
    if alpha3 == "srp" and country.upper() == "ME":
        return "me"
    if alpha3 == "zho":
        if country.upper() == "TW":
            return "zh-TW"
        return "zh-CN"
    if alpha2 and not alpha3:
        return str(alpha2).lower()
    return ALPHA3_TO_API.get(alpha3)


def language_payload_from_api_code(api_code, hearing_impaired=False, forced=False):
    code = str(api_code or "").strip()
    lower = code.lower()
    alpha3 = API_TO_ALPHA3.get(lower, lower)
    payload = {
        "alpha3": alpha3,
        "alpha2": _alpha2_for_alpha3(alpha3, lower),
        "hi": bool(hearing_impaired),
        "forced": bool(forced),
    }
    if lower == "ea":
        payload["country"] = "MX"
    elif lower == "pt-br":
        payload["country"] = "BR"
    elif lower == "me":
        payload["country"] = "ME"
    elif lower == "zh-cn":
        payload["country"] = "CN"
    elif lower == "zh-tw":
        payload["country"] = "TW"
    return payload


def _alpha2_for_alpha3(alpha3, api_code=None):
    if api_code and len(api_code) == 2:
        return api_code
    for key, value in ALPHA3_TO_API.items():
        if key == alpha3 and len(value) == 2:
            return value
    if alpha3 == "por":
        return "pt"
    if alpha3 == "zho":
        return "zh"
    if alpha3 == "spa":
        return "es"
    return None


def _requested_language_codes(languages):
    codes = []
    for language in languages or []:
        code = api_language_code(language)
        if code and code not in codes:
            codes.append(code)
    return sorted(codes, key=lambda item: item.lower())


def _requested_language_variants(languages):
    variants = {}
    for language in languages or []:
        code = api_language_code(language)
        if not code:
            continue
        hi = bool((language or {}).get("hi")) if isinstance(language, dict) else False
        forced = bool((language or {}).get("forced")) if isinstance(language, dict) else False
        variants.setdefault(code.lower(), set()).add((hi, forced))
    return variants


def _all_requested(languages, flag):
    values = [bool((item or {}).get(flag)) for item in languages or [] if isinstance(item, dict)]
    return bool(values) and all(values)


def _any_requested(languages, flag):
    return any(bool((item or {}).get(flag)) for item in languages or [] if isinstance(item, dict))


def is_real_forced(attributes):
    return bool((attributes or {}).get("foreign_parts_only")) and not bool((attributes or {}).get("hearing_impaired"))


class OpenSubtitlesComProvider:
    def __init__(self):
        self.token = None
        self.base_host = DEFAULT_HOST
        self.token_started = 0

    def search(self, video, languages, config):
        config = dict(config or {})
        self._require_config(config)
        language_codes = _requested_language_codes(languages)
        if not language_codes:
            return []
        self._ensure_login(config)
        video = video or {}
        params = self._build_search_params(video, languages, language_codes, config)
        if not params:
            return []
        result = self._with_auth_retry(
            lambda: self._http_get_json(
                self._api_url("subtitles"),
                params,
                self._search_headers(config),
                timeout=HTTP_TIMEOUT_SECONDS,
            ),
            config,
        )
        if not (result.get("data") or []) and any(key == "moviehash" for key, _value in params):
            params = [(key, value) for key, value in params if key != "moviehash"]
            result = self._with_auth_retry(
                lambda: self._http_get_json(
                    self._api_url("subtitles"),
                    params,
                    self._search_headers(config),
                    timeout=HTTP_TIMEOUT_SECONDS,
                ),
                config,
            )
        return self._results_from_response(video, languages, result, config)

    def _build_search_params(self, video, languages, language_codes, config):
        params = [("languages", ",".join(language_codes))]
        moviehash = _moviehash(video) if _bool_config(config, "use_hash", True) else None
        if moviehash:
            params.append(("moviehash", moviehash))
        episode_number = _int_or_none(video.get("episode"))
        season_number = _int_or_none(video.get("season"))
        imdb_id = _imdb_id(video.get("imdb_id"))
        if video.get("kind") == "episode":
            series_imdb_id = _imdb_id(video.get("series_imdb_id"))
            if imdb_id and not series_imdb_id:
                params.append(("imdb_id", imdb_id))
            elif series_imdb_id:
                params.append(("parent_imdb_id", series_imdb_id))
                if episode_number is not None:
                    params.append(("episode_number", episode_number))
                if season_number is not None:
                    params.append(("season_number", season_number))
            elif moviehash:
                if episode_number is not None:
                    params.append(("episode_number", episode_number))
                if season_number is not None:
                    params.append(("season_number", season_number))
            else:
                title_id = self._search_title_id(video, config, episode=True)
                if title_id is None:
                    return []
                params.append(("parent_feature_id", title_id))
                if episode_number is not None:
                    params.append(("episode_number", episode_number))
                if season_number is not None:
                    params.append(("season_number", season_number))
        elif video.get("kind") == "movie":
            if imdb_id:
                params.append(("imdb_id", imdb_id))
            elif moviehash:
                pass
            else:
                title_id = self._search_title_id(video, config, episode=False)
                if title_id is None:
                    return []
                params.append(("id", title_id))
        else:
            return []
        if not _bool_config(config, "include_ai_translated", False):
            params.append(("ai_translated", "exclude"))
        if _bool_config(config, "include_machine_translated", False):
            params.append(("machine_translated", "include"))
        if _all_requested(languages, "hi"):
            params.append(("hearing_impaired", "only"))
        return sorted(params, key=lambda item: item[0])

    def _search_title_id(self, video, config, episode=False):
        title = (video.get("series") if episode else video.get("title")) or ""
        title = str(title).strip()
        if not title:
            return None
        data = self._with_auth_retry(
            lambda: self._http_get_json(
                self._api_url("features"),
                [("query", title.lower())],
                self._search_headers(config),
                timeout=HTTP_TIMEOUT_SECONDS,
            ),
            config,
        )
        wanted_year = _int_or_none(video.get("year"))
        wanted_title = title.lower()
        wanted_types = {"movie"} if not episode else {"series", "tvshow", "tv show", "show"}
        for item in data.get("data") or []:
            attrs = item.get("attributes") or {}
            item_title = str(attrs.get("title") or "").lower()
            item_year = _int_or_none(attrs.get("year"))
            feature_type = _feature_type(attrs)
            if feature_type and feature_type not in wanted_types:
                continue
            if item_title == wanted_title and (wanted_year is None or item_year == wanted_year):
                return sanitize_external_id(item.get("id") or attrs.get("feature_id"))
        return None

    def _results_from_response(self, video, languages, result, config):
        include_ai = _bool_config(config, "include_ai_translated", False)
        include_machine = _bool_config(config, "include_machine_translated", False)
        requested_codes = {code.lower() for code in _requested_language_codes(languages)}
        requested_variants = _requested_language_variants(languages)
        results = []
        seen = set()
        for item in result.get("data") or []:
            attrs = item.get("attributes") or {}
            forced = is_real_forced(attrs)
            if attrs.get("ai_translated") and not include_ai:
                continue
            if attrs.get("machine_translated") and not include_machine:
                continue
            language_code = str(attrs.get("language") or "").lower()
            if language_code not in requested_codes:
                continue
            if (bool(attrs.get("hearing_impaired")), forced) not in requested_variants.get(language_code, set()):
                continue
            files = attrs.get("files") or []
            if not files:
                continue
            result_item = self._result(video, item, attrs, files[0], forced)
            if result_item["id"] in seen:
                continue
            seen.add(result_item["id"])
            results.append(result_item)
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def _result(self, video, item, attrs, file_info, forced):
        feature = attrs.get("feature_details") or {}
        language = language_payload_from_api_code(
            attrs.get("language"),
            hearing_impaired=attrs.get("hearing_impaired"),
            forced=forced,
        )
        release = attrs.get("release") or file_info.get("file_name") or feature.get("movie_name") or str(item.get("id"))
        matches = derive_matches(video, attrs, feature)
        score = compute_score(matches)
        score_without_hash = compute_score([match for match in matches if match != "hash"])
        file_id = file_info["file_id"]
        subtitle_id = attrs.get("subtitle_id") or item.get("id")
        return {
            "provider": PROVIDER_ID,
            "id": f"opensubtitlescom-{file_id}-{language['alpha3']}",
            "language": language,
            "release_info": release,
            "filename": file_info.get("file_name") or f"opensubtitlescom.{file_id}.srt",
            "matches": matches,
            "score": score,
            "score_without_hash": score_without_hash,
            "score_out_of": 100,
            "hash_verifiable": True,
            "hearing_impaired_verifiable": True,
            "hearing_impaired": bool(attrs.get("hearing_impaired")),
            "page_link": attrs.get("url"),
            "display": {
                "source": "opensubtitles.com",
                "title": feature.get("movie_name"),
                "release": release,
                "uploader": (attrs.get("uploader") or {}).get("name"),
                "downloads": attrs.get("download_count", 0),
                "ratings": attrs.get("ratings", 0),
                "trusted": bool(attrs.get("from_trusted")),
                "ai_translated": bool(attrs.get("ai_translated")),
                "machine_translated": bool(attrs.get("machine_translated")),
            },
            "provider_payload": {
                "provider": PROVIDER_ID,
                "schema": 1,
                "subtitle_id": subtitle_id,
                "file_id": file_id,
                "filename": file_info.get("file_name") or f"opensubtitlescom.{file_id}.srt",
                "language": language,
                "release_info": release,
            },
        }

    def download(self, provider_payload, language, config):
        del language
        config = dict(config or {})
        self._require_config(config)
        payload = provider_payload or {}
        file_id = payload.get("file_id")
        if not file_id:
            raise ValueError("opensubtitlescom download requires file_id")
        self._ensure_login(config)
        download_data = self._with_auth_retry(
            lambda: self._http_post_json(
                self._api_url("download"),
                {"file_id": int(file_id), "sub_format": "srt"},
                self._auth_headers(config, include_token=True),
                timeout=HTTP_TIMEOUT_SECONDS,
            ),
            config,
        )
        link = download_data.get("link")
        if not link:
            raise RuntimeError("OpenSubtitles.com download response did not include link")
        body = self._http_get_bytes(link, {"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_SECONDS)
        body, fmt = extract_download(body, payload.get("filename") or link)
        body = _normalize_line_endings(body)
        return _content_payload(body, fmt)

    def _ensure_login(self, config):
        if self.token and (time.time() - self.token_started) < TOKEN_TTL_SECONDS:
            return
        data = self._http_post_json(
            _base_api_url(DEFAULT_HOST, "login"),
            {"username": config["username"], "password": config["password"]},
            self._auth_headers(config, include_token=False),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        token = data.get("token")
        base_url = data.get("base_url")
        if not token:
            raise RuntimeError("OpenSubtitles.com login response did not include token")
        if not base_url:
            raise RuntimeError("OpenSubtitles.com login response did not include base_url")
        self.token = token
        self.base_host = _host_from_base_url(base_url)
        self.token_started = time.time()

    def _with_auth_retry(self, operation, config):
        try:
            return operation()
        except AuthenticationRequired:
            self.token = None
            self.token_started = 0
            self._ensure_login(config)
            return operation()

    def _api_url(self, path):
        return _base_api_url(self.base_host, path)

    def _search_headers(self, config):
        include_token = bool(self.token and self.base_host.startswith("vip"))
        return self._auth_headers(config, include_token=include_token)

    def _auth_headers(self, config, include_token=False):
        headers = {
            "Accept": "application/json",
            "Api-Key": str(config.get("api_key") or ""),
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if include_token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _require_config(self, config):
        for key in ("username", "password", "api_key"):
            if not str((config or {}).get(key) or "").strip():
                raise ValueError(f"OpenSubtitles.com {key} is required")

    def _http_get_json(self, path, params, headers, timeout=HTTP_TIMEOUT_SECONDS):
        body = self._http_request("GET", _url_with_params(path, params), headers, timeout=timeout)
        return _decode_json(body)

    def _http_post_json(self, path, payload, headers, timeout=HTTP_TIMEOUT_SECONDS):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        body = self._http_request("POST", path, headers, data=data, timeout=timeout)
        return _decode_json(body)

    def _http_get_bytes(self, path, headers, timeout=HTTP_TIMEOUT_SECONDS):
        return self._http_request("GET", path, headers, timeout=timeout)

    def _http_request(self, method, url, headers, data=None, timeout=HTTP_TIMEOUT_SECONDS):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = _error_message(body) or exc.reason or f"status {exc.code}"
            if exc.code in (400, 401):
                if exc.code == 401:
                    raise AuthenticationRequired(message) from exc
                raise ValueError(message) from exc
            if exc.code == 406:
                raise DownloadLimitExceeded(message) from exc
            if exc.code == 410:
                raise RuntimeError("OpenSubtitles.com download link expired") from exc
            if exc.code == 429:
                raise RateLimited(message) from exc
            if exc.code >= 500:
                raise RuntimeError(f"OpenSubtitles.com server error {exc.code}") from exc
            raise RuntimeError(f"OpenSubtitles.com request failed with status {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenSubtitles.com request failed: {exc.reason}") from exc


def derive_matches(video, attrs, feature):
    video = video or {}
    matches = []

    def add(value):
        if value not in matches:
            matches.append(value)

    if video.get("kind") == "episode":
        add("series")
        if _int_or_none(video.get("season")) == _int_or_none(feature.get("season_number")):
            add("season")
        if _int_or_none(video.get("episode")) == _int_or_none(feature.get("episode_number")):
            add("episode")
        if _imdb_id(video.get("series_imdb_id")) and _imdb_id(video.get("series_imdb_id")) == _int_or_none(feature.get("parent_imdb_id")):
            add("series_imdb_id")
    else:
        add("title")
        if _imdb_id(video.get("imdb_id")) and _imdb_id(video.get("imdb_id")) == _int_or_none(feature.get("imdb_id")):
            add("imdb_id")
    if _int_or_none(video.get("year")) == _int_or_none(feature.get("year")):
        add("year")
    if attrs.get("moviehash_match"):
        add("hash")
    release_group = str(video.get("release_group") or "").lower()
    release = str(attrs.get("release") or "").lower()
    if release_group and release_group in release:
        add("release_group")
    return matches


def compute_score(matches):
    weights = {
        "series": 20,
        "title": 25,
        "season": 15,
        "episode": 20,
        "series_imdb_id": 30,
        "imdb_id": 30,
        "year": 10,
        "hash": 35,
        "release_group": 15,
    }
    return min(100, sum(weights.get(match, 0) for match in matches))


def _base_api_url(host, path):
    host = _host_from_base_url(host)
    return f"https://{host}/api/v1/{str(path).lstrip('/')}"


def _host_from_base_url(value):
    text = str(value or DEFAULT_HOST).strip()
    if text.startswith(("http://", "https://")):
        parsed = urllib.parse.urlparse(text)
        return parsed.netloc or DEFAULT_HOST
    return text.strip("/") or DEFAULT_HOST


def _url_with_params(url, params):
    if not params:
        return url
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode(params)}"


def _decode_json(body):
    try:
        return json.loads((body or b"").decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("OpenSubtitles.com returned invalid JSON") from exc


def _error_message(body):
    try:
        data = json.loads((body or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("message") or data.get("error") or data.get("detail")


def _bool_config(config, key, default):
    value = (config or {}).get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _moviehash(video):
    hashes = (video or {}).get("hashes") or {}
    return hashes.get("opensubtitlescom") or (video or {}).get("moviehash")


def _imdb_id(value):
    if value in (None, ""):
        return None
    try:
        return sanitize_external_id(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    if isinstance(value, (list, tuple)):
        for item in value:
            converted = _int_or_none(item)
            if converted is not None:
                return converted
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _feature_type(attrs):
    value = str((attrs or {}).get("feature_type") or (attrs or {}).get("type") or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def extract_download(body, filename):
    if not body:
        raise ValueError("opensubtitlescom downloaded empty subtitle")
    stream = io.BytesIO(body)
    if zipfile.is_zipfile(stream):
        with zipfile.ZipFile(stream) as archive:
            names = [name for name in archive.namelist() if _subtitle_extension(name)]
            if not names:
                raise ValueError("opensubtitlescom archive contains no supported subtitle files")
            names.sort(key=lambda name: (not name.lower().endswith(".srt"), len(name), name.lower()))
            name = names[0]
            return archive.read(name), _subtitle_extension(name) or "srt"
    return body, _format_from_filename(filename)


def _subtitle_extension(name):
    lower = (name or "").lower()
    for extension in SUBTITLE_EXTENSIONS:
        if lower.endswith(extension):
            return extension.lstrip(".")
    return None


def _format_from_filename(filename):
    return _subtitle_extension(urllib.parse.urlparse(str(filename)).path) or "srt"


def _normalize_line_endings(body):
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _content_payload(body, fmt):
    if not body:
        raise ValueError("opensubtitlescom downloaded empty subtitle")
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
