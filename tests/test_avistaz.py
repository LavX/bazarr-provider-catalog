import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "avistaz"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("avistaz_provider", PROVIDER_DIR / "provider.py")
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
                  <tr><td>Title</td><td>Decision to Leave 2022 1080p BluRay x264-GROUP</td></tr>
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
                            <td><a href="https://avistaz.to/subtitles/12345/download">English ZIP</a></td>
                            <td>alice</td>
                          </tr>
                          <tr>
                            <td>Korean</td>
                            <td><a href="/subtitles/23456/download">Korean SRT</a></td>
                            <td>bob</td>
                          </tr>
                          <tr>
                            <td>French</td>
                            <td><a href="/subtitles/34567/download">French SRT</a></td>
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


def _zip_body(name="Decision.to.Leave.2022.English.srt"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n")
        archive.writestr("readme.txt", "ignored")
    return stream.getvalue()


class AvistazSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_cookies(self):
        provider = self.mod.AvistazProvider()

        with self.assertRaisesRegex(ValueError, "cookies"):
            provider.search(
                {"kind": "movie", "info_url": "https://avistaz.to/torrent/123-decision-to-leave"},
                [{"alpha3": "eng"}],
                {},
            )

    def test_search_returns_empty_when_video_did_not_come_from_avistaz(self):
        provider = self.mod.AvistazProvider()

        self.assertEqual(
            provider.search(
                {"kind": "movie", "info_url": "https://example.com/torrent/123"},
                [{"alpha3": "eng"}],
                {"cookies": "avistazx_session=valid", "user_agent": "UnitTest/1.0"},
            ),
            [],
        )

    def test_search_validates_cookies_and_parses_release_subtitle_table(self):
        provider = self.mod.AvistazProvider()
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
                "title": "Decision to Leave",
                "year": 2022,
                "info_url": "https://avistaz.to/torrent/123-decision-to-leave",
            },
            [{"alpha3": "eng"}, {"alpha3": "kor"}],
            {"cookies": "avistazx_session=valid; XSRF-TOKEN=token", "user_agent": "UnitTest/1.0"},
        )

        self.assertEqual(calls[0][0], "https://avistaz.to/rules")
        self.assertEqual(calls[0][1]["Referer"], "https://avistaz.to/")
        self.assertEqual(calls[0][1]["User-Agent"], "UnitTest/1.0")
        self.assertEqual(calls[0][2]["avistazx_session"], "valid")
        self.assertFalse(calls[0][3])
        self.assertEqual([item["language"]["alpha3"] for item in results], ["eng", "kor"])
        self.assertEqual(results[0]["provider"], "avistaz")
        self.assertEqual(results[0]["release_info"], "Decision to Leave 2022 1080p BluRay x264-GROUP")
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://avistaz.to/subtitles/12345/download")
        self.assertEqual(results[1]["provider_payload"]["download_url"], "https://avistaz.to/subtitles/23456/download")
        self.assertEqual(results[0]["display"]["uploader"], "alice")
        self.assertIn("hash", results[0]["matches"])

    def test_search_rejects_expired_cookies_on_rules_redirect(self):
        provider = self.mod.AvistazProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302, b"", {"location": "https://avistaz.to/auth/login"}
        )

        with self.assertRaisesRegex(PermissionError, "cookies"):
            provider.search(
                {"kind": "movie", "info_url": "https://avistaz.to/torrent/123-decision-to-leave"},
                [{"alpha3": "eng"}],
                {"cookies": "avistazx_session=expired"},
            )


class AvistazDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_direct_subtitle_payload(self):
        provider = self.mod.AvistazProvider()
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
        calls = []

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del timeout, allow_redirects
            calls.append((url, headers, cookies))
            return self.mod.HttpResponse(200, body, {"content-type": "application/x-subrip"})

        provider._http_get = get_bytes
        result = provider.download(
            {
                "download_url": "https://avistaz.to/subtitles/23456/download",
                "filename": "decision-to-leave.en.srt",
                "release_info": "Decision to Leave 2022",
            },
            {"alpha3": "eng"},
            {"cookies": "avistazx_session=valid", "user_agent": "UnitTest/1.0"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(calls[0][0], "https://avistaz.to/subtitles/23456/download")

    def test_download_extracts_subtitle_from_zip_archive(self):
        provider = self.mod.AvistazProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, _zip_body(), {"content-type": "application/zip"}
        )

        result = provider.download(
            {
                "download_url": "https://avistaz.to/subtitles/12345/download",
                "filename": "decision-to-leave.en.zip",
                "release_info": "Decision to Leave 2022",
            },
            {"alpha3": "eng"},
            {"cookies": "avistazx_session=valid"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertIn(b"Hello\n", payload)
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_subtitle_from_rar_archive(self):
        provider = self.mod.AvistazProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, b"Rar!\x1a\x07\x00rar-body", {"content-type": "application/vnd.rar"}
        )

        with mock.patch.object(self.mod, "_extract_rar_files", return_value=[("episode.srt", b"RAR subtitle")]):
            result = provider.download(
                {
                    "download_url": "https://avistaz.to/subtitles/12345/download",
                    "filename": "decision-to-leave.en.rar",
                    "release_info": "Decision to Leave 2022",
                },
                {"alpha3": "eng"},
                {"cookies": "avistazx_session=valid"},
            )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, b"RAR subtitle")
        self.assertEqual(result["format"], "srt")


if __name__ == "__main__":
    unittest.main()
