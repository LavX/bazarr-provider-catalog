import base64
import hashlib
import importlib.util
import io
import json
import unittest
import urllib.error
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "wizdom"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "wizdom_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_files(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class WizdomParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_movie_releases_extracts_wizdom_subs(self):
        rows = self.mod.parse_releases(
            {
                "subs": [
                    {"id": 101, "version": "Inception.2010.1080p.BluRay.x264"},
                    {"id": "102", "version": "Inception.2010.720p.BluRay.x264"},
                ]
            },
            media_type="movie",
            imdb_id="tt1375666",
            title="Inception",
        )

        self.assertEqual(rows[0]["subtitle_id"], "101")
        self.assertEqual(rows[0]["release"], "Inception.2010.1080p.BluRay.x264")
        self.assertEqual(rows[0]["page_link"], "https://wizdom.xyz/movies/tt1375666")

    def test_parse_episode_releases_supports_season_dict_shape(self):
        rows = self.mod.parse_releases(
            {
                "subs": {
                    "1": {
                        "2": [{"id": 201, "version": "Fauda.S01E02.HEBREW.WEB-DL"}],
                    }
                }
            },
            media_type="episode",
            imdb_id="tt4565380",
            title="Fauda",
            season=1,
            episode=2,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subtitle_id"], "201")

    def test_parse_episode_releases_supports_season_list_shape(self):
        rows = self.mod.parse_releases(
            {
                "subs": [
                    {},
                    {"2": [{"id": 301, "version": "Fauda.S01E02.HEBREW.HDTV"}]},
                ]
            },
            media_type="episode",
            imdb_id="tt4565380",
            title="Fauda",
            season=1,
            episode=2,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["subtitle_id"], "301")


class WizdomProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_skips_non_hebrew_requests(self):
        provider = self.mod.WizdomProvider()
        provider._http_get = lambda url, timeout=15, referer=None: self.fail(url)

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_skips_forced_or_hearing_impaired_hebrew_requests(self):
        provider = self.mod.WizdomProvider()
        provider._http_get = lambda url, timeout=15, referer=None: self.fail(url)

        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
                [{"alpha3": "heb", "alpha2": "he", "hi": True}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
                [{"alpha3": "heb", "alpha2": "he", "forced": True}],
                {},
            ),
            [],
        )

    def test_search_uses_existing_imdb_id_and_returns_hebrew_results(self):
        provider = self.mod.WizdomProvider()
        requested_urls = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            requested_urls.append(url)
            self.assertEqual(url, "https://wizdom.xyz/api/releases/tt1375666")
            return json.dumps(
                {"subs": [{"id": 101, "version": "Inception.2010.1080p.BluRay.x264"}]}
            ).encode("utf-8")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "heb", "alpha2": "he"}],
            {},
        )

        self.assertEqual(requested_urls, ["https://wizdom.xyz/api/releases/tt1375666"])
        self.assertEqual(results[0]["provider"], "wizdom")
        self.assertEqual(results[0]["language"]["alpha3"], "heb")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "101")
        self.assertIn("title", results[0]["matches"])

    def test_search_resolves_missing_movie_imdb_with_tmdb(self):
        provider = self.mod.WizdomProvider()
        responses = {
            "https://api.tmdb.org/3/search/movie?api_key="
            "a51ee051bcd762543373903de296e0a3&query=Inception&language=en&year=2010": {
                "results": [{"id": 27205}]
            },
            "https://api.tmdb.org/3/movie/27205?api_key="
            "a51ee051bcd762543373903de296e0a3&language=en": {
                "imdb_id": "tt1375666"
            },
            "https://wizdom.xyz/api/releases/tt1375666": {
                "subs": [{"id": 101, "version": "Inception.2010.1080p.BluRay.x264"}]
            },
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return json.dumps(responses[url]).encode("utf-8")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "heb", "alpha2": "he"}],
            {},
        )

        self.assertEqual(results[0]["provider_payload"]["imdb_id"], "tt1375666")

    def test_search_resolves_missing_episode_series_imdb_with_tmdb(self):
        provider = self.mod.WizdomProvider()
        responses = {
            "https://api.tmdb.org/3/search/tv?api_key="
            "a51ee051bcd762543373903de296e0a3&query=Fauda&language=en&year=2015": {
                "results": [{"id": 62286}]
            },
            "https://api.tmdb.org/3/tv/62286/external_ids?api_key="
            "a51ee051bcd762543373903de296e0a3&language=en": {
                "imdb_id": "tt4565380"
            },
            "https://wizdom.xyz/api/releases/tt4565380": {
                "subs": {
                    "1": {
                        "2": [{"id": 201, "version": "Fauda.S01E02.HEBREW.WEB-DL"}],
                    }
                }
            },
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return json.dumps(responses[url]).encode("utf-8")

        provider._http_get = stub
        results = provider.search(
            {
                "episode": 2,
                "imdb_id": "tt9999999",
                "kind": "episode",
                "season": 1,
                "series": "Fauda",
                "year": 2015,
            },
            [{"alpha3": "heb", "alpha2": "he"}],
            {},
        )

        self.assertEqual(results[0]["provider_payload"]["imdb_id"], "tt4565380")
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_treats_wizdom_http_500_as_no_results(self):
        provider = self.mod.WizdomProvider()

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            raise urllib.error.HTTPError(url, 500, "Server error", {}, io.BytesIO(b""))

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Missing", "year": 2024, "imdb_id": "tt0000000"},
            [{"alpha3": "heb", "alpha2": "he"}],
            {},
        )

        self.assertEqual(results, [])

    def test_download_extracts_zip_subtitle_and_normalizes_line_endings(self):
        provider = self.mod.WizdomProvider()
        body = _zip_files(
            {"Inception.he.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"}
        )
        provider._http_get = lambda url, timeout=10, referer=None: body

        result = provider.download(
            {
                "provider": "wizdom",
                "schema": 1,
                "subtitle_id": "101",
                "page_link": "https://wizdom.xyz/movies/tt1375666",
                "filename": "wizdom.inception.he.zip",
            },
            {"alpha3": "heb", "alpha2": "he"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_tries_next_archive_member_when_first_is_not_subtitle_text(self):
        provider = self.mod.WizdomProvider()
        body = _zip_files(
            {
                "bad.srt": b"not a subtitle",
                "good.srt": b"1\n00:00:01,000 --> 00:00:02,000\nGood line\n",
            }
        )
        provider._http_get = lambda url, timeout=10, referer=None: body

        result = provider.download(
            {
                "provider": "wizdom",
                "schema": 1,
                "subtitle_id": "101",
                "page_link": "https://wizdom.xyz/movies/tt1375666",
                "filename": "wizdom.inception.he.zip",
            },
            {"alpha3": "heb", "alpha2": "he"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Good line", decoded)
        self.assertNotIn(b"not a subtitle", decoded)

    def test_download_empty_body_returns_empty_payload(self):
        provider = self.mod.WizdomProvider()
        provider._http_get = lambda url, timeout=10, referer=None: b""

        result = provider.download(
            {"provider": "wizdom", "schema": 1, "subtitle_id": "101"},
            {"alpha3": "heb", "alpha2": "he"},
            {},
        )

        self.assertTrue(result["empty"])
        self.assertEqual(result["content_b64"], "")
