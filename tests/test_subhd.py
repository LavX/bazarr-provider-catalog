import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
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
DOWN_PAGE_EPISODE = (FIXTURE_DIR / "subhd_down_chernobyl.html").read_bytes()
API_DOWN = (FIXTURE_DIR / "subhd_api_down_chernobyl.json").read_bytes()
API_PREPARE = b'{"success":true,"url":"/down/uhC1c2"}'
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

    def test_bilingual_chinese_labels_are_chinese_first(self):
        block = (
            '<span class="p-1 fw-bold">双语</span>'
            '<span class="p-1 fw-bold">简体</span>'
            '<span class="p-1 fw-bold">繁体</span>'
            '<span class="p-1 fw-bold">英语</span>'
        ).encode("utf-8")

        self.assertEqual(self.mod._languages_from_block(block), ["zho"])

    def test_episode_search_extracts_subtitle_rows(self):
        rows = self.mod.parse_search_results(SEARCH_EPISODE)
        first = rows[0]
        self.assertEqual(first["subtitle_id"], "uhC1c2")
        self.assertEqual(first["media_id"], "27098632")
        self.assertIn("Chernobyl s01e01", first["release_info"])
        self.assertEqual(first["languages"], ["zho"])
        self.assertEqual(first["download_count"], 82)
        self.assertEqual(first["detail_url"], "https://subhd.tv/a/uhC1c2")

    def test_movie_search_extracts_multilingual_rows(self):
        rows = self.mod.parse_search_results(SEARCH_MOVIE)
        multilingual = next(item for item in rows if item["subtitle_id"] == "eVki86")
        self.assertEqual(multilingual["media_id"], "35010610")
        self.assertIn("eng", multilingual["languages"])
        self.assertIn("fra", multilingual["languages"])
        self.assertIn("spa", multilingual["languages"])

    def test_russian_label_maps_to_russian(self):
        block = '<span class="p-1 fw-bold">俄语</span>'.encode("utf-8")

        self.assertEqual(self.mod._languages_from_block(block), ["rus"])


class ParseDetailTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_detail_extracts_download_metadata(self):
        detail = self.mod.parse_detail_page(DETAIL_EPISODE, "https://subhd.tv/a/uhC1c2")
        self.assertEqual(detail["subtitle_id"], "uhC1c2")
        self.assertEqual(detail["media_title"], "Chernobyl")
        self.assertIn("Chernobyl s01e01", detail["release_info"])
        self.assertEqual(detail["format"], "srt")
        self.assertEqual(detail["languages"], ["zho"])

    def test_movie_detail_extracts_preview_file_and_metadata(self):
        # Pre-redesign markup: <a class="btn btn-danger down" sid=... href="/down/...">.
        detail = self.mod.parse_detail_page(DETAIL_MOVIE, "https://subhd.tv/a/PuCsoN")
        self.assertEqual(detail["subtitle_id"], "PuCsoN")
        self.assertEqual(detail["media_title"], "Project Hail Mary")
        self.assertEqual(detail["format"], "srt")
        self.assertEqual(detail["download_count"], 2343)
        self.assertEqual(detail["languages"], ["zho"])
        self.assertIn("1776336311088.srt", detail["files"])


class DownloadResponseTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_api_response_exposes_final_url(self):
        url = self.mod.parse_download_response(API_DOWN)
        self.assertEqual(url, "https://dl.subhd.tv/2026/03/1772600046334.srt")

    def test_download_api_response_rejects_refusal_with_site_message(self):
        body = '{"success":true,"pass":false,"msg":"下载频率过高，请不要再试","url":null}'.encode("utf-8")
        with self.assertRaisesRegex(ValueError, "refused: 下载频率过高"):
            self.mod.parse_download_response(body)

    def test_download_api_response_rejects_expired_temp_page(self):
        body = '{"success":false,"msg":"时间过长本临时页面已经失效","pass":false,"url":null}'.encode("utf-8")
        with self.assertRaisesRegex(ValueError, "临时页面已经失效"):
            self.mod.parse_download_response(body)

    def test_prepare_download_response_exposes_temp_page(self):
        url = self.mod.parse_prepare_download_response(API_PREPARE)
        self.assertEqual(url, "https://subhd.tv/down/uhC1c2")

    def test_prepare_download_response_rejects_failure(self):
        body = '{"success":false,"msg":"准备下载失败"}'.encode("utf-8")
        with self.assertRaisesRegex(ValueError, "prepare-download failed: 准备下载失败"):
            self.mod.parse_prepare_download_response(body)

    def test_temp_page_sid_comes_from_download_button(self):
        sid = self.mod.parse_temp_page_sid(DOWN_PAGE_EPISODE, "https://subhd.tv/down/other")
        self.assertEqual(sid, "uhC1c2")

    def test_temp_page_sid_falls_back_to_url(self):
        sid = self.mod.parse_temp_page_sid(b"<html></html>", "https://subhd.tv/down/Xy12ab?x=1")
        self.assertEqual(sid, "Xy12ab")


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
            [{"alpha3": "zho", "alpha2": "zh"}],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertEqual(called[:2], list(responses))
        first = results[0]
        self.assertEqual(first["provider"], "subhd")
        self.assertEqual(first["language"]["alpha3"], "zho")
        self.assertIn("episode", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], "uhC1c2")
        self.assertEqual(first["provider_payload"]["detail_url"], "https://subhd.tv/a/uhC1c2")

    def test_search_rechecks_detail_languages_before_returning_english(self):
        responses = {
            "https://subhd.tv/search/Chernobyl%20S01E01": SEARCH_EPISODE.replace(
                (
                    '<span class="p-1 fw-bold">双语</span>\n'
                    '            <span class="p-1 fw-bold">英语</span>'
                    '<span class="p-1 fw-bold">简体</span>'
                ).encode("utf-8"),
                '<span class="p-1 fw-bold">英语</span>'.encode("utf-8"),
                1,
            ),
            "https://subhd.tv/search/Chernobyl": SEARCH_EPISODE,
            "https://subhd.tv/a/uhC1c2": DETAIL_EPISODE,
        }
        provider = self.mod.SubHDProvider()

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])


PAYLOAD = {
    "provider": "subhd",
    "schema": 1,
    "subtitle_id": "uhC1c2",
    "detail_url": "https://subhd.tv/a/uhC1c2",
    "season": 1,
    "episode": 1,
}
FILE_URL = "https://dl.subhd.tv/2026/03/1772600046334.srt"


class SubHDProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _stub_provider(self, file_body=SRT_BODY, down_body=API_DOWN):
        provider = self.mod.SubHDProvider()
        called = []

        def get_stub(url, timeout=15, referer=None):
            del timeout
            called.append(("GET", url, referer))
            if url == "https://subhd.tv/a/uhC1c2":
                return DETAIL_EPISODE
            if url == "https://subhd.tv/down/uhC1c2":
                return DOWN_PAGE_EPISODE
            if url == FILE_URL:
                return file_body
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, payload, timeout=15, referer=None):
            del timeout
            called.append(("POST", url, referer, payload))
            if url == "https://subhd.tv/api/sub/prepare-download":
                return API_PREPARE
            if url == "https://subhd.tv/api/sub/down":
                return down_body
            raise AssertionError(f"unexpected POST: {url}")

        provider._http_get = get_stub
        provider._http_post_json = post_stub
        return provider, called

    def test_download_prepares_temp_page_posts_api_and_fetches_file(self):
        provider, called = self._stub_provider()

        result = provider.download(dict(PAYLOAD), {"alpha3": "zho", "alpha2": "zh"}, {})

        self.assertEqual(called, [
            ("GET", "https://subhd.tv/a/uhC1c2", None),
            ("POST", "https://subhd.tv/api/sub/prepare-download", "https://subhd.tv/a/uhC1c2",
             {"sid": "uhC1c2"}),
            ("GET", "https://subhd.tv/down/uhC1c2", "https://subhd.tv/a/uhC1c2"),
            ("POST", "https://subhd.tv/api/sub/down", "https://subhd.tv/down/uhC1c2",
             {"sid": "uhC1c2"}),
            ("GET", FILE_URL, "https://subhd.tv/down/uhC1c2"),
        ])
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])
        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertNotIn("encoding", result)

    def test_download_accepts_payload_with_legacy_download_url(self):
        provider, called = self._stub_provider()
        payload = dict(PAYLOAD, download_url="https://subhd.tv/down/uhC1c2")

        result = provider.download(payload, {"alpha3": "zho", "alpha2": "zh"}, {})

        self.assertEqual(called[1][1], "https://subhd.tv/api/sub/prepare-download")
        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)

    def test_download_returns_archive_bytes_for_zip_final_file(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("Chernobyl.S01E01.long.name.srt", b"1\n00:00 --> 00:01\nhi\n")
            archive.writestr("Chernobyl.S01E01.srt", SRT_BODY)
            archive.writestr("readme.txt", b"ignore")
        zip_body = archive_bytes.getvalue()
        provider, _ = self._stub_provider(file_body=zip_body)

        result = provider.download(dict(PAYLOAD), {"alpha3": "zho", "alpha2": "zh"}, {})

        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(zip_body).hexdigest())
        # Member selection (shortest .srt) is preserved; extraction is left to the host.
        self.assertEqual(result["member"], "Chernobyl.S01E01.srt")
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_refusal_is_not_retried(self):
        refusal = '{"success":true,"pass":false,"msg":"下载频率过高，请不要再试","url":null}'.encode("utf-8")
        provider, called = self._stub_provider(down_body=refusal)

        with self.assertRaisesRegex(ValueError, "下载频率过高"):
            provider.download(dict(PAYLOAD), {"alpha3": "zho", "alpha2": "zh"}, {})

        posts = [item[1] for item in called if item[0] == "POST"]
        self.assertEqual(posts, [
            "https://subhd.tv/api/sub/prepare-download",
            "https://subhd.tv/api/sub/down",
        ])

    def test_download_requires_subtitle_id_and_detail_url(self):
        provider, _ = self._stub_provider()
        with self.assertRaisesRegex(ValueError, "requires subtitle_id and detail_url"):
            provider.download({"subtitle_id": "uhC1c2"}, {"alpha3": "zho"}, {})

    def test_download_rejects_empty_final_file(self):
        provider, _ = self._stub_provider(file_body=b"")
        with self.assertRaisesRegex(ValueError, "empty subtitle"):
            provider.download(dict(PAYLOAD), {"alpha3": "zho", "alpha2": "zh"}, {})

    def test_download_rejects_html_error_body(self):
        provider, _ = self._stub_provider(file_body=b"<!DOCTYPE html><html><body>error</body></html>")
        with self.assertRaisesRegex(ValueError, "HTML"):
            provider.download(dict(PAYLOAD), {"alpha3": "zho", "alpha2": "zh"}, {})

    def test_archive_member_selection_prefers_shortest_srt(self):
        archive_body = io.BytesIO()
        with zipfile.ZipFile(archive_body, "w") as archive:
            archive.writestr("Chernobyl.S01E01.ass", b"styles")
            archive.writestr("Chernobyl.S01E01.long.srt", b"data")
            archive.writestr("Chernobyl.S01E01.srt", b"data")

        self.assertEqual(
            self.mod._select_archive_member(archive_body.getvalue()),
            "Chernobyl.S01E01.srt",
        )

    def test_archive_member_selection_rejects_archive_without_subtitles(self):
        archive_body = io.BytesIO()
        with zipfile.ZipFile(archive_body, "w") as archive:
            archive.writestr("readme.txt", b"no subtitles here")

        with self.assertRaisesRegex(ValueError, "no subtitle files"):
            self.mod._select_archive_member(archive_body.getvalue())


if __name__ == "__main__":
    unittest.main()
