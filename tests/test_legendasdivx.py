import base64
import hashlib
import importlib.util
import io
import json
import urllib.parse
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "legendasdivx"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "legendasdivx_provider", PROVIDER_DIR / "provider.py"
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


class LegendasDivxSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_username_and_password(self):
        provider = self.mod.LegendasDivxProvider()

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(_video("legendasdivx_video_dune_2021.json"), [{"alpha3": "por"}], {})

        with self.assertRaisesRegex(PermissionError, "username and password"):
            provider.search(
                _video("legendasdivx_video_dune_2021.json"),
                [{"alpha3": "por"}],
                {"username": "user"},
            )

    def test_movie_search_logs_in_and_parses_portuguese_results(self):
        provider = self.mod.LegendasDivxProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, allow_redirects=True):
            del timeout, allow_redirects
            calls.append(("GET", url, dict(headers), dict(cookies)))
            if url.endswith("/forum/ucp.php?mode=login"):
                return self.mod.HttpResponse(
                    200,
                    _fixture("legendasdivx_login.html"),
                    {"set-cookie": "PHPSESSID=guest; path=/"},
                )
            if "modules.php" in url:
                parsed = urllib.parse.urlparse(url)
                query = urllib.parse.parse_qs(parsed.query)
                self.assertEqual(query["d_op"], ["search"])
                self.assertEqual(query["op"], ["_jz00"])
                self.assertEqual(query["query"], ["tt1160419"])
                self.assertEqual(query["form_cat"], ["28"])
                return self.mod.HttpResponse(200, _fixture("legendasdivx_search_dune_por.html"), {})
            raise AssertionError(url)

        def post_response(url, data, headers, cookies, timeout=30, allow_redirects=True):
            del timeout, headers, allow_redirects
            calls.append(("POST", url, dict(data), dict(cookies)))
            self.assertEqual(data["sid"], "login-sid")
            self.assertEqual(data["username"], "user")
            self.assertEqual(data["password"], "pass")
            return self.mod.HttpResponse(
                302,
                b"",
                {"set-cookie": "PHPSESSID=auth; path=/; HttpOnly"},
            )

        provider._http_get = get_response
        provider._http_post = post_response
        results = provider.search(
            _video("legendasdivx_video_dune_2021.json"),
            [{"alpha3": "por"}],
            {"username": "user", "password": "pass", "request_delay_ms": 0},
        )

        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[1][0], "POST")
        self.assertEqual(calls[2][3]["PHPSESSID"], "auth")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["provider"], "legendasdivx")
        self.assertEqual(results[0]["language"], {"alpha3": "por", "alpha2": "pt", "hi": False, "forced": False})
        self.assertEqual(results[0]["provider_payload"]["lid"], "1101")
        self.assertEqual(results[0]["display"]["uploader"], "pt_uploader")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])

    def test_episode_search_uses_series_imdb_and_brazilian_language(self):
        provider = self.mod.LegendasDivxProvider()

        def get_response(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/forum/ucp.php?mode=login"):
                return self.mod.HttpResponse(200, _fixture("legendasdivx_login.html"), {})
            if "modules.php" in url:
                parsed = urllib.parse.urlparse(url)
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                self.assertEqual(query["d_op"], ["jz_00"])
                self.assertEqual(query["faz"], ["pesquisa_episodio"])
                self.assertEqual(query["idioma"], ["29"])
                self.assertEqual(query["temporada"], ["1"])
                self.assertEqual(query["episodio"], ["1"])
                self.assertEqual(query["imdb"], ["7366338"])
                return self.mod.HttpResponse(200, _fixture("legendasdivx_search_chernobyl_pob.html"), {})
            raise AssertionError(url)

        provider._http_get = get_response
        provider._http_post = lambda url, data, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302,
            b"",
            {"set-cookie": "PHPSESSID=auth; path=/"},
        )
        results = provider.search(
            _video("legendasdivx_video_chernobyl_s01e01.json"),
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"username": "user", "password": "pass", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "por-BR")
        self.assertEqual(results[0]["language"]["country"], "BR")
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_skip_wrong_fps_filters_mismatched_results(self):
        provider = self.mod.LegendasDivxProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: (
            self.mod.HttpResponse(200, _fixture("legendasdivx_login.html"), {})
            if url.endswith("/forum/ucp.php?mode=login")
            else self.mod.HttpResponse(200, _fixture("legendasdivx_search_dune_por.html"), {})
        )
        provider._http_post = lambda url, data, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302,
            b"",
            {"set-cookie": "PHPSESSID=auth; path=/"},
        )

        results = provider.search(
            _video("legendasdivx_video_dune_2021.json"),
            [{"alpha3": "por"}],
            {"username": "user", "password": "pass", "skip_wrong_fps": True, "request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["lid"] for item in results], ["1101"])

    def test_search_limit_raises(self):
        provider = self.mod.LegendasDivxProvider()
        body = b"<!--pesquisas: 145--><div class='pager_bar'>(145 encontradas)</div>"
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: (
            self.mod.HttpResponse(200, _fixture("legendasdivx_login.html"), {})
            if url.endswith("/forum/ucp.php?mode=login")
            else self.mod.HttpResponse(200, body, {})
        )
        provider._http_post = lambda url, data, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302,
            b"",
            {"set-cookie": "PHPSESSID=auth; path=/"},
        )

        with self.assertRaisesRegex(RuntimeError, "search limit"):
            provider.search(
                _video("legendasdivx_video_dune_2021.json"),
                [{"alpha3": "por"}],
                {"username": "user", "password": "pass", "request_delay_ms": 0},
            )


class LegendasDivxDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_extracts_matching_episode_file_from_zip(self):
        provider = self.mod.LegendasDivxProvider()
        archive_body = _zip_body(
            {
                "Chernobyl.S01E02.pt.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nEpisode two\r\n",
                "Chernobyl.S01E01.pt.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nEpisode one\r\n",
            }
        )
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: (
            self.mod.HttpResponse(200, _fixture("legendasdivx_login.html"), {})
            if url.endswith("/forum/ucp.php?mode=login")
            else self.mod.HttpResponse(200, archive_body, {"content-type": "application/zip"})
        )
        provider._http_post = lambda url, data, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302,
            b"",
            {"set-cookie": "PHPSESSID=auth; path=/"},
        )

        result = provider.download(
            {
                "provider": "legendasdivx",
                "schema": 1,
                "page_link": "https://www.legendasdivx.pt/modules.php?name=Downloads&d_op=getit&lid=2201",
                "filename": "legendasdivx.chernobyl.s01e01.pt.zip",
                "season": 1,
                "episode": 1,
                "release_info": "Chernobyl.S01E01.1080p.WEB.H264-MEMENTO",
            },
            {"alpha3": "por-BR", "alpha2": "pt", "country": "BR"},
            {"username": "user", "password": "pass", "request_delay_ms": 0},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Episode one", decoded)
        self.assertNotIn(b"Episode two", decoded)
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
