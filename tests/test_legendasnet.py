import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "legendasnet"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "legendasnet_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(name):
    return (FIXTURE_DIR / name).read_bytes()


def _json_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text())


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class LegendasNetSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_username_and_password(self):
        provider = self.mod.LegendasNetProvider()

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(_json_fixture("legendasnet_video_dune_2021.json"), [{"alpha3": "por-BR"}], {})

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(
                _json_fixture("legendasnet_video_dune_2021.json"),
                [{"alpha3": "por", "country": "BR"}],
                {"username": "user"},
            )

    def test_movie_search_logs_in_and_returns_forced_and_normal_results(self):
        provider = self.mod.LegendasNetProvider()
        calls = []

        def request(method, url, headers=None, json_body=None, timeout=30):
            del timeout
            calls.append((method, url, dict(headers or {}), dict(json_body or {})))
            if url.endswith("/login"):
                self.assertEqual(method, "POST")
                self.assertEqual(json_body, {"email": "user", "password": "pass"})
                return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})
            if url.endswith("/search/movie"):
                self.assertEqual(method, "GET")
                self.assertEqual(headers["Authorization"], "Bearer unit-token")
                self.assertEqual(
                    json_body,
                    {
                        "name": "Dune: Part One",
                        "page": 1,
                        "per_page": 25,
                        "imdb_id": "tt1160419",
                    },
                )
                return self.mod.HttpResponse(200, _fixture("legendasnet_search_dune.json"), {})
            raise AssertionError(url)

        provider._http_json = request
        results = provider.search(
            _json_fixture("legendasnet_video_dune_2021.json"),
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["provider"], "legendasnet")
        self.assertEqual(results[0]["language"], {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR", "hi": False, "forced": False})
        self.assertEqual(results[0]["provider_payload"]["file_id"], 101)
        self.assertEqual(results[0]["page_link"], "https://legendas.net/legenda?movie_id=438631&legenda_id=101")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertTrue(results[1]["language"]["forced"])
        self.assertTrue(results[1]["provider_payload"]["forced"])

    def test_movie_search_accepts_provider_hub_country_alpha2_language_payload(self):
        provider = self.mod.LegendasNetProvider()

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            if url.endswith("/login"):
                return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})
            if url.endswith("/search/movie"):
                return self.mod.HttpResponse(200, _fixture("legendasnet_search_dune.json"), {})
            raise AssertionError(url)

        provider._http_json = request
        results = provider.search(
            _json_fixture("legendasnet_video_dune_2021.json"),
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(len(results), 2)

    def test_movie_search_falls_back_to_alternative_title_when_primary_is_empty(self):
        provider = self.mod.LegendasNetProvider()
        searched_names = []

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, timeout
            if url.endswith("/login"):
                return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})
            if url.endswith("/search/movie"):
                searched_names.append(json_body["name"])
                if json_body["name"] == "Dune: Part One":
                    return self.mod.HttpResponse(200, b'{"success": true, "movies": []}', {})
                if json_body["name"] == "Dune":
                    return self.mod.HttpResponse(200, _fixture("legendasnet_search_dune.json"), {})
            raise AssertionError(url)

        provider._http_json = request
        results = provider.search(
            _json_fixture("legendasnet_video_dune_2021.json"),
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(searched_names, ["Dune: Part One", "Dune"])
        self.assertEqual(len(results), 2)

    def test_episode_search_filters_requested_episode(self):
        provider = self.mod.LegendasNetProvider()

        def request(method, url, headers=None, json_body=None, timeout=30):
            del headers, timeout
            if url.endswith("/login"):
                return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})
            if url.endswith("/search/tv"):
                self.assertEqual(method, "GET")
                self.assertEqual(
                    json_body,
                    {
                        "name": "Chernobyl",
                        "page": 1,
                        "per_page": 25,
                        "tv_episode": 1,
                        "tv_season": 1,
                        "imdb_id": "tt7366338",
                    },
                )
                return self.mod.HttpResponse(200, _fixture("legendasnet_search_chernobyl.json"), {})
            raise AssertionError(url)

        provider._http_json = request
        results = provider.search(
            _json_fixture("legendasnet_video_chernobyl_s01e01.json"),
            [{"alpha3": "por-BR"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual([item["provider_payload"]["file_id"] for item in results], [201])
        self.assertEqual(results[0]["page_link"], "https://legendas.net/tv_legenda?movie_id=87108&legenda_id=201")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_unsuccessful_api_payload_returns_no_results(self):
        provider = self.mod.LegendasNetProvider()
        provider._http_json = lambda method, url, headers=None, json_body=None, timeout=30: (
            self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})
            if url.endswith("/login")
            else self.mod.HttpResponse(200, b'{"success": false, "error": "not found"}', {})
        )

        results = provider.search(
            _json_fixture("legendasnet_video_dune_2021.json"),
            [{"alpha3": "por-BR"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(results, [])


class LegendasNetDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_extracts_first_file_from_zip(self):
        provider = self.mod.LegendasNetProvider()
        archive_body = _zip_body(
            {
                "Dune.Part.One.2021.pt-BR.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nMovie line\r\n",
                "README.txt": b"not a subtitle",
            }
        )

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})

        def get(url, headers=None, timeout=30):
            del timeout
            self.assertEqual(headers["Authorization"], "Bearer unit-token")
            self.assertEqual(url, "https://legendas.net/download/movie/101")
            return self.mod.HttpResponse(200, archive_body, {"content-type": "application/zip"})

        provider._http_json = request
        provider._http_get = get
        result = provider.download(
            {
                "provider": "legendasnet",
                "schema": 1,
                "download_link": "/download/movie/101",
                "filename": "legendasnet.101.zip",
            },
            {"alpha3": "por-BR"},
            {"username": "user", "password": "pass"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nMovie line\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_direct_ass_download_keeps_real_format_despite_zip_filename(self):
        provider = self.mod.LegendasNetProvider()
        subtitle_body = (
            b"[Script Info]\r\nScriptType: v4.00+\r\n\r\n[Events]\r\n"
            b"Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Movie line\r\n"
        )

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})

        def get(url, headers=None, timeout=30):
            del headers, timeout
            self.assertEqual(url, "https://legendas.net/legendas/dune.por-br.ass")
            return self.mod.HttpResponse(200, subtitle_body, {"content-type": "text/plain"})

        provider._http_json = request
        provider._http_get = get
        result = provider.download(
            {
                "provider": "legendasnet",
                "schema": 1,
                "download_link": "/legendas/dune.por-br.ass",
                "filename": "legendasnet.101.pt-br.zip",
            },
            {"alpha3": "por-BR"},
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_type"], "text/x-ssa")
        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, self.mod._normalize_line_endings(subtitle_body))

    def test_direct_download_uses_content_disposition_filename_for_format(self):
        provider = self.mod.LegendasNetProvider()
        subtitle_body = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nMovie line\n"

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})

        def get(url, headers=None, timeout=30):
            del headers, timeout
            return self.mod.HttpResponse(
                200,
                subtitle_body,
                {"Content-Disposition": 'attachment; filename="dune.por-br.vtt"'},
            )

        provider._http_json = request
        provider._http_get = get
        result = provider.download(
            {
                "provider": "legendasnet",
                "schema": 1,
                "download_link": "/download/movie/101",
                "filename": "legendasnet.101.pt-br.zip",
            },
            {"alpha3": "por-BR"},
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(result["format"], "vtt")
        self.assertEqual(result["content_type"], "text/vtt")

    def test_direct_download_without_extension_sniffs_content(self):
        provider = self.mod.LegendasNetProvider()
        subtitle_body = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nMovie line\n"

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})

        def get(url, headers=None, timeout=30):
            del headers, timeout
            return self.mod.HttpResponse(200, subtitle_body, {})

        provider._http_json = request
        provider._http_get = get
        result = provider.download(
            {
                "provider": "legendasnet",
                "schema": 1,
                "download_link": "/download/movie/101",
                "filename": "legendasnet.101.pt-br.zip",
            },
            {"alpha3": "por-BR"},
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(result["format"], "vtt")

    def test_download_reports_daily_limit(self):
        provider = self.mod.LegendasNetProvider()
        provider._http_json = lambda method, url, headers=None, json_body=None, timeout=30: self.mod.HttpResponse(
            200,
            _fixture("legendasnet_login.json"),
            {},
        )
        provider._http_get = lambda url, headers=None, timeout=30: self.mod.HttpResponse(429, b"limit", {})

        with self.assertRaisesRegex(RuntimeError, "Daily download limit"):
            provider.download(
                {"download_link": "/download/movie/101", "filename": "legendasnet.101.zip"},
                {"alpha3": "por-BR"},
                {"username": "user", "password": "pass"},
            )


if __name__ == "__main__":
    unittest.main()
