import base64
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "napiprojekt"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "napiprojekt_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHREK_VIDEO = json.loads((FIXTURE_DIR / "napiprojekt_video_shrek.json").read_text())
ATTACK_VIDEO = json.loads((FIXTURE_DIR / "napiprojekt_video_attack_on_titan_s02e01.json").read_text())
SUBTITLE_BYTES = "00:00:48: Zażółć gęślą jaźń...\r\n".encode("cp1250")

SEARCH_HTML = """
<div class="greyBoxCatcher">
  <a href="https://www.imdb.com/title/tt0126029/">IMDb</a>
  <a class="movieTitleCat" href="napisy-4684-Shrek-(2001)">Shrek (2001)</a>
</div>
<div class="greyBoxCatcher">
  <a class="movieTitleCat" href="napisy-1-Other-(2001)">Other</a>
</div>
""".encode("utf-8")

LIST_HTML = """
<table>
  <tr title="<b>Autor:</b> Jan Kowalski (real) <b>Video rozdzielczość:</b> 1080p< <b>Video FPS:</b> 23.976<">
    <td><a class="tableA" href="napiprojekt:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">download</a></td>
    <td>
      <p>ignored</p><p>700 MB</p><p>ignored</p><p>01:30:00</p><p>Jan Kowalski</p><p>2024-01-02</p>
    </td>
  </tr>
  <tr title="<b>Autor:</b> Automat (machine) <b>Video rozdzielczość:</b> 720p< <b>Video FPS:</b> 25<">
    <td><a class="tableA" href="napiprojekt:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">download</a></td>
    <td>
      <p>ignored</p><p>650 MB</p><p>ignored</p><p>01:29:00</p><p>Automat</p><p>2023-12-31</p>
    </td>
  </tr>
</table>
""".encode("utf-8")

BLANK_AUTHOR_HTML = """
<table>
  <tr title="<b>Video rozdzielczość:</b> 1080p< <b>Video FPS:</b> 23.976<">
    <td><a class="tableA" href="napiprojekt:cccccccccccccccccccccccccccccccc">download</a></td>
    <td>
      <p>ignored</p><p>700 MB</p><p>ignored</p><p>01:30:00</p><p></p><p>2024-01-02</p>
    </td>
  </tr>
</table>
""".encode("utf-8")

EPISODE_SEARCH_HTML = """
<div class="greyBoxCatcher">
  <a class="movieTitleCat" href="napisy-38715-Attack-on-Titan-(2013)">Attack on Titan (2013)</a>
</div>
""".encode("utf-8")


class HashTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_known_hashes_generate_expected_subhash(self):
        self.assertEqual(self.mod.get_subhash("444563eef63f83d47cabb888f7a45113"), "a6f09")
        self.assertEqual(self.mod.get_subhash("fe93bb3a7743c39e12c8d7c4a864dff1"), "8410a")

    def test_hash_download_url_contains_required_query(self):
        url = self.mod.hash_download_url("444563eef63f83d47cabb888f7a45113", "pl")

        self.assertIn("unit_napisy/dl.php", url)
        self.assertIn("l=PL", url)
        self.assertIn("f=444563eef63f83d47cabb888f7a45113", url)
        self.assertIn("t=a6f09", url)


class CatalogParseTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_catalog_search_prefers_matching_imdb_id(self):
        entries = self.mod.parse_catalog_search(SEARCH_HTML)
        selected = self.mod.select_catalog_title(SHREK_VIDEO, entries)

        self.assertEqual(selected["slug"], "4684-Shrek-(2001)")
        self.assertEqual(selected["matches"], ["imdb_id", "title", "year"])

    def test_catalog_rows_extract_metadata_and_release_info(self):
        rows = self.mod.parse_subtitle_rows(LIST_HTML, ["title", "year"])

        first = rows[0]
        self.assertEqual(first["hash"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(first["author"], "Jan Kowalski")
        self.assertEqual(first["resolution"], "1080p")
        self.assertEqual(first["fps"], "23.976")
        self.assertIn("Autor: Jan Kowalski", first["release_info"])
        self.assertEqual(first["matches"], ["title", "year"])

    def test_author_filters_skip_machine_rows_and_require_real_name(self):
        rows = self.mod.parse_subtitle_rows(
            LIST_HTML,
            ["title"],
            only_authors=True,
            only_real_names=True,
        )

        self.assertEqual([item["hash"] for item in rows], ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"])

    def test_only_authors_rejects_blank_author_rows(self):
        rows = self.mod.parse_subtitle_rows(
            BLANK_AUTHOR_HTML,
            ["title"],
            only_authors=True,
        )

        self.assertEqual(rows, [])


class NapiProjektProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_hash_and_catalog_results(self):
        provider = self.mod.NapiProjektProvider()
        called = []
        hash_url = self.mod.hash_download_url("444563eef63f83d47cabb888f7a45113", "pl")
        catalog_url = "https://www.napiprojekt.pl/napisy1,7,0-dla-4684-Shrek-(2001)"

        def get_stub(url, config=None, timeout=15, referer=None):
            del config, timeout, referer
            called.append(("GET", url))
            if url == hash_url:
                return SUBTITLE_BYTES
            if url == catalog_url:
                return LIST_HTML
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, data, config=None, timeout=15, referer=None):
            del config, timeout, referer
            called.append(("POST", url, data))
            self.assertEqual(data["queryString"], "Shrek")
            self.assertEqual(data["queryKind"], "2")
            return SEARCH_HTML

        provider._http_get = get_stub
        provider._http_post = post_stub
        results = provider.search(
            SHREK_VIDEO,
            [{"alpha3": "pol", "alpha2": "pl"}],
            {"only_authors": False, "only_real_names": False},
        )

        self.assertEqual(called[0], ("GET", hash_url))
        self.assertEqual(called[1][0], "POST")
        self.assertEqual(called[2], ("GET", catalog_url))
        self.assertEqual([item["provider_payload"]["source"] for item in results[:2]], ["hash", "catalog"])
        self.assertEqual(results[0]["score"], 100)
        self.assertEqual(results[0]["provider_payload"]["hash"], "444563eef63f83d47cabb888f7a45113")
        self.assertEqual(results[1]["provider_payload"]["hash"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertEqual(provider._content_cache["444563eef63f83d47cabb888f7a45113"], SUBTITLE_BYTES)

    def test_author_filter_skips_hash_lookup(self):
        provider = self.mod.NapiProjektProvider()
        called = []

        def fail_hash_get(url, config=None, timeout=15, referer=None):
            del config, timeout, referer
            called.append(("GET", url))
            if "unit_napisy" in url:
                raise AssertionError("hash lookup should be skipped when author filters are enabled")
            return LIST_HTML

        provider._http_get = fail_hash_get
        provider._http_post = lambda url, data, config=None, timeout=15, referer=None: SEARCH_HTML
        results = provider.search(
            SHREK_VIDEO,
            [{"alpha3": "pol", "alpha2": "pl"}],
            {"only_authors": True, "only_real_names": True},
        )

        self.assertEqual([item["provider_payload"]["source"] for item in results], ["catalog"])
        self.assertEqual(called, [("GET", "https://www.napiprojekt.pl/napisy1,7,0-dla-4684-Shrek-(2001)")])

    def test_episode_catalog_url_adds_season_episode_suffix(self):
        url = self.mod.catalog_subtitles_url("38715-Shingeki-no-kyojin-(2013)", ATTACK_VIDEO)

        self.assertEqual(url, "https://www.napiprojekt.pl/napisy1,7,0-dla-38715-Shingeki-no-kyojin-(2013)-s02e01")

    def test_episode_catalog_search_adds_exact_episode_matches_without_imdb(self):
        provider = self.mod.NapiProjektProvider()
        video = dict(ATTACK_VIDEO)
        video.pop("series_imdb_id", None)
        video["hashes"] = {}
        called = []

        def get_stub(url, config=None, timeout=15, referer=None):
            del config, timeout, referer
            called.append(("GET", url))
            if url == "https://www.napiprojekt.pl/napisy1,7,0-dla-38715-Attack-on-Titan-(2013)-s02e01":
                return LIST_HTML
            raise AssertionError(f"unexpected GET: {url}")

        def post_stub(url, data, config=None, timeout=15, referer=None):
            del config, timeout, referer
            called.append(("POST", url, data))
            return EPISODE_SEARCH_HTML

        provider._http_get = get_stub
        provider._http_post = post_stub
        results = provider.search(video, [{"alpha3": "pol", "alpha2": "pl"}], {})

        self.assertEqual(called[0][0], "POST")
        self.assertEqual(called[1], ("GET", "https://www.napiprojekt.pl/napisy1,7,0-dla-38715-Attack-on-Titan-(2013)-s02e01"))
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])
        self.assertGreater(results[0]["score"], 88)


class NapiProjektProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_hash_and_returns_raw_text_payload(self):
        provider = self.mod.NapiProjektProvider()
        provider._http_get = lambda url, config=None, timeout=15, referer=None: SUBTITLE_BYTES

        result = provider.download(
            {
                "provider": "napiprojekt",
                "schema": 1,
                "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "language": "pol",
                "format": "txt",
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SUBTITLE_BYTES)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SUBTITLE_BYTES).hexdigest())
        self.assertEqual(result["format"], "txt")
        self.assertEqual(result["content_type"], "text/plain")
        self.assertEqual(result["encoding"], "cp1250")

    def test_download_reuses_hash_content_cached_during_search(self):
        provider = self.mod.NapiProjektProvider()
        provider._content_cache["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] = SUBTITLE_BYTES

        def fail_get(url, config=None, timeout=15, referer=None):
            del config, timeout, referer
            raise AssertionError(f"unexpected GET: {url}")

        provider._http_get = fail_get
        result = provider.download(
            {
                "provider": "napiprojekt",
                "schema": 1,
                "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "language": "pol",
                "format": "txt",
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SUBTITLE_BYTES)

    def test_download_reports_utf8_for_flaresolverr_text_bytes(self):
        provider = self.mod.NapiProjektProvider()
        body = "00:00:48: Zażółć gęślą jaźń...\r\n".encode("utf-8")
        provider._content_cache["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] = body

        result = provider.download(
            {
                "provider": "napiprojekt",
                "schema": 1,
                "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "language": "pol",
                "format": "txt",
            },
            {"alpha3": "pol", "alpha2": "pl"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["encoding"], "utf-8")

    def test_download_rejects_not_found_marker(self):
        provider = self.mod.NapiProjektProvider()
        provider._http_get = lambda url, config=None, timeout=15, referer=None: b"NPc0"

        with self.assertRaisesRegex(ValueError, "no subtitle"):
            provider.download({"hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "language": "pol"}, {"alpha3": "pol"}, {})


class CloudflareTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_challenge_detection_uses_cloudflare_headers(self):
        self.assertTrue(
            self.mod.is_cloudflare_challenge(
                403,
                {"cf-mitigated": "challenge"},
                b"<title>Just a moment...</title>",
            )
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

    def test_cloudscraper_challenge_raises_without_flaresolverr(self):
        provider = self.mod.NapiProjektProvider()
        fake_scraper = _FakeScraper(_FakeResponse(403, b"<title>Just a moment...</title>", {"cf-mitigated": "challenge"}))
        original = self.mod.cloudscraper.create_scraper
        self.mod.cloudscraper.create_scraper = lambda **kwargs: fake_scraper
        try:
            with self.assertRaisesRegex(self.mod.CloudflareBlockedError, "no FlareSolverr"):
                provider._http_post("https://www.napiprojekt.pl/ajax/search_catalog.php", {"queryString": "Shrek"}, {})
        finally:
            self.mod.cloudscraper.create_scraper = original

    def test_cloudscraper_solves_anubis_inline_before_retrying_original_url(self):
        provider = self.mod.NapiProjektProvider()
        fake_scraper = _FakeScraper(
            [
                _FakeResponse(
                    401,
                    b'<script id="anubis_challenge">{}</script>',
                    url="https://www.napiprojekt.pl/.within.website/?redir=/ajax/search_catalog.php",
                ),
                _FakeResponse(200, b"<html>ok</html>", url="https://www.napiprojekt.pl/ajax/search_catalog.php"),
            ]
        )
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        original_create = self.mod.cloudscraper.create_scraper
        original_solve = self.mod.solve_anubis_challenge
        self.mod.cloudscraper.create_scraper = lambda **kwargs: fake_scraper
        self.mod.solve_anubis_challenge = fake_solve
        try:
            body = provider._http_post("https://www.napiprojekt.pl/ajax/search_catalog.php", {"queryString": "Shrek"}, {})
        finally:
            self.mod.cloudscraper.create_scraper = original_create
            self.mod.solve_anubis_challenge = original_solve

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(len(fake_scraper.calls), 2)
        self.assertEqual(fake_scraper.calls[0][1], "https://www.napiprojekt.pl/ajax/search_catalog.php")
        self.assertEqual(fake_scraper.calls[1][1], "https://www.napiprojekt.pl/ajax/search_catalog.php")
        self.assertIs(solved_calls[0][0], fake_scraper)
        self.assertEqual(
            solved_calls[0][1],
            "https://www.napiprojekt.pl/.within.website/?redir=/ajax/search_catalog.php",
        )
        self.assertEqual(solved_calls[0][2], "https://www.napiprojekt.pl/ajax/search_catalog.php")

    def test_cloudscraper_solves_anubis_body_before_retrying_original_url(self):
        provider = self.mod.NapiProjektProvider()
        anubis_body = (
            b'<html><head><meta http-equiv="refresh" '
            b'content="0; url=/.within.website/?redir=/ajax/search_catalog.php"></head></html>'
        )
        fake_scraper = _FakeScraper(
            [
                _FakeResponse(
                    200,
                    anubis_body,
                    url="https://www.napiprojekt.pl/ajax/search_catalog.php",
                ),
                _FakeResponse(200, b"<html>ok</html>", url="https://www.napiprojekt.pl/ajax/search_catalog.php"),
            ]
        )
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        original_create = self.mod.cloudscraper.create_scraper
        original_solve = self.mod.solve_anubis_challenge
        self.mod.cloudscraper.create_scraper = lambda **kwargs: fake_scraper
        self.mod.solve_anubis_challenge = fake_solve
        try:
            body = provider._http_post("https://www.napiprojekt.pl/ajax/search_catalog.php", {"queryString": "Shrek"}, {})
        finally:
            self.mod.cloudscraper.create_scraper = original_create
            self.mod.solve_anubis_challenge = original_solve

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(len(fake_scraper.calls), 2)
        self.assertIs(solved_calls[0][0], fake_scraper)
        self.assertEqual(solved_calls[0][1], "https://www.napiprojekt.pl/ajax/search_catalog.php")
        self.assertEqual(solved_calls[0][2], "https://www.napiprojekt.pl/ajax/search_catalog.php")

    def test_flaresolverr_timeout_is_capped_below_worker_deadline(self):
        self.assertEqual(
            self.mod._flaresolverr_timeout_ms({"flaresolverr_timeout_ms": 45000}),
            25000,
        )

    def test_flaresolverr_subtitle_download_reports_utf8_encoding(self):
        text = "Zażółć gęślą jaźń"

        def fake_urlopen(request, timeout):
            del request, timeout
            return _FakeUrlOpenResponse(
                json.dumps({"status": "ok", "solution": {"response": text}}).encode("utf-8")
            )

        with patch.object(self.mod.urllib.request, "urlopen", fake_urlopen):
            body = self.mod._flaresolverr_request(
                "GET",
                self.mod.hash_download_url("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "pl"),
                config={"flaresolverr_url": "http://127.0.0.1:8191/v1"},
            )

        payload = self.mod._content_payload(body, "txt")

        self.assertEqual(payload["encoding"], "utf-8")
        self.assertEqual(base64.b64decode(payload["content_b64"]).decode("utf-8"), text)

    def test_flaresolverr_post_sends_form_content_type(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            response = {
                "status": "ok",
                "solution": {"response": "<html>ok</html>"},
            }
            return _FakeUrlOpenResponse(json.dumps(response).encode("utf-8"))

        with patch.object(self.mod.urllib.request, "urlopen", fake_urlopen):
            self.mod._flaresolverr_request(
                "POST",
                self.mod.CATALOG_SEARCH_URL,
                data={"queryString": "Shrek", "queryKind": "2"},
                config={"flaresolverr_url": "http://127.0.0.1:8191/v1"},
                referer=self.mod.CATALOG_BASE_URL + "/",
            )

        payload = json.loads(requests[0][0].data.decode("utf-8"))
        self.assertEqual(payload["postData"], "queryString=Shrek&queryKind=2")
        self.assertEqual(payload["headers"]["Referer"], self.mod.CATALOG_BASE_URL + "/")
        self.assertEqual(payload["headers"]["Content-Type"], "application/x-www-form-urlencoded")


class _FakeResponse:
    def __init__(self, status_code, content, headers=None, url="https://www.napiprojekt.pl/ajax/search_catalog.php"):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = content.decode("utf-8", errors="replace")
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status for {self.status_code}")


class _FakeScraper:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []

    def get(self, url, *args, **kwargs):
        self.calls.append(("GET", url, args, kwargs))
        return self.responses.pop(0)

    def post(self, url, *args, **kwargs):
        self.calls.append(("POST", url, args, kwargs))
        return self.responses.pop(0)


class _FakeUrlOpenResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body
