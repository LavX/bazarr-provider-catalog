"""Browser identity regression checks at outgoing request and challenge seams."""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_provider(provider_id):
    path = ROOT / "providers" / provider_id / "provider.py"
    spec = importlib.util.spec_from_file_location(f"browser_identity_{provider_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BodyResponse:
    def __init__(self, body=b"{}"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class BrowserIdentityTests(unittest.TestCase):
    def test_subf2m_uses_stable_generated_default_and_configured_override(self):
        module = load_provider("subf2m")
        seen = []

        def urlopen(request, **_kwargs):
            seen.append(request.get_header("User-agent"))
            return BodyResponse(b"page")

        with patch.object(module.ua_generator, "generate", return_value=SimpleNamespace(text="Generated UA")) as generate, patch.object(
            module.urllib.request, "urlopen", side_effect=urlopen
        ):
            provider = module.SubF2MProvider()
            provider._http_get("https://subf2m.co/a")
            provider._http_get("https://subf2m.co/b")
            provider._http_get("https://subf2m.co/c", config={"user_agent": "Pasted UA"})
        self.assertEqual(seen, ["Generated UA", "Generated UA", "Pasted UA"])
        generate.assert_called_once()

    def test_subdl_preserves_environment_override_at_request_seam(self):
        module = load_provider("subdl")
        seen = []

        def send(request, _timeout):
            seen.append(request.get_header("User-agent"))
            return b"{}"

        with patch.object(module.ua_generator, "generate", return_value=SimpleNamespace(text="Generated UA")), patch.object(
            module, "_urlopen_with_retry", side_effect=send
        ), patch.dict(os.environ, {"SZ_USER_AGENT": "Pasted UA"}):
            provider = module.SubDLProvider()
            provider._http_get_json({})
            self.assertEqual(seen[-1], "Pasted UA")
            del os.environ["SZ_USER_AGENT"]
            provider._http_get_json({})
            self.assertEqual(seen[-1], "Generated UA")

    def test_subsource_sends_one_generated_identity_for_api_requests(self):
        module = load_provider("subsource")
        seen = []

        def urlopen(request, **_kwargs):
            seen.append(request.get_header("User-agent"))
            return BodyResponse()

        with patch.object(module.ua_generator, "generate", return_value=SimpleNamespace(text="Generated UA")) as generate, patch.object(
            module.urllib.request, "urlopen", side_effect=urlopen
        ), patch.dict(os.environ, {"SZ_USER_AGENT": ""}):
            provider = module.SubSourceProvider()
            provider._http_get_json("subtitles", {}, {"api_key": "test-key"})
            provider._http_get_json("subtitles", {}, {"api_key": "test-key"})
        self.assertEqual(seen, ["Generated UA", "Generated UA"])
        generate.assert_called_once()

    def test_subs4series_rebuilds_scraper_and_replays_helper_ua_and_cookie(self):
        module = load_provider("subs4series")
        created = []

        class CookieJar(dict):
            def set(self, name, value, domain=None):
                self[name] = value

        class Scraper:
            def __init__(self):
                self.cookies = CookieJar()
                self.calls = []
                self.closed = False

            def get(self, url, headers=None, timeout=None):
                self.calls.append((url, headers, timeout))
                return SimpleNamespace(
                    content=b"page", status_code=200, headers={}, url=url, raise_for_status=lambda: None
                )

            def close(self):
                self.closed = True

        def make_scraper(**options):
            scraper = Scraper()
            created.append((options, scraper))
            return scraper

        with patch.object(module.cloudscraper, "create_scraper", side_effect=make_scraper):
            provider = module.Subs4SeriesProvider()
            provider._http_get("https://www.subs4series.com/first")
            default_ua = provider._user_agent
            created[0][1].cookies.set("existing", "login")
            provider._store_flaresolverr_solution({
                "userAgent": "Helper UA",
                "cookies": [{"name": "cf_clearance", "value": "clear"}],
            })
            provider._http_get("https://www.subs4series.com/second")
        self.assertEqual(created[0][0]["browser"]["custom"], default_ua)
        self.assertEqual(created[1][0]["browser"]["custom"], "Helper UA")
        self.assertTrue(created[0][1].closed)
        self.assertEqual(created[1][1].cookies["existing"], "login")
        self.assertEqual(created[1][1].cookies["cf_clearance"], "clear")
        self.assertEqual(created[1][1].calls[0][1]["User-Agent"], "Helper UA")
        self.assertIn("cf_clearance=clear", created[1][1].calls[0][1]["Cookie"])

    def test_wizdom_retries_challenge_on_rebuilt_session_with_helper_identity(self):
        module = load_provider("wizdom")
        from tests.test_wizdom import FakeResponse, FakeSession

        url = "https://wizdom.xyz/api/releases/tt1375666"
        first = FakeSession([FakeResponse(url, status_code=403, text="Just a moment", headers={"cf-ray": "abc"})])
        second = FakeSession([FakeResponse(url, content=b'{}')])
        created = []

        def make_scraper(**options):
            created.append(options)
            return [first, second][len(created) - 1]

        with patch.object(module.cloudscraper, "create_scraper", side_effect=make_scraper):
            provider = module.WizdomProvider()
            provider._post_flaresolverr = lambda *_args, **_kwargs: {
                "solution": {"userAgent": "Helper UA", "cookies": [{"name": "cf_clearance", "value": "clear"}]}
            }
            self.assertEqual(provider._http_get(url, config={"flaresolverr_url": "http://helper/v1"}), b"{}")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)
        self.assertEqual(created[1]["browser"]["custom"], "Helper UA")
        self.assertEqual(second.calls[0][2]["User-Agent"], "Helper UA")
        self.assertIn("cf_clearance", [cookie.name for cookie in second.cookies])

    def test_napiprojekt_reuses_helper_ua_and_cookies_after_catalog_post(self):
        module = load_provider("napiprojekt")
        created = []

        class CookieJar(dict):
            def set(self, name, value, domain=None, path="/"):
                self[name] = value

        class Scraper:
            def __init__(self, responses):
                self.responses = list(responses)
                self.cookies = CookieJar()
                self.calls = []
                self.closed = False

            def post(self, url, **kwargs):
                return self._send("POST", url, kwargs)

            def get(self, url, **kwargs):
                return self._send("GET", url, kwargs)

            def _send(self, method, url, kwargs):
                self.calls.append((method, url, kwargs))
                return self.responses.pop(0)

            def close(self):
                self.closed = True

        def response(status, body, headers=None):
            return SimpleNamespace(
                status_code=status, content=body, headers=headers or {},
                url="https://www.napiprojekt.pl/ajax/search_catalog.php",
                raise_for_status=lambda: None,
            )

        original = Scraper([
            response(403, b"<title>Just a moment...</title>", {"cf-mitigated": "challenge"}),
            response(200, b"stale session"),
        ])
        original.cookies.set("login", "session")
        replacement = Scraper([response(200, b"next catalog page")])

        def make_scraper(**options):
            created.append(options)
            return [original, replacement][len(created) - 1]

        helper_answer = {
            "status": "ok",
            "solution": {
                "response": "first catalog page",
                "userAgent": "Helper UA",
                "cookies": [{"name": "cf_clearance", "value": "clear", "domain": ".napiprojekt.pl"}],
            },
        }
        with patch.object(module.ua_generator, "generate", return_value=SimpleNamespace(text="Generated UA")), patch.object(
            module.cloudscraper, "create_scraper", side_effect=make_scraper
        ), patch.object(module.urllib.request, "urlopen", return_value=BodyResponse(json.dumps(helper_answer).encode())):
            provider = module.NapiProjektProvider()
            self.assertEqual(provider._http_post(
                module.CATALOG_SEARCH_URL, {"queryString": "Shrek"},
                config={"flaresolverr_url": "http://helper/v1"},
            ), b"first catalog page")
            self.assertEqual(provider._http_get("https://www.napiprojekt.pl/next"), b"next catalog page")

        self.assertEqual(created[0]["browser"]["custom"], "Generated UA")
        self.assertEqual(created[1]["browser"]["custom"], "Helper UA")
        self.assertTrue(original.closed)
        self.assertEqual(replacement.cookies["login"], "session")
        self.assertEqual(replacement.cookies["cf_clearance"], "clear")
        self.assertEqual(replacement.calls[0][2]["headers"]["User-Agent"], "Helper UA")

    def test_special_manifests_pin_generator(self):
        ids = "legendasdivx legendasnet napiprojekt prijevodionline sub_scene subdl subf2m subs4series subsource turkcealtyaziorg wizdom yavkanet addic7ed avistaz cinemaz greeksubtitles karagarga".split()
        for provider_id in ids:
            with self.subTest(provider_id=provider_id):
                manifest = json.loads((ROOT / "providers" / provider_id / "provider.json").read_text())
                requirements = manifest["dependencies"]["requirements"]
                generator = next(item for item in requirements if item["name"] == "ua-generator")
                self.assertEqual(generator["version"], "2.1.6")
                self.assertEqual(generator["hashes"], ["sha256:877285e6adab3d6b0f9b295d954d121fc21a0305be8526038218e8f08be76df6"])
