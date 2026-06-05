import base64
import hashlib
import importlib.util
import io
import os
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsro"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subsro_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_body(filename, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, body)
    return stream.getvalue()


class SubsRoConfigTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_api_keys_trims_comma_separated_keys(self):
        self.assertEqual(
            self.mod.parse_api_keys(" first, second ,, third "),
            ["first", "second", "third"],
        )

    def test_language_code_maps_legacy_supported_languages(self):
        self.assertEqual(self.mod.api_language_code({"alpha3": "ron"}), "ro")
        self.assertEqual(self.mod.api_language_code({"alpha3": "eng"}), "en")
        self.assertIsNone(self.mod.api_language_code({"alpha3": "deu"}))

    def test_auth_headers_use_subs_ro_api_key_header(self):
        self.assertEqual(
            self.mod.auth_headers("secret-key")["X-Subs-Api-Key"],
            "secret-key",
        )


class SubsRoParsingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_season_from_romanian_title_or_release(self):
        self.assertEqual(self.mod.parse_season("Pluribus - Sezonul 2", ""), 2)
        self.assertEqual(self.mod.parse_season("", "Game.of.Thrones.S03E05.1080p"), 3)

    def test_imdb_values_keep_current_id_and_legacy_numeric_fallback(self):
        self.assertEqual(
            self.mod.imdb_search_values("tt1375666"),
            ["tt1375666", "1375666"],
        )


class SubsRoProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_api_key(self):
        provider = self.mod.SubsRoProvider()

        with self.assertRaisesRegex(ValueError, "api_key"):
            provider.search(
                {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
                [{"alpha3": "eng", "alpha2": "en"}],
                {},
            )

    def test_movie_search_uses_current_imdb_api_and_scores_identity_matches(self):
        provider = self.mod.SubsRoProvider()
        calls = []

        def stub(url, params, api_key, timeout=15):
            del timeout
            calls.append((url, params, api_key))
            return {
                "status": 200,
                "items": [
                    {
                        "id": 101,
                        "title": "Inception",
                        "year": 2010,
                        "description": "Inception.2010.1080p.BluRay.x264",
                        "downloadLink": "https://subs.ro/api/v1.0/subtitle/101/download",
                        "imdbid": "tt1375666",
                        "language": "en",
                        "type": "movie",
                    }
                ],
            }

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(
            calls,
            [
                (
                    "https://api.subs.ro/v1.0/search/imdbid/tt1375666",
                    {"language": "en"},
                    "key-one",
                )
            ],
        )
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "subsro")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("title", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], 101)
        self.assertEqual(
            first["provider_payload"]["download_url"],
            "https://subs.ro/api/v1.0/subtitle/101/download",
        )

    def test_episode_search_adds_episode_when_imdb_and_season_match(self):
        provider = self.mod.SubsRoProvider()

        def stub(url, params, api_key, timeout=15):
            del url, params, api_key, timeout
            return {
                "status": 200,
                "items": [
                    {
                        "id": 202,
                        "title": "Game of Thrones - Sezonul 1",
                        "year": 2011,
                        "description": "Game.of.Thrones.S01E01.1080p.WEB-DL",
                        "downloadLink": "https://subs.ro/api/v1.0/subtitle/202/download",
                        "imdbid": "tt0944947",
                        "language": "ro",
                        "type": "series",
                    }
                ],
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["language"]["alpha3"], "ron")
        self.assertIn("series", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertEqual(first["provider_payload"]["season"], 1)
        self.assertEqual(first["provider_payload"]["episode"], 1)

    def test_episode_match_not_awarded_for_wrong_episode_in_same_season(self):
        provider = self.mod.SubsRoProvider()

        def stub(url, params, api_key, timeout=15):
            del url, params, api_key, timeout
            return {
                "status": 200,
                "items": [
                    {
                        "id": 404,
                        "title": "Game of Thrones - Sezonul 1",
                        "year": 2011,
                        "description": "Game.of.Thrones.S01E03.1080p.WEB-DL",
                        "downloadLink": "https://subs.ro/api/v1.0/subtitle/404/download",
                        "imdbid": "tt0944947",
                        "language": "ro",
                        "type": "series",
                    }
                ],
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 5,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertNotIn("episode", first["matches"])

    def test_episode_match_falls_back_for_season_pack_without_episode_number(self):
        provider = self.mod.SubsRoProvider()

        def stub(url, params, api_key, timeout=15):
            del url, params, api_key, timeout
            return {
                "status": 200,
                "items": [
                    {
                        "id": 405,
                        "title": "Game of Thrones - Sezonul 1",
                        "year": 2011,
                        "description": "Game.of.Thrones.Sezonul.1.Complete.1080p.WEB-DL",
                        "downloadLink": "https://subs.ro/api/v1.0/subtitle/405/download",
                        "imdbid": "tt0944947",
                        "language": "ro",
                        "type": "series",
                    }
                ],
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 5,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "ron", "alpha2": "ro"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])

    def test_search_falls_back_to_legacy_numeric_imdb_id_when_current_id_has_no_items(self):
        provider = self.mod.SubsRoProvider()
        calls = []

        def stub(url, params, api_key, timeout=15):
            del params, api_key, timeout
            calls.append(url)
            if url.endswith("/tt1375666"):
                return {"status": 200, "items": []}
            return {
                "status": 200,
                "items": [
                    {
                        "id": 303,
                        "title": "Inception",
                        "year": 2010,
                        "description": "legacy numeric imdb result",
                        "downloadLink": "https://subs.ro/api/v1.0/subtitle/303/download",
                        "imdbid": "tt1375666",
                        "language": "en",
                        "type": "movie",
                    }
                ],
            }

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(
            calls,
            [
                "https://api.subs.ro/v1.0/search/imdbid/tt1375666",
                "https://api.subs.ro/v1.0/search/imdbid/1375666",
            ],
        )
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], 303)

    def test_key_rotation_retries_rate_limited_api_keys(self):
        provider = self.mod.SubsRoProvider()
        keys = []

        def stub(url, params, api_key, timeout=15):
            del url, params, timeout
            keys.append(api_key)
            if api_key == "key-one":
                return {"status": 429, "message": "rate limited"}
            return {"status": 200, "items": []}

        provider._http_get_json = stub
        data = provider._request_json(
            "https://api.subs.ro/v1.0/search/imdbid/tt1",
            {"language": "en"},
            {"api_key": "key-one,key-two"},
        )

        self.assertEqual(data["status"], 200)
        self.assertEqual(keys, ["key-one", "key-two"])

    def test_key_rotation_raises_when_all_api_keys_are_rate_limited(self):
        provider = self.mod.SubsRoProvider()
        provider._http_get_json = lambda url, params, api_key, timeout=15: {
            "status": 429,
            "message": "rate limited",
        }

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            provider._request_json(
                "https://api.subs.ro/v1.0/search/imdbid/tt1",
                {"language": "en"},
                {"api_key": "key-one,key-two"},
            )


class SubsRoProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_accepts_direct_subtitle_body(self):
        provider = self.mod.SubsRoProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nSubtitle\n"
        provider._http_get_bytes = lambda url, api_key, timeout=15: body

        result = provider.download(
            {
                "provider": "subsro",
                "schema": 1,
                "subtitle_id": 101,
                "download_url": "https://subs.ro/api/v1.0/subtitle/101/download",
                "filename": "subsro.101.en.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_zip_archive(self):
        provider = self.mod.SubsRoProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nSubtitle\n"
        provider._http_get_bytes = lambda url, api_key, timeout=15: _zip_body(
            "Inception.2010.en.srt",
            body,
        )

        result = provider.download(
            {
                "provider": "subsro",
                "schema": 1,
                "subtitle_id": 101,
                "download_url": "https://subs.ro/api/v1.0/subtitle/101/download",
                "filename": "subsro.101.en.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_rar_archive_via_bundled_py7zz(self):
        provider = self.mod.SubsRoProvider()
        provider._http_get_bytes = lambda url, api_key, timeout=15: b"Rar!\x1a\x07\x00rar body"
        with mock.patch.object(
            self.mod,
            "_extract_rar_files",
            return_value=[("Game.of.Thrones.S01E01.ro.srt", b"1\nsubtitle\n")],
        ) as extractor:
            result = provider.download(
                {
                    "provider": "subsro",
                    "schema": 1,
                    "subtitle_id": 202,
                    "download_url": "https://subs.ro/api/v1.0/subtitle/202/download",
                    "filename": "subsro.202.ro.rar",
                    "episode": 1,
                },
                {"alpha3": "ron", "alpha2": "ro"},
                {"api_key": "key-one"},
            )

        extractor.assert_called_once()
        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\nsubtitle\n")
        self.assertEqual(result["format"], "srt")

    def test_rar_extraction_prefers_bundled_py7zz(self):
        class FakePy7zz:
            @staticmethod
            def extract_archive(_archive_path, output_dir):
                with open(os.path.join(output_dir, "episode.srt"), "wb") as handle:
                    handle.write(b"subtitle")

        with mock.patch.object(self.mod, "py7zz", FakePy7zz):
            self.assertEqual(
                self.mod._extract_rar_files(b"rar bytes"),
                [("episode.srt", b"subtitle")],
            )


if __name__ == "__main__":
    unittest.main()
