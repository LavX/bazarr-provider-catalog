import base64
import hashlib
import importlib.util
import io
import urllib.error
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "moviesubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "moviesubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "moviesubtitles_search_interstellar.html").read_bytes()
MOVIE_HTML = (FIXTURE_DIR / "moviesubtitles_movie_interstellar.html").read_bytes()


def _zip_body(name, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
    return stream.getvalue()


class MoviesubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_links(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)
        self.assertEqual(rows[0]["movie_id"], "12038")
        self.assertEqual(rows[0]["title"], "Interstellar (2014)")
        self.assertEqual(rows[0]["url"], "https://www.moviesubtitles.org/movie-12038.html")

    def test_parse_movie_subtitles_extracts_release_rows(self):
        rows = self.mod.parse_movie_subtitles(MOVIE_HTML)
        self.assertEqual(rows[0]["subtitle_id"], "90389")
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["rip"], "Bluray")
        self.assertEqual(rows[0]["release"], "YIFY")
        self.assertEqual(rows[0]["download_url"], "https://www.moviesubtitles.org/download-90389.html")


class MoviesubtitlesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_movie_page_and_returns_language_results(self):
        provider = self.mod.MoviesubtitlesProvider()
        responses = {
            "https://www.moviesubtitles.org/search.php": SEARCH_HTML,
            "https://www.moviesubtitles.org/movie-12038.html": MOVIE_HTML,
        }
        posts = []

        def stub(url, data=None, timeout=15, referer=None):
            del timeout
            posts.append((url, data, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Interstellar", "year": 2014},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.moviesubtitles.org/search.php")
        self.assertEqual(posts[0][1], b"q=Interstellar+2014")
        self.assertEqual(results[0]["provider"], "moviesubtitles")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "90389")

    def test_download_extracts_zip_subtitle(self):
        provider = self.mod.MoviesubtitlesProvider()
        body = _zip_body(
            "Interstellar.Bluray.YIFY.en.srt",
            b"1\n00:00:01,000 --> 00:00:02,000\nMovie line\n",
        )
        provider._http_request = lambda url, data=None, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "moviesubtitles",
                "schema": 1,
                "subtitle_id": "90389",
                "url": "https://www.moviesubtitles.org/download-90389.html",
                "page_url": "https://www.moviesubtitles.org/subtitle-90389.html",
                "filename": "Interstellar.Bluray.YIFY.en.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Movie line", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_http_request_accepts_legacy_500_with_body(self):
        provider = self.mod.MoviesubtitlesProvider()
        error = urllib.error.HTTPError(
            "https://www.moviesubtitles.org/search.php",
            500,
            "Internal Server Error",
            {},
            io.BytesIO(SEARCH_HTML),
        )

        original = self.mod._open_url
        try:
            self.mod._open_url = lambda *args, **kwargs: (_ for _ in ()).throw(error)
            self.assertEqual(
                provider._http_request("https://www.moviesubtitles.org/search.php"),
                SEARCH_HTML,
            )
        finally:
            self.mod._open_url = original


if __name__ == "__main__":
    unittest.main()
