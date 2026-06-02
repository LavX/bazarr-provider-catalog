import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subclub"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("subclub_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_INCEPTION_HTML = (FIXTURE_DIR / "subclub_search_inception.html").read_bytes()
SEARCH_GOT_HTML = (FIXTURE_DIR / "subclub_search_game_of_thrones.html").read_bytes()
ARCHIVE_INCEPTION_HTML = (FIXTURE_DIR / "subclub_archive_10100.html").read_bytes()
ARCHIVE_GOT_HTML = (FIXTURE_DIR / "subclub_archive_11232.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SubclubParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_row(self):
        rows = self.mod.parse_search_results(SEARCH_INCEPTION_HTML)

        self.assertEqual(rows[0]["archive_id"], "10100")
        self.assertEqual(rows[0]["title"], "Inception")
        self.assertEqual(rows[0]["year"], 2010)
        self.assertIsNone(rows[0]["season"])
        self.assertEqual(rows[0]["imdb_id"], "tt1375666")
        self.assertEqual(rows[0]["fps"], 23.976)
        self.assertEqual(rows[0]["rating"], 5.0)

    def test_parse_search_results_extracts_episode_row(self):
        rows = self.mod.parse_search_results(SEARCH_GOT_HTML)

        self.assertEqual(rows[0]["archive_id"], "11232")
        self.assertEqual(rows[0]["title"], "Game of Thrones")
        self.assertEqual(rows[0]["season"], 1)
        self.assertEqual(rows[0]["episode"], 1)
        self.assertEqual(rows[0]["fps"], 23.976)

    def test_parse_archive_listing_extracts_direct_file_links(self):
        rows = self.mod.parse_archive_listing(ARCHIVE_GOT_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["filename"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertEqual(
            rows[1]["url"],
            "https://www.subclub.eu/down.php?id=11232&filename=R2FtZS5vZi5UaHJvbmVzLlMwMUUwMS43MjBwLkhEVFYueDI2NC1DVFUuc3J0",
        )


class SubclubProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_returns_archive_files(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            SEARCH_INCEPTION_HTML if "jutud.php" in url else ARCHIVE_INCEPTION_HTML
        )

        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
                "resolution": "720p",
                "source": "BluRay",
                "release_group": "CROSSBOW",
            },
            [{"alpha3": "est", "alpha2": "et"}],
            {},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "est")
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["archive_id"], "10100")

    def test_search_episode_returns_matching_release(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            SEARCH_GOT_HTML if "jutud.php" in url else ARCHIVE_GOT_HTML
        )

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
                "release_group": "CTU",
            },
            [{"alpha3": "est", "alpha2": "et"}],
            {},
        )

        self.assertEqual(len(results), 2)
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_ignores_unsupported_language_and_incomplete_video(self):
        provider = self.mod.SubclubProvider()

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "episode", "series": "Game of Thrones"}, [{"alpha3": "est"}], {}), [])

    def test_download_direct_file_normalizes_line_endings(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n"

        content = provider.download(
            {
                "url": "https://www.subclub.eu/down.php?id=11232&filename=abc",
                "archive_id": "11232",
                "filename": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"1\n00:00:01,000 --> 00:00:02,000\nText\n")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())

    def test_download_archive_fallback_selects_best_file(self):
        provider = self.mod.SubclubProvider()
        body = _zip_body(
            {
                "Game.of.Thrones.S01E01.720p.BluRay.X264-REWARD.srt": "wrong release",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right release",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=11232",
                "archive_id": "11232",
                "filename": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "season": 1,
                "episode": 1,
                "release_info": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        self.assertEqual(base64.b64decode(content["content_b64"]), b"right release")


if __name__ == "__main__":
    unittest.main()
