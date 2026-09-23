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
        # A movie has no episode/season for host-side member selection.
        self.assertIsNone(greek["provider_payload"]["episode"])
        self.assertIsNone(greek["provider_payload"]["season"])
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
        # The host needs episode (and season) to pick the archive member.
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)
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

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.GreekSubtitlesProvider()
        body = _zip_body(
            {
                ".hidden.srt": "hidden",
                "info.txt": "not a subtitle",
                "subs/Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "1\r\n00:00:01,000 --> 00:00:02,000\r\nLine\r\n",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1659162",
                "page_url": "http://subtitles.gr/subtitles/x/1659162/",
                "filename": "greeksubtitles.got.el.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "ell", "alpha2": "el"},
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
        provider = self.mod.GreekSubtitlesProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1659162",
                "filename": "greeksubtitles.got.el.zip",
                "season": 1,
                "episode": 7,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 7)
        self.assertNotIn("content_b64", content)
        self.assertNotIn("encoding", content)

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.GreekSubtitlesProvider()
        body = _zip_body({"Dune.2021.1080p.WEBRip.srt": "movie subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=2793668",
                "filename": "greeksubtitles.dune.el.zip",
                "season": None,
                "episode": None,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertIsNone(content["episode"])

    def test_download_direct_subtitle_returns_content_payload(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nRaw\r\n"

        content = provider.download(
            {
                "provider": "greeksubtitles",
                "schema": 1,
                "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                "filename": "raw.srt",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        body = base64.b64decode(content["content_b64"])
        self.assertEqual(body, b"1\n00:00:01,000 --> 00:00:02,000\nRaw\n")
        self.assertEqual(content["format"], "srt")
        self.assertEqual(content["content_sha256"], hashlib.sha256(body).hexdigest())
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", content)
        self.assertNotIn("archive_b64", content)

    def test_download_rejects_html_error_page(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!doctype html><html><body>not a subtitle</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "greeksubtitles",
                    "schema": 1,
                    "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                    "filename": "greeksubtitles.failure.zip",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

    def test_download_rejects_empty_body(self):
        provider = self.mod.GreekSubtitlesProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"   \r\n  "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "greeksubtitles",
                    "schema": 1,
                    "download_url": "https://www.greeksubtitles.info/getp.php?id=1",
                    "filename": "greeksubtitles.failure.zip",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

    def test_download_requires_download_url(self):
        provider = self.mod.GreekSubtitlesProvider()
        with self.assertRaises(ValueError):
            provider.download({"provider": "greeksubtitles", "schema": 1}, {"alpha3": "ell"}, {})


if __name__ == "__main__":
    unittest.main()
