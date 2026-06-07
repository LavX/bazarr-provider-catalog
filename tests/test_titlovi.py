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


def _rar_body(payload=b"chernobyl-pack"):
    return b"Rar!\x1a\x07\x00" + payload


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

    def test_download_returns_raw_zip_archive_with_episode(self):
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

        # Host-side extraction: the worker hands back the raw archive untouched.
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_extract_download_returns_raw_rar_archive(self):
        body = _rar_body()

        result = self.mod.extract_download(body, {"episode": 3, "language": "srp"})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 3)
        self.assertNotIn("encoding", result)

    def test_extract_download_episode_is_none_for_movies(self):
        body = _zip_body({"Dune.2021.srt": b"1\n00:00:01,000 --> 00:00:02,000\nMovie\n"})

        result = self.mod.extract_download(body, {"language": "eng", "filename": "dune.zip"})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertIsNone(result["episode"])

    def test_extract_download_pins_cyrillic_member_when_both_scripts_present(self):
        body = _zip_body(
            {
                "Chernobyl.S01E01.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nLatin\n",
                "Chernobyl.S01E01.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nCyrillic\n",
            }
        )

        result = self.mod.extract_download(
            body, {"language": "srp", "script": "Cyrl", "season": 1, "episode": 1}
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["member"], "Chernobyl.S01E01.cyr.srt")
        self.assertNotIn("episode", result)

    def test_extract_download_pins_latin_member_for_latin_script(self):
        body = _zip_body(
            {
                "Chernobyl.S01E01.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nCyrillic\n",
                "Chernobyl.S01E01.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nLatin\n",
            }
        )

        # Latin Serbian carries no `script` key; its absence selects the Latin member.
        result = self.mod.extract_download(
            body, {"language": "srp", "season": 1, "episode": 1}
        )

        self.assertEqual(result["member"], "Chernobyl.S01E01.lat.srt")
        self.assertNotIn("episode", result)

    def test_extract_download_pins_script_member_for_correct_episode_in_pack(self):
        body = _zip_body(
            {
                "Chernobyl.S01E01.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE1 latin\n",
                "Chernobyl.S01E01.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE1 cyr\n",
                "Chernobyl.S01E02.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE2 latin\n",
                "Chernobyl.S01E02.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE2 cyr\n",
            }
        )

        # Both scripts and several episodes: the host can do neither axis, so the provider
        # resolves episode and script together and pins exactly one member.
        result = self.mod.extract_download(
            body, {"language": "srp", "script": "Cyrl", "season": 1, "episode": 2}
        )

        self.assertEqual(result["member"], "Chernobyl.S01E02.cyr.srt")
        self.assertNotIn("episode", result)

    def test_extract_download_single_script_pack_defers_to_episode(self):
        body = _zip_body(
            {
                "Chernobyl.S01E01.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE1\n",
                "Chernobyl.S01E02.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE2\n",
            }
        )

        # Only one script present: nothing for us to disambiguate, host picks by episode.
        result = self.mod.extract_download(
            body, {"language": "srp", "season": 1, "episode": 1}
        )

        self.assertEqual(result["episode"], 1)
        self.assertNotIn("member", result)

    def test_extract_download_does_not_misread_latin_circle_as_cyrillic(self):
        # A Latin release whose title contains "circle" must not be bucketed as Cyrillic;
        # the script must be matched as a delimited token, not a substring.
        body = _zip_body(
            {
                "The.Circle.S01E01.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nLatin\n",
                "The.Circle.S01E01.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nCyrillic\n",
            }
        )

        cyr = self.mod.extract_download(
            body, {"language": "srp", "script": "Cyrl", "season": 1, "episode": 1}
        )
        self.assertEqual(cyr["member"], "The.Circle.S01E01.cyr.srt")

        lat = self.mod.extract_download(
            body, {"language": "srp", "season": 1, "episode": 1}
        )
        self.assertEqual(lat["member"], "The.Circle.S01E01.lat.srt")

    def test_extract_download_ignores_macosx_sidecar(self):
        # An AppleDouble sidecar (binary, listed first) must not be pinned as a member.
        body = _zip_body(
            {
                "__MACOSX/._Chernobyl.cyr.srt": b"\x00\x05\x16\x07",
                "Chernobyl.lat.srt": b"1\n00:00:01,000 --> 00:00:02,000\nLatin\n",
                "Chernobyl.cyr.srt": b"1\n00:00:01,000 --> 00:00:02,000\nCyrillic\n",
            }
        )

        result = self.mod.extract_download(body, {"language": "srp", "script": "Cyrl"})

        self.assertEqual(result["member"], "Chernobyl.cyr.srt")

    def test_extract_download_accepts_direct_subtitle_body(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nDirect subtitle\r\n"

        result = self.mod.extract_download(body, {"filename": "titlovi.1001.eng.srt"})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nDirect subtitle\n")
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)

    def test_extract_download_rejects_empty_body(self):
        for body in (b"", b"   "):
            with self.assertRaisesRegex(ValueError, "supported subtitle file"):
                self.mod.extract_download(body, {})

    def test_extract_download_rejects_html_error_body(self):
        body = b"<!DOCTYPE html><html><body>Not found</body></html>"

        with self.assertRaisesRegex(ValueError, "supported subtitle file"):
            self.mod.extract_download(body, {})

    def test_manifest_advertises_serbian_cyrillic_variant(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())
        languages = manifest.get("languages") or []

        def _alpha3_script(code):
            alpha2_map = {"sr": "srp"}
            if "-" in code:
                base, script = code.split("-", 1)
                return alpha2_map.get(base.lower(), base.lower()), script
            return alpha2_map.get(code.lower(), code.lower()), None

        advertised = {_alpha3_script(code) for code in languages}
        # Without the manifest fix the Cyrillic search/bundle path the provider
        # implements is unreachable because the catalog never routes srp-Cyrl to it.
        self.assertIn(("srp", "Cyrl"), advertised)
        self.assertIn(("srp", None), advertised)

    def test_search_routes_serbian_cyrillic_request_to_cirilica(self):
        provider = self.mod.TitloviProvider()
        provider._http_post = lambda url, params=None, headers=None, timeout=10: self.mod.HttpResponse(200, _fixture("titlovi_login.json"), {})

        cyrillic_results = {
            "PagesAvailable": 1,
            "SubtitleResults": [
                {
                    "Id": 3001,
                    "Title": "Dune",
                    "Lang": "Cirilica",
                    "Link": "https://kodi.titlovi.com/download/dune-cyr.zip",
                    "Release": "Dune.2021.1080p.WEB.H264-MEMENTO",
                    "Year": 2021,
                    "Rating": 4.5,
                    "DownloadCount": 12,
                }
            ],
        }

        def get(url, params=None, headers=None, timeout=10):
            del url, headers, timeout
            self.assertEqual(params["lang"], "Cirilica")
            return self.mod.HttpResponse(200, json.dumps(cyrillic_results).encode("utf-8"), {})

        provider._http_get = get
        results = provider.search(
            _video("titlovi_video_dune_2021.json"),
            [{"alpha3": "srp", "script": "Cyrl"}],
            {"username": "user", "password": "pass"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["3001"])
        self.assertEqual(results[0]["language"]["alpha3"], "srp")
        self.assertEqual(results[0]["language"]["script"], "Cyrl")

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
        original_sleep = self.mod.time.sleep

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
        self.mod.time.sleep = lambda *_args, **_kwargs: None
        try:
            response = provider._http_get("https://kodi.titlovi.com/api/subtitles/search")
        finally:
            self.mod.urllib.request.urlopen = original_urlopen
            self.mod.time.sleep = original_sleep

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.body, b"limited")


class _FakeResponse:
    def __init__(self, status, body, headers=None):
        self._status = status
        self._body = body
        self.headers = _FakeHeaders(headers or {})

    def getcode(self):
        return self._status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHeaders:
    def __init__(self, mapping):
        self._mapping = dict(mapping)

    def items(self):
        return self._mapping.items()


class TitloviTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.sleeps = []
        self.mod.time.sleep = lambda seconds=0, *a, **k: self.sleeps.append(seconds)

    def test_http_get_retries_transient_urlerror_then_succeeds(self):
        provider = self.mod.TitloviProvider()
        calls = []

        def fake_urlopen(request, timeout=10):
            del request, timeout
            calls.append(1)
            if len(calls) < 3:
                raise self.mod.urllib.error.URLError("connection reset")
            return _FakeResponse(200, b'{"ok": true}', {})

        self.mod.urllib.request.urlopen = fake_urlopen
        response = provider._http_get("https://kodi.titlovi.com/api/subtitles/search")

        self.assertEqual(len(calls), 3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"ok": true}')
        # Two backoff sleeps before the third, successful attempt.
        self.assertEqual(len(self.sleeps), 2)

    def test_http_get_retries_503_then_succeeds(self):
        provider = self.mod.TitloviProvider()
        calls = []

        def fake_urlopen(request, timeout=10):
            del timeout
            calls.append(1)
            if len(calls) == 1:
                raise self.mod.urllib.error.HTTPError(
                    request.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"down")
                )
            return _FakeResponse(200, b'{"SubtitleResults": []}', {})

        self.mod.urllib.request.urlopen = fake_urlopen
        response = provider._http_get("https://kodi.titlovi.com/api/subtitles/search")

        self.assertEqual(len(calls), 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.sleeps), 1)

    def test_http_post_retries_transient_then_succeeds(self):
        provider = self.mod.TitloviProvider()
        calls = []

        def fake_urlopen(request, timeout=10):
            del request, timeout
            calls.append(1)
            if len(calls) < 2:
                raise self.mod.socket.timeout("timed out")
            return _FakeResponse(200, _fixture("titlovi_login.json"), {})

        self.mod.urllib.request.urlopen = fake_urlopen
        response = provider._http_post(self.mod.TOKEN_URL, params={"username": "u"})

        self.assertEqual(len(calls), 2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.sleeps), 1)

    def test_http_get_does_not_retry_404(self):
        provider = self.mod.TitloviProvider()
        calls = []

        def fake_urlopen(request, timeout=10):
            del timeout
            calls.append(1)
            raise self.mod.urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"missing")
            )

        self.mod.urllib.request.urlopen = fake_urlopen
        response = provider._http_get("https://kodi.titlovi.com/api/subtitles/search")

        # 4xx other than 429 is converted on the first attempt and never retried.
        self.assertEqual(len(calls), 1)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.body, b"missing")
        self.assertEqual(self.sleeps, [])

    def test_http_get_propagates_urlerror_after_exhausting_attempts(self):
        provider = self.mod.TitloviProvider()
        calls = []

        def fake_urlopen(request, timeout=10):
            del request, timeout
            calls.append(1)
            raise self.mod.urllib.error.URLError("dns failure")

        self.mod.urllib.request.urlopen = fake_urlopen
        with self.assertRaises(self.mod.urllib.error.URLError):
            provider._http_get("https://kodi.titlovi.com/api/subtitles/search")

        self.assertEqual(len(calls), self.mod.HTTP_MAX_ATTEMPTS)
        self.assertEqual(len(self.sleeps), self.mod.HTTP_MAX_ATTEMPTS - 1)

    def test_http_get_honors_retry_after_on_429(self):
        provider = self.mod.TitloviProvider()
        calls = []

        def fake_urlopen(request, timeout=10):
            del timeout
            calls.append(1)
            if len(calls) == 1:
                raise self.mod.urllib.error.HTTPError(
                    request.full_url, 429, "Too Many Requests", {"Retry-After": "2"}, io.BytesIO(b"slow")
                )
            return _FakeResponse(200, b"{}", {})

        self.mod.urllib.request.urlopen = fake_urlopen
        response = provider._http_get("https://kodi.titlovi.com/api/subtitles/search")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.sleeps, [2.0])


if __name__ == "__main__":
    unittest.main()
