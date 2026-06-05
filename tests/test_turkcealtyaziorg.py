import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "turkcealtyaziorg"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("turkcealtyaziorg_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _movie_search_page():
    return b"""
    <html>
      <head><meta name="description" content="Inception altyazi"></head>
      <body>
        <div class="altyazi-list-wrapper">
          <div>
            <div class="altsonsez2">
              <div class="alisim"><div class="fl"><a href="/mov/123/inception.html">Inception</a></div></div>
              <div class="aldil"><span class="flagtr"></span></div>
              <div class="alcevirmen">cevirmen</div>
              <div class="alfps">23.976</div>
              <div class="alindirme">100</div>
              <div class="ta-container">
                <div class="ripdiv"><span class="rps r12"></span> / x264-GROUP <img src="/images/isitme.png"></div>
                <div class="datediv">2 gun once</div>
              </div>
            </div>
            <div class="altsonsez2">
              <div class="alisim"><div class="fl"><a href="/mov/124/inception-en.html">Inception EN</a></div></div>
              <div class="aldil"><span class="flagen"></span></div>
              <div class="alcevirmen"><span class="rip5"></span></div>
              <div class="alfps">24</div>
              <div class="alindirme">50</div>
              <div class="ta-container">
                <div class="ripdiv"><span class="rip9"></span> / WEB-DL-TEAM</div>
                <div class="datediv">1 saat once</div>
              </div>
            </div>
          </div>
        </div>
      </body>
    </html>
    """


def _title_page_search_page():
    return b"""
    <html>
      <body>
        <div class="altyazi-list-wrapper">
          <div>
            <div class="subtitle-row">
              <div class="alisim"><div class="fl"><a href="/mov/999/title-page.html">Inception title page row</a></div></div>
              <div class="aldil"><span class="flagtr"></span></div>
              <div class="alcevirmen">title-uploader</div>
              <div class="ta-container">
                <div class="ripdiv"><span class="rps r12"></span> / TITLE-GROUP</div>
              </div>
            </div>
          </div>
        </div>
      </body>
    </html>
    """


def _episode_search_page():
    return b"""
    <html>
      <head><meta name="description" content="Series altyazi"></head>
      <body>
        <div class="altyazi-list-wrapper">
          <div>
            <div class="altsonsez1 sezon_1">
              <div class="alisim"><div class="fl"><a href="/serie/200/s01e02.html">Episode subtitle</a></div></div>
              <div class="aldil"><span class="flagen"></span></div>
              <div class="alcd"><b>1</b><b>2</b></div>
              <div class="alcevirmen">episode-uploader</div>
              <div class="alfps">23.976</div>
              <div class="alindirme">15</div>
              <div class="ta-container">
                <div class="ripdiv"><span class="rps r8"></span> / HDTV-GROUP</div>
                <div class="datediv">3 hafta once</div>
              </div>
            </div>
            <div class="altsonsez1 sezon_1">
              <div class="alisim"><div class="fl"><a href="/serie/201/season-pack.html">Season pack</a></div></div>
              <div class="aldil"><span class="flagtr"></span></div>
              <div class="alcd"><b>1</b><b>Paket</b></div>
              <div class="alcevirmen">pack-uploader</div>
              <div class="alfps">23.976</div>
              <div class="alindirme">25</div>
              <div class="ta-container">
                <div class="ripdiv"><span class="rip4"></span> / BDRip-GROUP</div>
                <div class="datediv">1 ay once</div>
              </div>
            </div>
          </div>
        </div>
      </body>
    </html>
    """


def _download_page():
    return b"""
    <html>
      <body>
        <form>
          <input type="hidden" name="idid" value="11">
          <input type="hidden" name="altid" value="22">
          <input type="hidden" name="sidid" value="33">
        </form>
      </body>
    </html>
    """


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("subtitle.srt", "1\r\n00:00:01,000 --> 00:00:02,000\r\nMerhaba\r\n")
    return stream.getvalue()


class FakeCookieJar(dict):
    def set(self, name, value, domain=None, path=None):
        self[name] = {"value": value, "domain": domain, "path": path}


class FakeResponse:
    def __init__(self, status_code=200, body=b"ok", headers=None, url="https://turkcealtyazi.org", text=None):
        self.status_code = status_code
        self.content = body
        self.text = text if text is not None else body.decode("utf-8", "ignore")
        self.headers = headers or {}
        self.url = url


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.cookies = FakeCookieJar()
        self.calls = []

    def get(self, url, headers=None, cookies=None, timeout=None, allow_redirects=True):
        self.calls.append(("GET", url, headers, cookies, timeout, allow_redirects))
        return self.responses.pop(0)

    def post(self, url, data=None, headers=None, cookies=None, timeout=None, allow_redirects=True):
        self.calls.append(("POST", url, data, headers, cookies, timeout, allow_redirects))
        return self.responses.pop(0)


class TurkceAltyaziSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_empty_without_imdb_id(self):
        provider = self.mod.TurkceAltyaziOrgProvider()

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "tur"}], {}), [])

    def test_default_user_agent_is_browser_like(self):
        provider = self.mod.TurkceAltyaziOrgProvider()

        self.assertIn("Mozilla/5.0", provider._headers({})["User-Agent"])

    def test_search_skips_unsupported_language_without_network(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.fail(f"unexpected URL: {url}")

        results = provider.search({"kind": "movie", "imdb_id": "tt1375666"}, [{"alpha3": "spa"}], {})

        self.assertEqual(results, [])

    def test_access_check_uses_normal_http_timeout(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, allow_redirects=True, config=None):
            del headers, cookies, allow_redirects, config
            calls.append((url, timeout))
            return self.mod.HttpResponse(200, b"home", {})

        provider._http_get = get_response
        provider._ensure_access({}, {})

        self.assertEqual(calls, [("https://turkcealtyazi.org", self.mod.HTTP_TIMEOUT_SECONDS)])

    def test_movie_search_uses_imdb_id_cookies_user_agent_and_parses_rows(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, allow_redirects=True, config=None):
            del timeout, allow_redirects
            calls.append((url, headers, cookies))
            if url == "https://turkcealtyazi.org":
                return self.mod.HttpResponse(200, b"home", {})
            return self.mod.HttpResponse(200, _movie_search_page(), {})

        provider._http_get = get_response
        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666", "release_group": "GROUP"},
            [{"alpha3": "tur"}, {"alpha3": "eng"}],
            {"cookies": "cf_clearance=token; PHPSESSID=session", "user_agent": "UnitTest/1.0"},
        )

        self.assertEqual(calls[0][0], "https://turkcealtyazi.org")
        self.assertEqual(calls[0][1]["User-Agent"], "UnitTest/1.0")
        self.assertEqual(calls[0][1]["Referer"], "https://turkcealtyazi.org")
        self.assertEqual(calls[0][2]["cf_clearance"], "token")
        self.assertEqual(calls[1][0], "https://turkcealtyazi.org/find.php?cat=sub&find=1375666")
        self.assertEqual([item["language"]["alpha3"] for item in results], ["tur", "eng"])
        self.assertEqual(results[0]["provider"], "turkcealtyaziorg")
        self.assertTrue(results[0]["hearing_impaired"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["page_url"], "https://turkcealtyazi.org/mov/123/inception.html")
        self.assertEqual(results[0]["display"]["uploader"], "cevirmen")
        self.assertIn("BluRay", results[0]["release_info"])

    def test_movie_search_preserves_leading_zero_imdb_id(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        calls = []

        def get_response(url, headers, cookies, timeout=30, allow_redirects=True, config=None):
            del headers, cookies, timeout, allow_redirects, config
            calls.append(url)
            if url == "https://turkcealtyazi.org":
                return self.mod.HttpResponse(200, b"home", {})
            return self.mod.HttpResponse(200, _movie_search_page(), {})

        provider._http_get = get_response
        provider.search(
            {"kind": "movie", "title": "Brazil", "imdb_id": "tt0088846"},
            [{"alpha3": "tur"}],
            {},
        )

        self.assertEqual(calls[1], "https://turkcealtyazi.org/find.php?cat=sub&find=0088846")

    def test_candidate_includes_alpha2_and_hash_verifiable(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.mod.HttpResponse(
            200, b"home" if url == "https://turkcealtyazi.org" else _movie_search_page(), {}
        )

        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "tur"}, {"alpha3": "eng"}],
            {},
        )

        self.assertEqual(results[0]["language"]["alpha2"], "tr")
        self.assertEqual(results[1]["language"]["alpha2"], "en")
        self.assertEqual(results[0]["language"]["alpha3"], "tur")
        for result in results:
            self.assertIn("hash_verifiable", result)
            self.assertFalse(result["hash_verifiable"])
            self.assertIn("alpha2", result["language"])

    def test_movie_search_parses_title_page_rows_without_latest_list_classes(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.mod.HttpResponse(
            200, b"home" if url == "https://turkcealtyazi.org" else _title_page_search_page(), {}
        )

        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666", "release_group": "TITLE"},
            [{"alpha3": "tur"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["page_url"], "https://turkcealtyazi.org/mov/999/title-page.html")
        self.assertEqual(results[0]["display"]["uploader"], "title-uploader")
        self.assertIn("release_group", results[0]["matches"])

    def test_episode_search_filters_season_episode_and_keeps_season_pack(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.mod.HttpResponse(
            200, b"home" if url == "https://turkcealtyazi.org" else _episode_search_page(), {}
        )

        results = provider.search(
            {"kind": "episode", "series": "Example", "series_imdb_id": "tt0903747", "season": 1, "episode": 2},
            [{"alpha3": "eng"}, {"alpha3": "tur"}],
            {},
        )

        self.assertEqual([item["language"]["alpha3"] for item in results], ["eng", "tur"])
        self.assertEqual(results[0]["provider_payload"]["episode"], 2)
        self.assertFalse(results[0]["provider_payload"]["is_pack"])
        self.assertEqual(results[1]["provider_payload"]["episode"], 2)
        self.assertTrue(results[1]["provider_payload"]["is_pack"])
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_returns_empty_for_not_found_meta(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.mod.HttpResponse(
            200,
            b'<html><head><meta name="description" content="404 Error"></head></html>',
            {},
        )

        self.assertEqual(
            provider.search({"kind": "movie", "imdb_id": "tt1375666"}, [{"alpha3": "tur"}], {}),
            [],
        )

    def test_search_reports_cloudflare_challenge(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.mod.HttpResponse(
            403,
            b"<html><title>Just a moment...</title></html>",
            {"cf-mitigated": "challenge"},
        )

        with self.assertRaisesRegex(PermissionError, "Cloudflare"):
            provider.search({"kind": "movie", "imdb_id": "tt1375666"}, [{"alpha3": "tur"}], {})

    def test_http_get_uses_ai_cloudscraper_by_default(self):
        session = FakeSession([FakeResponse()])
        created = []

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                created.append(kwargs)
                return session

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.TurkceAltyaziOrgProvider()

        response = provider._http_get(
            "https://turkcealtyazi.org",
            provider._headers({}),
            {},
            config={},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(created[0]["interpreter"], "native")
        self.assertFalse(created[0]["enable_cookie_persistence"])
        self.assertEqual(session.calls[0][0], "GET")

    def test_get_session_retries_without_cookie_persistence_for_legacy_cloudscraper(self):
        session = FakeSession([FakeResponse()])
        created = []

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                created.append(dict(kwargs))
                if "enable_cookie_persistence" in kwargs:
                    raise TypeError(
                        "Session.__init__() got an unexpected keyword argument 'enable_cookie_persistence'"
                    )
                return session

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.TurkceAltyaziOrgProvider()

        provider._http_get("https://turkcealtyazi.org", provider._headers({}), {}, config={})

        self.assertEqual(len(created), 2)
        self.assertFalse(created[0]["enable_cookie_persistence"])
        self.assertNotIn("enable_cookie_persistence", created[1])
        self.assertEqual(created[1]["interpreter"], "native")

    def test_http_get_solves_anubis_inline_before_retrying_original_url(self):
        session = FakeSession(
            [
                FakeResponse(
                    401,
                    b'<script id="anubis_challenge">{}</script>',
                    url="https://turkcealtyazi.org/.within.website/?redir=/find.php",
                ),
                FakeResponse(200, b"<html>solved</html>", url="https://turkcealtyazi.org/find.php?cat=sub&find=1375666"),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        solved_calls = []

        def fake_solve(active_session, challenge_url, original_url, timeout):
            solved_calls.append((active_session, challenge_url, original_url, timeout))
            active_session.cookies.set("techaro.lol-anubis-auth", "ok", domain=".turkcealtyazi.org")
            return {"techaro.lol-anubis-auth": "ok"}

        self.mod.cloudscraper = FakeCloudscraper
        self.mod.solve_anubis_challenge = fake_solve
        provider = self.mod.TurkceAltyaziOrgProvider()

        response = provider._http_get(
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
            provider._headers({}),
            {},
            config={},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1], "https://turkcealtyazi.org/find.php?cat=sub&find=1375666")
        self.assertEqual(session.calls[1][1], "https://turkcealtyazi.org/find.php?cat=sub&find=1375666")
        self.assertIs(solved_calls[0][0], session)
        self.assertEqual(
            solved_calls[0][1],
            "https://turkcealtyazi.org/.within.website/?redir=/find.php",
        )
        self.assertEqual(
            solved_calls[0][2],
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
        )

    def test_http_get_solves_anubis_body_before_retrying_original_url(self):
        anubis_body = (
            b'<html><head><meta http-equiv="refresh" '
            b'content="0; url=/.within.website/?redir=/find.php"></head></html>'
        )
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    anubis_body,
                    url="https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
                ),
                FakeResponse(200, b"<html>solved</html>", url="https://turkcealtyazi.org/find.php?cat=sub&find=1375666"),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        solved_calls = []

        def fake_solve(active_session, challenge_url, original_url, timeout):
            solved_calls.append((active_session, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        self.mod.cloudscraper = FakeCloudscraper
        self.mod.solve_anubis_challenge = fake_solve
        provider = self.mod.TurkceAltyaziOrgProvider()

        response = provider._http_get(
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
            provider._headers({}),
            {},
            config={},
        )

        self.assertEqual(response.body, b"<html>solved</html>")
        self.assertEqual(len(session.calls), 2)
        self.assertIs(solved_calls[0][0], session)
        self.assertEqual(solved_calls[0][1], "https://turkcealtyazi.org/find.php?cat=sub&find=1375666")
        self.assertEqual(solved_calls[0][2], "https://turkcealtyazi.org/find.php?cat=sub&find=1375666")

    def test_http_get_uses_flaresolverr_after_cloudflare_challenge(self):
        session = FakeSession(
            [
                FakeResponse(
                    403,
                    b"<html><title>Just a moment...</title></html>",
                    {"cf-mitigated": "challenge"},
                ),
                FakeResponse(200, b"<html>solved</html>"),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        flaresolverr_calls = []

        def fake_post(url, payload, timeout):
            flaresolverr_calls.append((url, payload, timeout))
            return {
                "solution": {
                    "cookies": [
                        {"name": "cf_clearance", "value": "clear", "domain": ".turkcealtyazi.org"}
                    ],
                    "userAgent": "Solved UA",
                }
            }

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._post_flaresolverr = fake_post

        response = provider._http_get(
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
            provider._headers({}),
            {},
            config={
                "flaresolverr_url": "http://127.0.0.1:8191/v1",
                "flaresolverr_timeout_ms": 45000,
            },
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.headers["User-Agent"], "Solved UA")
        self.assertEqual(flaresolverr_calls[0][0], "http://127.0.0.1:8191/v1")
        self.assertEqual(flaresolverr_calls[0][1]["cmd"], "request.get")
        self.assertEqual(
            flaresolverr_calls[0][1]["url"],
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
        )
        self.assertEqual(flaresolverr_calls[0][1]["maxTimeout"], 45000)
        self.assertEqual(session.cookies["cf_clearance"]["value"], "clear")

    def test_flaresolverr_retry_drops_stale_request_cookies(self):
        session = FakeSession(
            [
                FakeResponse(
                    403,
                    b"<html><title>Just a moment...</title></html>",
                    {"cf-mitigated": "challenge"},
                ),
                FakeResponse(200, b"<html>solved</html>"),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._post_flaresolverr = lambda url, payload, timeout: {
            "solution": {
                "cookies": [
                    {"name": "cf_clearance", "value": "fresh", "domain": ".turkcealtyazi.org"}
                ],
                "userAgent": "Solved UA",
            }
        }

        provider._http_get(
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
            provider._headers({}),
            {"cf_clearance": "stale", "PHPSESSID": "keep"},
            config={"flaresolverr_url": "http://127.0.0.1:8191/v1"},
        )

        retry_cookies = session.calls[1][3]
        # The refreshed FlareSolverr cookie must not be masked by the stale value.
        self.assertNotIn("cf_clearance", retry_cookies or {})
        # Cookies FlareSolverr did not touch are still forwarded.
        self.assertEqual((retry_cookies or {}).get("PHPSESSID"), "keep")
        self.assertEqual(session.cookies["cf_clearance"]["value"], "fresh")

    def test_http_get_uses_flaresolverr_after_non_403_cloudflare_challenge(self):
        session = FakeSession(
            [
                FakeResponse(
                    503,
                    b"<html><title>Just a moment...</title></html>",
                    {},
                ),
                FakeResponse(200, b"<html>solved</html>"),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._post_flaresolverr = lambda url, payload, timeout: {
            "solution": {
                "cookies": [{"name": "cf_clearance", "value": "clear", "domain": ".turkcealtyazi.org"}],
                "userAgent": "Solved UA",
            }
        }

        response = provider._http_get(
            "https://turkcealtyazi.org/find.php?cat=sub&find=1375666",
            provider._headers({}),
            {},
            config={"flaresolverr_url": "http://127.0.0.1:8191/v1"},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(len(session.calls), 2)


class TurkceAltyaziDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_archive(self, archive_body):
        provider = self.mod.TurkceAltyaziOrgProvider()
        self.calls = []

        def get_response(url, headers, cookies, timeout=30, allow_redirects=True, config=None):
            del timeout, allow_redirects, config
            self.calls.append(("GET", url, headers, cookies))
            return self.mod.HttpResponse(200, _download_page(), {})

        def post_response(url, data, headers, cookies, timeout=30, config=None):
            del timeout, config
            self.calls.append(("POST", url, data, headers, cookies))
            return self.mod.HttpResponse(200, archive_body, {"content-type": "application/octet-stream"})

        provider._http_get = get_response
        provider._http_post = post_response
        return provider

    def test_download_posts_hidden_form_and_returns_raw_zip_for_host(self):
        body = _zip_body()
        provider = self._provider_with_archive(body)

        result = provider.download(
            {
                "page_url": "https://turkcealtyazi.org/mov/123/inception.html",
                "release_info": "BluRay,x264-GROUP",
                "filename": "inception.zip",
                "season": 1,
                "episode": 2,
            },
            {"alpha3": "tur"},
            {"cookies": "cf_clearance=token", "user_agent": "UnitTest/1.0"},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 2)
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", result)
        self.assertNotIn("member", result)
        self.assertNotIn("encoding", result)
        # The hidden download form is still posted with the page referer.
        self.assertEqual(self.calls[1][0], "POST")
        self.assertEqual(self.calls[1][1], "https://turkcealtyazi.org/ind")
        self.assertEqual(self.calls[1][2], {"idid": "11", "altid": "22", "sidid": "33"})
        self.assertEqual(self.calls[1][3]["Referer"], "https://turkcealtyazi.org/mov/123/inception.html")

    def test_download_returns_raw_rar_for_host(self):
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider = self._provider_with_archive(body)

        result = provider.download(
            {
                "page_url": "https://turkcealtyazi.org/serie/200/s01e02.html",
                "release_info": "HDTV-GROUP",
                "filename": "episode.rar",
                "season": 1,
                "episode": 7,
            },
            {"alpha3": "tur"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 7)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_archive_episode_is_none_for_movie(self):
        body = _zip_body()
        provider = self._provider_with_archive(body)

        result = provider.download(
            {
                "page_url": "https://turkcealtyazi.org/mov/123/inception.html",
                "filename": "inception.zip",
            },
            {"alpha3": "tur"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertIsNone(result["episode"])

    def test_download_direct_subtitle_body_stays_content_mode(self):
        provider = self._provider_with_archive(b"1\r\n00:00:01,000 --> 00:00:02,000\nMerhaba\r\n")

        result = provider.download(
            {
                "page_url": "https://turkcealtyazi.org/mov/123/inception.html",
                "filename": "inception.srt",
            },
            {"alpha3": "tur"},
            {},
        )

        content = base64.b64decode(result["content_b64"])
        self.assertEqual(content, b"1\n00:00:01,000 --> 00:00:02,000\nMerhaba\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["format"], "srt")
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", result)
        self.assertNotIn("archive_b64", result)

    def test_download_rejects_html_error_page(self):
        provider = self._provider_with_archive(
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "page_url": "https://turkcealtyazi.org/mov/123/inception.html",
                    "filename": "inception.zip",
                },
                {"alpha3": "tur"},
                {},
            )

    def test_download_rejects_empty_body(self):
        provider = self._provider_with_archive(b"   \r\n  ")

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "page_url": "https://turkcealtyazi.org/mov/123/inception.html",
                    "filename": "inception.zip",
                },
                {"alpha3": "tur"},
                {},
            )

    def test_search_episode_carries_season_and_episode_in_payload(self):
        provider = self.mod.TurkceAltyaziOrgProvider()
        provider._http_get = lambda url, headers, cookies, timeout=30, allow_redirects=True, config=None: self.mod.HttpResponse(
            200, b"home" if url == "https://turkcealtyazi.org" else _episode_search_page(), {}
        )

        results = provider.search(
            {"kind": "episode", "series": "Example", "series_imdb_id": "tt0903747", "season": 1, "episode": 2},
            [{"alpha3": "eng"}, {"alpha3": "tur"}],
            {},
        )

        # download() needs episode (and season) for host-side member selection.
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 2)


if __name__ == "__main__":
    unittest.main()
