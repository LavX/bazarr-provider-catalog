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
    "hashes": {"opensubtitles": "9f8e7d6c5b4a3210"},
    "original_name": "Game.of.Thrones.S01E01.1080p.WEB-DL",
}

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
        self.assertEqual(first["provider"], "opensubtitles_org")
        self.assertEqual(first["provider_payload"]["mode"], "native")
        self.assertEqual(first["provider_payload"]["subtitle_id"], "1952619105")
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

    def test_download_fetches_direct_zip_and_returns_subtitle_payload(self):
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
                "provider": "opensubtitles_org",
                "mode": "native",
                "subtitle_id": "1952619105",
                "download_url": "https://www.opensubtitles.org/en/subtitles/1952619105/game-of-thrones-winter-is-coming-en",
                "filename": "Game.of.Thrones.S01E01.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(calls, ["https://dl.opensubtitles.org/en/download/sub/1952619105"])
        self.assertEqual(data, SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["content_type"], "application/x-subrip")


if __name__ == "__main__":
    unittest.main()
