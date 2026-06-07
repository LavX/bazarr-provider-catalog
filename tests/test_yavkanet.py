import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "yavkanet"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("yavkanet_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMDB_HTML = b"""
<table>
  <tr>
    <td>
      <a class="balon" href="/subtitle/dune-2021" content="Dune.2021.1080p.WEB-DL-FLUX">
        Dune.2021.1080p.WEB-DL-FLUX
      </a>
      <span>(2021)</span>
    </td>
    <td><span title="Frames per second">23.976</span></td>
    <td><a class="click">uploader</a></td>
  </tr>
</table>
"""
DETAIL_HTML = b"""
<form method="POST" action="/subtitle/dune-2021/">
  <input type="hidden" name="subtitle_id" value="123">
  <input type="hidden" name="token" value="abc">
</form>
"""
CURRENT_IMDB_HTML = b"""
<div class="list-group" id="imdbSubtitleList">
  <a class="list-group-item list-group-item-action imdb-subtitle-item" data-season="0" data-lang="BG" href="/subs/11894/BG">
    <div class="d-flex justify-content-between gap-3 flex-wrap">
      <div class="min-w-0">
        <strong>Dune</strong>
        <span class="text-muted">(2021)</span>
        <div class="text-muted small">Dyuna</div>
      </div>
      <div class="d-flex align-items-center gap-2 flex-wrap small text-muted">
        <span><i class="bi bi-download"></i> 11535</span>
        <span>WEB</span>
        <span class="fw-semibold"><i class="bi bi-person"></i> WebRip</span>
      </div>
    </div>
  </a>
</div>
"""
CURRENT_DETAIL_HTML = b"""
<form id="search" name="search" action="/search" method="post">
  <input type="text" name="sea" value="">
</form>
<a href="https://yavka.net/download?q=token" id="down" class="btn btn-info">
  <b>DOWNLOAD</b>
</a>
"""
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nYavkaNet subtitle.\n"
# RAR4 signature followed by arbitrary bytes. Host-side extraction only needs the
# magic to route the body to archive mode; the host owns the real rar/zip stack.
RAR_BODY = b"Rar!\x1a\x07\x00" + SRT_BODY
CLOUDFLARE_BODY = b"<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script></html>"


def _zip_with(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class FakeResponse:
    def __init__(self, body, status=200, headers=None, url="https://yavka.net/imdb/tt1160419", text=None):
        self.content = body
        self.text = text if text is not None else body.decode("utf-8", "ignore")
        self.status_code = status
        self.headers = headers or {}
        self.url = url


class FakeUrlopenResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


class YavkaNetParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_imdb_results_extracts_rows(self):
        rows = self.mod.parse_imdb_results(IMDB_HTML)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["page_url"], "https://yavka.net/subtitle/dune-2021/")
        self.assertEqual(row["release"], "Dune.2021.1080p.WEB-DL-FLUX")
        self.assertEqual(row["title"], "Dune.2021.1080p.WEB-DL-FLUX")
        self.assertEqual(row["year"], 2021)
        self.assertEqual(row["fps"], 23.976)
        self.assertEqual(row["uploader"], "uploader")

    def test_parse_imdb_results_extracts_current_list_items(self):
        rows = self.mod.parse_imdb_results(CURRENT_IMDB_HTML)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["page_url"], "https://yavka.net/subs/11894/BG/")
        self.assertEqual(row["title"], "Dune")
        self.assertEqual(row["language"], "bul")
        self.assertIn("WEB", row["release"])
        self.assertEqual(row["year"], 2021)
        self.assertEqual(row["uploader"], "WebRip")

    def test_parse_download_form_extracts_post_fields(self):
        form = self.mod.parse_download_form(DETAIL_HTML)

        self.assertEqual(form["method"], "POST")
        self.assertEqual(form["action_url"], "https://yavka.net/subtitle/dune-2021/")
        self.assertEqual(form["data"], {"subtitle_id": "123", "token": "abc"})

    def test_parse_download_form_extracts_current_direct_link(self):
        form = self.mod.parse_download_form(CURRENT_DETAIL_HTML)

        self.assertEqual(form["method"], "GET")
        self.assertEqual(form["action_url"], "https://yavka.net/download?q=token")
        self.assertEqual(form["data"], {})

    def test_derive_matches_movie_release_tokens(self):
        row = self.mod.parse_imdb_results(IMDB_HTML)[0]
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "FLUX",
                "resolution": "1080p",
                "source": "Web",
            },
            row,
        )

        self.assertEqual(set(matches), {"title", "year", "release_group", "resolution", "source"})


class YavkaNetCloudflareTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_detects_cloudflare_challenge(self):
        self.assertTrue(self.mod.is_cloudflare_challenge(403, {"cf-mitigated": "challenge"}, b""))
        self.assertTrue(self.mod.is_cloudflare_challenge(403, {}, CLOUDFLARE_BODY))
        self.assertFalse(self.mod.is_cloudflare_challenge(200, {}, CLOUDFLARE_BODY))

    def test_http_get_uses_cloudscraper_by_default(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(b"<html>ok</html>")

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper) as create_scraper:
            state = {}
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state=state)

        self.assertEqual(body, b"<html>ok</html>")
        create_scraper.assert_called_once_with(
            browser={"custom": self.mod.USER_AGENT},
            interpreter="native",
            enable_cookie_persistence=False,
            debug=False,
        )
        scraper.get.assert_called_once()

    def test_http_get_retries_without_cookie_persistence_for_legacy_cloudscraper(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(b"<html>ok</html>")
        created = []

        def create_scraper(**kwargs):
            created.append(dict(kwargs))
            if "enable_cookie_persistence" in kwargs:
                raise TypeError(
                    "Session.__init__() got an unexpected keyword argument 'enable_cookie_persistence'"
                )
            return scraper

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", side_effect=create_scraper):
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(len(created), 2)
        self.assertFalse(created[0]["enable_cookie_persistence"])
        self.assertEqual(created[0]["interpreter"], "native")
        self.assertNotIn("enable_cookie_persistence", created[1])
        self.assertEqual(created[1]["interpreter"], "native")

    def test_http_get_solves_anubis_inline_before_retrying_original_url(self):
        challenge = FakeResponse(
            b'<script id="anubis_challenge">{}</script>',
            status=401,
            url="https://yavka.net/.within.website/?redir=/imdb/tt1160419",
        )
        solved_response = FakeResponse(b"<html>ok</html>", url="https://yavka.net/imdb/tt1160419")
        scraper = mock.MagicMock()
        scraper.get.side_effect = [challenge, solved_response]
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod,
            "solve_anubis_challenge",
            side_effect=fake_solve,
        ):
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        self.assertEqual(scraper.get.call_args_list[0].args[0], "https://yavka.net/imdb/tt1160419")
        self.assertEqual(scraper.get.call_args_list[1].args[0], "https://yavka.net/imdb/tt1160419")
        self.assertIs(solved_calls[0][0], scraper)
        self.assertEqual(solved_calls[0][1], "https://yavka.net/.within.website/?redir=/imdb/tt1160419")
        self.assertEqual(solved_calls[0][2], "https://yavka.net/imdb/tt1160419")

    def test_http_get_solves_anubis_body_before_retrying_original_url(self):
        anubis_body = (
            b'<html><head><meta http-equiv="refresh" '
            b'content="0; url=/.within.website/?redir=/imdb/tt1160419"></head></html>'
        )
        solved_response = FakeResponse(b"<html>ok</html>", url="https://yavka.net/imdb/tt1160419")
        scraper = mock.MagicMock()
        scraper.get.side_effect = [
            FakeResponse(anubis_body, status=200, url="https://yavka.net/imdb/tt1160419"),
            solved_response,
        ]
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod,
            "solve_anubis_challenge",
            side_effect=fake_solve,
        ):
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        self.assertIs(solved_calls[0][0], scraper)
        self.assertEqual(solved_calls[0][1], "https://yavka.net/imdb/tt1160419")
        self.assertEqual(solved_calls[0][2], "https://yavka.net/imdb/tt1160419")

    def test_http_get_uses_flaresolverr_fallback_after_challenge(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(
            CLOUDFLARE_BODY,
            status=403,
            headers={"cf-mitigated": "challenge"},
        )
        payload = {
            "status": "ok",
            "solution": {
                "status": 200,
                "response": "<html>solved</html>",
                "userAgent": "Mozilla/5.0 solved",
                "cookies": [{"name": "cf_clearance", "value": "token"}],
            },
        }

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.urllib.request,
            "urlopen",
            return_value=FakeUrlopenResponse(json.dumps(payload).encode("utf-8")),
        ) as urlopen:
            state = {}
            body = self.mod.http_get(
                "https://yavka.net/imdb/tt1160419",
                config={"flaresolverr_url": "http://flaresolverr:8191/v1", "flaresolverr_timeout_ms": 45000},
                state=state,
            )

        self.assertEqual(body, b"<html>solved</html>")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://flaresolverr:8191/v1")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["maxTimeout"], 30000)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30)
        self.assertEqual(state["flaresolverr_user_agent"], "Mozilla/5.0 solved")
        self.assertEqual(state["flaresolverr_cookies"], {"cf_clearance": "token"})

    def test_http_get_uses_flaresolverr_fallback_after_plain_403(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(b"Forbidden", status=403)
        payload = {
            "status": "ok",
            "solution": {
                "status": 200,
                "response": "subtitle-body",
                "cookies": [{"name": "cf_clearance", "value": "token"}],
            },
        }

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.urllib.request,
            "urlopen",
            return_value=FakeUrlopenResponse(json.dumps(payload).encode("utf-8")),
        ) as urlopen:
            state = {}
            body = self.mod.http_get(
                "https://yavka.net/download?q=token",
                config={"flaresolverr_url": "http://flaresolverr:8191/v1"},
                state=state,
            )

        self.assertEqual(body, b"subtitle-body")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["cmd"], "request.get")
        self.assertEqual(payload["url"], "https://yavka.net/download?q=token")
        self.assertEqual(state["flaresolverr_cookies"], {"cf_clearance": "token"})

    def test_http_get_raises_visible_error_without_flaresolverr(self):
        scraper = mock.MagicMock()
        scraper.get.return_value = FakeResponse(CLOUDFLARE_BODY, status=403)

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper):
            with self.assertRaises(self.mod.CloudflareBlockedError):
                self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})


class YavkaNetRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _scraper(self):
        return mock.MagicMock()

    def test_http_get_retries_transient_url_error_then_succeeds(self):
        # A single connection blip (URLError) must not abort the request: the
        # helper retries and ultimately returns the success body.
        import urllib.error

        scraper = self._scraper()
        scraper.get.side_effect = [
            urllib.error.URLError("connection reset by peer"),
            FakeResponse(b"<html>ok</html>"),
        ]

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        sleep.assert_called_once()
        self.assertGreater(sleep.call_args.args[0], 0)

    def test_http_get_retries_two_transient_errors_then_succeeds(self):
        # Two failures (a connection error then a read timeout) still recover
        # within the 3-attempt budget.
        import urllib.error

        scraper = self._scraper()
        scraper.get.side_effect = [
            urllib.error.URLError("name resolution failed"),
            TimeoutError("read timed out"),
            FakeResponse(b"<html>ok</html>"),
        ]

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_http_get_retries_transient_503_then_succeeds(self):
        # A transient 503 (no Cloudflare markers) is retried, then the success
        # body is returned. The 503 must not be mapped to an HTTPError.
        scraper = self._scraper()
        scraper.get.side_effect = [
            FakeResponse(b"upstream down", status=503),
            FakeResponse(b"<html>ok</html>"),
        ]

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        sleep.assert_called_once()

    def test_http_get_honors_retry_after_on_429(self):
        # A 429 with a Retry-After is retried and the header drives the backoff,
        # capped to the bounded ceiling.
        scraper = self._scraper()
        scraper.get.side_effect = [
            FakeResponse(b"slow down", status=429, headers={"Retry-After": "2"}),
            FakeResponse(b"<html>ok</html>"),
        ]

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            body = self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        self.assertGreaterEqual(sleep.call_args.args[0], 2)
        self.assertLessEqual(sleep.call_args.args[0], self.mod.RETRY_BACKOFF_CAP_SECONDS)

    def test_http_get_exhausts_retries_and_maps_final_503_to_error(self):
        # After the attempt budget is spent the last transient response is still
        # handled by the existing status mapping (503 -> HTTPError), not swallowed.
        import urllib.error

        scraper = self._scraper()
        scraper.get.return_value = FakeResponse(b"upstream down", status=503)

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(scraper.get.call_count, self.mod.HTTP_MAX_ATTEMPTS)
        ctx.exception.close()

    def test_http_get_does_not_retry_404(self):
        # A 4xx other than 429 is not transient: it must propagate on the first
        # attempt with no retry and no sleep.
        import urllib.error

        scraper = self._scraper()
        scraper.get.return_value = FakeResponse(b"not found", status=404)

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(scraper.get.call_count, 1)
        sleep.assert_not_called()
        ctx.exception.close()

    def test_http_get_does_not_retry_non_transient_exception(self):
        # A non-network exception (e.g. a parse/value error raised by the
        # transport layer) propagates immediately without retry.
        scraper = self._scraper()
        scraper.get.side_effect = ValueError("boom")

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            with self.assertRaises(ValueError):
                self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(scraper.get.call_count, 1)
        sleep.assert_not_called()

    def test_http_get_does_not_retry_cloudflare_challenge_response(self):
        # A 503 that is actually a Cloudflare challenge is left to the existing
        # challenge handling, not retried as a transient transport error.
        scraper = self._scraper()
        scraper.get.return_value = FakeResponse(CLOUDFLARE_BODY, status=503)

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            with self.assertRaises(self.mod.CloudflareBlockedError):
                self.mod.http_get("https://yavka.net/imdb/tt1160419", state={})

        self.assertEqual(scraper.get.call_count, 1)
        sleep.assert_not_called()

    def test_http_post_retries_transient_error_then_succeeds(self):
        # The search/login POST path is safe to repeat, so it is retried too.
        import urllib.error

        scraper = self._scraper()
        scraper.post.side_effect = [
            urllib.error.URLError("connection refused"),
            FakeResponse(b"subtitle-body"),
        ]

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.time, "sleep"
        ) as sleep:
            body = self.mod.http_post(
                "https://yavka.net/subtitle/dune-2021/",
                data={"subtitle_id": "123"},
                state={},
            )

        self.assertEqual(body, b"subtitle-body")
        self.assertEqual(scraper.post.call_count, 2)
        sleep.assert_called_once()


class YavkaNetProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_imdb_rows_and_detail_form(self):
        provider = self.mod.YavkaNetProvider()
        called = []

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state
            called.append((url, referer))
            if url == "https://yavka.net/imdb/tt1160419":
                return IMDB_HTML
            if url == "https://yavka.net/subtitle/dune-2021/":
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "FLUX",
                "resolution": "1080p",
                "source": "Web",
            },
            [{"alpha3": "bul", "alpha2": "bg", "hi": True, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called[0], ("https://yavka.net/imdb/tt1160419", "https://yavka.net/"))
        self.assertEqual(called[1], ("https://yavka.net/subtitle/dune-2021/", "https://yavka.net/imdb/tt1160419"))
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["provider"], "yavkanet")
        # Yavka rows are never verified as hearing impaired, so the emitted
        # variant is always non-HI even when the request asked for HI.
        self.assertEqual(result["language"], {"alpha3": "bul", "alpha2": "bg", "hi": False, "forced": False})
        self.assertFalse(result["hearing_impaired"])
        self.assertEqual(result["provider_payload"]["form_data"], {"subtitle_id": "123", "token": "abc"})
        self.assertIn("release_group", result["matches"])

    def test_search_supports_current_imdb_cards_and_direct_download_link(self):
        provider = self.mod.YavkaNetProvider()

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state, referer
            if url == "https://yavka.net/imdb/tt1160419":
                return CURRENT_IMDB_HTML
            if url == "https://yavka.net/subs/11894/BG/":
                return CURRENT_DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "source": "Web",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        payload = results[0]["provider_payload"]
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["download_url"], "https://yavka.net/download?q=token")
        self.assertEqual(payload["form_data"], {})

    def test_search_filters_current_imdb_cards_by_row_language(self):
        provider = self.mod.YavkaNetProvider()

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state, referer
            if url == "https://yavka.net/imdb/tt1160419":
                return CURRENT_IMDB_HTML
            if url == "https://yavka.net/subs/11894/BG/":
                return CURRENT_DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "source": "Web",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_movie_search_keeps_imdb_row_with_localized_title(self):
        provider = self.mod.YavkaNetProvider()
        localized_imdb_html = IMDB_HTML.replace(b"Dune.2021.1080p.WEB-DL-FLUX", b"Dyuna.2021.1080p.WEB-DL-FLUX")

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state, referer
            if url == "https://yavka.net/imdb/tt1160419":
                return localized_imdb_html
            if url == "https://yavka.net/subtitle/dune-2021/":
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "bul", "alpha2": "bg"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)

    def test_episode_search_stores_season_and_episode_in_payload(self):
        imdb_html = b"""
        <table>
          <tr>
            <td>
              <a class="balon" href="/subtitle/example-s01e02" content="Example.S01E02.1080p.WEB-DL">
                Example.S01E02.1080p.WEB-DL
              </a>
            </td>
          </tr>
        </table>
        """
        provider = self.mod.YavkaNetProvider()

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state, referer
            if url == "https://yavka.net/imdb/tt1160419":
                return imdb_html
            if url == "https://yavka.net/subtitle/example-s01e02/":
                return CURRENT_DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Example",
                "season": 1,
                "episode": 2,
                "series_imdb_id": "tt1160419",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        payload = results[0]["provider_payload"]
        # download() reads episode (and season) from the payload for host-side member selection.
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 2)

    def test_search_skips_unsupported_language_without_network(self):
        provider = self.mod.YavkaNetProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected network"))

        results = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {},
        )

        self.assertEqual(results, [])

    def test_download_posts_form_and_returns_zip_archive(self):
        body = _zip_with(
            {
                "Dune.2021.720p.WEB-DL.srt": b"wrong",
                "Dune.2021.1080p.WEB-DL-FLUX.srt": SRT_BODY,
            }
        )
        provider = self.mod.YavkaNetProvider()
        provider._http_post = lambda url, data, timeout=30, config=None, state=None, referer=None: body

        result = provider.download(
            {
                "download_url": "https://yavka.net/subtitle/dune-2021/",
                "form_data": {"subtitle_id": "123", "token": "abc"},
                "filename": "Dune.2021.1080p.WEB-DL-FLUX.zip",
                "release": "Dune.2021.1080p.WEB-DL-FLUX",
                "episode": None,
            },
            {"alpha3": "bul"},
            {},
        )

        # Host-side extraction: the worker returns the raw archive bytes untouched.
        self.assertNotIn("content_b64", result)
        raw = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(raw, body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertIsNone(result["episode"])
        self.assertNotIn("encoding", result)

    def test_download_uses_get_for_direct_download_links(self):
        body = _zip_with({"Dune.2021.WEB.srt": SRT_BODY})
        provider = self.mod.YavkaNetProvider()
        provider._http_get = lambda url, timeout=30, config=None, state=None, referer=None: body
        provider._http_post = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected POST"))

        result = provider.download(
            {
                "method": "GET",
                "download_url": "https://yavka.net/download?q=token",
                "form_data": {},
                "filename": "Dune.2021.WEB.zip",
                "release": "Dune.2021.WEB",
                "episode": None,
            },
            {"alpha3": "bul"},
            {},
        )

        raw = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(raw, body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())

    def test_extract_download_returns_zip_archive_for_movies(self):
        body = _zip_with({"Dune.2021.WEB.srt": SRT_BODY})

        result = self.mod.extract_download(body, {"filename": "Dune.2021.WEB.zip"})

        raw = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(raw, body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        # Movies carry no episode for host-side member selection.
        self.assertIsNone(result["episode"])
        self.assertNotIn("encoding", result)

    def test_extract_download_carries_episode_for_rar_archive(self):
        body = RAR_BODY

        result = self.mod.extract_download(body, {"filename": "Example.S01.rar", "episode": 2})

        raw = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(raw, body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 2)
        self.assertNotIn("encoding", result)

    def test_extract_download_pins_member_by_release_group(self):
        # Two members for the same episode differ only by release group; the host's
        # episode-only pick cannot tell them apart, so the worker pins the scored one.
        body = _zip_with(
            {
                "Example.S01E02.720p.WEB.NTb.srt": b"wrong group",
                "Example.S01E02.1080p.WEB.FLUX.srt": SRT_BODY,
            }
        )

        result = self.mod.extract_download(
            body,
            {
                "filename": "Example.S01E02.zip",
                "season": 1,
                "episode": 2,
                "video": {"release_group": "FLUX", "resolution": "1080p", "source": "Web"},
            },
        )

        self.assertEqual(result["member"], "Example.S01E02.1080p.WEB.FLUX.srt")
        self.assertNotIn("episode", result)
        self.assertNotIn("content_b64", result)

    def test_extract_download_pins_member_for_requested_episode_in_pack(self):
        # A season pack with several release groups per episode: resolve episode and
        # release group together so the pinned member matches the scored candidate.
        body = _zip_with(
            {
                "Example.S01E01.1080p.WEB.FLUX.srt": b"e1 flux",
                "Example.S01E02.720p.WEB.NTb.srt": b"e2 ntb",
                "Example.S01E02.1080p.WEB.FLUX.srt": SRT_BODY,
            }
        )

        result = self.mod.extract_download(
            body,
            {
                "filename": "Example.S01.zip",
                "season": 1,
                "episode": 2,
                "video": {"release_group": "FLUX", "resolution": "1080p", "source": "Web"},
            },
        )

        self.assertEqual(result["member"], "Example.S01E02.1080p.WEB.FLUX.srt")
        self.assertNotIn("episode", result)

    def test_extract_download_falls_back_to_episode_without_field_match(self):
        # Members differ but none matches the scored fields: ambiguous, so defer to the
        # host's episode selection rather than guessing.
        body = _zip_with(
            {
                "Example.S01E02.720p.HDTV.NTb.srt": b"one",
                "Example.S01E02.XviD.AC3.srt": b"two",
            }
        )

        result = self.mod.extract_download(
            body,
            {
                "filename": "Example.S01E02.zip",
                "season": 1,
                "episode": 2,
                "video": {"release_group": "FLUX", "resolution": "1080p", "source": "Bluray"},
            },
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 2)

    def test_extract_download_defers_when_requested_episode_absent(self):
        # The requested episode is in no member; pinning another episode's member that
        # happens to match the scored fields would hard-fail the host download, so defer.
        # (E01 here matches release_group/resolution/source; the request is for E02.)
        body = _zip_with(
            {
                "Example.S01E01.1080p.WEB.FLUX.srt": b"e1",
                "Example.S01E03.720p.HDTV.NTb.srt": b"e3",
            }
        )

        result = self.mod.extract_download(
            body,
            {
                "filename": "Example.S01.zip",
                "season": 1,
                "episode": 2,
                "video": {"release_group": "FLUX", "resolution": "1080p", "source": "Web"},
            },
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 2)

    def test_extract_download_rejects_empty_body(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            self.mod.extract_download(b"", {"filename": "Dune.2021.WEB.zip"})

    def test_extract_download_rejects_html_error_page(self):
        with self.assertRaisesRegex(ValueError, "HTML"):
            self.mod.extract_download(
                b"<!DOCTYPE html><html><body>error</body></html>",
                {"filename": "Dune.2021.WEB.zip"},
            )


class YavkaNetCodexFixTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_download_form_rejects_search_only_page(self):
        search_only_html = b"""
        <form id="search" name="search" action="/search" method="post">
          <input type="text" name="sea" value="">
        </form>
        """

        with self.assertRaisesRegex(ValueError, "did not expose a download form"):
            self.mod.parse_download_form(search_only_html)

    def test_parse_download_form_skips_search_form_before_download_form(self):
        mixed_html = b"""
        <form id="search" name="search" action="/search" method="post">
          <input type="text" name="sea" value="">
        </form>
        <form method="POST" action="/subtitle/dune-2021/">
          <input type="hidden" name="subtitle_id" value="123">
          <input type="hidden" name="token" value="abc">
        </form>
        """

        form = self.mod.parse_download_form(mixed_html)

        self.assertEqual(form["method"], "POST")
        self.assertEqual(form["action_url"], "https://yavka.net/subtitle/dune-2021/")
        self.assertEqual(form["data"], {"subtitle_id": "123", "token": "abc"})

    def test_flaresolverr_post_sets_form_content_type(self):
        scraper = mock.MagicMock()
        scraper.post.return_value = FakeResponse(CLOUDFLARE_BODY, status=403)
        payload = {
            "status": "ok",
            "solution": {"status": 200, "response": "subtitle-body"},
        }

        with mock.patch.object(self.mod.cloudscraper, "create_scraper", return_value=scraper), mock.patch.object(
            self.mod.urllib.request,
            "urlopen",
            return_value=FakeUrlopenResponse(json.dumps(payload).encode("utf-8")),
        ) as urlopen:
            body = self.mod.http_post(
                "https://yavka.net/subtitle/dune-2021/",
                data={"subtitle_id": "123", "token": "abc"},
                config={"flaresolverr_url": "http://flaresolverr:8191/v1"},
                state={},
            )

        self.assertEqual(body, b"subtitle-body")
        request = urlopen.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["cmd"], "request.post")
        self.assertEqual(sent["postData"], "subtitle_id=123&token=abc")
        self.assertEqual(sent["headers"], {"Content-Type": "application/x-www-form-urlencoded"})

    def test_row_match_requires_season_when_row_declares_other_season(self):
        video = {"kind": "episode", "series": "Example", "season": 3, "episode": 2}
        other_season_row = {
            "title": "Example",
            "release": "Example.S04E02.1080p.WEB-DL",
            "notes": "Example.S04E02.1080p.WEB-DL",
        }
        right_season_row = {
            "title": "Example",
            "release": "Example.S03E02.1080p.WEB-DL",
            "notes": "Example.S03E02.1080p.WEB-DL",
        }

        self.assertFalse(self.mod._row_matches_video(video, other_season_row))
        self.assertTrue(self.mod._row_matches_video(video, right_season_row))

    def test_search_drops_other_season_episode_rows(self):
        imdb_html = b"""
        <table>
          <tr>
            <td>
              <a class="balon" href="/subtitle/example-s04e02" content="Example.S04E02.1080p.WEB-DL">
                Example.S04E02.1080p.WEB-DL
              </a>
            </td>
          </tr>
        </table>
        """
        provider = self.mod.YavkaNetProvider()

        def stub(url, timeout=15, config=None, state=None, referer=None):
            del timeout, config, state, referer
            if url == "https://yavka.net/imdb/tt1160419":
                return imdb_html
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Example",
                "season": 3,
                "episode": 2,
                "series_imdb_id": "tt1160419",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_solve_pow_counts_difficulty_in_bits(self):
        # Difficulty 12 means 12 leading zero bits, i.e. only three leading zero
        # hex characters. The previous implementation treated difficulty as a
        # count of leading zero hex characters and would never accept this digest
        # (it would loop until the worker timed out). A fast, finite solve that
        # yields fewer leading zero hex chars than the difficulty proves the bit
        # interpretation is used.
        nonce, digest = self.mod._solve_pow("randomdata", 12)

        digest_bytes = bytes.fromhex(digest)
        self.assertGreaterEqual(self.mod._leading_zero_bits(digest_bytes), 12)
        leading_zero_hex_chars = len(digest) - len(digest.lstrip("0"))
        self.assertLess(leading_zero_hex_chars, 12)
        expected = hashlib.sha256(f"randomdata{nonce}".encode("utf-8")).hexdigest()
        self.assertEqual(digest, expected)

    def test_leading_zero_bits_counts_bits_not_hex_chars(self):
        self.assertEqual(self.mod._leading_zero_bits(bytes([0x0f, 0xff])), 4)
        self.assertEqual(self.mod._leading_zero_bits(bytes([0x00, 0x0f])), 12)
        self.assertEqual(self.mod._leading_zero_bits(bytes([0x00, 0x00, 0xff])), 16)

    def test_requested_languages_drops_forced_only_requests(self):
        rows = self.mod._requested_languages([{"alpha3": "bul", "alpha2": "bg", "forced": True}])

        self.assertEqual(rows, [])

    def test_requested_languages_never_marks_hi_or_forced(self):
        rows = self.mod._requested_languages([{"alpha3": "bul", "alpha2": "bg", "hi": True}])

        self.assertEqual(rows, [{"alpha3": "bul", "alpha2": "bg", "hi": False, "forced": False}])

    def test_search_skips_forced_only_request_without_network(self):
        provider = self.mod.YavkaNetProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected network"))

        results = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "bul", "alpha2": "bg", "forced": True}],
            {},
        )

        self.assertEqual(results, [])

    def test_extract_download_never_sets_encoding_for_archives(self):
        # The worker no longer guesses an encoding. The host runs chardet via
        # Subtitle.normalize() over the extracted member, so an archive payload
        # must not carry an encoding hint that could reintroduce mojibake.
        cyrillic = "Здравей свят".encode("cp1251")
        body = _zip_with({"Dune.2021.WEB.srt": cyrillic})

        result = self.mod.extract_download(body, {"filename": "Dune.2021.WEB.zip"})

        self.assertNotIn("encoding", result)
        raw = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(raw, body)

    def test_extract_download_passes_non_archive_subtitle_through(self):
        # A bare subtitle stream (not zip/rar) stays in content mode, still with
        # no worker-side encoding guess.
        result = self.mod.extract_download(SRT_BODY, {"filename": "Dune.2021.WEB.srt"})

        self.assertNotIn("encoding", result)
        self.assertNotIn("archive_b64", result)
        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, SRT_BODY)
        self.assertEqual(result["format"], "srt")


if __name__ == "__main__":
    unittest.main()
