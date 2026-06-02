import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "titlovi"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "titlovi_provider", PROVIDER_DIR / "provider.py"
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


class TitloviProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_username_and_password(self):
        provider = self.mod.TitloviProvider()

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(_video("titlovi_video_dune_2021.json"), [{"alpha3": "eng"}], {})

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(_video("titlovi_video_dune_2021.json"), [{"alpha3": "eng"}], {"username": "user"})

    def test_movie_search_logs_in_maps_languages_and_reads_pages(self):
        provider = self.mod.TitloviProvider()
        posts = []
        gets = []

        def post(url, params=None, headers=None, timeout=10):
            del headers, timeout
            posts.append((url, dict(params or {})))
            return self.mod.HttpResponse(200, _fixture("titlovi_login.json"), {})

        def get(url, params=None, headers=None, timeout=10):
            del headers, timeout
            gets.append((url, dict(params or {})))
            self.assertEqual(params["query"], "Dune")
            self.assertEqual(params["lang"], "English|Hrvatski")
            self.assertEqual(params["imdbID"], "tt1160419")
            self.assertEqual(params["token"], "token-value")
            self.assertEqual(params["userid"], "77")
            if params.get("pg") == 2:
                return self.mod.HttpResponse(200, _fixture("titlovi_search_dune_page2.json"), {})
            return self.mod.HttpResponse(200, _fixture("titlovi_search_dune_page1.json"), {})

        provider._http_post = post
        provider._http_get = get
        results = provider.search(
            _video("titlovi_video_dune_2021.json"),
            [{"alpha3": "eng"}, {"alpha3": "hrv"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(posts[0][0], "https://kodi.titlovi.com/api/subtitles/gettoken")
        self.assertEqual(posts[0][1], {"username": "user", "password": "pass", "json": True})
        self.assertEqual([call[1].get("pg") for call in gets], [None, 2])
        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["1001", "1002"])
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://kodi.titlovi.com/download/dune.zip")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_search_filters_api_rows_to_requested_languages(self):
        provider = self.mod.TitloviProvider()
        provider._http_post = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(200, _fixture("titlovi_login.json"), {})

        def get(url, params=None, headers=None, timeout=10):
            del url, headers, timeout
            self.assertEqual(params["lang"], "English")
            if params.get("pg") == 2:
                return self.mod.HttpResponse(200, _fixture("titlovi_search_dune_page2.json"), {})
            return self.mod.HttpResponse(200, _fixture("titlovi_search_dune_page1.json"), {})

        provider._http_get = get
        results = provider.search(
            _video("titlovi_video_dune_2021.json"),
            [{"alpha3": "eng"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["1001"])
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"eng"})

    def test_episode_search_filters_episode_and_allows_episode_zero_pack(self):
        provider = self.mod.TitloviProvider()
        provider._http_post = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(200, _fixture("titlovi_login.json"), {})

        def get(url, params=None, headers=None, timeout=10):
            del url, headers, timeout
            self.assertEqual(params["query"], "Chernobyl")
            self.assertEqual(params["lang"], "Srpski")
            self.assertEqual(params["season"], 1)
            self.assertNotIn("episode", params)
            return self.mod.HttpResponse(200, _fixture("titlovi_search_chernobyl_s01.json"), {})

        provider._http_get = get
        results = provider.search(
            _video("titlovi_video_chernobyl_s01e01.json"),
            [{"alpha3": "srp"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["2001", "2002"])
        self.assertFalse(results[0]["provider_payload"]["is_pack"])
        self.assertTrue(results[1]["provider_payload"]["is_pack"])
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_download_extracts_matching_episode_from_pack_zip(self):
        provider = self.mod.TitloviProvider()
        archive = _zip_body(
            {
                "Chernobyl.S01E02.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nEpisode two\r\n",
                "Chernobyl.S01E01.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nEpisode one\r\n",
            }
        )
        provider._http_get = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(200, archive, {})
        provider._login_token = "token-value"
        provider._user_id = "77"

        result = provider.download(
            {
                "download_url": "https://kodi.titlovi.com/download/chernobyl-pack.zip",
                "filename": "titlovi.2002.srp.zip",
                "season": 1,
                "episode": 1,
                "is_pack": True,
                "language": "srp",
            },
            {"alpha3": "srp"},
            {"username": "user", "password": "pass"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_extract_download_accepts_direct_subtitle_body(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nDirect subtitle\r\n"

        result = self.mod.extract_download(body, {"filename": "titlovi.1001.eng.zip"})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nDirect subtitle\n")
        self.assertEqual(result["format"], "srt")

    def test_extract_download_selects_serbian_script_from_bundled_archive(self):
        body = _zip_body(
            {
                "Movie.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nLatin\n",
                "Movie.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nCyrillic\n",
            }
        )

        latin = self.mod.extract_download(body, {"language": "srp", "filename": "movie.zip"})
        cyrillic = self.mod.extract_download(body, {"language": "srp", "script": "Cyrl", "filename": "movie.zip"})

        self.assertIn(b"Latin", base64.b64decode(latin["content_b64"]))
        self.assertIn(b"Cyrillic", base64.b64decode(cyrillic["content_b64"]))

    def test_download_reports_too_many_requests(self):
        provider = self.mod.TitloviProvider()
        provider._login_token = "token-value"
        provider._user_id = "77"
        provider._http_get = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(429, b"", {})

        with self.assertRaisesRegex(RuntimeError, "Too many requests"):
            provider.download(
                {"download_url": "https://kodi.titlovi.com/download/dune.zip", "filename": "movie.zip"},
                {"alpha3": "eng"},
                {"username": "user", "password": "pass"},
            )

    def test_search_returns_empty_after_search_server_error(self):
        provider = self.mod.TitloviProvider()
        provider._http_post = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(200, _fixture("titlovi_login.json"), {})
        provider._http_get = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(500, b"server error", {})

        results = provider.search(
            _video("titlovi_video_dune_2021.json"),
            [{"alpha3": "eng"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual(results, [])

    def test_search_keeps_earlier_results_when_later_page_fails(self):
        provider = self.mod.TitloviProvider()
        provider._http_post = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(200, _fixture("titlovi_login.json"), {})

        def get(url, params=None, headers=None, timeout=10):
            del url, headers, timeout
            if params.get("pg") == 2:
                return self.mod.HttpResponse(500, b"server error", {})
            return self.mod.HttpResponse(200, _fixture("titlovi_search_dune_page1.json"), {})

        provider._http_get = get
        results = provider.search(
            _video("titlovi_video_dune_2021.json"),
            [{"alpha3": "eng"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["1001"])

    def test_http_get_converts_urllib_http_error_to_response(self):
        provider = self.mod.TitloviProvider()
        original_urlopen = self.mod.urllib.request.urlopen

        def raise_http_error(request, timeout=10):
            del request, timeout
            raise self.mod.urllib.error.HTTPError(
                "https://kodi.titlovi.com/api/subtitles/search",
                429,
                "Too Many Requests",
                {},
                io.BytesIO(b"limited"),
            )

        self.mod.urllib.request.urlopen = raise_http_error
        try:
            response = provider._http_get("https://kodi.titlovi.com/api/subtitles/search")
        finally:
            self.mod.urllib.request.urlopen = original_urlopen

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.body, b"limited")


if __name__ == "__main__":
    unittest.main()
