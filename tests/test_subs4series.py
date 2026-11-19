import base64
import hashlib
import importlib.util
import io
import json
import re
import threading
import time
import unittest
import urllib.error
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subs4series"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subs4series_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SUGGESTIONS_HTML = (FIXTURE_DIR / "subs4series_suggestions.html").read_bytes()
EPISODE_HTML = (FIXTURE_DIR / "subs4series_episode_game_of_thrones_s01e01.html").read_bytes()
DIRECT_DOWNLOAD_HTML = (FIXTURE_DIR / "subs4series_download_page_direct.html").read_bytes()
CAPTCHA_DOWNLOAD_HTML = (FIXTURE_DIR / "subs4series_download_page_captcha.html").read_bytes()
# Two shows share the title "The Office": the 2024 one (path "the-office-") and the 2005 one.
OFFICE_SUGGESTIONS_HTML = b"""<select name="Mov_sel">
<option value="/tv-series/mr-bates-vs-the-post-office/s16df1514b8">Mr Bates vs. The Post Office</option>
<option value="/tv-series/the-office-/s2361cdce2f">The Office</option>
<option value="/tv-series/the-office/s4fcba70b44">The Office</option>
</select>"""
OFFICE_2024_EPISODE_URL = "https://www.subs4series.com/tv-series/the-office-/s2361cdce2f/season-1/episode-1"
OFFICE_2005_EPISODE_URL = "https://www.subs4series.com/tv-series/the-office/s4fcba70b44/season-1/episode-1"
OFFICE_2005_LISTING_URL = "https://www.subs4series.com/tv-series/the-office/s4fcba70b44/season-1"


def _episode_html_of_year(year):
    """The episode fixture with its header year (Game of Thrones (2011)) swapped for another."""
    assert EPISODE_HTML.count(b"</u> (2011)") == 1
    return EPISODE_HTML.replace(b"</u> (2011)", f"</u> ({year})".encode("ascii"))

# A season listing (/season-N) holds every subtitle of the season, season packs included.
EMPTY_LISTING_HTML = b"<html><body></body></html>"


def _is_season_listing(url):
    return bool(re.search(r"/season-\d+(?:/p-\d+)?$", url))


def _listing_row(release, slug):
    return (
        '<div class=" seeDark"><table><tr>'
        '<td><img src="//www.subs4series.com/images/el.gif" alt="Greek"></td>'
        f"<td><B>{release}</B></td>"
        f'<td><a href="/greek-subtitles/{slug}/x"><img src="dl_icon.png"></a></td>'
        "</tr></table></div>"
    )


def _listing_html(rows, last_page=None):
    """A season listing page of (release, slug) rows; last_page adds the site's p-N links."""
    body = "".join(_listing_row(release, slug) for release, slug in rows)
    if last_page:
        links = "".join(
            f'<a href="/tv-series/showname/s20a8bc7f2d/season-1/p-{page}" class="pg_page">{page + 1}</a>'
            for page in range(1, last_page + 1)
        )
        body += f'<div class="pg_pagination"><span class="pg_page active">1</span>{links}</div>'
    return f"<html><body>{body}</body></html>".encode("utf-8")


# Two-step getSub flow: the detail page only links to the getSub page; the reCAPTCHA gate
# and the real POST form live on that getSub page (not on the detail page).
DETAIL_PAGE_HTML = b'<html><body><a class="style55ws" href="/getSub-first.html">Download</a></body></html>'
DIRECT_GETSUB_HTML = (
    b'<html><body><form method="post" action="/getSub-final.html"><input type="submit"></form></body></html>'
)
DETAIL_URL = "https://www.subs4series.com/greek-subtitles/s277e869f4/game-of-thrones"
GETSUB_FIRST_URL = "https://www.subs4series.com/getSub-first.html"


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(".hidden.srt", "ignore")
        archive.writestr("Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt", "1\r\nsubtitle\r\n")
    return stream.getvalue()


def _multi_release_zip():
    # Two releases for the same episode (FEVER vs KILLERS) plus a hidden sidecar that must
    # be ignored. release_info token overlap disambiguates the two real members.
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("__MACOSX/._Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt", "junk")
        archive.writestr("Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt", "1\r\nfever\r\n")
        archive.writestr("Game.of.Thrones.S01E01.720p.WEB-DL.KILLERS.srt", "1\r\nkillers\r\n")
    return stream.getvalue()


class FakeScraperResponse:
    def __init__(self, status_code, headers, content, url="https://www.subs4series.com/search_report.php"):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.text = content.decode("utf-8", "ignore")
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("Cloudflare response should be handled before raise_for_status")


class FakeScraper:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []
        self.cookies = {}

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}, timeout))
        return self.responses.pop(0)


class _AnubisSession:
    """A requests-like session for solve_anubis_challenge, recording each GET's timeout."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = {}

    def get(self, url, timeout=None, allow_redirects=True):
        del allow_redirects
        self.calls.append((url, timeout))
        return self.responses.pop(0)


class FakeUrlopenResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class _DeadlineHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        try:
            if self.path == "/anubis" and not getattr(self.server, "challenge_seen", False):
                self.server.challenge_seen = True
                body = b'<script id="anubis_challenge">{"challenge":{"id":"a","randomData":"x","difficulty":0}}</script>'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/body")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/headers":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            elif self.path == "/chunks":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1;slow=")
            else:
                self.send_response(404 if self.path == "/error" else 200)
                self.send_header("Content-Length", "60")
                self.end_headers()
            pause = threading.Event()
            for _ in range(60):
                self.wfile.write(b"x")
                self.wfile.flush()
                pause.wait(0.025)
        except OSError:
            pass


class Subs4SeriesDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _DeadlineHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _provider(self):
        import requests

        provider = self.mod.Subs4SeriesProvider()
        provider._scraper = requests.Session()
        provider._scraper.trust_env = False
        provider._scraper_initialized = True
        self.addCleanup(provider._scraper.close)
        return provider

    def _url(self, path):
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def test_site_budget_interrupts_trickling_headers_body_chunks_and_redirects(self):
        provider = self._provider()
        for path in ("/headers", "/body", "/chunks", "/redirect"):
            with self.subTest(path=path), patch.object(self.mod, "SITE_CALL_BUDGET_SECONDS", 0.15):
                started = time.monotonic()
                with self.assertRaises(self.mod.ServiceUnavailable):
                    provider._http_get(self._url(path))
                self.assertLess(time.monotonic() - started, 1)

    def test_deadline_timeout_is_reported_as_service_unavailable(self):
        clock = [0.0]
        provider = self._provider()

        def get(*_args, **_kwargs):
            clock[0] = 60.0
            raise TimeoutError("deadline reached")

        provider._scraper.get = get
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(self.mod.ServiceUnavailable):
                provider._http_get(self._url("/ok"))

    def test_anubis_refetch_keeps_the_original_deadline(self):
        provider = self._provider()

        def solve(*_args, **_kwargs):
            threading.Event().wait(0.05)
            return {"proof": "ok"}

        with patch.object(self.mod, "SITE_CALL_BUDGET_SECONDS", 0.15), patch.object(
            self.mod, "solve_anubis_challenge", side_effect=solve
        ):
            started = time.monotonic()
            with self.assertRaises(self.mod.ServiceUnavailable):
                provider._http_get(self._url("/anubis"))
            self.assertLess(time.monotonic() - started, 0.5)

    def test_stdlib_fallback_bounds_error_bodies_and_headers(self):
        provider = self.mod.Subs4SeriesProvider()
        provider._get_scraper = lambda: None
        for path in ("/headers", "/error"):
            with self.subTest(path=path), patch.object(self.mod, "SITE_CALL_BUDGET_SECONDS", 0.15):
                started = time.monotonic()
                with self.assertRaises(self.mod.ServiceUnavailable):
                    provider._http_get(self._url(path))
                self.assertLess(time.monotonic() - started, 1)

    def test_stdlib_error_response_is_closed_when_the_deadline_interrupts_its_body(self):
        provider = self.mod.Subs4SeriesProvider()
        provider._get_scraper = lambda: None
        raw_request = provider._raw_request
        errors = []

        def request(*args, **kwargs):
            try:
                return raw_request(*args, **kwargs)
            except urllib.error.HTTPError as error:
                errors.append(error)
                raise

        provider._raw_request = request
        with patch.object(self.mod, "SITE_CALL_BUDGET_SECONDS", 0.15):
            with self.assertRaises(self.mod.ServiceUnavailable):
                provider._http_get(self._url("/error"))
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].fp.closed)

    def test_cloudscraper_replacement_adapters_and_reused_connections_are_watched(self):
        scraper = self.mod.cloudscraper.create_scraper(
            enable_cookie_persistence=False, enable_metrics=False,
            enable_performance_monitoring=False, enable_stealth=False,
            enable_tls_fingerprinting=False, enable_adaptive_timing=False,
            enable_ml_optimization=False, enable_anti_detection=False,
            enable_enhanced_spoofing=False, enable_enhanced_error_handling=False,
            rotate_tls_ciphers=False, min_request_interval=0,
            interpreter="native",
        )
        scraper.trust_env = False
        self.addCleanup(scraper.close)
        provider = self.mod.Subs4SeriesProvider()
        provider._scraper = scraper
        provider._scraper_initialized = True
        self.assertEqual(provider._http_get(self._url("/ok")), b"ok")
        adapter = self.mod.cloudscraper.CipherSuiteAdapter()
        context = adapter.ssl_context
        scraper.get_adapter(self._url("/ok")).close()
        scraper.mount("http://", adapter)
        self.assertEqual(provider._http_get(self._url("/ok")), b"ok")
        get_adapter = scraper.get_adapter
        get_connection = adapter.get_connection_with_tls_context
        pool_keys = list(adapter.poolmanager.pools.keys())
        self.assertEqual(len(pool_keys), 1)
        pool = adapter.poolmanager.pools[pool_keys[0]]
        connection_class = pool.ConnectionCls
        self.assertEqual(pool.num_connections, 1)
        self.assertEqual(provider._http_get(self._url("/ok")), b"ok")
        self.assertEqual(pool.num_connections, 1)
        self.assertIs(scraper.get_adapter, get_adapter)
        self.assertIs(adapter.get_connection_with_tls_context, get_connection)
        self.assertIs(pool.ConnectionCls, connection_class)
        self.assertIs(adapter.ssl_context, context)
        with patch.object(self.mod, "SITE_CALL_BUDGET_SECONDS", 0.15):
            with self.assertRaises(self.mod.ServiceUnavailable):
                provider._http_get(self._url("/body"))
        self.assertEqual(pool.num_connections, 1)
        self.assertFalse(any(t.name == "subs4series-deadline" for t in threading.enumerate()))


class Subs4SeriesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_season_episodes_read_the_release_name_forms_the_site_uses(self):
        cases = {
            "Showname S01E02 720p": (1, (2,)),
            "Showname 2x01 - Pilot": (2, (1,)),
            # Both seen live, where they used to score 75 instead of 100.
            "Showname(BBC) S02-Ep01": (2, (1,)),
            "Showname Season 1 Episode 01 - Pilot-gre": (1, (1,)),
            "Showname S03 E10 HDTV": (3, (10,)),
            "Showname S3.Ep.10": (3, (10,)),
            "Showname season 3 ep 10": (3, (10,)),
            # Seen live on a season 1 listing.
            "Showname Season1-Episode12": (1, (12,)),
            "Showname S01E24-25 - Finale (2) - HDTV": (1, (24, 25)),
            "Showname Season1-Episode24,25-part2,3": (1, (24, 25)),
            "Showname 3x17-18": (3, (17, 18)),
            "Showname S01E0 -  - 1080p-GRP": (1, (0,)),
        }
        for release, expected in cases.items():
            with self.subTest(release=release):
                self.assertEqual(self.mod._season_episodes(release), expected)

    def test_season_episodes_ignore_numbers_that_are_not_an_episode(self):
        for release in ("Showname 1080p x264 DDP5 1", "Showname Season 1 Complete", "Seasons 1080p", "Sons 2 Ep"):
            with self.subTest(release=release):
                self.assertEqual(self.mod._season_episodes(release), (None, ()))

    def test_season_episodes_take_no_second_episode_from_other_numbers(self):
        cases = {
            "Showname S01E05-720p": (1, (5,)),
            "Showname S01E05 - 1080p": (1, (5,)),
            "Showname S01E05 - 2 Parts": (1, (5,)),
            "Showname S01E05 DDP5.1": (1, (5,)),
            "Showname S01E05-07": (1, (5, 6, 7)),
        }
        for release, expected in cases.items():
            with self.subTest(release=release):
                self.assertEqual(self.mod._season_episodes(release), expected)

    def test_season_episodes_read_the_episode_word_and_episode_ranges(self):
        cases = {
            "Showname S01 Episode 5": (1, (5,)),
            "Showname S01 Episode24,25": (1, (24, 25)),
            "Showname S01E05-E06": (1, (5, 6)),
            "Showname S01E01-E10 720p": (1, tuple(range(1, 11))),
            "Showname Season 2 Episode 01-Episode 04": (2, (1, 2, 3, 4)),
        }
        for release, expected in cases.items():
            with self.subTest(release=release):
                self.assertEqual(self.mod._season_episodes(release), expected)

    def test_season_episodes_read_the_forms_seen_across_the_site(self):
        # Each form is copied from a live season listing, with the show's name replaced.
        cases = {
            # Episode 0 or 1 with a word run into the number.
            "Showname S01E0Preview - - 720p HDTV DD5 1 x264": (1, (0,)),
            "Showname (2010) S01E0season DVDRip": (1, (0,)),
            "Showname S01E01The One With The Roommate": (1, (1,)),
            # Season and episodes run together.
            "Showname S0405 The One With The Girlfriend {1080p}": (4, (5,)),
            "Showname S04E0102 HDTV x264": (4, (1, 2)),
            "Showname S04E01E02 720p HDTV": (4, (1, 2)),
            # An "x" between season and episode, with an "s" before it or a multiplication sign.
            "Showname s1x05_GR": (1, (5,)),
            "Showname 1\u00d702 The Second Episode HDTV": (1, (2,)),
            "Showname S2xEP01 hdtv xvid": (2, (1,)),
            # Ranges whose last number has no marker of its own.
            "Showname S1x12-22 {GR} (2005 - 2006)": (1, tuple(range(12, 23))),
            "Showname S04E01-4 - - 720 web-dl": (4, (1, 2, 3, 4)),
            "Showname Season 4 ep 1-11 720p HDTV": (4, tuple(range(1, 12))),
            "Showname S04E13-14 - Part (2 & 3) - HDTV": (4, (13, 14)),
            "Showname 4x13&14 - Part2_GR": (4, (13, 14)),
            # A miniseries names no season.
            "Showname E05 2001 1080p BluRay DD5 1 x264-GRP": (None, (5,)),
            "Showname part5 [2001] 720p BRRip x264": (None, (5,)),
            "Showname - Episodes 1-10 {GR} (2001)": (None, tuple(range(1, 11))),
            "Showname 1-5 episodes BDRip x264": (None, (1, 2, 3, 4, 5)),
            "Showname 2001 E01-10 HDDVD x264": (None, tuple(range(1, 11))),
            # No episode in a resolution.
            "Showname S02E720p WEB-DL - - 720p Web-dl": (None, ()),
            "Showname S01E1080p": (None, ()),
        }
        for release, expected in cases.items():
            with self.subTest(release=release):
                self.assertEqual(self.mod._season_episodes(release), expected)

    def test_derive_matches_scores_a_miniseries_episode_without_its_season(self):
        video = {"series": "Showname", "season": 1, "episode": 5}
        item = {"release_info": "Showname E05 2001 1080p BluRay"}
        self.assertEqual(set(self.mod.derive_matches(video, item)), {"series", "episode"})
        self.assertEqual(self.mod.compute_score(video, item), 95)
        self.assertNotIn("episode", self.mod.derive_matches({**video, "episode": 6}, item))

    def test_season_episodes_take_no_run_from_a_word_ending_in_e(self):
        # The "e" closing "The" is no episode marker: this is episode 1 alone.
        self.assertEqual(self.mod._season_episodes("Showname S02E01 The 1-2 Punch"), (2, (1,)))
        self.assertEqual(self.mod._season_episodes("Showname S02E01 Volume 1-2"), (2, (1,)))
        self.assertNotIn(
            "episode",
            self.mod.derive_matches({"series": "Showname", "season": 2, "episode": 2}, {"release_info": "Showname S02E01 The 1-2 Punch"}),
        )

    def test_derive_matches_counts_a_double_episode_for_both_episodes(self):
        video = {"series": "Showname", "season": 1}
        release = {"release_info": "Showname S01E24-25 - Finale (2) - HDTV"}
        for episode in (24, 25):
            with self.subTest(episode=episode):
                self.assertTrue({"series", "season", "episode"}.issubset(self.mod.derive_matches({**video, "episode": episode}, release)))
        self.assertNotIn("episode", self.mod.derive_matches({**video, "episode": 26}, release))
        self.assertNotIn("season", self.mod.derive_matches({**video, "season": 2, "episode": 25}, release))

    def test_derive_matches_reads_the_season_and_episode_words_without_spaces(self):
        matches = self.mod.derive_matches(
            {"series": "Showname", "season": 1, "episode": 12}, {"release_info": "Showname Season1-Episode12"}
        )
        self.assertTrue({"series", "season", "episode"}.issubset(matches))

    def test_pack_seasons_reads_the_season_pack_names_the_site_uses(self):
        cases = {
            # The first three seen live on a season 1 listing in November 2026.
            "Showname S01 1080p BluRay x264-GRP": {1},
            "Showname S01E0 -  - 1080p-GRP": {1},
            "Showname S01 DVDRip XviD-ABC [GR]": {1},
            "Showname Season 1 Complete": {1},
            "Showname Season1 DVDRip": {1},
            "Showname S01-S03 720p": {1, 2, 3},
            "Showname S01E01 - Pilot - HDTV": set(),
            "Showname S01E24-25 - Finale (2) - HDTV": set(),
            "Showname Season1-Episode12": set(),
            "Showname 3x17 {GR} [hdtv-xyz]": set(),
            "Showname S01 E05": set(),
            "Showname 1080p x264 DTS": set(),
            "Showname Season 02 720p": {2},
            "Showname S2 WEB-DL": {2},
            "Seasons 1080p": set(),
            "Showname Complete Series": set(),
            "Showname S01E05 Season Finale": set(),
            "Showname 5.1 Surround": set(),
            # A name that gives an episode is no pack, whichever way it writes it.
            "Showname S01 Episode 5": set(),
            "Showname S01 Episode24,25": set(),
            "Showname Season 1 - 05": set(),
            "Showname S01 - 05": set(),
            # Ranges of seasons, and of episodes, which hold only their own episodes.
            "Showname S01-03 1080p": {1, 2, 3},
            "Showname S1 - S3": {1, 2, 3},
            "Showname Season 1-3": {1, 2, 3},
            "Showname Seasons 1 - 3 Complete": {1, 2, 3},
            "Showname Seasons 1 to 2": {1, 2},
            "Showname S01E01-E10 720p": {1},
            "Showname S01-1080p": {1},
        }
        for release, expected in cases.items():
            with self.subTest(release=release):
                self.assertEqual(self.mod.pack_seasons(release), expected)

    def test_pack_holds_only_its_own_episodes(self):
        holds = self.mod.pack_holds
        # Ranges and wholes the site's listings use.
        self.assertTrue(holds("Showname Season 1 Episodes 13-22 Dvd", 1, 15))
        self.assertFalse(holds("Showname Season 1 Episodes 13-22 Dvd", 1, 5))
        self.assertTrue(holds("Showname S1x01-11 {GR}", 1, 11))
        self.assertFalse(holds("Showname S1x01-11 {GR}", 1, 12))
        self.assertTrue(holds("Showname S04E60-81 - Complete Season - SUBrip", 4, 5))
        self.assertTrue(holds("Showname S01E HDTV 720p", 1, 9))
        # A miniseries pack names no season and is read as the season searched.
        self.assertTrue(holds("Showname - Episodes 1-10 {GR} (2001)", 1, 10))
        self.assertFalse(holds("Showname - Episodes 1-10 {GR} (2001)", 1, 11))
        self.assertTrue(holds("Showname (All Episodes) 720p BRRip", 1, 7))
        self.assertFalse(holds("Showname E05 2001 1080p", 1, 5))
        self.assertTrue(holds("Showname S01 1080p BluRay", 1, 15))
        self.assertTrue(holds("Showname S01E0 - - 1080p-GRP", 1, 15))
        self.assertTrue(holds("Showname S01-S03", 2, 4))
        self.assertTrue(holds("Showname S01E01-E10 720p", 1, 10))
        self.assertFalse(holds("Showname S01E01-E10 720p", 1, 11))
        self.assertFalse(holds("Showname S01 1080p BluRay", 2, 1))
        self.assertFalse(holds("Showname S01E05 HDTV", 1, 5))

    def test_listing_pages_to_read_keeps_the_last_page_past_the_cap(self):
        self.assertEqual(self.mod._listing_pages_to_read(_listing_html([])), [])
        self.assertEqual(self.mod._listing_pages_to_read(_listing_html([], last_page=1)), [1])
        self.assertEqual(self.mod._listing_pages_to_read(_listing_html([], last_page=4)), [1, 2, 3, 4])
        self.assertEqual(self.mod._listing_pages_to_read(_listing_html([], last_page=9)), [1, 2, 3, 9])

    def test_parse_suggestions_extracts_show_paths(self):
        rows = self.mod.parse_suggestions(SUGGESTIONS_HTML)

        self.assertEqual(rows[0]["title"], "Game of Thrones")
        self.assertEqual(rows[0]["show_path"], "tv-series/game-of-thrones/s8985ffc551")
        self.assertEqual(
            rows[0]["url"],
            "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551",
        )

    def test_parse_episode_page_extracts_rows(self):
        rows = self.mod.parse_episode_page(EPISODE_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["language"], "ell")
        self.assertEqual(rows[0]["alpha2"], "el")
        self.assertEqual(rows[0]["series_title"], "Game of Thrones")
        self.assertEqual(rows[0]["year"], 2011)
        self.assertEqual(rows[0]["release_info"], "Game Of Thrones S01E01 - Winter is Coming - hdtv xvid-fever")
        self.assertEqual(rows[0]["uploader"], "aristarhos")
        self.assertEqual(rows[0]["downloads"], 4312)
        self.assertEqual(
            rows[0]["detail_url"],
            "https://www.subs4series.com/greek-subtitles/s277e869f4/game-of-thrones-s01e01-winter-is-coming-hdtv-xvid-fever",
        )
        self.assertEqual(rows[1]["language"], "eng")

    def test_extract_download_zip_returns_raw_archive_for_host(self):
        body = _zip_body()

        payload = self.mod.extract_download(
            body,
            {"filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.el.zip", "episode": 1},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(payload["episode"], 1)
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", payload)
        self.assertNotIn("member", payload)
        self.assertNotIn("encoding", payload)

    def test_extract_download_rar_returns_raw_archive_for_host(self):
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32

        payload = self.mod.extract_download(body, {"filename": "subs4series.rar", "episode": 5})

        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(payload["episode"], 5)
        self.assertNotIn("content_b64", payload)
        self.assertNotIn("encoding", payload)

    def test_extract_download_archive_episode_is_none_when_missing(self):
        body = _zip_body()

        payload = self.mod.extract_download(body, {"filename": "subs4series.zip"})

        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertIsNone(payload["episode"])

    def test_extract_download_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"   \r\n  ", {"filename": "subs4series.zip"})

    def test_extract_download_rejects_html_error_page(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
                b"<body>Subtitle not found</body></html>",
                {"filename": "subs4series.zip"},
            )

    def test_extract_download_pins_member_matching_release_info(self):
        # Multi-release zip for the same episode: the host's episode-only pick cannot tell
        # the FEVER and KILLERS releases apart, so the worker pins the release_info match.
        body = _multi_release_zip()

        payload = self.mod.extract_download(
            body,
            {
                "release_info": "Game Of Thrones S01E01 hdtv xvid-fever",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertEqual(payload["member"], "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt")
        self.assertNotIn("episode", payload)
        self.assertNotIn("content_b64", payload)
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())

    def test_extract_download_defers_when_release_info_does_not_disambiguate(self):
        # Two releases for the episode but the release_info overlaps neither group: a tie on
        # the shared episode tokens must defer to the host instead of pinning a guess.
        body = _multi_release_zip()

        payload = self.mod.extract_download(
            body,
            {
                "release_info": "Game Of Thrones S01E01",
                "season": 1,
                "episode": 1,
            },
        )

        self.assertNotIn("member", payload)
        self.assertEqual(payload["episode"], 1)

    def test_extract_download_excludes_sidecars_and_pins_real_member(self):
        # __MACOSX/dotfile sidecars and directory entries must never be pinned; with one
        # real member left there is nothing to disambiguate, so defer to the host.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("subs/", "")
            archive.writestr("__MACOSX/._Game.of.Thrones.S01E01.srt", "junk")
            archive.writestr(".DS_Store", "junk")
            archive.writestr("subs/Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt", "1\nsub\n")
        body = stream.getvalue()

        payload = self.mod.extract_download(
            body,
            {"release_info": "Game Of Thrones S01E01 hdtv xvid-fever", "season": 1, "episode": 1},
        )

        self.assertNotIn("member", payload)
        self.assertEqual(payload["episode"], 1)

    def test_extract_download_defers_on_wrong_episode_season_pack(self):
        # Season pack: the requested S01E02 is absent. Episode markers are present for other
        # episodes, so pinning any of them would hard-fail the host; defer instead.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Show.S01E01.HDTV.srt", "1\nsub\n")
            archive.writestr("Show.S01E03.HDTV.srt", "1\nsub\n")
        body = stream.getvalue()

        payload = self.mod.extract_download(
            body,
            {"release_info": "Show S01E02 hdtv", "season": 1, "episode": 2},
        )

        self.assertNotIn("member", payload)
        self.assertEqual(payload["episode"], 2)

    def test_extract_download_narrows_season_pack_by_season_and_episode(self):
        # A pack that repeats episode 5 across seasons: pinning on episode alone is ambiguous,
        # so narrow by BOTH season and episode and pick the S02E05 member.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Show.S01E05.HDTV.XviD-FEVER.srt", "1\nsub\n")
            archive.writestr("Show.S02E05.HDTV.XviD-FEVER.srt", "1\nsub\n")
        body = stream.getvalue()

        payload = self.mod.extract_download(
            body,
            {"release_info": "Show S02E05 hdtv xvid-fever", "season": 2, "episode": 5},
        )

        self.assertEqual(payload["member"], "Show.S02E05.HDTV.XviD-FEVER.srt")
        self.assertNotIn("episode", payload)

    def test_extract_download_does_not_match_resolution_digits_as_episode(self):
        # "720" (S07E20 compact form) must not match inside "720p"; with the requested
        # episode genuinely absent the markers force a defer.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Show.S07E19.720p.WEB.srt", "1\nsub\n")
            archive.writestr("Show.S07E21.720p.WEB.srt", "1\nsub\n")
        body = stream.getvalue()

        payload = self.mod.extract_download(
            body,
            {"release_info": "Show S07E20 720p web", "season": 7, "episode": 20},
        )

        self.assertNotIn("member", payload)
        self.assertEqual(payload["episode"], 20)

    def test_extract_download_does_not_pin_member_on_title_digit_boundary(self):
        # "The.Originals1E02" must NOT be read as S01E02: the "s" belongs to the title word
        # "Originals", not a season marker. Without a left alpha/digit boundary the SxxExx
        # regex pins this placeholder member (a lone episode match is pinned outright), so the
        # host hard-fails the "member in namelist" check or silently delivers the wrong
        # subtitle. The real S01.E03 member proves no requested-episode match exists, forcing
        # a defer to host episode selection.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("The.Originals1E02.placeholder.srt", "1\nsub\n")
            archive.writestr("The.Originals.S01.E03.eng.srt", "1\nsub\n")
        body = stream.getvalue()

        payload = self.mod.extract_download(
            body,
            {"release_info": "The Originals S01E02", "season": 1, "episode": 2},
        )

        self.assertNotEqual(payload.get("member"), "The.Originals1E02.placeholder.srt")
        self.assertNotIn("member", payload)
        self.assertEqual(payload["episode"], 2)

    def test_extract_download_reads_the_ep_form_of_a_season_pack(self):
        # The members of a live "S01E0" pack, named "S01 EP01" to "S01 EP25".
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for number in range(1, 26):
                archive.writestr(f"Showname S01 EP{number:02d} 1080p BluRay DTS x264-GRP.srt", f"1\r\nepisode {number}\r\n")
        payload = {"season": 1, "episode": 1, "release_info": "Showname S01E0 - - 1080p-GRP"}

        self.assertEqual(
            self.mod.extract_download(stream.getvalue(), payload)["member"],
            "Showname S01 EP01 1080p BluRay DTS x264-GRP.srt",
        )
        payload["episode"] = 12
        self.assertEqual(
            self.mod.extract_download(stream.getvalue(), payload)["member"],
            "Showname S01 EP12 1080p BluRay DTS x264-GRP.srt",
        )
        # Episode 26 is not in the pack: defer, and the host fails the download.
        payload["episode"] = 26
        self.assertNotIn("member", self.mod.extract_download(stream.getvalue(), payload))

    def test_extract_download_pins_a_double_episode_member_for_either_episode(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Showname.S01E23.HDTV.srt", "1\nsub\n")
            archive.writestr("Showname.S01E24-25.HDTV.srt", "1\nsub\n")
            archive.writestr("Showname.S02E25.HDTV.srt", "1\nsub\n")
        payload = {"season": 1, "release_info": "Showname S01 HDTV"}
        for episode in (24, 25):
            with self.subTest(episode=episode):
                member = self.mod.extract_download(stream.getvalue(), {**payload, "episode": episode})["member"]
                self.assertEqual(member, "Showname.S01E24-25.HDTV.srt")

    def test_extract_download_pins_members_named_with_season_and_episode_words(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Showname Season1-Episode01 HDTV.srt", "1\nsub\n")
            archive.writestr("Showname Season1-Episode02 DVDRip.srt", "1\nsub\n")
            archive.writestr("Showname Season1-Episode03 HDTV.srt", "1\nsub\n")
        payload = {"season": 1, "episode": 1, "release_info": "Showname Season 1 DVDRip"}
        self.assertEqual(self.mod.extract_download(stream.getvalue(), payload)["member"], "Showname Season1-Episode01 HDTV.srt")
        # An episode the pack lacks: no other episode's member is pinned.
        self.assertNotIn("member", self.mod.extract_download(stream.getvalue(), {**payload, "episode": 4}))

    def test_extract_download_rar_defers_to_host_episode_path(self):
        # RAR is not stdlib-listable: always fall back to the host episode path.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32

        payload = self.mod.extract_download(
            body,
            {"release_info": "Show S01E01 hdtv", "season": 1, "episode": 1},
        )

        self.assertNotIn("member", payload)
        self.assertEqual(payload["episode"], 1)


class Subs4SeriesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_episode_languages(self):
        provider = self.mod.Subs4SeriesProvider()
        calls = []
        responses = {
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1": SUGGESTIONS_HTML,
            "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1": EPISODE_HTML,
            "https://www.subs4series.com/tv-series/game-of-thrones-2011/s1111111111/season-1/episode-1": EPISODE_HTML,
            # Both shows are of the video's year, so both season listings are read for packs.
            "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1": EMPTY_LISTING_HTML,
            "https://www.subs4series.com/tv-series/game-of-thrones-2011/s1111111111/season-1": EMPTY_LISTING_HTML,
        }

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if url not in responses:
                if _is_season_listing(url):
                    return EMPTY_LISTING_HTML
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "title": "Winter Is Coming",
                "year": 2011,
                "source": "HDTV",
                "release_group": "FEVER",
            },
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls, list(responses))
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        first = results[0]
        expected_episode_url = "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1"
        self.assertEqual(first["provider"], "subs4series")
        self.assertIn("series", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertIn("release_group", first["matches"])
        self.assertEqual(first["score"], 100)
        self.assertEqual(first["provider_payload"]["page_link"], expected_episode_url)
        for item in results:
            self.assertEqual(item["filename"], item["provider_payload"]["filename"])
            self.assertEqual(item["score_without_hash"], item["score"])
            self.assertEqual(item["score_out_of"], 100)
            self.assertIs(item["hash_verifiable"], False)
            self.assertIs(item["hearing_impaired_verifiable"], False)
            self.assertIs(item["hearing_impaired"], False)
        # The host needs episode (and season) to pick the archive member.
        self.assertEqual(first["provider_payload"]["season"], 1)
        self.assertEqual(first["provider_payload"]["episode"], 1)

    def _same_title_search(self, video, episode_pages):
        """Search "The Office" with two shows of that title; episode_pages maps URL to body or error."""
        provider = self.mod.Subs4SeriesProvider()
        calls = []
        responses = {
            "https://www.subs4series.com/search_report.php?search=The+Office&searchType=1": OFFICE_SUGGESTIONS_HTML,
            **episode_pages,
        }

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if url not in responses:
                if _is_season_listing(url):
                    return EMPTY_LISTING_HTML
                raise AssertionError(f"unexpected URL: {url}")
            if isinstance(responses[url], Exception):
                raise responses[url]
            return responses[url]

        provider._http_get = stub
        video = {"kind": "episode", "series": "The Office", "season": 1, "episode": 1, **video}
        results = provider.search(video, [{"alpha3": "ell", "alpha2": "el"}], {"request_delay_ms": 0})
        return results, calls

    def test_search_keeps_only_the_same_title_show_of_the_video_year(self):
        results, calls = self._same_title_search(
            {"year": 2005},
            {OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024), OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
        )

        # Only the chosen show's season listing is read for packs.
        self.assertEqual(calls[1:], [OFFICE_2024_EPISODE_URL, OFFICE_2005_EPISODE_URL, OFFICE_2005_LISTING_URL])
        self.assertTrue(results)
        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2005_EPISODE_URL})

    def test_search_allows_one_year_of_slack_between_bazarr_and_the_site(self):
        results, _calls = self._same_title_search(
            {"year": 2006},
            {OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024), OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
        )

        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2005_EPISODE_URL})

    def test_pages_for_year_prefers_the_same_year_over_one_year_off(self):
        pages = [("a", 2006, []), ("b", 2005, []), ("c", 2024, [])]

        self.assertEqual(self.mod._pages_for_year(pages, 2005), [("b", 2005, [])])
        self.assertEqual(self.mod._pages_for_year(pages[::2], 2005), [("a", 2006, [])])
        self.assertEqual(self.mod._pages_for_year(pages, 1990), pages)
        self.assertEqual(self.mod._pages_for_year(pages, None), pages)
        self.assertEqual(self.mod._pages_for_year(pages[2:], 2005), pages[2:])

    def test_search_keeps_every_same_title_show_without_a_video_year(self):
        results, _calls = self._same_title_search(
            {},
            {OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024), OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
        )

        # Both pages carry the same fixture rows, so dedupe keeps the first show's copies.
        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2024_EPISODE_URL})

    def test_search_keeps_every_same_title_show_when_none_matches_the_year(self):
        results, _calls = self._same_title_search(
            {"year": 1995},
            {OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024), OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
        )

        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2024_EPISODE_URL})

    def test_search_skips_a_same_title_show_without_the_episode(self):
        not_found = urllib.error.HTTPError(OFFICE_2024_EPISODE_URL, 404, "Not Found", {}, None)
        results, calls = self._same_title_search(
            {"year": 2005},
            {OFFICE_2024_EPISODE_URL: not_found, OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
        )

        # The 2024 show lacks the episode, so its season listing is read for packs and its year.
        office_2024_listing = OFFICE_2024_EPISODE_URL.rsplit("/episode-", 1)[0]
        self.assertEqual(
            calls[1:], [OFFICE_2024_EPISODE_URL, office_2024_listing, OFFICE_2005_EPISODE_URL, OFFICE_2005_LISTING_URL]
        )
        self.assertTrue(results)
        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2005_EPISODE_URL})

    def test_search_skips_a_requests_404_too(self):
        class _Response:
            status_code = 404

        class _RequestsHTTPError(Exception):
            response = _Response()

        results, _calls = self._same_title_search(
            {"year": 2005},
            {OFFICE_2024_EPISODE_URL: _RequestsHTTPError("404 Client Error"), OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
        )

        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2005_EPISODE_URL})

    def test_search_still_raises_errors_other_than_404(self):
        unavailable = urllib.error.HTTPError(OFFICE_2024_EPISODE_URL, 503, "Service Unavailable", {}, None)
        self.addCleanup(unavailable.close)

        with self.assertRaises(urllib.error.HTTPError):
            self._same_title_search(
                {"year": 2005},
                {OFFICE_2024_EPISODE_URL: unavailable, OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005)},
            )

    def test_title_matches_without_qualifier(self):
        match = self.mod._title_matches_without_qualifier

        self.assertTrue(match("Shameless (US)", "Shameless", 2011))
        self.assertTrue(match("Shameless (US)", "Shameless (US)", 2011))
        self.assertTrue(match("The Office", "The Office (US)", 2005))
        self.assertTrue(match("Doctor Who (2005)", "Doctor Who", 2005))
        self.assertFalse(match("Doctor Who (2005)", "Doctor Who", 2006))
        self.assertFalse(match("Shameless (US)", "Shameless (UK)", 2004))
        self.assertFalse(match("Doctor Who (1963)", "Doctor Who", 2005))
        # A dated site title is never taken without a video year to back it.
        self.assertFalse(match("Doctor Who (2005)", "Doctor Who", None))
        self.assertFalse(match("Shameless (US)", "Shame", 2011))
        self.assertFalse(match("Mr Bates vs. The Post Office", "The Office (US)", 2005))

    def test_title_matches_either_name_of_a_bracketed_site_title(self):
        match = self.mod._title_matches
        # Live site titles of foreign shows, the English name first or second, and the names
        # Sonarr may send for each.
        cases = [
            ("Vis a Vis (Locked Up)", ["Locked Up", "Vis a vis", "Vis a Vis (Locked Up)"]),
            ("La Casa de Papel (Money Heist)", ["Money Heist", "La casa de papel"]),
            ("Cable Girls (Las Chicas del Cable)", ["Cable Girls", "Las chicas del cable"]),
            ("The Bureau (Le Bureau des Legendes)", ["The Bureau", "Le Bureau des Légendes"]),
            ("Coeurs Noirs (Dark Hearts)", ["Dark Hearts", "Coeurs noirs"]),
            ("Riding in Darkness (Nattryttarna)", ["Riding in Darkness", "Nattryttarna"]),
            ("Under a Dark Sun (Qui Seme le Vent)", ["Under a Dark Sun", "Qui sème le vent"]),
        ]
        for site_title, names in cases:
            for name in names:
                with self.subTest(site_title=site_title, name=name):
                    self.assertTrue(match(site_title, name, 2017))

        # Each name must match whole: a word of it, or a title that only contains it, is not enough.
        for site_title, name in [
            ("Girls Incarcerated: Young and Locked Up", "Locked Up"),
            ("Vis a vis: El oasis", "Vis a vis"),
            ("Money Heist: Korea - Joint Economic Area", "Money Heist"),
            ("Coeurs Noirs (Dark Hearts)", "Dark"),
            ("Riding in Darkness (Nattryttarna)", "Darkness"),
            ("The Bureau (Le Bureau des Legendes)", "Bureau"),
            # A "(US)" or "(2005)" qualifier is no alternative title.
            ("Shameless (US)", "US"),
            ("Doctor Who (2005)", "2005"),
        ]:
            with self.subTest(site_title=site_title, name=name):
                self.assertFalse(match(site_title, name, 2017))

    def test_find_show_paths_takes_a_show_by_either_name_of_its_bracketed_title(self):
        def options(*rows):
            return (
                '<select name="Mov_sel">'
                + "".join(f'<option value="/{path}">{title}</option>' for path, title in rows)
                + "</select>"
            ).encode("utf-8")

        # Live suggestion lists, searched by the English name.
        locked_up = options(
            ("tv-series/girls-incarcerated-young-and-locked-up/sea3eb5e822", "Girls Incarcerated: Young and Locked Up"),
            ("tv-series/vis-a-vis-locked-up/s062203e450", "Vis a Vis (Locked Up)"),
        )
        money_heist = options(
            ("tv-series/money-heist-korea-joint-economic-area/sbb1b6878b7", "Money Heist: Korea - Joint Economic Area"),
            ("tv-series/la-casa-de-papel-money-heist/saa5d0cc71e", "La Casa de Papel (Money Heist)"),
        )
        the_bureau = options(
            ("tv-series/the-bureau-le-bureau-des-legendes/s16836225cc", "The Bureau (Le Bureau des Legendes)"),
        )
        dark = options(
            ("tv-series/the-dark/s619e9d8a2b", "The Dark"),
            ("tv-series/under-a-dark-sun-qui-seme-le-vent/s3a62a45c73", "Under a Dark Sun (Qui Seme le Vent)"),
            ("tv-series/coeurs-noirs-dark-hearts-/s2bb340bcce", "Coeurs Noirs (Dark Hearts)"),
            ("tv-series/riding-in-darkness-nattryttarna/s4f23d145ce", "Riding in Darkness (Nattryttarna)"),
            ("tv-series/dark/s2cfc74f10a", "Dark"),
            ("tv-series/in-the-dark/s23ca1f8017", "In the Dark"),
        )
        cases = [
            ({"series": "Locked Up", "year": 2015}, locked_up, ["tv-series/vis-a-vis-locked-up/s062203e450"]),
            ({"series": "Money Heist", "year": 2017}, money_heist, ["tv-series/la-casa-de-papel-money-heist/saa5d0cc71e"]),
            ({"series": "The Bureau", "year": 2015}, the_bureau, ["tv-series/the-bureau-le-bureau-des-legendes/s16836225cc"]),
            (
                {"series": "Le Bureau des Légendes", "year": 2015},
                the_bureau,
                ["tv-series/the-bureau-le-bureau-des-legendes/s16836225cc"],
            ),
            # "Dark" names no bracketed show here, only the show called just that.
            ({"series": "Dark", "year": 2017}, dark, ["tv-series/dark/s2cfc74f10a"]),
            ({"series": "Dark Hearts", "year": 2025}, dark, ["tv-series/coeurs-noirs-dark-hearts-/s2bb340bcce"]),
        ]
        for video, html, expected in cases:
            with self.subTest(series=video["series"]):
                paths, _calls = self._find_show_paths(video, html)
                self.assertEqual(paths, expected)

    def _find_show_paths(self, video, suggestions_html):
        provider = self.mod.Subs4SeriesProvider()
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            return suggestions_html

        provider._http_get = stub
        return [path for path, _exact in provider._find_shows({"kind": "episode", **video}, {})], calls

    def test_find_show_paths_takes_a_site_title_with_a_country_qualifier(self):
        html = b'<select name="Mov_sel"><option value="/tv-series/shameless-us/s8a71806125">Shameless (US)</option></select>'

        paths, _calls = self._find_show_paths({"series": "Shameless", "year": 2011}, html)
        self.assertEqual(paths, ["tv-series/shameless-us/s8a71806125"])

        paths, _calls = self._find_show_paths({"series": "Shameless (UK)", "year": 2004}, html)
        self.assertEqual(paths, [])

    def test_find_show_paths_takes_a_plain_site_title_for_a_sonarr_country_title(self):
        paths, _calls = self._find_show_paths({"series": "The Office (US)", "year": 2005}, OFFICE_SUGGESTIONS_HTML)

        # Both shows named "The Office" qualify here; the episode page years pick the 2005 one.
        self.assertEqual(paths, ["tv-series/the-office-/s2361cdce2f", "tv-series/the-office/s4fcba70b44"])

    def test_find_show_paths_prefers_an_exact_title_over_a_qualified_one(self):
        html = (
            b'<select name="Mov_sel">'
            b'<option value="/tv-series/shameless-us/s8a71806125">Shameless (US)</option>'
            b'<option value="/tv-series/shameless/s1234567890">Shameless</option>'
            b"</select>"
        )

        # Without a video year an exact title wins outright.
        paths, _calls = self._find_show_paths({"series": "Shameless"}, html)
        self.assertEqual(paths, ["tv-series/shameless/s1234567890"])

        # With one, both go on to compete by page year (see the search tests).
        paths, _calls = self._find_show_paths({"series": "Shameless", "year": 2004}, html)
        self.assertEqual(paths, ["tv-series/shameless/s1234567890", "tv-series/shameless-us/s8a71806125"])

    def test_find_show_paths_takes_dated_site_titles_only_for_their_year(self):
        html = (
            b'<select name="Mov_sel">'
            b'<option value="/tv-series/doctor-who-2023/s1">Doctor Who (2023)</option>'
            b'<option value="/tv-series/doctor-who-2005/s2">Doctor Who (2005)</option>'
            b'<option value="/tv-series/doctor-who-1963/s3">Doctor Who (1963)</option>'
            b"</select>"
        )

        paths, _calls = self._find_show_paths({"series": "Doctor Who", "year": 2005}, html)
        self.assertEqual(paths, ["tv-series/doctor-who-2005/s2"])

        paths, _calls = self._find_show_paths({"series": "Doctor Who"}, html)
        self.assertEqual(paths, [])

    def test_search_finds_the_sonarr_country_title_on_the_plain_site_title(self):
        provider = self.mod.Subs4SeriesProvider()
        responses = {
            "https://www.subs4series.com/search_report.php?search=The+Office+%28US%29&searchType=1": OFFICE_SUGGESTIONS_HTML,
            OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024),
            OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005),
        }

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if url not in responses:
                if _is_season_listing(url):
                    return EMPTY_LISTING_HTML
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "The Office (US)", "year": 2005, "season": 1, "episode": 1},
            [{"alpha3": "ell", "alpha2": "el"}],
            {},
        )

        self.assertTrue(results)
        self.assertEqual({item["provider_payload"]["page_link"] for item in results}, {OFFICE_2005_EPISODE_URL})
        # The release names lack "US", yet the show was picked by title and year, so its
        # subtitles keep the series match instead of falling to 40.
        self.assertTrue(all("series" in item["matches"] for item in results))
        self.assertEqual({item["score"] for item in results}, {100})

    def _search_with_pages(self, video, responses):
        provider = self.mod.Subs4SeriesProvider()

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if url not in responses:
                if _is_season_listing(url):
                    return EMPTY_LISTING_HTML
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        return provider.search({"kind": "episode", "season": 1, "episode": 1, **video}, [{"alpha3": "ell", "alpha2": "el"}], {})

    def test_search_drops_another_countrys_shows_found_without_the_qualifier(self):
        # The site has no British "The Office": its two shows of that name are dated 2005 and
        # 2024, both far from 2001, so a UK video gets nothing rather than US subtitles.
        results = self._search_with_pages(
            {"series": "The Office (UK)", "year": 2001},
            {
                "https://www.subs4series.com/search_report.php?search=The+Office+%28UK%29&searchType=1": OFFICE_SUGGESTIONS_HTML,
                OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024),
                OFFICE_2005_EPISODE_URL: _episode_html_of_year(2005),
            },
        )

        self.assertEqual(results, [])

    def test_search_drops_a_lone_qualified_site_show_of_another_year(self):
        # "Shameless" 2004 is the British show; the site's only match is "Shameless (US)" (2011).
        suggestions = b'<select name="Mov_sel"><option value="/tv-series/shameless-us/s8a71806125">Shameless (US)</option></select>'
        responses = {
            "https://www.subs4series.com/search_report.php?search=Shameless&searchType=1": suggestions,
            "https://www.subs4series.com/tv-series/shameless-us/s8a71806125/season-1/episode-1": _episode_html_of_year(2011),
        }

        self.assertEqual(self._search_with_pages({"series": "Shameless", "year": 2004}, responses), [])
        self.assertTrue(self._search_with_pages({"series": "Shameless", "year": 2011}, responses))
        # Without a video year there is nothing to check the show against, so it is kept.
        self.assertTrue(self._search_with_pages({"series": "Shameless"}, responses))

    def test_search_rejects_a_qualifier_only_match_without_a_header_year(self):
        suggestions = b'<select name="Mov_sel"><option value="/tv-series/shameless-us/s8a71806125">Shameless (US)</option></select>'
        responses = {
            "https://www.subs4series.com/search_report.php?search=Shameless&searchType=1": suggestions,
            "https://www.subs4series.com/tv-series/shameless-us/s8a71806125/season-1/episode-1": EPISODE_HTML.replace(b"</u> (2011)", b"</u>"),
        }

        self.assertEqual(self._search_with_pages({"series": "Shameless", "year": 2004}, responses), [])

    def test_search_lets_an_exact_and_a_qualified_show_compete_by_year(self):
        # A site listing both "Shameless" (UK, 2004) and "Shameless (US)" (2011): the exact
        # title must not win for a 2011 video.
        suggestions = (
            b'<select name="Mov_sel">'
            b'<option value="/tv-series/shameless/s1234567890">Shameless</option>'
            b'<option value="/tv-series/shameless-us/s8a71806125">Shameless (US)</option>'
            b"</select>"
        )
        uk_url = "https://www.subs4series.com/tv-series/shameless/s1234567890/season-1/episode-1"
        us_url = "https://www.subs4series.com/tv-series/shameless-us/s8a71806125/season-1/episode-1"
        responses = {
            "https://www.subs4series.com/search_report.php?search=Shameless&searchType=1": suggestions,
            uk_url: _episode_html_of_year(2004),
            us_url: _episode_html_of_year(2011).replace(b"/greek-subtitles/s277e869f4/", b"/greek-subtitles/s2011000000/"),
        }

        def shows(video):
            return {item["provider_payload"]["page_link"] for item in self._search_with_pages(video, responses)}

        self.assertEqual(shows({"series": "Shameless", "year": 2011}), {us_url})
        self.assertEqual(shows({"series": "Shameless", "year": 2012}), {us_url})
        self.assertEqual(shows({"series": "Shameless", "year": 2004}), {uk_url})
        self.assertEqual(shows({"series": "Shameless"}), {uk_url})
        # No show within a year: the exact title is kept, as before.
        self.assertEqual(shows({"series": "Shameless", "year": 1990}), {uk_url})

    def test_search_keeps_every_exact_title_show_when_none_is_near_the_year(self):
        # The 2005 page gets its own subtitle links, or deduplication would hide it.
        page_2005 = _episode_html_of_year(2005).replace(b"/greek-subtitles/s277e869f4/", b"/greek-subtitles/s2005000000/")
        results = self._search_with_pages(
            {"series": "The Office", "year": 2001},
            {
                "https://www.subs4series.com/search_report.php?search=The+Office&searchType=1": OFFICE_SUGGESTIONS_HTML,
                OFFICE_2024_EPISODE_URL: _episode_html_of_year(2024),
                OFFICE_2005_EPISODE_URL: page_2005,
            },
        )

        self.assertEqual(
            {item["provider_payload"]["page_link"] for item in results},
            {OFFICE_2024_EPISODE_URL, OFFICE_2005_EPISODE_URL},
        )

    def test_search_keeps_the_series_match_for_a_show_found_by_an_alternative_title(self):
        provider = self.mod.Subs4SeriesProvider()
        responses = {
            "https://www.subs4series.com/search_report.php?search=Le+Trone+de+fer&searchType=1": b"<html></html>",
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1": SUGGESTIONS_HTML,
            "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1": EPISODE_HTML,
            "https://www.subs4series.com/tv-series/game-of-thrones-2011/s1111111111/season-1/episode-1": EPISODE_HTML,
        }

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if url not in responses:
                if _is_season_listing(url):
                    return EMPTY_LISTING_HTML
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Le Trone de fer",
                "alternative_series": ["Game of Thrones"],
                "year": 2011,
                "season": 1,
                "episode": 1,
            },
            [{"alpha3": "ell", "alpha2": "el"}],
            {},
        )

        self.assertTrue(results)
        self.assertEqual({item["score"] for item in results}, {100})

    def test_derive_matches_alone_still_needs_the_series_words(self):
        item = {"series_title": "The Office", "release_info": "The Office 1x01 - Pilot"}
        video = {"series": "The Office (US)", "season": 1, "episode": 1}

        self.assertNotIn("series", self.mod.derive_matches(video, item))
        self.assertIn("series", self.mod.derive_matches(video, item, show_matched=True))
        self.assertEqual(self.mod.compute_score(video, item), 40)
        self.assertEqual(self.mod.compute_score(video, item, show_matched=True), 100)

    SHOWNAME_SUGGESTIONS_HTML = b'<select name="Mov_sel"><option value="/tv-series/showname/s20a8bc7f2d">Showname</option></select>'
    SHOWNAME_EPISODE_URL = "https://www.subs4series.com/tv-series/showname/s20a8bc7f2d/season-1/episode-1"
    SHOWNAME_LISTING_URL = "https://www.subs4series.com/tv-series/showname/s20a8bc7f2d/season-1"

    def _showname_search(self, listing_pages, episode_rows=(("Showname S01E01 - Pilot - HDTV", "sae63f4455"),), episode_page=None, episode=1):
        """Search Showname S01E<episode>; episode_page, when given, replaces the episode page (a body or an error)."""
        provider = self.mod.Subs4SeriesProvider()
        calls = []
        episode_url = self.SHOWNAME_EPISODE_URL.replace("/episode-1", f"/episode-{episode}")
        responses = {
            "https://www.subs4series.com/search_report.php?search=Showname&searchType=1": self.SHOWNAME_SUGGESTIONS_HTML,
            episode_url: _listing_html(episode_rows) if episode_page is None else episode_page,
            **listing_pages,
        }

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            if isinstance(responses[url], Exception):
                raise responses[url]
            return responses[url]

        provider._http_get = stub
        video = {"kind": "episode", "series": "Showname", "season": 1, "episode": episode, "release_group": "GRP", "source": "Blu-Ray"}
        results = provider.search(video, [{"alpha3": "ell", "alpha2": "el"}], {})
        return {item["release_info"]: item for item in results}, calls

    def test_search_reads_the_season_listing_when_the_episode_has_no_page(self):
        # Nobody filed a subtitle under episode 7, so its page is a 404, but a pack holds it.
        missing = urllib.error.HTTPError(self.SHOWNAME_EPISODE_URL.replace("/episode-1", "/episode-7"), 404, "Not Found", {}, None)
        self.addCleanup(missing.close)
        results, calls = self._showname_search(
            {
                self.SHOWNAME_LISTING_URL: _listing_html(
                    [("Showname S01 1080p BluRay x264-GRP", "s5dcc7874d4"), ("Showname S01E03 HDTV", "s3")]
                )
            },
            episode_page=missing,
            episode=7,
        )

        # The listing is read once, for the show's year and for its packs.
        self.assertEqual(calls[1:], [self.SHOWNAME_EPISODE_URL.replace("/episode-1", "/episode-7"), self.SHOWNAME_LISTING_URL])
        self.assertEqual(set(results), {"Showname S01 1080p BluRay x264-GRP"})
        pack = results["Showname S01 1080p BluRay x264-GRP"]
        self.assertTrue(pack["provider_payload"]["season_pack"])
        self.assertEqual(pack["provider_payload"]["episode"], 7)
        self.assertEqual(pack["score"], 100)

    def test_search_returns_nothing_when_neither_the_episode_nor_the_season_has_a_page(self):
        missing_episode = urllib.error.HTTPError(self.SHOWNAME_EPISODE_URL, 404, "Not Found", {}, None)
        missing_season = urllib.error.HTTPError(self.SHOWNAME_LISTING_URL, 404, "Not Found", {}, None)
        self.addCleanup(missing_episode.close)
        self.addCleanup(missing_season.close)
        results, calls = self._showname_search({self.SHOWNAME_LISTING_URL: missing_season}, episode_page=missing_episode)

        self.assertEqual(results, {})
        self.assertEqual(calls[1:], [self.SHOWNAME_EPISODE_URL, self.SHOWNAME_LISTING_URL])

    def test_search_skips_a_partial_pack_without_the_episode(self):
        results, _calls = self._showname_search(
            {
                self.SHOWNAME_LISTING_URL: _listing_html(
                    [("Showname S01E01-E10 720p WEB", "sa"), ("Showname S01E11-E20 720p WEB", "sb")]
                )
            },
            episode_rows=(),
            episode=15,
        )

        self.assertEqual(set(results), {"Showname S01E11-E20 720p WEB"})

    def test_search_raises_a_block_on_the_season_listing(self):
        for error in (
            self.mod.CloudflareBlockedError("subs4series hit a Cloudflare block"),
            self.mod.ServiceUnavailable("subs4series Anubis stage=request budget_exhausted"),
        ):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self._showname_search({self.SHOWNAME_LISTING_URL: error})

    def test_search_keeps_the_episode_results_when_the_listing_fails_otherwise(self):
        unavailable = urllib.error.HTTPError(self.SHOWNAME_LISTING_URL, 503, "Service Unavailable", {}, None)
        with self.assertLogs(self.mod.logger, "WARNING"):
            results, _calls = self._showname_search({self.SHOWNAME_LISTING_URL: unavailable})

        self.assertEqual(set(results), {"Showname S01E01 - Pilot - HDTV"})

    def test_search_adds_the_season_packs_from_the_season_listing(self):
        # Season 1 as listed live: packs filed under episode 0 and under no episode (both
        # on the last page) and under episode 24 (first page), among other episodes' subtitles.
        results, calls = self._showname_search({
            self.SHOWNAME_LISTING_URL: _listing_html(
                [
                    ("Showname S01E24-25 - Finale (2) - HDTV", "sb737b0abb"),
                    ("Showname S01 DVDRip XviD-ABC [GR]", "s18386e2803"),
                    ("Showname 3x17 {GR} [hdtv-xyz]", "sdb36752571"),
                ],
                last_page=1,
            ),
            f"{self.SHOWNAME_LISTING_URL}/p-1": _listing_html(
                [
                    ("Showname S01E01 - Pilot - HDTV", "sae63f4455"),
                    ("Showname S01E0 -  - 1080p-GRP", "saf95977f9"),
                    ("Showname S01 1080p BluRay x264-GRP", "s5dcc7874d4"),
                ]
            ),
        })

        self.assertEqual(calls[1:], [self.SHOWNAME_EPISODE_URL, self.SHOWNAME_LISTING_URL, f"{self.SHOWNAME_LISTING_URL}/p-1"])
        self.assertEqual(
            set(results),
            {
                "Showname S01E01 - Pilot - HDTV",
                "Showname S01 DVDRip XviD-ABC [GR]",
                "Showname S01E0 - - 1080p-GRP",
                "Showname S01 1080p BluRay x264-GRP",
            },
        )
        episode = results["Showname S01E01 - Pilot - HDTV"]
        self.assertNotIn("season_pack", episode["provider_payload"])
        self.assertEqual(episode["provider_payload"]["page_link"], self.SHOWNAME_EPISODE_URL)
        pack = results["Showname S01 1080p BluRay x264-GRP"]
        self.assertEqual(set(pack["matches"]), {"series", "season", "episode", "release_group", "source"})
        self.assertEqual(pack["score"], 100)
        # The host takes episode 1 out of the pack, so the payload names it, not the site's 0.
        self.assertEqual(pack["provider_payload"]["season"], 1)
        self.assertEqual(pack["provider_payload"]["episode"], 1)
        self.assertIs(pack["provider_payload"]["season_pack"], True)
        self.assertEqual(pack["provider_payload"]["page_link"], self.SHOWNAME_LISTING_URL)
        self.assertIn(".s01e01.", pack["filename"])
        self.assertEqual(results["Showname S01E0 - - 1080p-GRP"]["score"], 100)

    def test_search_marks_a_pack_filed_under_the_episode_itself(self):
        results, _calls = self._showname_search(
            {self.SHOWNAME_LISTING_URL: EMPTY_LISTING_HTML},
            episode_rows=(("Showname S01 DVDRip XviD-ABC [GR]", "s18386e2803"),),
        )

        pack = results["Showname S01 DVDRip XviD-ABC [GR]"]
        self.assertIs(pack["provider_payload"]["season_pack"], True)
        self.assertIn("season", pack["matches"])
        self.assertIn("episode", pack["matches"])

    def test_search_keeps_the_episode_subtitles_when_the_season_listing_fails(self):
        unavailable = urllib.error.HTTPError(self.SHOWNAME_LISTING_URL, 503, "Service Unavailable", {}, io.BytesIO())

        with self.assertLogs(self.mod.logger, "WARNING"):
            results, _calls = self._showname_search({self.SHOWNAME_LISTING_URL: unavailable})

        self.assertEqual(set(results), {"Showname S01E01 - Pilot - HDTV"})

    def test_extract_download_takes_the_episode_out_of_a_season_pack(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for number in range(1, 4):
                archive.writestr(f"Showname.S01E{number:02d}.1080p.BluRay.x264-GRP.srt", f"1\r\nepisode {number}\r\n")
        payload = {"season": 1, "episode": 2, "season_pack": True, "release_info": "Showname S01 1080p BluRay x264-GRP"}

        result = self.mod.extract_download(stream.getvalue(), payload)

        self.assertEqual(result["member"], "Showname.S01E02.1080p.BluRay.x264-GRP.srt")

    def test_search_rejects_movies(self):
        provider = self.mod.Subs4SeriesProvider()

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Game of Thrones"}, [{"alpha3": "ell"}], {}),
            [],
        )

    def test_download_fetches_getsub_page_then_posts_form_action(self):
        # The detail page only links to the getSub page; the provider must fetch that page
        # (after the anti-block GETs) and POST the form action found there, not the detail link.
        provider = self.mod.Subs4SeriesProvider()
        seen_gets = []
        posts = []

        def get_stub(url, timeout=15, referer=None, config=None):
            del timeout, config
            seen_gets.append((url, referer))
            if url == DETAIL_URL:
                return DETAIL_PAGE_HTML
            if url == GETSUB_FIRST_URL:
                return DIRECT_GETSUB_HTML
            if "anti-block" in url:
                return b"ok"
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, data, timeout=15, referer=None):
            del timeout
            posts.append((url, data, referer))
            return _zip_body()

        provider._http_get = get_stub
        provider._http_post = post_stub
        result = provider.download(
            {
                "detail_url": DETAIL_URL,
                "page_link": "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1",
                "filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.el.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"request_delay_ms": 0},
        )

        # the getSub page was fetched, and the anti-block GETs ran before it
        self.assertIn(GETSUB_FIRST_URL, [g[0] for g in seen_gets])
        self.assertTrue(any("anti-block-layover.php" in item[0] for item in seen_gets))
        # POST went to the form action on the getSub page, refered from the getSub page
        self.assertEqual(posts[0][0], "https://www.subs4series.com/getSub-final.html")
        self.assertEqual(posts[0][1]["my_recaptcha_challenge_field"], "manual_challenge")
        self.assertEqual(posts[0][2], GETSUB_FIRST_URL)
        # Archive mode: the worker forwards the raw zip and carries episode for the host.
        body = _zip_body()
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_posts_captcha_response_when_required(self):
        # reCAPTCHA lives on the getSub page: solve it there and POST that page's form action.
        provider = self.mod.Subs4SeriesProvider()
        posts = []

        def get_stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if url == DETAIL_URL:
                return DETAIL_PAGE_HTML
            if url == GETSUB_FIRST_URL:
                return CAPTCHA_DOWNLOAD_HTML
            if "anti-block" in url:
                return b"ok"
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, data, timeout=15, referer=None):
            del timeout
            posts.append((url, data, referer))
            return _zip_body()

        provider._http_get = get_stub
        provider._http_post = post_stub
        result = provider.download(
            {
                "detail_url": DETAIL_URL,
                "page_link": "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1",
                "filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.el.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"captcha_response": "solved-token", "request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4series.com/getSub-captcha-token.html")
        self.assertEqual(posts[0][1]["g-recaptcha-response"], "solved-token")
        self.assertEqual(posts[0][1]["recaptcha_response"], "solved-token")
        self.assertEqual(base64.b64decode(result["archive_b64"]), _zip_body())
        self.assertEqual(result["episode"], 1)

    def _gated_download(self, getsub_bodies, config=None, post_body=None):
        # Serves each getSub fetch the next body from getsub_bodies, and records the
        # detail page loads, getSub fetches, POSTs and sleeps the download makes.
        provider = self.mod.Subs4SeriesProvider()
        calls = {"detail": 0, "getsub": 0, "posts": [], "sleeps": []}
        bodies = list(getsub_bodies)

        def get_stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if url == DETAIL_URL:
                calls["detail"] += 1
                return DETAIL_PAGE_HTML
            if url == GETSUB_FIRST_URL:
                calls["getsub"] += 1
                return bodies.pop(0)
            if "anti-block" in url:
                return b"ok"
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, data, timeout=15, referer=None):
            del timeout, referer
            calls["posts"].append((url, data))
            return post_body if post_body is not None else _zip_body()

        provider._http_get = get_stub
        provider._http_post = post_stub
        payload = {
            "detail_url": DETAIL_URL,
            "page_link": "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1",
            "filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.el.zip",
            "season": 1,
            "episode": 1,
        }
        with patch.object(self.mod.time, "sleep", side_effect=calls["sleeps"].append):
            result = provider.download(payload, {"alpha3": "ell", "alpha2": "el"}, {"request_delay_ms": 0, **(config or {})})
        return result, calls

    def test_download_takes_a_fresh_token_once_when_an_unsolvable_captcha_shows(self):
        # The gate is occasional now: without a solver, reload the detail page for a new
        # getSub token, and the second getSub answers with the archive.
        result, calls = self._gated_download([CAPTCHA_DOWNLOAD_HTML, _zip_body()])

        self.assertEqual(base64.b64decode(result["archive_b64"]), _zip_body())
        self.assertEqual(result["episode"], 1)
        self.assertEqual((calls["detail"], calls["getsub"]), (2, 2))
        self.assertEqual(calls["sleeps"], [self.mod.CAPTCHA_RETRY_DELAY_SECONDS])
        self.assertEqual(calls["posts"], [])

    def test_download_reports_a_captcha_that_stays_as_throttled(self):
        gate = CAPTCHA_DOWNLOAD_HTML.replace(b"<form", b"<title>Subs4Series - Download</title><form", 1)

        with self.assertRaises(self.mod.APIThrottled) as raised:
            self._gated_download([gate, gate])

        message = str(raised.exception)
        self.assertIn("reCAPTCHA", message)
        self.assertIn("'Subs4Series - Download'", message)
        self.assertIn("captcha_solver_url", message)

    def test_download_with_a_solver_does_not_wait_for_the_captcha_to_clear(self):
        result, calls = self._gated_download([CAPTCHA_DOWNLOAD_HTML], config={"captcha_response": "solved-token"})

        self.assertEqual(base64.b64decode(result["archive_b64"]), _zip_body())
        self.assertEqual((calls["detail"], calls["getsub"]), (1, 1))
        self.assertEqual(calls["sleeps"], [])
        self.assertEqual(calls["posts"][0][1]["g-recaptcha-response"], "solved-token")

    def test_download_tells_the_solver_whether_the_recaptcha_is_invisible(self):
        # The live gate is a visible v2 checkbox; the solver used to be told it was invisible.
        invisible_gate = CAPTCHA_DOWNLOAD_HTML.replace(b'class="g-recaptcha"', b'class="g-recaptcha" data-size="invisible"', 1)
        self.assertNotEqual(invisible_gate, CAPTCHA_DOWNLOAD_HTML)
        for gate, expected in ((CAPTCHA_DOWNLOAD_HTML, False), (invisible_gate, True)):
            with self.subTest(invisible=expected):
                payloads = []

                def raw_stub(self, url, method="GET", data=None, headers=None, timeout=15):
                    del self, url, method, headers, timeout
                    payloads.append(json.loads(data.decode("utf-8")))
                    return b'{"token": "solved"}'

                with patch.object(self.mod.Subs4SeriesProvider, "_raw_request", raw_stub):
                    result, calls = self._gated_download([gate], config={"captcha_solver_url": "http://solver.local/solve"})

                self.assertEqual(payloads[0]["invisible"], expected)
                self.assertEqual(payloads[0]["site_key"], "site-key-123")
                self.assertEqual(calls["posts"][0][1]["g-recaptcha-response"], "solved")
                self.assertEqual(base64.b64decode(result["archive_b64"]), _zip_body())

    def test_download_fails_clearly_when_the_solver_returns_no_token(self):
        provider_raw = []

        def raw_stub(self, url, method="GET", data=None, headers=None, timeout=15):
            del self, method, data, headers, timeout
            provider_raw.append(url)
            return b'{"error": "unsolved"}'

        with patch.object(self.mod.Subs4SeriesProvider, "_raw_request", raw_stub):
            with self.assertRaisesRegex(ValueError, "no response token"):
                self._gated_download([CAPTCHA_DOWNLOAD_HTML], config={"captcha_solver_url": "http://solver.local/solve"})

        self.assertEqual(provider_raw, ["http://solver.local/solve"])

    def test_create_cloudscraper_uses_ai_cloudscraper_options(self):
        scraper = object()

        with patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper) as create_scraper:
            result = self.mod._create_cloudscraper()

        self.assertIs(result, scraper)
        create_scraper.assert_called_once_with(
            browser={"custom": self.mod.USER_AGENT},
            interpreter="native",
            enable_cookie_persistence=False,
            debug=False,
        )

    def test_create_cloudscraper_retries_without_cookie_persistence_for_legacy_ai_cloudscraper(self):
        scraper = object()

        with patch.object(
            self.mod.cloudscraper,
            "create_scraper",
            side_effect=[
                TypeError("unexpected keyword argument 'enable_cookie_persistence'"),
                scraper,
            ],
        ) as create_scraper:
            result = self.mod._create_cloudscraper()

        self.assertIs(result, scraper)
        self.assertEqual(create_scraper.call_count, 2)
        self.assertEqual(create_scraper.call_args_list[0].kwargs["enable_cookie_persistence"], False)
        self.assertNotIn("enable_cookie_persistence", create_scraper.call_args_list[1].kwargs)

    def test_http_get_solves_anubis_inline_before_retrying_original_url(self):
        provider = self.mod.Subs4SeriesProvider()
        challenge_response = FakeScraperResponse(
            401,
            {},
            b'<script id="anubis_challenge">{}</script>',
            url="https://www.subs4series.com/.within.website/?redir=/search_report.php",
        )
        scraper = FakeScraper(
            [
                challenge_response,
                FakeScraperResponse(200, {}, b"<option>Game of Thrones</option>"),
            ]
        )
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, timeout, deadline=None):
            solved_calls.append((active_scraper, challenge_url, timeout, deadline - time.monotonic()))
            return {"techaro.lol-anubis-auth": "ok"}

        with patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), patch.object(
            self.mod,
            "solve_anubis_challenge",
            side_effect=fake_solve,
        ):
            body = provider._http_get("https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1")

        self.assertIn(b"Game of Thrones", body)
        self.assertEqual(len(scraper.calls), 2)
        self.assertEqual(
            scraper.calls[0][0],
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
        )
        self.assertEqual(
            scraper.calls[1][0],
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
        )
        self.assertIs(solved_calls[0][0], scraper)
        self.assertEqual(
            solved_calls[0][1],
            "https://www.subs4series.com/.within.website/?redir=/search_report.php",
        )
        # The solve gets the call's 60 s budget, not a fresh one.
        self.assertTrue(55 < solved_calls[0][3] <= self.mod.SITE_CALL_BUDGET_SECONDS)

    def test_http_get_solves_anubis_body_before_retrying_original_url(self):
        provider = self.mod.Subs4SeriesProvider()
        anubis_body = (
            b'<html><head><meta http-equiv="refresh" '
            b'content="0; url=/.within.website/?redir=/search_report.php"></head></html>'
        )
        scraper = FakeScraper(
            [
                FakeScraperResponse(
                    200,
                    {},
                    anubis_body,
                    url="https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
                ),
                FakeScraperResponse(200, {}, b"<option>Game of Thrones</option>"),
            ]
        )
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, timeout, deadline=None):
            solved_calls.append((active_scraper, challenge_url, timeout, deadline - time.monotonic()))
            return {"techaro.lol-anubis-auth": "ok"}

        with patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), patch.object(
            self.mod,
            "solve_anubis_challenge",
            side_effect=fake_solve,
        ):
            body = provider._http_get("https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1")

        self.assertIn(b"Game of Thrones", body)
        self.assertEqual(len(scraper.calls), 2)
        self.assertIs(solved_calls[0][0], scraper)
        self.assertEqual(
            solved_calls[0][1],
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
        )

    def test_anubis_proof_of_work_stops_at_the_call_budget(self):
        page = FakeScraperResponse(
            200, {}, b'<script id="anubis_challenge">{"challenge": {"id": "c1", "randomData": "abc", "difficulty": 64}}</script>'
        )
        session = _AnubisSession([page])
        started = time.monotonic()

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "proof_of_work"):
            self.mod.solve_anubis_challenge(
                session, "https://www.subs4series.com/.within.website/?redir=/", deadline=started + 0.2
            )

        self.assertLess(time.monotonic() - started, 5)

    def test_anubis_wait_longer_than_the_budget_aborts_without_sleeping(self):
        page = FakeScraperResponse(
            200, {}, b'<meta http-equiv="refresh" content="999; url=/.within.website/x/cmd/anubis/api/pass-challenge?x=1">'
        )
        session = _AnubisSession([page])
        sleeps = []

        with patch.object(self.mod.time, "sleep", side_effect=sleeps.append):
            with self.assertRaisesRegex(self.mod.ServiceUnavailable, "pass_submit"):
                self.mod.solve_anubis_challenge(session, "https://www.subs4series.com/.within.website/?redir=/")

        self.assertEqual(sleeps, [])
        self.assertEqual(len(session.calls), 1)

    def test_anubis_requests_get_only_what_is_left_of_the_budget(self):
        page = FakeScraperResponse(
            200, {}, b'<meta http-equiv="refresh" content="0; url=/.within.website/x/cmd/anubis/api/pass-challenge?x=1">'
        )
        session = _AnubisSession([page, FakeScraperResponse(200, {}, b"ok")])

        self.mod.solve_anubis_challenge(
            session, "https://www.subs4series.com/.within.website/?redir=/", deadline=time.monotonic() + 3
        )

        self.assertEqual(len(session.calls), 2)
        for _url, (connect, read) in session.calls:
            self.assertLessEqual(connect, 3)
            self.assertLessEqual(read, 3)

    def test_extract_anubis_challenge_accepts_string_challenge(self):
        challenge = self.mod._extract_anubis_challenge(
            '<script id="anubis_challenge">'
            + json.dumps(
                {
                    "rules": {"algorithm": "slow", "difficulty": 7},
                    "challenge": "random-string",
                }
            )
            + "</script>"
        )

        self.assertEqual(challenge["id"], "random-string")
        self.assertEqual(challenge["randomData"], "random-string")
        self.assertEqual(challenge["difficulty"], 7)
        self.assertEqual(challenge["method"], "slow")

    def test_http_get_uses_flaresolverr_after_cloudflare_block(self):
        provider = self.mod.Subs4SeriesProvider()
        challenge_response = FakeScraperResponse(
            403,
            {"Server": "cloudflare"},
            b"<html><title>Attention Required! | Cloudflare</title></html>",
        )
        scraper = FakeScraper(challenge_response)
        flaresolverr_payload = {
            "status": "ok",
            "solution": {
                "status": 200,
                "response": "<option value='/tv-series/game-of-thrones/s8985ffc551'>Game of Thrones</option>",
                "userAgent": "Mozilla/5.0 solved",
                "cookies": [{"name": "cf_clearance", "value": "token"}],
            },
        }

        with patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), patch.object(
            provider._opener,
            "open",
            return_value=FakeUrlopenResponse(json.dumps(flaresolverr_payload).encode("utf-8")),
        ) as urlopen:
            body = provider._http_get(
                "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
                config={
                    "flaresolverr_url": "http://flaresolverr:8191/v1",
                    "flaresolverr_timeout_ms": 45000,
                },
                referer="https://www.subs4series.com",
            )

        self.assertIn(b"Game of Thrones", body)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://flaresolverr:8191/v1")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["cmd"], "request.get")
        self.assertEqual(payload["maxTimeout"], 25000)
        self.assertEqual(provider._flaresolverr_cookies["cf_clearance"], "token")
        self.assertEqual(provider._flaresolverr_user_agent, "Mozilla/5.0 solved")


if __name__ == "__main__":
    unittest.main()
