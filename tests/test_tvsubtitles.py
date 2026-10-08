import base64
import hashlib
import importlib.util
import io
import socket
import unittest
import urllib.error
import zipfile
from email.message import Message
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "tvsubtitles"
SHOW_INDEX_HTML = (ROOT / "tests" / "fixtures" / "tvsubtitles_shows.html").read_bytes()


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "tvsubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_files(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


SEASON_HTML = b"""
<table id="table5">
  <tr><td>1x01</td><td><a href="episode-501.html">Pilot</a></td></tr>
  <tr><td>1x02</td><td><a href="episode-502.html">Diversity Day</a></td></tr>
</table>
"""


EPISODE_HTML = b"""
<a href="/subtitle-7001.html">
  <div class="subtitlen">
    <h5><img src="/images/flags/en.gif" /> The.Office.US.S01E02.DVDRip.XviD-SAiNTS</h5>
    <p title="rip">DVDRip</p>
  </div>
</a>
<a href="/subtitle-7002.html">
  <div class="subtitlen">
    <h5><img src="/images/flags/br.gif" /> The.Office.US.S01E02.HDTV</h5>
    <p title="rip">HDTV</p>
  </div>
</a>
"""


EPISODE_HEADER_LANGUAGE_HTML = b"""
<div style="clear:both; padding-top:10px;"><span><b>English subtitles</b></span></div>
<a href="/subtitle-7001.html">
  <div title="Download subtitles" class="subtitlen">
    <h5>The.Office.US.S01E02.DVDRip.XviD-SAiNTS</h5>
    <p title="rip">DVDRip</p>
  </div>
</a>
"""


class TvSubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_show_index_extracts_show_ids_and_first_years(self):
        rows = self.mod.parse_show_index(SHOW_INDEX_HTML)

        self.assertEqual(
            rows,
            [
                {"show_id": "58", "series": "The Office", "first_year": 2005, "title": "The Office"},
                {"show_id": "3111", "series": "The Office", "first_year": 2024, "title": "The Office"},
                {"show_id": "576", "series": "The Office (UK)", "first_year": 2001, "title": "The Office (UK)"},
            ],
        )

    def test_pick_show_id_matches_series_year_and_region_alias(self):
        suggestions = self.mod.parse_show_index(SHOW_INDEX_HTML)

        self.assertEqual(self.mod.pick_show_id(suggestions, "The Office", 2005), "58")
        self.assertEqual(self.mod.pick_show_id(suggestions, "The Office", 2024), "3111")
        self.assertEqual(self.mod.pick_show_id(suggestions, "The Office", 2001), "576")
        self.assertEqual(self.mod.pick_show_id(suggestions, "The Office (UK)"), "576")
        self.assertEqual(self.mod.pick_show_id(suggestions, "The Office (UK)", 2005), None)

    def test_parse_episode_ids_extracts_episode_page_ids(self):
        episode_ids = self.mod.parse_episode_ids(SEASON_HTML)

        self.assertEqual(episode_ids, {1: "501", 2: "502"})

    def test_explicit_region_does_not_match_other_region(self):
        rows = [{"show_id": "1", "series": "Example (US)", "first_year": 2020}]
        self.assertIsNone(self.mod.pick_show_id(rows, "Example (UK)", 2020))
        self.assertEqual(self.mod.pick_show_id(rows, "Example", 2020), "1")
        unqualified = [{"show_id": "2", "series": "Example", "first_year": 2020}]
        self.assertEqual(self.mod.pick_show_id(unqualified, "Example (UK)", 2020), "2")

    def test_parse_episode_subtitles_extracts_language_and_release_rows(self):
        rows = self.mod.parse_episode_subtitles(
            EPISODE_HTML,
            series="The Office",
            season=1,
            episode=2,
            year=2005,
        )

        self.assertEqual(rows[0]["subtitle_id"], "7001")
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["release"], "The.Office.US.S01E02.DVDRip.XviD-SAiNTS")
        self.assertEqual(rows[0]["rip"], "DVDRip")
        self.assertEqual(rows[1]["language"], "por")
        self.assertEqual(rows[1]["country"], "BR")

    def test_parse_episode_subtitles_uses_language_section_header(self):
        rows = self.mod.parse_episode_subtitles(
            EPISODE_HEADER_LANGUAGE_HTML,
            series="The Office",
            season=1,
            episode=2,
            year=2005,
        )

        self.assertEqual(rows[0]["subtitle_id"], "7001")
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["release"], "The.Office.US.S01E02.DVDRip.XviD-SAiNTS")


class TvSubtitlesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_skips_movies(self):
        provider = self.mod.TvSubtitlesProvider()
        provider._http_get = lambda url, timeout=10, referer=None: self.fail(url)

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_finds_requested_episode_subtitle(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = []
        responses = {
            ("GET", "https://www.tvsubtitles.net/tvshows.html"): SHOW_INDEX_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-58-1.html"): SEASON_HTML,
            ("GET", "https://www.tvsubtitles.net/episode-502.html"): EPISODE_HTML,
        }

        def stub(url, data=None, timeout=10, referer=None):
            del timeout, referer
            method = "POST" if data is not None else "GET"
            calls.append((method, url, data))
            if (method, url) not in responses:
                raise AssertionError(f"unexpected request: {method} {url}")
            return responses[(method, url)]

        provider._http_request = stub
        results = provider.search(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": 2, "year": 2005},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0], ("GET", "https://www.tvsubtitles.net/tvshows.html", None))
        self.assertEqual(results[0]["provider"], "tvsubtitles")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "7001")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_reuses_show_index_for_later_queries(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = []
        responses = {
            "https://www.tvsubtitles.net/tvshows.html": SHOW_INDEX_HTML,
            "https://www.tvsubtitles.net/tvshow-58-1.html": SEASON_HTML,
            "https://www.tvsubtitles.net/episode-502.html": EPISODE_HTML,
        }

        def stub(url, data=None, timeout=10, referer=None):
            del data, timeout, referer
            calls.append(url)
            return responses[url]

        provider._http_request = stub
        video = {"kind": "episode", "series": "The Office", "season": 1, "episode": 2, "year": 2005}
        language = [{"alpha3": "eng", "alpha2": "en"}]
        provider.search(video, language, {})
        provider.search(video, language, {})

        self.assertEqual(calls.count("https://www.tvsubtitles.net/tvshows.html"), 1)

    def test_show_index_cache_expires_after_one_hour(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = []
        provider._http_get = lambda url, timeout=10, referer=None: calls.append(url) or SHOW_INDEX_HTML
        times = [10.0, 10.0, 3609.0, 3611.0, 3611.0]

        with mock.patch.object(self.mod.time, "monotonic", side_effect=times):
            provider._get_show_index({})
            provider._get_show_index({})
            provider._get_show_index({})

        self.assertEqual(
            calls,
            [
                "https://www.tvsubtitles.net/tvshows.html",
                "https://www.tvsubtitles.net/tvshows.html",
            ],
        )

    def test_invalid_show_index_is_retried_on_next_search(self):
        provider = self.mod.TvSubtitlesProvider()
        with mock.patch.object(provider, "_http_get", side_effect=[b"<html>Checking your browser</html>", SHOW_INDEX_HTML]) as request:
            self.assertEqual(provider._get_show_index({}), b"<html>Checking your browser</html>")
            self.assertEqual(provider._get_show_index({}), SHOW_INDEX_HTML)
            self.assertEqual(provider._get_show_index({}), SHOW_INDEX_HTML)
        self.assertEqual(request.call_count, 2)

    def test_search_accepts_episode_lists_by_using_lowest_episode(self):
        provider = self.mod.TvSubtitlesProvider()
        responses = {
            ("GET", "https://www.tvsubtitles.net/tvshows.html"): SHOW_INDEX_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-58-1.html"): SEASON_HTML,
            ("GET", "https://www.tvsubtitles.net/episode-501.html"): EPISODE_HTML,
        }

        def stub(url, data=None, timeout=10, referer=None):
            method = "POST" if data is not None else "GET"
            return responses[(method, url)]

        provider._http_request = stub
        results = provider.search(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": [1, 2], "year": 2005},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(results[0]["provider_payload"]["episode"], 1)

    def test_search_returns_country_alpha2_for_brazilian_portuguese(self):
        provider = self.mod.TvSubtitlesProvider()
        responses = {
            ("GET", "https://www.tvsubtitles.net/tvshows.html"): SHOW_INDEX_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-58-1.html"): SEASON_HTML,
            ("GET", "https://www.tvsubtitles.net/episode-502.html"): EPISODE_HTML,
        }

        def stub(url, data=None, timeout=10, referer=None):
            del timeout, referer
            method = "POST" if data is not None else "GET"
            return responses[(method, url)]

        provider._http_request = stub
        results = provider.search(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": 2, "year": 2005},
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")

    def test_search_keeps_plain_portuguese_separate_from_brazilian_portuguese(self):
        provider = self.mod.TvSubtitlesProvider()
        responses = {
            ("GET", "https://www.tvsubtitles.net/tvshows.html"): SHOW_INDEX_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-58-1.html"): SEASON_HTML,
            ("GET", "https://www.tvsubtitles.net/episode-502.html"): EPISODE_HTML,
        }

        def stub(url, data=None, timeout=10, referer=None):
            del timeout, referer
            method = "POST" if data is not None else "GET"
            return responses[(method, url)]

        provider._http_request = stub
        results = provider.search(
            {"kind": "episode", "series": "The Office", "season": 1, "episode": 2, "year": 2005},
            [{"alpha3": "por", "alpha2": "pt"}],
            {},
        )

        self.assertEqual(results, [])

    def test_download_follows_script_redirect_and_returns_zip_archive(self):
        provider = self.mod.TvSubtitlesProvider()
        zip_body = _zip_files(
            {"The.Office.S01E02.en.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nLine\r\n"}
        )
        responses = {
            "https://www.tvsubtitles.net/download-7001.html": (
                b"<script>var s1 = 'download/'; var s2 = 'subtitle-7001.zip';</script>"
            ),
            "https://www.tvsubtitles.net/download/subtitle-7001.zip": zip_body,
        }

        provider._http_request = lambda url, data=None, timeout=10, referer=None: responses[url]
        result = provider.download(
            {
                "provider": "tvsubtitles",
                "schema": 1,
                "subtitle_id": "7001",
                "filename": "tvsubtitles.the-office.s01e02.en.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(zip_body).hexdigest())
        self.assertEqual(result["member"], "The.Office.S01E02.en.srt")
        self.assertNotIn("encoding", result)
        self.assertNotIn("content_b64", result)

    def test_download_quotes_script_redirect_paths_with_spaces(self):
        provider = self.mod.TvSubtitlesProvider()
        zip_body = _zip_files(
            {"The.Office.S01E02.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nLine\n"}
        )
        responses = {
            "https://www.tvsubtitles.net/download-7001.html": (
                b"<script>var s1 = 'files/'; var s2 = 'The Office_1x02_en.zip';</script>"
            ),
            "https://www.tvsubtitles.net/files/The%20Office_1x02_en.zip": zip_body,
        }

        provider._http_request = lambda url, data=None, timeout=10, referer=None: responses[url]
        result = provider.download(
            {"provider": "tvsubtitles", "schema": 1, "subtitle_id": "7001"},
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["member"], "The.Office.S01E02.en.srt")

    def test_download_rejects_empty_body(self):
        provider = self.mod.TvSubtitlesProvider()
        provider._http_request = lambda url, data=None, timeout=10, referer=None: b""

        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "tvsubtitles", "schema": 1, "subtitle_id": "7001"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_rejects_non_archive_body(self):
        provider = self.mod.TvSubtitlesProvider()
        provider._http_request = lambda url, data=None, timeout=10, referer=None: (
            b"<html><body>Not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "tvsubtitles", "schema": 1, "subtitle_id": "7001"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_rejects_archives_with_multiple_subtitle_files(self):
        provider = self.mod.TvSubtitlesProvider()
        body = _zip_files(
            {
                "one.srt": b"1\n00:00:01,000 --> 00:00:02,000\nOne\n",
                "two.srt": b"1\n00:00:01,000 --> 00:00:02,000\nTwo\n",
            }
        )
        provider._http_request = lambda url, data=None, timeout=10, referer=None: body

        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "tvsubtitles", "schema": 1, "subtitle_id": "7001"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class TvSubtitlesBrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_requests_keep_generated_identity_headers_referer_and_cookie_session(self):
        received = []

        class OfflineResponse:
            def __init__(self, url, set_cookie=None):
                self.url = url
                self.headers = Message()
                if set_cookie:
                    self.headers.add_header("Set-Cookie", set_cookie)

            def info(self):
                return self.headers

            def geturl(self):
                return self.url

            def read(self):
                return b"ok"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        generate_patcher = mock.patch.object(
            self.mod.ua_generator,
            "generate",
            wraps=self.mod.ua_generator.generate,
        )
        generate = generate_patcher.start()
        self.addCleanup(generate_patcher.stop)

        provider = self.mod.TvSubtitlesProvider()
        cookie_handler = next(
            handler
            for handler in provider._opener.handlers
            if isinstance(handler, self.mod.urllib.request.HTTPCookieProcessor)
        )
        self.assertIs(cookie_handler.cookiejar, provider._cookie_jar)

        def offline_open(request, timeout):
            self.assertEqual(timeout, self.mod.HTTP_TIMEOUT_SECONDS)
            request = cookie_handler.http_request(request)
            received.append(request)
            set_cookie = (
                "tvsubtitles_session=active; Path=/"
                if request.full_url.endswith("/tvshows.html")
                else None
            )
            response = OfflineResponse(request.full_url, set_cookie=set_cookie)
            cookie_handler.http_response(request, response)
            return response

        provider._opener.open = offline_open
        base_url = self.mod.BASE_URL
        self.assertEqual(provider._http_get(f"{base_url}/tvshows.html"), b"ok")
        self.assertEqual(
            provider._http_get(
                f"{base_url}/tvshow-58-1.html",
                referer=f"{base_url}/tvshows.html",
            ),
            b"ok",
        )

        generate.assert_called_once()
        generation = generate.call_args.kwargs
        self.assertEqual(generation["device"], "desktop")
        self.assertEqual(generation["platform"], "linux")
        self.assertEqual(generation["browser"], "firefox")
        self.assertTrue(generation["options"].latest_versions)
        self.assertEqual(len(received), 2)

        def header(request, name):
            return next(
                value
                for key, value in request.header_items()
                if key.lower() == name.lower()
            )

        user_agents = [header(request, "User-Agent") for request in received]
        self.assertEqual(user_agents[0], user_agents[1])
        self.assertIn("Firefox/", user_agents[0])
        self.assertIn("Linux", user_agents[0])
        self.assertNotIn("BazarrProviderHub", user_agents[0])
        for request in received:
            self.assertEqual(
                header(request, "Accept"),
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            self.assertEqual(header(request, "Accept-Language"), "en-US,en;q=0.9")
            self.assertEqual(header(request, "X-Requested-With"), "XMLHttpRequest")
        self.assertEqual(header(received[0], "Referer"), f"{base_url}/")
        self.assertEqual(
            header(received[1], "Referer"), f"{base_url}/tvshows.html"
        )
        self.assertEqual(header(received[1], "Cookie"), "tvsubtitles_session=active")


def _http_error(code, headers=None):
    import email.message

    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError(
        url="https://www.tvsubtitles.net/", code=code, msg="boom", hdrs=message, fp=None
    )


class TvSubtitlesTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.slept = []
        # Make backoff a no-op and observable so the loop runs instantly.
        self.mod.time.sleep = lambda seconds: self.slept.append(seconds)

    def _patch_urlopen(self, provider, outcomes):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            index = calls["count"]
            calls["count"] += 1
            outcome = outcomes[index]
            if isinstance(outcome, Exception):
                raise outcome
            return _FakeResponse(outcome)

        provider._opener.open = fake_urlopen
        return calls

    def test_retries_url_error_then_succeeds(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(
            provider, [urllib.error.URLError("connection reset"), b"OK"]
        )

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.slept), 1)

    def test_retries_503_twice_then_succeeds(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(
            provider, [_http_error(503), _http_error(503), b"OK"]
        )

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.slept), 2)

    def test_retries_socket_timeout_then_succeeds(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(provider, [socket.timeout("slow"), b"OK"])

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(calls["count"], 2)

    def test_gives_up_after_three_transient_failures(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(
            provider,
            [
                urllib.error.URLError("down"),
                urllib.error.URLError("down"),
                urllib.error.URLError("down"),
            ]
        )

        with self.assertRaises(urllib.error.URLError):
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.slept), 2)

    def test_404_is_not_retried_and_propagates(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(provider, [_http_error(404), b"OK"])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.slept, [])

    def test_403_is_not_retried_and_propagates(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(provider, [_http_error(403), b"OK"])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(calls["count"], 1)

    def test_non_network_error_propagates_immediately(self):
        provider = self.mod.TvSubtitlesProvider()
        calls = self._patch_urlopen(provider, [ValueError("bad parse"), b"OK"])

        with self.assertRaises(ValueError):
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.slept, [])

    def test_429_honors_retry_after_header(self):
        provider = self.mod.TvSubtitlesProvider()
        self._patch_urlopen(provider, [_http_error(429, {"Retry-After": "2"}), b"OK"])

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(self.slept, [2.0])
