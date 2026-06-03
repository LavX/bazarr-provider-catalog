import base64
import hashlib
import importlib.util
import io
import json
import unittest
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "pipocas"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "pipocas_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(name):
    return (FIXTURE_DIR / name).read_bytes()


def _video(name):
    return json.loads((FIXTURE_DIR / name).read_text())


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class PipocasProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_username_and_password(self):
        provider = self.mod.PipocasProvider()

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(_video("pipocas_video_dune_2021.json"), [{"alpha3": "por"}], {})

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(
                _video("pipocas_video_dune_2021.json"),
                [{"alpha3": "por"}],
                {"username": "user"},
            )

    def test_movie_search_logs_in_maps_brazilian_language_and_parses_details(self):
        provider = self.mod.PipocasProvider()
        calls = []

        def get(url, headers=None, timeout=10, params=None):
            calls.append(("GET", url, dict(headers or {}), dict(params or {})))
            if url.endswith("/login"):
                return self.mod.HttpResponse(200, _fixture("pipocas_login.html"), {})
            if url.endswith("/legendas"):
                self.assertEqual(
                    params,
                    {"t": "rel", "l": "brasileiro", "page": 1, "s": "Dune: Part One"},
                )
                return self.mod.HttpResponse(200, _fixture("pipocas_search_dune.html"), {})
            if url.endswith("/legendas/info/501"):
                return self.mod.HttpResponse(200, _fixture("pipocas_detail_dune.html"), {})
            if url.endswith("/legendas/info/502"):
                return self.mod.HttpResponse(200, _fixture("pipocas_detail_dune.html").replace(b"/501", b"/502"), {})
            raise AssertionError(url)

        def post(url, data, headers=None, timeout=10):
            calls.append(("POST", url, dict(data), dict(headers or {})))
            self.assertEqual(data, {"username": "user", "password": "pass", "_token": "csrf-token-value"})
            return self.mod.HttpResponse(200, b"<html>profile</html>", {"set-cookie": "session=ok"})

        provider._http_get = get
        provider._http_post = post
        results = provider.search(
            _video("pipocas_video_dune_2021.json"),
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"username": "user", "password": "pass", "request_delay_ms": 0},
        )

        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[1][0], "POST")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["provider"], "pipocas")
        self.assertEqual(results[0]["language"], {"alpha3": "por-BR", "alpha2": "pt", "country": "BR", "hi": False, "forced": False})
        self.assertEqual(results[0]["provider_payload"]["sub_id"], "501")
        self.assertEqual(results[0]["display"]["uploader"], "movie_uploader")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_language_mapping_honors_country_alpha2_for_brazilian_portuguese(self):
        language = self.mod._language_for_request(
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}
        )

        self.assertEqual(language["site"], "brasileiro")
        self.assertEqual(language["country"], "BR")

    def test_search_applies_delay_before_each_detail_fetch(self):
        provider = self.mod.PipocasProvider()
        provider._ensure_authenticated = lambda config: None
        sleeps = []
        self.mod.time.sleep = sleeps.append

        def get(url, headers=None, timeout=10, params=None):
            del headers, timeout, params
            if url.endswith("/legendas"):
                return self.mod.HttpResponse(200, _fixture("pipocas_search_dune.html"), {})
            if url.endswith("/legendas/info/501"):
                return self.mod.HttpResponse(200, _fixture("pipocas_detail_dune.html"), {})
            if url.endswith("/legendas/info/502"):
                return self.mod.HttpResponse(200, _fixture("pipocas_detail_dune.html").replace(b"/501", b"/502"), {})
            raise AssertionError(url)

        provider._http_get = get
        provider.search(
            _video("pipocas_video_dune_2021.json"),
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"username": "user", "password": "pass", "request_delay_ms": 123},
        )

        self.assertEqual(sleeps, [0.123, 0.123, 0.123])

    def test_store_cookies_preserves_duplicate_set_cookie_headers(self):
        provider = self.mod.PipocasProvider()

        provider._store_cookies(
            [
                ("Set-Cookie", "session=abc; Path=/"),
                ("Set-Cookie", "remember=def; Path=/"),
            ]
        )

        self.assertEqual(provider._cookies["session"], "abc")
        self.assertEqual(provider._cookies["remember"], "def")

    def test_episode_search_uses_episode_query_and_english_language(self):
        provider = self.mod.PipocasProvider()

        def get(url, headers=None, timeout=10, params=None):
            del headers, timeout
            if url.endswith("/login"):
                return self.mod.HttpResponse(200, _fixture("pipocas_login.html"), {})
            if url.endswith("/legendas"):
                self.assertEqual(
                    params,
                    {"t": "rel", "l": "ingles", "page": 1, "s": "Chernobyl S01E01"},
                )
                return self.mod.HttpResponse(200, _fixture("pipocas_search_chernobyl.html"), {})
            if url.endswith("/legendas/info/601"):
                return self.mod.HttpResponse(200, _fixture("pipocas_detail_chernobyl.html"), {})
            raise AssertionError(url)

        provider._http_get = get
        provider._http_post = lambda url, data, headers=None, timeout=10: self.mod.HttpResponse(200, b"profile", {})
        results = provider.search(
            _video("pipocas_video_chernobyl_s01e01.json"),
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["sub_id"] for item in results], ["601"])
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_login_failure_is_reported(self):
        provider = self.mod.PipocasProvider()
        provider._http_get = lambda url, headers=None, timeout=10, params=None: self.mod.HttpResponse(
            200,
            _fixture("pipocas_login.html"),
            {},
        )
        provider._http_post = lambda url, data, headers=None, timeout=10: self.mod.HttpResponse(
            200,
            b"Cria uma conta",
            {},
        )

        with self.assertRaisesRegex(PermissionError, "login failed"):
            provider.search(
                _video("pipocas_video_dune_2021.json"),
                [{"alpha3": "por"}],
                {"username": "bad", "password": "bad"},
            )

    def test_download_extracts_matching_episode_file_from_zip(self):
        provider = self.mod.PipocasProvider()
        archive_body = _zip_body(
            {
                "Chernobyl.S01E02.en.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nEpisode two\r\n",
                "Chernobyl.S01E01.en.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nEpisode one\r\n",
            }
        )
        provider._authenticated = True
        provider._http_get = lambda url, headers=None, timeout=10, params=None: self.mod.HttpResponse(200, archive_body, {})
        result = provider.download(
            {
                "provider": "pipocas",
                "schema": 1,
                "download_url": "https://pipocas.tv/legendas/download/601",
                "filename": "pipocas.601.zip",
                "season": 1,
                "episode": 1,
                "release_info": "Chernobyl.S01E01.1080p.WEB.H264-MEMENTO",
            },
            {"alpha3": "eng"},
            {"username": "user", "password": "pass"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_accepts_direct_subtitle_content(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        provider._http_get = lambda url, headers=None, timeout=10, params=None: self.mod.HttpResponse(
            200,
            b"1\r\n00:00:01,000 --> 00:00:02,000\r\nDirect line\r\n",
            {},
        )
        result = provider.download(
            {
                "download_url": "https://pipocas.tv/legendas/download/501",
                "filename": "pipocas.501.srt",
            },
            {"alpha3": "por"},
            {"username": "user", "password": "pass"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Direct line", decoded)


if __name__ == "__main__":
    unittest.main()
