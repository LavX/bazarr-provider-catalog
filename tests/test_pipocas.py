import base64
import hashlib
import importlib.util
import io
import json
import unittest
from unittest import mock
import urllib.parse
import urllib.request
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
        self.assertEqual(
            results[0]["language"],
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR", "hi": False, "forced": False},
        )
        self.assertEqual(results[0]["id"], "pipocas-501-por-BR")
        self.assertEqual(
            results[0]["provider_payload"]["filename"],
            "pipocas.dune-part-one-2021-1080p-web-dl-ddp5-1-h-264-ntb.pt",
        )
        self.assertFalse(results[0]["filename"].endswith(".zip"))
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
        self.assertEqual(language["alpha3"], "por")
        self.assertEqual(language["country_alpha2"], "BR")

    def test_search_links_accept_class_and_attribute_order(self):
        body = (
            b'<a href="/legendas/info/501" class="no-decoration text-dark">One</a>'
            b'<a class="text-dark other no-decoration" title="two" href="/legendas/info/502">Two</a>'
        )
        self.assertEqual(
            self.mod.parse_search_results(body),
            ["https://pipocas.tv/legendas/info/501", "https://pipocas.tv/legendas/info/502"],
        )

    def test_login_accepts_csrf_meta_attributes_in_any_order(self):
        provider = self.mod.PipocasProvider()
        provider._http_get = lambda url, headers=None, timeout=10, params=None: self.mod.HttpResponse(
            200, b'<meta content="fresh-token" data-page="login" name="csrf-token">', {}
        )
        posted = []

        def post(url, data, headers=None, timeout=10):
            posted.append(data)
            return self.mod.HttpResponse(200, b"<html>profile</html>", {})

        provider._http_post = post
        provider._ensure_authenticated({"username": "user", "password": "pass"})
        self.assertEqual(posted[0]["_token"], "fresh-token")

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
            b"<html><body>Cria uma conta</body></html>",
            {},
        )

        with self.assertRaisesRegex(PermissionError, "login failed"):
            provider.search(
                _video("pipocas_video_dune_2021.json"),
                [{"alpha3": "por"}],
                {"username": "bad", "password": "bad"},
            )

    def test_download_hands_zip_to_host_with_episode_context(self):
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

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive_body).hexdigest())
        self.assertEqual((result["season"], result["episode"]), (1, 1))
        self.assertNotIn("content_b64", result)

    def test_rar_hands_raw_archive_to_host(self):
        body = b"Rar!\x1a\x07\x01\x00" + b"archive fixture"
        result = self.mod.extract_download(body, {"season": 2, "episode": 3})
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual((result["season"], result["episode"]), (2, 3))

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

    def test_download_infers_direct_format_from_response_filename(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        provider._http_get = lambda url, headers=None, timeout=10, params=None: self.mod.HttpResponse(
            200,
            b"1\r\n00:00:01,000 --> 00:00:02,000\r\nDirect line\r\n",
            {"Content-Disposition": 'attachment; filename="Dune.Part.One.srt"'},
        )
        # provider_payload no longer carries a ".zip" extension after search,
        # so the direct branch must rely on the response Content-Disposition.
        result = provider.download(
            {
                "download_url": "https://pipocas.tv/legendas/download/501",
                "filename": "pipocas.dune-part-one.pt",
            },
            {"alpha3": "por"},
            {"username": "user", "password": "pass"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Direct line", decoded)
        self.assertEqual(result["format"], "srt")

    def test_redirect_handler_forwards_new_cookie_only_to_same_origin(self):
        provider = self.mod.PipocasProvider()
        handler = self.mod._CookieCapturingRedirectHandler(provider._store_cookies, provider._cookie_header)

        request = urllib.request.Request("https://pipocas.tv/login")
        redirect_headers = _FakeHeaders([("Set-Cookie", "session=secret; Path=/; HttpOnly")])
        new_request = handler.redirect_request(
            request,
            io.BytesIO(b""),
            302,
            "Found",
            redirect_headers,
            "https://pipocas.tv/perfil",
        )

        # The session cookie set on the redirect response must be stored even
        # though urllib transparently follows the redirect afterwards.
        self.assertEqual(provider._cookies.get("session"), "secret")
        self.assertIsNotNone(new_request)
        self.assertEqual(new_request.get_full_url(), "https://pipocas.tv/perfil")
        self.assertEqual(new_request.get_header("Cookie"), "session=secret")

        with self.assertRaises((ValueError, PermissionError)):
            handler.redirect_request(
                new_request, io.BytesIO(b""), 302, "Found",
                _FakeHeaders([]), "https://example.org/perfil",
            )


    def test_account_pages_allow_comments_and_head_prefix(self):
        for page in (
            b"<!-- comment --><html><body>Cria uma conta</body></html>",
            b"<!-- first --> \n <!-- second --><head></head><body>Cria uma conta</body>",
            b"<head></head><body>Cria uma conta</body>",
        ):
            with self.subTest(page=page):
                self.assertTrue(self.mod._requires_account(page))

    def test_subtitle_dialogue_does_not_trigger_account_recovery(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nCria uma conta\n"
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as packed:
            packed.writestr("Movie.srt", body)
        for content, key in ((body, "content_b64"), (archive.getvalue(), "archive_b64")):
            with self.subTest(payload=key):
                provider = self.mod.PipocasProvider()
                provider._authenticated = True
                provider._http_get = mock.Mock(return_value=self.mod.HttpResponse(200, content))
                provider._http_post = mock.Mock(side_effect=AssertionError("Subtitle text triggered login"))
                result = provider.download({"download_url": self.mod.DOWNLOAD_URL.format(id=501), "filename": "Movie.srt"}, {"alpha3": "por"}, {"username": "user", "password": "pass"})
                self.assertEqual(base64.b64decode(result[key]), content)
                self.assertEqual(provider._http_get.call_count, 1)
                provider._http_post.assert_not_called()

    def test_expired_search_session_reauthenticates_once(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        provider._cookies = {"session": "stale"}
        searches, logins = [], []

        def get(url, headers=None, timeout=15, params=None):
            if url == self.mod.LOGIN_URL:
                self.assertFalse(provider._authenticated)
                self.assertNotIn("stale", provider._cookies.values())
                return self.mod.HttpResponse(200, _fixture("pipocas_login.html"))
            searches.append(url)
            return self.mod.HttpResponse(200, b"<html><body>Cria uma conta</body></html>" if len(searches) == 1 else b"<html>no results</html>")

        def post(url, data, **kwargs):
            logins.append(url)
            return self.mod.HttpResponse(200, b"<html>profile</html>")

        provider._http_get, provider._http_post = get, post
        result = provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "por"}], {"username": "user", "password": "pass"})
        self.assertEqual(result, [])
        self.assertEqual(len(searches), 2)
        self.assertEqual(len(logins), 1)

    def test_expired_download_session_reauthenticates_once(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        downloads, logins = [], []
        subtitle = b"1\n00:00:01,000 --> 00:00:02,000\nText\n"

        def get(url, headers=None, timeout=15, params=None):
            if url == self.mod.LOGIN_URL:
                return self.mod.HttpResponse(200, _fixture("pipocas_login.html"))
            downloads.append(url)
            return self.mod.HttpResponse(200, b"<html><body>Cria uma conta</body></html>" if len(downloads) == 1 else subtitle)

        def post(url, data, **kwargs):
            logins.append(url)
            return self.mod.HttpResponse(200, b"<html>profile</html>")

        provider._http_get, provider._http_post = get, post
        result = provider.download({"download_url": self.mod.DOWNLOAD_URL.format(id=501), "filename": "pipocas.dune.pt"}, {"alpha3": "por"}, {"username": "user", "password": "pass"})
        self.assertEqual(base64.b64decode(result["content_b64"]), subtitle)
        self.assertEqual((len(downloads), len(logins)), (2, 1))

    def test_expired_detail_session_reauthenticates_once(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        provider._cookies = {"session": "stale"}
        search_page = b'<a class="text-dark no-decoration" href="/legendas/info/501">Dune</a>'
        detail_url = "https://pipocas.tv/legendas/info/501"
        details, logins = [], []

        def get(url, headers=None, timeout=15, params=None):
            if url == self.mod.LOGIN_URL:
                self.assertFalse(provider._authenticated)
                self.assertNotIn("stale", provider._cookies.values())
                return self.mod.HttpResponse(200, _fixture("pipocas_login.html"))
            if url == self.mod.SEARCH_URL:
                return self.mod.HttpResponse(200, search_page)
            self.assertEqual(url, detail_url)
            details.append(url)
            return self.mod.HttpResponse(
                200,
                b"<html><body>Cria uma conta</body></html>" if len(details) == 1 else _fixture("pipocas_detail_dune.html"),
            )

        def post(url, data, **kwargs):
            logins.append(url)
            return self.mod.HttpResponse(200, b"<html>profile</html>")

        provider._http_get, provider._http_post = get, post
        results = provider.search(
            {"kind": "movie", "title": "Dune"},
            [{"alpha3": "por"}],
            {"username": "user", "password": "pass"},
        )
        self.assertEqual([item["provider_payload"]["sub_id"] for item in results], ["501"])
        self.assertEqual((len(details), len(logins)), (2, 1))

    def test_repeated_account_page_stops_after_one_login_retry(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        searches = []

        def get(url, headers=None, timeout=15, params=None):
            if url == self.mod.LOGIN_URL:
                return self.mod.HttpResponse(200, _fixture("pipocas_login.html"))
            searches.append(url)
            return self.mod.HttpResponse(200, b"<html><body>Cria uma conta</body></html>")

        provider._http_get = get
        provider._http_post = mock.Mock(return_value=self.mod.HttpResponse(200, b"<html>profile</html>"))
        with self.assertRaises(PermissionError):
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "por"}], {"username": "user", "password": "pass"})
        self.assertEqual(len(searches), 2)
        self.assertEqual(provider._http_post.call_count, 1)

    def test_rate_limit_does_not_trigger_login_retry(self):
        provider = self.mod.PipocasProvider()
        provider._authenticated = True
        provider._http_get = mock.Mock(return_value=self.mod.HttpResponse(429, b"<html><body>Cria uma conta</body></html>"))
        provider._http_post = mock.Mock()
        with self.assertRaisesRegex(urllib.error.HTTPError, "429"):
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "por"}], {"username": "user", "password": "pass"})
        self.assertEqual(provider._http_get.call_count, 1)
        provider._http_post.assert_not_called()

    def test_detail_links_are_restricted_to_pipocas_origin(self):
        urls = ["http://127.0.0.1:8080/legendas/info/1", "https://foreign.test/legendas/info/2", "https://pipocas.tv@foreign.test/legendas/info/3", "https://pipocas.tv:444/legendas/info/4", "/legendas/info/5", "https://pipocas.tv:443/legendas/info/6"]
        page = "".join(f'<a class="text-dark no-decoration" href="{url}">x</a>' for url in urls).encode()
        self.assertEqual(self.mod.parse_search_results(page), ["https://pipocas.tv/legendas/info/5", "https://pipocas.tv:443/legendas/info/6"])

    def test_detail_page_rejects_off_origin_download_link(self):
        body = _fixture("pipocas_detail_dune.html").replace(
            b'"/legendas/download/501"',
            b'"http://127.0.0.1:8080/legendas/download/501"',
        )
        with self.assertRaises(ValueError):
            self.mod.parse_detail_page(body, "https://pipocas.tv/legendas/info/501")

    def test_http_methods_reject_off_origin_before_network_io(self):
        provider = self.mod.PipocasProvider()
        provider._opener = mock.Mock()
        for url in ("http://127.0.0.1:8080/legendas/info/1", "https://pipocas.tv:444/legendas/info/1", "https://user@pipocas.tv/legendas/info/1"):
            with self.subTest(url=url):
                with self.assertRaises((ValueError, PermissionError)):
                    provider._http_get(url)
                with self.assertRaises((ValueError, PermissionError)):
                    provider._http_post(url, {"password": "test-only"})
        provider._opener.open.assert_not_called()

    def test_direct_subtitle_format_without_disposition_filename(self):
        cases = [
            (b"1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n", {}, "srt"),
            (b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nText\n", {"Content-Type": "text/vtt; charset=utf-8"}, "vtt"),
            (b"[Script Info]\nScriptType: v4.00+\n[Events]\n", {"Content-Type": "application/octet-stream"}, "ass"),
            (b"{25}{50}Text", {}, "sub"),
        ]
        for body, headers, fmt in cases:
            with self.subTest(fmt=fmt):
                provider = self.mod.PipocasProvider()
                provider._authenticated = True
                provider._http_get = mock.Mock(return_value=self.mod.HttpResponse(200, body, headers))
                result = provider.download({"download_url": self.mod.DOWNLOAD_URL.format(id=501), "filename": "pipocas.dune.pt"}, {"alpha3": "por"}, {"username": "user", "password": "pass"})
                self.assertEqual(result["format"], fmt)
                self.assertEqual(base64.b64decode(result["content_b64"]), body)
                self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
                self.assertNotIn("encoding", result)

    def test_direct_format_fallback_rejects_html_and_unknown_text(self):
        for body in (b"<html><body>Error</body></html>", b"Unknown response"):
            provider = self.mod.PipocasProvider()
            provider._authenticated = True
            provider._http_get = mock.Mock(return_value=self.mod.HttpResponse(200, body, {"Content-Type": "text/plain"}))
            with self.assertRaises(ValueError):
                provider.download({"download_url": self.mod.DOWNLOAD_URL.format(id=501), "filename": "pipocas.dune.pt"}, {"alpha3": "por"}, {"username": "user", "password": "pass"})

    def test_multiword_release_group_requires_every_token(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 1, "release_group": "Horrible Subs"}
        self.assertIn("release_group", self.mod.derive_matches(video, "Show.S01E01.Horrible-Subs"))
        self.assertNotIn("release_group", self.mod.derive_matches(video, "Show.S01E01.Horrible"))


class _FakeHeaders:
    """Minimal multi-valued header container mirroring http.client.HTTPMessage."""

    def __init__(self, items):
        self._items = list(items)

    def get_all(self, name, failobj=None):
        wanted = name.lower()
        values = [value for key, value in self._items if key.lower() == wanted]
        return values or failobj

    def items(self):
        return list(self._items)

    def get(self, name, failobj=None):
        wanted = name.lower()
        for key, value in self._items:
            if key.lower() == wanted:
                return value
        return failobj

    def __iter__(self):
        return iter(key for key, _value in self._items)


if __name__ == "__main__":
    unittest.main()
