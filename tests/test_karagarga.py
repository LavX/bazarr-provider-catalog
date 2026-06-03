import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

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
                self.assertEqual(data, {"username": "main-user", "password": "main-pass"})
                return self.mod.HttpResponse(200, b"ok", {"set-cookie": "pass=tracker-pass; path=/"})
            if url == "https://forum.karagarga.in/index.php":
                self.assertEqual(data["ips_username"], "forum-user")
                self.assertEqual(data["ips_password"], "forum-pass")
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
            if url == "https://karagarga.in/pots.php":
                self.assertEqual(params, {"search": "Dune", "status": "completed"})
                self.assertEqual(cookies["pass"], "tracker-pass")
                return self.mod.HttpResponse(200, _search_page(), {})
            if url == "https://forum.karagarga.in/topic/123-dune/":
                self.assertEqual(cookies["session_id"], "forum-session")
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

        provider._http_post = post_response
        cookies = provider._ensure_authenticated({"username": "main-user", "password": "main-pass"})

        self.assertEqual(cookies["pass"], "tracker-pass")
        self.assertEqual(cookies["session_id"], "forum-session")
        self.assertEqual(cookies["pass_hash"], "forum-pass-hash")
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
            del headers, timeout, params
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
        self.assertEqual(calls[0][0], "https://forum.karagarga.in/download/file.php?id=best")
        self.assertFalse(calls[0][2])

    def test_download_rejects_login_html_response(self):
        provider = self.mod.KaragargaProvider()
        provider._authenticated = True
        provider._cookies = {"pass": "tracker-pass", "session_id": "forum-session", "pass_hash": "hash"}

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


if __name__ == "__main__":
    unittest.main()
