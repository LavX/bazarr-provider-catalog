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

    def test_search_movie_uses_imdb_year_and_language_slug(self):
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
        # Subsarr stores and filters the lowercase-hyphenated Subscene slug, so the
        # outbound language param must be the slug, not the title-cased display name.
        self.assertEqual(params["language"], "english")
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

    def test_search_uses_exact_subsarr_slug_for_tricky_languages(self):
        # Subsarr filters on the stored slug. Title-casing the slug (Farsi Persian,
        # Chinese Bg Code, Ukranian) would never match the database, so the outbound
        # language param must be the exact lowercase-hyphenated slug.
        provider = self.mod.SubsarrProvider()
        cases = {"fas": "farsi_persian", "zho": "chinese-bg-code", "ukr": "ukranian"}
        for alpha3, expected_slug in cases.items():
            calls = []

            def stub(url, timeout=30, config=None):
                del timeout, config
                calls.append(url)
                return EMPTY

            provider._http_get_json = stub
            provider.search(
                {"kind": "movie", "title": "Some Movie", "year": 2020, "imdb_id": "tt9"},
                [{"alpha3": alpha3, "hi": False}],
                {"base_url": "https://subsarr.test", "request_delay_ms": 0},
            )
            self.assertEqual(_query(calls[0])["language"], expected_slug)

    def test_country_alpha2_payload_resolves_brazilian_portuguese(self):
        # Bazarr passes the regional payload {"alpha3": "por", "country_alpha2": "BR"}.
        self.assertEqual(self.mod._country({"alpha3": "por", "country_alpha2": "BR"}), "BR")
        requested = self.mod._requested_languages([{"alpha3": "por", "country_alpha2": "BR"}])
        self.assertEqual(requested[0]["slug"], "brazillian-portuguese")

    def test_search_sends_brazilian_portuguese_slug_for_country_alpha2(self):
        provider = self.mod.SubsarrProvider()
        calls = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            calls.append(url)
            return EMPTY

        provider._http_get_json = stub
        provider.search(
            {"kind": "movie", "title": "Cidade de Deus", "year": 2002, "imdb_id": "tt0317248"},
            [{"alpha3": "por", "country_alpha2": "BR", "hi": False}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )
        self.assertEqual(_query(calls[0])["language"], "brazillian-portuguese")

    def test_result_preserves_brazilian_portuguese_country_marker(self):
        provider = self.mod.SubsarrProvider()
        response = {
            "items": [
                {
                    "id": "br-1",
                    "language": "brazillian-portuguese",
                    "hi": False,
                    "download_url": "https://subsarr.test/api/v1/subtitles/br-1/download",
                    "title": "Cidade de Deus",
                    "releases": ["Cidade.de.Deus.2002.1080p"],
                    "filename": "Cidade.de.Deus.2002.1080p.srt",
                }
            ]
        }
        provider._http_get_json = lambda url, timeout=30, config=None: response
        results = provider.search(
            {"kind": "movie", "title": "Cidade de Deus", "year": 2002, "imdb_id": "tt0317248"},
            [{"alpha3": "por", "country_alpha2": "BR", "hi": False}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )
        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")
        self.assertEqual(results[0]["provider_payload"]["country_alpha2"], "BR")

    def test_search_skips_forced_only_requests(self):
        provider = self.mod.SubsarrProvider()
        calls = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            calls.append(url)
            return MOVIE_IMDB

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "forced": True}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )
        self.assertEqual(results, [])
        self.assertEqual(calls, [])

    def test_search_paginates_to_find_non_hi_on_later_pages(self):
        # Subsarr ignores hi=false server-side, so HI rows can fill the first page.
        # The provider must paginate before applying the local HI filter, otherwise
        # the non-HI subtitle on page two is never seen.
        provider = self.mod.SubsarrProvider()
        per_page = self.mod.PER_PAGE
        page_one = [
            {
                "id": f"hi-{index}",
                "language": "english",
                "hi": True,
                "download_url": f"https://subsarr.test/api/v1/subtitles/hi-{index}/download",
                "title": "Dune: Part One",
                "releases": ["Dune.2021.1080p"],
                "filename": "Dune.2021.1080p.HI.srt",
            }
            for index in range(per_page)
        ]
        page_two = [
            {
                "id": "normal-1",
                "language": "english",
                "hi": False,
                "download_url": "https://subsarr.test/api/v1/subtitles/normal-1/download",
                "title": "Dune: Part One",
                "releases": ["Dune.2021.1080p"],
                "filename": "Dune.2021.1080p.srt",
            }
        ]
        pages = []

        def stub(url, timeout=30, config=None):
            del timeout, config
            page = int(_query(url).get("page", "1"))
            pages.append(page)
            return {"items": page_one if page == 1 else page_two}

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"base_url": "https://subsarr.test", "request_delay_ms": 0},
        )
        self.assertIn(2, pages)
        self.assertEqual([item["provider_payload"]["record_id"] for item in results], ["normal-1"])

    def test_hi_request_filters_server_side_via_hi_param(self):
        # When hi is requested, Subsarr filters server-side; the outbound hi flag must
        # carry through so the server returns only HI rows.
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

    def test_download_url_uses_configured_scheme_behind_tls_proxy(self):
        # Subsarr can return http:// behind a TLS-terminating proxy. The stored URL
        # must reuse the configured https scheme and path prefix.
        provider = self.mod.SubsarrProvider()
        response = {
            "items": [
                {
                    "id": "sub-1",
                    "language": "english",
                    "hi": False,
                    "download_url": "http://subsarr.internal/api/v1/subtitles/sub-1/download",
                    "title": "Dune: Part One",
                    "releases": ["Dune.2021.1080p"],
                    "filename": "Dune.2021.1080p.srt",
                }
            ]
        }
        provider._http_get_json = lambda url, timeout=30, config=None: response
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"base_url": "https://subsarr.public/subsarr", "request_delay_ms": 0},
        )
        self.assertEqual(
            results[0]["provider_payload"]["download_url"],
            "https://subsarr.public/subsarr/api/v1/subtitles/sub-1/download",
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
