import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "titulky"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "titulky_provider", PROVIDER_DIR / "provider.py"
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


class TitulkyProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_credentials_and_boolean_options(self):
        provider = self.mod.TitulkyProvider()

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(_video("titulky_video_dune_2021.json"), [{"alpha3": "ces"}], {})

        with self.assertRaisesRegex(ValueError, "approved_only"):
            provider.search(
                _video("titulky_video_dune_2021.json"),
                [{"alpha3": "ces"}],
                {"username": "user", "password": "pass", "approved_only": "yes"},
            )

    def test_movie_search_logs_in_uses_imdb_and_parses_czech_rows(self):
        provider = self.mod.TitulkyProvider()
        posts = []
        gets = []

        def post(url, data=None, headers=None, timeout=30):
            del headers, timeout
            posts.append((url, dict(data or {})))
            return self.mod.HttpResponse(
                302,
                b"",
                {"Location": "/?msg_type=i&msg=ok", "set-cookie": "sid=session"},
                url,
            )

        def get(url, headers=None, timeout=30, allow_redirects=False):
            del headers, timeout, allow_redirects
            gets.append(url)
            if "action=serial" in url:
                self.assertIn("step=0", url)
                self.assertIn("id=1160419", url)
                return self.mod.HttpResponse(200, _fixture("titulky_browse_dune.html"), {}, url)
            raise AssertionError(url)

        provider._http_post = post
        provider._http_get = get
        results = provider.search(
            _video("titulky_video_dune_2021.json"),
            [{"alpha3": "ces", "alpha2": "cs"}],
            {"username": "user", "password": "pass", "approved_only": False, "skip_wrong_fps": False},
        )

        self.assertEqual(posts[0][0], "https://premium.titulky.com")
        self.assertEqual(posts[0][1], {"LoginName": "user", "LoginPassword": "pass"})
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["provider"], "titulky")
        self.assertEqual(results[0]["language"], {"alpha3": "ces", "alpha2": "cs", "hi": False, "forced": False})
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "101")
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://premium.titulky.com/download.php?id=101")
        self.assertTrue(results[0]["display"]["approved"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_episode_search_filters_language_approval_and_reads_fps(self):
        provider = self.mod.TitulkyProvider()
        calls = []

        provider._http_post = lambda url, data=None, headers=None, timeout=30: self.mod.HttpResponse(
            302,
            b"",
            {"Location": "/?msg_type=i&msg=ok"},
            url,
        )

        def get(url, headers=None, timeout=30, allow_redirects=False):
            del headers, timeout, allow_redirects
            calls.append(url)
            if "action=serial" in url:
                self.assertIn("step=1", url)
                self.assertIn("id=7366338", url)
                return self.mod.HttpResponse(200, _fixture("titulky_browse_chernobyl.html"), {}, url)
            if "action=detail&id=201" in url:
                return self.mod.HttpResponse(200, _fixture("titulky_detail_25fps.html"), {}, url)
            raise AssertionError(url)

        provider._http_get = get
        results = provider.search(
            _video("titulky_video_chernobyl_s01e01.json"),
            [{"alpha3": "slk", "alpha2": "sk"}],
            {"username": "user", "password": "pass", "approved_only": True, "skip_wrong_fps": True},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["201"])
        self.assertEqual(results[0]["language"]["alpha3"], "slk")
        self.assertEqual(results[0]["display"]["fps"], 25.0)
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_mismatched_fps_keeps_result_with_low_score_and_no_matches(self):
        provider = self.mod.TitulkyProvider()
        provider._http_post = lambda url, data=None, headers=None, timeout=30: self.mod.HttpResponse(
            302,
            b"",
            {"Location": "/?msg_type=i&msg=ok"},
            url,
        )
        provider._http_get = lambda url, headers=None, timeout=30, allow_redirects=False: self.mod.HttpResponse(
            200,
            _fixture("titulky_detail_24fps.html") if "action=detail" in url else _fixture("titulky_browse_chernobyl.html"),
            {},
            url,
        )

        results = provider.search(
            _video("titulky_video_chernobyl_s01e01.json"),
            [{"alpha3": "slk", "alpha2": "sk"}],
            {"username": "user", "password": "pass", "approved_only": True, "skip_wrong_fps": True},
        )

        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "201")
        self.assertEqual(results[0]["matches"], [])
        self.assertEqual(results[0]["score"], 1)

    def test_legacy_fps_equivalents_match(self):
        self.assertTrue(self.mod._framerate_equal(23.976, 24.0))
        self.assertTrue(self.mod._framerate_equal(23.98, 24.0))

    def test_download_extracts_zip_subtitle(self):
        provider = self.mod.TitulkyProvider()
        provider._logged_in = True
        body = _zip_body(
            {
                "Dune.2021.cs.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nCzech line\r\n",
            }
        )
        provider._http_get = lambda url, headers=None, timeout=30, allow_redirects=False: self.mod.HttpResponse(200, body, {}, url)
        result = provider.download(
            {
                "download_url": "https://premium.titulky.com/download.php?id=101",
                "page_link": "https://premium.titulky.com/?action=detail&id=101",
                "filename": "titulky.101.cs.zip",
            },
            {"alpha3": "ces"},
            {"username": "user", "password": "pass", "approved_only": False, "skip_wrong_fps": False},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nCzech line\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_extract_download_accepts_direct_subtitle_body(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nCzech line\r\n"

        result = self.mod.extract_download(body, {"filename": "titulky.101.cs.zip"})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nCzech line\n")
        self.assertEqual(result["format"], "srt")

    def test_single_file_archive_without_subtitle_reports_download_limit(self):
        body = _zip_body({"limit.txt": b"limit exceeded"})

        with self.assertRaisesRegex(RuntimeError, "download limit"):
            self.mod.extract_download(body, {"filename": "limit.zip"})

    def test_http_get_converts_urllib_http_error_to_response(self):
        provider = self.mod.TitulkyProvider()
        original_build_opener = self.mod.urllib.request.build_opener

        class FailingOpener:
            def open(self, request, timeout=30):
                del request, timeout
                raise self_mod.urllib.error.HTTPError(
                    "https://premium.titulky.com/download.php?id=101",
                    429,
                    "Too Many Requests",
                    {},
                    io.BytesIO(b"limited"),
                )

        self_mod = self.mod
        self.mod.urllib.request.build_opener = lambda *args: FailingOpener()
        try:
            response = provider._http_get("https://premium.titulky.com/download.php?id=101")
        finally:
            self.mod.urllib.request.build_opener = original_build_opener

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.body, b"limited")


if __name__ == "__main__":
    unittest.main()
