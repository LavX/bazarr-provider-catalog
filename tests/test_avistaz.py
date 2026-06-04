import base64
import hashlib
import importlib.util
import io
import json
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


def _variant_release_page():
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
                            <td>Brazilian Portuguese</td>
                            <td><a href="/subtitles/45678/download">Decision.to.Leave.pt-BR.ass</a></td>
                            <td>dana</td>
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


def _duplicate_language_release_page():
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
                            <td><a href="/subtitles/12345/download">English ZIP</a></td>
                            <td>alice</td>
                          </tr>
                          <tr>
                            <td>English</td>
                            <td><a href="/subtitles/23456/download">English ZIP</a></td>
                            <td>bob</td>
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


class AvistazManifestTests(unittest.TestCase):
    def test_manifest_pins_all_py7zz_platform_wheels(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        py7zz = next(item for item in manifest["dependencies"]["requirements"] if item["name"] == "py7zz")

        self.assertEqual(
            set(py7zz["hashes"]),
            {
                "sha256:e6394b5ba89d61ceca780e6d67b89896fd58f0606c9b07227446496b52e8d0ae",
                "sha256:cd3d911976c184e61c641baf4e26a80f4ac474c830bf12cc8e541490c229bfd6",
                "sha256:6ae5d0516a1a1d43d905e40799ff75aedbbac431374dff740481f548a51d538f",
            },
        )


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
        self.assertNotIn("hash", results[0]["matches"])
        self.assertFalse(results[0]["hash_verifiable"])
        self.assertFalse(results[0]["hearing_impaired_verifiable"])
        self.assertFalse(results[0]["hearing_impaired"])

    def test_search_honors_country_alpha2_and_preserves_row_filename(self):
        provider = self.mod.AvistazProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _variant_release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "Decision to Leave",
                "year": 2022,
                "info_url": "https://avistaz.to/torrent/123-decision-to-leave",
            },
            [{"alpha3": "por", "country_alpha2": "BR"}],
            {"cookies": "avistazx_session=valid"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"], {"alpha3": "por", "country_alpha2": "BR", "hi": False, "forced": False})
        self.assertEqual(results[0]["filename"], "Decision.to.Leave.pt-BR.ass")
        self.assertEqual(results[0]["provider_payload"]["filename"], "Decision.to.Leave.pt-BR.ass")
        self.assertNotIn("hash", results[0]["matches"])
        self.assertFalse(results[0]["hash_verifiable"])
        self.assertEqual(results[0]["score"], results[0]["score_without_hash"])

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

    def test_search_rejects_forced_language_requests(self):
        provider = self.mod.AvistazProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
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
            [{"alpha3": "eng", "forced": True}],
            {"cookies": "avistazx_session=valid"},
        )

        self.assertEqual(results, [])

    def test_search_gives_same_language_rows_distinct_ids(self):
        provider = self.mod.AvistazProvider()

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del headers, cookies, timeout, allow_redirects
            if url.endswith("/rules"):
                return self.mod.HttpResponse(200, b"<html>rules</html>", {})
            return self.mod.HttpResponse(200, _duplicate_language_release_page(), {})

        provider._http_get = get_bytes
        results = provider.search(
            {
                "kind": "movie",
                "title": "Decision to Leave",
                "year": 2022,
                "info_url": "https://avistaz.to/torrent/123-decision-to-leave",
            },
            [{"alpha3": "eng"}],
            {"cookies": "avistazx_session=valid"},
        )

        self.assertEqual(len(results), 2)
        ids = [item["id"] for item in results]
        self.assertEqual(len(set(ids)), 2)
        self.assertIn("12345", ids[0])
        self.assertIn("23456", ids[1])


class AvistazDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_direct_subtitle_payload(self):
        provider = self.mod.AvistazProvider()
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
        calls = []

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del timeout
            calls.append((url, headers, cookies, allow_redirects))
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
        self.assertFalse(calls[0][3])

    def test_download_rejects_login_redirects_and_html(self):
        provider = self.mod.AvistazProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            302, b"", {"location": "https://avistaz.to/login"}
        )

        with self.assertRaises(PermissionError):
            provider.download(
                {
                    "download_url": "https://avistaz.to/subtitles/23456/download",
                    "filename": "decision-to-leave.en.srt",
                },
                {"alpha3": "eng"},
                {"cookies": "avistazx_session=expired"},
            )

        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, b"<html><form action='/login'>login</form></html>", {"content-type": "text/html"}
        )

        with self.assertRaises(PermissionError):
            provider.download(
                {
                    "download_url": "https://avistaz.to/subtitles/23456/download",
                    "filename": "decision-to-leave.en.srt",
                },
                {"alpha3": "eng"},
                {"cookies": "avistazx_session=expired"},
            )

    def test_download_uses_content_disposition_filename_for_direct_payload(self):
        provider = self.mod.AvistazProvider()
        body = b"[Script Info]\nTitle: Decision to Leave\n"

        def get_bytes(url, headers, cookies, timeout=30, allow_redirects=True):
            del url, headers, cookies, timeout
            self.assertFalse(allow_redirects)
            return self.mod.HttpResponse(
                200,
                body,
                {
                    "content-type": "application/octet-stream",
                    "content-disposition": 'attachment; filename="decision-to-leave.en.ass"',
                },
            )

        provider._http_get = get_bytes
        result = provider.download(
            {
                "download_url": "https://avistaz.to/subtitles/23456/download",
                "filename": "download",
                "release_info": "Decision to Leave 2022",
            },
            {"alpha3": "eng"},
            {"cookies": "avistazx_session=valid"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_type"], "text/x-ssa")

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

    def test_download_selects_archive_member_by_season_and_episode(self):
        provider = self.mod.AvistazProvider()
        body = _zip_body("Show.S01E02.srt")
        stream = io.BytesIO(body)
        with zipfile.ZipFile(stream, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Show.S02E02.srt", "correct")

        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True: self.mod.HttpResponse(
            200, stream.getvalue(), {"content-type": "application/zip"}
        )

        result = provider.download(
            {
                "download_url": "https://avistaz.to/subtitles/12345/download",
                "filename": "show.en.zip",
                "kind": "episode",
                "season": 2,
                "episode": 2,
            },
            {"alpha3": "eng"},
            {"cookies": "avistazx_session=valid"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"correct")


if __name__ == "__main__":
    unittest.main()
