import importlib.util
from email.message import Message
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_CASES = {
    "animekalesi": (
        "AnimeKalesiProvider",
        [
            ("_http_get", ("https://www.animekalesi.com/first",), {}),
            ("_http_get", ("https://www.animekalesi.com/second",), {}),
        ],
    ),
    "animetosho": (
        "AnimeToshoProvider",
        [
            ("_http_get", ("https://animetosho.org/first",), {}),
            ("_http_get", ("https://animetosho.org/second",), {}),
        ],
    ),
    "bollynook": (
        "BollyNookProvider",
        [
            ("_http_request", ("https://bollynook.com/first",), {}),
            ("_http_request", ("https://bollynook.com/second", b"x=1"), {}),
        ],
    ),
    "fansubs": (
        "FansubsProvider",
        [
            ("_http_get", ("https://fansubs.ru/first",), {}),
            ("_http_post", ("https://fansubs.ru/second", {"search": "test"}), {}),
        ],
    ),
    "greeksubs": (
        "GreekSubsProvider",
        [
            ("_http_request", ("https://greeksubs.net/first",), {}),
            ("_http_request", ("https://greeksubs.net/second", b"x=1"), {}),
        ],
    ),
    "isubtitles": (
        "ISubtitlesProvider",
        [
            ("_http_get", ("https://isubtitles.org/first",), {}),
            ("_http_get", ("https://isubtitles.org/second",), {}),
        ],
    ),
    "kitsunekko": (
        "KitsunekkoProvider",
        [
            ("_http_get", ("https://kitsunekko.net/first",), {}),
            ("_http_get", ("https://kitsunekko.net/second",), {}),
        ],
    ),
    "moviesubtitles": (
        "MoviesubtitlesProvider",
        [
            ("_http_request", ("https://www.moviesubtitles.org/first",), {}),
            ("_http_request", ("https://www.moviesubtitles.org/second", b"x=1"), {}),
        ],
    ),
    "my_subs": (
        "MySubsProvider",
        [
            ("_http_get", ("https://my-subs.co/first",), {}),
            ("_http_get", ("https://my-subs.co/second",), {}),
        ],
    ),
    "nekur": (
        "NekurProvider",
        [
            ("_http_get", ("https://nekur.net/first",), {}),
            ("_http_post_search", ("test",), {}),
        ],
    ),
    "pipocas": (
        "PipocasProvider",
        [
            ("_http_get", ("https://pipocas.tv/first",), {}),
            ("_http_post", ("https://pipocas.tv/second", {"search": "test"}), {}),
        ],
    ),
    "regielive": (
        "RegieLiveProvider",
        [
            ("_http_get", ("https://regielive.ro/first",), {}),
            ("_http_get", ("https://regielive.ro/second",), {}),
        ],
    ),
    "soustitreseu": (
        "SoustitreseuProvider",
        [
            ("_http_get", ("https://www.sous-titres.eu/first",), {}),
            ("_http_get", ("https://www.sous-titres.eu/second",), {}),
        ],
    ),
    "subclub": (
        "SubclubProvider",
        [
            ("_http_get", ("https://subclub.eu/first",), {}),
            ("_http_get", ("https://subclub.eu/second",), {}),
        ],
    ),
    "subhd": (
        "SubHDProvider",
        [
            ("_http_get", ("https://subhd.tv/first",), {}),
            ("_http_post_json", ("https://subhd.tv/second", {"search": "test"}), {}),
        ],
    ),
    "subs4free": (
        "Subs4FreeProvider",
        [
            ("_http_get", ("https://subs4free.info/first",), {}),
            ("_http_post", ("https://subs4free.info/second", {"search": "test"}), {}),
        ],
    ),
}

COOKIE_PROVIDERS = {
    "animekalesi",
    "greeksubs",
    "pipocas",
    "regielive",
    "soustitreseu",
    "subclub",
    "subhd",
    "subs4free",
}


class _OfflineResponse:
    def __init__(self, url, set_cookie=None):
        self.url = url
        self.headers = Message()
        if set_cookie:
            self.headers.add_header("Set-Cookie", set_cookie)

    def read(self):
        return b"ok"

    def getcode(self):
        return 200

    def info(self):
        return self.headers

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _load_provider_module(provider_id):
    path = ROOT / "providers" / provider_id / "provider.py"
    spec = importlib.util.spec_from_file_location(f"{provider_id}_identity_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request_header(request, name):
    return next(
        value for key, value in request.header_items() if key.lower() == name.lower()
    )


class BrowserIdentityGroupATests(unittest.TestCase):
    def test_requests_reuse_one_browser_identity_and_cookie_session_per_instance(self):
        for provider_id, (class_name, calls) in PROVIDER_CASES.items():
            with self.subTest(provider=provider_id):
                module = _load_provider_module(provider_id)
                generator = patch.object(
                    module.ua_generator,
                    "generate",
                    wraps=module.ua_generator.generate,
                )
                generate = generator.start()
                self.addCleanup(generator.stop)
                provider = getattr(module, class_name)()
                received = []
                cookie_processor = None
                if provider_id in COOKIE_PROVIDERS and provider_id != "pipocas":
                    cookie_processor = next(
                        handler
                        for handler in provider._opener.handlers
                        if isinstance(
                            handler, module.urllib.request.HTTPCookieProcessor
                        )
                    )

                def offline_open(request, timeout=None):
                    if cookie_processor is not None:
                        request = cookie_processor.http_request(request)
                    received.append(request)
                    response = _OfflineResponse(
                        request.full_url,
                        "session=active; Path=/" if len(received) == 1 else None,
                    )
                    if cookie_processor is not None:
                        cookie_processor.http_response(request, response)
                    return response

                if hasattr(provider, "_opener"):
                    provider._opener.open = offline_open
                else:
                    urlopen_patcher = patch.object(
                        module.urllib.request, "urlopen", offline_open
                    )
                    urlopen_patcher.start()
                    self.addCleanup(urlopen_patcher.stop)

                for method_name, args, kwargs in calls:
                    getattr(provider, method_name)(*args, **kwargs)

                self.assertEqual(len(received), len(calls))
                generate.assert_called_once()
                generation = generate.call_args.kwargs
                self.assertEqual(generation["device"], "desktop")
                self.assertEqual(generation["platform"], ("linux", "windows"))
                self.assertEqual(generation["browser"], ("firefox", "chrome"))
                self.assertTrue(generation["options"].latest_versions)
                user_agents = [
                    _request_header(request, "User-Agent") for request in received
                ]
                self.assertEqual(len(set(user_agents)), 1)
                self.assertEqual(user_agents[0], provider._user_agent)
                self.assertNotIn("BazarrProviderHub", user_agents[0])
                self.assertTrue(user_agents[0].startswith("Mozilla/5.0 "))

                if provider_id in COOKIE_PROVIDERS:
                    self.assertIn(
                        "session=active", _request_header(received[1], "Cookie")
                    )


if __name__ == "__main__":
    unittest.main()
