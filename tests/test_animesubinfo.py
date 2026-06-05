import base64
import hashlib
import importlib.util
import io
import unittest
import urllib.error
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "animesubinfo"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "animesubinfo_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KIMETSU_HTML = (FIXTURE_DIR / "animesubinfo_search_kimetsu_ep01.html").read_bytes()
KIMETSU_REFRESHED_HTML = (FIXTURE_DIR / "animesubinfo_search_kimetsu_ep01_refreshed.html").read_bytes()
AKIRA_HTML = (FIXTURE_DIR / "animesubinfo_search_akira.html").read_bytes()


def _zip_body(filename, content):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, content)
    return buffer.getvalue()


class AnimeSubInfoParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_metadata_and_download_form(self):
        rows = self.mod.parse_search_results(KIMETSU_HTML)

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["subtitle_id"], "68055")
        self.assertEqual(first["title_org"], "Kimetsu no Yaiba ep01")
        self.assertEqual(first["title_eng"], "Demon Slayer ep01")
        self.assertEqual(first["title_alt"], "Kimetsu no Yaiba ep01")
        self.assertEqual(first["author"], "Askara")
        self.assertEqual(first["format_type"], "Advanced SSA")
        self.assertEqual(first["size"], "11kB")
        self.assertEqual(first["download_hash"], "session-hash-1")
        self.assertEqual(first["download_count"], 3836)
        self.assertEqual(first["download_url"], "http://animesub.info/sciagnij.php")
        self.assertEqual(first["release_groups"], ["Horrible Subs"])

    def test_parse_search_results_extracts_comma_release_groups(self):
        rows = self.mod.parse_search_results(KIMETSU_HTML)

        self.assertEqual(rows[1]["release_groups"], ["SubsPlease", "Erai-raws"])

    def test_build_search_strategies_matches_episode_and_movie_flow(self):
        episode = {
            "kind": "episode",
            "series": "Kimetsu no Yaiba",
            "season": 1,
            "episode": 1,
            "alternative_series": ["Demon Slayer", "Blade of Demon Destruction", "Ignored"],
        }
        movie = {"kind": "movie", "title": "Akira", "alternative_titles": ["AKIRA"]}

        self.assertEqual(
            self.mod.build_search_strategies(episode),
            [
                ("org", "Kimetsu no Yaiba ep01"),
                ("en", "Kimetsu no Yaiba ep01"),
                ("pl", "Kimetsu no Yaiba ep01"),
                ("en", "Demon Slayer ep01"),
                ("en", "Demon Slayer"),
                ("en", "Blade of Demon Destruction ep01"),
                ("en", "Blade of Demon Destruction"),
            ],
        )
        self.assertEqual(
            self.mod.build_search_strategies(movie),
            [("org", "Akira"), ("en", "Akira"), ("pl", "Akira"), ("en", "AKIRA"), ("org", "AKIRA")],
        )


class AnimeSubInfoSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_episode_candidate_from_first_episode_strategy(self):
        provider = self.mod.AnimeSubInfoProvider()
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            called.append(url)
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["szukane"], ["Kimetsu no Yaiba ep01"])
            self.assertEqual(query["pTitle"], ["org"])
            return KIMETSU_HTML

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Kimetsu no Yaiba",
                "season": 1,
                "episode": 1,
                "release_group": "Horrible Subs",
            },
            [{"alpha3": "pol", "alpha2": "pl"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(called), 1)
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["provider"], "animesubinfo")
        self.assertEqual(first["language"]["alpha3"], "pol")
        self.assertIn("series", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertIn("release_group", first["matches"])
        self.assertIn("audio_codec", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], "68055")
        self.assertEqual(first["provider_payload"]["download_hash"], "session-hash-1")
        self.assertEqual(first["provider_payload"]["search_query"], "Kimetsu no Yaiba ep01")
        self.assertEqual(first["provider_payload"]["title_type"], "org")

    def test_derive_matches_does_not_assume_every_episode_row_matches(self):
        video = {
            "kind": "episode",
            "series": "Kimetsu no Yaiba",
            "season": 1,
            "episode": 1,
        }

        wrong_episode = {
            "title_org": "Kimetsu no Yaiba ep02",
            "title_eng": "Demon Slayer ep02",
            "title_alt": "",
            "format_type": "Advanced SSA",
            "release_groups": [],
        }
        season_only = {
            "title_org": "Kimetsu no Yaiba season 1",
            "title_eng": "Demon Slayer season 1",
            "title_alt": "",
            "format_type": "Advanced SSA",
            "release_groups": [],
        }

        self.assertNotIn("episode", self.mod.derive_matches(video, wrong_episode))
        self.assertNotIn("episode", self.mod.derive_matches(video, season_only))

    def test_search_returns_movie_candidate_with_year_match(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._http_get = lambda url, timeout=15, referer=None: AKIRA_HTML

        results = provider.search(
            {"kind": "movie", "title": "Akira", "year": 1988},
            [{"alpha3": "pol", "alpha2": "pl"}],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("movie", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "9785")

    def test_search_rejects_unsupported_language_or_media(self):
        provider = self.mod.AnimeSubInfoProvider()

        self.assertEqual(
            provider.search(
                {"kind": "episode", "series": "Kimetsu no Yaiba", "season": 1, "episode": 1},
                [{"alpha3": "eng", "alpha2": "en"}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "series", "series": "Kimetsu no Yaiba"},
                [{"alpha3": "pol", "alpha2": "pl"}],
                {},
            ),
            [],
        )


class AnimeSubInfoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_posts_hidden_fields_and_returns_raw_zip_for_host(self):
        provider = self.mod.AnimeSubInfoProvider()
        posts = []
        body = _zip_body(
            "[HorribleSubs] Kimetsu no Yaiba - 01 [1080p].ass",
            "[Script Info]\r\nTitle: ok\r\n",
        )
        provider._http_post = lambda url, data, timeout=15, referer=None: posts.append((url, data, referer)) or body

        result = provider.download(
            {
                "provider": "animesubinfo",
                "schema": 1,
                "subtitle_id": "68055",
                "download_hash": "session-hash-1",
                "download_url": "http://animesub.info/sciagnij.php",
                "search_url": "http://animesub.info/szukaj.php?szukane=Kimetsu+no+Yaiba+ep01&pTitle=org&pSortuj=pobrn",
                "filename": "animesubinfo.68055.zip",
                "episode": 1,
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(posts[0][0], "http://animesub.info/sciagnij.php")
        self.assertEqual(posts[0][1]["id"], "68055")
        self.assertEqual(posts[0][1]["sh"], "session-hash-1")
        self.assertEqual(posts[0][1]["single_file"], "Pobierz napisy")
        # Archive mode: the worker hands the raw archive bytes back untouched and only
        # names the member it selected from the cheap stdlib listing.
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "[HorribleSubs] Kimetsu no Yaiba - 01 [1080p].ass")
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_refreshes_hash_after_security_error(self):
        provider = self.mod.AnimeSubInfoProvider()
        posts = []
        provider._http_get = lambda url, timeout=15, referer=None: KIMETSU_REFRESHED_HTML
        archive = _zip_body("Kimetsu.no.Yaiba.ep01.srt", "1\r\n00:00:01,000 --> 00:00:02,000\r\nOK\r\n")

        def post(url, data, timeout=15, referer=None):
            del timeout, referer
            posts.append(data["sh"])
            if len(posts) == 1:
                return b"<html><body>Blad zabezpieczen.</body></html>"
            return archive

        provider._http_post = post
        result = provider.download(
            {
                "provider": "animesubinfo",
                "schema": 1,
                "subtitle_id": "68055",
                "download_hash": "expired-hash",
                "download_url": "http://animesub.info/sciagnij.php",
                "search_query": "Kimetsu no Yaiba ep01",
                "title_type": "org",
                "filename": "animesubinfo.68055.zip",
                "episode": 1,
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(posts, ["expired-hash", "session-hash-refreshed"])
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["member"], "Kimetsu.no.Yaiba.ep01.srt")

    def test_download_returns_direct_subtitle_with_normalized_line_endings(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nOK\r\n"

        result = provider.download(
            {
                "provider": "animesubinfo",
                "schema": 1,
                "subtitle_id": "raw",
                "download_hash": "hash",
                "download_url": "http://animesub.info/sciagnij.php",
                "filename": "raw.srt",
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nOK\n")
        self.assertEqual(result["format"], "srt")

    def test_download_detects_direct_ass_body_despite_synthetic_zip_name(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: (
            b"\xef\xbb\xbf[Script Info]\r\nTitle: direct\r\n[Events]\r\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,OK\r\n"
        )

        result = provider.download(
            {
                "provider": "animesubinfo",
                "schema": 1,
                "subtitle_id": "raw-ass",
                "download_hash": "hash",
                "download_url": "http://animesub.info/sciagnij.php",
                "filename": "animesubinfo.direct.raw-ass.pl.zip",
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(result["format"], "ass")
        self.assertTrue(base64.b64decode(result["content_b64"]).startswith(b"\xef\xbb\xbf[Script Info]\n"))

    def test_download_direct_body_does_not_ship_worker_encoding(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nOK\r\n"

        result = provider.download(
            {
                "provider": "animesubinfo",
                "schema": 1,
                "subtitle_id": "raw",
                "download_hash": "hash",
                "download_url": "http://animesub.info/sciagnij.php",
                "filename": "raw.srt",
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertNotIn("encoding", result)
        self.assertNotIn("archive_b64", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: b"   "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "animesubinfo",
                    "schema": 1,
                    "subtitle_id": "raw",
                    "download_hash": "hash",
                    "download_url": "http://animesub.info/sciagnij.php",
                    "filename": "raw.srt",
                },
                {"alpha3": "pol", "alpha2": "pl"},
                {},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: b"<html><body>Error page</body></html>"

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "animesubinfo",
                    "schema": 1,
                    "subtitle_id": "raw",
                    "download_hash": "hash",
                    "download_url": "http://animesub.info/sciagnij.php",
                    "filename": "raw.srt",
                },
                {"alpha3": "pol", "alpha2": "pl"},
                {},
            )

    def test_archive_rejects_wrong_season_episode_marker(self):
        with self.assertRaises(ValueError):
            self.mod.select_subtitle_file(["Show.S01E02.srt"], {"episode": 1})

        selected = self.mod.select_subtitle_file(
            ["Show.S01E02.srt", "Show.S01E01.ass"],
            {"episode": 1},
        )
        self.assertEqual(selected, "Show.S01E01.ass")


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class _RecordingOpener:
    """Stub urllib opener that yields a queued sequence of outcomes per open() call."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def open(self, request, timeout=None):
        del request, timeout
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)


class AnimeSubInfoTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.sleeps = []
        self._orig_sleep = self.mod.time.sleep
        self.mod.time.sleep = lambda seconds: self.sleeps.append(seconds)
        self.addCleanup(self._restore_sleep)

    def _restore_sleep(self):
        self.mod.time.sleep = self._orig_sleep

    def _http_error(self, code, headers=None):
        error = urllib.error.HTTPError(
            "http://animesub.info/x", code, "boom", headers or {}, io.BytesIO(b"")
        )
        self.addCleanup(error.close)
        return error

    def test_get_retries_url_error_then_succeeds(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener(
            [urllib.error.URLError("connection reset"), b"<html>ok</html>"]
        )

        body = provider._http_get("http://animesub.info/szukaj.php")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(provider._opener.calls, 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_get_retries_503_twice_then_succeeds(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener(
            [self._http_error(503), self._http_error(503), b"<html>ok</html>"]
        )

        body = provider._http_get("http://animesub.info/szukaj.php")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(provider._opener.calls, 3)
        # Exponential backoff: 0.5 then 1.0 seconds.
        self.assertEqual(self.sleeps, [0.5, 1.0])

    def test_post_retries_timeout_then_succeeds(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener([TimeoutError(), b"archive-bytes"])

        body = provider._http_post(
            "http://animesub.info/sciagnij.php", {"id": "1", "sh": "h"}
        )

        self.assertEqual(body, b"archive-bytes")
        self.assertEqual(provider._opener.calls, 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_429_honors_retry_after_header(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener(
            [self._http_error(429, {"Retry-After": "3"}), b"<html>ok</html>"]
        )

        body = provider._http_get("http://animesub.info/szukaj.php")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(self.sleeps, [3.0])

    def test_404_is_not_retried_and_propagates_first_call(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener([self._http_error(404)])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("http://animesub.info/szukaj.php")

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(provider._opener.calls, 1)
        self.assertEqual(self.sleeps, [])

    def test_persistent_503_exhausts_retries_and_raises(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener(
            [self._http_error(503), self._http_error(503), self._http_error(503)]
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("http://animesub.info/szukaj.php")

        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(provider._opener.calls, 3)
        self.assertEqual(self.sleeps, [0.5, 1.0])

    def test_persistent_url_error_exhausts_retries_and_raises(self):
        provider = self.mod.AnimeSubInfoProvider()
        provider._opener = _RecordingOpener(
            [
                urllib.error.URLError("reset"),
                urllib.error.URLError("reset"),
                urllib.error.URLError("reset"),
            ]
        )

        with self.assertRaises(urllib.error.URLError):
            provider._http_get("http://animesub.info/szukaj.php")

        self.assertEqual(provider._opener.calls, 3)


if __name__ == "__main__":
    unittest.main()
