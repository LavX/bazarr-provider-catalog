import base64
import hashlib
import importlib.util
import io
import json
import socket
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
        self.assertTrue(self.mod._framerate_equal(23.978, 23.976))
        self.assertTrue(self.mod._framerate_equal(23.98, 24.0))

    def test_search_reauthenticates_after_session_cookie_expires(self):
        provider = self.mod.TitulkyProvider()
        provider._logged_in = True  # reused worker that still believes it is logged in
        posts = []
        serial_gets = []

        def post(url, data=None, headers=None, timeout=30):
            del headers, timeout
            posts.append((url, dict(data or {})))
            return self.mod.HttpResponse(
                302, b"", {"Location": "/?msg_type=i&msg=ok", "set-cookie": "sid=fresh"}, url
            )

        def get(url, headers=None, timeout=30, allow_redirects=False):
            del headers, timeout, allow_redirects
            if "action=serial" not in url:
                raise AssertionError(url)
            serial_gets.append(url)
            if len(serial_gets) == 1:
                # Expired session: Titulky bounced the browse request to a message page.
                return self.mod.HttpResponse(
                    200,
                    b"<html>session expired</html>",
                    {},
                    "https://premium.titulky.com/?msg_type=e&msg=login",
                )
            return self.mod.HttpResponse(200, _fixture("titulky_browse_dune.html"), {}, url)

        provider._http_post = post
        provider._http_get = get
        results = provider.search(
            _video("titulky_video_dune_2021.json"),
            [{"alpha3": "ces", "alpha2": "cs"}],
            {"username": "user", "password": "pass", "approved_only": False, "skip_wrong_fps": False},
        )

        self.assertEqual(len(posts), 1)  # re-login happened exactly once
        self.assertEqual(posts[0][1], {"LoginName": "user", "LoginPassword": "pass"})
        self.assertEqual(len(serial_gets), 2)  # retried the browse request after re-auth
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "101")

    def test_store_cookies_preserves_duplicate_set_cookie_headers(self):
        provider = self.mod.TitulkyProvider()

        provider._store_cookies(
            [
                ("Set-Cookie", "sid=session-token; Path=/"),
                ("Set-Cookie", "premium=premium-token; Path=/"),
            ]
        )

        self.assertEqual(provider._cookies["sid"], "session-token")
        self.assertEqual(provider._cookies["premium"], "premium-token")

    def test_download_zip_archive_returns_raw_archive_for_host(self):
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
                "download_url": "https://premium.titulky.com/download.php?id=201",
                "page_link": "https://premium.titulky.com/?action=detail&id=201",
                "filename": "titulky.201.cs.zip",
                "episode": 3,
            },
            {"alpha3": "ces"},
            {"username": "user", "password": "pass", "approved_only": False, "skip_wrong_fps": False},
        )

        # Host-side extraction: the worker forwards the raw archive untouched.
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 3)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_build_download_payload_rar_archive_returns_raw_archive_for_host(self):
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 64

        result = self.mod.build_download_payload(body, {"filename": "titulky.101.cs.zip", "episode": 7})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 7)
        self.assertNotIn("content_b64", result)

    def test_build_download_payload_archive_episode_is_none_for_movie(self):
        body = _zip_body({"Dune.2021.cs.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nCzech line\r\n"})

        result = self.mod.build_download_payload(body, {"filename": "titulky.101.cs.zip", "episode": None})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertIsNone(result["episode"])

    def test_build_download_payload_pins_member_by_release_info_overlap(self):
        # Two releases for the same episode in one zip: the host's episode-only pick cannot
        # tell them apart, so the worker pins the member overlapping the scored release_info.
        body = _zip_body(
            {
                "Chernobyl.S01E01.720p.WEB-DL.x264-NTb.srt": b"web line\n",
                "Chernobyl.S01E01.1080p.BluRay.x264-AMIABLE.srt": b"bluray line\n",
            }
        )

        result = self.mod.build_download_payload(
            body,
            {
                "filename": "titulky.201.sk.zip",
                "release_info": "Chernobyl S01E01 720p WEB-DL x264 NTb",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertEqual(result["member"], "Chernobyl.S01E01.720p.WEB-DL.x264-NTb.srt")
        self.assertNotIn("episode", result)
        self.assertNotIn("content_b64", result)

    def test_build_download_payload_prefers_non_forced_member(self):
        # A forced sidecar must never outrank the main subtitle even when both share the
        # episode and the release tokens.
        body = _zip_body(
            {
                "Chernobyl.S01E01.WEB-DL.forced.srt": b"forced line\n",
                "Chernobyl.S01E01.WEB-DL.srt": b"full line\n",
            }
        )

        result = self.mod.build_download_payload(
            body,
            {
                "filename": "titulky.201.sk.zip",
                "release_info": "Chernobyl S01E01 WEB-DL",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertEqual(result["member"], "Chernobyl.S01E01.WEB-DL.srt")

    def test_build_download_payload_defers_when_release_overlap_ties(self):
        # Both members overlap the release_info equally: no unique winner, so defer to the
        # host's episode selection instead of guessing.
        body = _zip_body(
            {
                "Chernobyl.S01E01.WEB-DL.GROUPA.srt": b"a\n",
                "Chernobyl.S01E01.WEB-DL.GROUPB.srt": b"b\n",
            }
        )

        result = self.mod.build_download_payload(
            body,
            {
                "filename": "titulky.201.sk.zip",
                "release_info": "Chernobyl S01E01 WEB-DL",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 1)

    def test_build_download_payload_defers_when_requested_episode_absent(self):
        # Episode markers present but none matches the requested episode: pinning a member
        # from another episode would hard-fail the host download, so defer.
        body = _zip_body(
            {
                "Chernobyl.S01E02.720p.WEB-DL.srt": b"e02\n",
                "Chernobyl.S01E03.720p.WEB-DL.srt": b"e03\n",
            }
        )

        result = self.mod.build_download_payload(
            body,
            {
                "filename": "titulky.201.sk.zip",
                "release_info": "Chernobyl S01E01 720p WEB-DL",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 1)

    def test_build_download_payload_ignores_sidecars_and_dirs_when_pinning(self):
        # macOS sidecars, dotfiles, and directory entries must not count as members; the
        # real subtitle stays the only candidate and the host episode pick lands there.
        body = _zip_body(
            {
                "subs/": b"",
                "__MACOSX/._Chernobyl.S01E01.720p.WEB-DL.srt": b"junk\n",
                ".DS_Store": b"junk\n",
                "subs/Chernobyl.S01E01.720p.WEB-DL.srt": b"real\n",
            }
        )

        result = self.mod.build_download_payload(
            body,
            {
                "filename": "titulky.201.sk.zip",
                "release_info": "Chernobyl S01E01 720p WEB-DL",
                "season": 1,
                "episode": 1,
            },
        )

        # Only one real subtitle member survives filtering, so the worker defers by episode.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 1)

    def test_build_download_payload_episode_token_does_not_match_resolution(self):
        # S07E20 absent: a "720" episode code must not match "720p" and "264" must not match
        # "x264", so the worker defers rather than mispicking on a resolution/codec token.
        body = _zip_body(
            {
                "Show.S07E19.720p.x264-A.srt": b"a\n",
                "Show.S07E21.720p.x264-B.srt": b"b\n",
            }
        )

        result = self.mod.build_download_payload(
            body,
            {
                "filename": "titulky.201.sk.zip",
                "release_info": "Show S07E20 720p x264",
                "season": 7,
                "episode": 20,
            },
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 20)

    def test_build_download_payload_accepts_direct_subtitle_body(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nCzech line\r\n"

        result = self.mod.build_download_payload(body, {"filename": "titulky.101.cs.srt"})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nCzech line\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        # The host detects the encoding via chardet; the worker must not guess.
        self.assertNotIn("encoding", result)

    def test_build_download_payload_rejects_empty_body(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            self.mod.build_download_payload(b"", {"filename": "titulky.101.cs.zip"})
        with self.assertRaisesRegex(ValueError, "empty"):
            self.mod.build_download_payload(b"   \n  ", {"filename": "titulky.101.cs.zip"})

    def test_build_download_payload_rejects_html_error_page(self):
        body = b"<!DOCTYPE html><html><body>Limit exceeded</body></html>"

        with self.assertRaisesRegex(ValueError, "did not return a supported subtitle"):
            self.mod.build_download_payload(body, {"filename": "limit.zip"})

    def test_search_stores_episode_and_season_in_payload(self):
        provider = self.mod.TitulkyProvider()
        provider._http_post = lambda url, data=None, headers=None, timeout=30: self.mod.HttpResponse(
            302, b"", {"Location": "/?msg_type=i&msg=ok"}, url
        )

        def get(url, headers=None, timeout=30, allow_redirects=False):
            del headers, timeout, allow_redirects
            if "action=serial" in url:
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

        payload = results[0]["provider_payload"]
        self.assertEqual(payload["episode"], 1)
        self.assertEqual(payload["season"], 1)

    def test_http_get_converts_urllib_http_error_to_response(self):
        provider = self.mod.TitulkyProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        original_sleep = self.mod.time.sleep
        calls = []

        class FailingOpener:
            def open(self, request, timeout=30):
                del request, timeout
                calls.append(1)
                raise self_mod.urllib.error.HTTPError(
                    "https://premium.titulky.com/download.php?id=101",
                    429,
                    "Too Many Requests",
                    {},
                    io.BytesIO(b"limited"),
                )

        self_mod = self.mod
        self.mod.urllib.request.build_opener = lambda *args: FailingOpener()
        self.mod.time.sleep = lambda *args, **kwargs: None
        try:
            response = provider._http_get("https://premium.titulky.com/download.php?id=101")
        finally:
            self.mod.urllib.request.build_opener = original_build_opener
            self.mod.time.sleep = original_sleep

        # 429 is transient: retried up to RETRY_MAX_ATTEMPTS, then the final
        # response is converted to an HttpResponse (the 429->error mapping is
        # preserved for the caller to handle).
        self.assertEqual(len(calls), self.mod.RETRY_MAX_ATTEMPTS)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.body, b"limited")

    def test_http_get_retries_transient_url_error_then_succeeds(self):
        provider = self.mod.TitulkyProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        original_sleep = self.mod.time.sleep
        attempts = []
        sleeps = []

        class _Resp:
            def __init__(self):
                self._body = b"ok-body"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return self._body

            @property
            def headers(self):
                return {}

            def geturl(self):
                return "https://premium.titulky.com/?action=serial"

        class FlakyOpener:
            def open(self, request, timeout=30):
                del request, timeout
                attempts.append(1)
                if len(attempts) == 1:
                    raise self_mod.urllib.error.URLError("connection reset by peer")
                if len(attempts) == 2:
                    raise socket.timeout("timed out")
                return _Resp()

        self_mod = self.mod
        self.mod.urllib.request.build_opener = lambda *args: FlakyOpener()
        self.mod.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            response = provider._http_get("https://premium.titulky.com/?action=serial")
        finally:
            self.mod.urllib.request.build_opener = original_build_opener
            self.mod.time.sleep = original_sleep

        # Two transient failures, recovered on the third attempt.
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"ok-body")

    def test_http_get_retries_503_then_succeeds(self):
        provider = self.mod.TitulkyProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        original_sleep = self.mod.time.sleep
        attempts = []

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return b"recovered"

            @property
            def headers(self):
                return {}

            def geturl(self):
                return "https://premium.titulky.com/?action=serial"

        class FlakyOpener:
            def open(self, request, timeout=30):
                del request, timeout
                attempts.append(1)
                if len(attempts) == 1:
                    raise self_mod.urllib.error.HTTPError(
                        "https://premium.titulky.com/?action=serial",
                        503,
                        "Service Unavailable",
                        {},
                        io.BytesIO(b"down"),
                    )
                return _Resp()

        self_mod = self.mod
        self.mod.urllib.request.build_opener = lambda *args: FlakyOpener()
        self.mod.time.sleep = lambda *args, **kwargs: None
        try:
            response = provider._http_get("https://premium.titulky.com/?action=serial")
        finally:
            self.mod.urllib.request.build_opener = original_build_opener
            self.mod.time.sleep = original_sleep

        self.assertEqual(len(attempts), 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"recovered")

    def test_http_get_does_not_retry_404(self):
        provider = self.mod.TitulkyProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        original_sleep = self.mod.time.sleep
        attempts = []
        sleeps = []

        class FailingOpener:
            def open(self, request, timeout=30):
                del request, timeout
                attempts.append(1)
                raise self_mod.urllib.error.HTTPError(
                    "https://premium.titulky.com/?action=serial",
                    404,
                    "Not Found",
                    {},
                    io.BytesIO(b"missing"),
                )

        self_mod = self.mod
        self.mod.urllib.request.build_opener = lambda *args: FailingOpener()
        self.mod.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            response = provider._http_get("https://premium.titulky.com/?action=serial")
        finally:
            self.mod.urllib.request.build_opener = original_build_opener
            self.mod.time.sleep = original_sleep

        # 4xx (other than 429) is not transient: a single attempt, no backoff,
        # converted to a response that the caller maps to an error.
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(response.status_code, 404)

    def test_http_get_does_not_retry_url_error_after_max_attempts(self):
        provider = self.mod.TitulkyProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        original_sleep = self.mod.time.sleep
        attempts = []

        class FailingOpener:
            def open(self, request, timeout=30):
                del request, timeout
                attempts.append(1)
                raise self_mod.urllib.error.URLError("name or service not known")

        self_mod = self.mod
        self.mod.urllib.request.build_opener = lambda *args: FailingOpener()
        self.mod.time.sleep = lambda *args, **kwargs: None
        try:
            with self.assertRaises(self.mod.urllib.error.URLError):
                provider._http_get("https://premium.titulky.com/?action=serial")
        finally:
            self.mod.urllib.request.build_opener = original_build_opener
            self.mod.time.sleep = original_sleep

        # A persistent transient error is retried up to the cap, then re-raised
        # unchanged so the provider surfaces the same failure it does today.
        self.assertEqual(len(attempts), self.mod.RETRY_MAX_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
