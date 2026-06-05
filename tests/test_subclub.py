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
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", content)
        self.assertNotIn("archive_b64", content)

    def test_download_zip_archive_returns_raw_archive_for_host(self):
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
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 1)
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", content)
        self.assertNotIn("member", content)
        self.assertNotIn("encoding", content)

    def test_download_rar_archive_returns_raw_archive_for_host(self):
        provider = self.mod.SubclubProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=11232",
                "archive_id": "11232",
                "filename": "subclub-11232.rar",
                "season": 1,
                "episode": 7,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 7)
        self.assertNotIn("content_b64", content)

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.SubclubProvider()
        body = _zip_body({"Inception.2010.720p.BluRay.srt": "movie subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "archive_url": "https://www.subclub.eu/down.php?id=10100",
                "archive_id": "10100",
                "filename": "subclub-10100.zip",
                "season": None,
                "episode": None,
            },
            {"alpha3": "est", "alpha2": "et"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertIsNone(content["episode"])

    def test_download_archive_rejects_html_error_page(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "archive_url": "https://www.subclub.eu/down.php?id=11232",
                    "archive_id": "11232",
                    "filename": "subclub-11232.zip",
                },
                {"alpha3": "est", "alpha2": "et"},
                {},
            )

    def test_download_archive_rejects_empty_body(self):
        provider = self.mod.SubclubProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"   \r\n  "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "archive_url": "https://www.subclub.eu/down.php?id=11232",
                    "archive_id": "11232",
                    "filename": "subclub-11232.zip",
                },
                {"alpha3": "est", "alpha2": "et"},
                {},
            )

    def test_search_synthetic_fallback_carries_episode_in_payload(self):
        provider = self.mod.SubclubProvider()
        # Search HTML has a matching episode row, but the archive-content endpoint
        # answers with no subtitle links, forcing the synthetic fallback archive.
        provider._http_get = lambda url, timeout=30, referer=None: (
            SEARCH_GOT_HTML if "jutud.php" in url else b"<html><body>no files</body></html>"
        )

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "resolution": "720p",
                "source": "HDTV",
                "release_group": "CTU",
            },
            [{"alpha3": "est", "alpha2": "et"}],
            {},
        )

        self.assertTrue(results)
        payload = results[0]["provider_payload"]
        self.assertEqual(payload["filename"], "subclub-11232.zip")
        # The host needs episode (and season) to pick the archive member.
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 1)

        # The archive body is forwarded raw with the episode carried through.
        body = _zip_body(
            {
                "Game.of.Thrones.S01E01.720p.BluRay.X264-REWARD.srt": "wrong release",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right release",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body
        content = provider.download(payload, {"alpha3": "est", "alpha2": "et"}, {})

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["episode"], 1)


if __name__ == "__main__":
    unittest.main()
