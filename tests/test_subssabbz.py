import base64
import hashlib
import importlib.util
import io
import unittest
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subssabbz"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("subssabbz_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_INCEPTION_EN_HTML = (FIXTURE_DIR / "subssabbz_search_inception_en.html").read_bytes()
SEARCH_INCEPTION_BG_HTML = (FIXTURE_DIR / "subssabbz_search_inception_bg.html").read_bytes()
SEARCH_GOT_BG_HTML = (FIXTURE_DIR / "subssabbz_search_game_of_thrones_bg.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SubsSabBzParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_english_movie_row(self):
        rows = self.mod.parse_search_results(SEARCH_INCEPTION_EN_HTML)

        self.assertEqual(rows[0]["download_url"], "http://subs.sab.bz/index.php?act=download&attach_id=51168")
        self.assertEqual(rows[0]["title"], "Inception")
        self.assertEqual(rows[0]["year"], 2010)
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["num_cds"], 2)
        self.assertEqual(rows[0]["fps"], 23.976)
        self.assertEqual(rows[0]["imdb_id"], "tt1375666")
        self.assertEqual(rows[0]["uploader"], "ZIL")

    def test_parse_search_results_extracts_bulgarian_row(self):
        rows = self.mod.parse_search_results(SEARCH_INCEPTION_BG_HTML)

        self.assertEqual(rows[0]["language"], "bul")
        self.assertEqual(rows[0]["download_url"], "http://subs.sab.bz/index.php?act=download&attach_id=51764")

    def test_extract_archive_files_reads_zip_members(self):
        body = _zip_body(
            {
                "Inception.2010.DVDRip.XviD.AC3-ViSiON.srt": "subtitle",
                "README.txt": "ignored",
            }
        )

        rows = self.mod.extract_archive_files(body)

        self.assertEqual(rows[0]["filename"], "Inception.2010.DVDRip.XviD.AC3-ViSiON.srt")
        self.assertEqual(rows[0]["content"], b"subtitle")


class SubsSabBzProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_posts_language_and_returns_archive_files(self):
        provider = self.mod.SubsSabBzProvider()
        calls = []
        archive = _zip_body({"Inception.DVDRiP.XviD-ARROW.CD1.srt": "english"})

        def post_stub(url, data, timeout=30, referer=None):
            del timeout, referer
            calls.append((url, data))
            return SEARCH_INCEPTION_EN_HTML

        provider._http_post = post_stub
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
                "release_group": "ARROW",
                "fps": 23.976,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0][1]["select-language"], "1")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["download_url"], "http://subs.sab.bz/index.php?act=download&attach_id=51168")

    def test_search_episode_filters_archive_members_by_episode(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "Game.of.Thrones.S01E02.720p.HDTV.x264-CTU.srt": "wrong episode",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right episode",
            }
        )
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_GOT_BG_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
                "release_group": "CTU",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("series_imdb_id", results[0]["matches"])

    def test_search_preserves_hi_and_forced_requested_flags(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Inception.DVDRiP.XviD-ARROW.srt": "english"})
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_INCEPTION_EN_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en", "hi": True, "forced": True}],
            {},
        )

        self.assertTrue(results[0]["language"]["hi"])
        self.assertTrue(results[0]["language"]["forced"])

    def test_search_ignores_unsupported_language_and_incomplete_video(self):
        provider = self.mod.SubsSabBzProvider()

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "fra"}], {}), [])
        self.assertEqual(provider.search({"kind": "episode", "series": "Game of Thrones"}, [{"alpha3": "bul"}], {}), [])

    def test_http_post_retries_temporary_403_response(self):
        provider = self.mod.SubsSabBzProvider()
        sleeps = []
        original_sleep = self.mod.time.sleep

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b"ok"

        class Opener:
            def __init__(self):
                self.calls = 0

            def open(self, request, timeout=30):
                del request, timeout
                self.calls += 1
                if self.calls < 3:
                    raise urllib.error.HTTPError(
                        self.mod.SEARCH_URL,
                        403,
                        "Forbidden",
                        hdrs=None,
                        fp=io.BytesIO(b""),
                    )
                return Response()

        opener = Opener()
        opener.mod = self.mod
        provider._opener = opener
        self.mod.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            body = provider._http_post(self.mod.SEARCH_URL, {"act": "search"})
        finally:
            self.mod.time.sleep = original_sleep

        self.assertEqual(body, b"ok")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(sleeps, [10, 10])

    def test_download_selects_named_archive_member_and_normalizes_line_endings(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "wrong.srt": "wrong",
                "Inception.DVDRiP.XviD-ARROW.CD1.srt": "1\r\nText\r\n",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        content = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=51168",
                "filename": "Inception.DVDRiP.XviD-ARROW.CD1.srt",
                "release_info": "Inception.DVDRiP.XviD-ARROW.CD1.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"1\nText\n")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(content["format"], "srt")


if __name__ == "__main__":
    unittest.main()
