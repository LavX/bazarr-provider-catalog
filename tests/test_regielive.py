import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "regielive"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "regielive_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOVIE_JSON = (FIXTURE_DIR / "regielive_search_movie.json").read_bytes()
EPISODE_JSON = (FIXTURE_DIR / "regielive_search_episode.json").read_bytes()
EMPTY_JSON = (FIXTURE_DIR / "regielive_empty.json").read_bytes()
SEARCH_HTML = (FIXTURE_DIR / "regielive_search_dune_html.html").read_bytes()
DETAIL_HTML = (FIXTURE_DIR / "regielive_detail_dune_html.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class RegieLiveParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_query_params_include_name_and_year(self):
        params = self.mod.build_query_params({"kind": "movie", "title": "Dune", "year": 2021})
        self.assertEqual(params, {"nume": "Dune", "an": "2021"})

    def test_episode_query_params_include_series_season_episode_and_year(self):
        params = self.mod.build_query_params(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 1,
                "episode": 1,
                "year": 2008,
            }
        )
        self.assertEqual(
            params,
            {"nume": "Breaking Bad", "sezon": "1", "episod": "1", "an": "2008"},
        )

    def test_episode_query_params_collapse_multi_episode_list(self):
        params = self.mod.build_query_params(
            {
                "kind": "episode",
                "series": "The Office",
                "season": 1,
                "episode": [2, 1],
                "year": 2005,
            }
        )
        self.assertEqual(
            params,
            {"nume": "The Office", "sezon": "1", "episod": "1", "an": "2005"},
        )

    def test_parse_search_results_flattens_nested_subtitles(self):
        rows = self.mod.parse_search_results(MOVIE_JSON)
        self.assertEqual([row["subtitle_id"] for row in rows], ["2573535", "2573536"])
        self.assertEqual(rows[0]["title"], "Dune.2021.1080p.BluRay.x264")
        self.assertEqual(rows[0]["download_url"], "https://subtitrari.regielive.ro/download/2573535")
        self.assertEqual(rows[0]["rating"], 8.7)

    def test_parse_search_results_accepts_empty_or_missing_results(self):
        self.assertEqual(self.mod.parse_search_results(EMPTY_JSON), [])
        self.assertEqual(self.mod.parse_search_results(b"[]"), [])
        self.assertEqual(self.mod.parse_search_results(b"{}"), [])

    def test_parse_search_results_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            self.mod.parse_search_results(b"<html>not json</html>")

    def test_parse_html_search_results_filters_movie_title_and_year(self):
        rows = self.mod.parse_html_search_results(SEARCH_HTML, {"kind": "movie", "title": "Dune", "year": 2021})

        self.assertEqual(
            rows,
            [
                {
                    "title": "Dune",
                    "url": "https://subtitrari.regielive.ro/dune-39590/",
                    "year": "2021",
                    "kind": "movie",
                }
            ],
        )

    def test_parse_html_detail_results_extracts_download_rows(self):
        rows = self.mod.parse_html_detail_results(DETAIL_HTML, "https://subtitrari.regielive.ro/dune-39590/")

        self.assertEqual([row["subtitle_id"] for row in rows], ["503896", "503898"])
        self.assertEqual(rows[0]["title"], "Dune 2021 1080p HDRip X264 AC3-EVO")
        self.assertEqual(rows[0]["download_url"], "https://subtitrari.regielive.ro/descarca-39590-503896.zip")
        self.assertEqual(rows[0]["rating"], 4.34)
        self.assertEqual(rows[1]["download_url"], "https://subtitrari.regielive.ro/descarca-39590-503898.zip")


class RegieLiveProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_api_and_returns_movie_result(self):
        provider = self.mod.RegieLiveProvider()
        calls = []

        def stub(url, headers=None, timeout=15, referer=None):
            del timeout, referer
            calls.append((url, headers))
            return MOVIE_JSON

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "release_group": "x264"},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(
            calls[0][0],
            "https://api.regielive.ro/bazarr/search.php?nume=Dune&an=2021",
        )
        self.assertEqual(calls[0][1]["RL-API"], "API-BAZARR-YTZ-SL")
        first = results[0]
        self.assertEqual(first["provider"], "regielive")
        self.assertEqual(first["language"], {"alpha3": "ron", "alpha2": "ro", "hi": False, "forced": False})
        self.assertIn("title", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertIn("release_group", first["matches"])
        self.assertEqual(first["provider_payload"]["download_url"], "https://subtitrari.regielive.ro/download/2573535")

    def test_search_falls_back_to_html_when_api_rejects_request(self):
        provider = self.mod.RegieLiveProvider()
        calls = []

        def stub(url, headers=None, timeout=15, referer=None):
            del headers, timeout, referer
            calls.append(url)
            if url == "https://api.regielive.ro/bazarr/search.php?nume=Dune&an=2021":
                raise RuntimeError("regielive rejected the request")
            if url == "https://subtitrari.regielive.ro/cauta.html?s=Dune":
                return SEARCH_HTML
            if url == "https://subtitrari.regielive.ro/dune-39590/":
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "release_group": "EVO"},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(
            calls,
            [
                "https://api.regielive.ro/bazarr/search.php?nume=Dune&an=2021",
                "https://subtitrari.regielive.ro/cauta.html?s=Dune",
                "https://subtitrari.regielive.ro/dune-39590/",
            ],
        )
        self.assertGreaterEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["provider"], "regielive")
        self.assertEqual(first["provider_payload"]["subtitle_id"], "503896")
        self.assertEqual(first["provider_payload"]["download_url"], "https://subtitrari.regielive.ro/descarca-39590-503896.zip")
        self.assertIn("release_group", first["matches"])

    def test_search_returns_only_romanian(self):
        provider = self.mod.RegieLiveProvider()

        def stub(url, headers=None, timeout=15, referer=None):
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Dune", "year": 2021},
                [{"alpha3": "eng", "alpha2": "en"}],
                {},
            ),
            [],
        )

    def test_episode_search_returns_episode_matches(self):
        provider = self.mod.RegieLiveProvider()

        def stub(url, headers=None, timeout=15, referer=None):
            del headers, timeout, referer
            self.assertEqual(
                url,
                "https://api.regielive.ro/bazarr/search.php?nume=Breaking+Bad&sezon=1&episod=1&an=2008",
            )
            return EPISODE_JSON

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "year": 2008},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results[0]["release_info"], "Breaking.Bad.S01E01.HDTV.XviD-FQM")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_html_fallback_follows_requested_season(self):
        provider = self.mod.RegieLiveProvider()
        search_html = (
            b"<ul id=\"lista-filme\"><li>"
            b"<div class=\"tags-imagini\"><a class=\"tag-serial\">Serial</a></div>"
            b"<h2><a href=\"https://subtitrari.regielive.ro/the-office-1234/\""
            b" class=\"text-xl\">The Office</a>"
            b"<span>(2005)</span></h2>"
            b"</li></ul>"
        )
        # The serial root page renders the latest season (S05); only the
        # sezonul-1 page has the requested S01E01 rows.
        root_detail_html = (
            b"<ul><li class=\"subtitrare\">"
            b"<span id=\"sub_900\">The.Office.S05E01.HDTV.XviD-LOL</span>"
            b"<a href=\"https://subtitrari.regielive.ro/descarca-1234-900.zip\">Descarca</a>"
            b"</li></ul>"
        )
        season_detail_html = (
            b"<ul><li class=\"subtitrare\">"
            b"<span id=\"sub_901\">The.Office.S01E01.PDTV.XviD-FQM</span>"
            b"<a href=\"https://subtitrari.regielive.ro/descarca-1234-901.zip\">Descarca</a>"
            b"</li></ul>"
        )
        calls = []

        def stub(url, headers=None, timeout=15, referer=None):
            del headers, timeout, referer
            calls.append(url)
            if url.startswith("https://api.regielive.ro/"):
                raise RuntimeError("regielive rejected the request")
            if url == "https://subtitrari.regielive.ro/cauta.html?s=The+Office":
                return search_html
            if url == "https://subtitrari.regielive.ro/the-office-1234/sezonul-1/":
                return season_detail_html
            if url == "https://subtitrari.regielive.ro/the-office-1234/":
                return root_detail_html
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": 1, "year": 2005},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"request_delay_ms": 0},
        )

        self.assertIn(
            "https://subtitrari.regielive.ro/the-office-1234/sezonul-1/", calls
        )
        self.assertNotIn("https://subtitrari.regielive.ro/the-office-1234/", calls)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "901")
        self.assertEqual(
            results[0]["release_info"], "The.Office.S01E01.PDTV.XviD-FQM"
        )

    def test_search_skips_forced_only_and_hi_only_romanian(self):
        provider = self.mod.RegieLiveProvider()

        def stub(url, headers=None, timeout=15, referer=None):
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Dune", "year": 2021},
                [{"alpha3": "ron", "alpha2": "ro", "forced": True}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Dune", "year": 2021},
                [{"alpha3": "ron", "alpha2": "ro", "hi": True}],
                {},
            ),
            [],
        )

    def test_download_visits_landing_page_then_downloads_zip_and_extracts_subtitle(self):
        provider = self.mod.RegieLiveProvider()
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nSalut\r\n"
        archive = _zip_body({"readme.txt": b"not a subtitle", "Dune.2021.srt": body})
        calls = []

        def stub(url, headers=None, timeout=15, referer=None):
            del headers, timeout
            calls.append((url, referer))
            if url == "https://subtitrari.regielive.ro":
                return b"ok"
            if url == "https://subtitrari.regielive.ro/download/2573535":
                return archive
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        result = provider.download(
            {"download_url": "https://subtitrari.regielive.ro/download/2573535"},
            {"alpha3": "ron", "alpha2": "ro"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(
            calls,
            [
                ("https://subtitrari.regielive.ro", None),
                ("https://subtitrari.regielive.ro/download/2573535", "https://subtitrari.regielive.ro"),
            ],
        )
        self.assertEqual(base64.b64decode(result["content_b64"]), body.replace(b"\r\n", b"\n"))
        self.assertEqual(result["content_sha256"], hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest())
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertEqual(result["format"], "srt")

    def test_download_skips_hidden_and_txt_files(self):
        provider = self.mod.RegieLiveProvider()
        archive = _zip_body(
            {
                ".hidden.srt": b"hidden",
                "notes.txt": b"notes",
                "Season/file.ass": b"[Script Info]\r\nTitle: Test\r\n",
            }
        )

        def stub(url, headers=None, timeout=15, referer=None):
            del headers, timeout, referer
            return b"ok" if url == "https://subtitrari.regielive.ro" else archive

        provider._http_get = stub
        result = provider.download(
            {"download_url": "https://subtitrari.regielive.ro/download/ass"},
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["format"], "ass")
        self.assertEqual(base64.b64decode(result["content_b64"]), b"[Script Info]\nTitle: Test\n")

    def test_download_raises_for_server_500_body(self):
        provider = self.mod.RegieLiveProvider()

        def stub(url, headers=None, timeout=15, referer=None):
            del headers, timeout, referer
            return b"ok" if url == "https://subtitrari.regielive.ro" else b"500"

        provider._http_get = stub
        with self.assertRaises(ValueError):
            provider.download(
                {"download_url": "https://subtitrari.regielive.ro/download/2573535"},
                {"alpha3": "ron", "alpha2": "ro"},
                {},
            )
