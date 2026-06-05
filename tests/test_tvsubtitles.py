import base64
import hashlib
import importlib.util
import io
import socket
import unittest
import urllib.error
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "tvsubtitles"


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


SEARCH_HTML = b"""
<div class="left">
  <li><div><a href="/tvshow-1234.html">The Office (US) (2005-2013)</a></div></li>
  <li><div><a href="/tvshow-9999.html">The Office (2001-2003)</a></div></li>
</div>
"""


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

    def test_parse_show_suggestions_extracts_matching_show_id(self):
        rows = self.mod.parse_show_suggestions(SEARCH_HTML)

        self.assertEqual(rows[0]["show_id"], "1234")
        self.assertEqual(rows[0]["series"], "The Office")
        self.assertEqual(rows[0]["first_year"], 2005)

    def test_pick_show_id_matches_series_and_year(self):
        show_id = self.mod.pick_show_id(
            self.mod.parse_show_suggestions(SEARCH_HTML),
            series="The Office",
            year=2005,
        )

        self.assertEqual(show_id, "1234")

    def test_parse_episode_ids_extracts_episode_page_ids(self):
        episode_ids = self.mod.parse_episode_ids(SEASON_HTML)

        self.assertEqual(episode_ids, {1: "501", 2: "502"})

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
            ("POST", "https://www.tvsubtitles.net/search1.php"): SEARCH_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-1234-1.html"): SEASON_HTML,
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

        self.assertEqual(calls[0], ("POST", "https://www.tvsubtitles.net/search1.php", b"qs=The+Office"))
        self.assertEqual(results[0]["provider"], "tvsubtitles")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "7001")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_accepts_episode_lists_by_using_lowest_episode(self):
        provider = self.mod.TvSubtitlesProvider()
        responses = {
            ("POST", "https://www.tvsubtitles.net/search1.php"): SEARCH_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-1234-1.html"): SEASON_HTML,
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
            ("POST", "https://www.tvsubtitles.net/search1.php"): SEARCH_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-1234-1.html"): SEASON_HTML,
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
            ("POST", "https://www.tvsubtitles.net/search1.php"): SEARCH_HTML,
            ("GET", "https://www.tvsubtitles.net/tvshow-1234-1.html"): SEASON_HTML,
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

    def _patch_urlopen(self, outcomes):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            index = calls["count"]
            calls["count"] += 1
            outcome = outcomes[index]
            if isinstance(outcome, Exception):
                raise outcome
            return _FakeResponse(outcome)

        self.mod.urllib.request.urlopen = fake_urlopen
        return calls

    def test_retries_url_error_then_succeeds(self):
        calls = self._patch_urlopen(
            [urllib.error.URLError("connection reset"), b"OK"]
        )
        provider = self.mod.TvSubtitlesProvider()

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.slept), 1)

    def test_retries_503_twice_then_succeeds(self):
        calls = self._patch_urlopen(
            [_http_error(503), _http_error(503), b"OK"]
        )
        provider = self.mod.TvSubtitlesProvider()

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.slept), 2)

    def test_retries_socket_timeout_then_succeeds(self):
        calls = self._patch_urlopen([socket.timeout("slow"), b"OK"])
        provider = self.mod.TvSubtitlesProvider()

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(calls["count"], 2)

    def test_gives_up_after_three_transient_failures(self):
        calls = self._patch_urlopen(
            [
                urllib.error.URLError("down"),
                urllib.error.URLError("down"),
                urllib.error.URLError("down"),
            ]
        )
        provider = self.mod.TvSubtitlesProvider()

        with self.assertRaises(urllib.error.URLError):
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.slept), 2)

    def test_404_is_not_retried_and_propagates(self):
        calls = self._patch_urlopen([_http_error(404), b"OK"])
        provider = self.mod.TvSubtitlesProvider()

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.slept, [])

    def test_403_is_not_retried_and_propagates(self):
        calls = self._patch_urlopen([_http_error(403), b"OK"])
        provider = self.mod.TvSubtitlesProvider()

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(calls["count"], 1)

    def test_non_network_error_propagates_immediately(self):
        calls = self._patch_urlopen([ValueError("bad parse"), b"OK"])
        provider = self.mod.TvSubtitlesProvider()

        with self.assertRaises(ValueError):
            provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.slept, [])

    def test_429_honors_retry_after_header(self):
        self._patch_urlopen([_http_error(429, {"Retry-After": "2"}), b"OK"])
        provider = self.mod.TvSubtitlesProvider()

        body = provider._http_get("https://www.tvsubtitles.net/tvshow-1.html")

        self.assertEqual(body, b"OK")
        self.assertEqual(self.slept, [2.0])
