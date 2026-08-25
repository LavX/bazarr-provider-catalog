import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "opensubtitles_org"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "opensubtitles_org_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EPISODE_VIDEO = {
    "kind": "episode",
    "series": "Game of Thrones",
    "title": "Winter Is Coming",
    "year": 2011,
    "season": 1,
    "episode": 1,
    "series_imdb_id": "tt0944947",
    "imdb_id": "tt1480055",
    "fps": "23.976",
    "size": 234567890,
    "original_name": "Game.of.Thrones.S01E01.1080p.WEB-DL",
}

HASH_EPISODE_VIDEO = dict(EPISODE_VIDEO, hashes={"opensubtitles": "9f8e7d6c5b4a3210"})

LANGUAGES = [{"alpha3": "eng", "alpha2": "en"}]
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nWinter is coming.\n"


class FakeResponse:
    def __init__(self, url, status_code=200, text="", content=None, headers=None):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = headers or {}

    def close(self):
        pass


class FakeCookieJar(list):
    def set(self, name, value, domain=None, path="/"):
        self.append(type("Cookie", (), {"name": name, "value": value, "domain": domain, "path": path})())

    def update(self, cookies):
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                self.set(name, value)
            return
        for cookie in cookies:
            self.append(cookie)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}
        self.cookies = FakeCookieJar()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected GET: {url}")
        return self.responses.pop(0)


def _zip_bytes(filename="Game.of.Thrones.S01E01.srt", body=SRT_BODY):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(filename, body)
    return output.getvalue()


SEARCH_HTML = """
<table id="search_results">
  <tr><th>poster</th><th>title</th><th>imdb</th><th>subs</th></tr>
  <tr>
    <td></td>
    <td><a class="bnone" href="/en/search/sublanguageid-all/imdbid-1480055">"Game of Thrones" Winter Is Coming (2011)</a></td>
    <td><a href="https://www.imdb.com/title/tt1480055/">IMDb</a></td>
    <td>5</td>
  </tr>
</table>
"""

SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619105">
      <strong><a href="/en/subtitles/1952619105/game-of-thrones-winter-is-coming-en">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619105">4312x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""

HINDI_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619108">
      <strong><a href="/en/subtitles/1952619108/game-of-thrones-winter-is-coming">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619108">12x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""

PORTUGUESE_BR_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619112">
      <strong><a href="/en/subtitles/1952619112/game-of-thrones-winter-is-coming">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619112">8x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""

HI_FORCED_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619109">
      <strong><a href="/en/subtitles/1952619109/game-of-thrones-winter-is-coming-en">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      Hearing Impaired<br />
      Foreign parts only<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619109">22x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""

FORCED_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619113">
      <strong><a href="/en/subtitles/1952619113/game-of-thrones-winter-is-coming-en">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      Foreign parts only<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619113">10x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""

WRONG_FPS_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619110">
      <strong><a href="/en/subtitles/1952619110/game-of-thrones-winter-is-coming-en">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619110">9x</a>
      <span class="p">25.000</span>
    </td>
  </tr>
</table>
"""

HASH_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619120">
      <strong><a href="/en/subtitles/1952619120/game-of-thrones-winter-is-coming-en">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619120">99x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""

WRONG_EPISODE_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619111">
      <strong><a href="/en/subtitles/1952619111/game-of-thrones-the-kingsroad-en">"Game of Thrones" The Kingsroad (2011)</a></strong><br />
      Game.of.Thrones.S01E02.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619111">3x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""


class ManifestSchemaTests(unittest.TestCase):
    def test_manifest_exposes_native_antibot_settings_without_helper_or_xmlrpc_controls(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        properties = manifest["config_schema"]["properties"]

        self.assertEqual(
            sorted(properties),
            [
                "also_foreign",
                "flaresolverr_timeout_ms",
                "flaresolverr_url",
                "only_foreign",
                "request_delay_ms",
                "skip_wrong_fps",
                "use_tag_search",
            ],
        )
        for hidden_field in (
            "is_vip",
            "password",
            "scraper_service_url",
            "timeout",
            "use_ssl",
            "use_web_scraper",
            "username",
        ):
            self.assertNotIn(hidden_field, properties)
        self.assertEqual(manifest["secret_fields"], [])

    def test_manifest_languages_round_trip_through_parser_filtering(self):
        mod = _load_provider_module()
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))

        for language in manifest["languages"]:
            alpha3, _, country = language.partition("-")
            requested = {"alpha3": alpha3}
            if country:
                requested["country_alpha2"] = country
            code = mod._opensubtitles_code(
                mod.LanguageInfo(alpha3=alpha3, country_alpha2=country or None)
            )
            parsed = mod._language_from_opensubtitles_code(code)
            self.assertIsNotNone(parsed, language)
            self.assertTrue(mod._language_requested(parsed, [requested], {}), language)


class AntibotSessionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_http_get_uses_ai_cloudscraper_and_solves_anubis_inline(self):
        session = FakeSession(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/.within.website/?redir=/",
                    status_code=401,
                    text='<script id="anubis_challenge">{}</script>',
                ),
                FakeResponse(
                    "https://www.opensubtitles.org/en/search/subs",
                    text="<html><title>Subtitles</title></html>",
                ),
            ]
        )
        created = []

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                created.append(kwargs)
                return session

        solved_calls = []

        def fake_solve(active_session, challenge_url, original_url, timeout):
            solved_calls.append((active_session, challenge_url, original_url, timeout))
            active_session.cookies.set("techaro.lol-anubis-auth", "ok", domain=".opensubtitles.org")
            return {"techaro.lol-anubis-auth": "ok"}

        self.mod.cloudscraper = FakeCloudscraper
        self.mod.solve_anubis_challenge = fake_solve

        provider = self.mod.OpenSubtitlesOrgProvider()
        response = provider._http_get("https://www.opensubtitles.org/", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1], "https://www.opensubtitles.org/")
        self.assertEqual(session.calls[1][1], "https://www.opensubtitles.org/")
        self.assertIs(solved_calls[0][0], session)
        self.assertEqual(solved_calls[0][1], "https://www.opensubtitles.org/.within.website/?redir=/")
        self.assertEqual(solved_calls[0][2], "https://www.opensubtitles.org/")
        self.assertEqual(created[0]["interpreter"], "native")
        self.assertFalse(created[0]["enable_cookie_persistence"])

    def _provider_with_session(self, session):
        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper
        self.mod._backoff_delay = lambda attempt: 0  # keep the retry backoff instant in tests
        return self.mod.OpenSubtitlesOrgProvider()

    def test_http_get_retries_transient_401_then_succeeds(self):
        # A bare 401 (no Anubis redirect) is a transient anti-bot rate-limit block; the
        # provider should re-solve a fresh challenge rather than fail the whole search.
        session = FakeSession(
            [
                FakeResponse("https://www.opensubtitles.org/en/search", status_code=401, text="blocked"),
                FakeResponse("https://www.opensubtitles.org/en/search", status_code=200, text="<html>ok</html>"),
            ]
        )
        provider = self._provider_with_session(session)
        response = provider._http_get("https://www.opensubtitles.org/en/search", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)

    def test_http_get_resolves_cloudflare_then_anubis_in_one_pass(self):
        # opensubtitles.org layers Cloudflare in front of Anubis: clear CF, then solve the
        # Anubis gate that appears, all within a single attempt (no retry/backoff needed).
        session = FakeSession(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=403,
                    headers={"cf-ray": "abc"},
                    text="cloudflare",
                ),
                FakeResponse(
                    "https://www.opensubtitles.org/.within.website/?redir=/en/search",
                    status_code=401,
                    text='<script id="anubis_challenge">{}</script>',
                ),
                FakeResponse("https://www.opensubtitles.org/en/search", status_code=200, text="<html>ok</html>"),
            ]
        )
        provider = self._provider_with_session(session)
        provider._fallback_to_flaresolverr = lambda url, config: None  # pretend FlareSolverr cleared CF

        solved = []

        def fake_solve(active_session, challenge_url, original_url, timeout):
            solved.append(challenge_url)
            return {"techaro.lol-anubis-auth": "ok"}

        self.mod.solve_anubis_challenge = fake_solve
        response = provider._http_get("https://www.opensubtitles.org/en/search", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 3)  # CF fetch + Anubis re-fetch + clear fetch, one attempt
        self.assertEqual(len(solved), 1)

    def test_http_get_raises_after_exhausting_401_retries(self):
        session = FakeSession(
            [FakeResponse("https://www.opensubtitles.org/en/search", status_code=401, text="blocked")] * 3
        )
        provider = self._provider_with_session(session)
        with self.assertRaises(self.mod.ServiceUnavailable):
            provider._http_get("https://www.opensubtitles.org/en/search", {})
        self.assertEqual(len(session.calls), self.mod.CHALLENGE_RETRY_ATTEMPTS)

    def test_get_session_retries_without_cookie_persistence_for_legacy_cloudscraper(self):
        session = FakeSession([])
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

        provider = self.mod.OpenSubtitlesOrgProvider()
        active_session = provider._get_session()

        self.assertIs(active_session, session)
        self.assertEqual(len(created), 2)
        self.assertFalse(created[0]["enable_cookie_persistence"])
        self.assertNotIn("enable_cookie_persistence", created[1])
        self.assertEqual(created[1]["interpreter"], "native")

    def test_http_get_uses_flaresolverr_after_cloudflare_challenge(self):
        session = FakeSession(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=403,
                    text="<html>Just a moment... challenge-platform</html>",
                    headers={"cf-ray": "abc"},
                ),
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    text="<html><title>Search</title></html>",
                ),
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
                        {"name": "cf_clearance", "value": "clear", "domain": ".opensubtitles.org"}
                    ],
                    "userAgent": "Solved UA",
                }
            }

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.OpenSubtitlesOrgProvider()
        provider._post_flaresolverr = fake_post

        response = provider._http_get(
            "https://www.opensubtitles.org/en/search",
            {
                "flaresolverr_url": "http://127.0.0.1:8191/v1",
                "flaresolverr_timeout_ms": 45000,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.headers["User-Agent"], "Solved UA")
        self.assertEqual(flaresolverr_calls[0][0], "http://127.0.0.1:8191/v1")
        self.assertEqual(flaresolverr_calls[0][1]["cmd"], "request.get")
        self.assertEqual(flaresolverr_calls[0][1]["url"], "https://www.opensubtitles.org/en/search")
        self.assertEqual(flaresolverr_calls[0][1]["maxTimeout"], 45000)

    def test_cloudflare_without_flaresolverr_is_visible_error(self):
        session = FakeSession(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=403,
                    text="<html>Just a moment... challenge-platform</html>",
                    headers={"cf-ray": "abc"},
                ),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.OpenSubtitlesOrgProvider()

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "FlareSolverr"):
            provider._http_get("https://www.opensubtitles.org/en/search", {})

    def test_http_get_does_not_treat_normal_200_page_as_cloudflare_challenge(self):
        session = FakeSession(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=200,
                    text="<html><title>Search</title><script src='/challenge-platform/h/b/scripts.js'></script></html>",
                ),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper
        provider = self.mod.OpenSubtitlesOrgProvider()

        response = provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 1)

    def test_http_get_solves_in_place_anubis_body_before_returning_response(self):
        session = FakeSession(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=403,
                    text=(
                        '<script id="anubis_challenge">'
                        '{"challenge":{"id":"challenge-id","randomData":"abc","difficulty":4}}'
                        "</script>"
                    ),
                ),
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    text="<html><title>Search</title></html>",
                ),
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        solved_calls = []

        def fake_solve(active_session, challenge_url, original_url, timeout):
            solved_calls.append((active_session, challenge_url, original_url, timeout))
            active_session.cookies.set("techaro.lol-anubis-auth", "ok", domain=".opensubtitles.org")
            return {"techaro.lol-anubis-auth": "ok"}

        self.mod.cloudscraper = FakeCloudscraper
        self.mod.solve_anubis_challenge = fake_solve
        provider = self.mod.OpenSubtitlesOrgProvider()

        response = provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(solved_calls[0][1], "https://www.opensubtitles.org/en/search")
        self.assertEqual(solved_calls[0][2], "https://www.opensubtitles.org/en/search")


class NativeSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_uses_native_opensubtitles_pages_and_returns_candidates(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        self.assertEqual(calls[0], "https://www.opensubtitles.org/en/search/sublanguageid-all/imdbid-1480055")
        self.assertEqual(calls[1], "https://www.opensubtitles.org/en/search/sublanguageid-eng/imdbid-1480055")
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "opensubtitles")
        self.assertEqual(first["provider_payload"]["mode"], "native")
        self.assertEqual(first["provider_payload"]["subtitle_id"], "1952619105")
        self.assertEqual(first["provider_payload"]["season"], 1)
        self.assertEqual(first["provider_payload"]["episode"], 1)
        self.assertEqual(first["provider_payload"]["download_url"], "https://www.opensubtitles.org/en/subtitles/1952619105/game-of-thrones-winter-is-coming-en")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("episode", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertEqual(first["display"]["download_count"], 4312)
        self.assertNotIn("hash", first["matches"])
        self.assertFalse(first["hash_verifiable"])
        self.assertIn("score", first)
        self.assertEqual(first["score"], first["score_without_hash"])
        self.assertEqual(first["score_out_of"], 100)

    def test_search_fetches_requested_language_when_direct_imdb_listing_is_unfiltered(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        self.assertEqual(
            calls,
            [
                "https://www.opensubtitles.org/en/search/sublanguageid-all/imdbid-1480055",
                "https://www.opensubtitles.org/en/search/sublanguageid-eng/imdbid-1480055",
            ],
        )
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider_payload"]["subtitle_id"], "1952619105")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("episode", first["matches"])

    def test_search_preserves_forced_and_hearing_impaired_row_flags(self):
        provider = self.mod.OpenSubtitlesOrgProvider()

        def fake_get(url, config):
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=HI_FORCED_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en", "forced": True, "hi": True}],
            {"skip_wrong_fps": True},
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["language"]["forced"])
        self.assertTrue(results[0]["language"]["hi"])
        self.assertTrue(results[0]["hearing_impaired"])

    def test_foreign_config_includes_or_limits_forced_rows(self):
        provider = self.mod.OpenSubtitlesOrgProvider()

        def fake_get(url, config):
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML + FORCED_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        also_results = provider.search(
            EPISODE_VIDEO,
            LANGUAGES,
            {"skip_wrong_fps": True, "also_foreign": True},
        )
        only_results = provider.search(
            EPISODE_VIDEO,
            LANGUAGES,
            {"skip_wrong_fps": True, "only_foreign": True},
        )

        self.assertEqual([item["language"]["forced"] for item in also_results], [False, True])
        self.assertEqual(len(only_results), 1)
        self.assertTrue(only_results[0]["language"]["forced"])

    def test_search_uses_page_language_for_advertised_language_without_slug_suffix(self):
        provider = self.mod.OpenSubtitlesOrgProvider()

        def fake_get(url, config):
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-hin/imdbid-1480055" in url:
                return FakeResponse(url, text=HINDI_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "hin"}],
            {"skip_wrong_fps": True},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "hin")

    def test_search_preserves_regional_language_keys_from_page_language(self):
        provider = self.mod.OpenSubtitlesOrgProvider()

        def fake_get(url, config):
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-pob/imdbid-1480055" in url:
                return FakeResponse(url, text=PORTUGUESE_BR_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {"skip_wrong_fps": True},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")

    def test_wrong_fps_candidate_is_kept_with_lowered_matches(self):
        provider = self.mod.OpenSubtitlesOrgProvider()

        def fake_get(url, config):
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=WRONG_FPS_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["matches"], [])
        self.assertEqual(results[0]["score"], 0)

    def test_tag_search_builds_tag_lookup_before_imdb_lookup(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        context = self.mod.build_search_context(EPISODE_VIDEO, {"use_tag_search": True})

        url = provider._build_search_url("Game of Thrones", context)

        self.assertEqual(
            url,
            "https://www.opensubtitles.org/en/search/sublanguageid-all/tag-Game.of.Thrones.S01E01.1080p.WEB-DL",
        )

    def test_episode_search_without_imdb_sends_and_enforces_season_episode(self):
        video = dict(EPISODE_VIDEO)
        video.pop("imdb_id")
        video.pop("series_imdb_id")
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search2?" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=WRONG_EPISODE_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(video, LANGUAGES, {"skip_wrong_fps": True})

        self.assertIn("Season=1", calls[0])
        self.assertIn("Episode=1", calls[0])
        self.assertEqual(results, [])

    def test_release_name_excludes_uploader_count_and_fps_metadata(self):
        rows = self.mod._parse_subtitle_rows(
            SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/sublanguageid-eng/imdbid-1480055",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["release_name"], "Game.of.Thrones.S01E01.1080p.WEB-DL")
        self.assertEqual(rows[0]["filename"], "Game.of.Thrones.S01E01.1080p.WEB-DL.en.srt")

    def test_match_uses_normalized_episode_for_list_inputs(self):
        multi_episode_video = dict(EPISODE_VIDEO)
        multi_episode_video["episode"] = [1, 2]
        context = self.mod.build_search_context(multi_episode_video, {})

        matches = self.mod._matches_for_video(
            multi_episode_video,
            "episode",
            "Game of Thrones",
            "Game.of.Thrones.S01E01.1080p.WEB-DL",
            2011,
            "tt1480055",
            context.season,
            context.episode,
            None,
        )

        self.assertIn("episode", matches)
        self.assertIn("season", matches)

    def test_download_rejects_non_challenge_4xx_instead_of_wrapping_error_body(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        error_body = b"Subtitle has been removed."
        session = FakeSession(
            [
                FakeResponse(
                    "https://dl.opensubtitles.org/en/download/sub/1952619105",
                    status_code=404,
                    content=error_body,
                )
            ]
        )

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "HTTP 404"):
            provider.download(
                {
                    "provider": "opensubtitles",
                    "mode": "native",
                    "subtitle_id": "1952619105",
                    "download_url": "https://www.opensubtitles.org/en/subtitles/1952619105/game-of-thrones-winter-is-coming-en",
                    "filename": "Game.of.Thrones.S01E01.srt",
                },
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_returns_archive_bytes_and_selected_member(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []
        archive = _zip_bytes()

        def fake_get(url, config):
            calls.append(url)
            return FakeResponse(
                url,
                content=archive,
                headers={"content-type": "application/zip"},
            )

        provider._http_get = fake_get

        result = provider.download(
            {
                "provider": "opensubtitles",
                "mode": "native",
                "subtitle_id": "1952619105",
                "download_url": "https://www.opensubtitles.org/en/subtitles/1952619105/game-of-thrones-winter-is-coming-en",
                "filename": "Game.of.Thrones.S01E01.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        # The host extracts the archive: the worker returns the raw archive bytes and
        # the member it selected, with no content/encoding fields.
        self.assertEqual(calls, ["https://dl.opensubtitles.org/en/download/sub/1952619105"])
        data = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.srt")
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_selects_episode_member_when_filename_absent(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        archive = _zip_bytes(filename="Game.of.Thrones.S01E01.1080p.srt")

        provider._http_get = lambda url, config: FakeResponse(
            url, content=archive, headers={"content-type": "application/zip"}
        )

        result = provider.download(
            {
                "provider": "opensubtitles",
                "mode": "native",
                "subtitle_id": "1952619105",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        # No preferred filename: the first subtitle member is selected.
        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.1080p.srt")
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())

    def test_download_returns_rar_archive_for_host_to_pick_by_episode(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        rar_body = b"Rar!\x1a\x07\x00rest-of-archive-bytes"

        provider._http_get = lambda url, config: FakeResponse(url, content=rar_body)

        result = provider.download(
            {
                "provider": "opensubtitles",
                "mode": "native",
                "subtitle_id": "1952619105",
                "filename": "Game.of.Thrones.S01E01.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        data = base64.b64decode(result["archive_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, rar_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(rar_body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("member", result)

    def test_download_follows_html_page_to_zip_and_returns_archive(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        archive = _zip_bytes()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "download/sub" in url:
                return FakeResponse(
                    url,
                    text='<html><a href="/en/subtitleserve/sub/1952619105">Download</a></html>',
                    content=b'<html><a href="/en/subtitleserve/sub/1952619105">Download</a></html>',
                )
            return FakeResponse(url, content=archive)

        provider._http_get = fake_get

        result = provider.download(
            {
                "provider": "opensubtitles",
                "mode": "native",
                "subtitle_id": "1952619105",
                "filename": "Game.of.Thrones.S01E01.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.srt")
        self.assertEqual(
            base64.b64decode(result["archive_b64"].encode("ascii"), validate=True), archive
        )

    def test_download_returns_direct_non_archive_subtitle_as_content(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        provider._http_get = lambda url, config: FakeResponse(url, content=SRT_BODY)

        result = provider.download(
            {
                "provider": "opensubtitles",
                "mode": "native",
                "subtitle_id": "1952619105",
                "filename": "Game.of.Thrones.S01E01.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(
            base64.b64decode(result["content_b64"].encode("ascii"), validate=True), SRT_BODY
        )
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertNotIn("archive_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        provider._http_get = lambda url, config: FakeResponse(url, content=b"")

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "empty"):
            provider.download(
                {"provider": "opensubtitles", "mode": "native", "subtitle_id": "1952619105"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_rejects_html_error_page_without_link(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        body = b"<html><body>Subtitle has been removed.</body></html>"
        provider._http_get = lambda url, config: FakeResponse(url, text=body.decode(), content=body)

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "no subtitle link"):
            provider.download(
                {"provider": "opensubtitles", "mode": "native", "subtitle_id": "1952619105"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )


class MovieHashMatchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_queries_moviehash_listing_and_awards_hash_match(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "moviehash-9f8e7d6c5b4a3210" in url and "sublanguageid-eng" in url:
                return FakeResponse(url, text=HASH_SUBTITLES_HTML)
            if "moviehash-9f8e7d6c5b4a3210" in url and "sublanguageid-all" in url:
                return FakeResponse(url, text=HASH_SUBTITLES_HTML)
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(HASH_EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        # The moviehash listing is queried (with the byte size segment) so
        # hash-matched results come back.
        self.assertTrue(
            any("moviehash-9f8e7d6c5b4a3210" in url for url in calls),
            calls,
        )
        self.assertTrue(
            any("moviebytesize-234567890" in url for url in calls),
            calls,
        )

        hashed = next(item for item in results if item["provider_payload"]["subtitle_id"] == "1952619120")
        self.assertIn("hash", hashed["matches"])
        self.assertTrue(hashed["hash_verifiable"])
        self.assertEqual(hashed["provider_payload"]["moviehash"], "9f8e7d6c5b4a3210")
        # The hash match lifts the score to the maximum, while score_without_hash
        # reflects only the non-hash matches (series/season/episode/imdb_id).
        self.assertEqual(hashed["score"], 100)
        self.assertEqual(
            hashed["score_without_hash"],
            self.mod._score_from_matches(hashed["matches"], include_hash=False),
        )
        self.assertLess(hashed["score_without_hash"], hashed["score"])

    def test_regular_listing_rows_are_not_awarded_a_hash_match(self):
        provider = self.mod.OpenSubtitlesOrgProvider()

        def fake_get(url, config):
            if "moviehash-9f8e7d6c5b4a3210" in url:
                # The moviehash listing finds nothing for this video.
                return FakeResponse(url, text="<table id=\"search_results\"></table>")
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        # The video carries a hash, but the imdb/title listing rows must not be
        # tagged with that hash: only genuine moviehash-listing rows may.
        results = provider.search(HASH_EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        imdb_row = next(item for item in results if item["provider_payload"]["subtitle_id"] == "1952619105")
        self.assertNotIn("hash", imdb_row["matches"])
        self.assertFalse(imdb_row["hash_verifiable"])
        self.assertIsNone(imdb_row["provider_payload"]["moviehash"])

    def test_search_without_hash_does_not_query_moviehash_listing(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SEARCH_HTML)
            if "search/sublanguageid-eng/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        provider.search(EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        self.assertFalse(any("moviehash" in url for url in calls), calls)


class TransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.slept = []
        self._orig_sleep = self.mod.time.sleep
        self.mod.time.sleep = lambda seconds: self.slept.append(seconds)

    def tearDown(self):
        self.mod.time.sleep = self._orig_sleep

    def _install_session(self, steps):
        # Each step is either a callable raising an exception or a FakeResponse.
        calls = []

        class RetrySession:
            def __init__(self):
                self.headers = {}
                self.cookies = FakeCookieJar()
                self._steps = list(steps)

            def get(self, url, **kwargs):
                calls.append(url)
                step = self._steps.pop(0)
                if isinstance(step, Exception):
                    raise step
                return step

        session = RetrySession()

        class FakeCloudscraper:
            @staticmethod
            def create_scraper(**kwargs):
                return session

        self.mod.cloudscraper = FakeCloudscraper
        return calls

    def test_http_get_retries_after_transient_urlerror_then_succeeds(self):
        import urllib.error

        success = FakeResponse(
            "https://www.opensubtitles.org/en/search",
            text="<html><title>Search</title></html>",
        )
        calls = self._install_session(
            [urllib.error.URLError("connection reset"), success]
        )
        provider = self.mod.OpenSubtitlesOrgProvider()

        response = provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.slept), 1)

    def test_http_get_retries_after_transient_503_then_succeeds(self):
        success = FakeResponse(
            "https://www.opensubtitles.org/en/search",
            text="<html><title>Search</title></html>",
        )
        calls = self._install_session(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=503,
                    text="upstream is briefly unavailable",
                ),
                success,
            ]
        )
        provider = self.mod.OpenSubtitlesOrgProvider()

        response = provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.slept), 1)

    def test_http_get_honors_retry_after_header_on_transient_429(self):
        success = FakeResponse(
            "https://www.opensubtitles.org/en/search",
            text="<html><title>Search</title></html>",
        )
        calls = self._install_session(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=429,
                    text="slow down",
                    headers={"Retry-After": "2"},
                ),
                success,
            ]
        )
        provider = self.mod.OpenSubtitlesOrgProvider()

        response = provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.slept, [2.0])

    def test_http_get_does_not_retry_4xx_and_propagates_first_failure(self):
        calls = self._install_session(
            [
                FakeResponse(
                    "https://www.opensubtitles.org/en/search",
                    status_code=404,
                    text="not found",
                )
            ]
        )
        provider = self.mod.OpenSubtitlesOrgProvider()

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "HTTP 404"):
            provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.slept, [])

    def test_http_get_gives_up_after_max_attempts_on_persistent_transient_error(self):
        import urllib.error

        calls = self._install_session(
            [
                urllib.error.URLError("connection reset"),
                urllib.error.URLError("connection reset"),
                urllib.error.URLError("connection reset"),
            ]
        )
        provider = self.mod.OpenSubtitlesOrgProvider()

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "request failed"):
            provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(len(calls), self.mod.RETRY_MAX_ATTEMPTS)
        self.assertEqual(len(self.slept), self.mod.RETRY_MAX_ATTEMPTS - 1)

    def test_http_get_does_not_retry_non_transport_exception(self):
        calls = self._install_session([ValueError("bad parse")])
        provider = self.mod.OpenSubtitlesOrgProvider()

        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "request failed"):
            provider._http_get("https://www.opensubtitles.org/en/search", {})

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.slept, [])


class PowDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_solve_pow_raises_service_unavailable_when_deadline_passed(self):
        # An already-expired deadline must abort instead of looping forever on a
        # high-difficulty challenge.
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.mod._solve_pow("random", 64, deadline=self.mod.time.monotonic() - 1)

    def test_solve_anubis_pow_challenge_aborts_when_budget_exhausted(self):
        challenge_json = (
            '<script id="anubis_challenge">'
            '{"challenge":{"id":"abc","randomData":"seed","difficulty":64,"method":"fast"}}'
            "</script>"
        )
        session = FakeSession([FakeResponse("https://www.opensubtitles.org/.within.website/", text=challenge_json)])

        # A tiny timeout makes the proof-of-work budget expire almost
        # immediately, so an unsolvable difficulty cannot hang the worker.
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.mod.solve_anubis_challenge(
                session,
                "https://www.opensubtitles.org/.within.website/?redir=/",
                "https://www.opensubtitles.org/",
                timeout=0.001,
            )

    def test_solve_anubis_preact_challenge_aborts_when_budget_exhausted(self):
        challenge_json = (
            '<script id="anubis_challenge">'
            '{"challenge":{"id":"abc","randomData":"seed","difficulty":64,"method":"preact"}}'
            "</script>"
        )
        session = FakeSession([FakeResponse("https://www.opensubtitles.org/.within.website/", text=challenge_json)])

        with self.assertRaises(self.mod.ServiceUnavailable):
            self.mod.solve_anubis_challenge(
                session,
                "https://www.opensubtitles.org/.within.website/?redir=/",
                "https://www.opensubtitles.org/",
                timeout=0.001,
            )


class SerbianLanguageTests(unittest.TestCase):
    """OpenSubtitles.org identifies Serbian by a legacy code that differs from
    the standard one, and the site has historically emitted more than one
    spelling. Getting either direction wrong makes Serbian silently unavailable:
    the search asks for a language id the site does not use, and rows that do
    come back are dropped by the language filter."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_request_uses_the_legacy_serbian_id(self):
        language = self.mod.LanguageInfo(alpha3="srp", alpha2="sr")
        self.assertEqual(self.mod._opensubtitles_code(language), "scc")

    def test_legacy_serbian_codes_all_resolve_to_serbian(self):
        for code in ("scc", "srp"):
            with self.subTest(code=code):
                language = self.mod._language_from_opensubtitles_code(code)
                self.assertIsNotNone(language, f"{code} was dropped entirely")
                self.assertEqual(language.alpha3, "srp")

    def test_two_letter_serbian_resolves(self):
        language = self.mod._language_from_opensubtitles_code("sr")
        self.assertIsNotNone(language)
        self.assertEqual(language.alpha3, "srp")

    def test_serbian_rows_satisfy_a_serbian_request(self):
        requested = [{"alpha3": "srp", "alpha2": "sr"}]
        for code in ("scc", "sr"):
            with self.subTest(code=code):
                language = self.mod._language_from_opensubtitles_code(code)
                self.assertTrue(
                    self.mod._language_requested(language, requested),
                    f"a row tagged {code} was filtered out of a Serbian search",
                )

    def test_unresolvable_row_carries_no_language_rather_than_english(self):
        """Mislabelling is strictly worse than carrying no language: it silently
        pollutes English results with subtitles in some other language. The row
        itself is kept, so the page still registers as a direct listing, and it
        is dropped when candidates are built."""
        language = self.mod._language_from_subtitle_url(
            "https://www.opensubtitles.org/en/subtitles/123/some-title",
            fallback_url="https://www.opensubtitles.org/en/search/sublanguageid-all",
        )
        self.assertIsNone(language)

    def test_resolvable_slug_still_wins(self):
        language = self.mod._language_from_subtitle_url(
            "https://www.opensubtitles.org/en/subtitles/123/some-title-scc",
            fallback_url="https://www.opensubtitles.org/en/search/sublanguageid-all",
        )
        self.assertIsNotNone(language)
        self.assertEqual(language.alpha3, "srp")


class DirectListingDetectionTests(unittest.TestCase):
    """Row parsing must keep detecting a direct subtitle listing even when a
    row's language cannot be resolved from its link.

    `_regular_candidates` branches on whether `_parse_subtitle_rows` returned
    anything. If unresolvable rows are dropped there, an unfiltered listing whose
    rows carry no language suffix looks empty, the page is misparsed as a movie
    results page, and the search returns nothing. That would regress working
    searches, which is worse than the mislabelling this change set out to fix.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def test_rows_without_a_resolvable_language_are_still_detected(self):
        rows = self.mod._parse_subtitle_rows(
            HINDI_SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/sublanguageid-all/imdbid-tt1480055",
        )
        self.assertTrue(
            rows, "an unfiltered listing must still register as a direct listing"
        )

    def test_such_rows_carry_no_language_rather_than_a_guess(self):
        rows = self.mod._parse_subtitle_rows(
            HINDI_SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/sublanguageid-all/imdbid-tt1480055",
        )
        self.assertIsNone(rows[0]["language"])


class MontenegrinTests(unittest.TestCase):
    """The manifest declares srp-ME, and the site has a distinct id for it."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_montenegrin_uses_its_own_site_id(self):
        language = self.mod.LanguageInfo(
            alpha3="srp", alpha2="sr", country_alpha2="ME"
        )
        self.assertEqual(self.mod._opensubtitles_code(language), "mne")

    def test_plain_serbian_still_uses_the_legacy_serbian_id(self):
        language = self.mod.LanguageInfo(alpha3="srp", alpha2="sr")
        self.assertEqual(self.mod._opensubtitles_code(language), "scc")

    def test_montenegrin_rows_resolve_back_to_montenegrin(self):
        language = self.mod._language_from_opensubtitles_code("mne")
        self.assertIsNotNone(language)
        self.assertEqual(language.alpha3, "srp")
        self.assertEqual(language.country_alpha2, "ME")


class SlugFalsePositiveTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_a_spanish_title_ending_in_ser_does_not_satisfy_a_serbian_request(self):
        """The slug resolver takes the last dash-separated token, so mapping a
        plausible-looking three letter code the site does not actually emit
        would turn ordinary titles into false Serbian rows. "ser" is a common
        Spanish and Portuguese infinitive.

        An unknown token still passes through as an unrecognised code, which is
        pre-existing behaviour and cannot be tightened without a real language
        database this plugin deliberately does not carry. What matters is that
        it is never served to a Serbian search."""
        language = self.mod._language_from_subtitle_url(
            "https://www.opensubtitles.org/en/subtitles/123/llegar-a-ser",
            fallback_url="https://www.opensubtitles.org/en/search/sublanguageid-all",
        )
        self.assertFalse(
            self.mod._language_requested(language, [{"alpha3": "srp", "alpha2": "sr"}])
        )


class TitleOnlyDirectListingTests(unittest.TestCase):
    """A video with no IMDb id searches through /en/search2, which can land on a
    direct subtitle listing whose links carry no language suffix.

    Those rows are kept with no language so the page still registers as a direct
    listing, then dropped at candidate build time. Unless the requested language
    is refetched they are dropped for good and the search returns nothing, which
    is exactly what the tag and IMDb paths were fixed to stop doing.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def _title_only_video(self):
        video = dict(EPISODE_VIDEO)
        video.pop("imdb_id")
        video.pop("series_imdb_id")
        return video

    def test_redirected_listing_is_refetched_in_the_requested_language(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        landing_url = "https://www.opensubtitles.org/en/search/sublanguageid-all/idmovie-77777"
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search2?" in url:
                # The site redirects a title search onto the real listing URL.
                return FakeResponse(landing_url, text=HINDI_SUBTITLES_HTML)
            if "sublanguageid-hin/idmovie-77777" in url:
                return FakeResponse(url, text=HINDI_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(
            self._title_only_video(),
            [{"alpha3": "hin"}],
            {"skip_wrong_fps": True},
        )

        self.assertIn(
            "https://www.opensubtitles.org/en/search/sublanguageid-hin/idmovie-77777",
            calls,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "hin")

    def test_unredirected_title_search_is_refetched_with_a_language_filter(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search2?" in url and "SubLanguageID=hin" in url:
                return FakeResponse(
                    "https://www.opensubtitles.org/en/search/sublanguageid-hin/idmovie-77777",
                    text=HINDI_SUBTITLES_HTML,
                )
            if "search2?" in url:
                return FakeResponse(url, text=HINDI_SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(
            self._title_only_video(),
            [{"alpha3": "hin"}],
            {"skip_wrong_fps": True},
        )

        self.assertTrue(any("SubLanguageID=hin" in url for url in calls))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "hin")

    def test_in_place_language_filter_still_resolves_its_rows(self):
        """The site may honour SubLanguageID without redirecting off /en/search2.

        The filter then lives in the query string only, so reading the language
        from the path alone leaves every row unresolved, they are all dropped at
        candidate build time, and the search returns nothing: the exact failure
        this branch exists to remove.
        """
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            # Served in place: the response URL is the one we asked for.
            return FakeResponse(url, text=HINDI_SUBTITLES_HTML)

        provider._http_get = fake_get

        results = provider.search(
            self._title_only_video(),
            [{"alpha3": "hin"}],
            {"skip_wrong_fps": True},
        )

        self.assertTrue(any("SubLanguageID=hin" in url for url in calls))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "hin")


class UnresolvedRowDeduplicationTests(unittest.TestCase):
    """A row kept only so the page registers as a direct listing must not burn
    its subtitle id.

    Such a row carries no language and is dropped when candidates are built. If
    the id is recorded as seen anyway, a later pass over the same subtitle, on a
    page that does resolve it, is silently skipped as a duplicate.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def test_a_row_with_no_language_does_not_consume_its_id(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        context = self.mod.build_search_context(EPISODE_VIDEO, {})
        items = self.mod._parse_subtitle_rows(
            HINDI_SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/sublanguageid-all/idmovie-77777",
        )
        self.assertIsNone(items[0]["language"])
        seen = set()
        provider._candidates_from_items(
            items, {"title": "x", "kind": "episode"}, EPISODE_VIDEO, [{"alpha3": "hin"}], context, {}, seen
        )
        self.assertEqual(seen, set())

    def test_a_later_pass_can_still_resolve_a_row_an_earlier_pass_could_not(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        context = self.mod.build_search_context(EPISODE_VIDEO, {})
        result = {"title": "Game of Thrones", "kind": "episode"}
        languages = [{"alpha3": "hin"}]
        unresolved = self.mod._parse_subtitle_rows(
            HINDI_SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/sublanguageid-all/idmovie-77777",
        )
        resolved = self.mod._parse_subtitle_rows(
            HINDI_SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/sublanguageid-hin/idmovie-77777",
        )
        self.assertEqual(unresolved[0]["subtitle_id"], resolved[0]["subtitle_id"])

        seen = set()
        self.assertEqual(
            provider._candidates_from_items(
                unresolved, result, EPISODE_VIDEO, languages, context, {}, seen
            ),
            [],
        )
        self.assertEqual(
            len(
                provider._candidates_from_items(
                    resolved, result, EPISODE_VIDEO, languages, context, {}, seen
                )
            ),
            1,
            "the resolved pass was skipped as a duplicate of the unresolved one",
        )


class LanguageFilteredUrlTests(unittest.TestCase):
    """The filter is matched on URL components, not on the raw string.

    Since the URL now comes back from the site rather than being built here, its
    casing and shape are not ours to assume. The sibling reader _SUBLANGUAGE_RE
    is already case insensitive.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def test_the_path_segment_matches_whatever_case_the_site_used(self):
        self.assertEqual(
            self.mod._language_filtered_url(
                "https://www.opensubtitles.org/en/search/SubLanguageID-all/idmovie-77777",
                "hin",
            ),
            "https://www.opensubtitles.org/en/search/sublanguageid-hin/idmovie-77777",
        )

    def test_a_search_term_is_never_rewritten(self):
        filtered = self.mod._language_filtered_url(
            "https://www.opensubtitles.org/en/search2?MovieName=sublanguageid-all&action=search",
            "hin",
        )
        self.assertIn("MovieName=sublanguageid-all", filtered)
        self.assertIn("SubLanguageID=hin", filtered)

    def test_a_fragment_is_left_alone(self):
        filtered = self.mod._language_filtered_url(
            "https://www.opensubtitles.org/en/search2?MovieName=x&action=search#sublanguageid-all",
            "hin",
        )
        self.assertTrue(filtered.endswith("#sublanguageid-all"), filtered)
        self.assertIn("SubLanguageID=hin", filtered)

    def test_a_trailing_slash_does_not_disable_the_filter(self):
        filtered = self.mod._language_filtered_url(
            "https://www.opensubtitles.org/en/search2/?MovieName=x&action=search",
            "hin",
        )
        self.assertIsNotNone(filtered)
        self.assertIn("SubLanguageID=hin", filtered)

    def test_a_listing_already_narrowed_to_one_language_is_left_alone(self):
        self.assertIsNone(
            self.mod._language_filtered_url(
                "https://www.opensubtitles.org/en/search/sublanguageid-eng/imdbid-1480055",
                "hin",
            )
        )


class LandedUrlTrustTests(unittest.TestCase):
    """The URL a fetch landed on becomes a refetch target, the base row hrefs are
    resolved against and the source of the row language. A redirect off the site
    must not be able to steer any of that."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_an_off_site_redirect_is_not_followed_or_joined_against(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            return FakeResponse(
                "https://evil.example/en/search/sublanguageid-all/idmovie-1",
                text=SUBTITLES_HTML,
            )

        provider._http_get = fake_get

        video = dict(EPISODE_VIDEO)
        video.pop("imdb_id")
        video.pop("series_imdb_id")
        results = provider.search(video, LANGUAGES, {"skip_wrong_fps": True})

        self.assertFalse(
            [url for url in calls if "evil.example" in url],
            f"an off-site redirect was refetched: {calls}",
        )
        for result in results:
            self.assertNotIn("evil.example", result["page_link"])
            self.assertNotIn(
                "evil.example", result["provider_payload"]["download_url"]
            )

    def test_only_http_urls_on_the_site_are_trusted(self):
        for url in (
            "https://www.opensubtitles.org/en/search/imdbid-1480055",
            "http://opensubtitles.org/en/search/imdbid-1480055",
            "https://dl.opensubtitles.org/en/download/sub/1",
        ):
            with self.subTest(url=url):
                self.assertTrue(self.mod._is_site_url(url))
        for url in (
            "https://evil.example/en/search/imdbid-1480055",
            "https://evil-opensubtitles.org/en/search",
            "https://www.opensubtitles.org.evil.example/en/search",
            "file:///etc/passwd",
            "ftp://www.opensubtitles.org/x",
            "",
        ):
            with self.subTest(url=url):
                self.assertFalse(self.mod._is_site_url(url))

    def test_a_canonicalising_redirect_does_not_disable_the_refetch(self):
        """The site may answer the sublanguageid-all listing on its canonical URL,
        which carries no filter. The refetch has to fall back to the URL we asked
        for, or a language search silently stops being filtered."""
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "sublanguageid-all" in url:
                return FakeResponse(
                    "https://www.opensubtitles.org/en/search/imdbid-1480055",
                    text=HINDI_SUBTITLES_HTML,
                )
            return FakeResponse(url, text=HINDI_SUBTITLES_HTML)

        provider._http_get = fake_get

        results = provider.search(
            EPISODE_VIDEO, [{"alpha3": "hin"}], {"skip_wrong_fps": True}
        )

        self.assertTrue(
            any("sublanguageid-hin" in url for url in calls),
            f"the language refetch never happened: {calls}",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "hin")

    def test_a_canonicalising_redirect_does_not_unlabel_the_rows(self):
        """A filtered fetch answered on a URL that drops the filter still has to
        resolve its rows, from the filter we asked for."""
        self.assertIsNone(
            self.mod._language_from_page_url(
                "https://www.opensubtitles.org/en/search/imdbid-1480055"
            )
        )
        rows = self.mod._parse_subtitle_rows(
            HINDI_SUBTITLES_HTML,
            "https://www.opensubtitles.org/en/search/imdbid-1480055",
            "https://www.opensubtitles.org/en/search/sublanguageid-hin/imdbid-1480055",
        )
        self.assertEqual(rows[0]["language"].alpha3, "hin")


class RefetchContainmentTests(unittest.TestCase):
    """The speculative language refetch is an optimisation on top of results the
    search already holds. A transient anti-bot block on it must not throw those
    away: recovering the whole search from one 401/403/429 is the property the
    challenge retry work was built for."""

    def setUp(self):
        self.mod = _load_provider_module()

    def _title_only_video(self):
        video = dict(EPISODE_VIDEO)
        video.pop("imdb_id")
        video.pop("series_imdb_id")
        return video

    def _search_with_failing_refetch(self, error):
        provider = self.mod.OpenSubtitlesOrgProvider()
        landing_url = "https://www.opensubtitles.org/en/search/sublanguageid-all/idmovie-77777"

        def fake_get(url, config):
            if "search2?" in url:
                return FakeResponse(landing_url, text=SUBTITLES_HTML)
            raise error

        provider._http_get = fake_get
        return provider.search(
            self._title_only_video(), LANGUAGES, {"skip_wrong_fps": True}
        )

    def test_a_rate_limited_refetch_keeps_the_candidates_already_in_hand(self):
        results = self._search_with_failing_refetch(self.mod.RateLimited("429"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "eng")

    def test_a_blocked_refetch_keeps_the_candidates_already_in_hand(self):
        results = self._search_with_failing_refetch(
            self.mod.ServiceUnavailable("OpenSubtitles.org HTTP 403")
        )
        self.assertEqual(len(results), 1)

    def test_an_unfilterable_listing_is_not_fetched_once_per_language(self):
        """When the result URL cannot carry a language filter every language
        collapses onto the same URL, so fetching it once per requested language
        is pure amplification against a site that throttles bursts."""
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            return FakeResponse(url, text=SUBTITLES_HTML)

        provider._http_get = fake_get
        result = {
            "title": "Game of Thrones",
            "kind": "episode",
            "url": "https://www.opensubtitles.org/en/search/idmovie-77777",
        }
        context = self.mod.build_search_context(EPISODE_VIDEO, {})
        provider._subtitles_for_result(
            result,
            EPISODE_VIDEO,
            [{"alpha3": "eng"}, {"alpha3": "deu"}, {"alpha3": "hin"}],
            context,
            {},
        )
        self.assertEqual(calls, [result["url"]])


SERBIAN_SLUG_SUBTITLES_HTML = """
<table>
  <tr>
    <td id="main1952619109">
      <strong><a href="/en/subtitles/1952619109/game-of-thrones-winter-is-coming-scc">"Game of Thrones" Winter Is Coming (2011)</a></strong><br />
      Game.of.Thrones.S01E01.1080p.WEB-DL<br />
      <a href="/en/profile/uploader">syncmaster</a>
      <a href="/en/subtitleserve/sub/1952619109">4312x</a>
      <span class="p">23.976</span>
    </td>
  </tr>
</table>
"""


class MontenegrinListingTests(unittest.TestCase):
    """Montenegrin has its own site id, so a Montenegrin request no longer maps
    onto the plain Serbian one. A row on a Montenegrin listing whose slug still
    carries the Serbian code then reads as country-less Serbian, which the
    country match rejects, and the search returns nothing."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_a_serbian_slug_on_a_montenegrin_listing_stays_montenegrin(self):
        language = self.mod._language_from_subtitle_url(
            "https://www.opensubtitles.org/en/subtitles/123/some-title-scc",
            fallback_url="https://www.opensubtitles.org/en/search/sublanguageid-mne/imdbid-1480055",
        )
        self.assertEqual(language.alpha3, "srp")
        self.assertEqual(language.country_alpha2, "ME")

    def test_a_serbian_listing_does_not_gain_a_country(self):
        language = self.mod._language_from_subtitle_url(
            "https://www.opensubtitles.org/en/subtitles/123/some-title-scc",
            fallback_url="https://www.opensubtitles.org/en/search/sublanguageid-scc/imdbid-1480055",
        )
        self.assertEqual(language.alpha3, "srp")
        self.assertIsNone(language.country_alpha2)

    def test_a_different_language_never_takes_the_listing_country(self):
        language = self.mod._language_from_subtitle_url(
            "https://www.opensubtitles.org/en/subtitles/123/some-title-ger",
            fallback_url="https://www.opensubtitles.org/en/search/sublanguageid-mne/imdbid-1480055",
        )
        self.assertEqual(language.alpha3, "deu")
        self.assertIsNone(language.country_alpha2)

    def test_a_montenegrin_search_returns_the_rows_of_its_own_listing(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            return FakeResponse(url, text=SERBIAN_SLUG_SUBTITLES_HTML)

        provider._http_get = fake_get

        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "srp", "alpha2": "sr", "country_alpha2": "ME"}],
            {"skip_wrong_fps": True},
        )

        self.assertTrue(any("sublanguageid-mne" in url for url in calls), calls)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "srp")


if __name__ == "__main__":
    unittest.main()
