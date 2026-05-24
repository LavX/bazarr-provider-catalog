import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "fansubs"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

SEARCH_FIXTURE = (FIXTURE_DIR / "fansubs_search_devil_may_cry.html").read_bytes()
DETAIL_FIXTURE = (FIXTURE_DIR / "fansubs_detail_devil_may_cry_2025.html").read_bytes()


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "fansubs_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FansubsQueryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_with_year_emits_precise_then_loose(self):
        queries = self.mod.build_queries(
            {"kind": "movie", "title": "Ghost in the Shell", "year": 1995}
        )
        self.assertEqual(queries, ["Ghost in the Shell 1995", "Ghost in the Shell"])

    def test_episode_searches_series_title_only(self):
        queries = self.mod.build_queries(
            {"kind": "episode", "series": "Devil May Cry", "season": 1, "episode": 8}
        )
        self.assertEqual(queries, ["Devil May Cry"])

    def test_missing_required_fields_return_empty(self):
        self.assertEqual(self.mod.build_queries({"kind": "movie"}), [])
        self.assertEqual(self.mod.build_queries({"kind": "episode"}), [])


class FansubsParsingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_decode_page_defaults_to_windows_1251(self):
        body = "Привет".encode("cp1251")

        self.assertEqual(self.mod.decode_page(body), "Привет")

    def test_parse_search_results_extracts_archive_media_only(self):
        results = self.mod.parse_search_results(SEARCH_FIXTURE)

        self.assertEqual([item["media_id"] for item in results], ["1505", "7297"])
        self.assertEqual(results[1]["title"], "Devil May Cry (2025)")
        self.assertEqual(
            results[1]["detail_url"], "http://fansubs.ru/base.php?id=7297"
        )

    def test_parse_detail_page_extracts_subtitle_rows_and_authors(self):
        details = self.mod.parse_detail_page(DETAIL_FIXTURE, "7297")

        self.assertEqual(details["media_id"], "7297")
        self.assertEqual(details["title"], "Devil May Cry (2025)")
        self.assertEqual(len(details["subtitles"]), 2)
        first = details["subtitles"][0]
        self.assertEqual(first["subtitle_id"], "13605")
        self.assertEqual(first["title"], "ONA 1-12")
        self.assertEqual(first["format"], "ass")
        self.assertEqual(first["date"], "22.05.26")
        self.assertEqual(first["author"], "Katsura")
        self.assertTrue(first["has_note"])

    def test_episode_range_matching_handles_packs(self):
        self.assertTrue(self.mod.episode_range_matches("ONA 1-12", 8))
        self.assertTrue(self.mod.episode_range_matches("TV 08", 8))
        self.assertFalse(self.mod.episode_range_matches("ONA 13-16", 8))

    def test_select_subtitle_file_prefers_requested_episode(self):
        names = [
            "Devil May Cry - 01.ass",
            "Devil May Cry - 08.ass",
            "Devil May Cry - 09.ass",
        ]

        selected = self.mod.select_subtitle_file(
            names, {"kind": "episode", "episode": 8}
        )

        self.assertEqual(selected, "Devil May Cry - 08.ass")

    def test_episode_year_match_lifts_score_above_episode_only_match(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Devil May Cry", "year": 2025, "episode": 8},
            "Devil May Cry (2025)",
            "ONA 1-12",
        )

        self.assertIn("year", matches)
        self.assertEqual(self.mod.compute_score(matches), 100)
        self.assertEqual(self.mod.compute_score(["series", "season", "episode"]), 95)


class FansubsProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_russian_candidates_for_matching_episode_pack(self):
        provider = self.mod.FansubsProvider()

        def post(url, data, timeout=15):
            self.assertEqual(url, "http://fansubs.ru/search.php")
            self.assertIn("query", data)
            return SEARCH_FIXTURE, {}

        def get(url, timeout=15):
            if url == "http://fansubs.ru/base.php?id=1505":
                return b"<html></html>"
            if url == "http://fansubs.ru/base.php?id=7297":
                return DETAIL_FIXTURE
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_post = post
        provider._http_get = get

        results = provider.search(
            video={
                "kind": "episode",
                "series": "Devil May Cry",
                "season": 1,
                "episode": 8,
            },
            languages=[{"alpha3": "rus", "alpha2": "ru", "hi": False, "forced": False}],
            config={},
        )

        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["provider"], "fansubs")
        self.assertEqual(candidate["language"]["alpha3"], "rus")
        self.assertEqual(candidate["provider_payload"]["subtitle_id"], "13605")
        self.assertEqual(candidate["provider_payload"]["format"], "ass")
        self.assertIn("episode", candidate["matches"])
        self.assertEqual(candidate["page_link"], "http://fansubs.ru/base.php?id=7297")

    def test_search_skips_unsupported_languages_without_network(self):
        provider = self.mod.FansubsProvider()
        provider._http_post = mock.Mock(side_effect=AssertionError("network called"))

        results = provider.search(
            video={"kind": "movie", "title": "Devil May Cry"},
            languages=[{"alpha3": "eng", "alpha2": "en"}],
            config={},
        )

        self.assertEqual(results, [])

    def test_search_retries_site_rate_limit_page_once(self):
        provider = self.mod.FansubsProvider()
        calls = []

        def post(url, data, timeout=15):
            calls.append(data["query"])
            if len(calls) == 1:
                return (
                    "Repeat the search in 5 seconds".encode("cp1251"),
                    {},
                )
            return SEARCH_FIXTURE, {}

        def get(url, timeout=15):
            if url == "http://fansubs.ru/base.php?id=1505":
                return b"<html></html>"
            if url == "http://fansubs.ru/base.php?id=7297":
                return DETAIL_FIXTURE
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_post = post
        provider._http_get = get

        with mock.patch.object(self.mod.time, "sleep") as sleep:
            results = provider.search(
                video={
                    "kind": "episode",
                    "series": "Devil May Cry",
                    "season": 1,
                    "episode": 8,
                },
                languages=[{"alpha3": "rus", "alpha2": "ru"}],
                config={},
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(calls, ["Devil May Cry", "Devil May Cry"])
        sleep.assert_called_once_with(5)


class FansubsProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_download_body(self, body, headers):
        provider = self.mod.FansubsProvider()

        def post(url, data, timeout=15):
            self.assertEqual(url, "http://fansubs.ru/base.php")
            self.assertEqual(data["srt"], "13605")
            return body, headers

        provider._http_post = post
        return provider

    def test_download_returns_direct_subtitle_payload(self):
        body = b"[Script Info]\nTitle: Direct\n"
        provider = self._provider_with_download_body(
            body,
            {
                "content-disposition": 'attachment; filename="devil_may_cry.ass"',
                "content-type": "text/plain",
            },
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "ass",
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, body)
        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())

    def test_download_extracts_matching_file_from_zip(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Devil May Cry - 01.ass", b"episode one")
            zf.writestr("Devil May Cry - 08.ass", b"episode eight")
        provider = self._provider_with_download_body(
            archive.getvalue(),
            {"content-disposition": 'attachment; filename="devil_may_cry.zip"'},
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "ass",
                "video": {"kind": "episode", "episode": 8},
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, b"episode eight")
        self.assertEqual(result["format"], "ass")

    def test_download_extracts_matching_file_from_rar_reader(self):
        provider = self._provider_with_download_body(
            b"rar bytes",
            {"content-disposition": 'attachment; filename="devil_may_cry.rar"'},
        )

        with mock.patch.object(self.mod, "_is_rar_archive", return_value=True):
            with mock.patch.object(
                self.mod,
                "_extract_rar_files",
                return_value=[
                    ("Devil May Cry - 01.ass", b"episode one"),
                    ("Devil May Cry - 08.ass", b"episode eight"),
                ],
            ):
                result = provider.download(
                    {
                        "provider": "fansubs",
                        "schema": 1,
                        "subtitle_id": "13605",
                        "format": "ass",
                        "video": {"kind": "episode", "episode": 8},
                    },
                    {"alpha3": "rus", "alpha2": "ru"},
                    {},
                )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, b"episode eight")
        self.assertEqual(result["format"], "ass")

    def test_rar_extraction_falls_back_to_unar_when_rarfile_read_fails(self):
        with mock.patch.object(
            self.mod, "_extract_rar_files_with_rarfile", side_effect=OSError("bad rar")
        ):
            with mock.patch.object(
                self.mod,
                "_extract_rar_files_with_unar",
                return_value=[("Devil May Cry - 08.srt", b"episode eight")],
            ) as fallback:
                files = self.mod._extract_rar_files(b"rar bytes")

        self.assertEqual(files, [("Devil May Cry - 08.srt", b"episode eight")])
        fallback.assert_called_once_with(b"rar bytes")

    def test_rar_extraction_falls_back_to_7z_when_only_7z_is_available(self):
        def which(command):
            return "/usr/bin/7z" if command == "7z" else None

        with mock.patch.object(
            self.mod, "_extract_rar_files_with_rarfile", side_effect=OSError("bad rar")
        ):
            with mock.patch.object(self.mod.shutil, "which", side_effect=which):
                with mock.patch.object(
                    self.mod,
                    "_extract_rar_files_with_7z",
                    return_value=[("Devil May Cry - 08.srt", b"episode eight")],
                    create=True,
                ) as fallback:
                    files = self.mod._extract_rar_files(b"rar bytes")

        self.assertEqual(files, [("Devil May Cry - 08.srt", b"episode eight")])
        fallback.assert_called_once_with(b"rar bytes")


if __name__ == "__main__":
    unittest.main()
