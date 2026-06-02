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
PROVIDER_DIR = ROOT / "providers" / "subx"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subx_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_body(filename, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, body)
    return stream.getvalue()


class SubXHelpersTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_auth_headers_use_bearer_api_key(self):
        headers = self.mod.auth_headers("secret")
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_build_episode_queries_keeps_exact_season_and_title_fallbacks(self):
        queries = self.mod.build_episode_queries(
            {"series": "Breaking.Bad", "season": 3, "episode": 13}
        )
        self.assertEqual(
            queries,
            [
                ("Breaking Bad S03E13", 3, 13),
                ("Breaking Bad S03", 3, None),
                ("Breaking Bad", 3, None),
            ],
        )

    def test_build_episode_queries_includes_alternative_series_titles(self):
        queries = self.mod.build_episode_queries(
            {
                "series": "La casa de papel",
                "alternative_series": ["Money Heist"],
                "season": 1,
                "episode": 2,
            }
        )

        self.assertEqual(
            queries,
            [
                ("La casa de papel S01E02", 1, 2),
                ("La casa de papel S01", 1, None),
                ("La casa de papel", 1, None),
                ("Money Heist S01E02", 1, 2),
                ("Money Heist S01", 1, None),
                ("Money Heist", 1, None),
            ],
        )

    def test_language_variant_detects_spain_spanish_keywords(self):
        spain = self.mod.language_payload_from_description("Subtitulos en castellano de Espana")
        latin = self.mod.language_payload_from_description("Subtitulos latinoamericanos")

        self.assertEqual(spain["country"], "ES")
        self.assertEqual(latin["country"], "MX")


class SubXProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_api_key(self):
        provider = self.mod.SubXProvider()

        with self.assertRaisesRegex(ValueError, "api_key"):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "spa", "alpha2": "es"}],
                {},
            )

    def test_movie_search_prefers_imdb_id_and_returns_result(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del timeout
            calls.append((path, params, api_key))
            return {
                "items": [
                    {
                        "id": "sub-1",
                        "video_type": "movie",
                        "title": "Inception",
                        "imdb_id": "tt1375666",
                        "description": "Inception 2010 BluRay latino",
                        "uploader_name": "tester",
                        "downloads": 42,
                    }
                ],
                "total": 1,
            }

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(
            calls,
            [
                (
                    "/api/subtitles/search",
                    {
                        "limit": 200,
                        "video_type": "movie",
                        "imdb_id": "tt1375666",
                        "year": 2010,
                    },
                    "key-one",
                )
            ],
        )
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "subx")
        self.assertEqual(first["language"]["alpha3"], "spa")
        self.assertEqual(first["language"]["country"], "MX")
        self.assertIn("title", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], "sub-1")

    def test_episode_search_keeps_exact_episode_before_season_pack(self):
        provider = self.mod.SubXProvider()

        def stub(path, params, api_key, timeout=10):
            del path, api_key, timeout
            self.assertEqual(params["imdb_id"], "tt0903747")
            return {
                "items": [
                    {
                        "id": "wrong-episode",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": 12,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad S03E12 latino",
                    },
                    {
                        "id": "season-pack",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": None,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad temporada 3 castellano",
                    },
                    {
                        "id": "exact",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": 13,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad S03E13 castellano",
                    },
                ],
                "total": 3,
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "imdb_id": "tt0903747",
                "year": 2008,
            },
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["exact"])
        self.assertEqual(results[0]["language"]["country"], "ES")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_episode_search_uses_season_pack_when_no_exact_episode_exists(self):
        provider = self.mod.SubXProvider()

        def stub(path, params, api_key, timeout=10):
            del path, params, api_key, timeout
            return {
                "items": [
                    {
                        "id": "season-pack",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": None,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad temporada 3 castellano",
                    }
                ],
                "total": 1,
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "imdb_id": "tt0903747",
            },
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "season-pack")
        self.assertTrue(results[0]["provider_payload"]["season_pack"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_retries_rate_limit_once(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del path, params, timeout
            calls.append(api_key)
            if len(calls) == 1:
                raise self.mod.RateLimited("retry later", retry_after=0)
            return {"items": [], "total": 0}

        provider._http_get_json = stub
        provider.search(
            {"kind": "movie", "title": "Inception"},
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(calls, ["key-one", "key-one"])

    def test_search_returns_empty_after_repeated_server_errors(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del path, params, timeout
            calls.append(api_key)
            raise RuntimeError("SubX server error 500")

        provider._http_get_json = stub

        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "spa", "alpha2": "es"}],
                {"api_key": "key-one", "request_delay_ms": 0},
            ),
            [],
        )
        self.assertEqual(calls, ["key-one", "key-one", "key-one"])

    def test_search_raises_after_repeated_rate_limits(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del path, params, timeout
            calls.append(api_key)
            raise self.mod.RateLimited("retry later", retry_after=0)

        provider._http_get_json = stub

        with self.assertRaises(self.mod.RateLimited):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "spa", "alpha2": "es"}],
                {"api_key": "key-one", "request_delay_ms": 0},
            )
        self.assertEqual(calls, ["key-one", "key-one", "key-one"])


class SubXProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_accepts_direct_subtitle_body(self):
        provider = self.mod.SubXProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHola\n"
        provider._http_get_bytes = lambda path, api_key, timeout=30: body

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "sub-1",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/sub-1/download",
                "filename": "subx.sub-1.es.srt",
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_zip_archive_episode_file(self):
        provider = self.mod.SubXProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHola\n"
        provider._http_get_bytes = lambda path, api_key, timeout=30: _zip_body(
            "Breaking.Bad.S03E13.es.srt",
            body,
        )

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "exact",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/exact/download",
                "filename": "subx.exact.es.zip",
                "episode": 13,
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_rar_archive_via_bundled_py7zz(self):
        provider = self.mod.SubXProvider()
        provider._http_get_bytes = lambda path, api_key, timeout=30: b"Rar!\x1a\x07\x00rar body"
        with mock.patch.object(
            self.mod,
            "_extract_rar_files",
            return_value=[("Breaking.Bad.S03E13.es.srt", b"1\nsubtitle\n")],
        ) as extractor:
            result = provider.download(
                {
                    "provider": "subx",
                    "schema": 1,
                    "subtitle_id": "exact",
                    "download_url": "https://subx-api.duckdns.org/api/subtitles/exact/download",
                    "filename": "subx.exact.es.rar",
                    "episode": 13,
                },
                {"alpha3": "spa", "alpha2": "es"},
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
