import base64
import hashlib
import importlib.util
import socket
import urllib.error
import urllib.parse
import urllib.request
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "greeksubs"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "greeksubs_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOVIE_HTML = (FIXTURE_DIR / "greeksubs_movie_dune.html").read_bytes()
SERIES_HTML = (FIXTURE_DIR / "greeksubs_series_game_of_thrones.html").read_bytes()
EPISODE_HTML = (FIXTURE_DIR / "greeksubs_episode_game_of_thrones_s01e01.html").read_bytes()
DOWNLOAD_GATE_HTML = (FIXTURE_DIR / "greeksubs_download_gate.html").read_bytes()


class GreekSubsParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_subtitle_page_extracts_movie_rows(self):
        page = self.mod.parse_subtitle_page(MOVIE_HTML, "https://greeksubs.net/en/view/tt1160419")

        self.assertEqual(page["sec_code"], "8Iza7aiF9")
        self.assertEqual(page["title"], "Dune")
        self.assertEqual(page["rows"][0]["subtitle_id"], "Keylo-616ed4e09f037")
        self.assertEqual(page["rows"][0]["language"], "ell")
        self.assertEqual(page["rows"][0]["release"], "DUNE (2021)")
        self.assertEqual(page["rows"][0]["downloads"], 34117)
        self.assertEqual(page["rows"][0]["uploader"], "Magico Team")

    def test_parse_series_page_finds_matching_episode_link(self):
        episodes = self.mod.parse_episode_links(SERIES_HTML)

        self.assertEqual(
            episodes[(1, 1)]["url"],
            "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1",
        )
        self.assertEqual(episodes[(1, 1)]["episode_imdb_id"], "tt1480055")
        self.assertNotIn((1, 3), episodes)

    def test_extract_download_form_reads_post_fields(self):
        form = self.mod.extract_download_form(DOWNLOAD_GATE_HTML)

        self.assertEqual(
            form,
            {
                "langcode": "el",
                "uid": "tt1480055",
                "output": "game_of_thrones_-_01x01_-_winter_is_coming.hdtv.xvid-fever.srt",
                "dll": "1",
            },
        )


    def test_encoding_names_greek_legacy_codepage_only_for_greek_text(self):
        self.assertEqual(self.mod._encoding_for("Καλημέρα".encode("utf-8")), "utf-8")
        self.assertEqual(self.mod._encoding_for("Καλημέρα".encode("windows-1253")), "windows-1253")
        # 0xD2 is unassigned in both Greek codepages; 0x80 and 0x85 are only punctuation there.
        self.assertEqual(self.mod._encoding_for(b"caf\xd2"), "latin-1")
        self.assertEqual(self.mod._encoding_for(b"\x80 5 \x85"), "latin-1")

    def test_response_filename_reads_plain_and_rfc2231_content_disposition(self):
        def response(value):
            return self.mod.HttpResponse(200, b"", {"content-disposition": value})

        self.assertEqual(self.mod._response_filename(response('attachment; filename="dune (2021).srt"')), "dune (2021).srt")
        self.assertEqual(
            self.mod._response_filename(response("attachment; filename*=UTF-8''%CE%B1%CE%B2.srt")),
            "\u03b1\u03b2.srt",
        )
        self.assertEqual(self.mod._response_filename(response('attachment; filename="../x/evil.srt"')), "evil.srt")
        self.assertEqual(self.mod._response_filename(self.mod.HttpResponse(200, b"", {})), "")


class GreekSubsProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_returns_greek_subtitle_result(self):
        provider = self.mod.GreekSubsProvider()
        requests = []

        def stub(url, data=None, timeout=20, referer=None):
            del data, timeout, referer
            requests.append(url)
            if url == "https://greeksubs.net/en/view/tt1160419":
                return MOVIE_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_request = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "source": "WEBRip",
            },
            [{"alpha3": "ell", "alpha2": "el"}],
            {},
        )

        self.assertEqual(requests, ["https://greeksubs.net/en/view/tt1160419"])
        self.assertEqual(results[0]["provider"], "greeksubs")
        self.assertEqual(results[0]["language"]["alpha3"], "ell")
        self.assertEqual(results[0]["filename"], "greeksubs.DUNE.2021.el.srt")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://greeksubs.net/dll/Keylo-616ed4e09f037/0/8Iza7aiF9")

    def test_search_episode_fetches_series_page_then_episode_page(self):
        provider = self.mod.GreekSubsProvider()
        requests = []

        def stub(url, data=None, timeout=20, referer=None):
            del data, timeout, referer
            requests.append(url)
            if url == "https://greeksubs.net/en/view/tt0944947":
                return SERIES_HTML
            if url == "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1":
                return EPISODE_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_request = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "series_imdb_id": "tt0944947",
                "season": 1,
                "episode": 1,
            },
            [{"alpha3": "ell", "alpha2": "el"}],
            {},
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(results[0]["provider_payload"]["page_url"], requests[1])
        self.assertEqual(results[0]["release_info"], "Game_of_thrones_-_01x01_-_winter_is_coming.hdtv.xvid-fever")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_requires_greek_language_and_imdb_id(self):
        provider = self.mod.GreekSubsProvider()
        provider._http_request = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("network should not be called")
        )

        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Dune", "year": 2021, "imdb_id": "tt1160419"},
                [{"alpha3": "eng", "alpha2": "en"}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Dune", "year": 2021},
                [{"alpha3": "ell", "alpha2": "el"}],
                {},
            ),
            [],
        )

    def test_download_returns_subtitle_served_directly_by_the_gate_url(self):
        provider = self.mod.GreekSubsProvider()
        page_url = "https://greeksubs.net/en/view/tt1160419"
        download_url = "https://greeksubs.net/dll/Keylo-616ed4e09f037/0/8Iza7aiF9"
        subtitle = "1\r\n00:00:04,120 --> 00:00:08,690\r\nΤα όνειρα είναι μηνύματα.\r\n".encode("windows-1253")
        calls = []

        def stub(url, data=None, timeout=20, referer=None, allow_redirects=True):
            del timeout
            calls.append((url, data, referer, allow_redirects))
            return self.mod.HttpResponse(200, subtitle, _headers("application/octet-stream", 'attachment; filename="dune (2021).srt"'))

        provider._http_fetch = stub
        result = provider.download(
            {
                "provider": "greeksubs",
                "schema": 1,
                "download_url": download_url,
                "page_url": page_url,
                "subtitle_id": "Keylo-616ed4e09f037",
                "filename": "greeksubs.DUNE.2021.el.ass",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(calls, [(download_url, None, page_url, False)])
        self.assertEqual(decoded, subtitle.replace(b"\r\n", b"\n"))
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["encoding"], "windows-1253")
        self.assertEqual(decoded.decode(result["encoding"]).splitlines()[2], "Τα όνειρα είναι μηνύματα.")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertFalse(result["empty"])

    def test_download_posts_legacy_gate_fields_and_returns_subtitle_payload(self):
        provider = self.mod.GreekSubsProvider()
        subtitle = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nGreek line\r\n"
        calls = []

        def stub(url, data=None, timeout=20, referer=None, allow_redirects=True):
            del timeout
            calls.append((url, data, referer))
            if data is None:
                self.assertFalse(allow_redirects)
                self.assertEqual(referer, "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1")
                return self.mod.HttpResponse(200, DOWNLOAD_GATE_HTML, _headers("text/html; charset=UTF-8"))
            self.assertEqual(
                urllib.parse.parse_qs(data.decode("ascii")),
                {
                    "langcode": ["el"],
                    "uid": ["tt1480055"],
                    "output": ["game_of_thrones_-_01x01_-_winter_is_coming.hdtv.xvid-fever.srt"],
                    "dll": ["1"],
                },
            )
            return self.mod.HttpResponse(200, subtitle, _headers("application/octet-stream"))

        provider._http_fetch = stub
        result = provider.download(
            {
                "provider": "greeksubs",
                "schema": 1,
                "download_url": "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/QyjKcYY2K",
                "page_url": "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1",
                "filename": "game_of_thrones_-_01x01_-_winter_is_coming.hdtv.xvid-fever.srt",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nGreek line\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertEqual(result["encoding"], "utf-8")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertFalse(result["empty"])

    def test_download_rejects_missing_gate_form(self):
        provider = self.mod.GreekSubsProvider()
        provider._http_fetch = lambda *args, **kwargs: self.mod.HttpResponse(
            200, b"<html>expired token</html>", _headers("text/html")
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "greeksubs",
                    "schema": 1,
                    "download_url": "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/QyjKcYY2K",
                    "page_url": "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

    def test_download_refreshes_used_sec_code_from_matching_detail_page(self):
        provider = self.mod.GreekSubsProvider()
        page_url = "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1"
        stale_url = "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/stale-token"
        fresh_url = "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/dZCzW0PjQ"
        subtitle = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nGreek line\r\n"
        calls = []

        def stub(url, data=None, timeout=20, referer=None, allow_redirects=True):
            del timeout
            calls.append((url, data, referer, allow_redirects))
            if url == stale_url and data is None:
                # GreekSubs refuses a used or foreign-session token by redirecting home.
                return self.mod.HttpResponse(302, b"", _headers("text/html", location="https://greeksubs.net/"))
            if url == page_url and data is None:
                return self.mod.HttpResponse(200, EPISODE_HTML, _headers("text/html; charset=UTF-8"))
            if url == fresh_url and data is None:
                return self.mod.HttpResponse(200, subtitle, _headers("application/octet-stream", 'attachment; filename="got.srt"'))
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_fetch = stub
        result = provider.download(
            {
                "provider": "greeksubs",
                "schema": 1,
                "download_url": stale_url,
                "page_url": page_url,
                "subtitle_id": "kDCtQ-5a72ef1d3756e",
                "filename": "game-of-thrones.srt",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual([call[0] for call in calls], [stale_url, page_url, fresh_url])
        self.assertEqual(calls[0][2:], (page_url, False))
        self.assertIsNone(calls[1][2])
        self.assertEqual(calls[2][2:], (page_url, False))
        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nGreek line\n")

    def test_download_refreshes_expired_legacy_gate_from_matching_detail_page(self):
        provider = self.mod.GreekSubsProvider()
        page_url = "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1"
        stale_url = "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/stale-token"
        fresh_url = "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/dZCzW0PjQ"
        subtitle = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nGreek line\r\n"
        calls = []

        def stub(url, data=None, timeout=20, referer=None, allow_redirects=True):
            del timeout, allow_redirects
            calls.append((url, data, referer))
            if url == stale_url and data is None:
                return self.mod.HttpResponse(200, b"<html>expired token</html>", _headers("text/html"))
            if url == page_url and data is None:
                return self.mod.HttpResponse(200, EPISODE_HTML, _headers("text/html"))
            if url == fresh_url and data is None:
                return self.mod.HttpResponse(200, DOWNLOAD_GATE_HTML, _headers("text/html"))
            if url == fresh_url and data is not None:
                return self.mod.HttpResponse(200, subtitle, _headers("application/octet-stream"))
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_fetch = stub
        result = provider.download(
            {
                "provider": "greeksubs",
                "schema": 1,
                "download_url": stale_url,
                "page_url": page_url,
                "subtitle_id": "kDCtQ-5a72ef1d3756e",
                "filename": "game-of-thrones.srt",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {},
        )

        self.assertEqual([call[0] for call in calls], [stale_url, page_url, fresh_url, fresh_url])
        self.assertEqual(calls[3][2], fresh_url)
        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nGreek line\n")

    def test_download_gives_up_when_refreshed_token_is_refused_too(self):
        provider = self.mod.GreekSubsProvider()
        page_url = "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1"
        calls = []

        def stub(url, data=None, timeout=20, referer=None, allow_redirects=True):
            del data, timeout, referer, allow_redirects
            calls.append(url)
            if url == page_url:
                return self.mod.HttpResponse(200, EPISODE_HTML, _headers("text/html"))
            return self.mod.HttpResponse(302, b"", _headers("text/html", location="https://greeksubs.net/"))

        provider._http_fetch = stub
        with self.assertRaisesRegex(ValueError, "after a token refresh"):
            provider.download(
                {
                    "provider": "greeksubs",
                    "schema": 1,
                    "download_url": "https://greeksubs.net/dll/kDCtQ-5a72ef1d3756e/0/stale-token",
                    "page_url": page_url,
                    "subtitle_id": "kDCtQ-5a72ef1d3756e",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

        self.assertEqual(len(calls), 3)

    def test_expired_download_rejects_subtitle_id_missing_from_detail_page(self):
        provider = self.mod.GreekSubsProvider()
        page_url = "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1"
        stale_url = "https://greeksubs.net/dll/wrong-subtitle-id/0/stale-token"
        calls = []

        def stub(url, data=None, timeout=20, referer=None, allow_redirects=True):
            del data, timeout, referer, allow_redirects
            calls.append(url)
            if url == stale_url:
                return self.mod.HttpResponse(302, b"", _headers("text/html", location="https://greeksubs.net/"))
            if url == page_url:
                return self.mod.HttpResponse(200, EPISODE_HTML, _headers("text/html"))
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_fetch = stub
        with self.assertRaisesRegex(ValueError, "subtitle id"):
            provider.download(
                {
                    "provider": "greeksubs",
                    "schema": 1,
                    "download_url": stale_url,
                    "page_url": page_url,
                    "subtitle_id": "wrong-subtitle-id",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {},
            )

        self.assertEqual(calls, [stale_url, page_url])


def _headers(content_type, disposition=None, location=None):
    headers = {"Content-Type": content_type}
    if disposition:
        headers["Content-Disposition"] = disposition
    if location:
        headers["Location"] = location
    return headers


class _FakeResponse:
    def __init__(self, body):
        self._body = body
        self.headers = {}

    def getcode(self):
        return 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class GreekSubsTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.slept = []
        self._real_sleep = self.mod.time.sleep
        self.mod.time.sleep = lambda seconds: self.slept.append(seconds)

    def tearDown(self):
        self.mod.time.sleep = self._real_sleep

    def _http_error(self, code, headers=None):
        return urllib.error.HTTPError(
            "https://greeksubs.net/x", code, "boom", headers or {}, None
        )

    def test_retries_on_url_error_then_succeeds(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise urllib.error.URLError("connection reset")
            return _FakeResponse(b"ok")

        provider._opener.open = fake_open

        self.assertEqual(provider._http_request("https://greeksubs.net/x"), b"ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(self.slept), 2)

    def test_retries_on_503_then_succeeds(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise self._http_error(503)
            return _FakeResponse(b"ok")

        provider._opener.open = fake_open

        self.assertEqual(provider._http_request("https://greeksubs.net/x"), b"ok")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.slept), 1)

    def test_honors_retry_after_header_on_429(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise self._http_error(429, {"Retry-After": "5"})
            return _FakeResponse(b"ok")

        provider._opener.open = fake_open

        self.assertEqual(provider._http_request("https://greeksubs.net/x"), b"ok")
        self.assertEqual(self.slept, [5.0])

    def test_timeout_is_retried(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise socket.timeout("timed out")
            return _FakeResponse(b"ok")

        provider._opener.open = fake_open

        self.assertEqual(provider._http_request("https://greeksubs.net/x"), b"ok")
        self.assertEqual(len(attempts), 2)

    def test_does_not_retry_on_404(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(1)
            raise self._http_error(404)

        provider._opener.open = fake_open

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_request("https://greeksubs.net/x")
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.slept, [])

    def test_gives_up_after_max_attempts(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(1)
            raise urllib.error.URLError("dns failure")

        provider._opener.open = fake_open

        with self.assertRaises(urllib.error.URLError):
            provider._http_request("https://greeksubs.net/x")
        self.assertEqual(len(attempts), self.mod.HTTP_MAX_ATTEMPTS)
        self.assertEqual(len(self.slept), self.mod.HTTP_MAX_ATTEMPTS - 1)

    def test_gate_fetch_returns_redirect_unfollowed_without_retrying(self):
        provider = self.mod.GreekSubsProvider()
        attempts = []

        def fake_open(request, timeout=None):
            attempts.append(request.full_url)
            raise self._http_error(302, {"Location": "https://greeksubs.net/"})

        provider._no_redirect_opener.open = fake_open

        response = provider._http_fetch("https://greeksubs.net/dll/x/0/used", allow_redirects=False)
        self.assertEqual(
            (response.status, response.headers["Location"], response.body),
            (302, "https://greeksubs.net/", b""),
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.slept, [])

    def test_no_redirect_opener_does_not_allow_redirects(self):
        provider = self.mod.GreekSubsProvider()
        handler = next(
            h for h in provider._no_redirect_opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
        )
        self.assertIsInstance(handler, self.mod._NoRedirectHandler)
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "Found", {}, "https://greeksubs.net/")
        )
