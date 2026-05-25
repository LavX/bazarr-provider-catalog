import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subhd"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subhd_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_EPISODE = (FIXTURE_DIR / "subhd_search_chernobyl_s01e01.html").read_bytes()
DETAIL_EPISODE = (FIXTURE_DIR / "subhd_detail_chernobyl_s01e01.html").read_bytes()
SEARCH_MOVIE = (FIXTURE_DIR / "subhd_search_project_hail_mary.html").read_bytes()
DETAIL_MOVIE = (FIXTURE_DIR / "subhd_detail_project_hail_mary.html").read_bytes()
API_DOWN = (FIXTURE_DIR / "subhd_api_down_chernobyl.json").read_bytes()
SRT_BODY = (FIXTURE_DIR / "subhd_download_chernobyl.srt").read_bytes()


class BuildQueriesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_uses_episode_tag_then_series(self):
        queries = self.mod.build_queries(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1}
        )
        self.assertEqual(queries, ["Chernobyl S01E01", "Chernobyl"])

    def test_movie_uses_title_year_then_title(self):
        queries = self.mod.build_queries(
            {"kind": "movie", "title": "Project Hail Mary", "year": 2026}
        )
        self.assertEqual(queries, ["Project Hail Mary 2026", "Project Hail Mary"])


class ParseSearchResultsTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_search_extracts_subtitle_rows(self):
        rows = self.mod.parse_search_results(SEARCH_EPISODE)
        first = rows[0]
        self.assertEqual(first["subtitle_id"], "uhC1c2")
        self.assertEqual(first["media_id"], "27098632")
        self.assertIn("Chernobyl s01e01", first["release_info"])
        self.assertIn("eng", first["languages"])
        self.assertIn("zho", first["languages"])
        self.assertEqual(first["download_count"], 82)
        self.assertEqual(first["detail_url"], "https://subhd.tv/a/uhC1c2")

    def test_movie_search_extracts_multilingual_rows(self):
        rows = self.mod.parse_search_results(SEARCH_MOVIE)
        multilingual = next(item for item in rows if item["subtitle_id"] == "eVki86")
        self.assertEqual(multilingual["media_id"], "35010610")
        self.assertIn("eng", multilingual["languages"])
        self.assertIn("fra", multilingual["languages"])
        self.assertIn("spa", multilingual["languages"])


class ParseDetailTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_detail_extracts_download_metadata(self):
        detail = self.mod.parse_detail_page(DETAIL_EPISODE, "https://subhd.tv/a/uhC1c2")
        self.assertEqual(detail["subtitle_id"], "uhC1c2")
        self.assertEqual(detail["download_url"], "https://subhd.tv/down/uhC1c2")
        self.assertIn("Chernobyl s01e01", detail["release_info"])
        self.assertEqual(detail["format"], "srt")
        self.assertIn("eng", detail["languages"])
        self.assertIn("zho", detail["languages"])

    def test_movie_detail_extracts_preview_file_and_metadata(self):
        detail = self.mod.parse_detail_page(DETAIL_MOVIE, "https://subhd.tv/a/PuCsoN")
        self.assertEqual(detail["subtitle_id"], "PuCsoN")
        self.assertEqual(detail["format"], "srt")
        self.assertEqual(detail["download_count"], 2343)
        self.assertIn("eng", detail["languages"])
        self.assertIn("zho", detail["languages"])
        self.assertIn("1776336311088.srt", detail["files"])


class DownloadResponseTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_api_response_exposes_final_url(self):
        url = self.mod.parse_download_response(API_DOWN)
        self.assertEqual(url, "https://dl.subhd.tv/2026/03/1772600046334.srt")

    def test_download_api_response_rejects_captcha(self):
        body = b'{"success":true,"pass":false,"msg":"<svg></svg>","url":null}'
        with self.assertRaisesRegex(ValueError, "captcha"):
            self.mod.parse_download_response(body)


class SubHDProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_language_result(self):
        responses = {
            "https://subhd.tv/search/Chernobyl%20S01E01": SEARCH_EPISODE,
            "https://subhd.tv/a/uhC1c2": DETAIL_EPISODE,
        }
        provider = self.mod.SubHDProvider()
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertEqual(called[:2], list(responses))
        first = results[0]
        self.assertEqual(first["provider"], "subhd")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("episode", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], "uhC1c2")
        self.assertEqual(first["provider_payload"]["download_url"], "https://subhd.tv/down/uhC1c2")


class SubHDProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_seeds_session_posts_api_and_fetches_file(self):
        provider = self.mod.SubHDProvider()
        called = []

        def get_stub(url, timeout=15, referer=None):
            del timeout
            called.append(("GET", url, referer))
            if url == "https://subhd.tv/a/uhC1c2":
                return DETAIL_EPISODE
            if url == "https://subhd.tv/down/uhC1c2":
                return b"<html>download page</html>"
            if url == "https://dl.subhd.tv/2026/03/1772600046334.srt":
                return SRT_BODY
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, payload, timeout=15, referer=None):
            del timeout
            called.append(("POST", url, referer, payload))
            self.assertEqual(payload, {"sid": "uhC1c2", "cap": ""})
            return API_DOWN

        provider._http_get = get_stub
        provider._http_post_json = post_stub

        result = provider.download(
            {
                "provider": "subhd",
                "schema": 1,
                "subtitle_id": "uhC1c2",
                "detail_url": "https://subhd.tv/a/uhC1c2",
                "download_url": "https://subhd.tv/down/uhC1c2",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(called[0], ("GET", "https://subhd.tv/a/uhC1c2", None))
        self.assertEqual(called[1], ("GET", "https://subhd.tv/down/uhC1c2", "https://subhd.tv/a/uhC1c2"))
        self.assertEqual(called[2][0], "POST")
        self.assertEqual(called[3][0], "GET")
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])
        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())


if __name__ == "__main__":
    unittest.main()
