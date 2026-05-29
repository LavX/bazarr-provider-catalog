import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "yavkanet"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("yavkanet_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMDB_HTML = b"""
<table>
  <tr>
    <td>
      <a class="balon" href="/subtitle/dune-2021" content="Dune.2021.1080p.WEB-DL-FLUX">
        Dune.2021.1080p.WEB-DL-FLUX
      </a>
      <span>(2021)</span>
    </td>
    <td><span title="Frames per second">23.976</span></td>
    <td><a class="click">uploader</a></td>
  </tr>
</table>
"""
DETAIL_HTML = b"""
<form method="POST" action="/subtitle/dune-2021/">
  <input type="hidden" name="subtitle_id" value="123">
  <input type="hidden" name="token" value="abc">
</form>
"""
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nYavkaNet subtitle.\n"
CLOUDFLARE_BODY = b"<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script></html>"


def _zip_with(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class FakeResponse:
    def __init__(self, body, status=200, headers=None, url="https://yavka.net/imdb/tt1160419"):
        self.content = body
        self.status_code = status
        self.headers = headers or {}
        self.url = url


class FakeUrlopenResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class YavkaNetParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_imdb_results_extracts_rows(self):
        rows = self.mod.parse_imdb_results(IMDB_HTML)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["page_url"], "https://yavka.net/subtitle/dune-2021/")
        self.assertEqual(row["release"], "Dune.2021.1080p.WEB-DL-FLUX")
        self.assertEqual(row["title"], "Dune.2021.1080p.WEB-DL-FLUX")
        self.assertEqual(row["year"], 2021)
        self.assertEqual(row["fps"], 23.976)
        self.assertEqual(row["uploader"], "uploader")

    def test_parse_download_form_extracts_post_fields(self):
        form = self.mod.parse_download_form(DETAIL_HTML)

        self.assertEqual(form["method"], "POST")
        self.assertEqual(form["action_url"], "https://yavka.net/subtitle/dune-2021/")
        self.assertEqual(form["data"], {"subtitle_id": "123", "token": "abc"})

    def test_derive_matches_movie_release_tokens(self):
        row = self.mod.parse_imdb_results(IMDB_HTML)[0]
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "FLUX",
                "resolution": "1080p",
                "source": "Web",
            },
            row,
        )

        self.assertEqual(set(matches), {"title", "year", "release_group", "resolution", "source"})


class YavkaNetCloudflareTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_detects_cloudflare_challenge(self):
        self.assertTrue(self.mod.is_cloudflare_challenge(403, {"cf-mitigated": "challenge"}, b""))
        self.assertTrue(self.mod.is_cloudflare_challenge(403, {}, CLOUDFLARE_BODY))
        self.assertFalse(self.mod.is_cloudflare_challenge(200, {}, CLOUDFLARE_BODY))

    def test_http_get_uses_cloudscraper_by_default(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(b"<html>ok</html>")

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper) as create_scraper:
            state = {}
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state=state)

        self.assertEqual(body, b"<html>ok</html>")
        create_scraper.assert_called_once()
        scraper.get.assert_called_once()

    def test_http_get_uses_flaresolverr_fallback_after_challenge(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(
            CLOUDFLARE_BODY,
            status=403,
            headers={"cf-mitigated": "challenge"},
        )
        payload = {
            "status": "ok",
            "solution": {
                "status": 200,
                "response": "<html>solved</html>",
                "userAgent": "Mozilla/5.0 solved",
                "cookies": [{"name": "cf_clearance", "value": "token"}],
            },
        }

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.urllib.request,
            "urlopen",
            return_value=FakeUrlopenResponse(json.dumps(payload).encode("utf-8")),
        ) as urlopen:
            state = {}
            body = self.mod.http_get(
                "https://yavka.net/imdb/tt1160419",
                config={"flaresolverr_url": "http://flaresolverr:8191/v1", "flaresolverr_timeout_ms": 45000},
                state=state,
            )

        self.assertEqual(body, b"<html>solved</html>")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://flaresolverr:8191/v1")
        self.assertEqual(state["flaresolverr_user_agent"], "Mozilla/5.0 solved")
        self.assertEqual(state["flaresolverr_cookies"], {"cf_clearance": "token"})

    def test_http_get_raises_visible_error_without_flaresolverr(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(CLOUDFLARE_BODY, status=403)

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper):
            with self.assertRaises(self.mod.CloudflareBlockedError):
                self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})


class YavkaNetProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_imdb_rows_and_detail_form(self):
        provider = self.mod.YavkaNetProvider()
        called = []

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state
            called.append((url, referer))
            if url == "https://yavka.net/imdb/tt1160419":
                return IMDB_HTML
            if url == "https://yavka.net/subtitle/dune-2021/":
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "FLUX",
                "resolution": "1080p",
                "source": "Web",
            },
            [{"alpha3": "bul", "alpha2": "bg", "hi": True, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called[0], ("https://yavka.net/imdb/tt1160419", "https://yavka.net/"))
        self.assertEqual(called[1], ("https://yavka.net/subtitle/dune-2021/", "https://yavka.net/imdb/tt1160419"))
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["provider"], "yavkanet")
        self.assertEqual(result["language"], {"alpha3": "bul", "alpha2": "bg", "hi": True, "forced": False})
        self.assertEqual(result["provider_payload"]["form_data"], {"subtitle_id": "123", "token": "abc"})
        self.assertIn("release_group", result["matches"])

    def test_movie_search_keeps_imdb_row_with_localized_title(self):
        provider = self.mod.YavkaNetProvider()
        localized_imdb_html = IMDB_HTML.replace(b"Dune.2021.1080p.WEB-DL-FLUX", b"Dyuna.2021.1080p.WEB-DL-FLUX")

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state, referer
            if url == "https://yavka.net/imdb/tt1160419":
                return localized_imdb_html
            if url == "https://yavka.net/subtitle/dune-2021/":
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "bul", "alpha2": "bg"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)

    def test_search_skips_unsupported_language_without_network(self):
        provider = self.mod.YavkaNetProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected network"))

        results = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertEqual(results, [])

    def test_download_posts_form_and_extracts_zip_subtitle(self):
        body = _zip_with(
            {
                "Dune.2021.720p.WEB-DL.srt": b"wrong",
                "Dune.2021.1080p.WEB-DL-FLUX.srt": SRT_BODY,
            }
        )
        provider = self.mod.YavkaNetProvider()
        provider._http_post = lambda url, data, timeout=30, config=None, state=None, referer=None: body

        result = provider.download(
            {
                "download_url": "https://yavka.net/subtitle/dune-2021/",
                "form_data": {"subtitle_id": "123", "token": "abc"},
                "filename": "Dune.2021.1080p.WEB-DL-FLUX.zip",
                "release": "Dune.2021.1080p.WEB-DL-FLUX",
                "video": {
                    "kind": "movie",
                    "title": "Dune",
                    "year": 2021,
                    "release_group": "FLUX",
                    "resolution": "1080p",
                    "source": "Web",
                },
            },
            {"alpha3": "bul"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_rejects_episode_archive_without_requested_member(self):
        body = _zip_with({"Example.S01E03.srt": b"wrong episode"})

        with self.assertRaisesRegex(ValueError, "requested episode"):
            self.mod.extract_download(
                body,
                {
                    "filename": "Example.S01.zip",
                    "video": {"kind": "episode", "series": "Example", "season": 1, "episode": 2},
                },
            )


if __name__ == "__main__":
    unittest.main()
