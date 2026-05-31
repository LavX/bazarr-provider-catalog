import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subs4free"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subs4free_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "subs4free_search_inception.html").read_bytes()
SUGGESTIONS_HTML = (FIXTURE_DIR / "subs4free_suggestions.html").read_bytes()
DOWNLOAD_HTML = (FIXTURE_DIR / "subs4free_download_page.html").read_bytes()


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(".hidden.srt", "ignore")
        archive.writestr("Inception.2010.1080p.BluRay.RARBG.srt", "one\r\ntwo\r\n")
    return stream.getvalue()


class Subs4FreeParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_rows(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["language"], "ell")
        self.assertEqual(rows[0]["alpha2"], "el")
        self.assertEqual(rows[0]["release_info"], "Inception 2010 1080p BluRay H264 AAC-RARBG [SubRip]")
        self.assertEqual(rows[0]["uploader"], "Nerddork")
        self.assertEqual(rows[0]["downloads"], 1699)
        self.assertEqual(
            rows[0]["detail_url"],
            "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception-2010-1080p-bluray-h264-aac-rarbg-subrip",
        )
        self.assertEqual(rows[1]["language"], "eng")

    def test_parse_suggestions_keeps_option_links(self):
        rows = self.mod.parse_suggestions(SUGGESTIONS_HTML)

        self.assertEqual(rows[0]["title"], "Inception 2010")
        self.assertEqual(
            rows[0]["url"],
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010",
        )

    def test_parse_download_form_extracts_post_id_and_click_bounds(self):
        form = self.mod.parse_download_form(DOWNLOAD_HTML)

        self.assertEqual(form["id"], "tkMTc4MDI0MDQ5MTE4NTk1MjUxMTg2MTg5NjQ2MTg5")
        self.assertEqual(form["width"], 200)
        self.assertEqual(form["height"], 58)

    def test_extract_download_reads_zip_subtitle_and_skips_hidden_files(self):
        payload = self.mod.extract_download(_zip_body(), {"filename": "inception.el.zip"})

        self.assertEqual(payload["format"], "srt")
        self.assertEqual(payload["encoding"], "utf-8")
        self.assertEqual(payload["empty"], False)
        self.assertIn("one\ntwo\n", self.mod._decode_payload_text(payload))


class Subs4FreeProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_movie_languages(self):
        provider = self.mod.Subs4FreeProvider()
        calls = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search_report.php" in url:
                return SEARCH_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "source": "Blu-ray",
                "resolution": "1080p",
                "video_codec": "H.264",
                "audio_codec": "AAC",
                "release_group": "RARBG",
            },
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertTrue(any("Inception+2010" in url for url in calls))
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        first = results[0]
        self.assertEqual(first["provider"], "subs4free")
        self.assertEqual(first["score"], 100)
        self.assertIn("title", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertIn("resolution", first["matches"])
        self.assertEqual(first["provider_payload"]["detail_url"], results[0]["page_link"])

    def test_search_uses_matching_suggestion_pages_when_present(self):
        provider = self.mod.Subs4FreeProvider()
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": SUGGESTIONS_HTML,
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010": SEARCH_HTML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "ell")

    def test_download_posts_form_after_anti_block_requests(self):
        provider = self.mod.Subs4FreeProvider()
        seen_gets = []
        posts = []

        def get_stub(url, timeout=15, referer=None):
            del timeout
            seen_gets.append((url, referer))
            if url == "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception":
                return DOWNLOAD_HTML
            if "anti-block" in url or "favicon.ico" in url:
                return b"ok"
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, data, timeout=15, referer=None):
            del timeout
            posts.append((url, data, referer))
            return _zip_body()

        provider._http_get = get_stub
        provider._http_post = post_stub
        result = provider.download(
            {
                "detail_url": "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception",
                "filename": "inception.el.zip",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4free.info/getSub.php")
        self.assertEqual(posts[0][1]["id"], "tkMTc4MDI0MDQ5MTE4NTk1MjUxMTg2MTg5NjQ2MTg5")
        self.assertEqual(posts[0][2], "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception")
        self.assertTrue(any("favicon.ico" in item[0] for item in seen_gets))
        self.assertIn("one\ntwo\n", self.mod._decode_payload_text(result))


if __name__ == "__main__":
    unittest.main()
