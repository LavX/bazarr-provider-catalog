import base64
import hashlib
import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "gestdown"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

SHOW_LOOKUP = (FIXTURE_DIR / "gestdown_show_breaking_bad.json").read_bytes()
SUBTITLES_ENGLISH = (
    FIXTURE_DIR / "gestdown_subtitles_breaking_bad_s01e01_english.json"
).read_bytes()
SUBTITLE_BODY = (
    FIXTURE_DIR / "gestdown_download_breaking_bad_s01e01_english.srt"
).read_bytes()


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "gestdown_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GestdownParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_show_lookup_returns_public_show_ids(self):
        shows = self.mod.parse_show_lookup(SHOW_LOOKUP)

        self.assertEqual(len(shows), 1)
        self.assertEqual(shows[0]["id"], "31ffb6ce-c000-4079-8912-b3f72057baed")
        self.assertEqual(shows[0]["name"], "Breaking Bad")
        self.assertEqual(shows[0]["tvdb_id"], 81189)

    def test_parse_show_lookup_handles_missing_show(self):
        self.assertEqual(self.mod.parse_show_lookup(b'{"shows": []}'), [])

    def test_parse_subtitles_filters_incomplete_and_keeps_metadata(self):
        payload = json.loads(SUBTITLES_ENGLISH)
        payload["matchingSubtitles"].append(
            {
                "subtitleId": "draft-id",
                "version": "draft",
                "completed": False,
                "hearingImpaired": False,
                "downloadUri": "/subtitles/download/draft-id",
                "qualities": [],
                "downloadCount": 0,
                "source": "Gestdown",
            }
        )

        entries = self.mod.parse_subtitle_results(json.dumps(payload).encode("utf-8"))

        self.assertGreaterEqual(len(entries), 1)
        ids = {entry["subtitle_id"] for entry in entries}
        self.assertIn("69cf7d79-052c-4f12-a57d-995d77de43ad", ids)
        self.assertNotIn("draft-id", ids)
        first = entries[0]
        self.assertEqual(first["version"], "0tv")
        self.assertEqual(first["release_info"], "0tv")
        self.assertEqual(first["download_count"], 418)
        self.assertFalse(first["hearing_impaired"])
        self.assertEqual(
            first["download_url"],
            "https://api.gestdown.info/subtitles/download/69cf7d79-052c-4f12-a57d-995d77de43ad",
        )

    def test_release_list_splits_comma_versions(self):
        payload = {
            "matchingSubtitles": [
                {
                    "subtitleId": "multi",
                    "version": "WEB-DL, HDTV, BluRay",
                    "completed": True,
                    "hearingImpaired": True,
                    "downloadUri": "/subtitles/download/multi",
                    "qualities": ["720p", "1080p"],
                    "downloadCount": 7,
                    "source": "Gestdown",
                }
            ]
        }

        entries = self.mod.parse_subtitle_results(json.dumps(payload).encode("utf-8"))

        self.assertEqual(entries[0]["releases"], ["WEB-DL", "HDTV", "BluRay"])
        self.assertEqual(entries[0]["release_info"], "WEB-DL\nHDTV\nBluRay")
        self.assertTrue(entries[0]["hearing_impaired"])
        self.assertEqual(entries[0]["qualities"], ["720p", "1080p"])


class GestdownLanguageTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_manifest_declares_built_in_region_and_script_variants(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))

        self.assertIn("pt-BR", manifest["languages"])
        self.assertIn("sr-Latn", manifest["languages"])
        self.assertIn("sr-Cyrl", manifest["languages"])

    def test_maps_common_worker_language_payloads(self):
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "eng", "alpha2": "en"}),
            "English",
        )
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "fra", "alpha2": "fr"}),
            "French",
        )
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "cat", "alpha2": "ca"}),
            "Català",
        )
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "eus", "alpha2": "eu"}),
            "Euskera",
        )
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "glg", "alpha2": "gl"}),
            "Galego",
        )
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "zho", "alpha2": "zh"}),
            "Chinese (Simplified)",
        )

    def test_maps_brazilian_portuguese_to_addic7ed_label(self):
        self.assertEqual(
            self.mod.gestdown_language_name(
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}
            ),
            "Portuguese (Brazilian)",
        )

    def test_plain_portuguese_keeps_base_label(self):
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "por", "alpha2": "pt"}),
            "Portuguese",
        )

    def test_maps_serbian_script_variants_to_addic7ed_labels(self):
        self.assertEqual(
            self.mod.gestdown_language_name(
                {"alpha3": "srp", "alpha2": "sr", "script": "Latn"}
            ),
            "Serbian (Latin)",
        )
        self.assertEqual(
            self.mod.gestdown_language_name(
                {"alpha3": "srp", "alpha2": "sr", "script": "Cyrl"}
            ),
            "Serbian (Cyrillic)",
        )

    def test_plain_serbian_keeps_base_label(self):
        self.assertEqual(
            self.mod.gestdown_language_name({"alpha3": "srp", "alpha2": "sr"}),
            "Serbian",
        )

    def test_unsupported_language_returns_none(self):
        self.assertIsNone(self.mod.gestdown_language_name({"alpha3": "zzz"}))


class GestdownProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_uses_tvdb_show_then_episode_language_endpoint(self):
        provider = self.mod.GestdownProvider()
        calls = []

        def get_json(url, timeout=30):
            calls.append((url, timeout))
            if url == "https://api.gestdown.info/shows/external/tvdb/81189":
                return SHOW_LOOKUP
            if url == (
                "https://api.gestdown.info/subtitles/get/"
                "31ffb6ce-c000-4079-8912-b3f72057baed/1/1/English"
            ):
                return SUBTITLES_ENGLISH
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_json
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "series_tvdb_id": 81189,
                "season": 1,
                "episode": 1,
                "release_group": "0TV",
            },
            [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            {"locked_retry_delay_ms": 0},
        )

        self.assertEqual(calls[0][0], "https://api.gestdown.info/shows/external/tvdb/81189")
        self.assertEqual(calls[1][0], (
            "https://api.gestdown.info/subtitles/get/"
            "31ffb6ce-c000-4079-8912-b3f72057baed/1/1/English"
        ))
        self.assertGreaterEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "gestdown")
        self.assertEqual(first["id"], "gestdown-69cf7d79-052c-4f12-a57d-995d77de43ad-eng")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("series", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertIn("tvdb_id", first["matches"])
        self.assertIn("release_group", first["matches"])
        self.assertFalse(first["hash_verifiable"])
        self.assertTrue(first["hearing_impaired_verifiable"])
        self.assertEqual(first["display"]["downloads"], 418)
        self.assertEqual(
            first["provider_payload"]["download_url"],
            "https://api.gestdown.info/subtitles/download/69cf7d79-052c-4f12-a57d-995d77de43ad",
        )

    def test_search_skips_movies_and_missing_tvdb_id(self):
        provider = self.mod.GestdownProvider()

        def unexpected_get(url, timeout=30):
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = unexpected_get

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Breaking Bad"}, [{"alpha3": "eng"}], {}),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1},
                [{"alpha3": "eng"}],
                {},
            ),
            [],
        )

    def test_search_allows_season_zero_specials(self):
        provider = self.mod.GestdownProvider()
        calls = []

        def get_json(url, timeout=30):
            calls.append(url)
            if url == "https://api.gestdown.info/shows/external/tvdb/81189":
                return SHOW_LOOKUP
            if url == (
                "https://api.gestdown.info/subtitles/get/"
                "31ffb6ce-c000-4079-8912-b3f72057baed/0/1/English"
            ):
                return SUBTITLES_ENGLISH
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_json
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "series_tvdb_id": 81189,
                "season": 0,
                "episode": 1,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"locked_retry_delay_ms": 0},
        )

        self.assertIn(
            "https://api.gestdown.info/subtitles/get/"
            "31ffb6ce-c000-4079-8912-b3f72057baed/0/1/English",
            calls,
        )
        self.assertGreaterEqual(len(results), 1)

    def test_search_uses_variant_labels_and_preserves_country_and_script(self):
        provider = self.mod.GestdownProvider()
        calls = []

        def get_json(url, timeout=30):
            calls.append(url)
            if url == "https://api.gestdown.info/shows/external/tvdb/81189":
                return SHOW_LOOKUP
            if url.startswith(
                "https://api.gestdown.info/subtitles/get/"
                "31ffb6ce-c000-4079-8912-b3f72057baed/1/1/"
            ):
                return SUBTITLES_ENGLISH
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_json
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "series_tvdb_id": 81189,
                "season": 1,
                "episode": 1,
            },
            [
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
                {"alpha3": "srp", "alpha2": "sr", "script": "Cyrl"},
            ],
            {"locked_retry_delay_ms": 0},
        )

        base = (
            "https://api.gestdown.info/subtitles/get/"
            "31ffb6ce-c000-4079-8912-b3f72057baed/1/1/"
        )
        self.assertIn(base + "Portuguese%20%28Brazilian%29", calls)
        self.assertIn(base + "Serbian%20%28Cyrillic%29", calls)

        by_id = {result["id"]: result for result in results}
        pt_br = by_id[
            "gestdown-69cf7d79-052c-4f12-a57d-995d77de43ad-por-BR"
        ]
        self.assertEqual(pt_br["language"]["alpha3"], "por")
        self.assertEqual(pt_br["language"]["country_alpha2"], "BR")

        sr_cyrl = by_id[
            "gestdown-69cf7d79-052c-4f12-a57d-995d77de43ad-srp-Cyrl"
        ]
        self.assertEqual(sr_cyrl["language"]["alpha3"], "srp")
        self.assertEqual(sr_cyrl["language"]["script"], "Cyrl")
        self.assertIsNone(sr_cyrl["language"].get("country_alpha2"))

    def test_search_returns_no_results_after_repeated_423s(self):
        provider = self.mod.GestdownProvider()
        calls = []

        def get_json(url, timeout=30):
            calls.append(url)
            if url == "https://api.gestdown.info/shows/external/tvdb/81189":
                return SHOW_LOOKUP
            raise urllib.error.HTTPError(url, 423, "Locked", hdrs=None, fp=io.BytesIO())

        provider._http_get = get_json
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "series_tvdb_id": 81189,
                "season": 1,
                "episode": 1,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"locked_retry_delay_ms": 0},
        )

        self.assertEqual(results, [])
        self.assertEqual(calls.count(
            "https://api.gestdown.info/subtitles/get/"
            "31ffb6ce-c000-4079-8912-b3f72057baed/1/1/English"
        ), 3)


class GestdownProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_payload_url_and_returns_srt_bytes(self):
        provider = self.mod.GestdownProvider()
        calls = []

        def get_bytes(url, timeout=30):
            calls.append((url, timeout))
            self.assertEqual(
                url,
                "https://api.gestdown.info/subtitles/download/69cf7d79-052c-4f12-a57d-995d77de43ad",
            )
            return SUBTITLE_BODY

        provider._http_get = get_bytes
        result = provider.download(
            {
                "provider": "gestdown",
                "schema": 1,
                "subtitle_id": "69cf7d79-052c-4f12-a57d-995d77de43ad",
                "download_url": "https://api.gestdown.info/subtitles/download/69cf7d79-052c-4f12-a57d-995d77de43ad",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(calls[0][0], "https://api.gestdown.info/subtitles/download/69cf7d79-052c-4f12-a57d-995d77de43ad")
        self.assertEqual(data, SUBTITLE_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SUBTITLE_BODY).hexdigest())
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])

    def test_download_requires_download_url(self):
        provider = self.mod.GestdownProvider()

        with self.assertRaisesRegex(ValueError, "download_url"):
            provider.download({"subtitle_id": "missing-url"}, {"alpha3": "eng"}, {})


if __name__ == "__main__":
    unittest.main()
