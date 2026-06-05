import base64
import hashlib
import importlib.util
import json
import lzma
import urllib.error
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "animetosho"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "animetosho_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERIES_ENTRIES = (FIXTURE_DIR / "animetosho_series_277518.json").read_bytes()
TORRENT_DETAIL = (FIXTURE_DIR / "animetosho_torrent_616869.json").read_bytes()
VIDEO = json.loads((FIXTURE_DIR / "animetosho_video_solo_leveling_s01e12.json").read_text())
ASS_BODY = b"[Script Info]\nTitle: AnimeTosho fixture\n"


class ParseSeriesEntriesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_filters_complete_entries_limits_then_orders_newest_first(self):
        body = json.dumps(
            [
                {"id": 1, "status": "complete", "timestamp": 10},
                {"id": 2, "status": "pending", "timestamp": 99},
                {"id": 3, "status": "complete", "timestamp": 30},
                {"id": 4, "status": "complete", "timestamp": 20},
            ]
        ).encode("utf-8")

        entries = self.mod.parse_series_entries(body, search_threshold=2)

        self.assertEqual([item["id"] for item in entries], [3, 1])

    def test_live_series_fixture_keeps_ready_entries(self):
        entries = self.mod.parse_series_entries(SERIES_ENTRIES, search_threshold=2)

        self.assertEqual([item["id"] for item in entries], [616869, 613089])
        self.assertTrue(all(item["status"] == "complete" for item in entries))


class ParseTorrentSubtitlesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_live_torrent_fixture_extracts_subtitle_attachments(self):
        entry = {"id": 616869, "title": "[ToonsHub] Solo Leveling S01E12"}
        rows = self.mod.parse_torrent_subtitles(TORRENT_DETAIL, entry)

        first = rows[0]
        self.assertEqual(first["subtitle_id"], 1979653)
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertEqual(first["language"]["alpha2"], "en")
        self.assertEqual(first["format"], "ass")
        self.assertEqual(first["filename"], "Solo.Leveling.S01E12.Arise.2160p.B-Global.WEB-DL.MULTi.AAC2.0.H.264.MSubs-ToonsHub.mkv")
        self.assertEqual(first["release_info"], "[ToonsHub] Solo Leveling S01E12")
        self.assertEqual(first["download_url"], "https://animetosho.org/storage/attach/001e3505/1979653.xz")

    def test_bibliographic_language_codes_canonicalise(self):
        body = _torrent_body(
            [
                {
                    "id": 10,
                    "type": "subtitle",
                    "info": {"lang": "ger", "name": "German (Germany)", "codec": "ASS"},
                }
            ]
        )

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"})

        self.assertEqual(rows[0]["language"]["alpha3"], "deu")
        self.assertEqual(rows[0]["language"]["alpha2"], "de")

    def test_missing_language_defaults_to_english(self):
        body = _torrent_body(
            [
                {
                    "id": 11,
                    "type": "subtitle",
                    "info": {"name": "Unknown subtitle", "codec": "SRT"},
                }
            ]
        )

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"})

        self.assertEqual(rows[0]["language"]["alpha3"], "eng")
        self.assertEqual(rows[0]["language"]["alpha2"], "en")

    def test_unsupported_valid_language_is_dropped_instead_of_relabelled_english(self):
        rows = self.mod.parse_torrent_subtitles(
            TORRENT_DETAIL,
            {"id": 616869, "title": "[ToonsHub] Solo Leveling S01E12"},
        )

        self.assertNotIn(2001915, {row["subtitle_id"] for row in rows})

    def test_forced_track_names_set_forced_language_flag(self):
        rows = self.mod.parse_torrent_subtitles(
            TORRENT_DETAIL,
            {"id": 616869, "title": "[ToonsHub] Solo Leveling S01E12"},
        )
        forced = {row["subtitle_id"]: row for row in rows if row["subtitle_id"] in {2001923, 2001924, 2001925, 2001926, 2001927}}

        self.assertEqual(set(forced), {2001923, 2001924, 2001925, 2001926, 2001927})
        self.assertTrue(all(row["forced"] for row in forced.values()))
        self.assertTrue(all(row["language"]["forced"] for row in forced.values()))

    def test_brazilian_portuguese_preserves_country(self):
        body = _torrent_body(
            [
                {
                    "id": 12,
                    "type": "subtitle",
                    "info": {"lang": "por", "name": "Portuguese (Brazil)", "codec": "ASS"},
                }
            ]
        )

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"})

        self.assertEqual(rows[0]["language"]["alpha3"], "por")
        self.assertEqual(rows[0]["language"]["alpha2"], "pt")
        self.assertEqual(rows[0]["language"]["country_alpha2"], "BR")

    def test_spanish_country_variants_preserve_country(self):
        rows = self.mod.parse_torrent_subtitles(
            TORRENT_DETAIL,
            {"id": 616869, "title": "[ToonsHub] Solo Leveling S01E12"},
        )
        by_id = {row["subtitle_id"]: row for row in rows}

        self.assertEqual(by_id[1979656]["language"]["country_alpha2"], "MX")
        self.assertEqual(by_id[1979657]["language"]["country_alpha2"], "ES")
        self.assertEqual(by_id[2001923]["language"]["country_alpha2"], "ES")
        self.assertEqual(by_id[2001927]["language"]["country_alpha2"], "MX")

    def test_unsupported_subtitle_codecs_are_dropped(self):
        body = _torrent_body(
            [
                {
                    "id": 13,
                    "type": "subtitle",
                    "info": {"lang": "eng", "name": "English PGS", "codec": "PGS"},
                }
            ]
        )

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"})

        self.assertEqual(rows, [])

    def test_batch_torrent_filters_media_files_to_requested_episode(self):
        body = json.dumps(
            {
                "files": [
                    {
                        "filename": "Solo.Leveling.S01E02.1080p.WEB-DL.mkv",
                        "attachments": [
                            {
                                "id": 14,
                                "type": "subtitle",
                                "info": {"lang": "eng", "name": "English", "codec": "ASS"},
                            }
                        ],
                    },
                    {
                        "filename": "Solo.Leveling.S01E12.1080p.WEB-DL.mkv",
                        "attachments": [
                            {
                                "id": 15,
                                "type": "subtitle",
                                "info": {"lang": "eng", "name": "English", "codec": "ASS"},
                            }
                        ],
                    },
                ]
            }
        ).encode("utf-8")

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"}, VIDEO)

        self.assertEqual([row["subtitle_id"] for row in rows], [15])

    def test_markerless_files_in_multi_file_batch_are_rejected(self):
        body = json.dumps(
            {
                "files": [
                    {
                        "filename": "Solo Leveling - 01.mkv",
                        "attachments": [
                            {
                                "id": 20,
                                "type": "subtitle",
                                "info": {"lang": "eng", "name": "English", "codec": "ASS"},
                            }
                        ],
                    },
                    {
                        "filename": "Solo Leveling - 12.mkv",
                        "attachments": [
                            {
                                "id": 21,
                                "type": "subtitle",
                                "info": {"lang": "eng", "name": "English", "codec": "ASS"},
                            }
                        ],
                    },
                ]
            }
        ).encode("utf-8")

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"}, VIDEO)

        self.assertEqual(rows, [])

    def test_single_markerless_media_file_is_accepted(self):
        body = json.dumps(
            {
                "files": [
                    {
                        "filename": "Solo Leveling - 12.mkv",
                        "attachments": [
                            {
                                "id": 22,
                                "type": "subtitle",
                                "info": {"lang": "eng", "name": "English", "codec": "ASS"},
                            }
                        ],
                    }
                ]
            }
        ).encode("utf-8")

        rows = self.mod.parse_torrent_subtitles(body, {"id": 1, "title": "Release"}, VIDEO)

        self.assertEqual([row["subtitle_id"] for row in rows], [22])


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_matches_include_legacy_explicit_fields(self):
        matches = self.mod.derive_matches(
            VIDEO,
            "Solo.Leveling.S01E12.Arise.2160p.B-Global.WEB-DL.MULTi.AAC2.0.H.264.MSubs-ToonsHub.mkv",
        )

        self.assertIn("title", matches)
        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)
        self.assertIn("tvdb_id", matches)


class AnimeToshoProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_uses_anidb_episode_id_and_returns_requested_language(self):
        provider = self.mod.AnimeToshoProvider()
        called = []
        responses = {
            "https://feed.animetosho.org/json?eid=277518": SERIES_ENTRIES,
            "https://feed.animetosho.org/json?show=torrent&id=616869": TORRENT_DETAIL,
        }

        def stub(url, timeout=15):
            del timeout
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"search_threshold": 1, "request_delay_ms": 0},
        )

        self.assertEqual(called, list(responses))
        self.assertGreater(len(results), 0)
        first = results[0]
        self.assertEqual(first["provider"], "animetosho")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertEqual(first["provider_payload"]["subtitle_id"], 1979653)
        self.assertEqual(first["provider_payload"]["download_url"], "https://animetosho.org/storage/attach/001e3505/1979653.xz")
        self.assertIn("episode", first["matches"])
        self.assertEqual(first["score"], 96)

    def test_search_skips_non_episode_and_missing_anidb_episode_id(self):
        provider = self.mod.AnimeToshoProvider()

        def fail_get(url, timeout=15):
            del timeout
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fail_get

        self.assertEqual(provider.search({"kind": "movie", "title": "Akira"}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "episode", "series": "Solo Leveling"}, [{"alpha3": "eng"}], {}), [])

    def test_search_continues_after_one_torrent_detail_error(self):
        provider = self.mod.AnimeToshoProvider()
        called = []
        series_entries = json.dumps(
            [
                {"id": 1, "status": "complete", "timestamp": 20, "title": "bad detail"},
                {"id": 616869, "status": "complete", "timestamp": 10, "title": "[ToonsHub] Solo Leveling S01E12"},
            ]
        ).encode("utf-8")

        def stub(url, timeout=15):
            del timeout
            called.append(url)
            if url == "https://feed.animetosho.org/json?eid=277518":
                return series_entries
            if url == "https://feed.animetosho.org/json?show=torrent&id=1":
                raise urllib.error.URLError("temporary detail failure")
            if url == "https://feed.animetosho.org/json?show=torrent&id=616869":
                return TORRENT_DETAIL
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"search_threshold": 2, "request_delay_ms": 0},
        )

        self.assertIn("https://feed.animetosho.org/json?show=torrent&id=1", called)
        self.assertTrue(results)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], 1979653)

    def test_search_honors_requested_portuguese_country(self):
        provider = self.mod.AnimeToshoProvider()
        series_entries = json.dumps(
            [{"id": 1, "status": "complete", "timestamp": 10, "title": "release"}]
        ).encode("utf-8")
        torrent_detail = _torrent_body(
            [
                {
                    "id": 16,
                    "type": "subtitle",
                    "info": {"lang": "por", "name": "Portuguese (Brazil)", "codec": "ASS"},
                },
                {
                    "id": 17,
                    "type": "subtitle",
                    "info": {"lang": "por", "name": "Portuguese (Portugal)", "codec": "ASS"},
                },
            ]
        )

        def stub(url, timeout=15):
            del timeout
            if url == "https://feed.animetosho.org/json?eid=277518":
                return series_entries
            if url == "https://feed.animetosho.org/json?show=torrent&id=1":
                return torrent_detail
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "PT"}],
            {"search_threshold": 1, "request_delay_ms": 0},
        )

        self.assertEqual([result["provider_payload"]["subtitle_id"] for result in results], [17])

    def test_search_honors_country_alias_in_language_request(self):
        provider = self.mod.AnimeToshoProvider()
        series_entries = json.dumps(
            [{"id": 1, "status": "complete", "timestamp": 10, "title": "release"}]
        ).encode("utf-8")
        torrent_detail = _torrent_body(
            [
                {
                    "id": 23,
                    "type": "subtitle",
                    "info": {"lang": "por", "name": "Portuguese (Brazil)", "codec": "ASS"},
                },
                {
                    "id": 24,
                    "type": "subtitle",
                    "info": {"lang": "por", "name": "Portuguese (Portugal)", "codec": "ASS"},
                },
            ]
        )

        def stub(url, timeout=15):
            del timeout
            if url == "https://feed.animetosho.org/json?eid=277518":
                return series_entries
            if url == "https://feed.animetosho.org/json?show=torrent&id=1":
                return torrent_detail
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "por", "alpha2": "pt", "country": "PT"}],
            {"search_threshold": 1, "request_delay_ms": 0},
        )

        self.assertEqual([result["provider_payload"]["subtitle_id"] for result in results], [24])

    def test_search_honors_requested_forced_flag(self):
        provider = self.mod.AnimeToshoProvider()
        series_entries = json.dumps(
            [{"id": 1, "status": "complete", "timestamp": 10, "title": "release"}]
        ).encode("utf-8")

        def stub(url, timeout=15):
            del timeout
            if url == "https://feed.animetosho.org/json?eid=277518":
                return series_entries
            if url == "https://feed.animetosho.org/json?show=torrent&id=1":
                return TORRENT_DETAIL
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "spa", "alpha2": "es", "country_alpha2": "ES", "forced": False}],
            {"search_threshold": 1, "request_delay_ms": 0},
        )

        self.assertEqual([result["provider_payload"]["subtitle_id"] for result in results], [1979657])

    def test_search_honors_requested_hi_flag(self):
        provider = self.mod.AnimeToshoProvider()
        series_entries = json.dumps(
            [{"id": 1, "status": "complete", "timestamp": 10, "title": "release"}]
        ).encode("utf-8")
        torrent_detail = _torrent_body(
            [
                {
                    "id": 18,
                    "type": "subtitle",
                    "info": {"lang": "eng", "name": "English", "codec": "ASS"},
                },
                {
                    "id": 19,
                    "type": "subtitle",
                    "info": {"lang": "eng", "name": "English SDH", "codec": "ASS"},
                },
            ]
        )

        def stub(url, timeout=15):
            del timeout
            if url == "https://feed.animetosho.org/json?eid=277518":
                return series_entries
            if url == "https://feed.animetosho.org/json?show=torrent&id=1":
                return torrent_detail
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            {"search_threshold": 1, "request_delay_ms": 0},
        )

        self.assertEqual([result["provider_payload"]["subtitle_id"] for result in results], [18])


class AnimeToshoProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_decompresses_xz_attachment(self):
        provider = self.mod.AnimeToshoProvider()

        def stub(url, timeout=15):
            del timeout
            self.assertEqual(url, "https://animetosho.org/storage/attach/001e3505/1979653.xz")
            return lzma.compress(ASS_BODY)

        provider._http_get = stub
        result = provider.download(
            {
                "provider": "animetosho",
                "download_url": "https://animetosho.org/storage/attach/001e3505/1979653.xz",
                "format": "ass",
            },
            {"alpha3": "eng"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), ASS_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(ASS_BODY).hexdigest())
        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_type"], "text/x-ssa")
        self.assertFalse(result["empty"])

    def test_download_rejects_non_xz_bytes(self):
        provider = self.mod.AnimeToshoProvider()
        provider._http_get = lambda url, timeout=15: b"not xz"

        with self.assertRaisesRegex(ValueError, "xz"):
            provider.download({"download_url": "https://example.invalid/sub"}, {"alpha3": "eng"}, {})


def _torrent_body(attachments):
    body = {
        "files": [
            {
                "filename": "Solo.Leveling.S01E12.1080p.WEB-DL.mkv",
                "attachments": attachments,
            }
        ]
    }
    return json.dumps(body).encode("utf-8")
