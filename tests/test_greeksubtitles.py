import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "greeksubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "greeksubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DUNE_HTML = (FIXTURE_DIR / "greeksubtitles_search_dune.html").read_bytes()
GOT_PAGE0_HTML = (FIXTURE_DIR / "greeksubtitles_search_game_of_thrones_page0.html").read_bytes()
GOT_PAGE1_HTML = (FIXTURE_DIR / "greeksubtitles_search_game_of_thrones_page1.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class GreekSubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_page_extracts_language_rows_and_download_ids(self):
        page = self.mod.parse_search_page(DUNE_HTML, "https://gr.greek-subtitles.com/search.php?name=Dune+2021")

        self.assertIsNone(page["next_url"])
        self.assertEqual(len(page["rows"]), 2)
        self.assertEqual(page["rows"][0]["subtitle_id"], "2793668")
        self.assertEqual(page["rows"][0]["language"], "ell")
        self.assertEqual(page["rows"][0]["alpha2"], "el")
        self.assertEqual(page["rows"][0]["page_url"], "http://subtitles.gr/subtitles/Dune-2021-1080p-WEBRip-x264-AAC5-1-YTS-MX-/2793668/")
        self.assertEqual(page["rows"][0]["release"], "Dune 2021 1080p WEBRip x264 AAC5 1 YTS MX")
        self.assertEqual(page["rows"][0]["downloads"], 321)
        self.assertEqual(page["rows"][1]["language"], "eng")
        self.assertEqual(page["rows"][1]["alpha2"], "en")

    def test_parse_search_page_extracts_next_page_url(self):
        page = self.mod.parse_search_page(
            GOT_PAGE0_HTML,
            "https://gr.greek-subtitles.com/search.php?name=Game+of+Thrones+S01E01",
        )

        self.assertEqual(
            page["next_url"],
            "https://gr.greek-subtitles.com/search.php?page=1&name=Game%20of%20Thrones%20S01E01&sort=name",
        )
        self.assertEqual(page["rows"], [])

    def test_build_search_queries_matches_movie_and_episode_flow(self):
        self.assertEqual(
            self.mod.build_search_queries(
                {
                    "kind": "episode",
                    "series": "Game of Thrones",
                    "alternative_series": ["GoT"],
                    "season": 1,
                    "episode": 1,
                }
            ),
            ["Game of Thrones S01E01", "GoT S01E01"],
        )
        self.assertEqual(
            self.mod.build_search_queries(
                {"kind": "movie", "title": "Dune", "alternative_titles": ["Dune: Part One"], "year": 2021}
            ),
            ["Dune 2021", "Dune: Part One 2021"],
        )

    def test_derive_matches_requires_episode_year_in_release(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": 2, "year": 2005},
            "The Office S01E02 HDTV x264",
        )

        self.assertIn("series", matches)
        self.assertIn("episode", matches)
        self.assertNotIn("year", matches)

    def test_derive_matches_compares_whole_tokens(self):
        matches = self.mod.derive_matches(
            {"kind": "movie", "title": "Ann", "year": 2021},
            "Joanne 2021 WEBRip",
        )

        self.assertNotIn("title", matches)


class GreekSubtitlesSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_returns_requested_greek_and_english_results(self):
        provider = self.mod.GreekSubtitlesProvider()
        called = []

        def stub(url, timeout=30, referer=None):
            del timeout, referer
            called.append(url)
            self.assertEqual(url, "https://gr.greek-subtitles.com/search.php?name=Dune+2021")
            return DUNE_HTML

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "source": "WEBRip",
                "release_group": "YTS",
            },
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, ["https://gr.greek-subtitles.com/search.php?name=Dune+2021"])
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        greek = next(item for item in results if item["language"]["alpha3"] == "ell")
        self.assertEqual(greek["provider"], "greeksubtitles")
        self.assertEqual(greek["provider_payload"]["subtitle_id"], "2793668")
        self.assertEqual(greek["provider_payload"]["download_url"], "https://www.greeksubtitles.info/getp.php?id=2793668")
        self.assertIn("title", greek["matches"])
        self.assertIn("year", greek["matches"])
        self.assertIn("source", greek["matches"])
        self.assertIn("release_group", greek["matches"])

    def test_search_episode_follows_next_page(self):
        provider = self.mod.GreekSubtitlesProvider()
        responses = {
            "https://gr.greek-subtitles.com/search.php?name=Game+of+Thrones+S01E01": GOT_PAGE0_HTML,
            "https://gr.greek-subtitles.com/search.php?page=1&name=Game%20of%20Thrones%20S01E01&sort=name": GOT_PAGE1_HTML,
        }
        called = []

        def stub(url, timeout=30, referer=None):
            del timeout, referer
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "source": "HDTV",
                "video_codec": "x264",
                "release_group": "CTU",
            },
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, list(responses))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1659162")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_rejects_unsupported_language_or_media(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not run"))

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune", "year": 2021}, [{"alpha3": "fra", "alpha2": "fr"}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "series", "series": "Game of Thrones"}, [{"alpha3": "ell", "alpha2": "el"}], {}),
            [],
        )


class GreekSubtitlesDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_extracts_first_visible_subtitle_from_zip(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None: _zip_body(
            {
                ".hidden.srt": "hidden",
                "info.txt": "not a subtitle",
                "subs/Dune.2021.srt": "1\r\n00:00:01,000 --> 00:00:02,000\r\nLine\r\n",
            }
        )

        result = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=2793668",
                "page_url": "http://subtitles.gr/subtitles/Dune-2021-1080p-WEBRip-x264-AAC5-1-YTS-MX-/2793668/",
                "filename": "Dune.2021.zip",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        body = base64.b64decode(result["content_b64"])
        self.assertEqual(body, b"1\n00:00:01,000 --> 00:00:02,000\nLine\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())

    def test_extract_download_uses_rar_extractor(self):
        original = self.mod._extract_rar_files
        self.mod._extract_rar_files = lambda body: [("subtitle.ass", b"[Script Info]\r\nTitle: ok\r\n")]
        try:
            body, subtitle_format = self.mod.extract_download(b"Rar!\x1a\x07\x00fake", {})
        finally:
            self.mod._extract_rar_files = original

        self.assertEqual(body, b"[Script Info]\nTitle: ok\n")
        self.assertEqual(subtitle_format, "ass")

    def test_download_returns_raw_subtitle_payload(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nRaw\r\n"

        result = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                "filename": "raw.srt",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nRaw\n")
        self.assertEqual(result["format"], "srt")

    def test_extract_download_rejects_html_response(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"<!doctype html><html><body>not a subtitle</body></html>",
                {"filename": "greeksubtitles.failure.zip"},
            )

    def test_extract_download_rejects_unsupported_raw_response(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"download temporarily unavailable",
                {"filename": "greeksubtitles.failure.zip"},
            )


if __name__ == "__main__":
    unittest.main()
