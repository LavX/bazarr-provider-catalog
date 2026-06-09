import base64
import hashlib
import importlib.util
import io
import json
import socket
import unittest
import urllib.error
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
        # Season/episode are carried so the host can pick the archive member.
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)
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

    def test_download_zip_archive_returns_raw_archive_for_host(self):
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

        # Host-side extraction: the raw archive is forwarded with the member the
        # provider selected. No extraction, decoding, or encoding guess worker-side.
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive_body).hexdigest())
        self.assertEqual(result["member"], "Dune.Part.One.2021.pt-BR.srt")
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_rar_archive_returns_raw_archive_with_episode(self):
        provider = self.mod.LegendasNetProvider()
        archive_body = b"Rar!\x1a\x07\x00rar-bytes"

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})

        def get(url, headers=None, timeout=30):
            del headers, timeout
            return self.mod.HttpResponse(200, archive_body, {})

        provider._http_json = request
        provider._http_get = get
        result = provider.download(
            {
                "provider": "legendasnet",
                "schema": 1,
                "download_link": "/download/tv/201",
                "filename": "legendasnet.201.zip",
                "episode": 1,
                "season": 1,
            },
            {"alpha3": "por-BR"},
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive_body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("member", result)
        self.assertNotIn("encoding", result)

    def test_download_7z_archive_returns_raw_archive_with_episode(self):
        provider = self.mod.LegendasNetProvider()
        archive_body = b"7z\xbc\xaf\x27\x1c7z-bytes"

        def request(method, url, headers=None, json_body=None, timeout=30):
            del method, headers, json_body, timeout
            return self.mod.HttpResponse(200, _fixture("legendasnet_login.json"), {})

        def get(url, headers=None, timeout=30):
            del headers, timeout
            return self.mod.HttpResponse(200, archive_body, {})

        provider._http_json = request
        provider._http_get = get
        result = provider.download(
            {
                "provider": "legendasnet",
                "schema": 1,
                "download_link": "/download/movie/101",
                "filename": "legendasnet.101.zip",
                "episode": None,
            },
            {"alpha3": "por-BR"},
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive_body)
        self.assertIsNone(result["episode"])
        self.assertNotIn("member", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.LegendasNetProvider()
        provider._http_json = lambda method, url, headers=None, json_body=None, timeout=30: self.mod.HttpResponse(
            200, _fixture("legendasnet_login.json"), {}
        )
        provider._http_get = lambda url, headers=None, timeout=30: self.mod.HttpResponse(200, b"   ", {})

        with self.assertRaisesRegex(ValueError, "empty body"):
            provider.download(
                {"download_link": "/download/movie/101", "filename": "legendasnet.101.zip"},
                {"alpha3": "por-BR"},
                {"username": "user", "password": "pass"},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.LegendasNetProvider()
        provider._http_json = lambda method, url, headers=None, json_body=None, timeout=30: self.mod.HttpResponse(
            200, _fixture("legendasnet_login.json"), {}
        )
        provider._http_get = lambda url, headers=None, timeout=30: self.mod.HttpResponse(
            200, b"<!DOCTYPE html><html><body>error</body></html>", {}
        )

        with self.assertRaisesRegex(ValueError, "HTML/error page"):
            provider.download(
                {"download_link": "/download/movie/101", "filename": "legendasnet.101.zip"},
                {"alpha3": "por-BR"},
                {"username": "user", "password": "pass"},
            )

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
        self.assertNotIn("encoding", result)
        self.assertNotIn("archive_b64", result)
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


class _FakeUrlopenResponse:
    def __init__(self, status, body, headers):
        self.status = status
        self._body = body
        self.headers = _FakeHeaders(headers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class _FakeHeaders:
    def __init__(self, headers):
        self._headers = dict(headers or {})

    def items(self):
        return self._headers.items()


class LegendasNetTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.slept = []
        self._real_sleep = self.mod.time.sleep
        self.mod.time.sleep = lambda seconds: self.slept.append(seconds)
        self._real_urlopen = self.mod.urllib.request.urlopen

    def tearDown(self):
        self.mod.time.sleep = self._real_sleep
        self.mod.urllib.request.urlopen = self._real_urlopen

    def _install_urlopen(self, outcomes):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            del request, timeout
            outcome = outcomes[calls["count"]]
            calls["count"] += 1
            if isinstance(outcome, Exception):
                raise outcome
            status, body, headers = outcome
            return _FakeUrlopenResponse(status, body, headers)

        self.mod.urllib.request.urlopen = fake_urlopen
        return calls

    def test_http_get_retries_transient_urlerror_then_succeeds(self):
        provider = self.mod.LegendasNetProvider()
        calls = self._install_urlopen(
            [
                urllib.error.URLError("connection reset"),
                (200, b"subtitle-bytes", {"content-type": "text/plain"}),
            ]
        )

        response = provider._http_get("https://legendas.net/download/movie/101")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"subtitle-bytes")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.slept), 1)

    def test_http_get_retries_timeout_twice_then_succeeds(self):
        provider = self.mod.LegendasNetProvider()
        calls = self._install_urlopen(
            [
                socket.timeout("timed out"),
                socket.timeout("timed out"),
                (200, b"ok", {}),
            ]
        )

        response = provider._http_get("https://legendas.net/download/movie/101")

        self.assertEqual(response.status, 200)
        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.slept), 2)

    def test_http_get_retries_on_503_then_succeeds(self):
        provider = self.mod.LegendasNetProvider()

        def make_503():
            return urllib.error.HTTPError(
                "https://legendas.net/download/movie/101", 503, "Service Unavailable", {}, io.BytesIO(b"down")
            )

        calls = self._install_urlopen(
            [
                make_503(),
                (200, b"recovered", {}),
            ]
        )

        response = provider._http_get("https://legendas.net/download/movie/101")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"recovered")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.slept), 1)

    def test_http_get_does_not_retry_404(self):
        provider = self.mod.LegendasNetProvider()

        def make_404():
            return urllib.error.HTTPError(
                "https://legendas.net/download/movie/101", 404, "Not Found", {}, io.BytesIO(b"missing")
            )

        calls = self._install_urlopen([make_404()])

        response = provider._http_get("https://legendas.net/download/movie/101")

        # 4xx (other than 429) is converted to a status-bearing response on the
        # first attempt and never retried.
        self.assertEqual(response.status, 404)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.slept, [])

    def test_http_get_does_not_retry_after_final_transient_failure(self):
        provider = self.mod.LegendasNetProvider()
        calls = self._install_urlopen(
            [
                urllib.error.URLError("reset"),
                urllib.error.URLError("reset"),
                urllib.error.URLError("reset"),
            ]
        )

        with self.assertRaises(urllib.error.URLError):
            provider._http_get("https://legendas.net/download/movie/101")

        # Exactly HTTP_MAX_ATTEMPTS attempts, then the same transport error
        # propagates unchanged.
        self.assertEqual(calls["count"], self.mod.HTTP_MAX_ATTEMPTS)
        self.assertEqual(len(self.slept), self.mod.HTTP_MAX_ATTEMPTS - 1)

    def test_http_get_honors_retry_after_on_429(self):
        provider = self.mod.LegendasNetProvider()

        def make_429():
            return urllib.error.HTTPError(
                "https://legendas.net/download/movie/101",
                429,
                "Too Many Requests",
                {"Retry-After": "5"},
                io.BytesIO(b"slow down"),
            )

        self._install_urlopen(
            [
                make_429(),
                (200, b"ok", {}),
            ]
        )

        response = provider._http_get("https://legendas.net/download/movie/101")

        self.assertEqual(response.status, 200)
        # Retry-After (5s) wins over the small backoff base on the 429 retry.
        self.assertEqual(self.slept, [5.0])

    def test_http_json_retries_transient_urlerror_then_succeeds(self):
        provider = self.mod.LegendasNetProvider()
        calls = self._install_urlopen(
            [
                urllib.error.URLError("dns failure"),
                (200, b'{"access_token": "unit-token"}', {}),
            ]
        )

        response = provider._http_json(
            "POST", "https://legendas.net/api/v1/login", json_body={"email": "u", "password": "p"}
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'{"access_token": "unit-token"}')
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.slept), 1)


if __name__ == "__main__":
    unittest.main()
