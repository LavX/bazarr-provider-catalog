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

    def test_search_parses_direct_imdb_subtitle_listing_without_extra_page_fetch(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def fake_get(url, config):
            calls.append(url)
            if "search/sublanguageid-all/imdbid-1480055" in url:
                return FakeResponse(url, text=SUBTITLES_HTML)
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = fake_get

        results = provider.search(EPISODE_VIDEO, LANGUAGES, {"skip_wrong_fps": True})

        self.assertEqual(calls, ["https://www.opensubtitles.org/en/search/sublanguageid-all/imdbid-1480055"])
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider_payload"]["subtitle_id"], "1952619105")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("episode", first["matches"])

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


if __name__ == "__main__":
    unittest.main()
