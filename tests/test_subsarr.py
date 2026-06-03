import base64
import hashlib
import importlib.util
import json
import urllib.parse
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsarr"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subsarr_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOVIE_IMDB = json.loads((FIXTURE_DIR / "subsarr_search_movie_imdb.json").read_text())
EMPTY = json.loads((FIXTURE_DIR / "subsarr_search_empty.json").read_text())
EPISODE_QUERY = json.loads((FIXTURE_DIR / "subsarr_search_episode_query.json").read_text())


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


class SubsarrProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_base_url_is_required_and_must_include_scheme(self):
        provider = self.mod.SubsarrProvider()

        with self.assertRaises(ValueError):
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng"}], {})
        with self.assertRaises(ValueError):
            provider.search(
                {"kind": "movie", "title": "Dune"},
                [{"alpha3": "eng"}],
                {"base_url": "subsarr.local"},
            )

    def test_search_movie_uses_imdb_year_and_language_name(self):
        provider = self.mod.SubsarrProvider()
        calls = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            calls.append(url)
            return MOVIE_IMDB

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )

        params = _query(calls[0])
        self.assertEqual(calls[0].split("?")[0], "https://subsarr.test/api/v1/subtitles/search")
        self.assertEqual(params["language"], "English")
        self.assertEqual(params["hi"], "false")
        self.assertEqual(params["imdb_id"], "tt1160419")
        self.assertEqual(params["year"], "2021")
        self.assertEqual(params["per_page"], "100")
        self.assertEqual([item["provider_payload"]["record_id"] for item in results], ["sub-1"])
        self.assertIn("title", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["language_name"], "English")

    def test_search_falls_back_to_title_query_when_imdb_returns_empty(self):
        provider = self.mod.SubsarrProvider()
        calls = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            calls.append(url)
            return EMPTY if len(calls) == 1 else MOVIE_IMDB

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )

        self.assertEqual(_query(calls[1])["query"], "Dune: Part One")
        self.assertEqual(results[0]["provider_payload"]["record_id"], "sub-1")

    def test_search_episode_includes_season_episode_and_fallback_query(self):
        provider = self.mod.SubsarrProvider()
        calls = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            calls.append(url)
            return EMPTY if len(calls) == 1 else EPISODE_QUERY

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1, "series_imdb_id": "tt7366338"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"base_url": "https://subsarr.test/", "request_delay_ms": 0},
        )

        first_params = _query(calls[0])
        fallback_params = _query(calls[1])
        self.assertEqual(first_params["season"], "1")
        self.assertEqual(first_params["episode"], "1")
        self.assertEqual(first_params["imdb_id"], "tt7366338")
        self.assertEqual(first_params["hi"], "false")
        self.assertEqual(fallback_params["query"], "Chernobyl")
        self.assertIn("episode", results[0]["matches"])

    def test_search_accepts_hi_requested_language(self):
        provider = self.mod.SubsarrProvider()
        provider._http_get_json = lambda url, timeout=30, config=None: MOVIE_IMDB

        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": True}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["record_id"] for item in results], ["sub-2"])
        self.assertTrue(results[0]["language"]["hi"])

    def test_search_sends_hi_filter_in_query(self):
        provider = self.mod.SubsarrProvider()
        calls = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            calls.append(url)
            return MOVIE_IMDB

        provider._http_get_json = stub
        provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": True}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )

        self.assertEqual(_query(calls[0])["hi"], "true")

    def test_search_preserves_base_url_path_prefix_for_downloads(self):
        provider = self.mod.SubsarrProvider()
        response = {
            "items": [
                {
                    "id": "sub-1",
                    "language": "English",
                    "hi": False,
                    "download_url": "https://subsarr.test/api/v1/subtitles/sub-1/download",
                    "title": "Dune: Part One",
                    "releases": ["Dune.2021.1080p.WEBRip.x264-RARBG"],
                    "filename": "Dune.2021.1080p.WEBRip.x264-RARBG.srt",
                }
            ]
        }
        provider._http_get_json = lambda url, timeout=30, config=None: response

        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"base_url": "https://subsarr.test/subsarr", "request_delay_ms": 0},
        )

        self.assertEqual(
            results[0]["provider_payload"]["download_url"],
            "https://subsarr.test/subsarr/api/v1/subtitles/sub-1/download",
        )

    def test_search_maps_brazilian_portuguese_to_subsarr_slug(self):
        self.assertEqual(
            self.mod.language_slug({"alpha3": "por", "country": "BR"}),
            "brazillian-portuguese",
        )

    def test_download_fetches_raw_subtitle_and_normalizes_line_endings(self):
        provider = self.mod.SubsarrProvider()
        provider._http_get_bytes = lambda url, timeout=30, config=None: (
            b"1\r\n00:00:01,000 --> 00:00:02,000\r\nMovie line\r\n"
        )

        result = provider.download(
            {
                "provider": "subsarr",
                "schema": 1,
                "record_id": "sub-1",
                "download_url": "https://subsarr.test/download/sub-1.srt",
                "filename": "Dune.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"base_url": "https://subsarr.test"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertNotIn(b"\r\n", decoded)
        self.assertIn(b"Movie line", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
