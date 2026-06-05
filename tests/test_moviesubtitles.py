import base64
import hashlib
import importlib.util
import io
import socket
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


def _zip_files(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
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

    def test_parse_movie_subtitles_normalizes_portuguese_labels(self):
        body = b"""
          <a href="/subtitle-1.html" title="Download Portuguese subtitles"><b>Movie Portuguese</b></a>
          <a href="/subtitle-2.html" title="Download Portugese(br) subtitles"><b>Movie Brazilian Portuguese</b></a>
        """
        rows = self.mod.parse_movie_subtitles(body)

        self.assertEqual([row["language"] for row in rows], ["por", "por"])

    def test_row_matches_rejects_wrong_movie_year(self):
        self.assertFalse(
            self.mod._row_matches_video(
                {"kind": "movie", "title": "Suspiria", "year": 2018},
                {"release_info": "Suspiria.1977.1080p"},
                {"title": "Suspiria (1977)", "year": 1977},
            )
        )

    def test_dns_error_detection_uses_exception_type(self):
        error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

        self.assertTrue(self.mod._looks_like_dns_error(error))


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

    def test_download_returns_zip_archive_for_host_extraction(self):
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

        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Interstellar.Bluray.YIFY.en.srt")

    def test_download_selects_sub_member_from_zip(self):
        body = _zip_body(
            "Interstellar.Bluray.YIFY.en.sub",
            b"{1}{24}Movie line\n",
        )

        result = self.mod.extract_download(body, {"filename": "movie.zip"})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Interstellar.Bluray.YIFY.en.sub")

    def test_download_selects_first_multipart_member(self):
        body = _zip_files(
            {
                "Movie.CD1.srt": b"1\n00:00:01,000 --> 00:00:02,000\nPart one\n",
                "Movie.CD2.srt": b"1\n00:10:01,000 --> 00:10:02,000\nPart two\n",
            }
        )

        result = self.mod.extract_download(body, {"filename": "movie.zip"})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["member"], "Movie.CD1.srt")

    def test_download_selects_primary_track_over_hi_member(self):
        body = _zip_files(
            {
                "Movie.HI.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHI track\n",
                "Movie.srt": b"1\n00:00:01,000 --> 00:00:02,000\nMain track\n",
            }
        )

        result = self.mod.extract_download(body, {"filename": "movie.zip"})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["member"], "Movie.srt")

    def test_download_returns_rar_archive_with_episode(self):
        body = b"Rar!\x1a\x07\x00" + b"rar payload bytes"

        result = self.mod.extract_download(body, {"filename": "movie.rar", "episode": None})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertIsNone(result["episode"])
        self.assertNotIn("member", result)

    def test_download_returns_direct_subtitle_content(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nDirect line\n"

        result = self.mod.extract_download(body, {"filename": "movie.srt"})

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"   ", {"filename": "movie.srt"})

    def test_download_rejects_html_body_when_not_zip(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"<html><title>download blocked</title></html>",
                {"filename": "movie.zip"},
            )

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

    def test_http_request_rejects_500_body_for_downloads(self):
        provider = self.mod.MoviesubtitlesProvider()

        def raise_download_500(*args, **kwargs):
            raise urllib.error.HTTPError(
                "https://www.moviesubtitles.org/download-90389.html",
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b"<html>server error</html>"),
            )

        original = self.mod._open_url
        try:
            self.mod._open_url = raise_download_500
            with self.assertRaises(urllib.error.HTTPError):
                provider._http_request("https://www.moviesubtitles.org/download-90389.html")
        finally:
            self.mod._open_url = original


if __name__ == "__main__":
    unittest.main()
