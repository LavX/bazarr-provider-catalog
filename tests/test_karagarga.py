import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "karagarga"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("karagarga_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _search_page():
    return b"""
    <html>
      <body>
        <table cellspacing="5">
          <tr>
            <td>1</td>
            <td>Dune (2021)</td>
            <td></td>
            <td></td>
            <td></td>
            <td>English</td>
            <td></td>
            <td></td>
            <td></td>
            <td class="approved"><a href="https://forum.karagarga.in/topic/123-dune/">approved forum</a></td>
            <td></td>
          </tr>
          <tr>
            <td>2</td>
            <td>Dune (2021)</td>
            <td></td>
            <td></td>
            <td></td>
            <td>French</td>
            <td></td>
            <td></td>
            <td></td>
            <td class="approved"><a href="https://forum.karagarga.in/topic/ignored/">approved forum</a></td>
            <td></td>
          </tr>
          <tr>
            <td>3</td>
            <td>Dune (1984)</td>
            <td></td>
            <td></td>
            <td></td>
            <td>English</td>
            <td></td>
            <td></td>
            <td></td>
            <td class="approved"><a href="https://forum.karagarga.in/topic/wrong-year/">approved forum</a></td>
            <td></td>
          </tr>
        </table>
      </body>
    </html>
    """


def _forum_page():
    return b"""
    <html>
      <body>
        <div class="post entry-content">
          <p>
            <span class="desc lighter">4 downloads</span>
            <a href="https://forum.karagarga.in/download/file.php?id=low"><strong>Dune.2021.HDTV-GROUP</strong></a>
          </p>
          <li class="attachment">
            <span class="desc lighter">27 downloads</span>
            <a href="https://forum.karagarga.in/download/file.php?id=best"><strong>Dune.2021.BluRay-GROUP</strong></a>
          </li>
          <div>
            <span class="desc lighter">bad count</span>
            <a href="https://forum.karagarga.in/download/file.php?id=bad"><strong>Broken</strong></a>
          </div>
        </div>
      </body>
    </html>
    """


class KaragargaSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_main_credentials(self):
        provider = self.mod.KaragargaProvider()

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search({"kind": "movie", "title": "Dune", "year": 2021}, [{"alpha3": "eng"}], {})

    def test_movie_search_logs_into_tracker_and_forum_then_returns_most_downloaded_subtitle(self):
        provider = self.mod.KaragargaProvider()
        calls = []

        def post_response(url, data, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, timeout, allow_redirects
            calls.append(("POST", url, data, cookies, params))
            if url == "https://karagarga.in/takelogin.php":
                self.assertEqual(cookies, {})
                self.assertEqual(data, {"username": "main-user", "password": "main-pass"})
                return self.mod.HttpResponse(200, b"ok", {"set-cookie": "pass=tracker-pass; path=/"})
            if url == "https://forum.karagarga.in/index.php":
                self.assertEqual(cookies, {"csrf": "forum-csrf"})
                self.assertEqual(data["ips_username"], "forum-user")
                self.assertEqual(data["ips_password"], "forum-pass")
                self.assertEqual(data["auth_key"], "session-key")
                self.assertEqual(params["do"], "process")
                return self.mod.HttpResponse(
                    200,
                    b"ok",
                    {"set-cookie": "session_id=forum-session; path=/, pass_hash=forum-pass-hash; path=/"},
                )
            raise AssertionError(url)

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, timeout, allow_redirects
            calls.append(("GET", url, cookies, params))
            if url == "https://forum.karagarga.in/index.php" and (params or {}).get("section") == "login":
                self.assertNotIn("do", params)
                return self.mod.HttpResponse(
                    200,
                    b"<html><form><input type='hidden' name='auth_key' value='session-key'></form></html>",
                    {"set-cookie": "csrf=forum-csrf; path=/"},
                )
            if url == "https://karagarga.in/pots.php":
                self.assertEqual(params, {"search": "Dune", "status": "completed"})
                self.assertEqual(cookies, {"pass": "tracker-pass"})
                return self.mod.HttpResponse(200, _search_page(), {})
            if url == "https://forum.karagarga.in/topic/123-dune/":
                self.assertEqual(cookies, {"csrf": "forum-csrf", "session_id": "forum-session", "pass_hash": "forum-pass-hash"})
                return self.mod.HttpResponse(200, _forum_page(), {})
            raise AssertionError(url)

        provider._http_post = post_response
        provider._http_get = get_response
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "release_group": "GROUP"},
            [{"alpha3": "eng"}],
            {
                "username": "main-user",
                "password": "main-pass",
                "f_username": "forum-user",
                "f_password": "forum-pass",
            },
        )

        self.assertEqual(calls[0][1], "https://karagarga.in/takelogin.php")
        self.assertEqual(calls[1][1], "https://forum.karagarga.in/index.php")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "karagarga")
        self.assertEqual(results[0]["language"], {"alpha3": "eng", "hi": False, "forced": False})
        self.assertEqual(results[0]["release_info"], "Dune.2021.BluRay-GROUP")
        self.assertEqual(results[0]["display"]["downloads"], 27)
        self.assertEqual(
            results[0]["provider_payload"]["page_url"],
            "https://forum.karagarga.in/download/file.php?id=best",
        )
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_login_accepts_redirect_responses_that_set_auth_cookies(self):
        provider = self.mod.KaragargaProvider()
        calls = []

        def post_response(url, data, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del data, headers, cookies, timeout, params
            calls.append((url, allow_redirects))
            if url == "https://karagarga.in/takelogin.php":
                return self.mod.HttpResponse(302, b"", [("Set-Cookie", "pass=tracker-pass; path=/")])
            if url == "https://forum.karagarga.in/index.php":
                return self.mod.HttpResponse(
                    302,
                    b"",
                    [
                        ("Set-Cookie", "session_id=forum-session; path=/"),
                        ("Set-Cookie", "pass_hash=forum-pass-hash; path=/"),
                    ],
                )
            raise AssertionError(url)

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, cookies, timeout, params, allow_redirects
            self.assertEqual(url, "https://forum.karagarga.in/index.php")
            return self.mod.HttpResponse(
                200,
                b"<form><input name='auth_key' value='session-key'></form>",
                {},
            )

        provider._http_post = post_response
        provider._http_get = get_response
        provider._ensure_authenticated({"username": "main-user", "password": "main-pass"})

        self.assertEqual(provider._tracker_cookies, {"pass": "tracker-pass"})
        self.assertEqual(provider._forum_cookies, {"session_id": "forum-session", "pass_hash": "forum-pass-hash"})
        self.assertEqual(calls, [("https://karagarga.in/takelogin.php", False), ("https://forum.karagarga.in/index.php", False)])

    def test_search_returns_empty_for_episode_or_non_english_request(self):
        provider = self.mod.KaragargaProvider()

        self.assertEqual(
            provider.search({"kind": "episode", "series": "Dune"}, [{"alpha3": "eng"}], {"username": "u", "password": "p"}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "fra"}], {"username": "u", "password": "p"}),
            [],
        )


class KaragargaDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_attachment_and_returns_normalized_content(self):
        provider = self.mod.KaragargaProvider()
        calls = []

        provider._http_post = lambda url, data, headers, cookies, timeout=30, params=None, allow_redirects=True: (
            self.mod.HttpResponse(200, b"ok", {"set-cookie": "pass=tracker-pass; path=/"})
            if url == "https://karagarga.in/takelogin.php"
            else self.mod.HttpResponse(200, b"ok", {"set-cookie": "session_id=forum-session; path=/, pass_hash=hash; path=/"})
        )

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, timeout
            if url == "https://forum.karagarga.in/index.php" and (params or {}).get("section") == "login":
                return self.mod.HttpResponse(
                    200,
                    b"<form><input name='auth_key' value='session-key'></form>",
                    {},
                )
            calls.append((url, cookies, allow_redirects))
            return self.mod.HttpResponse(
                200,
                b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n",
                {"content-type": "application/x-subrip"},
            )

        provider._http_get = get_response
        result = provider.download(
            {
                "page_url": "https://forum.karagarga.in/download/file.php?id=best",
                "release_info": "Dune.2021.BluRay-GROUP",
                "filename": "Dune.2021.BluRay-GROUP.srt",
            },
            {"alpha3": "eng"},
            {"username": "main-user", "password": "main-pass"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)
        self.assertEqual(calls[0][0], "https://forum.karagarga.in/download/file.php?id=best")
        self.assertEqual(calls[0][1], {"session_id": "forum-session", "pass_hash": "hash"})
        self.assertFalse(calls[0][2])

    def test_download_rejects_login_html_response(self):
        provider = self.mod.KaragargaProvider()
        provider._authenticated = True
        provider._tracker_cookies = {"pass": "tracker-pass"}
        provider._forum_cookies = {"session_id": "forum-session", "pass_hash": "hash"}

        provider._http_get = lambda url, headers, cookies, timeout=30, params=None, allow_redirects=True: (
            self.mod.HttpResponse(
                200,
                b"<html><form action='index.php?app=core&module=global&section=login'>login</form></html>",
                {"content-type": "text/html"},
            )
        )

        with self.assertRaises(PermissionError):
            provider.download(
                {
                    "page_url": "https://forum.karagarga.in/download/file.php?id=best",
                    "filename": "Dune.2021.BluRay-GROUP.srt",
                },
                {"alpha3": "eng"},
                {"username": "main-user", "password": "main-pass"},
            )


class KaragargaCookieTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_store_response_cookies_preserves_duplicate_set_cookie_headers(self):
        target = {}
        response = self.mod.HttpResponse(
            200,
            b"",
            [
                ("Set-Cookie", "session_id=forum-session; path=/"),
                ("Set-Cookie", "pass_hash=forum-pass-hash; path=/"),
            ],
        )

        self.mod._store_response_cookies(target, response)

        self.assertEqual(target, {"session_id": "forum-session", "pass_hash": "forum-pass-hash"})


class KaragargaForumAuthKeyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_forum_login_uses_scraped_auth_key_from_login_page(self):
        provider = self.mod.KaragargaProvider()
        order = []

        def post_response(url, data, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, timeout, params, allow_redirects
            order.append(("POST", url))
            if url == "https://karagarga.in/takelogin.php":
                return self.mod.HttpResponse(200, b"ok", {"set-cookie": "pass=tracker-pass; path=/"})
            if url == "https://forum.karagarga.in/index.php":
                # Without scraping the per-session key this would still be the hardcoded default.
                self.assertEqual(data["auth_key"], "fresh-session-key")
                return self.mod.HttpResponse(
                    200,
                    b"ok",
                    {"set-cookie": "session_id=s; path=/, pass_hash=h; path=/"},
                )
            raise AssertionError(url)

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            order.append(("GET", url))
            self.assertEqual(url, "https://forum.karagarga.in/index.php")
            self.assertEqual(params.get("section"), "login")
            self.assertNotIn("do", params)
            return self.mod.HttpResponse(
                200,
                b"<form><input type='hidden' name='auth_key' value='fresh-session-key'></form>",
                {},
            )

        provider._http_post = post_response
        provider._http_get = get_response
        provider._ensure_authenticated({"username": "u", "password": "p"})

        # The login page must be fetched before the forum credentials are posted.
        self.assertEqual(
            order,
            [
                ("POST", "https://karagarga.in/takelogin.php"),
                ("GET", "https://forum.karagarga.in/index.php"),
                ("POST", "https://forum.karagarga.in/index.php"),
            ],
        )

    def test_parse_auth_key_reads_hidden_input(self):
        body = b"<form><input type='hidden' name='auth_key' value='abc123'></form>"
        self.assertEqual(self.mod._parse_auth_key(body), "abc123")
        self.assertEqual(self.mod._parse_auth_key(b"<form></form>"), "")


class KaragargaForumParsingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_relative_attachment_urls_are_resolved_against_forum(self):
        body = b"""
        <div class="post entry-content">
          <p>
            <span class="desc lighter">5 downloads</span>
            <a href="index.php?app=core&module=attach&attach_id=42"><strong>Dune.2021.BluRay-GROUP.srt</strong></a>
          </p>
        </div>
        """

        subtitles = self.mod.parse_forum_page(body)

        self.assertEqual(len(subtitles), 1)
        self.assertEqual(
            subtitles[0]["page_url"],
            "https://forum.karagarga.in/index.php?app=core&module=attach&attach_id=42",
        )

    def test_non_srt_attachment_format_is_preserved(self):
        body = b"""
        <div class="post entry-content">
          <p>
            <span class="desc lighter">8 downloads</span>
            <a href="index.php?app=core&module=attach&attach_id=7"><strong>Dune.2021.BluRay-GROUP.ass</strong></a>
          </p>
        </div>
        """

        subtitles = self.mod.parse_forum_page(body)
        candidate = self.mod._candidate(subtitles[0], {"title": "Dune", "year": 2021})

        self.assertEqual(subtitles[0]["attachment_format"], "ass")
        self.assertTrue(candidate["filename"].endswith(".ass"))
        self.assertEqual(self.mod._format_from_filename(candidate["filename"]), "ass")


class KaragargaProtectedGetTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_raises_when_protected_get_returns_login_page(self):
        provider = self.mod.KaragargaProvider()
        provider._authenticated = True
        provider._tracker_cookies = {"pass": "p"}
        provider._forum_cookies = {"session_id": "s", "pass_hash": "h"}

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, cookies, timeout, params
            self.assertFalse(allow_redirects)
            return self.mod.HttpResponse(
                200,
                b"<html><form action='index.php?app=core&module=global&section=login'>sign in</form></html>",
                {"content-type": "text/html"},
            )

        provider._http_get = get_response

        with self.assertRaises(PermissionError):
            provider.search(
                {"kind": "movie", "title": "Dune", "year": 2021},
                [{"alpha3": "eng"}],
                {"username": "u", "password": "p"},
            )


class KaragargaLanguageFilterTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_hi_or_forced_only_english_request_is_rejected(self):
        provider = self.mod.KaragargaProvider()

        def fail(*args, **kwargs):
            raise AssertionError("should not authenticate or fetch for unsupported variants")

        provider._http_post = fail
        provider._http_get = fail
        config = {"username": "u", "password": "p"}

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng", "hi": True}], config),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng", "forced": True}], config),
            [],
        )

    def test_wants_plain_english_helper(self):
        self.assertTrue(self.mod._wants_plain_english([{"alpha3": "eng"}]))
        self.assertTrue(self.mod._wants_plain_english([{"alpha3": "eng", "hi": False, "forced": False}]))
        self.assertTrue(
            self.mod._wants_plain_english([{"alpha3": "eng", "hi": True}, {"alpha3": "eng"}])
        )
        self.assertFalse(self.mod._wants_plain_english([{"alpha3": "eng", "hi": True}]))
        self.assertFalse(self.mod._wants_plain_english([{"alpha3": "eng", "forced": True}]))
        self.assertFalse(self.mod._wants_plain_english([{"alpha3": "fra"}]))


class KaragargaOriginTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_transport_rejects_forged_initial_origins_before_io(self):
        forged = (
            "https://forum.karagarga.in.evil.test/file",
            "https://forum.karagarga.in@evil.test/file",
            "https://evil.test@forum.karagarga.in/file",
            "http://forum.karagarga.in/file",
            "https://forum.karagarga.in:444/file",
            "https://karagarga.in.evil.test/file",
        )
        with patch.object(self.mod.urllib.request, "build_opener") as build_opener:
            for url in forged:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.mod._http_request("GET", url, {}, {"pass": "secret"})
            build_opener.assert_not_called()

    def test_transport_accepts_explicit_default_port_and_checks_redirects(self):
        origin = self.mod._allowed_origin("https://forum.karagarga.in:443/file")
        self.assertEqual(origin, "forum.karagarga.in")
        handler = self.mod._ScopedRedirectHandler(origin)
        request = self.mod.urllib.request.Request(
            "https://forum.karagarga.in/file", headers={"Cookie": "session_id=secret"}
        )
        for redirected in (
            "https://evil.test/file",
            "https://karagarga.in/file",
            "http://forum.karagarga.in/file",
            "https://forum.karagarga.in:444/file",
            "https://user@forum.karagarga.in/file",
        ):
            with self.subTest(url=redirected), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, redirected)
        followed = handler.redirect_request(
            request, None, 302, "Found", {}, "https://forum.karagarga.in/next"
        )
        self.assertEqual(followed.full_url, "https://forum.karagarga.in/next")

    def test_download_rejects_foreign_payload_before_authentication(self):
        provider = self.mod.KaragargaProvider()
        provider._ensure_authenticated = lambda config: self.fail("unexpected login")
        with self.assertRaises(ValueError):
            provider.download({"page_url": "https://evil.test/file"}, None, {})

    def test_parsed_foreign_links_never_become_requests(self):
        search = _search_page().replace(
            b"https://forum.karagarga.in/topic/123-dune/",
            b"https://forum.karagarga.in.evil.test/topic/123-dune/",
        )
        self.assertEqual(self.mod.parse_search_page(search, 2021), [])
        forum = _forum_page().replace(
            b"https://forum.karagarga.in/download/file.php?id=best",
            b"https://evil.test/download/file.php?id=best",
        )
        self.assertTrue(all("evil.test" not in item["page_url"] for item in self.mod.parse_forum_page(forum)))


class KaragargaAttachmentSafetyTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _download(self, body, filename="Dune.srt"):
        provider = self.mod.KaragargaProvider()
        provider._authenticated = True
        provider._tracker_cookies = {"pass": "tracker-pass"}
        provider._forum_cookies = {"session_id": "forum-session", "pass_hash": "hash"}

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            self.assertEqual(cookies, provider._forum_cookies)
            return self.mod.HttpResponse(200, body, {})

        provider._http_get = get_response
        return provider.download(
            {"page_url": "https://forum.karagarga.in/download/file.php?id=1", "filename": filename},
            None, {},
        )

    def test_zip_and_rar_signatures_use_raw_host_archive_contract(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Dune.srt", "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        for body in (stream.getvalue(), b"Rar!\x1a\x07\x00archive-bytes", b"Rar!\x1a\x07\x01\x00archive-bytes"):
            with self.subTest(body=body[:8]):
                result = self._download(body, "Dune.srt")
                self.assertEqual(base64.b64decode(result["archive_b64"]), body)
                self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
                self.assertNotIn("content_b64", result)
                self.assertNotIn("format", result)

    def test_unknown_attachment_is_rejected_instead_of_false_srt(self):
        for body, filename in ((b"opaque binary\x00data", "Dune.srt"), (b"7z\xbc\xaf\x27\x1cdata", "Dune.srt"), (b"ordinary web page", "blob.dat")):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self._download(body, filename)

    def test_known_direct_subtitle_formats_keep_bytes_and_format(self):
        for extension, body in (
            ("ass", b"[Script Info]\r\nTitle: Sample\r\n"),
            ("ssa", b"[Script Info]\r\nTitle: Sample\r\n"),
            ("vtt", b"WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\nHi\r\n"),
            ("sub", b"{1}{24}Hi\r\n"),
        ):
            with self.subTest(extension=extension):
                result = self._download(body, "Dune." + extension)
                self.assertEqual(result["format"], extension)
                self.assertEqual(base64.b64decode(result["content_b64"]), body.replace(b"\r\n", b"\n"))

    def test_utf16_bom_direct_subtitle_preserves_raw_bytes_for_host_decoding(self):
        body = "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n".encode("utf-16")
        result = self._download(body, "attachment")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())

    def test_recognized_direct_subtitle_can_supply_missing_extension(self):
        result = self._download(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n", "attachment")
        self.assertEqual(result["format"], "vtt")
        self.assertEqual(base64.b64decode(result["content_b64"]), b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n")


if __name__ == "__main__":
    unittest.main()
