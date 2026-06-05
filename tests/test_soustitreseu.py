import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "soustitreseu"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "soustitreseu_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_GOT_HTML = (FIXTURE_DIR / "soustitreseu_search_game_of_thrones.html").read_bytes()
SERIES_GOT_HTML = (FIXTURE_DIR / "soustitreseu_series_game_of_thrones.html").read_bytes()
SEARCH_DUNE_HTML = (FIXTURE_DIR / "soustitreseu_search_dune.html").read_bytes()
FILM_DUNE_HTML = (FIXTURE_DIR / "soustitreseu_film_dune.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SoustitreseuParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_media_rows(self):
        rows = self.mod.parse_search_results(SEARCH_GOT_HTML)

        self.assertEqual(rows[0]["media_type"], "series")
        self.assertTrue(rows[0]["exact"])
        self.assertEqual(rows[0]["title"], "Game Of Thrones")
        self.assertEqual(rows[0]["url"], "https://www.sous-titres.eu/series/game_of_thrones.html")
        self.assertEqual(rows[1]["media_type"], "film")
        self.assertEqual(rows[1]["year"], 2014)

    def test_parse_series_archive_rows_extracts_episode_and_languages(self):
        rows = self.mod.parse_archive_rows(
            SERIES_GOT_HTML,
            "https://www.sous-titres.eu/series/game_of_thrones.html",
            "series",
        )

        self.assertEqual(rows[0]["season"], 1)
        self.assertEqual(rows[0]["episode"], 1)
        self.assertEqual(rows[0]["filename"], "Game.Of.Thrones.1x01.ENFR.FBK.zip")
        self.assertEqual(rows[0]["languages"], ["eng", "fra"])
        self.assertIn("FBK", rows[0]["release_info"])
        self.assertEqual(rows[1]["season"], 1)
        self.assertIsNone(rows[1]["episode"])

    def test_parse_film_archive_rows_extracts_movie_downloads(self):
        rows = self.mod.parse_archive_rows(
            FILM_DUNE_HTML,
            "https://www.sous-titres.eu/films/dune_part_one.html",
            "film",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["filename"], "Dune.Part.One.(2021).Z2.WEB.zip")
        self.assertEqual(rows[1]["languages"], ["fra"])
        self.assertEqual(
            rows[1]["url"],
            "https://www.sous-titres.eu/films/download/krbse0p1duwc8oi/Dune.Part.One.%282021%29.Z2.WEB.zip",
        )

    def test_archive_languages_from_vo_vf_filenames(self):
        self.assertEqual(
            self.mod._languages_from_archive("Game.Of.Thrones.1x01.VO.FBK.zip", "<img src='img/flag.jpg' />"),
            ["eng"],
        )
        self.assertEqual(
            self.mod._languages_from_archive("Game.Of.Thrones.1x01.VF.FBK.zip", "<img src='img/flag.jpg' />"),
            ["fra"],
        )

    def test_parse_archive_rows_detects_vo_language_without_flag_alt(self):
        rows = self.mod.parse_archive_rows(
            """
            <a class="subList download" href="download/abc/Game.Of.Thrones.1x01.VO.zip">
              <span class="filenameSerie">Game.Of.Thrones.1x01.VO.zip</span>
              <span class="episodeNum">1 x 01</span>
              <span class="lang"><img src="img/flag.jpg" /></span>
            </a>
            """,
            "https://www.sous-titres.eu/series/game_of_thrones.html",
            "series",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["languages"], ["eng"])

    def test_parse_archive_rows_accepts_sublist_before_href(self):
        rows = self.mod.parse_archive_rows(
            """
            <a class="subList download" href="download/abc/Game.Of.Thrones.1x01.ENFR.zip">
              <span class="filenameSerie">Game.Of.Thrones.1x01.ENFR.zip</span>
              <span class="episodeNum">1 x 01</span>
              <img title="en" />
            </a>
            """,
            "https://www.sous-titres.eu/series/game_of_thrones.html",
            "series",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["url"],
            "https://www.sous-titres.eu/series/download/abc/Game.Of.Thrones.1x01.ENFR.zip",
        )


class SoustitreseuProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_episode_returns_requested_languages(self):
        provider = self.mod.SoustitreseuProvider()
        calls = []
        responses = {
            "https://www.sous-titres.eu/search.html?q=Game+of+Thrones": SEARCH_GOT_HTML,
            "https://www.sous-titres.eu/series/game_of_thrones.html": SERIES_GOT_HTML,
        }

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            return responses[url]

        provider._http_get = get_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "release_group": "FBK",
            },
            [{"alpha3": "fra", "alpha2": "fr"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"eng", "fra"})
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(calls[-1], "https://www.sous-titres.eu/series/game_of_thrones.html")

    def test_search_movie_returns_matching_film_archive(self):
        provider = self.mod.SoustitreseuProvider()
        provider._http_get = lambda url, timeout=15, referer=None: (
            SEARCH_DUNE_HTML if "search.html" in url else FILM_DUNE_HTML
        )

        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune: Part One",
                "year": 2021,
                "source": "WEB",
            },
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "fra")
        self.assertEqual(results[0]["provider_payload"]["media_type"], "film")
        self.assertIn("title", results[0]["matches"])

    def test_search_movie_drops_unrelated_fallback_rows(self):
        provider = self.mod.SoustitreseuProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search.html" in url:
                return SEARCH_DUNE_HTML
            raise AssertionError(f"unexpected detail request: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {"kind": "movie", "title": "Interstellar", "year": 2014},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertEqual(results, [])
        self.assertEqual(calls, ["https://www.sous-titres.eu/search.html?q=Interstellar"])

    def test_search_movie_rejects_explicit_year_mismatch(self):
        provider = self.mod.SoustitreseuProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search.html" in url:
                return SEARCH_DUNE_HTML
            if url == "https://www.sous-titres.eu/films/dune.html":
                return b"<html><body></body></html>"
            raise AssertionError(f"unexpected detail request: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 1984},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertEqual(results, [])
        self.assertNotIn(
            "https://www.sous-titres.eu/films/dune_part_one.html",
            calls,
        )

    def test_search_ignores_unsupported_or_incomplete_requests(self):
        provider = self.mod.SoustitreseuProvider()

        self.assertEqual(provider.search({"kind": "episode", "series": "Game of Thrones"}, [{"alpha3": "fra"}], {}), [])
        self.assertEqual(provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "deu"}], {}), [])

    def test_download_selects_episode_language_from_zip_archive(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.101.ctu.720p.VF.NoTAG.srt": "french subtitle",
                "Game.Of.Thrones.101.ctu.720p.VO.NoTAG.srt": "english subtitle",
                "Game.Of.Thrones.102.ctu.720p.VO.NoTAG.srt": "wrong episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/1f7d3i5ypv2bv0m/Game.Of.Thrones.1x01.ENFR.FBK.zip",
                "filename": "Game.Of.Thrones.1x01.ENFR.FBK.zip",
                "media_type": "series",
                "season": 1,
                "episode": 1,
                "release_info": "Game.Of.Thrones.1x01.ENFR.FBK.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"english subtitle")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(content["format"], "srt")

    def test_download_prioritizes_episode_over_language_from_zip_archive(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.S01E01.VF.srt": "requested episode",
                "Game.Of.Thrones.S01E02.VO.srt": "wrong episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/1f7d3i5ypv2bv0m/Game.Of.Thrones.S01.ENFR.zip",
                "filename": "Game.Of.Thrones.S01.ENFR.zip",
                "media_type": "series",
                "season": 1,
                "episode": 1,
                "release_info": "Game.Of.Thrones.S01.ENFR.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(content["content_b64"]), b"requested episode")


if __name__ == "__main__":
    unittest.main()
