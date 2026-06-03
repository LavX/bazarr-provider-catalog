import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path
from unittest import mock

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


def _zip_body(name="caligari.english.srt"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n")
        archive.writestr("readme.txt", "ignored")
    return stream.getvalue()


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
        self.assertIn("hash", results[0]["matches"])

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
        self.assertEqual(calls[0][0], "https://cinemaz.to/subtitles/222/download")
        self.assertFalse(calls[0][3])

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

    def test_download_extracts_subtitle_from_zip_archive(self):
        provider = self.mod.CinemaZProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, _zip_body(), {"content-type": "application/zip"}
        )

        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/111/download",
                "filename": "caligari.en.zip",
                "release_info": "The Cabinet of Dr Caligari 1920",
            },
            {"alpha3": "eng"},
            {"cookies": "cinemazx_session=valid"},
        )

        payload = base64.b64decode(result["content_b64"])
        self.assertIn(b"Hello\n", payload)
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_subtitle_from_rar_archive(self):
        provider = self.mod.CinemaZProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, b"Rar!\x1a\x07\x00rar-body", {"content-type": "application/vnd.rar"}
        )

        with mock.patch.object(self.mod, "_extract_rar_files", return_value=[("episode.srt", b"RAR subtitle")]):
            result = provider.download(
                {
                    "download_url": "https://cinemaz.to/subtitles/111/download",
                    "filename": "caligari.en.rar",
                    "release_info": "The Cabinet of Dr Caligari 1920",
                },
                {"alpha3": "eng"},
                {"cookies": "cinemazx_session=valid"},
            )

        payload = base64.b64decode(result["content_b64"])
        self.assertEqual(payload, b"RAR subtitle")
        self.assertEqual(result["format"], "srt")

    def test_download_selects_archive_member_by_season_and_episode(self):
        provider = self.mod.CinemaZProvider()
        body = _zip_body("Show.S01E02.srt")
        stream = io.BytesIO(body)
        with zipfile.ZipFile(stream, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Show.S02E02.srt", "correct")

        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, stream.getvalue(), {"content-type": "application/zip"}
        )

        result = provider.download(
            {
                "download_url": "https://cinemaz.to/subtitles/111/download",
                "filename": "show.en.zip",
                "kind": "episode",
                "season": 2,
                "episode": 2,
            },
            {"alpha3": "eng"},
            {"cookies": "cinemazx_session=valid"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"correct")


if __name__ == "__main__":
    unittest.main()
