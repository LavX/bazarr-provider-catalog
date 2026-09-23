import base64
import hashlib
import importlib.util
import socket
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "kitsunekko"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "kitsunekko_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INDEX_HTML = (FIXTURE_DIR / "kitsunekko_index.html").read_bytes()
COWBOY_HTML = (FIXTURE_DIR / "kitsunekko_directory_cowboy_bebop.html").read_bytes()
AKIRA_HTML = (FIXTURE_DIR / "kitsunekko_directory_akira.html").read_bytes()
COWBOY_ZIP = (FIXTURE_DIR / "kitsunekko_cowboy_bebop.zip").read_bytes()
AKIRA_ASS = (FIXTURE_DIR / "kitsunekko_akira.ass").read_bytes()


class KitsunekkoParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_index_directories_extracts_titles_and_urls(self):
        rows = self.mod.parse_index_directories(INDEX_HTML)
        self.assertEqual(rows[0]["title"], "Akira")
        self.assertEqual(rows[0]["url"], "https://kitsunekko.net/dirlist.php?dir=subtitles%2FAkira%2F")
        self.assertEqual(rows[1]["title"], "Cowboy Bebop")

    def test_parse_file_listing_extracts_supported_files(self):
        rows = self.mod.parse_file_listing(COWBOY_HTML, "Cowboy Bebop")
        self.assertEqual(rows[0]["filename"], "Cowboy.Bebop.The.Movie.2001.HMAX.WEB-DL.srt")
        self.assertEqual(rows[0]["size_bytes"], 92118)
        self.assertEqual(rows[0]["format"], "srt")
        self.assertEqual(rows[1]["format"], "zip")
        self.assertEqual(rows[1]["url"], "https://kitsunekko.net/subtitles/Cowboy%20Bebop/Cowboy%20Bebop.zip")

    def test_parse_file_listing_skips_unsupported_archives(self):
        html = b'<a href="subtitles/Foo/Foo.7z"><strong>Foo.7z</strong></a>'
        self.assertEqual(self.mod.parse_file_listing(html, "Foo"), [])


class KitsunekkoProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_episode_archive_candidate(self):
        provider = self.mod.KitsunekkoProvider()
        responses = {
            "https://kitsunekko.net/dirlist.php?dir=subtitles%2F": INDEX_HTML,
            "https://kitsunekko.net/dirlist.php?dir=subtitles%2FCowboy+Bebop%2F": COWBOY_HTML,
        }
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Cowboy Bebop", "season": 1, "episode": 1},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, list(responses))
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertEqual(first["provider"], "kitsunekko")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("series", first["matches"])
        self.assertEqual(first["provider_payload"]["archive_format"], "zip")
        self.assertEqual(first["provider_payload"]["episode"], 1)

    def test_search_returns_movie_candidate(self):
        provider = self.mod.KitsunekkoProvider()
        responses = {
            "https://kitsunekko.net/dirlist.php?dir=subtitles%2F": INDEX_HTML,
            "https://kitsunekko.net/dirlist.php?dir=subtitles%2FAkira%2F": AKIRA_HTML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Akira", "year": 1988},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["provider_payload"]["format"], "ass")
        self.assertIn("title", results[0]["matches"])


class KitsunekkoProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_archive_for_host_extraction(self):
        provider = self.mod.KitsunekkoProvider()
        provider._http_get = lambda url, timeout=15, referer=None: COWBOY_ZIP
        result = provider.download(
            {
                "provider": "kitsunekko",
                "schema": 1,
                "url": "https://kitsunekko.net/subtitles/Cowboy%20Bebop/Cowboy%20Bebop.zip",
                "filename": "Cowboy Bebop.zip",
                "format": "zip",
                "archive_format": "zip",
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        # The raw archive bytes are forwarded for host-side extraction. The provider
        # still lists the zip and picks the member, but no longer extracts or decodes it.
        self.assertEqual(base64.b64decode(result["archive_b64"]), COWBOY_ZIP)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(COWBOY_ZIP).hexdigest())
        self.assertEqual(self.mod._select_zip_member(COWBOY_ZIP, 1), result["member"])
        self.assertTrue(result["member"].lower().endswith(".srt"))
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_empty_archive_body_raises(self):
        provider = self.mod.KitsunekkoProvider()
        provider._http_get = lambda url, timeout=15, referer=None: b""
        with self.assertRaises(Exception):
            provider.download(
                {
                    "provider": "kitsunekko",
                    "schema": 1,
                    "url": "https://kitsunekko.net/subtitles/Cowboy%20Bebop/Cowboy%20Bebop.zip",
                    "filename": "Cowboy Bebop.zip",
                    "format": "zip",
                    "archive_format": "zip",
                    "episode": 1,
                },
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_html_archive_body_raises(self):
        provider = self.mod.KitsunekkoProvider()
        provider._http_get = lambda url, timeout=15, referer=None: b"<!doctype html><html><body>error</body></html>"
        with self.assertRaises(Exception):
            provider.download(
                {
                    "provider": "kitsunekko",
                    "schema": 1,
                    "url": "https://kitsunekko.net/subtitles/Cowboy%20Bebop/Cowboy%20Bebop.zip",
                    "filename": "Cowboy Bebop.zip",
                    "format": "zip",
                    "archive_format": "zip",
                    "episode": 1,
                },
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_returns_direct_subtitle_file(self):
        provider = self.mod.KitsunekkoProvider()
        provider._http_get = lambda url, timeout=15, referer=None: AKIRA_ASS
        result = provider.download(
            {
                "provider": "kitsunekko",
                "schema": 1,
                "url": "https://kitsunekko.net/subtitles/Akira/Akira.ass",
                "filename": "Akira.ass",
                "format": "ass",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), AKIRA_ASS)
        self.assertEqual(result["format"], "ass")


class KitsunekkoHttpTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_http_get_retries_transient_timeout(self):
        provider = self.mod.KitsunekkoProvider()
        calls = []
        original_urlopen = self.mod.urllib.request.urlopen

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"ok"

        def fake_urlopen(request, timeout=15):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise socket.timeout("timed out")
            return Response()

        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            self.assertEqual(provider._http_get("https://kitsunekko.net/"), b"ok")
        finally:
            self.mod.urllib.request.urlopen = original_urlopen
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
