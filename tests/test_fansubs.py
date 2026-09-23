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

    def test_search_stores_season_and_episode_in_provider_payload(self):
        provider = self.mod.FansubsProvider()

        def post(url, data, timeout=15):
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
            languages=[{"alpha3": "rus", "alpha2": "ru"}],
            config={},
        )

        payload = results[0]["provider_payload"]
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 8)

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
        # The host normalizes encoding; the worker must not guess a codepage.
        self.assertNotIn("encoding", result)

    def test_download_pins_zip_member_matching_requested_episode(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Devil May Cry - 01.ass", b"episode one")
            zf.writestr("Devil May Cry - 08.ass", b"episode eight")
        archive_bytes = archive.getvalue()
        provider = self._provider_with_download_body(
            archive_bytes,
            {"content-disposition": 'attachment; filename="devil_may_cry.zip"'},
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "ass",
                "season": 1,
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        self.assertNotIn("content_b64", result)
        data = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, archive_bytes)
        self.assertEqual(
            result["archive_sha256"], hashlib.sha256(archive_bytes).hexdigest()
        )
        # A multi-member episode pack pins the member carrying the requested
        # episode rather than deferring to the host's generic episode pick.
        self.assertEqual(result["member"], "Devil May Cry - 08.ass")
        self.assertNotIn("episode", result)
        self.assertNotIn("encoding", result)

    def test_download_defers_when_no_member_carries_requested_episode(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Devil May Cry - 01.ass", b"episode one")
            zf.writestr("Devil May Cry - 02.ass", b"episode two")
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
                "season": 1,
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        # Episode 8 is absent from every member: pinning a wrong member would
        # hard-fail the host download, so defer to host-side episode selection.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 8)

    def test_download_defers_when_multiple_members_carry_episode(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Devil May Cry - 08 [Katsura].ass", b"group one")
            zf.writestr("Devil May Cry - 08 [Other].ass", b"group two")
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
                "season": 1,
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        # Two members both carry episode 8 and nothing breaks the tie: defer
        # instead of pinning an arbitrary one.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 8)

    def test_download_ignores_sidecar_and_resolution_lookalikes(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            # 720p must not match episode 720; the real episode-8 member must win,
            # and the __MACOSX sidecar must be skipped entirely.
            zf.writestr("__MACOSX/._Devil May Cry - 08.ass", b"junk")
            zf.writestr("Devil May Cry - 08 [720p].ass", b"episode eight")
            zf.writestr("Devil May Cry - 09 [720p].ass", b"episode nine")
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
                "season": 1,
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        self.assertEqual(result["member"], "Devil May Cry - 08 [720p].ass")

    def test_download_defers_when_codec_token_looks_like_episode(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            # "x264" must NOT satisfy a request for episode 264: pinning this
            # member would silently deliver episode 5 for a request for 264.
            zf.writestr("Devil May Cry - 05 [x264].ass", b"episode five")
            zf.writestr("Devil May Cry - 06.ass", b"episode six")
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
                "season": 1,
                "episode": 264,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        # No member carries episode 264; the codec tag "x264" is not a match, so
        # defer to host-side episode selection instead of pinning a wrong member.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 264)

    def test_download_defers_when_resolution_token_looks_like_episode(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            # "720p" must NOT satisfy a request for episode 720: pinning this
            # member would silently deliver episode 12 for a request for 720.
            zf.writestr("Devil May Cry - 12 [720p].ass", b"episode twelve")
            zf.writestr("Devil May Cry - 13.ass", b"episode thirteen")
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
                "season": 1,
                "episode": 720,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        # No member carries episode 720; the resolution tag "720p" is not a match,
        # so defer to host-side episode selection instead of pinning a wrong member.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 720)

    def test_download_defers_when_match_is_only_in_wrong_season(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Series S02E08.srt", b"season two episode eight")
            zf.writestr("Series S02E09.srt", b"season two episode nine")
        provider = self._provider_with_download_body(
            archive.getvalue(),
            {"content-disposition": 'attachment; filename="series.zip"'},
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "srt",
                "season": 1,
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        # The only episode-8 member is tagged S02; the request is S01E08, so the
        # bare-number fallback must not claim it. Defer to host episode selection.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 8)

    def test_download_pins_member_by_season_episode_marker(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Series S01E08.srt", b"season one episode eight")
            zf.writestr("Series S02E08.srt", b"season two episode eight")
        provider = self._provider_with_download_body(
            archive.getvalue(),
            {"content-disposition": 'attachment; filename="series.zip"'},
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "srt",
                "season": 1,
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        # A pack repeating episode 8 across seasons must pin by BOTH season and
        # episode, not the bare number.
        self.assertEqual(result["member"], "Series S01E08.srt")

    def test_download_carries_episode_from_nested_video_payload(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
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

        self.assertEqual(result["episode"], 8)

    def test_download_returns_rar_archive_for_host_extraction(self):
        rar_bytes = b"Rar!\x1a\x07\x00" + b"payload bytes that are not a real archive"
        provider = self._provider_with_download_body(
            rar_bytes,
            {"content-disposition": 'attachment; filename="devil_may_cry.rar"'},
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "ass",
                "episode": 8,
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        self.assertNotIn("content_b64", result)
        data = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, rar_bytes)
        self.assertEqual(
            result["archive_sha256"], hashlib.sha256(rar_bytes).hexdigest()
        )
        self.assertEqual(result["episode"], 8)

    def test_download_archive_episode_is_none_when_absent(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Ghost in the Shell.srt", b"movie subtitle")
        provider = self._provider_with_download_body(
            archive.getvalue(),
            {"content-disposition": 'attachment; filename="ghost.zip"'},
        )

        result = provider.download(
            {
                "provider": "fansubs",
                "schema": 1,
                "subtitle_id": "13605",
                "format": "srt",
            },
            {"alpha3": "rus", "alpha2": "ru"},
            {},
        )

        self.assertIn("archive_b64", result)
        self.assertIsNone(result["episode"])

    def test_download_rejects_empty_body(self):
        provider = self._provider_with_download_body(
            b"",
            {"content-disposition": 'attachment; filename="devil_may_cry.srt"'},
        )

        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "fansubs", "schema": 1, "subtitle_id": "13605"},
                {"alpha3": "rus", "alpha2": "ru"},
                {},
            )

    def test_download_rejects_html_error_page(self):
        provider = self._provider_with_download_body(
            b"<!DOCTYPE html><html><body>not found</body></html>",
            {"content-type": "text/html"},
        )

        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "fansubs", "schema": 1, "subtitle_id": "13605"},
                {"alpha3": "rus", "alpha2": "ru"},
                {},
            )

    def test_download_requires_subtitle_id(self):
        provider = self.mod.FansubsProvider()

        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "fansubs", "schema": 1},
                {"alpha3": "rus", "alpha2": "ru"},
                {},
            )


if __name__ == "__main__":
    unittest.main()
