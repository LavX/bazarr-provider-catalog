import base64
import hashlib
import importlib.util
import io
import json
import socket
import unittest
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "prijevodionline"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "prijevodionline_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INDEX_HTML = (FIXTURE_DIR / "prijevodionline_index_game_of_thrones.html").read_bytes()
SERIES_HTML = (FIXTURE_DIR / "prijevodionline_series_game_of_thrones.html").read_bytes()
SUBTITLES_HTML = (FIXTURE_DIR / "prijevodionline_subtitles_game_of_thrones_s01e01.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class PrijevodiOnlineParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_series_index_extracts_series_id_and_slug(self):
        rows = self.mod.parse_series_index(INDEX_HTML)

        self.assertEqual(rows[1]["series_id"], "935")
        self.assertEqual(rows[1]["title"], "Game of Thrones")
        self.assertEqual(rows[1]["slug"], "game-of-thrones")
        self.assertEqual(rows[1]["url"], "https://www.prijevodi-online.org/serije/view/935/game-of-thrones")

    def test_parse_series_page_extracts_key_and_episode_id(self):
        parsed = self.mod.parse_series_page(SERIES_HTML)

        self.assertEqual(parsed["key"], "ca7a167e13db896fe2324b2cbf10311f")
        self.assertEqual(parsed["episodes"][(1, 1)]["episode_id"], "33945")
        self.assertEqual(parsed["episodes"][(1, 1)]["title"], "Winter is Coming")
        self.assertEqual(parsed["episodes"][(2, 1)]["episode_id"], "43179")

    def test_parse_subtitle_rows_extracts_languages_releases_and_verified(self):
        rows = self.mod.parse_subtitle_rows(SUBTITLES_HTML)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["subtitle_id"], "18050")
        self.assertEqual(rows[0]["language"], "hrv")
        self.assertTrue(rows[0]["verified"])
        self.assertEqual(rows[0]["releases"], ["HDTV.XviD-FEVER", "720p.HDTV.X264-CTU"])
        self.assertEqual(rows[1]["language"], "srp")
        self.assertEqual(rows[2]["language"], "cnr")
        self.assertFalse(rows[2]["verified"])

    def test_index_url_normalizes_non_ascii_title_letters(self):
        self.assertEqual(
            self.mod._index_url("Élite"),
            "https://www.prijevodi-online.org/serije/index/e",
        )
        self.assertEqual(
            self.mod._index_url("Çukur"),
            "https://www.prijevodi-online.org/serije/index/c",
        )

    def test_parse_series_page_keeps_episode_zero_specials(self):
        body = (
            '<script>epizode.key = "ca7a167e13db896fe2324b2cbf10311f";</script>'
            '<h3 id="sezona-1">Sezona 1</h3>'
            '<div id="epizoda-90000">'
            '<ul class="epizoda actual">'
            '<li class="broj">0.</li>'
            '<li class="naziv"><a class="open" rel="/prijevod/get/90000" '
            'title="Download Special">Special</a></li>'
            '<li class="status">prevedeno</li>'
            "</ul></div>"
        )

        parsed = self.mod.parse_series_page(body)

        self.assertIn((1, 0), parsed["episodes"])
        self.assertEqual(parsed["episodes"][(1, 0)]["episode_id"], "90000")
        self.assertEqual(parsed["episodes"][(1, 0)]["title"], "Special")

    def test_find_series_matches_titles_that_drop_apostrophes(self):
        provider = self.mod.PrijevodiOnlineProvider()
        index_html = (
            '<table><tr id="serija-77">'
            '<td><a href="/serije/view/77/da-vincis-demons" '
            'title="Da Vincis Demons">Da Vincis Demons</a></td>'
            "</tr></table>"
        ).encode("utf-8")
        provider._http_get = lambda url, timeout=10, referer=None: index_html

        series = provider._find_series("Da Vinci's Demons")

        self.assertIsNotNone(series)
        self.assertEqual(series["series_id"], "77")
        self.assertEqual(series["slug"], "da-vincis-demons")

    def test_manifest_advertises_accepted_montenegrin_code(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())

        self.assertIn("cnr", manifest["languages"])
        self.assertNotIn("mne", manifest["languages"])
        # Every advertised code must be accepted by _requested_languages so the
        # marketplace never filters this provider out for a code it rejects.
        for code in manifest["languages"]:
            self.assertEqual(
                self.mod._requested_languages([{"alpha3": code}]),
                {code},
                msg=f"manifest advertises {code} but the provider rejects it",
            )


class PrijevodiOnlineProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_episode_languages(self):
        provider = self.mod.PrijevodiOnlineProvider()
        calls = []
        responses = {
            "https://www.prijevodi-online.org/serije/index/g": INDEX_HTML,
            "https://www.prijevodi-online.org/serije/view/935/game-of-thrones": SERIES_HTML,
            "https://www.prijevodi-online.org/prijevod/get/33945": SUBTITLES_HTML,
        }

        def get_stub(url, timeout=10, referer=None):
            del timeout, referer
            calls.append(("GET", url))
            return responses[url]

        def post_stub(url, data, timeout=10, referer=None):
            del timeout, referer
            calls.append(("POST", url, data))
            self.assertEqual(data, {"key": "ca7a167e13db896fe2324b2cbf10311f"})
            return responses[url]

        provider._http_get = get_stub
        provider._http_post = post_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "release_group": "CTU",
                "source": "HDTV",
            },
            [{"alpha3": "hrv", "alpha2": "hr"}, {"alpha3": "srp", "alpha2": "sr"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"hrv", "srp"})
        self.assertEqual(results[0]["provider_payload"]["episode_id"], "33945")
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(calls[-1][0], "POST")

    def test_search_hbs_request_maps_each_row_to_hbs(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=10, referer=None: (
            INDEX_HTML if "index" in url else SERIES_HTML
        )
        provider._http_post = lambda url, data, timeout=10, referer=None: SUBTITLES_HTML

        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1},
            [{"alpha3": "hbs", "alpha2": "sh"}],
            {},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"hbs"})
        self.assertEqual(len(results), 3)

    def test_search_supports_standard_montenegrin_code(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=10, referer=None: (
            INDEX_HTML if "index" in url else SERIES_HTML
        )
        provider._http_post = lambda url, data, timeout=10, referer=None: SUBTITLES_HTML

        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1},
            [{"alpha3": "cnr", "alpha2": "me"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "cnr")
        self.assertEqual(results[0]["language"]["alpha2"], "me")

    def test_search_ignores_movies_and_missing_episode_fields(self):
        provider = self.mod.PrijevodiOnlineProvider()

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Game of Thrones"}, [{"alpha3": "hrv"}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "episode", "series": "Game of Thrones", "season": 1}, [{"alpha3": "hrv"}], {}),
            [],
        )

    def test_download_pins_release_member_for_requested_episode(self):
        # Happy path: a multi-member zip with a release that uniquely matches the scored
        # releases must pin that member (not just defer the whole archive by episode).
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Game.of.Thrones.S01E02.HDTV.srt": "wrong episode",
                "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt": "wrong release",
                "Game.of.Thrones.S01E01.720p.HDTV.CTU.srt": "right subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/game-of-thrones-01x01-winter-is-coming-hdtv-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.zip",
                "subtitle_id": "18050",
                "season": 1,
                "episode": 1,
                "releases": ["720p.HDTV.X264-CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        # Archive mode: the worker hands the raw bytes back, the host extracts the pinned
        # member. No content extraction or decoding happens on the worker.
        self.assertNotIn("content_b64", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.720p.HDTV.CTU.srt")
        self.assertNotIn("episode", result)
        self.assertNotIn("encoding", result)

    def test_download_defers_to_episode_when_release_tie_is_ambiguous(self):
        # Defer: two members for the requested episode tie on releases overlap (or carry
        # no release tokens at all), so we must NOT pin a member and instead let the host
        # pick by episode. Pinning the wrong member would silently deliver a bad subtitle.
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Game.of.Thrones.S01E01.HDTV.part1.srt": "a",
                "Game.of.Thrones.S01E01.HDTV.part2.srt": "b",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got-s01e01-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.zip",
                "subtitle_id": "18050",
                "season": 1,
                "episode": 1,
                "releases": ["HDTV"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 1)

    def test_download_does_not_pin_wrong_season_member(self):
        # A season pack repeats the episode number across seasons. The requested S02E05
        # must never be served the S01E05 member: with no S02E05 present but episode
        # markers around, defer to the host (which fails loudly on a true no-match)
        # instead of pinning the wrong season.
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Show.S01E05.720p.WEB.CTU.srt": "season 1",
                "Show.S03E05.720p.WEB.CTU.srt": "season 3",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/show-s02e05-hr",
                "filename": "prijevodionline.show.s02e05.hr.zip",
                "subtitle_id": "18050",
                "season": 2,
                "episode": 5,
                "releases": ["720p.WEB.CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 5)

    def test_download_pins_lone_season_match_in_cross_season_pack(self):
        # A cross-season pack repeats the episode number across seasons (S01E05 and S02E05).
        # When exactly one member matches the requested season+episode, the worker must pin
        # it: handing the host a season-blind episode pick could silently deliver the other
        # season's same-numbered episode (a wrong-season subtitle). Request S02E05 -> pin the
        # S02E05 member, never the S01E05 one.
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Show.S01E05.720p.WEB-DL-CTU.srt": "season 1",
                "Show.S02E05.720p.WEB-DL-CTU.srt": "season 2",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/show-s02e05-hr",
                "filename": "prijevodionline.show.s02e05.hr.zip",
                "subtitle_id": "18050",
                "season": 2,
                "episode": 5,
                "releases": ["720p.WEB-DL-CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(result["member"], "Show.S02E05.720p.WEB-DL-CTU.srt")
        self.assertNotIn("episode", result)

    def test_download_does_not_mispin_on_unbounded_episode_marker(self):
        # Left-boundary guard: a bonus member like "Show.Extras1E02..." must NOT be read as
        # the SxxExx marker for S01E02 just because "s1e02" appears inside "extraS1E02".
        # Without the boundary the decoy is wrongly pooled for the requested episode and, on
        # a release-token overlap, gets pinned instead of the genuine S01E02 member.
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Show.Extras1E02.WEB-DL-CTU.srt": "bonus reel, not S01E02",
                "Show.S01E02.HDTV-FEVER.srt": "the real S01E02 subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/show-s01e02-hr",
                "filename": "prijevodionline.show.s01e02.hr.zip",
                "subtitle_id": "18050",
                "season": 1,
                "episode": 2,
                "releases": ["WEB-DL-CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        # The decoy's release tokens overlap the requested release, so a season/episode pool
        # that wrongly includes it would pin it. The boundary keeps it out, leaving only the
        # genuine member to pin.
        self.assertEqual(result["member"], "Show.S01E02.HDTV-FEVER.srt")
        self.assertNotIn("episode", result)

    def test_download_ignores_sidecar_and_separated_token_members(self):
        # __MACOSX/dot sidecars must be excluded, and a 3-digit episode code must not match
        # a resolution substring: "720p" must NOT satisfy a request for S07E20. Here only
        # the genuine S07E20 member (written with a separator) is the real candidate.
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "__MACOSX/._Show.S07E20.srt": "mac sidecar",
                ".hidden.srt": "dotfile",
                "Show.720p.WEB-DL.srt": "resolution decoy (no S07E20 token)",
                "Show.S07.E20.HDTV-FEVER.srt": "right episode, wrong release",
                "Show.S07.E20.WEB-DL-CTU.srt": "right subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/show-s07e20-hr",
                "filename": "prijevodionline.show.s07e20.hr.zip",
                "subtitle_id": "18050",
                "season": 7,
                "episode": 20,
                "releases": ["WEB-DL-CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(result["member"], "Show.S07.E20.WEB-DL-CTU.srt")
        self.assertNotIn("episode", result)

    def test_download_returns_rar_archive_bytes_for_host_extraction(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = b"Rar!\x1a\x07\x00" + b"binary rar payload"
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got-s01e01-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.rar",
                "subtitle_id": "18050",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("encoding", result)

    def test_download_carries_none_episode_when_payload_has_no_episode(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body({"some.subtitle.srt": "data"})
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/movie-hr",
                "filename": "prijevodionline.movie.hr.zip",
                "subtitle_id": "18050",
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertIsNone(result["episode"])

    def test_download_returns_content_for_direct_subtitle_body(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got-s01e01-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.srt",
                "subtitle_id": "18050",
                "episode": 1,
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"   \n"

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got",
                    "subtitle_id": "18050",
                    "episode": 1,
                },
                {"alpha3": "hrv", "alpha2": "hr"},
                {},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!DOCTYPE html><html><body>Not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got",
                    "subtitle_id": "18050",
                    "episode": 1,
                },
                {"alpha3": "hrv", "alpha2": "hr"},
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


class _ScriptedOpener:
    """Opener stub that yields a scripted sequence of errors then a body.

    Each entry is either an exception instance (raised) or bytes (returned via a
    response context manager). Records every call so tests can assert attempt
    counts.
    """

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self.calls = 0

    def open(self, request, timeout=None):
        del request, timeout
        self.calls += 1
        step = self._sequence.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeResponse(step)


def _http_error(code, retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://www.prijevodi-online.org/test", code, "boom", headers, io.BytesIO(b"")
    )


class PrijevodiOnlineTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.slept = []
        self._orig_sleep = self.mod.time.sleep
        # Patch the module-level sleep to a no-op recorder so retries are instant
        # and observable.
        self.mod.time.sleep = lambda seconds: self.slept.append(seconds)

    def tearDown(self):
        self.mod.time.sleep = self._orig_sleep

    def test_http_get_retries_url_error_then_succeeds(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener(
            [urllib.error.URLError("connection reset"), b"<html>ok</html>"]
        )
        provider._opener = opener

        body = provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len(self.slept), 1)

    def test_http_get_retries_timeout_then_succeeds(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([socket.timeout("timed out"), b"payload"])
        provider._opener = opener

        body = provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(body, b"payload")
        self.assertEqual(opener.calls, 2)

    def test_http_get_retries_503_twice_then_succeeds(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([_http_error(503), _http_error(503), b"finally"])
        provider._opener = opener

        body = provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(body, b"finally")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(len(self.slept), 2)

    def test_http_get_gives_up_after_three_attempts(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener(
            [_http_error(500), _http_error(500), _http_error(500)]
        )
        provider._opener = opener

        with self.assertRaises(urllib.error.HTTPError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(opener.calls, 3)

    def test_http_get_does_not_retry_404(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([_http_error(404), b"never reached"])
        provider._opener = opener

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(opener.calls, 1)
        self.assertEqual(self.slept, [])

    def test_http_get_does_not_retry_403(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([_http_error(403), b"never reached"])
        provider._opener = opener

        with self.assertRaises(urllib.error.HTTPError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(opener.calls, 1)

    def test_http_post_retries_then_succeeds(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([_http_error(502), b"post-ok"])
        provider._opener = opener

        body = provider._http_post(
            "https://www.prijevodi-online.org/prijevod/get/33945", {"key": "abc"}
        )

        self.assertEqual(body, b"post-ok")
        self.assertEqual(opener.calls, 2)

    def test_429_honors_retry_after_header(self):
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([_http_error(429, retry_after="3"), b"after-429"])
        provider._opener = opener

        body = provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(body, b"after-429")
        self.assertEqual(self.slept, [3.0])

    def test_backoff_is_capped(self):
        # A Retry-After far above the cap must still be clamped.
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([_http_error(429, retry_after="600"), b"ok"])
        provider._opener = opener

        provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(self.slept, [self.mod.RETRY_BACKOFF_CAP_SECONDS])

    def test_value_error_is_not_retried(self):
        # A non-network error from the transport must propagate on the first hit.
        provider = self.mod.PrijevodiOnlineProvider()
        opener = _ScriptedOpener([ValueError("not a network problem"), b"x"])
        provider._opener = opener

        with self.assertRaises(ValueError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/g")

        self.assertEqual(opener.calls, 1)
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()


class _RecordingOpener:
    """Returns scripted bodies while keeping every request for inspection."""

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self.requests = []

    def open(self, request, timeout=None):
        del timeout
        self.requests.append(request)
        entry = self._sequence.pop(0)
        if isinstance(entry, Exception):
            raise entry

        class _Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response(entry)


def _challenge_error(url="https://www.prijevodi-online.org/serije/index/s"):
    return urllib.error.HTTPError(
        url,
        403,
        "Forbidden",
        {"Server": "cloudflare", "cf-mitigated": "challenge", "Content-Type": "text/html"},
        io.BytesIO(b"<html><title>Just a moment...</title></html>"),
    )


def _solution(body="<html>ok</html>", status=200, cookies=(), user_agent="FS UA", url=None):
    return {
        "status": "ok",
        "solution": {
            "url": url or "https://www.prijevodi-online.org/serije/index/s",
            "status": status,
            "response": body,
            "cookies": [{"name": name, "value": value} for name, value in cookies],
            "userAgent": user_agent,
        },
    }


class PrijevodiOnlineCloudflareTests(unittest.TestCase):
    """The site fronts everything with a Cloudflare managed challenge now; the
    provider delegates challenged requests to FlareSolverr and reuses the
    solved cookies and User-Agent, or fails with an actionable message."""

    def setUp(self):
        self.mod = _load_provider_module()
        self._orig_sleep = self.mod.time.sleep
        self.mod.time.sleep = lambda seconds: None

    def tearDown(self):
        self.mod.time.sleep = self._orig_sleep

    def _provider(self, opener, config=None, solutions=None):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._opener = opener
        provider._config = dict(config or {})
        self.solver_payloads = []

        def transport(payload):
            self.solver_payloads.append(payload)
            return (solutions or []).pop(0)

        provider._flaresolverr_transport = transport
        return provider

    def test_challenge_without_flaresolverr_raises_a_clear_error(self):
        opener = _RecordingOpener([_challenge_error()])
        provider = self._provider(opener)
        with self.assertRaises(self.mod.CloudflareBlockedError) as caught:
            provider._http_get("https://www.prijevodi-online.org/serije/index/s")
        self.assertIn("FlareSolverr", str(caught.exception))
        # A challenge is not a transient failure: no retry storm against the wall.
        self.assertEqual(len(opener.requests), 1)

    def test_challenge_is_solved_through_flaresolverr(self):
        opener = _RecordingOpener([_challenge_error(), b"<html>later</html>"])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution(cookies=[("cf_clearance", "token")])],
        )

        body = provider._http_get("https://www.prijevodi-online.org/serije/index/s")

        # The solve earns cookies and a User-Agent; the payload itself comes
        # from replaying the original request through the opener.
        self.assertEqual(body, b"<html>later</html>")
        self.assertEqual(self.solver_payloads[0]["cmd"], "request.get")
        jar = {cookie.name: cookie.value for cookie in provider._cookie_jar}
        self.assertEqual(jar.get("cf_clearance"), "token")
        # The clearance cookie is bound to the browser that earned it, so the
        # replay (and every later request) presents the solved User-Agent.
        self.assertEqual(opener.requests[-1].get_header("User-agent"), "FS UA")

    def test_challenged_post_is_replayed_as_a_flaresolverr_post(self):
        opener = _RecordingOpener([_challenge_error(), b"rows"])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution(body="rows")],
        )

        body = provider._http_post(
            "https://www.prijevodi-online.org/prijevod/get/1", {"key": "abc"}
        )

        self.assertEqual(body, b"rows")
        payload = self.solver_payloads[0]
        self.assertEqual(payload["cmd"], "request.post")
        self.assertEqual(payload["postData"], "key=abc")
        # The replay is a real POST again, body intact.
        replay = opener.requests[-1]
        self.assertEqual(replay.get_method(), "POST")
        self.assertEqual(replay.data, b"key=abc")

    def test_a_solution_that_is_still_a_challenge_raises(self):
        opener = _RecordingOpener([_challenge_error()])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution(body="<html>Just a moment...</html>", status=403)],
        )
        with self.assertRaises(self.mod.CloudflareBlockedError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/s")

    def test_a_plain_403_still_raises_http_error(self):
        opener = _RecordingOpener([_http_error(403)])
        provider = self._provider(opener)
        with self.assertRaises(urllib.error.HTTPError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/s")

    def test_search_threads_config_into_the_transport(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._opener = _RecordingOpener([_challenge_error()])
        with self.assertRaises(self.mod.CloudflareBlockedError):
            provider.search(
                {"kind": "episode", "series": "Show", "season": 1, "episode": 1},
                [{"alpha3": "hrv"}],
                {"flaresolverr_url": ""},
            )


    def test_binary_downloads_survive_the_clearance(self):
        archive = b"PK\x03\x04\x00binary\xffbytes"
        opener = _RecordingOpener([_challenge_error(), archive])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution(cookies=[("cf_clearance", "token")])],
        )
        body = provider._http_get("https://www.prijevodi-online.org/preuzmi/1/hr")
        # FlareSolverr's JSON response field cannot carry a ZIP intact; the
        # bytes must come from the replay, exactly as the server sent them.
        self.assertEqual(body, archive)

    def test_transient_errors_after_the_clearance_keep_their_retry(self):
        opener = _RecordingOpener([_challenge_error(), _http_error(503), b"fine"])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution()],
        )
        body = provider._http_get("https://www.prijevodi-online.org/serije/index/s")
        self.assertEqual(body, b"fine")

    def test_a_challenge_on_the_replay_is_a_hard_failure_not_a_loop(self):
        opener = _RecordingOpener([_challenge_error(), _challenge_error()])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution()],
        )
        with self.assertRaises(self.mod.CloudflareBlockedError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/s")
        self.assertEqual(len(self.solver_payloads), 1)

    def test_a_generic_cloudflare_error_page_is_not_a_challenge(self):
        # Cloudflare's ordinary error template carries cf-error-details too; a
        # plain 403 with it must keep its meaning instead of demanding a solver.
        error = urllib.error.HTTPError(
            "https://www.prijevodi-online.org/serije/index/s",
            403,
            "Forbidden",
            {"Server": "cloudflare", "Content-Type": "text/html"},
            io.BytesIO(b"<html>cf-error-details: access denied</html>"),
        )
        provider = self._provider(_RecordingOpener([error]))
        with self.assertRaises(urllib.error.HTTPError):
            provider._http_get("https://www.prijevodi-online.org/serije/index/s")

    def test_the_solve_window_fits_the_worker_deadline(self):
        self.assertEqual(
            self.mod._flaresolverr_timeout_ms({"flaresolverr_timeout_ms": 30000}), 25000
        )

    def test_the_replay_drops_the_stale_cookie_header(self):
        # The opener's cookie processor stamps requests with the jar's (stale)
        # clearance; a request that already carries a Cookie header is left
        # alone by the processor, so the replay must shed it or the fresh
        # clearance never gets sent.
        opener = _RecordingOpener([_challenge_error(), b"ok"])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution(cookies=[("cf_clearance", "fresh")])],
        )
        import urllib.request as _ur

        request = _ur.Request(
            "https://www.prijevodi-online.org/serije/index/s",
            headers={"User-Agent": "old", "Cookie": "cf_clearance=stale"},
        )
        body = provider._open_with_retry(request, 10)
        self.assertEqual(body, b"ok")
        replay = opener.requests[-1]
        self.assertIsNone(replay.get_header("Cookie"))
        self.assertEqual(replay.get_header("User-agent"), "FS UA")

    def test_an_exhausted_deadline_refuses_to_solve(self):
        opener = _RecordingOpener([_challenge_error()])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution()],
        )
        import urllib.request as _ur

        request = _ur.Request("https://www.prijevodi-online.org/serije/index/s")
        with self.assertRaises(self.mod.CloudflareBlockedError) as caught:
            provider._open_with_retry(
                request, 10, deadline=self.mod.time.monotonic() + 3
            )
        self.assertIn("deadline", str(caught.exception))
        # No solver call was even attempted: the budget could not fit one.
        self.assertEqual(self.solver_payloads, [])

    def test_the_solve_window_shrinks_to_the_remaining_budget(self):
        opener = _RecordingOpener([_challenge_error(), b"ok"])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1", "flaresolverr_timeout_ms": 25000},
            solutions=[_solution()],
        )
        import urllib.request as _ur

        request = _ur.Request("https://www.prijevodi-online.org/serije/index/s")
        provider._open_with_retry(
            request, 10, deadline=self.mod.time.monotonic() + 13
        )
        # 13s budget minus the 5s replay reserve and the 2s solver transport
        # buffer leaves ~6s for the solver, not the configured 25s: even a
        # solver that uses its whole window cannot eat the replay reserve.
        self.assertLessEqual(self.solver_payloads[0]["maxTimeout"], 6000)
        self.assertGreaterEqual(self.solver_payloads[0]["maxTimeout"], 5000)

    def test_a_429_challenge_reaches_the_solver(self):
        error = urllib.error.HTTPError(
            "https://www.prijevodi-online.org/serije/index/s",
            429,
            "Too Many Requests",
            {"Server": "cloudflare", "cf-mitigated": "challenge"},
            io.BytesIO(b"<html><title>Just a moment...</title></html>"),
        )
        opener = _RecordingOpener([error, b"ok"])
        provider = self._provider(
            opener,
            config={"flaresolverr_url": "http://fs:8191/v1"},
            solutions=[_solution()],
        )
        body = provider._http_get("https://www.prijevodi-online.org/serije/index/s")
        self.assertEqual(body, b"ok")
        self.assertEqual(len(self.solver_payloads), 1)

    def test_a_plain_429_keeps_its_retry_semantics(self):
        opener = _RecordingOpener([_http_error(429), b"ok"])
        provider = self._provider(opener)
        body = provider._http_get("https://www.prijevodi-online.org/serije/index/s")
        self.assertEqual(body, b"ok")
        self.assertEqual(self.solver_payloads, [])

    def test_search_arms_the_shared_deadline_before_its_first_request(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._opener = _RecordingOpener(
            [urllib.error.URLError("down")] * (self.mod.HTTP_RETRIES + 1)
        )
        with self.assertRaises(urllib.error.URLError):
            provider.search(
                {"kind": "episode", "series": "Show", "season": 1, "episode": 1},
                [{"alpha3": "hrv"}],
                {},
            )
        self.assertIsNotNone(provider._deadline)
        remaining = provider._deadline - self.mod.time.monotonic()
        self.assertLessEqual(remaining, self.mod.WORKER_DEADLINE_SECONDS)

    def test_an_instance_deadline_is_picked_up_and_bounds_the_solve(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._deadline = self.mod.time.monotonic() + 3
        provider._opener = _RecordingOpener([_challenge_error()])
        provider._config = {"flaresolverr_url": "http://fs:8191/v1"}
        provider._flaresolverr_transport = lambda payload: (_ for _ in ()).throw(
            AssertionError("solver must not run on an exhausted shared budget")
        )
        import urllib.request as _ur

        with self.assertRaises(self.mod.CloudflareBlockedError) as caught:
            provider._open_with_retry(
                _ur.Request("https://www.prijevodi-online.org/serije/index/s"), 10
            )
        self.assertIn("deadline", str(caught.exception))

    def test_politeness_delays_never_sleep_into_the_deadline(self):
        provider = self.mod.PrijevodiOnlineProvider()
        slept = []
        self.mod.time.sleep = lambda seconds: slept.append(seconds)
        provider._deadline = self.mod.time.monotonic() + 2
        provider._pause({"request_delay_ms": 5000})
        self.assertTrue(all(seconds <= 1.0 for seconds in slept), slept)

    def test_politeness_delays_run_in_full_with_budget_to_spare(self):
        provider = self.mod.PrijevodiOnlineProvider()
        slept = []
        self.mod.time.sleep = lambda seconds: slept.append(seconds)
        provider._deadline = self.mod.time.monotonic() + 20
        provider._pause({"request_delay_ms": 3000})
        self.assertEqual(slept, [3.0])

    def test_manifest_declares_the_flaresolverr_capability(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text("utf-8"))
        self.assertIs(manifest.get("flaresolverr"), True)
        properties = manifest["config_schema"]["properties"]
        self.assertIn("flaresolverr_url", properties)
        self.assertIn("flaresolverr_timeout_ms", properties)
