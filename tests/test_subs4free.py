import base64
import email.message
import email.utils
import hashlib
import importlib.util
import io
import time
import unittest
import urllib.error
import urllib.parse
import zipfile
from http.cookiejar import Cookie
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subs4free"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subs4free_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "subs4free_search_inception.html").read_bytes()
SUGGESTIONS_HTML = (FIXTURE_DIR / "subs4free_suggestions.html").read_bytes()
DOWNLOAD_HTML = (FIXTURE_DIR / "subs4free_download_page.html").read_bytes()
# Live Anubis v1.26.2 challenge page, trimmed to its Anubis parts; X-Real-Ip replaced.
CHALLENGE_HTML = (FIXTURE_DIR / "subs4free_anubis_challenge.html").read_bytes()


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(".hidden.srt", "ignore")
        archive.writestr("Inception.2010.1080p.BluRay.RARBG.srt", "one\r\ntwo\r\n")
    return stream.getvalue()


def _zip_body_with(name, content):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, content)
    return stream.getvalue()


def _zip_body_members(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name in names:
            archive.writestr(name, "1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n")
    return stream.getvalue()


def _row(language, release, identifier):
    if language == "ell":
        language_name = "Greek"
        path_language = "greek"
        sprite = "elgif"
    else:
        language_name = "English"
        path_language = "english"
        sprite = "engif"
    slug = release.lower().replace(" ", "-")
    return f"""
    <div class="movie-details">
      <div class="movie-info">
        <a class="movie-heading"
           href="/{path_language}-subtitles/{identifier}/{slug}"
           title="{language_name} subtitles for {release}">
          <div class="sprite {sprite}"></div>
          <span>{release}</span>
        </a>
        <p>Uploaded by <a href="/subtitles-by-u1.html">Uploader</a></p>
      </div>
      <div class="movie-download"><p><b>1</b>DLs</p></div>
    </div>
    """.encode("utf-8")


def _html_with_rows(rows):
    return b"<!doctype html><html><body>" + b"".join(rows) + b"</body></html>"


class Subs4FreeParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_rows(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["language"], "ell")
        self.assertEqual(rows[0]["alpha2"], "el")
        self.assertEqual(rows[0]["release_info"], "Inception 2010 1080p BluRay H264 AAC-RARBG [SubRip]")
        self.assertEqual(rows[0]["uploader"], "Nerddork")
        self.assertEqual(rows[0]["downloads"], 1699)
        self.assertEqual(
            rows[0]["detail_url"],
            "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception-2010-1080p-bluray-h264-aac-rarbg-subrip",
        )
        self.assertEqual(rows[1]["language"], "eng")

    def test_parse_suggestions_keeps_option_links(self):
        rows = self.mod.parse_suggestions(SUGGESTIONS_HTML)

        self.assertEqual(rows[0]["title"], "Inception 2010")
        self.assertEqual(
            rows[0]["url"],
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010",
        )

    def test_parse_suggestions_normalizes_legacy_mov_sel_query_links(self):
        rows = self.mod.parse_suggestions(
            b"""
            <select name="Mov_sel">
              <option value="?p=movie-details/m640fe75810/inception-2010">Inception 2010</option>
            </select>
            """
        )

        self.assertEqual(
            rows[0]["url"],
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010",
        )

    def test_parse_download_form_extracts_post_id_and_click_bounds(self):
        form = self.mod.parse_download_form(DOWNLOAD_HTML)

        self.assertEqual(form["id"], "tkMTc4MDI0MDQ5MTE4NTk1MjUxMTg2MTg5NjQ2MTg5")
        self.assertEqual(form["width"], 200)
        self.assertEqual(form["height"], 58)

    def test_extract_download_zip_returns_raw_archive_for_host(self):
        body = _zip_body()
        payload = self.mod.extract_download(body, {"filename": "inception.el.zip", "episode": None})

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertIsNone(payload["episode"])
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", payload)
        self.assertNotIn("member", payload)
        self.assertNotIn("encoding", payload)

    def test_extract_download_rar_returns_raw_archive_for_host(self):
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        payload = self.mod.extract_download(body, {"filename": "inception.el.rar", "episode": 3})

        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(payload["episode"], 3)
        self.assertNotIn("content_b64", payload)
        self.assertNotIn("encoding", payload)

    def test_extract_download_movie_archive_carries_none_episode(self):
        body = _zip_body_with("Inception.2010.srt", "one\r\ntwo\r\n")
        payload = self.mod.extract_download(body, {"filename": "inception.el.zip"})

        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertIsNone(payload["episode"])

    def test_extract_download_direct_subtitle_normalizes_line_endings(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n"
        payload = self.mod.extract_download(body, {"filename": "inception.el.srt"})

        self.assertEqual(payload["format"], "srt")
        self.assertEqual(payload["empty"], False)
        data = base64.b64decode(payload["content_b64"])
        self.assertEqual(data, b"1\n00:00:01,000 --> 00:00:02,000\nText\n")
        self.assertEqual(payload["content_sha256"], hashlib.sha256(data).hexdigest())
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", payload)
        self.assertNotIn("archive_b64", payload)

    def test_extract_download_pins_release_member_in_multi_member_zip(self):
        # A multi-member zip with no episode signal: reproduce the old release-based pick by
        # pinning the member whose tokens overlap the scored release_info.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.RARBG.srt",
            "Inception.2010.720p.WEBRip.YIFY.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay H264 AAC-RARBG",
                "episode": None,
            },
        )

        self.assertEqual(payload["member"], "Inception.2010.1080p.BluRay.RARBG.srt")
        self.assertNotIn("episode", payload)
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertNotIn("content_b64", payload)

    def test_extract_download_defers_when_no_unique_release_winner(self):
        # Two members tie on release_info overlap: cannot disambiguate, so defer to the host
        # episode path rather than pin a coin-flip member.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.srt",
            "Inception.2010.1080p.BluRay.Director.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay",
                "episode": None,
            },
        )

        self.assertNotIn("member", payload)
        self.assertIsNone(payload["episode"])

    def test_extract_download_does_not_pin_wrong_release_member(self):
        # The release_info points at the BluRay cut; the WEBRip member must never be pinned.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.RARBG.srt",
            "Different.Movie.2011.720p.WEBRip.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertEqual(payload["member"], "Inception.2010.1080p.BluRay.RARBG.srt")

    def test_extract_download_skips_sidecars_and_directories(self):
        # __MACOSX/dotfile sidecars and directory entries must not count as members: only the
        # real subtitle remains, so a lone member defers to the host (no pin).
        body = _zip_body_members([
            "subs/",
            "__MACOSX/._Inception.2010.BluRay.srt",
            ".DS_Store",
            "subs/Inception.2010.1080p.BluRay.RARBG.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertNotIn("member", payload)
        self.assertIsNone(payload["episode"])

    def test_extract_download_does_not_pin_forced_over_main(self):
        # A forced sidecar shares release tokens with the main track; the forced penalty keeps
        # the main subtitle as the pinned member.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.RARBG.srt",
            "Inception.2010.1080p.BluRay.RARBG.forced.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertEqual(payload["member"], "Inception.2010.1080p.BluRay.RARBG.srt")

    def test_extract_download_rar_multi_member_defers_to_host(self):
        # RAR is not stdlib-listable: never pin, always hand the archive to the host.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 64
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.rar",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertNotIn("member", payload)
        self.assertIsNone(payload["episode"])
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)

    def test_extract_download_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"   \r\n  ", {"filename": "inception.el.zip"})

    def test_extract_download_rejects_html_error_page(self):
        body = (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )
        with self.assertRaises(ValueError):
            self.mod.extract_download(body, {"filename": "inception.el.zip"})

    def test_extract_download_reports_penalty_bodies_as_throttle(self):
        # The two bodies getSub.php sent with its 302 while the anti-flood penalty was active.
        for body in (b"Refresh timorias [1]<BR>", b"Polla alla isxiei timoria...<BR>"):
            with self.subTest(body=body):
                with self.assertRaises(self.mod.APIThrottled):
                    self.mod.extract_download(body, {"filename": "inception.el.zip"})

    def test_extract_download_reports_bounce_to_detail_page_as_throttle(self):
        # Following the refusal's 302 lands back on the detail page with its getSub.php form.
        with self.assertRaises(self.mod.APIThrottled) as raised:
            self.mod.extract_download(DOWNLOAD_HTML, {"filename": "inception.el.zip"})
        # The worker maps the remote class name onto the host's APIThrottled.
        self.assertEqual(type(raised.exception).__name__, "APIThrottled")

    def test_extract_download_keeps_subtitles_that_quote_the_penalty_word(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nH timoria\r\n\r\n" + b"x" * 600
        payload = self.mod.extract_download(body, {"filename": "inception.el.srt"})
        self.assertIn("content_b64", payload)
        archive = _zip_body_with("Refresh.timorias.srt", "timoria")
        payload = self.mod.extract_download(archive, {"filename": "inception.el.zip"})
        self.assertEqual(base64.b64decode(payload["archive_b64"]), archive)


class Subs4FreeProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_movie_languages(self):
        provider = self.mod.Subs4FreeProvider()
        calls = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search_report.php" in url:
                return SEARCH_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "source": "Blu-ray",
                "resolution": "1080p",
                "video_codec": "H.264",
                "audio_codec": "AAC",
                "release_group": "RARBG",
            },
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertTrue(any("Inception+2010" in url for url in calls))
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        first = results[0]
        self.assertEqual(first["provider"], "subs4free")
        self.assertEqual(first["score"], 100)
        self.assertIn("title", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertIn("resolution", first["matches"])
        self.assertEqual(first["provider_payload"]["detail_url"], results[0]["page_link"])
        # Host-side member selection needs episode (and season) in the payload; movies carry None.
        self.assertIn("episode", first["provider_payload"])
        self.assertIsNone(first["provider_payload"]["episode"])
        self.assertIsNone(first["provider_payload"]["season"])

    def test_search_uses_matching_suggestion_pages_when_present(self):
        provider = self.mod.Subs4FreeProvider()
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": SUGGESTIONS_HTML,
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010": SEARCH_HTML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "ell")

    def test_search_normalizes_legacy_mov_sel_suggestion_pages(self):
        provider = self.mod.Subs4FreeProvider()
        suggestions = b"""
        <select name="Mov_sel">
          <option value="?p=movie-details/m640fe75810/inception-2010">Inception 2010</option>
        </select>
        """
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": suggestions,
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010": SEARCH_HTML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)

    def test_search_rejects_unrelated_direct_rows(self):
        provider = self.mod.Subs4FreeProvider()
        body = _html_with_rows([
            _row("eng", "Interstellar 2014 1080p BluRay", "s-interstellar"),
        ])

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if "search_report.php" in url:
                return body
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_search_applies_candidate_cap_after_language_filtering(self):
        provider = self.mod.Subs4FreeProvider()
        rows = [
            _row("ell", f"Inception 2010 Greek Release {index}", f"s-ell-{index}")
            for index in range(self.mod.MAX_CANDIDATES_PER_QUERY)
        ]
        rows.append(_row("eng", "Inception 2010 English Release", "s-eng"))
        body = _html_with_rows(rows)

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if "search_report.php" in url:
                return body
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "eng")

    def test_search_continues_fallback_queries_until_requested_languages_are_covered(self):
        provider = self.mod.Subs4FreeProvider()
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": _html_with_rows([
                _row("ell", "Inception 2010 Greek Release", "s-ell"),
            ]),
            "https://www.subs4free.info/search_report.php?search=Inception&searchType=1": _html_with_rows([
                _row("eng", "Inception 2010 English Release", "s-eng"),
            ]),
        }
        calls = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        self.assertTrue(any("search=Inception+2010" in url for url in calls))
        self.assertTrue(any("search=Inception&" in url for url in calls))

    def test_derive_matches_accepts_list_valued_codec_metadata(self):
        matches = self.mod.derive_matches(
            {
                "title": "Inception",
                "year": 2010,
                "audio_codec": ["DTS-HD", "MA"],
            },
            {"release_info": "Inception 2010 1080p BluRay"},
        )

        self.assertIn("title", matches)
        self.assertIn("year", matches)
        self.assertNotIn("audio_codec", matches)

    def test_search_accepts_release_year_when_title_contains_year_token(self):
        provider = self.mod.Subs4FreeProvider()
        body = _html_with_rows([
            _row("eng", "1917 2019 1080p BluRay", "s-1917"),
        ])

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if "search_report.php" in url:
                return body
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "1917", "year": 2019},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["release_info"], "1917 2019 1080p BluRay")

    def test_download_posts_form_straight_after_the_detail_page(self):
        provider = self.mod.Subs4FreeProvider()
        seen_gets = []
        posts = []

        def get_stub(url, timeout=15, referer=None):
            del timeout
            seen_gets.append((url, referer))
            if url == "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception":
                return DOWNLOAD_HTML
            raise AssertionError(f"unexpected GET: {url}")

        archive = _zip_body()

        def post_stub(url, data, timeout=15, referer=None):
            del timeout
            posts.append((url, data, referer))
            return archive

        provider._http_get = get_stub
        provider._http_post = post_stub
        result = provider.download(
            {
                "detail_url": "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception",
                "filename": "inception.el.zip",
                "episode": None,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4free.info/getSub.php")
        self.assertEqual(posts[0][1]["id"], "tkMTc4MDI0MDQ5MTE4NTk1MjUxMTg2MTg5NjQ2MTg5")
        self.assertEqual(posts[0][2], "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception")
        # No legacy anti-block calls (images.subs4free.info, subs4series.com): the only
        # GET is the detail page, and the POST follows it directly.
        self.assertEqual([url for url, _referer in seen_gets],
                         ["https://www.subs4free.info/greek-subtitles/s3591aab93d/inception"])
        # Archive mode: download() forwards the raw archive bytes for host-side extraction.
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertIsNone(result["episode"])
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_refused_by_penalty_raises_throttle_once(self):
        provider = self.mod.Subs4FreeProvider()
        posts = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            return DOWNLOAD_HTML if "greek-subtitles" in url else b"ok"

        def post_stub(url, data, timeout=15, referer=None):
            del timeout, referer
            posts.append(url)
            # urllib follows the refusal's 302 back to the detail page.
            return DOWNLOAD_HTML

        provider._http_get = get_stub
        provider._http_post = post_stub
        with self.assertRaises(self.mod.APIThrottled):
            provider.download(
                {
                    "detail_url": "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception",
                    "filename": "inception.el.zip",
                    "episode": None,
                },
                {"alpha3": "ell", "alpha2": "el"},
                {"request_delay_ms": 0},
            )
        # A refusal is not retried: the site would only extend the penalty.
        self.assertEqual(len(posts), 1)


def _anubis_auth_cookie(host="www.subs4free.info"):
    return Cookie(0, "techaro.lol-anubis-auth", "token", None, False, host, False, False,
                  "/", True, True, None, False, None, None, {})


class Subs4FreeAnubisTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        patcher = mock.patch.object(self.mod.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extracts_live_challenge_page(self):
        challenge = self.mod._extract_anubis_challenge(CHALLENGE_HTML.decode())
        self.assertEqual(challenge["method"], "fast")
        self.assertEqual(challenge["difficulty"], 4)
        self.assertEqual(challenge["id"], "01a16d54-5362-7b86-8e31-d25676e44c1e")
        self.assertTrue(self.mod._is_anubis_response("https://www.subs4free.info/", 200, CHALLENGE_HTML))
        self.assertFalse(self.mod._is_anubis_response("https://www.subs4free.info/", 200, SEARCH_HTML))

    def test_extract_reads_rules_when_challenge_omits_them(self):
        page = (
            '<script id="anubis_challenge" type="application/json">'
            '{"rules":{"algorithm":"slow","difficulty":2},"challenge":{"id":"a","randomData":"b"}}</script>'
        )
        challenge = self.mod._extract_anubis_challenge(page)
        self.assertEqual((challenge["method"], challenge["difficulty"]), ("slow", 2))

    def test_extract_accepts_string_form_challenge(self):
        # Older Anubis builds publish the random data as a bare string, as subs4series parses.
        page = (
            '<script id="anubis_challenge" type="application/json">'
            '{"rules":{"algorithm":"fast","difficulty":3},"challenge":"abc123"}</script>'
        )
        challenge = self.mod._extract_anubis_challenge(page)
        self.assertEqual(challenge, {"id": "abc123", "randomData": "abc123", "difficulty": 3, "method": "fast"})
        with_id = page.replace('"challenge":"abc123"', '"id":"c-1","challenge":"abc123"')
        self.assertEqual(self.mod._extract_anubis_challenge(with_id)["id"], "c-1")
        empty = page.replace('"challenge":"abc123"', '"challenge":""')
        self.assertIsNone(self.mod._extract_anubis_challenge(empty))

    def test_extract_reads_algorithm_or_method_from_either_place(self):
        template = '<script id="anubis_challenge" type="application/json">{%s}</script>'
        cases = [
            ('"challenge":{"id":"a","randomData":"b","algorithm":"slow"}', "slow"),
            ('"rules":{"method":"preact"},"challenge":{"id":"a","randomData":"b"}', "preact"),
            ('"challenge":{"id":"a","randomData":"b"}', "fast"),
        ]
        for body, method in cases:
            with self.subTest(body=body):
                self.assertEqual(self.mod._extract_anubis_challenge(template % body)["method"], method)

    def test_proof_of_work_meets_difficulty(self):
        nonce, digest = self.mod._solve_pow("abc", 4)
        self.assertEqual(digest, hashlib.sha256(f"abc{nonce}".encode()).hexdigest())
        self.assertTrue(digest.startswith("0000"))

    def test_get_solves_challenge_then_refetches(self):
        provider = self.mod.Subs4FreeProvider()
        calls = []
        url = "https://www.subs4free.info/search_report.php?search=Inception&searchType=1"

        def fetch(request, timeout, opener=None):
            del timeout
            calls.append(request.full_url)
            if "pass-challenge" in request.full_url:
                # The pass endpoint is called without following its redirect.
                self.assertIs(opener, provider._pass_opener)
                query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
                self.assertEqual(query["redir"], ["/search_report.php?search=Inception&searchType=1"])
                self.assertTrue(query["response"][0].startswith("0000"))
                provider._cookie_jar.set_cookie(_anubis_auth_cookie())
                return 302, request.full_url, b""
            return 200, url, CHALLENGE_HTML if len(calls) == 1 else SEARCH_HTML

        provider._fetch = fetch
        self.assertEqual(provider._http_get(url), SEARCH_HTML)
        self.assertEqual(len(calls), 3)

    def test_post_is_resent_after_challenge(self):
        provider = self.mod.Subs4FreeProvider()
        methods = []
        detail = "https://www.subs4free.info/greek-subtitles/abc/inception"

        def fetch(request, timeout, opener=None):
            del timeout, opener
            methods.append(request.get_method())
            if "pass-challenge" in request.full_url:
                # A challenged POST sends the pass endpoint back to the form's page.
                query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
                self.assertEqual(query["redir"], ["/greek-subtitles/abc/inception"])
                provider._cookie_jar.set_cookie(_anubis_auth_cookie())
                return 302, request.full_url, b""
            if len(methods) == 1:
                return 200, request.full_url, CHALLENGE_HTML
            self.assertEqual(request.data, b"id=1&x=2&y=3")
            return 200, request.full_url, b"PK-archive"

        provider._fetch = fetch
        body = provider._http_post(self.mod.DOWNLOAD_URL, {"id": "1", "x": "2", "y": "3"}, referer=detail)
        self.assertEqual(body, b"PK-archive")
        self.assertEqual(methods, ["POST", "GET", "POST"])

    def test_failed_solves_renew_identity_then_raise(self):
        provider = self.mod.Subs4FreeProvider()
        jars = []

        def fetch(request, timeout, opener=None):
            del timeout, opener
            if provider._cookie_jar not in jars:
                jars.append(provider._cookie_jar)
            if "pass-challenge" in request.full_url:
                return 403, request.full_url, b"denied"
            return 200, request.full_url, CHALLENGE_HTML

        provider._fetch = fetch
        with self.assertRaises(self.mod.ServiceUnavailable):
            provider._http_get("https://www.subs4free.info/")
        self.assertEqual(len(jars), self.mod.CHALLENGE_RETRY_ATTEMPTS)

    def test_off_site_gate_keeps_the_site_identity(self):
        # A failed gate on a third-party host must not wipe the subs4free.info
        # clearance cookie.
        provider = self.mod.Subs4FreeProvider()
        jar = provider._cookie_jar

        def fetch(request, timeout, opener=None):
            del timeout, opener
            if "pass-challenge" in request.full_url:
                return 403, request.full_url, b""
            return 200, request.full_url, CHALLENGE_HTML

        provider._fetch = fetch
        with self.assertRaises(self.mod.ServiceUnavailable):
            provider._http_get("https://www.subs4series.com/includes/anti-block.php")
        self.assertIs(provider._cookie_jar, jar)

    def test_failed_gate_on_images_host_keeps_the_site_identity(self):
        # images.subs4free.info runs its own Anubis gate. Renewing the identity on a failed
        # gate there would wipe the www clearance cookie right before the getSub.php POST,
        # which would then bounce to the detail page and read as the anti-flood penalty.
        provider = self.mod.Subs4FreeProvider()
        provider._cookie_jar.set_cookie(_anubis_auth_cookie())
        jar = provider._cookie_jar
        pages = []

        def fetch(request, timeout, opener=None):
            del timeout, opener
            if "pass-challenge" in request.full_url:
                return 403, request.full_url, b""
            pages.append(request.full_url)
            return 200, request.full_url, CHALLENGE_HTML

        provider._fetch = fetch
        with self.assertRaises(self.mod.ServiceUnavailable):
            provider._http_get("https://images.subs4free.info/favicon.ico")
        self.assertIs(provider._cookie_jar, jar)
        self.assertTrue(provider._has_anubis_auth("www.subs4free.info"))
        self.assertEqual(len(pages), 1)

    def test_only_the_www_host_renews_the_identity(self):
        self.assertTrue(self.mod._renews_identity("https://www.subs4free.info/getSub.php"))
        for url in (
            "https://images.subs4free.info/favicon.ico",
            "https://subs4free.info/",
            "https://www.subs4series.com/includes/anti-block.php",
            "https://www.subs4free.info.example.com/",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.mod._renews_identity(url))

    def test_proof_of_work_budget_exhaustion_aborts_without_retry(self):
        # A new identity cannot make the proof of work cheaper: running out of budget
        # mid-solve ends the call at once, with no renewal and no pass submission.
        self.mod.SITE_CALL_BUDGET_SECONDS = 0.2
        hard = CHALLENGE_HTML.replace(b'"difficulty":4', b'"difficulty":40')
        provider = self.mod.Subs4FreeProvider()
        jar = provider._cookie_jar
        calls = []

        def fetch(request, timeout, opener=None):
            del timeout, opener
            calls.append(request.full_url)
            return 200, request.full_url, hard

        provider._fetch = fetch
        with self.assertRaises(self.mod.ServiceUnavailable) as raised:
            provider._http_get("https://www.subs4free.info/")
        self.assertIn("proof_of_work budget_exhausted", str(raised.exception))
        self.assertEqual(calls, ["https://www.subs4free.info/"])
        self.assertIs(provider._cookie_jar, jar)

    def test_attempts_share_one_call_budget(self):
        # Every fetch "takes" 20 s on a fake clock and the pass endpoint keeps refusing.
        # The call must stop at the 60 s budget instead of running three full attempts,
        # and every request timeout must fit what is left of it.
        clock = [1000.0]
        timeouts = []
        provider = self.mod.Subs4FreeProvider()

        def fetch(request, timeout, opener=None):
            del opener
            timeouts.append((timeout, 1000.0 + self.mod.SITE_CALL_BUDGET_SECONDS - clock[0]))
            clock[0] += 20
            if "pass-challenge" in request.full_url:
                return 403, request.full_url, b""
            return 200, request.full_url, CHALLENGE_HTML

        provider._fetch = fetch
        with mock.patch.object(self.mod.time, "monotonic", lambda: clock[0]):
            with self.assertRaises(self.mod.ServiceUnavailable) as raised:
                provider._http_get("https://www.subs4free.info/")
        self.assertIn("budget_exhausted", str(raised.exception))
        self.assertLessEqual(clock[0] - 1000.0, self.mod.SITE_CALL_BUDGET_SECONDS)
        for timeout, left in timeouts:
            self.assertLessEqual(timeout, left)

    def test_rate_limit_raises(self):
        provider = self.mod.Subs4FreeProvider()
        provider._fetch = lambda request, timeout, opener=None: (429, request.full_url, b"")
        with self.assertRaises(self.mod.RateLimited):
            provider._http_get("https://www.subs4free.info/")

    def _rate_limited_by_opener(self, retry_after):
        provider = self.mod.Subs4FreeProvider()
        headers = email.message.Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after

        def open_(request, timeout=None):
            del timeout
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", headers, io.BytesIO(b""))

        provider._opener.open = open_
        with self.assertRaises(self.mod.RateLimited) as raised:
            provider._http_get("https://www.subs4free.info/")
        return raised.exception

    def test_rate_limit_carries_retry_after_seconds(self):
        # The worker sends error.retry_after to the host with the RateLimited class name.
        self.assertEqual(self._rate_limited_by_opener("120").retry_after, 120)

    def test_rate_limit_carries_retry_after_http_date(self):
        when = email.utils.formatdate(time.time() + 300, usegmt=True)
        self.assertAlmostEqual(self._rate_limited_by_opener(when).retry_after, 300, delta=5)

    def test_rate_limit_ignores_unusable_retry_after(self):
        self.assertIsNone(self._rate_limited_by_opener(None).retry_after)
        past = email.utils.formatdate(time.time() - 60, usegmt=True)
        for value in ("soon", "0", "-5", "inf", "nan", past):
            with self.subTest(value=value):
                self.assertIsNone(self.mod._retry_after_seconds(value))

    def test_plain_http_error_is_not_a_provider_throttle(self):
        provider = self.mod.Subs4FreeProvider()
        provider._fetch = lambda request, timeout, opener=None: (404, request.full_url, b"Not Found")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            provider._http_get("https://www.subs4free.info/greek-subtitles/missing/x")
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
