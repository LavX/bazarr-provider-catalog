import importlib.util
from email.message import Message
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_CASES = {
    "subssabbz": (
        "SubsSabBzProvider",
        [
            ("_http_get", ("https://subs.sab.bz/search",), {}),
            ("_http_post", ("https://subs.sab.bz/search", {"query": "Dune"}), {}),
        ],
    ),
    "subsunacs": (
        "SubsUnacsProvider",
        [
            ("_http_get", ("https://subsunacs.net/search",), {}),
            ("_http_post", ("https://subsunacs.net/search", {"query": "Dune"}), {}),
        ],
    ),
    "subsynchro": (
        "SubsynchroProvider",
        [
            ("_http_request", ("https://www.subsynchro.com/search",), {}),
            ("_http_request", ("https://www.subsynchro.com/search", b"query=Dune"), {}),
        ],
    ),
    "subtitlecat": (
        "SubtitlecatProvider",
        [
            ("_http_get", ("https://www.subtitlecat.com/search/first",), {}),
            ("_http_get", ("https://www.subtitlecat.com/search/second",), {}),
        ],
    ),
    "subtitlestar": (
        "SubtitlestarProvider",
        [
            ("_http_get", ("https://subtitlestar.com/first",), {}),
            ("_http_get", ("https://subtitlestar.com/second",), {}),
        ],
    ),
    "subtitrarinoi": (
        "SubtitrariNoiProvider",
        [
            ("_http_post", ("https://www.subtitrari-noi.ro/search", {"cautare": "Dune"}), {}),
            ("_http_get", ("https://www.subtitrari-noi.ro/download/1",), {}),
        ],
    ),
    "subtitriid": (
        "SubtitriIdProvider",
        [
            ("_http_get", ("https://subtitri.do.am/search/first",), {}),
            ("_http_get", ("https://subtitri.do.am/search/second",), {}),
        ],
    ),
    "subtitulamostv": (
        "SubtitulamosTVProvider",
        [
            ("_http_get", ("https://www.subtitulamos.tv/search/first",), {}),
            (
                "_http_get",
                ("https://www.subtitulamos.tv/search/second",),
                {"user_agent": "user-agent-override"},
            ),
        ],
    ),
    "supersubtitles": (
        "SuperSubtitlesProvider",
        [
            ("_http_get", ("https://feliratok.eu/first",), {}),
            ("_http_get", ("https://feliratok.eu/second",), {}),
        ],
    ),
    "titlovi": (
        "TitloviProvider",
        [
            ("_http_get", ("https://kodi.titlovi.com/api/subtitles/search",), {}),
            (
                "_http_post",
                ("https://kodi.titlovi.com/api/subtitles/gettoken", {"username": "user"}),
                {"headers": {"User-Agent": "user-agent-override"}},
            ),
        ],
    ),
    "titrari": (
        "TitrariProvider",
        [
            ("_http_get", ("https://www.titrari.ro/first",), {}),
            ("_http_get", ("https://www.titrari.ro/second",), {}),
        ],
    ),
    "titulky": (
        "TitulkyProvider",
        [
            ("_http_get", ("https://premium.titulky.com/first",), {}),
            (
                "_http_get",
                ("https://premium.titulky.com/second",),
                {"headers": {"User-Agent": "user-agent-override"}},
            ),
        ],
    ),
    "vladoon": (
        "VladoonProvider",
        [
            ("_http_get", ("https://vladoon.com/subs/search/first",), {}),
            ("_http_get", ("https://vladoon.com/subs/search/second",), {}),
        ],
    ),
    "yifysubtitles": (
        "YifySubtitlesProvider",
        [
            ("_http_get", ("https://yifysubtitles.ch/first",), {}),
            ("_http_get", ("https://yifysubtitles.ch/second",), {}),
        ],
    ),
    "zimuku": (
        "ZimukuProvider",
        [
            ("_http_get_response", ("https://srtku.com/first",), {}),
            ("_http_get_response", ("https://srtku.com/second",), {}),
        ],
    ),
}

COOKIE_PROVIDERS = {"subssabbz", "subsunacs", "titrari", "zimuku"}


class _OfflineResponse:
    def __init__(self, url, set_cookie=None):
        self.url = url
        self.status = 200
        self.headers = Message()
        if set_cookie:
            self.headers.add_header("Set-Cookie", set_cookie)

    def read(self, *_args):
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
        (value for key, value in request.header_items() if key.lower() == name.lower()),
        None,
    )


class BrowserIdentityGroupBTests(unittest.TestCase):
    def test_outgoing_requests_reuse_one_browser_identity_and_keep_session_cookies(self):
        for provider_id, (class_name, calls) in PROVIDER_CASES.items():
            with self.subTest(provider=provider_id):
                module = _load_provider_module(provider_id)
                with patch.object(
                    module.ua_generator,
                    "generate",
                    wraps=module.ua_generator.generate,
                ) as generate:
                    provider = getattr(module, class_name)()
                    received = []
                    cookie_processor = None
                    if provider_id in COOKIE_PROVIDERS:
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

                    if provider_id == "titlovi":
                        transport = patch.object(
                            module,
                            "_urlopen_with_retry",
                            side_effect=lambda request, timeout: (
                                received.append(request)
                                or module.HttpResponse(200, b"ok", {})
                            ),
                        )
                    elif provider_id == "titulky":
                        transport = patch.object(
                            module,
                            "_open_with_retry",
                            side_effect=lambda opener, request, timeout: (
                                received.append(request)
                                or _OfflineResponse(request.full_url)
                            ),
                        )
                    elif hasattr(provider, "_opener"):
                        provider._opener.open = offline_open
                        transport = None
                    else:
                        transport = patch.object(
                            module.urllib.request, "urlopen", offline_open
                        )

                    if transport is not None:
                        transport.start()
                    try:
                        for method_name, args, kwargs in calls:
                            getattr(provider, method_name)(*args, **kwargs)
                    finally:
                        if transport is not None:
                            transport.stop()

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
                    for index, (_, _, kwargs) in enumerate(calls):
                        override = (kwargs.get("headers") or {}).get(
                            "User-Agent", kwargs.get("user_agent")
                        )
                        self.assertEqual(
                            user_agents[index], override or provider._user_agent
                        )
                    self.assertNotIn("BazarrProviderHub", provider._user_agent)
                    self.assertTrue(provider._user_agent.startswith("Mozilla/5.0 "))

                    if provider_id in COOKIE_PROVIDERS:
                        self.assertIn(
                            "session=active", _request_header(received[1], "Cookie")
                        )


if __name__ == "__main__":
    unittest.main()
