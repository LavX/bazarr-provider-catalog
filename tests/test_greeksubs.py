import base64
import hashlib
import importlib.util
import socket
import urllib.error
import urllib.parse
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

    def test_download_posts_gate_fields_and_returns_subtitle_payload(self):
        provider = self.mod.GreekSubsProvider()
        subtitle = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nGreek line\r\n"
        calls = []

        def stub(url, data=None, timeout=20, referer=None):
            del timeout
            calls.append((url, data, referer))
            if data is None:
                self.assertEqual(referer, "https://greeksubs.net/en/view/tt1480055/subtitle-for-game-of-thrones-winter-is-coming-season-1-episode-1")
                return DOWNLOAD_GATE_HTML
            self.assertEqual(
                urllib.parse.parse_qs(data.decode("ascii")),
                {
                    "langcode": ["el"],
                    "uid": ["tt1480055"],
                    "output": ["game_of_thrones_-_01x01_-_winter_is_coming.hdtv.xvid-fever.srt"],
                    "dll": ["1"],
                },
            )
            return subtitle

        provider._http_request = stub
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
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertFalse(result["empty"])

    def test_download_rejects_missing_gate_form(self):
        provider = self.mod.GreekSubsProvider()
        provider._http_request = lambda *args, **kwargs: b"<html>expired token</html>"

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


class _FakeResponse:
    def __init__(self, body):
        self._body = body

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
