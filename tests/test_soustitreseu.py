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
PROVIDER_DIR = ROOT / "providers" / "soustitreseu"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "soustitreseu_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_GOT_HTML = (FIXTURE_DIR / "soustitreseu_search_game_of_thrones.html").read_bytes()
SERIES_GOT_HTML = (FIXTURE_DIR / "soustitreseu_series_game_of_thrones.html").read_bytes()
SEARCH_DUNE_HTML = (FIXTURE_DIR / "soustitreseu_search_dune.html").read_bytes()
FILM_DUNE_HTML = (FIXTURE_DIR / "soustitreseu_film_dune.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SoustitreseuParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_media_rows(self):
        rows = self.mod.parse_search_results(SEARCH_GOT_HTML)

        self.assertEqual(rows[0]["media_type"], "series")
        self.assertTrue(rows[0]["exact"])
        self.assertEqual(rows[0]["title"], "Game Of Thrones")
        self.assertEqual(rows[0]["url"], "https://www.sous-titres.eu/series/game_of_thrones.html")
        self.assertEqual(rows[1]["media_type"], "film")
        self.assertEqual(rows[1]["year"], 2014)

    def test_parse_series_archive_rows_extracts_episode_and_languages(self):
        rows = self.mod.parse_archive_rows(
            SERIES_GOT_HTML,
            "https://www.sous-titres.eu/series/game_of_thrones.html",
            "series",
        )

        self.assertEqual(rows[0]["season"], 1)
        self.assertEqual(rows[0]["episode"], 1)
        self.assertEqual(rows[0]["filename"], "Game.Of.Thrones.1x01.ENFR.FBK.zip")
        self.assertEqual(rows[0]["languages"], ["eng", "fra"])
        self.assertIn("FBK", rows[0]["release_info"])
        self.assertEqual(rows[1]["season"], 1)
        self.assertIsNone(rows[1]["episode"])

    def test_parse_film_archive_rows_extracts_movie_downloads(self):
        rows = self.mod.parse_archive_rows(
            FILM_DUNE_HTML,
            "https://www.sous-titres.eu/films/dune_part_one.html",
            "film",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["filename"], "Dune.Part.One.(2021).Z2.WEB.zip")
        self.assertEqual(rows[1]["languages"], ["fra"])
        self.assertEqual(
            rows[1]["url"],
            "https://www.sous-titres.eu/films/download/krbse0p1duwc8oi/Dune.Part.One.%282021%29.Z2.WEB.zip",
        )

    def test_archive_languages_from_vo_vf_filenames(self):
        self.assertEqual(
            self.mod._languages_from_archive("Game.Of.Thrones.1x01.VO.FBK.zip", "<img src='img/flag.jpg' />"),
            ["eng"],
        )
        self.assertEqual(
            self.mod._languages_from_archive("Game.Of.Thrones.1x01.VF.FBK.zip", "<img src='img/flag.jpg' />"),
            ["fra"],
        )

    def test_parse_archive_rows_detects_vo_language_without_flag_alt(self):
        rows = self.mod.parse_archive_rows(
            """
            <a class="subList download" href="download/abc/Game.Of.Thrones.1x01.VO.zip">
              <span class="filenameSerie">Game.Of.Thrones.1x01.VO.zip</span>
              <span class="episodeNum">1 x 01</span>
              <span class="lang"><img src="img/flag.jpg" /></span>
            </a>
            """,
            "https://www.sous-titres.eu/series/game_of_thrones.html",
            "series",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["languages"], ["eng"])

    def test_parse_archive_rows_accepts_sublist_before_href(self):
        rows = self.mod.parse_archive_rows(
            """
            <a class="subList download" href="download/abc/Game.Of.Thrones.1x01.ENFR.zip">
              <span class="filenameSerie">Game.Of.Thrones.1x01.ENFR.zip</span>
              <span class="episodeNum">1 x 01</span>
              <img title="en" />
            </a>
            """,
            "https://www.sous-titres.eu/series/game_of_thrones.html",
            "series",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["url"],
            "https://www.sous-titres.eu/series/download/abc/Game.Of.Thrones.1x01.ENFR.zip",
        )


class SoustitreseuProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_episode_returns_requested_languages(self):
        provider = self.mod.SoustitreseuProvider()
        calls = []
        responses = {
            "https://www.sous-titres.eu/search.html?q=Game+of+Thrones": SEARCH_GOT_HTML,
            "https://www.sous-titres.eu/series/game_of_thrones.html": SERIES_GOT_HTML,
        }

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            return responses[url]

        provider._http_get = get_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "release_group": "FBK",
            },
            [{"alpha3": "fra", "alpha2": "fr"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"eng", "fra"})
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(calls[-1], "https://www.sous-titres.eu/series/game_of_thrones.html")

    def test_search_movie_returns_matching_film_archive(self):
        provider = self.mod.SoustitreseuProvider()
        provider._http_get = lambda url, timeout=15, referer=None: (
            SEARCH_DUNE_HTML if "search.html" in url else FILM_DUNE_HTML
        )

        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune: Part One",
                "year": 2021,
                "source": "WEB",
            },
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "fra")
        self.assertEqual(results[0]["provider_payload"]["media_type"], "film")
        self.assertIn("title", results[0]["matches"])

    def test_search_movie_drops_unrelated_fallback_rows(self):
        provider = self.mod.SoustitreseuProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search.html" in url:
                return SEARCH_DUNE_HTML
            raise AssertionError(f"unexpected detail request: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {"kind": "movie", "title": "Interstellar", "year": 2014},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertEqual(results, [])
        self.assertEqual(calls, ["https://www.sous-titres.eu/search.html?q=Interstellar"])

    def test_search_movie_rejects_explicit_year_mismatch(self):
        provider = self.mod.SoustitreseuProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search.html" in url:
                return SEARCH_DUNE_HTML
            if url == "https://www.sous-titres.eu/films/dune.html":
                return b"<html><body></body></html>"
            raise AssertionError(f"unexpected detail request: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 1984},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertEqual(results, [])
        self.assertNotIn(
            "https://www.sous-titres.eu/films/dune_part_one.html",
            calls,
        )

    def test_search_ignores_unsupported_or_incomplete_requests(self):
        provider = self.mod.SoustitreseuProvider()

        self.assertEqual(provider.search({"kind": "episode", "series": "Game of Thrones"}, [{"alpha3": "fra"}], {}), [])
        self.assertEqual(provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "deu"}], {}), [])

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.101.ctu.720p.VF.NoTAG.srt": "french subtitle",
                "Game.Of.Thrones.101.ctu.720p.VO.NoTAG.srt": "english subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/1f7d3i5ypv2bv0m/Game.Of.Thrones.1x01.ENFR.FBK.zip",
                "filename": "Game.Of.Thrones.1x01.ENFR.FBK.zip",
                "media_type": "series",
                "season": 1,
                "episode": 1,
                "release_info": "Game.Of.Thrones.1x01.ENFR.FBK.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 1)
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", content)
        self.assertNotIn("member", content)
        self.assertNotIn("encoding", content)

    def test_download_rar_archive_returns_raw_archive_for_host(self):
        provider = self.mod.SoustitreseuProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/1f7d3i5ypv2bv0m/Game.Of.Thrones.S01.ENFR.rar",
                "filename": "Game.Of.Thrones.S01.ENFR.rar",
                "media_type": "series",
                "season": 1,
                "episode": 7,
                "release_info": "Game.Of.Thrones.S01.ENFR.rar",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 7)
        self.assertNotIn("content_b64", content)

    def test_download_pins_language_member_when_archive_mixes_languages(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.101.ctu.720p.VF.NoTAG.srt": "french subtitle",
                "Game.Of.Thrones.101.ctu.720p.VO.NoTAG.srt": "english subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/x/Game.Of.Thrones.1x01.ENFR.FBK.zip",
                "filename": "Game.Of.Thrones.1x01.ENFR.FBK.zip",
                "media_type": "series",
                "season": 1,
                "episode": 1,
                "language": "fra",
                "release_info": "Game.Of.Thrones.1x01.ENFR.FBK.zip",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        # The archive carries both VO (English) and VF (French); a French request must
        # pin the VF member rather than let the host stream the English one.
        self.assertEqual(content["member"], "Game.Of.Thrones.101.ctu.720p.VF.NoTAG.srt")
        self.assertNotIn("episode", content)
        self.assertNotIn("content_b64", content)

    def test_download_pins_language_episode_member_in_season_pack(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.S01E01.VF.srt": "fr e1",
                "Game.Of.Thrones.S01E01.VO.srt": "en e1",
                "Game.Of.Thrones.S01E02.VF.srt": "fr e2",
                "Game.Of.Thrones.S01E02.VO.srt": "en e2",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/x/Game.Of.Thrones.S01.ENFR.zip",
                "filename": "Game.Of.Thrones.S01.ENFR.zip",
                "media_type": "series",
                "season": 1,
                "episode": 2,
                "language": "eng",
                "release_info": "Game.Of.Thrones.S01.ENFR.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        # Both languages and both episodes present: resolve language and episode together.
        self.assertEqual(content["member"], "Game.Of.Thrones.S01E02.VO.srt")
        self.assertNotIn("episode", content)

    def test_download_single_language_archive_defers_to_host(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.S01E01.VF.srt": "fr e1",
                "Game.Of.Thrones.S01E02.VF.srt": "fr e2",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/x/Game.Of.Thrones.S01.FR.zip",
                "filename": "Game.Of.Thrones.S01.FR.zip",
                "media_type": "series",
                "season": 1,
                "episode": 2,
                "language": "fra",
                "release_info": "Game.Of.Thrones.S01.FR.zip",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        # Only one language present: nothing to disambiguate, let the host pick by episode.
        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 2)

    def test_download_nxnn_episode_does_not_substring_match(self):
        # A mixed-language pack whose members use the 1xNN form: a request for episode 2
        # must NOT pin the "1x20" (episode 20) member via a "1x2" substring; defer instead.
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Game.Of.Thrones.1x20.VF.srt": "fr e20",
                "Game.Of.Thrones.1x20.VO.srt": "en e20",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/x/Game.Of.Thrones.S01.ENFR.zip",
                "filename": "Game.Of.Thrones.S01.ENFR.zip",
                "media_type": "series",
                "season": 1,
                "episode": 2,
                "language": "fra",
                "release_info": "Game.Of.Thrones.S01.ENFR.zip",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 2)

    def test_download_does_not_read_resolution_as_episode(self):
        # S07E20 yields episode code "720", which must not match the "720p" resolution in a
        # wrong-episode member. The requested episode is absent, so defer to the host.
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Show.S03E05.720p.VF.srt": "fr wrong episode",
                "Show.S03E05.720p.VO.srt": "en wrong episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/x/Show.S07.ENFR.zip",
                "filename": "Show.S07.ENFR.zip",
                "media_type": "series",
                "season": 7,
                "episode": 20,
                "language": "fra",
                "release_info": "Show.S07.ENFR.zip",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        self.assertNotIn("member", content)
        self.assertEqual(content["episode"], 20)

    def test_download_does_not_mislabel_french_word_as_english(self):
        # "Asterix.en.Bretagne" is a French (VF) release; the bare ".en." token must not
        # tag it English, so an English request pins the real VO member.
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "Asterix.en.Bretagne.VF.srt": "french",
                "Asterix.in.Britain.VO.srt": "english",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/films/download/x/Asterix.ENFR.zip",
                "filename": "Asterix.ENFR.zip",
                "media_type": "film",
                "season": None,
                "episode": None,
                "language": "eng",
                "release_info": "Asterix.ENFR.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(content["member"], "Asterix.in.Britain.VO.srt")

    def test_download_ignores_macosx_sidecar(self):
        # An AppleDouble sidecar (listed first) matching the requested episode and language
        # must not be pinned in place of the real subtitle member.
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body(
            {
                "__MACOSX/._Show.S01E01.VF.srt": "\x00\x05binary",
                "Show.S01E01.VO.srt": "english",
                "Show.S01E01.VF.srt": "french",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/x/Show.S01E01.ENFR.zip",
                "filename": "Show.S01E01.ENFR.zip",
                "media_type": "series",
                "season": 1,
                "episode": 1,
                "language": "fra",
                "release_info": "Show.S01E01.ENFR.zip",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        self.assertEqual(content["member"], "Show.S01E01.VF.srt")

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.SoustitreseuProvider()
        body = _zip_body({"Dune.Part.One.2021.WEB.VF.srt": "movie subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/films/download/krbse0p1duwc8oi/Dune.Part.One.(2021).Z2.WEB.zip",
                "filename": "Dune.Part.One.(2021).Z2.WEB.zip",
                "media_type": "film",
                "season": None,
                "episode": None,
                "release_info": "Dune.Part.One.(2021).Z2.WEB.zip",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertIsNone(content["episode"])
        self.assertNotIn("content_b64", content)

    def test_download_direct_subtitle_body_returns_content_mode(self):
        provider = self.mod.SoustitreseuProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nText\n"
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.sous-titres.eu/series/download/abc/Game.Of.Thrones.1x01.srt",
                "filename": "Game.Of.Thrones.1x01.srt",
                "media_type": "series",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, body)
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(content["format"], "srt")
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", content)
        self.assertNotIn("archive_b64", content)

    def test_download_rejects_html_error_page(self):
        provider = self.mod.SoustitreseuProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.sous-titres.eu/series/download/abc/Game.Of.Thrones.1x01.zip",
                    "filename": "Game.Of.Thrones.1x01.zip",
                },
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_rejects_empty_body(self):
        provider = self.mod.SoustitreseuProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"   \r\n  "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.sous-titres.eu/series/download/abc/Game.Of.Thrones.1x01.zip",
                    "filename": "Game.Of.Thrones.1x01.zip",
                },
                {"alpha3": "eng", "alpha2": "en"},
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


class _FakeOpener:
    """Stubs the lowest urllib transport: opener.open() raises a queued error or returns a body."""

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


class SoustitreseuTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.sleeps = []
        self.mod.time.sleep = lambda seconds: self.sleeps.append(seconds)

    def _http_error(self, url, code, reason, headers=None):
        # urllib.error.HTTPError allocates a temp file for its body; close it on
        # teardown so 3.14 does not emit a ResourceWarning during GC.
        error = urllib.error.HTTPError(url, code, reason, headers or {}, None)
        self.addCleanup(error.close)
        return error

    def _provider_with_outcomes(self, outcomes):
        provider = self.mod.SoustitreseuProvider()
        opener = _FakeOpener(outcomes)
        provider._opener = opener
        return provider, opener

    def test_http_get_retries_url_error_then_succeeds(self):
        provider, opener = self._provider_with_outcomes(
            [urllib.error.URLError("connection reset"), b"<html>ok</html>"]
        )

        body = provider._http_get("https://www.sous-titres.eu/search.html?q=x")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_http_get_retries_timeout_then_succeeds(self):
        provider, opener = self._provider_with_outcomes(
            [socket.timeout("read timed out"), TimeoutError("timed out"), b"body"]
        )

        body = provider._http_get("https://www.sous-titres.eu/x.html")

        self.assertEqual(body, b"body")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(self.sleeps), 2)
        # Exponential backoff: 0.5s then 1.0s.
        self.assertEqual(self.sleeps, [0.5, 1.0])

    def test_http_get_retries_503_then_succeeds(self):
        error = self._http_error("https://www.sous-titres.eu/x.html", 503, "Service Unavailable")
        provider, opener = self._provider_with_outcomes([error, b"recovered"])

        body = provider._http_get("https://www.sous-titres.eu/x.html")

        self.assertEqual(body, b"recovered")
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_http_get_honors_retry_after_on_429(self):
        error = self._http_error(
            "https://www.sous-titres.eu/x.html",
            429,
            "Too Many Requests",
            {"Retry-After": "3"},
        )
        provider, opener = self._provider_with_outcomes([error, b"ok"])

        body = provider._http_get("https://www.sous-titres.eu/x.html")

        self.assertEqual(body, b"ok")
        self.assertEqual(opener.calls, 2)
        # Retry-After (3s) wins over the 0.5s base backoff.
        self.assertEqual(self.sleeps, [3.0])

    def test_http_get_does_not_retry_404(self):
        error = self._http_error("https://www.sous-titres.eu/missing.html", 404, "Not Found")
        # Only the error is queued: a retry would pop past it and raise IndexError,
        # so reaching the assertions proves the 404 propagated on the first call.
        provider, opener = self._provider_with_outcomes([error])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.sous-titres.eu/missing.html")

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(opener.calls, 1)
        self.assertEqual(self.sleeps, [])

    def test_http_get_gives_up_after_max_attempts(self):
        outcomes = [urllib.error.URLError("down")] * 3
        provider, opener = self._provider_with_outcomes(outcomes)

        with self.assertRaises(urllib.error.URLError):
            provider._http_get("https://www.sous-titres.eu/x.html")

        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(self.sleeps), 2)


if __name__ == "__main__":
    unittest.main()
