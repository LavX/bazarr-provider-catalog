import base64
import hashlib
import http.server
import io
import importlib.util
import json
import ssl
import threading
import unittest
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsdump"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

BASE_URL = "http://subsdump.example.test"
CONFIG = {"base_url": BASE_URL, "api_key": "test-key"}
INFO_URL = f"{BASE_URL}/api/v1/info"
MOVIE_SEARCH_URL = (
    f"{BASE_URL}/api/v1/movies/tt1160419/subtitles"
    "?language=eng&per_page=100&release=Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv&year=2021"
)
EPISODE_SEARCH_URL = (
    f"{BASE_URL}/api/v1/series/tt0903747/seasons/1/episodes/1/subtitles"
    "?language=eng&per_page=100&release=breaking.bad.s01e01.720p.bluray.x264-reward.mkv"
)
POR_BR_SEARCH_URL = (
    f"{BASE_URL}/api/v1/movies/tt1160419/subtitles"
    "?language=por-BR&per_page=100&release=Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv&year=2021"
)
MOVIE_FALLBACK_URL = (
    f"{BASE_URL}/api/v1/subtitles"
    "?language=eng&per_page=100&release=Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv"
    "&title=Dune&media_type=movie&year=2021"
)
EPISODE_FALLBACK_URL = (
    f"{BASE_URL}/api/v1/subtitles"
    "?language=eng&per_page=100&release=breaking.bad.s01e01.720p.bluray.x264-reward.mkv"
    "&title=Breaking+Bad&media_type=episode&season=1&episode=1"
)
SEASON_TWO_SEARCH_URL = (
    f"{BASE_URL}/api/v1/series/tt0903747/seasons/2/episodes/1/subtitles"
    "?language=eng&per_page=100&release=breaking.bad.s01e01.720p.bluray.x264-reward.mkv"
)
CONTENT_URL = f"{BASE_URL}/api/v1/subtitles/42/content"
SRT_BYTES = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
SRT_NORMALIZED = SRT_BYTES.replace(b"\r\n", b"\n")


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subsdump_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_bytes(name):
    return (FIXTURE_DIR / name).read_bytes()


def _fixture_json(name):
    return json.loads(_fixture_bytes(name).decode("utf-8"))


def _info_body(name="SubsDump", api_version="v1"):
    return json.dumps(
        {"name": name, "version": "2.1.0", "api_version": api_version}
    ).encode("utf-8")


def _movie_video(**overrides):
    video = {
        "kind": "movie",
        "title": "Dune",
        "year": 2021,
        "imdb_id": "tt1160419",
        "original_name": "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv",
    }
    video.update(overrides)
    return video


def _episode_video(**overrides):
    video = {
        "kind": "episode",
        "series": "Breaking Bad",
        "title": "Pilot",
        "season": 1,
        "episode": 1,
        "year": 2008,
        "series_imdb_id": "tt0903747",
        "original_name": "breaking.bad.s01e01.720p.bluray.x264-reward.mkv",
    }
    video.update(overrides)
    return video


def _record(record_id=42, code="eng", title="Dune", releases=None, **overrides):
    record = {
        "id": record_id,
        "media": {
            "type": "movie",
            "title": title,
            "imdb_id": "tt1160419",
            "year": 2021,
            "season": None,
            "episode": None,
        },
        "language": {"code": code, "name": "English"},
        "releases": releases if releases is not None else [
            "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX"
        ],
        "hearing_impaired": False,
        "uploader": "contributor",
        "links": {
            "page": f"/subtitles/{record_id}",
            "archive": f"/api/v1/subtitles/{record_id}/archive",
            "content": f"/api/v1/subtitles/{record_id}/content",
        },
    }
    record.update(overrides)
    return record


def _zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()


def _sent_request(url):
    """Split a captured URL into its path and single-valued query params."""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    return parsed.path, {key: values[0] for key, values in query.items()}


class _Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        if limit is None or limit < 0:
            return self._body
        return self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SubsDumpTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_responses(self, responses, calls=None):
        provider = self.mod.SubsDumpProvider()

        def stub(url, api_key=None, timeout=None, max_bytes=None):
            del timeout, max_bytes
            if calls is not None:
                calls.append((url, api_key))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        return provider

    def _patch_transport(self, route, error=None):
        """Route every provider request away from the network.

        Both urlopen and any build_opener result funnel through
        OpenerDirector.open, so patching that one method intercepts the
        provider's requests no matter which entry point it uses, and
        returns the list of Request objects it captured.
        """
        requests = []

        def fake_open(opener, request, data=None, timeout=None):
            del opener, data, timeout
            requests.append(request)
            if error is not None:
                raise error
            return route(request)

        patcher = patch.object(
            self.mod.urllib.request.OpenerDirector, "open", fake_open
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return requests


class BaseUrlTests(SubsDumpTestCase):
    def test_missing_base_url_raises(self):
        with self.assertRaisesRegex(self.mod.ConfigurationError, "base_url"):
            self.mod._base_url({})
        with self.assertRaisesRegex(self.mod.ConfigurationError, "base_url"):
            self.mod._base_url({"base_url": "  "})

    def test_scheme_is_required(self):
        with self.assertRaisesRegex(self.mod.ConfigurationError, "http:// or https://"):
            self.mod._base_url({"base_url": "subsdump.example.test"})

    def test_credentials_are_rejected(self):
        with self.assertRaisesRegex(
            self.mod.ConfigurationError, "credentials"
        ):
            self.mod._base_url({"base_url": "http://user:pass@subsdump.example.test"})

    def test_empty_embedded_credentials_are_rejected(self):
        # urllib parses "http://:@host" and "http://@host" as carrying empty
        # credentials, which truthiness testing let through.
        for base_url in ("http://:@subsdump.example.test", "http://@subsdump.example.test"):
            with self.subTest(base_url=base_url):
                with self.assertRaisesRegex(self.mod.ConfigurationError, "credentials"):
                    self.mod._base_url({"base_url": base_url})

    def test_scheme_is_case_insensitive(self):
        self.assertEqual(
            self.mod._base_url({"base_url": "HTTP://subsdump.example.test"}),
            "HTTP://subsdump.example.test",
        )

    def test_bad_port_is_rejected(self):
        # Reading the port during validation surfaces a malformed port at
        # config time instead of as a generic request failure later.
        with self.assertRaisesRegex(self.mod.ConfigurationError, "port"):
            self.mod._base_url({"base_url": "https://subsdump.example.test:abc"})

    def test_hostless_base_url_is_rejected(self):
        # "http:///api/v1" parses to an empty host and used to validate fine.
        with self.assertRaisesRegex(self.mod.ConfigurationError, "host"):
            self.mod._base_url({"base_url": "http:///api/v1"})

    def test_query_string_is_rejected(self):
        with self.assertRaisesRegex(self.mod.ConfigurationError, "query"):
            self.mod._base_url({"base_url": "http://subsdump.example.test/?path=x"})

    def test_fragment_is_rejected(self):
        with self.assertRaisesRegex(self.mod.ConfigurationError, "fragment"):
            self.mod._base_url({"base_url": "http://subsdump.example.test/#info"})

    def test_trailing_slash_is_stripped(self):
        base_url = self.mod._base_url({"base_url": "http://subsdump.example.test/"})
        self.assertEqual(base_url, "http://subsdump.example.test")
        self.assertEqual(
            self.mod._base_url({"base_url": "http://subsdump.example.test///"}),
            "http://subsdump.example.test",
        )

    def test_api_key_is_optional(self):
        self.assertIsNone(self.mod._api_key({}))
        self.assertIsNone(self.mod._api_key({"api_key": "  "}))
        self.assertEqual(self.mod._api_key({"api_key": " key "}), "key")


class WorkerExceptionTests(SubsDumpTestCase):
    def test_the_worker_reconstructs_these_exception_names(self):
        # The worker boundary rebuilds exceptions by class name only, so the
        # names and their bases are part of the host contract.
        self.assertEqual(self.mod.AuthenticationError.__name__, "AuthenticationError")
        self.assertTrue(issubclass(self.mod.AuthenticationError, ValueError))
        self.assertEqual(self.mod.ConfigurationError.__name__, "ConfigurationError")
        self.assertTrue(issubclass(self.mod.ConfigurationError, ValueError))
        self.assertEqual(self.mod.ServiceUnavailable.__name__, "ServiceUnavailable")
        self.assertTrue(issubclass(self.mod.ServiceUnavailable, RuntimeError))


class ServiceCodeTests(SubsDumpTestCase):
    def test_alpha3_codes_resolve_to_themselves(self):
        self.assertEqual(self.mod._service_code("eng"), "eng")
        self.assertEqual(self.mod._service_code("bul"), "bul")
        self.assertEqual(self.mod._service_code("zho"), "zho")

    def test_alpha2_codes_resolve_to_alpha3(self):
        self.assertEqual(self.mod._service_code("en"), "eng")
        self.assertEqual(self.mod._service_code("pt"), "por")
        self.assertEqual(self.mod._service_code("kl"), "kal")

    def test_bibliographic_codes_are_not_service_codes(self):
        # The worker builds requested languages from babelfish alpha3 codes,
        # which are ISO 639-3, and the service speaks those same codes, so
        # the bibliographic 639-2/B aliases never arrive here. An unknown
        # code resolves to None: the language is skipped, never guessed.
        for code in ("cze", "dut", "fre", "ger", "gre", "ice", "per", "rum", "chi", "arm", "geo"):
            self.assertIsNone(self.mod._service_code(code), code)

    def test_brazilian_portuguese_forms(self):
        self.assertEqual(self.mod._service_code("por-BR"), "por-BR")
        self.assertEqual(self.mod._service_code("por-br"), "por-BR")
        self.assertEqual(self.mod._service_code("pt-BR"), "por-BR")

    def test_other_countries_resolve_to_the_base_language(self):
        self.assertEqual(self.mod._service_code("por-PT"), "por")
        self.assertEqual(self.mod._service_code("eng-US"), "eng")

    def test_unsupported_codes_resolve_to_none(self):
        self.assertIsNone(self.mod._service_code("xxx"))
        self.assertIsNone(self.mod._service_code(""))
        self.assertIsNone(self.mod._service_code(None))
        self.assertIsNone(self.mod._service_code("qaa-QB"))


class RequestedLanguageTests(SubsDumpTestCase):
    def test_dict_alpha3_is_accepted(self):
        self.assertEqual(
            self.mod._requested_languages([{"alpha3": "eng", "hi": False, "forced": False}]),
            [{"code": "eng", "hi": False}],
        )

    def test_string_and_code_key_forms_are_accepted(self):
        self.assertEqual(self.mod._requested_languages(["eng"]), [{"code": "eng", "hi": False}])
        self.assertEqual(
            self.mod._requested_languages([{"code": "bul"}]),
            [{"code": "bul", "hi": False}],
        )

    def test_hi_flag_is_carried(self):
        self.assertEqual(
            self.mod._requested_languages([{"alpha3": "eng", "hi": True}]),
            [{"code": "eng", "hi": True}],
        )

    def test_forced_requests_are_skipped(self):
        # The service has no forced-only distinction, so those requests get
        # nothing instead of a plain subtitle mislabelled as forced.
        self.assertEqual(
            self.mod._requested_languages([{"alpha3": "eng", "forced": True}]),
            [],
        )

    def test_unsupported_languages_are_skipped(self):
        self.assertEqual(self.mod._requested_languages([{"alpha3": "xxx"}]), [])
        self.assertEqual(self.mod._requested_languages([]), [])
        self.assertEqual(self.mod._requested_languages(None), [])
        self.assertEqual(self.mod._requested_languages([42]), [])

    def test_duplicates_are_deduplicated(self):
        self.assertEqual(
            self.mod._requested_languages([{"alpha3": "eng"}, {"alpha2": "en"}, "eng"]),
            [{"code": "eng", "hi": False}],
        )
        # The hi flag separates variants of the same language.
        self.assertEqual(
            len(self.mod._requested_languages([{"alpha3": "eng"}, {"alpha3": "eng", "hi": True}])),
            2,
        )

    def test_brazilian_portuguese_variants(self):
        for language in (
            {"alpha3": "por-BR"},
            {"alpha2": "pt", "country_alpha2": "BR"},
            {"alpha3": "por", "country": "BR"},
            {"alpha3": "por", "region": "br"},
        ):
            self.assertEqual(
                self.mod._requested_languages([language]),
                [{"code": "por-BR", "hi": False}],
                language,
            )


class SearchRequestTests(SubsDumpTestCase):
    def test_movie_with_imdb_uses_the_movie_endpoint(self):
        path, params = self.mod._search_request(_movie_video(), "eng")

        self.assertEqual(path, "/api/v1/movies/tt1160419/subtitles")
        self.assertEqual(
            params,
            {
                "language": "eng",
                "per_page": 100,
                "release": "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv",
                "year": 2021,
            },
        )

    def test_movie_without_year_sends_no_year(self):
        video = _movie_video()
        del video["year"]
        path, params = self.mod._search_request(video, "eng")
        self.assertEqual(path, "/api/v1/movies/tt1160419/subtitles")
        self.assertNotIn("year", params)

    def test_movie_without_filename_sends_no_release(self):
        video = _movie_video()
        del video["original_name"]
        path, params = self.mod._search_request(video, "eng")
        self.assertNotIn("release", params)
        self.assertEqual(params["language"], "eng")
        self.assertEqual(params["per_page"], 100)

    def test_release_takes_the_basename_of_a_path(self):
        video = _movie_video(original_path="/media/movies/Dune.2021.1080p.mkv")
        del video["original_name"]
        path, params = self.mod._search_request(video, "eng")
        self.assertEqual(params["release"], "Dune.2021.1080p.mkv")

    def test_release_is_truncated_to_512_chars(self):
        video = _movie_video(original_name="x" * 600 + ".mkv")
        path, params = self.mod._search_request(video, "eng")
        self.assertEqual(len(params["release"]), 512)

    def test_episode_with_series_imdb_uses_the_episode_endpoint(self):
        path, params = self.mod._search_request(_episode_video(), "eng")

        self.assertEqual(
            path, "/api/v1/series/tt0903747/seasons/1/episodes/1/subtitles"
        )
        # The episode endpoint takes no year, even when the video carries one.
        self.assertEqual(
            params,
            {
                "language": "eng",
                "per_page": 100,
                "release": "breaking.bad.s01e01.720p.bluray.x264-reward.mkv",
            },
        )

    def test_season_zero_is_a_valid_season(self):
        path, params = self.mod._search_request(_episode_video(season=0), "eng")
        self.assertEqual(
            path, "/api/v1/series/tt0903747/seasons/0/episodes/1/subtitles"
        )

    def test_movie_title_fallback_without_imdb(self):
        video = _movie_video()
        del video["imdb_id"]
        path, params = self.mod._search_request(video, "eng")

        self.assertEqual(path, "/api/v1/subtitles")
        self.assertEqual(
            params,
            {
                "language": "eng",
                "per_page": 100,
                "release": "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv",
                "title": "Dune",
                "media_type": "movie",
                "year": 2021,
            },
        )

    def test_movie_fallback_without_title_or_imdb_is_none(self):
        video = _movie_video()
        del video["imdb_id"]
        del video["title"]
        self.assertIsNone(self.mod._search_request(video, "eng"))

    def test_malformed_imdb_falls_back_to_title_search(self):
        video = _movie_video(imdb_id="1160419")
        path, params = self.mod._search_request(video, "eng")
        self.assertEqual(path, "/api/v1/subtitles")
        self.assertEqual(params["media_type"], "movie")
        self.assertEqual(params["title"], "Dune")

    def test_episode_title_fallback_without_series_imdb(self):
        video = _episode_video()
        del video["series_imdb_id"]
        path, params = self.mod._search_request(video, "eng")

        self.assertEqual(path, "/api/v1/subtitles")
        self.assertEqual(
            params,
            {
                "language": "eng",
                "per_page": 100,
                "release": "breaking.bad.s01e01.720p.bluray.x264-reward.mkv",
                "title": "Breaking Bad",
                "media_type": "episode",
                "season": 1,
                "episode": 1,
            },
        )

    def test_episode_fallback_sends_no_year(self):
        # Upstream sends no year for episodes, and a series year could empty
        # out later seasons on a service that indexes per season.
        video = _episode_video()
        del video["series_imdb_id"]
        path, params = self.mod._search_request(video, "eng")
        self.assertNotIn("year", params)

    def test_episode_fallback_without_numbers_sends_no_numbers(self):
        video = _episode_video()
        del video["series_imdb_id"]
        del video["season"]
        del video["episode"]
        path, params = self.mod._search_request(video, "eng")
        self.assertEqual(path, "/api/v1/subtitles")
        self.assertNotIn("season", params)
        self.assertNotIn("episode", params)

    def test_episode_with_imdb_but_without_episode_falls_back(self):
        video = _episode_video()
        del video["episode"]
        path, params = self.mod._search_request(video, "eng")
        self.assertEqual(path, "/api/v1/subtitles")
        self.assertEqual(params["media_type"], "episode")
        self.assertNotIn("episode", params)

    def test_unknown_kinds_have_no_request(self):
        self.assertIsNone(self.mod._search_request({"kind": "other"}, "eng"))
        self.assertIsNone(self.mod._search_request(None, "eng"))
        self.assertIsNone(self.mod._search_request({}, "eng"))


class ValidRecordTests(SubsDumpTestCase):
    def test_a_well_formed_record_is_valid(self):
        self.assertTrue(self.mod._valid_record(_record(), "eng"))

    def test_bool_is_not_a_valid_id(self):
        self.assertFalse(self.mod._valid_record(_record(record_id=True), "eng"))

    def test_non_positive_ids_are_invalid(self):
        self.assertFalse(self.mod._valid_record(_record(record_id=0), "eng"))
        self.assertFalse(self.mod._valid_record(_record(record_id=-42), "eng"))

    def test_non_int_ids_are_invalid(self):
        self.assertFalse(self.mod._valid_record(_record(record_id="42"), "eng"))
        self.assertFalse(self.mod._valid_record(_record(record_id=None), "eng"))
        self.assertFalse(self.mod._valid_record(_record(record_id=42.0), "eng"))

    def test_non_dict_records_are_invalid(self):
        self.assertFalse(self.mod._valid_record("subtitles", "eng"))
        self.assertFalse(self.mod._valid_record(None, "eng"))

    def test_media_must_be_a_dict_with_a_string_title(self):
        self.assertFalse(self.mod._valid_record(_record(media="Dune"), "eng"))
        self.assertFalse(self.mod._valid_record(_record(title=""), "eng"))
        self.assertFalse(self.mod._valid_record(_record(title=42), "eng"))
        media = dict(_record()["media"])
        del media["title"]
        self.assertFalse(self.mod._valid_record(_record(media=media), "eng"))

    def test_language_code_must_equal_the_requested_code(self):
        self.assertFalse(self.mod._valid_record(_record(code="ara"), "eng"))
        self.assertFalse(self.mod._valid_record(_record(code="ENG"), "eng"))
        self.assertFalse(self.mod._valid_record(_record(language="eng"), "eng"))
        self.assertTrue(self.mod._valid_record(_record(code="por-BR"), "por-BR"))

    def test_releases_must_be_a_list_of_strings(self):
        self.assertFalse(
            self.mod._valid_record(_record(releases="Dune.2021.1080p.x264"), "eng")
        )
        self.assertFalse(
            self.mod._valid_record(_record(releases=["ok", 42]), "eng")
        )
        self.assertTrue(self.mod._valid_record(_record(releases=[]), "eng"))

    def test_tampered_links_are_invalid(self):
        record = _record()
        record["links"]["page"] = "/subtitles/99"
        self.assertFalse(self.mod._valid_record(record, "eng"))
        record = _record()
        record["links"]["content"] = "/api/v1/subtitles/99/content"
        self.assertFalse(self.mod._valid_record(record, "eng"))
        record = _record()
        record["links"]["content"] = "/api/v1/subtitles/42/content?token=x"
        self.assertFalse(self.mod._valid_record(record, "eng"))
        self.assertFalse(self.mod._valid_record(_record(links="nope"), "eng"))


class ParseSubtitlesTests(SubsDumpTestCase):
    def test_movie_fixture_keeps_only_valid_records(self):
        records = self.mod._parse_subtitles(
            _fixture_bytes("subsdump_search_dune_2021.json"), "eng"
        )
        self.assertEqual([record["id"] for record in records], [42, 43, 46])

    def test_episode_fixture_parses(self):
        records = self.mod._parse_subtitles(
            _fixture_bytes("subsdump_search_breaking_bad_s01e01.json"), "eng"
        )
        self.assertEqual([record["id"] for record in records], [51, 52, 53])

    def test_por_br_records_only_match_the_por_br_code(self):
        records = self.mod._parse_subtitles(
            _fixture_bytes("subsdump_search_dune_2021_por_br.json"), "por-BR"
        )
        self.assertEqual([record["id"] for record in records], [61])
        records = self.mod._parse_subtitles(
            _fixture_bytes("subsdump_search_dune_2021_por_br.json"), "por"
        )
        self.assertEqual([record["id"] for record in records], [62])

    def test_only_an_empty_list_is_a_legitimate_empty_search(self):
        self.assertEqual(self.mod._parse_subtitles(b'{"subtitles": []}', "eng"), [])

    def test_a_missing_subtitles_field_raises(self):
        # A reply without the field is broken, not empty: collapsing it into
        # "no results" hides a service or config problem.
        with self.assertRaisesRegex(ValueError, "'subtitles'"):
            self.mod._parse_subtitles(b"{}", "eng")

    def test_a_null_subtitles_field_raises(self):
        with self.assertRaisesRegex(ValueError, "'subtitles'"):
            self.mod._parse_subtitles(b'{"subtitles": null}', "eng")

    def test_non_list_subtitles_raises(self):
        with self.assertRaisesRegex(ValueError, "not a list"):
            self.mod._parse_subtitles(b'{"subtitles": {}}', "eng")

    def test_malformed_json_raises(self):
        with self.assertRaisesRegex(ValueError, "malformed JSON"):
            self.mod._parse_subtitles(b"<html>oops</html>", "eng")

    def test_non_object_payload_raises(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            self.mod._parse_subtitles(b'["subtitles"]', "eng")

    def test_a_garbled_search_reply_is_a_plain_error_not_a_park(self):
        # One garbled 200 reply from a service that already passed the
        # identity check is a single failed search, not a configuration
        # problem: the host parks a provider on the latter for half a day.
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            MOVIE_SEARCH_URL: b"<html>oops</html>",
        })
        with self.assertRaises(ValueError) as caught:
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)
        self.assertNotIsInstance(caught.exception, self.mod.ConfigurationError)
        self.assertIn("malformed JSON", str(caught.exception))

    def test_a_non_object_search_reply_is_a_plain_error_not_a_park(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            MOVIE_SEARCH_URL: b'["subtitles"]',
        })
        with self.assertRaises(ValueError) as caught:
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)
        self.assertNotIsInstance(caught.exception, self.mod.ConfigurationError)
        self.assertIn("JSON object", str(caught.exception))


class DeriveMatchesTests(SubsDumpTestCase):
    def test_movie_title_and_year_match(self):
        matches = self.mod._derive_matches(_movie_video(), _record())
        self.assertEqual(matches, ["title", "year"])

    def test_movie_title_match_is_casefolded(self):
        matches = self.mod._derive_matches(
            _movie_video(title="DUNE"), _record(title="dune")
        )
        self.assertIn("title", matches)

    def test_sequel_title_does_not_match(self):
        matches = self.mod._derive_matches(_movie_video(), _record(title="Dune: Part Two"))
        self.assertNotIn("title", matches)

    def test_movie_year_only_matches_when_the_record_carries_it(self):
        record = _record()
        del record["media"]["year"]
        self.assertNotIn("year", self.mod._derive_matches(_movie_video(), record))
        record = _record()
        record["media"]["year"] = None
        self.assertNotIn("year", self.mod._derive_matches(_movie_video(), record))

    def test_missing_versus_missing_year_is_not_a_match(self):
        video = _movie_video()
        del video["year"]
        record = _record()
        del record["media"]["year"]
        self.assertNotIn("year", self.mod._derive_matches(video, record))

    def test_episode_series_season_and_episode_match(self):
        record = _record(
            record_id=51,
            media={
                "type": "episode",
                "title": "Breaking Bad",
                "imdb_id": "tt0903747",
                "year": 2008,
                "season": 1,
                "episode": 1,
            },
            releases=["breaking.bad.s01e01.720p.bluray.x264-reward"],
        )
        matches = self.mod._derive_matches(_episode_video(), record)
        self.assertEqual(matches, ["series", "season", "episode"])

    def test_no_episode_match_without_a_season_on_the_video(self):
        # A coinciding episode number alone is not an episode match: without
        # a season on the video there is no episode identity to award.
        record = _record(
            record_id=51,
            media={
                "type": "episode",
                "title": "Breaking Bad",
                "imdb_id": "tt0903747",
                "year": 2008,
                "season": 1,
                "episode": 1,
            },
            releases=["breaking.bad.s01e01.720p.bluray.x264-reward"],
        )
        video = _episode_video()
        del video["season"]
        self.assertNotIn("episode", self.mod._derive_matches(video, record))

    def test_series_match_is_casefolded(self):
        record = _record(
            record_id=52,
            media={
                "type": "episode",
                "title": "breaking bad",
                "imdb_id": "tt0903747",
                "year": 2008,
                "season": 1,
                "episode": 1,
            },
            releases=["breaking.bad.s01e01.1080p.WEB-DL.DD5.1.H264-SHITBOX"],
        )
        self.assertIn("series", self.mod._derive_matches(_episode_video(), record))

    def test_season_zero_is_a_real_season(self):
        record = _record(
            media={
                "type": "episode",
                "title": "Breaking Bad",
                "imdb_id": "tt0903747",
                "year": 2008,
                "season": 0,
                "episode": 1,
            },
        )
        self.assertIn("season", self.mod._derive_matches(_episode_video(season=0), record))

    def test_season_only_matches_when_the_record_carries_it(self):
        record = _record(
            media={
                "type": "episode",
                "title": "Breaking Bad",
                "imdb_id": "tt0903747",
                "year": 2008,
                "season": None,
                "episode": None,
            },
        )
        matches = self.mod._derive_matches(_episode_video(), record)
        self.assertEqual(matches, ["series"])

    def test_release_attributes_match_within_one_release(self):
        video = _movie_video(
            source="Web",
            resolution="1080p",
            video_codec="H.264",
            audio_codec="AC3",
            release_group="SHITBOX",
        )
        matches = self.mod._derive_matches(video, _record())
        self.assertEqual(
            matches,
            ["title", "year", "source", "resolution", "video_codec", "audio_codec", "release_group"],
        )

    def test_release_attributes_never_pool_across_releases(self):
        video = _movie_video(release_group="FOO-BAR")
        record = _record(releases=["Show.2024.1080p.WEB-DL.x264-FOO", "Show.2024.1080p.WEB-DL.x264-BAR"])
        self.assertNotIn("release_group", self.mod._derive_matches(video, record))
        record = _record(releases=["Show.2024.1080p.WEB-DL.x264-FOO-BAR"])
        self.assertIn("release_group", self.mod._derive_matches(video, record))

    def test_release_group_does_not_manufacture_a_source_match(self):
        video = _movie_video(source="Blu-ray")
        record = _record(releases=["Show.2024.1080p.WEB-DL.x264-BD"])
        self.assertNotIn("source", self.mod._derive_matches(video, record))
        video = _movie_video(source="Web")
        self.assertIn("source", self.mod._derive_matches(video, record))

    def test_title_words_do_not_manufacture_a_source_match(self):
        video = _movie_video(title="The Web", source="Web")
        record = _record(title="The Web", releases=["The.Web.2024.1080p.BluRay.x264"])
        matches = self.mod._derive_matches(video, record)
        self.assertNotIn("source", matches)
        video = _movie_video(title="The Web", source="Blu-ray")
        self.assertIn("source", self.mod._derive_matches(video, record))

    def test_dd_plus_is_eac3_not_ac3(self):
        video = _movie_video(audio_codec="AC3")
        record = _record(releases=["Show.2024.1080p.WEB-DL.DDP5.1.H264-GROUP"])
        self.assertNotIn("audio_codec", self.mod._derive_matches(video, record))
        video = _movie_video(audio_codec="EAC3")
        self.assertIn("audio_codec", self.mod._derive_matches(video, record))

    def test_streaming_service_aliases_match(self):
        video = _movie_video(streaming_service="HBO Max")
        record = _record(releases=["Show.2024.1080p.HMAX.WEB-DL.DDP5.1.H264-GROUP"])
        self.assertIn("streaming_service", self.mod._derive_matches(video, record))

    def test_list_valued_metadata_is_coerced(self):
        video = _movie_video(video_codec=["H.264"])
        self.assertIn("video_codec", self.mod._derive_matches(video, _record()))

    def test_score_is_95_on_identity_and_80_otherwise(self):
        self.assertEqual(self.mod._compute_score(["title", "year"]), 95)
        self.assertEqual(self.mod._compute_score(["series", "season", "episode"]), 95)
        self.assertEqual(self.mod._compute_score(["source"]), 80)
        self.assertEqual(self.mod._compute_score([]), 80)


class SeasonConflictTests(SubsDumpTestCase):
    def _episode_record(self, record_id=71, season=1, episode=1, releases=None):
        return _record(
            record_id=record_id,
            releases=releases if releases is not None else ["show.s01e01.720p.web.h264-grp"],
            media={
                "type": "episode",
                "title": "Breaking Bad",
                "imdb_id": "tt0903747",
                "year": 2008,
                "season": season,
                "episode": episode,
            },
        )

    def test_a_record_of_another_season_is_a_conflict(self):
        self.assertTrue(
            self.mod._names_other_season(_episode_video(season=2), self._episode_record())
        )
        self.assertFalse(
            self.mod._names_other_season(_episode_video(season=1), self._episode_record())
        )

    def test_a_record_without_a_season_is_never_a_conflict(self):
        video = _episode_video()
        self.assertFalse(
            self.mod._names_other_season(video, self._episode_record(season=None, episode=None))
        )
        # A season pack names its own season and defers the episode pick.
        self.assertFalse(
            self.mod._names_other_season(video, self._episode_record(episode=None))
        )

    def test_a_video_without_a_season_is_never_a_conflict(self):
        video = _episode_video()
        del video["season"]
        self.assertFalse(self.mod._names_other_season(video, self._episode_record()))

    def test_movies_never_conflict(self):
        self.assertFalse(
            self.mod._names_other_season(_movie_video(), self._episode_record())
        )

    def test_search_for_s02e01_returns_no_s01e01_candidate(self):
        # A coinciding episode number must not carry home another season's
        # subtitle as an exact-match candidate.
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            SEASON_TWO_SEARCH_URL: json.dumps(
                {"subtitles": [self._episode_record()]}
            ).encode("utf-8"),
        })

        results = provider.search(_episode_video(season=2), [{"alpha3": "eng"}], CONFIG)

        self.assertEqual(results, [])

    def test_season_packs_still_surface_their_own_season(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            EPISODE_SEARCH_URL: json.dumps(
                {"subtitles": [self._episode_record(episode=None)]}
            ).encode("utf-8"),
        })

        results = provider.search(_episode_video(), [{"alpha3": "eng"}], CONFIG)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["matches"], ["series", "season"])
        self.assertEqual(results[0]["score"], 80)
        # The host still gets the requested episode for member selection.
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)


class PayloadBoundsTests(SubsDumpTestCase):
    def test_payload_releases_are_capped_at_512_characters(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            MOVIE_SEARCH_URL: json.dumps(
                {"subtitles": [_record(releases=["x" * 10000])]}
            ).encode("utf-8"),
        })

        results = provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

        self.assertEqual(len(results), 1)
        # The payload is persisted across the worker boundary and is capped;
        # the result's release_info stays whole.
        self.assertEqual(results[0]["provider_payload"]["releases"], ["x" * 512])
        self.assertEqual(results[0]["release_info"], "x" * 10000)
        # The filename derives from the same capped source.
        self.assertEqual(len(results[0]["filename"]), 512 + len(".srt"))


class SearchTests(SubsDumpTestCase):
    def _movie_responses(self, body=None, **extra):
        responses = {
            INFO_URL: _info_body(),
            MOVIE_SEARCH_URL: body if body is not None else _fixture_bytes(
                "subsdump_search_dune_2021.json"
            ),
        }
        responses.update(extra)
        return responses

    def test_movie_search_returns_valid_records(self):
        calls = []
        provider = self._provider_with_responses(
            self._movie_responses(), calls
        )

        results = provider.search(_movie_video(), [{"alpha3": "eng", "hi": False}], CONFIG)

        self.assertEqual(len(results), 2)
        result = results[0]
        self.assertEqual(result["provider"], "subsdump")
        self.assertEqual(result["id"], "subsdump-42-eng")
        self.assertEqual(
            result["language"], {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}
        )
        self.assertEqual(result["release_info"], "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX")
        self.assertEqual(result["filename"], "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt")
        self.assertEqual(result["matches"], ["title", "year"])
        self.assertEqual(result["score"], 95)
        self.assertEqual(result["score_without_hash"], 95)
        self.assertEqual(result["score_out_of"], 100)
        self.assertFalse(result["hash_verifiable"])
        self.assertTrue(result["hearing_impaired_verifiable"])
        self.assertFalse(result["hearing_impaired"])
        self.assertEqual(result["page_link"], f"{BASE_URL}/subtitles/42")
        self.assertEqual(
            result["display"],
            {
                "source": "subsdump",
                "title": "Dune",
                "uploader": "contributor",
                "language_code": "eng",
            },
        )
        self.assertEqual(
            result["provider_payload"],
            {
                "provider": "subsdump",
                "schema": 1,
                "subtitle_id": 42,
                "content_path": "/api/v1/subtitles/42/content",
                "page_path": "/subtitles/42",
                "releases": ["Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX"],
                "language_code": "eng",
            },
        )
        # The second result is the weaker title-mismatch record, sorted after.
        self.assertEqual(results[1]["id"], "subsdump-46-eng")
        self.assertEqual(results[1]["score"], 80)
        # No secret ever reaches a result.
        self.assertNotIn("test-key", json.dumps(results))

    def test_api_key_travels_to_every_call(self):
        calls = []
        provider = self._provider_with_responses(self._movie_responses(), calls)

        provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

        self.assertTrue(calls)
        for url, api_key in calls:
            self.assertEqual(api_key, "test-key", url)

    def test_info_is_verified_once_per_config(self):
        calls = []
        provider = self._provider_with_responses(self._movie_responses(), calls)

        provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)
        provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

        info_calls = [url for url, _ in calls if url == INFO_URL]
        self.assertEqual(len(info_calls), 1)
        self.assertEqual(len(calls), 3)

    def test_empty_result(self):
        provider = self._provider_with_responses(
            self._movie_responses(body=b'{"subtitles": []}')
        )
        results = provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)
        self.assertEqual(results, [])

    def test_wrong_service_is_rejected(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(name="SomethingElse"),
        })
        with self.assertRaisesRegex(self.mod.ConfigurationError, "SubsDump v1"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_wrong_api_version_is_rejected(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(api_version="v2"),
        })
        with self.assertRaisesRegex(self.mod.ConfigurationError, "SubsDump v1"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_malformed_info_reply_is_a_configuration_error(self):
        provider = self._provider_with_responses({
            INFO_URL: b"<html>not json</html>",
        })
        with self.assertRaisesRegex(self.mod.ConfigurationError, "JSON"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_non_object_info_reply_is_a_configuration_error(self):
        provider = self._provider_with_responses({
            INFO_URL: b'["SubsDump"]',
        })
        with self.assertRaisesRegex(self.mod.ConfigurationError, "JSON"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_wrong_service_is_rejected_on_download_too(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(name="SomethingElse"),
            CONTENT_URL: SRT_BYTES,
        })
        with self.assertRaisesRegex(self.mod.ConfigurationError, "SubsDump v1"):
            provider.download(
                {
                    "provider": "subsdump",
                    "content_path": "/api/v1/subtitles/42/content",
                },
                {"alpha3": "eng"},
                CONFIG,
            )

    def test_hearing_impaired_reflects_the_record(self):
        provider = self._provider_with_responses(self._movie_responses())

        results = provider.search(_movie_video(), [{"alpha3": "eng", "hi": True}], CONFIG)

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["id"], "subsdump-43-eng")
        self.assertTrue(result["hearing_impaired"])
        self.assertTrue(result["hearing_impaired_verifiable"])

    def test_hearing_impaired_request_never_gets_plain_records(self):
        provider = self._provider_with_responses(self._movie_responses())

        normal = provider.search(_movie_video(), [{"alpha3": "eng", "hi": False}], CONFIG)
        hearing_impaired = provider.search(
            _movie_video(), [{"alpha3": "eng", "hi": True}], CONFIG
        )

        self.assertEqual({result["id"] for result in normal}, {"subsdump-42-eng", "subsdump-46-eng"})
        self.assertEqual({result["id"] for result in hearing_impaired}, {"subsdump-43-eng"})

    def test_one_get_per_endpoint_per_language(self):
        calls = []
        provider = self._provider_with_responses(self._movie_responses(), calls)

        # Two hi variants of one language share one request, because the
        # service's language parameter is the only difference between them.
        results = provider.search(
            _movie_video(),
            [{"alpha3": "eng", "hi": False}, {"alpha3": "eng", "hi": True}],
            CONFIG,
        )

        self.assertEqual(len(results), 3)
        search_calls = [url for url, _ in calls if url != INFO_URL]
        self.assertEqual(search_calls, [MOVIE_SEARCH_URL])

    def test_two_languages_get_two_requests(self):
        calls = []
        provider = self._provider_with_responses(
            {
                INFO_URL: _info_body(),
                MOVIE_SEARCH_URL: _fixture_bytes("subsdump_search_dune_2021.json"),
                POR_BR_SEARCH_URL: _fixture_bytes("subsdump_search_dune_2021_por_br.json"),
            },
            calls,
        )

        provider.search(
            _movie_video(),
            [{"alpha3": "eng"}, {"alpha3": "por-BR"}],
            CONFIG,
        )

        search_calls = [url for url, _ in calls if url != INFO_URL]
        self.assertEqual(len(search_calls), 2)

    def test_forced_only_request_returns_nothing_without_a_request(self):
        calls = []
        provider = self._provider_with_responses({}, calls)

        results = provider.search(_movie_video(), [{"alpha3": "eng", "forced": True}], CONFIG)

        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_unsupported_language_returns_nothing_without_a_request(self):
        calls = []
        provider = self._provider_with_responses({}, calls)

        results = provider.search(_movie_video(), [{"alpha3": "xxx"}], CONFIG)
        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_unsupported_kind_returns_nothing_without_a_request(self):
        calls = []
        provider = self._provider_with_responses({}, calls)

        results = provider.search({"kind": "other"}, [{"alpha3": "eng"}], CONFIG)
        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_missing_base_url_raises(self):
        provider = self._provider_with_responses({})
        with self.assertRaisesRegex(ValueError, "base_url"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], {})

    def test_movie_title_fallback_params_are_sent(self):
        calls = []
        provider = self._provider_with_responses(
            {
                INFO_URL: _info_body(),
                MOVIE_FALLBACK_URL: _fixture_bytes("subsdump_search_dune_2021.json"),
            },
            calls,
        )
        video = _movie_video()
        del video["imdb_id"]

        provider.search(video, [{"alpha3": "eng"}], CONFIG)

        path, params = _sent_request(calls[-1][0])
        self.assertEqual(path, "/api/v1/subtitles")
        self.assertEqual(
            params,
            {
                "language": "eng",
                "per_page": "100",
                "release": "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.mkv",
                "title": "Dune",
                "media_type": "movie",
                "year": "2021",
            },
        )

    def test_episode_search_returns_the_requested_episode_payload(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            EPISODE_SEARCH_URL: _fixture_bytes("subsdump_search_breaking_bad_s01e01.json"),
        })

        results = provider.search(_episode_video(), [{"alpha3": "eng"}], CONFIG)

        self.assertEqual(len(results), 2)
        result = results[0]
        self.assertEqual(result["id"], "subsdump-51-eng")
        self.assertEqual(result["matches"], ["series", "season", "episode"])
        self.assertEqual(result["score"], 95)
        self.assertEqual(
            result["provider_payload"],
            {
                "provider": "subsdump",
                "schema": 1,
                "subtitle_id": 51,
                "content_path": "/api/v1/subtitles/51/content",
                "page_path": "/subtitles/51",
                "releases": ["breaking.bad.s01e01.720p.bluray.x264-reward"],
                "language_code": "eng",
                "season": 1,
                "episode": 1,
            },
        )
        # The record without season numbers scores on the series alone.
        self.assertEqual(results[1]["id"], "subsdump-53-eng")
        self.assertEqual(results[1]["matches"], ["series"])

    def test_episode_title_fallback_params_are_sent(self):
        calls = []
        provider = self._provider_with_responses(
            {
                INFO_URL: _info_body(),
                EPISODE_FALLBACK_URL: _fixture_bytes("subsdump_search_breaking_bad_s01e01.json"),
            },
            calls,
        )
        video = _episode_video()
        del video["series_imdb_id"]

        provider.search(video, [{"alpha3": "eng"}], CONFIG)

        path, params = _sent_request(calls[-1][0])
        self.assertEqual(path, "/api/v1/subtitles")
        self.assertEqual(
            params,
            {
                "language": "eng",
                "per_page": "100",
                "release": "breaking.bad.s01e01.720p.bluray.x264-reward.mkv",
                "title": "Breaking Bad",
                "media_type": "episode",
                "season": "1",
                "episode": "1",
            },
        )
        # Upstream sends no year for the episode fallback.
        self.assertNotIn("year", params)

    def test_brazilian_portuguese_normalization(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            POR_BR_SEARCH_URL: _fixture_bytes("subsdump_search_dune_2021_por_br.json"),
        })

        results = provider.search(
            _movie_video(), [{"alpha3": "por", "country_alpha2": "BR"}], CONFIG
        )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["id"], "subsdump-61-por-BR")
        self.assertEqual(
            result["language"],
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR", "hi": False, "forced": False},
        )
        self.assertEqual(result["provider_payload"]["language_code"], "por-BR")

    def test_search_failure_propagates(self):
        provider = self.mod.SubsDumpProvider()

        def stub(url, api_key=None, timeout=None, max_bytes=None):
            del api_key, timeout, max_bytes, url
            raise RuntimeError("service broken")

        provider._http_get = stub
        with self.assertRaises(RuntimeError):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)


class WorkerContractTests(SubsDumpTestCase):
    """Pin the call shape the worker uses, so a parameter rename cannot
    silently keep every positional test green while the host fails."""

    def test_search_accepts_worker_keyword_arguments(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            MOVIE_SEARCH_URL: _fixture_bytes("subsdump_search_dune_2021.json"),
        })

        results = provider.search(
            video=_movie_video(), languages=[{"alpha3": "eng"}], config=CONFIG
        )

        self.assertEqual(
            [result["id"] for result in results],
            ["subsdump-42-eng", "subsdump-46-eng"],
        )

    def test_download_accepts_worker_keyword_arguments(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            CONTENT_URL: SRT_BYTES,
        })

        content = provider.download(
            provider_payload={
                "provider": "subsdump",
                "schema": 1,
                "subtitle_id": 42,
                "content_path": "/api/v1/subtitles/42/content",
                "page_path": "/subtitles/42",
                "releases": ["Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX"],
                "language_code": "eng",
            },
            language={"alpha3": "eng"},
            config=CONFIG,
        )

        self.assertEqual(base64.b64decode(content["content_b64"]), SRT_NORMALIZED)

    def test_changing_the_api_key_reruns_the_identity_check(self):
        # The identity cache is keyed on the base_url and api_key pair, so a
        # new key must re-verify the service before it is trusted.
        calls = []
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            MOVIE_SEARCH_URL: _fixture_bytes("subsdump_search_dune_2021.json"),
        }, calls)

        provider.search(
            _movie_video(), [{"alpha3": "eng"}],
            {"base_url": BASE_URL, "api_key": "key-one"},
        )
        provider.search(
            _movie_video(), [{"alpha3": "eng"}],
            {"base_url": BASE_URL, "api_key": "key-two"},
        )

        info_keys = [api_key for url, api_key in calls if url == INFO_URL]
        self.assertEqual(info_keys, ["key-one", "key-two"])


class DownloadTests(SubsDumpTestCase):
    def _payload(self, **overrides):
        payload = {
            "provider": "subsdump",
            "schema": 1,
            "subtitle_id": 42,
            "content_path": "/api/v1/subtitles/42/content",
            "page_path": "/subtitles/42",
            "releases": ["Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX"],
            "language_code": "eng",
        }
        payload.update(overrides)
        return payload

    def _provider_with_content(self, body, **payload_overrides):
        return self._provider_with_responses({
            INFO_URL: _info_body(),
            CONTENT_URL: body,
        }), self._payload(**payload_overrides)

    def test_content_download_normalizes_and_hashes(self):
        provider, payload = self._provider_with_content(SRT_BYTES)

        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        data = base64.b64decode(content["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, SRT_NORMALIZED)
        self.assertEqual(content["content_sha256"], hashlib.sha256(SRT_NORMALIZED).hexdigest())
        self.assertEqual(content["format"], "srt")
        self.assertEqual(content["content_type"], "application/x-subrip")
        self.assertFalse(content["empty"])
        self.assertNotIn("encoding", content)
        self.assertNotIn("test-key", json.dumps(content))

    def test_format_is_detected_from_the_bytes(self):
        cases = {
            b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n": "vtt",
            b"[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n": "ass",
            b"[Script Info]\nScriptType: v4.00\n\n[V4 Styles]\n": "ssa",
            b"{1}{1}Hi": "sub",
        }
        for body, expected_format in cases.items():
            provider, payload = self._provider_with_content(body)
            content = provider.download(payload, {"alpha3": "eng"}, CONFIG)
            self.assertEqual(content["format"], expected_format, body)
        provider, payload = self._provider_with_content(b"\xef\xbb\xbfWEBVTT\n\nHi\n")
        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)
        self.assertEqual(content["format"], "vtt")

    def test_a_ssa_release_name_breaks_the_script_info_tie(self):
        # [Script Info] opens both SSA and ASS files: a .ssa release name
        # counts as the hint when the styles section is out of the window.
        provider, payload = self._provider_with_content(
            b"[Script Info]\nScriptType: v4.00\n\n",
            releases=["Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.ssa"],
        )
        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)
        self.assertEqual(content["format"], "ssa")

    def test_utf16_content_is_returned_unchanged(self):
        # Byte-level CRLF replacement inside a UTF-16 stream rewrites its
        # characters: U+4E0D became U+4E0A and the line breaks doubled before
        # the NUL guard. The host's chardet and normalize do the real decoding.
        body = "1\r\n00:00:01,000 --> 00:00:02,000\r\n\u4e0d\r\n".encode("utf-16")
        provider, payload = self._provider_with_content(body)

        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        data = base64.b64decode(content["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, body)

    def test_an_html_comment_page_is_rejected(self):
        # A complete HTML document can open with a comment instead of a tag.
        provider, payload = self._provider_with_content(
            b"<!-- maintenance -->\n<html><body>be right back</body></html>"
        )
        with self.assertRaisesRegex(ValueError, "HTML"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)

    def test_srt_with_literal_html_text_is_accepted(self):
        # Literal "<html>" text inside a cue is not markup; searching the
        # whole sniff window for markup refused subtitles like this one.
        provider, payload = self._provider_with_content(
            b"1\n00:00:01,000 --> 00:00:02,000\nThe <html> tag is literal.\n"
        )

        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        self.assertEqual(content["format"], "srt")
        self.assertIn(b"The <html> tag is literal.", base64.b64decode(content["content_b64"]))

    def test_an_archive_with_html_text_in_its_header_is_archive_mode(self):
        # Archive magic is sniffed before the HTML check, so markup-like
        # text inside an archive's early bytes cannot refuse the archive.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "Dune.<html>.2021.srt",
                b"1\n00:00:01,000 --> 00:00:02,000\nHi\n",
            )
        body = buffer.getvalue()
        self.assertIn(b"<html", body[:512])

        provider, payload = self._provider_with_content(body)
        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        self.assertIn("archive_b64", content)
        self.assertEqual(base64.b64decode(content["archive_b64"]), body)

    def test_utf16_formats_are_detected(self):
        # A UTF-16 body is decoded for the sniff alone, so its real format
        # is reported instead of falling through to srt.
        cases = {
            "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n": "ass",
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n": "vtt",
        }
        for text, expected_format in cases.items():
            with self.subTest(text=text):
                provider, payload = self._provider_with_content(text.encode("utf-16"))
                content = provider.download(payload, {"alpha3": "eng"}, CONFIG)
                self.assertEqual(content["format"], expected_format)
                self.assertEqual(
                    base64.b64decode(content["content_b64"]), text.encode("utf-16")
                )

    def test_utf16_html_page_is_rejected(self):
        body = "<!-- maintenance -->\n<html><body>be right back</body></html>".encode(
            "utf-16"
        )
        provider, payload = self._provider_with_content(body)
        with self.assertRaisesRegex(ValueError, "HTML"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)

    def test_format_falls_back_to_release_extension_then_srt(self):
        provider, payload = self._provider_with_content(
            b"plain text without signature\n", releases=["Dune.2021.1080p.WEBRip.ass"]
        )
        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)
        self.assertEqual(content["format"], "ass")

        provider, payload = self._provider_with_content(b"plain text without signature\n")
        content = provider.download(payload, {"alpha3": "eng"}, CONFIG)
        self.assertEqual(content["format"], "srt")

    def test_zip_download_returns_archive_mode(self):
        zip_body = _zip_bytes({"dune.2021.1080p.srt": SRT_BYTES})
        provider, payload = self._provider_with_content(zip_body, episode=1)

        archive = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        self.assertEqual(set(archive), {"archive_b64", "archive_sha256", "episode"})
        self.assertEqual(archive["episode"], 1)
        raw = base64.b64decode(archive["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), archive["archive_sha256"])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(raw)))
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            self.assertEqual(bundle.namelist(), ["dune.2021.1080p.srt"])
        # The host extracts and detects the encoding: the worker sets none.
        self.assertNotIn("encoding", archive)
        self.assertNotIn("member", archive)

    def test_rar_download_returns_archive_mode(self):
        rar_body = b"Rar!\x1a\x07\x00" + b"\x00" * 64
        provider, payload = self._provider_with_content(rar_body, episode=3)

        archive = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        self.assertEqual(archive["episode"], 3)
        raw = base64.b64decode(archive["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(raw, rar_body)

    def test_movie_archive_has_no_episode(self):
        zip_body = _zip_bytes({"dune.2021.1080p.srt": SRT_BYTES})
        provider, payload = self._provider_with_content(zip_body)

        archive = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        self.assertIsNone(archive["episode"])

    def test_html_body_is_rejected(self):
        provider, payload = self._provider_with_content(
            b"<!DOCTYPE html><html><body>login</body></html>"
        )
        with self.assertRaisesRegex(ValueError, "HTML"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)

    def test_empty_body_is_rejected(self):
        provider, payload = self._provider_with_content(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)
        provider, payload = self._provider_with_content(b"  \n")
        with self.assertRaisesRegex(ValueError, "empty"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)

    def test_absolute_content_path_is_rejected(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            "https://evil.test/api/v1/subtitles/42/content": SRT_BYTES,
        })
        with self.assertRaisesRegex(ValueError, "content path"):
            provider.download(
                self._payload(content_path="https://evil.test/api/v1/subtitles/42/content"),
                {"alpha3": "eng"},
                CONFIG,
            )

    def test_tampered_content_path_is_rejected(self):
        provider = self._provider_with_responses({INFO_URL: _info_body()})
        for path in (
            "/api/v1/subtitles/42/content?token=x",
            "/api/v1/subtitles/42/content\n",
            "api/v1/subtitles/42/content",
            "/api/v1/subtitles/../../admin/content",
            "/api/v1/subtitles/abc/content",
            None,
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "content path"):
                    provider.download(
                        self._payload(content_path=path), {"alpha3": "eng"}, CONFIG
                    )

    def test_payload_of_another_provider_is_rejected(self):
        provider = self._provider_with_responses({
            INFO_URL: _info_body(),
            CONTENT_URL: SRT_BYTES,
        })
        with self.assertRaisesRegex(ValueError, "another provider"):
            provider.download(
                self._payload(provider="vladoon"), {"alpha3": "eng"}, CONFIG
            )

    def test_missing_content_path_is_rejected(self):
        provider = self._provider_with_responses({INFO_URL: _info_body()})
        with self.assertRaisesRegex(ValueError, "content path"):
            provider.download({}, {"alpha3": "eng"}, CONFIG)


class HttpErrorTests(SubsDumpTestCase):
    def _provider_with_http_error(self, error):
        provider = self.mod.SubsDumpProvider()
        self._patch_transport(lambda request: _Response(_info_body()), error=error)
        return provider

    def test_rejected_api_key_is_an_authentication_error(self):
        for code in (401, 403):
            with self.subTest(code=code):
                provider = self._provider_with_http_error(
                    urllib.error.HTTPError(INFO_URL, code, "denied", None, None)
                )
                with self.assertRaisesRegex(self.mod.AuthenticationError, "API key"):
                    provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_service_not_ready_is_a_service_unavailable_error(self):
        provider = self._provider_with_http_error(
            urllib.error.HTTPError(INFO_URL, 503, "unavailable", None, None)
        )
        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "not ready"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_other_http_statuses_are_errors(self):
        provider = self._provider_with_http_error(
            urllib.error.HTTPError(INFO_URL, 500, "boom", None, None)
        )
        with self.assertRaisesRegex(RuntimeError, "status 500"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_transport_failures_are_errors(self):
        provider = self._provider_with_http_error(urllib.error.URLError("dns failed"))
        with self.assertRaisesRegex(RuntimeError, "request failed"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_a_tls_failure_is_a_configuration_error(self):
        # A TLS failure says the URL or its scheme is wrong for this host,
        # which reads as a settings problem, not a transient service one.
        provider = self._provider_with_http_error(
            urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        )
        with self.assertRaisesRegex(self.mod.ConfigurationError, "TLS"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_download_auth_rejection_is_an_authentication_error(self):
        provider = self._provider_with_http_error(
            urllib.error.HTTPError(CONTENT_URL, 401, "denied", None, None)
        )
        payload = {
            "provider": "subsdump",
            "content_path": "/api/v1/subtitles/42/content",
        }
        with self.assertRaisesRegex(self.mod.AuthenticationError, "API key"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)

    def test_api_key_header_is_sent_when_configured(self):
        provider = self.mod.SubsDumpProvider()
        requests = self._patch_transport(lambda request: _Response(_info_body()))

        provider._http_get(INFO_URL, api_key="secret-key")

        self.assertEqual(requests[-1].get_header("X-api-key"), "secret-key")

    def test_no_api_key_header_without_a_key(self):
        provider = self.mod.SubsDumpProvider()
        requests = self._patch_transport(lambda request: _Response(_info_body()))

        provider._http_get(INFO_URL)

        self.assertIsNone(requests[-1].get_header("X-api-key"))


class SuccessStatusTests(SubsDumpTestCase):
    """Only plain 200 is a success: urllib raises from 400 up, so other 2xx
    codes used to be consumed silently."""

    def test_a_201_json_reply_raises(self):
        provider = self.mod.SubsDumpProvider()
        self._patch_transport(lambda request: _Response(_info_body(), status=201))

        with self.assertRaisesRegex(RuntimeError, "status 201"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

    def test_a_206_download_reply_raises(self):
        provider = self.mod.SubsDumpProvider()

        def route(request):
            if request.full_url == INFO_URL:
                return _Response(_info_body())
            return _Response(SRT_BYTES, status=206)

        self._patch_transport(route)
        payload = {
            "provider": "subsdump",
            "content_path": "/api/v1/subtitles/42/content",
        }

        with self.assertRaisesRegex(RuntimeError, "status 206"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)


class RedirectLeakTests(SubsDumpTestCase):
    """A 3xx from the instance must fail loudly, and the host the Location
    names must never receive the API key."""

    def test_cross_origin_redirect_fails_and_keeps_the_key_home(self):
        foreign_hits = []

        class ForeignHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802, http.server API
                foreign_hits.append(self.headers.get("X-API-Key"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        foreign = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ForeignHandler)
        foreign_url = f"http://127.0.0.1:{foreign.server_address[1]}"
        foreign.daemon_threads = True
        # Cleanups run in reverse registration order, so the close is
        # registered first and the shutdown (with its poll wait) runs first.
        self.addCleanup(foreign.server_close)
        threading.Thread(target=foreign.serve_forever, daemon=True).start()
        self.addCleanup(foreign.shutdown)

        class InstanceHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802, http.server API
                self.send_response(302)
                self.send_header("Location", f"{foreign_url}/api/v1/info")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        instance = http.server.ThreadingHTTPServer(("127.0.0.1", 0), InstanceHandler)
        instance.daemon_threads = True
        # Cleanups run in reverse registration order, so the close is
        # registered first and the shutdown (with its poll wait) runs first.
        self.addCleanup(instance.server_close)
        threading.Thread(target=instance.serve_forever, daemon=True).start()
        self.addCleanup(instance.shutdown)

        config = {
            "base_url": f"http://127.0.0.1:{instance.server_address[1]}",
            "api_key": "secret-key",
        }
        provider = self.mod.SubsDumpProvider()

        with self.assertRaisesRegex(RuntimeError, "status 302"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], config)

        # The redirect was never followed, so the foreign host saw no request
        # at all, let alone one carrying the key.
        self.assertEqual(foreign_hits, [])


class ResponseBoundsTests(SubsDumpTestCase):
    def _provider_with_body(self, body):
        provider = self.mod.SubsDumpProvider()

        def route(request):
            if request.full_url == INFO_URL:
                return _Response(_info_body())
            return _Response(body)

        self._patch_transport(route)
        return provider

    def test_search_response_is_bounded(self):
        # Valid JSON with whitespace padding above the cap: without the
        # bound this parses cleanly, so the size error is the only failure.
        padding = b'{"subtitles": []} '
        body = padding + b" " * (self.mod.MAX_SEARCH_BYTES - len(padding))
        self.assertEqual(len(body), self.mod.MAX_SEARCH_BYTES)
        oversized = body + b" "
        self.assertEqual(len(oversized), self.mod.MAX_SEARCH_BYTES + 1)

        provider = self._provider_with_body(oversized)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG)

        provider = self._provider_with_body(body)
        self.assertEqual(provider.search(_movie_video(), [{"alpha3": "eng"}], CONFIG), [])

    def test_download_response_is_bounded(self):
        content = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
        body = content + b" " * (self.mod.MAX_DOWNLOAD_BYTES - len(content))
        self.assertEqual(len(body), self.mod.MAX_DOWNLOAD_BYTES)
        oversized = body + b" "

        payload = {
            "provider": "subsdump",
            "content_path": "/api/v1/subtitles/42/content",
        }
        provider = self._provider_with_body(oversized)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            provider.download(payload, {"alpha3": "eng"}, CONFIG)

        provider = self._provider_with_body(body)
        result = provider.download(payload, {"alpha3": "eng"}, CONFIG)
        self.assertEqual(result["format"], "srt")


if __name__ == "__main__":
    unittest.main()
