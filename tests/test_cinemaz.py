import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "cinemaz"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("cinemaz_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release_page():
    return b"""
    <html>
      <body>
        <section id="content-area">
          <div class="block">
            <div class="table-responsive">
              <table>
                <tbody>
                  <tr><td>Title</td><td>The Cabinet of Dr Caligari 1920 1080p BluRay x264-GROUP</td></tr>
                  <tr>
                    <td>Subtitles</td>
                    <td>
                      <table>
                        <thead>
                          <tr><th>Language</th><th>Download</th><th>Uploader</th></tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>English</td>
                            <td><a href="https://cinemaz.to/subtitles/111/download">English ZIP</a></td>
                            <td>alice</td>
                          </tr>
                          <tr>
                            <td>German</td>
                            <td><a href="/subtitles/222/download">German SRT</a></td>
                            <td>bob</td>
                          </tr>
                          <tr>
                            <td>French</td>
                            <td><a href="/subtitles/333/download">French SRT</a></td>
                            <td>charlie</td>
                          </tr>
                        </tbody>
                      </table>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </body>
    </html>
    """


def _unit3d_release_page():
    return b"""
    <html>
      <body>
        <h1>The Cabinet of Dr Caligari 1920 1080p BluRay x264-GROUP</h1>
        <section id="subtitles">
          <table>
            <thead>
              <tr><th>Language</th><th>Extension</th><th>Download</th><th>Uploader</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>English</td>
                <td>ASS</td>
                <td><a href="/subtitles/444/download">Download</a></td>
                <td>dana</td>
              </tr>
            </tbody>
          </table>
        </section>
      </body>
    </html>
    """


def _unit3d_actions_release_page():
    return b"""
    <html>
      <body>
        <h1>The Cabinet of Dr Caligari 1920 1080p BluRay x264-GROUP</h1>
        <section id="subtitles">
          <table>
            <thead>
              <tr>
                <th>Language</th><th>Note</th><th>Extension</th><th>Size</th>
                <th>Downloads</th><th>Uploaded</th><th>Uploader</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>English</td>
                <td>full</td>
                <td>SRT</td>
                <td>12 KiB</td>
                <td>42</td>
                <td>2026-01-01</td>
                <td>dana</td>
                <td>
                  <a href="/subtitles/555">View</a>
                  <a href="/subtitles/555/download">Download</a>
                </td>
              </tr>
              <tr>
                <td>Brazilian Portuguese</td>
                <td>full</td>
                <td>SRT</td>
                <td>11 KiB</td>
                <td>7</td>
                <td>2026-01-02</td>
                <td>bruno</td>
                <td>
                  <a href="/subtitles/666">View</a>
                  <a href="/subtitles/666/download">Download</a>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      </body>
    </html>
    """


def _zip_body(name="caligari.english.srt"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n")
        archive.writestr("readme.txt", "ignored")
    return stream.getvalue()


def _rar_body():
    return (ROOT / "tests" / "fixtures" / "subcentral_blue_lights_ion10.rar").read_bytes()


class CinemaZManifestTests(unittest.TestCase):
    def test_manifest_does_not_bundle_an_archive_extractor(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        requirements = manifest["dependencies"]["requirements"]
        names = {item["name"].lower() for item in requirements}

        self.assertEqual(manifest["version"], "0.1.5")
        self.assertTrue(names.isdisjoint({"py7zz", "py7zr", "rarfile"}))


class CinemaZSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_cookies(self):
        provider = self.mod.CinemaZProvider()

        with self.assertRaisesRegex(ValueError, "cookies"):
            provider.search(
                {"kind": "movie", "info_url": "https://cinemaz.to/torrent/123-caligari"},
                [{"alpha3": "eng"}],
                {},
            )

    def test_search_returns_empty_when_video_did_not_come_from_cinemaz(self):
        provider = self.mod.CinemaZProvider()

        self.assertEqual(
            provider.search(
                {"kind": "movie", "info_url": "https://example.com/torrent/123"},
                [{"alpha3": "eng"}],
                {"cookies": "cinemazx_session=valid", "user_agent": "UnitTest/1.0"},
            ),
            [],
        )

    def test_search_validates_cookies_and_parses_release_subtitle_table(self):
        provider = self.mod.CinemaZProvider()
        calls = []

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del timeout
            calls.append((url, headers, cookies, allow_redirects))
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "The Cabinet of Dr Caligari",
                "year": 1920,
                "hash": "0123456789abcdef0123456789abcdef",
                "release_group": "GROUP",
                "info_url": "https://cinemaz.to/torrent/123-caligari",
            },
            [{"alpha3": "eng"}, {"alpha3": "deu"}],
            {"cookies": "cinemazx_session=valid; XSRF-TOKEN=token", "user_agent": "UnitTest/1.0"},
        )

        self.assertEqual(calls[0][0], "https://cinemaz.to/rules")
        self.assertEqual(calls[0][1]["Referer"], "https://cinemaz.to/")
        self.assertEqual(calls[0][1]["User-Agent"], "UnitTest/1.0")
        self.assertEqual(calls[0][2]["cinemazx_session"], "valid")
        self.assertFalse(calls[0][3])
        self.assertEqual([item["language"]["alpha3"] for item in results], ["eng", "deu"])
        self.assertEqual(results[0]["provider"], "cinemaz")
        self.assertEqual(results[0]["release_info"], "The Cabinet of Dr Caligari 1920 1080p BluRay x264-GROUP")
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://cinemaz.to/subtitles/111/download")
        self.assertEqual(results[1]["provider_payload"]["download_url"], "https://cinemaz.to/subtitles/222/download")
        self.assertEqual(results[0]["display"]["uploader"], "alice")
        self.assertEqual(results[0]["matches"], ["title", "year", "release_group"])
        self.assertEqual(results[0]["score"], 60)
        self.assertEqual(results[0]["score_without_hash"], results[0]["score"])
        self.assertFalse(results[0]["hash_verifiable"])
        self.assertNotIn("hash", results[0]["matches"])

    def test_search_parses_unit3d_h1_subtitles_layout_and_extension_column(self):
        provider = self.mod.CinemaZProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _unit3d_release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "The Cabinet of Dr Caligari",
                "year": 1920,
                "info_url": "https://cinemaz.to/torrent/123-caligari",
            },
            [{"alpha3": "eng"}],
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["release_info"], "The Cabinet of Dr Caligari 1920 1080p BluRay x264-GROUP")
        self.assertEqual(results[0]["provider_payload"]["filename"], "cinemaz-444.eng.ass")
        self.assertEqual(results[0]["display"]["uploader"], "dana")

    def test_search_rejects_expired_cookies_on_rules_redirect(self):
        provider = self.mod.CinemaZProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302, b"", {"location": "https://cinemaz.to/auth/login"}
        )

        with self.assertRaisesRegex(PermissionError, "cookies"):
            provider.search(
                {"kind": "movie", "info_url": "https://cinemaz.to/torrent/123-caligari"},
                [{"alpha3": "eng"}],
                {"cookies": "cinemazx_session=expired"},
            )

    def test_search_reads_download_link_from_unit3d_actions_column(self):
        provider = self.mod.CinemaZProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _unit3d_actions_release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "The Cabinet of Dr Caligari",
                "info_url": "https://cinemaz.to/torrent/123-caligari",
            },
            [{"alpha3": "eng"}],
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        # The download endpoint is preferred over the sibling view link.
        self.assertEqual(
            results[0]["provider_payload"]["download_url"],
            "https://cinemaz.to/subtitles/555/download",
        )
        self.assertEqual(results[0]["display"]["uploader"], "dana")

    def test_search_returns_brazilian_portuguese_as_por_variant(self):
        provider = self.mod.CinemaZProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _unit3d_actions_release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "The Cabinet of Dr Caligari",
                "info_url": "https://cinemaz.to/torrent/123-caligari",
            },
            [{"alpha3": "por", "country_alpha2": "BR"}],
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")
        self.assertEqual(
            results[0]["provider_payload"]["download_url"],
            "https://cinemaz.to/subtitles/666/download",
        )

    def test_search_returns_brazilian_row_for_generic_portuguese(self):
        provider = self.mod.CinemaZProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _unit3d_actions_release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "The Cabinet of Dr Caligari",
                "info_url": "https://cinemaz.to/torrent/123-caligari",
            },
            [{"alpha3": "por"}],
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")

    def test_search_skips_forced_only_language_requests(self):
        provider = self.mod.CinemaZProvider()
        calls = []

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            calls.append(url)
            return self.mod.HttpResponse(200, b"<html>rules</html>", {})

        provider._http_get = get_bytes
        results = provider.search(
            {"kind": "movie", "info_url": "https://cinemaz.to/torrent/123-caligari"},
            [{"alpha3": "eng", "forced": True}],
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(results, [])
        # A forced-only request must short-circuit before any network calls.
        self.assertEqual(calls, [])


class CinemaZDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_direct_subtitle_payload(self):
        provider = self.mod.CinemaZProvider()
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
        calls = []

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del timeout
            calls.append((url, headers, cookies, allow_redirects))
            return self.mod.HttpResponse(200, body, {"content-type": "application/x-subrip"})

        provider._http_get = get_bytes
        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/222/download",
                "filename": "caligari.de.srt",
                "release_info": "The Cabinet of Dr Caligari 1920",
            },
            {"alpha3": "deu"},
            {"cookies": "cinemazx_session=valid", "user_agent": "UnitTest/1.0"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)
        self.assertEqual(calls[0][0], "https://cinemaz.to/subtitles/222/download")
        self.assertFalse(calls[0][3])

    def test_download_uses_response_filename_for_direct_format(self):
        provider = self.mod.CinemaZProvider()
        body = b"[Script Info]\nTitle: Caligari\n"
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, body, {"Content-Disposition": 'attachment; filename="caligari.de.ass"'}
        )

        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/222/download",
                "filename": "download",
            },
            {"alpha3": "deu"},
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_type"], "text/x-ssa")

    def test_download_rejects_login_redirects_and_html(self):
        provider = self.mod.CinemaZProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302, b"", {"location": "https://cinemaz.to/login"}
        )

        with self.assertRaises(PermissionError):
            provider.download(
                {
                    "download_url": "https://cinemaz.to/subtitles/222/download",
                    "filename": "caligari.de.srt",
                },
                {"alpha3": "deu"},
                {"cookies": "cinemazx_session=expired"},
            )

        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, b"<html><form action='/login'>login</form></html>", {"content-type": "text/html"}
        )

        with self.assertRaises(PermissionError):
            provider.download(
                {
                    "download_url": "https://cinemaz.to/subtitles/222/download",
                    "filename": "caligari.de.srt",
                },
                {"alpha3": "deu"},
                {"cookies": "cinemazx_session=expired"},
            )

    def test_download_returns_zip_archive_for_host_extraction(self):
        provider = self.mod.CinemaZProvider()
        archive = _zip_body()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, archive, {"content-type": "application/zip"}
        )

        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/111/download",
                "filename": "caligari.en.zip",
                "release_info": "The Cabinet of Dr Caligari 1920",
                "season": 2,
                "episode": 5,
            },
            {"alpha3": "eng"},
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["season"], 2)
        self.assertEqual(result["episode"], 5)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_for_host_extraction(self):
        provider = self.mod.CinemaZProvider()
        archive = _rar_body()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, archive, {"content-type": "application/vnd.rar"}
        )

        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/111/download",
                "filename": "caligari.en.rar",
                "release_info": "The Cabinet of Dr Caligari 1920",
            },
            {"alpha3": "eng"},
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertIsNone(result["season"])
        self.assertIsNone(result["episode"])

    def test_archive_detection_uses_bytes_instead_of_filename(self):
        provider = self.mod.CinemaZProvider()
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nSubtitle\r\n"

        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, body, {"content-type": "application/octet-stream"}
        )

        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/111/download",
                "filename": "subtitle.rar",
            },
            {"alpha3": "eng"},
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body.replace(b"\r\n", b"\n"))
        self.assertNotIn("archive_b64", result)


class CinemaZRequestSecurityTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_rejects_unsafe_urls_before_cookie_parsing_or_io(self):
        provider = self.mod.CinemaZProvider()
        unsafe_urls = (
            "http://cinemaz.to/subtitles/123/download",
            "https://user:secret@cinemaz.to/subtitles/123/download",
            "https://@cinemaz.to/subtitles/123/download",
            "https://cinemaz.to:444/subtitles/123/download",
            "https://evil.invalid/subtitles/123/download",
        )

        for url in unsafe_urls:
            with self.subTest(url=url):
                with patch.object(self.mod, "_parse_cookies", side_effect=AssertionError("cookies parsed")) as parse_cookies:
                    with patch.object(self.mod.urllib.request, "build_opener") as build_opener:
                        with self.assertRaisesRegex(ValueError, "HTTPS"):
                            provider.download({"download_url": url}, {"alpha3": "eng"}, {})
                parse_cookies.assert_not_called()
                build_opener.assert_not_called()

    def test_parser_discards_unsafe_download_links(self):
        unsafe_links = (
            b"http://cinemaz.to/subtitles/222/download",
            b"https://user:secret@cinemaz.to/subtitles/222/download",
            b"https://cinemaz.to:444/subtitles/222/download",
            b"https://evil.invalid/subtitles/222/download",
        )

        for link in unsafe_links:
            with self.subTest(link=link):
                page = _release_page().replace(b"/subtitles/222/download", link)
                parsed = self.mod.parse_release_page(page, "https://cinemaz.to/torrent/123")
                urls = [subtitle["download_url"] for subtitle in parsed["subtitles"]]
                self.assertNotIn(link.decode("ascii"), urls)
                self.assertIn("https://cinemaz.to/subtitles/111/download", urls)

    def test_http_get_blocks_unsafe_redirects_before_forwarding_cookies(self):
        class FakeResponse:
            def __init__(self, status, body=b"", headers=None):
                self.status = status
                self._body = body
                self.headers = headers or {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return self._body

        class FakeOpener:
            def __init__(self, redirect_handler, location):
                self.redirect_handler = redirect_handler
                self.location = location
                self.requests = []

            def open(self, request, timeout):
                del timeout
                self.requests.append(request)
                if len(self.requests) == 1:
                    redirected = self.redirect_handler.redirect_request(
                        request, None, 302, "Found", {"Location": self.location}, self.location
                    )
                    if redirected is None:
                        return FakeResponse(302, headers={"Location": self.location})
                    return self.open(redirected, 30)
                return FakeResponse(200, b"subtitle")

        unsafe_locations = (
            "https://evil.invalid/subtitles/123/download",
            "http://cinemaz.to/subtitles/123/download",
            "https://cinemaz.to:444/subtitles/123/download",
            "https://user:secret@cinemaz.to/subtitles/123/download",
        )

        for location in unsafe_locations:
            with self.subTest(location=location):
                provider = self.mod.CinemaZProvider()
                openers = []

                def make_opener(*handlers):
                    handler = handlers[0] if handlers else self.mod.urllib.request.HTTPRedirectHandler()
                    opener = FakeOpener(handler, location)
                    openers.append(opener)
                    return opener

                with patch.object(self.mod.urllib.request, "build_opener", side_effect=make_opener):
                    response = provider._http_get(
                        "https://cinemaz.to/subtitles/start/download",
                        {"User-Agent": "UnitTest/1.0"},
                        {"cinemazx_session": "secret"},
                    )

                opener = openers[0]
                self.assertEqual(
                    len(opener.requests),
                    1,
                    [(request.full_url, request.get_header("Cookie")) for request in opener.requests],
                )
                self.assertEqual(response.status, 302)
                self.assertEqual(opener.requests[0].get_header("Cookie"), "cinemazx_session=secret")

    def test_http_get_preserves_same_origin_redirects_and_cookie(self):
        class FakeResponse:
            def __init__(self, status, body=b"", headers=None):
                self.status = status
                self._body = body
                self.headers = headers or {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return self._body

        class FakeOpener:
            def __init__(self, redirect_handler):
                self.redirect_handler = redirect_handler
                self.requests = []

            def open(self, request, timeout):
                del timeout
                self.requests.append(request)
                if len(self.requests) == 1:
                    redirected = self.redirect_handler.redirect_request(
                        request,
                        None,
                        302,
                        "Found",
                        {"Location": "https://cinemaz.to:443/subtitles/123/download"},
                        "https://cinemaz.to:443/subtitles/123/download",
                    )
                    return self.open(redirected, 30)
                return FakeResponse(200, b"subtitle")

        provider = self.mod.CinemaZProvider()
        openers = []

        def make_opener(*handlers):
            handler = handlers[0] if handlers else self.mod.urllib.request.HTTPRedirectHandler()
            opener = FakeOpener(handler)
            openers.append(opener)
            return opener

        with patch.object(self.mod.urllib.request, "build_opener", side_effect=make_opener):
            response = provider._http_get(
                "https://cinemaz.to/subtitles/start/download",
                {"User-Agent": "UnitTest/1.0"},
                {"cinemazx_session": "secret"},
            )

        opener = openers[0]
        self.assertEqual(response.status, 200)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(opener.requests[1].full_url, "https://cinemaz.to:443/subtitles/123/download")
        self.assertEqual(opener.requests[1].get_header("Cookie"), "cinemazx_session=secret")


class CinemaZScoreTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_score_is_monotonic_for_zero_through_six_matches(self):
        scores = [self.mod._score(["match"] * count) for count in range(7)]

        self.assertEqual(scores, [0, 20, 40, 60, 80, 95, 95])
        self.assertTrue(all(left <= right for left, right in zip(scores, scores[1:])))
        self.assertLess(scores[0], min(scores[1:]))


if __name__ == "__main__":
    unittest.main()
