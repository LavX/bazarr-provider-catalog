import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "ktuvit"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("ktuvit_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_d(payload):
    import json

    return json.dumps({"d": json.dumps(payload)}).encode("utf-8")


def _movie_page():
    return b"""
    <html>
      <body>
        <table id="subtitlesList">
          <tbody>
            <tr>
              <td>Dune.2021.BluRay-GROUP<br></td>
              <td></td><td></td><td></td><td></td>
              <td><a data-subtitle-id="SUB1">download</a></td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """


def _episode_page():
    return b"""
    <html>
      <body>
        <table>
          <tr>
            <td>Example.Show.S01E02.WEB-DL-GROUP<br></td>
            <td></td><td></td><td></td><td></td>
            <td><input data-sub-id="EP1"></td>
          </tr>
        </table>
      </body>
    </html>
    """


class KtuvitSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_credentials(self):
        provider = self.mod.KtuvitProvider()

        with self.assertRaisesRegex(PermissionError, "email and hashed_password"):
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "heb"}], {})

    def test_movie_search_logs_in_and_parses_movie_subtitles(self):
        provider = self.mod.KtuvitProvider()
        calls = []

        def post_response(url, json_data, headers, cookies, timeout=30):
            del headers, timeout
            calls.append(("POST", url, json_data, cookies))
            if url.endswith("/Services/MembershipService.svc/Login"):
                self.assertEqual(json_data, {"request": {"Email": "user@example.com", "Password": "hash"}})
                return self.mod.HttpResponse(200, _json_d({"IsSuccess": True}), {"set-cookie": "Login=login-token; path=/"})
            if url.endswith("/Services/ContentProvider.svc/SearchPage_search"):
                request = json_data["request"]
                self.assertEqual(request["FilmName"], "Dune")
                self.assertEqual(request["SearchType"], "0")
                self.assertEqual(request["Year"], 2021)
                self.assertEqual(cookies["Login"], "login-token")
                return self.mod.HttpResponse(
                    200,
                    _json_d({"Films": [{"IMDB_Link": "https://www.imdb.com/title/tt1160419/", "ID": "MOV1"}]}),
                    {},
                )
            raise AssertionError(url)

        def get_response(url, headers, cookies, timeout=30):
            del headers, timeout
            calls.append(("GET", url, cookies))
            if url.endswith("/MovieInfo.aspx?ID=MOV1"):
                self.assertEqual(cookies["Login"], "login-token")
                return self.mod.HttpResponse(200, _movie_page(), {})
            raise AssertionError(url)

        provider._http_post = post_response
        provider._http_get = get_response
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "imdb_id": "tt1160419", "release_group": "GROUP"},
            [{"alpha3": "heb"}],
            {"email": "user@example.com", "hashed_password": "hash"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "ktuvit")
        self.assertEqual(results[0]["language"], {"alpha3": "heb", "hi": False, "forced": False})
        self.assertEqual(results[0]["release_info"], "Dune.2021.BluRay-GROUP")
        self.assertEqual(results[0]["provider_payload"]["ktuvit_id"], "MOV1")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "SUB1")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_episode_search_uses_series_imdb_id_and_parses_ajax_rows(self):
        provider = self.mod.KtuvitProvider()

        provider._http_post = lambda url, json_data, headers, cookies, timeout=30: (
            self.mod.HttpResponse(200, _json_d({"IsSuccess": True}), {"set-cookie": "Login=login-token; path=/"})
            if url.endswith("/Login")
            else self.mod.HttpResponse(
                200,
                _json_d({"Films": [{"IMDB_Link": "https://www.imdb.com/title/tt0903747/", "ID": "SER1"}]}),
                {},
            )
        )

        def get_response(url, headers, cookies, timeout=30):
            del headers, cookies, timeout
            self.assertEqual(
                url,
                "https://www.ktuvit.me/Services/GetModuleAjax.ashx?moduleName=SubtitlesList&SeriesID=SER1&Season=1&Episode=2",
            )
            return self.mod.HttpResponse(200, _episode_page(), {})

        provider._http_get = get_response
        results = provider.search(
            {
                "kind": "episode",
                "series": "Example Show",
                "year": 2008,
                "series_imdb_id": "tt0903747",
                "season": 1,
                "episode": 2,
                "release_group": "GROUP",
            },
            [{"alpha3": "heb"}],
            {"email": "user@example.com", "hashed_password": "hash"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["ktuvit_id"], "SER1")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "EP1")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("series_imdb_id", results[0]["matches"])

    def test_movie_search_uses_tmdb_fallback_when_imdb_id_is_missing(self):
        provider = self.mod.KtuvitProvider()
        tmdb_urls = []

        provider._http_post = lambda url, json_data, headers, cookies, timeout=30: (
            self.mod.HttpResponse(200, _json_d({"IsSuccess": True}), {"set-cookie": "Login=login-token; path=/"})
            if url.endswith("/Login")
            else self.mod.HttpResponse(
                200,
                _json_d({"Films": [{"IMDB_Link": "https://www.imdb.com/title/tt1160419/", "ID": "MOV1"}]}),
                {},
            )
        )

        def get_response(url, headers, cookies, timeout=30):
            del headers, cookies, timeout
            tmdb_urls.append(url)
            if "api.tmdb.org/3/search/movie" in url:
                return self.mod.HttpResponse(200, b'{"results":[{"id":438631}]}', {})
            if "api.tmdb.org/3/movie/438631" in url:
                return self.mod.HttpResponse(200, b'{"imdb_id":"tt1160419"}', {})
            if url.endswith("/MovieInfo.aspx?ID=MOV1"):
                return self.mod.HttpResponse(200, _movie_page(), {})
            raise AssertionError(url)

        provider._http_get = get_response
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [{"alpha3": "heb"}],
            {"email": "user@example.com", "hashed_password": "hash"},
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(any("search/movie" in url for url in tmdb_urls))
        self.assertTrue(any("movie/438631" in url for url in tmdb_urls))


class KtuvitDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_requests_identifier_then_fetches_subtitle(self):
        provider = self.mod.KtuvitProvider()
        calls = []

        def post_response(url, json_data, headers, cookies, timeout=30):
            del headers, timeout
            calls.append(("POST", url, json_data, cookies))
            if url.endswith("/Login"):
                return self.mod.HttpResponse(200, _json_d({"IsSuccess": True}), {"set-cookie": "Login=login-token; path=/"})
            if url.endswith("/RequestSubtitleDownload"):
                self.assertEqual(json_data["request"]["FilmID"], "MOV1")
                self.assertEqual(json_data["request"]["SubtitleID"], "SUB1")
                self.assertEqual(cookies["Login"], "login-token")
                return self.mod.HttpResponse(200, _json_d({"DownloadIdentifier": "DLID"}), {})
            raise AssertionError(url)

        def get_response(url, headers, cookies, timeout=30):
            del headers, timeout
            calls.append(("GET", url, cookies))
            self.assertEqual(url, "https://www.ktuvit.me/Services/DownloadFile.ashx?DownloadIdentifier=DLID")
            return self.mod.HttpResponse(
                200,
                "1\r\n00:00:01,000 --> 00:00:02,000\r\nשלום\r\n".encode("utf-8"),
                {"content-type": "application/x-subrip"},
            )

        provider._http_post = post_response
        provider._http_get = get_response
        result = provider.download(
            {
                "ktuvit_id": "MOV1",
                "subtitle_id": "SUB1",
                "release_info": "Dune.2021.BluRay-GROUP",
                "filename": "Dune.2021.BluRay-GROUP.srt",
            },
            {"alpha3": "heb"},
            {"email": "user@example.com", "hashed_password": "hash"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, "1\n00:00:01,000 --> 00:00:02,000\nשלום\n".encode("utf-8"))
        self.assertEqual(result["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(calls[1][1], "https://www.ktuvit.me/Services/ContentProvider.svc/RequestSubtitleDownload")


if __name__ == "__main__":
    unittest.main()
