import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "addic7ed"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("addic7ed_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shows_page():
    return b"""
    <html>
      <body>
        <table>
          <tr><td class="version"><h3><a href="/show/123">Example Show (2020)</a></h3></td></tr>
          <tr><td class="version"><h3><a href="/show/321">Other Show</a></h3></td></tr>
        </table>
      </body>
    </html>
    """


def _episode_page():
    return b"""
    <html>
      <body>
        <table>
          <tr class="epeven">
            <td>1</td>
            <td>2</td>
            <td><a href="/serie/Example_Show/1/2/Pilot">Pilot</a></td>
            <td>English</td>
            <td>WEB-DL+GROUP</td>
            <td>Completed</td>
            <td>HI</td>
            <td></td>
            <td></td>
            <td><a href="/updated/1/2/123">Download</a></td>
          </tr>
          <tr class="epeven">
            <td>1</td>
            <td>2</td>
            <td><a href="/serie/Example_Show/1/2/Pilot">Pilot</a></td>
            <td>English</td>
            <td>WEB-DL+GROUP</td>
            <td>95%</td>
            <td></td>
            <td></td>
            <td></td>
            <td><a href="/updated/1/2/incomplete">Download</a></td>
          </tr>
          <tr class="epeven">
            <td>1</td>
            <td>3</td>
            <td><a href="/serie/Example_Show/1/3/Next">Next</a></td>
            <td>English</td>
            <td>HDTV</td>
            <td>Completed</td>
            <td></td>
            <td></td>
            <td></td>
            <td><a href="/updated/1/3/124">Download</a></td>
          </tr>
        </table>
      </body>
    </html>
    """


def _episode_page_with_brazilian_portuguese():
    return b"""
    <html>
      <body>
        <table>
          <tr class="epeven">
            <td>1</td>
            <td>2</td>
            <td><a href="/serie/Example_Show/1/2/Pilot">Pilot</a></td>
            <td>Portuguese (Brazilian)</td>
            <td>WEB-DL+GROUP</td>
            <td>Completed</td>
            <td></td>
            <td></td>
            <td></td>
            <td><a href="/updated/1/2/porbr">Download</a></td>
          </tr>
        </table>
      </body>
    </html>
    """


def _movie_search_page():
    return b"""
    <html>
      <body>
        <table class="tabel">
          <tr><td><a href="movie/55">Dune (2021)</a></td></tr>
          <tr><td><a href="movie/66">Dune (1984)</a></td></tr>
        </table>
      </body>
    </html>
    """


def _movie_page():
    return b"""
    <html>
      <body>
        <table align="center" border="0" class="tabel95" width="100%">
          <tr><td class="NewsTitle">Version WEB-DL.GROUP, 0.00</td><td class="uploader">movie-uploader</td></tr>
          <tr>
            <td>English</td>
            <td>Completed</td>
            <td><a href="/download/movie/55/eng">Download</a></td>
          </tr>
          <tr><td><img src="/images/hi.jpg"></td></tr>
        </table>
      </body>
    </html>
    """


def _partial_movie_page():
    return b"""
    <html>
      <body>
        <table align="center" border="0" class="tabel95" width="100%">
          <tr><td class="NewsTitle">Version WEB-DL.GROUP, 0.00</td><td class="uploader">movie-uploader</td></tr>
          <tr>
            <td>English</td>
            <td>99.86% Completed</td>
            <td><a href="/download/movie/55/eng-partial">Download</a></td>
          </tr>
        </table>
      </body>
    </html>
    """


class Addic7edSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_credentials_or_cookies(self):
        provider = self.mod.Addic7edProvider()

        with self.assertRaisesRegex(PermissionError, "credentials or cookies"):
            provider.search({"kind": "episode", "series": "Example Show"}, [{"alpha3": "eng"}], {})

    def test_episode_search_uses_cookies_and_parses_rows(self):
        provider = self.mod.Addic7edProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del timeout, allow_redirects
            calls.append((url, headers, cookies, params))
            if url.endswith("/panel.php"):
                return self.mod.HttpResponse(200, b"panel", {})
            if url.endswith("/shows.php"):
                return self.mod.HttpResponse(200, _shows_page(), {})
            if url.endswith("/ajax_loadShow.php"):
                self.assertEqual(params, {"show": "123", "season": 1, "langs": "|"})
                self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
                return self.mod.HttpResponse(200, _episode_page(), {})
            raise AssertionError(url)

        provider._http_get = get_response
        results = provider.search(
            {
                "kind": "episode",
                "series": "Example Show",
                "year": 2020,
                "season": 1,
                "episode": 2,
                "release_group": "GROUP",
            },
            [{"alpha3": "eng"}],
            {"cookies": "PHPSESSID=session; wikisubtitlesuser=user", "user_agent": "UnitTest/1.0"},
        )

        self.assertEqual(calls[0][0], "https://www.addic7ed.com/panel.php")
        self.assertEqual(calls[0][1]["User-Agent"], "UnitTest/1.0")
        self.assertEqual(calls[0][2]["PHPSESSID"], "session")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "addic7ed")
        self.assertEqual(results[0]["language"], {"alpha3": "eng", "hi": True, "forced": False})
        self.assertEqual(results[0]["release_info"], "WEB-DL,GROUP")
        self.assertEqual(results[0]["provider_payload"]["download_link"], "updated/1/2/123")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertIn("source", results[0]["matches"])

    def test_episode_search_matches_requested_multi_episode_list(self):
        provider = self.mod.Addic7edProvider()

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/panel.php"):
                return self.mod.HttpResponse(200, b"panel", {})
            if url.endswith("/shows.php"):
                return self.mod.HttpResponse(200, _shows_page(), {})
            if url.endswith("/ajax_loadShow.php"):
                self.assertEqual(params, {"show": "123", "season": 1, "langs": "|"})
                return self.mod.HttpResponse(200, _episode_page(), {})
            raise AssertionError(url)

        provider._http_get = get_response
        results = provider.search(
            {
                "kind": "episode",
                "series": "Example Show",
                "year": 2020,
                "season": 1,
                "episode": [2, 3],
            },
            [{"alpha3": "eng"}],
            {"cookies": "PHPSESSID=session"},
        )

        self.assertEqual([item["provider_payload"]["episode"] for item in results], [2, 3])

    def test_episode_search_preserves_brazilian_portuguese_country(self):
        provider = self.mod.Addic7edProvider()

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/panel.php"):
                return self.mod.HttpResponse(200, b"panel", {})
            if url.endswith("/shows.php"):
                return self.mod.HttpResponse(200, _shows_page(), {})
            if url.endswith("/ajax_loadShow.php"):
                return self.mod.HttpResponse(200, _episode_page_with_brazilian_portuguese(), {})
            raise AssertionError(url)

        provider._http_get = get_response
        results = provider.search(
            {
                "kind": "episode",
                "series": "Example Show",
                "year": 2020,
                "season": 1,
                "episode": 2,
            },
            [{"alpha3": "por", "country_alpha2": "BR"}],
            {"cookies": "PHPSESSID=session"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"], {"alpha3": "por", "country_alpha2": "BR", "hi": False, "forced": False})

    def test_movie_search_logs_in_and_parses_movie_page(self):
        provider = self.mod.Addic7edProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del timeout, cookies, allow_redirects
            calls.append(("GET", url, headers, params))
            if url.endswith("/login.php"):
                return self.mod.HttpResponse(200, b"<html>login</html>", {})
            if url.endswith("/search.php"):
                self.assertEqual(params, {"search": "Dune"})
                return self.mod.HttpResponse(200, _movie_search_page(), {})
            if url.endswith("/movie/55"):
                return self.mod.HttpResponse(200, _movie_page(), {})
            raise AssertionError(url)

        def post_response(url, data, headers, cookies, timeout=30, allow_redirects=True):
            del timeout, cookies
            calls.append(("POST", url, data, headers, allow_redirects))
            self.assertEqual(data["username"], "user")
            self.assertEqual(data["password"], "pass")
            return self.mod.HttpResponse(302, b"", {"location": "/"})

        provider._http_get = get_response
        provider._http_post = post_response
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "release_group": "GROUP"},
            [{"alpha3": "eng"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(calls[0][1], "https://www.addic7ed.com/login.php")
        self.assertEqual(calls[1][0], "POST")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["download_link"], "download/movie/55/eng")
        self.assertEqual(results[0]["display"]["uploader"], "movie-uploader")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_movie_search_rejects_partial_completed_rows(self):
        provider = self.mod.Addic7edProvider()

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del timeout, headers, cookies, allow_redirects
            if url.endswith("/panel.php"):
                return self.mod.HttpResponse(200, b"panel", {})
            if url.endswith("/search.php"):
                return self.mod.HttpResponse(200, _movie_search_page(), {})
            if url.endswith("/movie/55"):
                return self.mod.HttpResponse(200, _partial_movie_page(), {})
            raise AssertionError(url)

        provider._http_get = get_response
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "release_group": "GROUP"},
            [{"alpha3": "eng"}],
            {"cookies": "PHPSESSID=session"},
        )

        self.assertEqual(results, [])


class Addic7edDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_uses_referer_and_normalizes_text(self):
        provider = self.mod.Addic7edProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, params=None, allow_redirects=True):
            del timeout, params, allow_redirects
            calls.append((url, headers, cookies))
            if url.endswith("/panel.php"):
                return self.mod.HttpResponse(200, b"panel", {})
            if url.endswith("/updated/1/2/123"):
                return self.mod.HttpResponse(
                    200,
                    b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n",
                    {"content-type": "text/plain"},
                )
            raise AssertionError(url)

        provider._http_get = get_response
        result = provider.download(
            {
                "page_url": "https://www.addic7ed.com/serie/Example_Show/1/2/Pilot",
                "download_link": "updated/1/2/123",
                "filename": "Example.Show.S01E02.srt",
            },
            {"alpha3": "eng"},
            {"cookies": "PHPSESSID=session"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(calls[1][1]["Referer"], "https://www.addic7ed.com/serie/Example_Show/1/2/Pilot")

    def test_download_reports_html_limit_response(self):
        provider = self.mod.Addic7edProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, params=None, allow_redirects=True: (
            self.mod.HttpResponse(200, b"panel", {})
            if url.endswith("/panel.php")
            else self.mod.HttpResponse(200, b"<html>limit</html>", {"content-type": "text/html"})
        )

        with self.assertRaisesRegex(RuntimeError, "download limit"):
            provider.download(
                {
                    "page_url": "https://www.addic7ed.com/serie/Example_Show/1/2/Pilot",
                    "download_link": "updated/1/2/123",
                    "filename": "Example.Show.S01E02.srt",
                },
                {"alpha3": "eng"},
                {"cookies": "PHPSESSID=session"},
            )


class Addic7edCookieTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_store_response_cookies_preserves_duplicate_set_cookie_headers(self):
        target = {}
        response = self.mod.HttpResponse(
            302,
            b"",
            [
                ("Set-Cookie", "PHPSESSID=session; path=/"),
                ("Set-Cookie", "wikisubtitlesuser=user; path=/"),
                ("Set-Cookie", "wikisubtitlespass=pass; path=/"),
            ],
        )

        self.mod._store_response_cookies(target, response)

        self.assertEqual(
            target,
            {
                "PHPSESSID": "session",
                "wikisubtitlesuser": "user",
                "wikisubtitlespass": "pass",
            },
        )


if __name__ == "__main__":
    unittest.main()
