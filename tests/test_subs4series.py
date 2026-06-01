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

    def test_extract_download_reads_zip_subtitle_and_skips_hidden_files(self):
        payload = self.mod.extract_download(
            _zip_body(),
            {"filename": "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.el.zip"},
        )

        self.assertEqual(payload["format"], "srt")
        self.assertEqual(payload["encoding"], "utf-8")
        self.assertFalse(payload["empty"])
        self.assertIn("1\nsubtitle\n", self.mod._decode_payload_text(payload))


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
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4series.com/getSub-direct-token.html")
        self.assertEqual(posts[0][1]["my_recaptcha_challenge_field"], "manual_challenge")
        self.assertEqual(posts[0][2], "https://www.subs4series.com/english-subtitles/sb6a7a0c63b/game-of-thrones")
        self.assertTrue(any("anti-block-layover.php" in item[0] for item in seen_gets))
        body = base64.b64decode(result["content_b64"])
        self.assertEqual(hashlib.sha256(body).hexdigest(), result["content_sha256"])
        self.assertIn("1\nsubtitle\n", self.mod._decode_payload_text(result))

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
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"captcha_response": "solved-token", "request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4series.com/getSub-captcha-token.html")
        self.assertEqual(posts[0][1]["g-recaptcha-response"], "solved-token")
        self.assertEqual(posts[0][1]["recaptcha_response"], "solved-token")
        self.assertIn("1\nsubtitle\n", self.mod._decode_payload_text(result))

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
