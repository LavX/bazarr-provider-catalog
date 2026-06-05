import base64
import hashlib
import importlib.util
import io
import urllib.parse
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subf2m"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subf2m_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_DUNE = (FIXTURE_DIR / "subf2m_search_dune.html").read_bytes()
SEARCH_CHERNOBYL = (FIXTURE_DIR / "subf2m_search_chernobyl.html").read_bytes()
DETAIL_DUNE_EN = (FIXTURE_DIR / "subf2m_detail_dune_english.html").read_bytes()
DETAIL_CHERNOBYL_EN = (FIXTURE_DIR / "subf2m_detail_chernobyl_english.html").read_bytes()
DOWNLOAD_GATE_DUNE = (FIXTURE_DIR / "subf2m_download_gate_dune.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class SubF2MParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_links_and_titles(self):
        rows = self.mod.parse_search_results(SEARCH_DUNE)

        self.assertEqual(rows[2]["path"], "/subtitles/dune-2021")
        self.assertEqual(rows[2]["title"], "Dune: Part One (2021)")
        self.assertEqual(rows[2]["year"], 2021)

    def test_rank_movie_paths_prefers_matching_year_and_title(self):
        paths = self.mod.rank_movie_paths(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021},
            self.mod.parse_search_results(SEARCH_DUNE),
        )

        self.assertEqual(paths[0]["path"], "/subtitles/dune-2021")

    def test_rank_episode_paths_accepts_worded_season(self):
        paths = self.mod.rank_episode_paths(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "year": 2019},
            self.mod.parse_search_results(SEARCH_CHERNOBYL),
        )

        self.assertEqual(paths[0]["path"], "/subtitles/chernobyl")

    def test_rank_episode_paths_rejects_conflicting_year(self):
        paths = self.mod.rank_episode_paths(
            {"kind": "episode", "series": "The Office", "season": 1, "year": 2005},
            [
                {"path": "/subtitles/the-office-uk", "title": "The Office - First Season (2001)", "year": 2001, "season": 1, "index": 0},
                {"path": "/subtitles/the-office-us", "title": "The Office - First Season (2005)", "year": 2005, "season": 1, "index": 1},
            ],
        )

        self.assertEqual([path["path"] for path in paths], ["/subtitles/the-office-us"])

    def test_parse_subtitle_page_filters_episode_rows(self):
        rows = self.mod.parse_subtitle_page(
            DETAIL_CHERNOBYL_EN,
            "eng",
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
        )

        self.assertEqual([row["subtitle_id"] for row in rows], ["2647618", "2956831"])
        self.assertIn("S01E01", rows[0]["release_info"])
        self.assertIn("COMPLETE.SEASON.01", rows[1]["release_info"])

    def test_parse_subtitle_page_marks_forced_and_hi_rows(self):
        forced_rows = self.mod.parse_subtitle_page(
            DETAIL_CHERNOBYL_EN,
            "eng",
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
        )
        hi_rows = self.mod.parse_subtitle_page(
            DETAIL_DUNE_EN,
            "eng",
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
        )

        self.assertTrue(forced_rows[0]["forced"])
        self.assertFalse(forced_rows[0]["hearing_impaired"])
        self.assertTrue(hi_rows[0]["hearing_impaired"])
        self.assertFalse(hi_rows[0]["forced"])
        self.assertTrue(self.mod._looks_hearing_impaired("HI only"))

    def test_parse_subtitle_page_accepts_plain_season_pack(self):
        body = DETAIL_CHERNOBYL_EN.replace(
            b"Chernobyl.2019.COMPLETE.SEASON.01.1080p.Blu-ray.x265.10bit.AC3",
            b"Chernobyl.S01.1080p.Blu-ray.x265.10bit.AC3",
        ).replace(b"Complete season pack", b"Season pack")

        rows = self.mod.parse_subtitle_page(
            body,
            "eng",
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
        )

        self.assertIn("2956831", [row["subtitle_id"] for row in rows])

    def test_parse_subtitle_page_rejects_wrong_imdb_id(self):
        rows = self.mod.parse_subtitle_page(
            DETAIL_DUNE_EN,
            "eng",
            {"kind": "movie", "title": "Arrival", "year": 2016, "imdb_id": "tt2543164"},
        )

        self.assertEqual(rows, [])

    def test_parse_subtitle_page_only_marks_imdb_when_page_confirms_it(self):
        # The page omits the IMDb link, so an expected id from the request must
        # not inflate the row into an exact-id match.
        body = DETAIL_DUNE_EN.replace(b"imdb.com/title/tt1160419", b"example.com/no-imdb")

        rows = self.mod.parse_subtitle_page(
            body,
            "eng",
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
        )

        self.assertTrue(rows)
        self.assertTrue(all(not row["imdb_matched"] for row in rows))

        confirmed = self.mod.parse_subtitle_page(
            DETAIL_DUNE_EN,
            "eng",
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
        )

        self.assertTrue(confirmed[0]["imdb_matched"])

    def test_requested_languages_keeps_same_language_variants(self):
        requested = self.mod._requested_languages(
            [
                {"alpha3": "eng", "alpha2": "en", "forced": False},
                {"alpha3": "eng", "alpha2": "en", "forced": True},
            ]
        )

        self.assertEqual(len(requested), 2)
        self.assertEqual({meta["forced"] for meta in requested}, {False, True})

    def test_parse_download_button_extracts_absolute_download_url(self):
        url = self.mod.parse_download_url(
            DOWNLOAD_GATE_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english/3331049",
        )

        self.assertEqual(
            url,
            "https://subf2m.co/subtitles/dune-2021/english/3331049/download",
        )


class SubF2MProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_fetches_language_page_and_returns_payload(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english": DETAIL_DUNE_EN,
        }
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls, list(responses))
        self.assertEqual(results[0]["provider"], "subf2m")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertTrue(results[0]["language"]["hi"])
        self.assertTrue(results[0]["hearing_impaired"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "3331049")

    def test_search_filters_rows_by_requested_forced_flag(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Chernobyl&l=": SEARCH_CHERNOBYL,
            "https://subf2m.co/subtitles/chernobyl/english": DETAIL_CHERNOBYL_EN,
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1, "year": 2019, "series_imdb_id": "tt7366338"},
            [{"alpha3": "eng", "alpha2": "en", "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["2956831"])

    def test_search_filters_rows_by_requested_hi_flag(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/searchbytitle?query=Dune&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english": DETAIL_DUNE_EN,
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_search_episode_returns_episode_and_season_pack(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Chernobyl&l=": SEARCH_CHERNOBYL,
            "https://subf2m.co/subtitles/chernobyl/english": DETAIL_CHERNOBYL_EN,
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1, "year": 2019, "series_imdb_id": "tt7366338"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["2647618", "2956831"])
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("season", results[1]["matches"])
        self.assertTrue(results[0]["language"]["forced"])
        self.assertFalse(results[1]["language"]["forced"])

    def test_search_maps_brazilian_portuguese_country_variant(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/brazillian-portuguese": DETAIL_DUNE_EN.replace(
                b"/subtitles/dune-2021/english/3331049",
                b"/subtitles/dune-2021/brazillian-portuguese/2706706",
            ),
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["provider_payload"]["language_path"], "brazillian-portuguese")

    def test_search_maps_brazilian_portuguese_country_alpha2(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/brazillian-portuguese": DETAIL_DUNE_EN.replace(
                b"/subtitles/dune-2021/english/3331049",
                b"/subtitles/dune-2021/brazillian-portuguese/2706706",
            ),
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["alpha2"], "pt")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")
        self.assertEqual(results[0]["provider_payload"]["language_path"], "brazillian-portuguese")
        self.assertEqual(results[0]["provider_payload"]["country_alpha2"], "BR")

    def test_download_follows_detail_gate_and_extracts_zip_subtitle(self):
        provider = self.mod.SubF2MProvider()
        archive_body = _zip_body(
            {
                "Dune.Part.One.2021.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nMovie line\n",
            }
        )
        responses = {
            "https://subf2m.co/subtitles/dune-2021/english/3331049": DOWNLOAD_GATE_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english/3331049/download": archive_body,
        }
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, config
            calls.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        result = provider.download(
            {
                "provider": "subf2m",
                "schema": 1,
                "page_url": "https://subf2m.co/subtitles/dune-2021/english/3331049",
                "filename": "subf2m.dune.english.3331049.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"request_delay_ms": 0},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(calls[1][1], "https://subf2m.co/subtitles/dune-2021/english/3331049")
        self.assertIn(b"Movie line", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_selects_matching_episode_file_from_season_zip(self):
        archive_body = _zip_body(
            {
                "Chernobyl.S01E02.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nEpisode two\n",
                "Chernobyl.S01E01.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n",
            }
        )

        result = self.mod.extract_download(
            archive_body,
            {"filename": "chernobyl.zip", "season": 1, "episode": 1},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Episode one", decoded)
        self.assertNotIn(b"Episode two", decoded)


if __name__ == "__main__":
    unittest.main()
