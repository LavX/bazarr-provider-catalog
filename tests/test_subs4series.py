import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
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

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}, timeout))
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


class Subs4SeriesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

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
        }

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if url not in responses:
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
        # The host needs episode (and season) to pick the archive member.
        self.assertEqual(first["provider_payload"]["season"], 1)
        self.assertEqual(first["provider_payload"]["episode"], 1)

    def test_search_rejects_movies(self):
        provider = self.mod.Subs4SeriesProvider()

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Game of Thrones"}, [{"alpha3": "ell"}], {}),
            [],
        )

    def test_download_posts_direct_target_after_anti_block_requests(self):
        provider = self.mod.Subs4SeriesProvider()
        seen_gets = []
        posts = []

        def get_stub(url, timeout=15, referer=None, config=None):
            del timeout, config
            seen_gets.append((url, referer))
            if url == "https://www.subs4series.com/english-subtitles/sb6a7a0c63b/game-of-thrones":
                return DIRECT_DOWNLOAD_HTML
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
                "detail_url": "https://www.subs4series.com/english-subtitles/sb6a7a0c63b/game-of-thrones",
                "page_link": "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1",
                "filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.en.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4series.com/getSub-direct-token.html")
        self.assertEqual(posts[0][1]["my_recaptcha_challenge_field"], "manual_challenge")
        self.assertEqual(posts[0][2], "https://www.subs4series.com/english-subtitles/sb6a7a0c63b/game-of-thrones")
        self.assertTrue(any("anti-block-layover.php" in item[0] for item in seen_gets))
        # Archive mode: the worker forwards the raw zip and carries episode for the host.
        body = _zip_body()
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_posts_captcha_response_when_required(self):
        provider = self.mod.Subs4SeriesProvider()
        posts = []

        def get_stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if url == "https://www.subs4series.com/greek-subtitles/s277e869f4/game-of-thrones":
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
                "detail_url": "https://www.subs4series.com/greek-subtitles/s277e869f4/game-of-thrones",
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

    def test_download_requires_captcha_solution_when_page_has_recaptcha(self):
        provider = self.mod.Subs4SeriesProvider()
        provider._http_get = lambda url, timeout=15, referer=None, config=None: CAPTCHA_DOWNLOAD_HTML

        with self.assertRaisesRegex(ValueError, "captcha"):
            provider.download(
                {
                    "detail_url": "https://www.subs4series.com/greek-subtitles/s277e869f4/game-of-thrones",
                    "page_link": "https://www.subs4series.com/tv-series/game-of-thrones/s8985ffc551/season-1/episode-1",
                    "filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.el.zip",
                },
                {"alpha3": "ell", "alpha2": "el"},
                {"request_delay_ms": 0},
            )

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

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
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
        self.assertEqual(
            solved_calls[0][2],
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
        )

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

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
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
        self.assertEqual(
            solved_calls[0][2],
            "https://www.subs4series.com/search_report.php?search=Game+of+Thrones&searchType=1",
        )

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
            self.mod.urllib.request,
            "urlopen",
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
