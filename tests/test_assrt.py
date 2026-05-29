import base64
import hashlib
import importlib.util
import json
import urllib.parse
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "assrt"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "assrt_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUOTA = json.loads((FIXTURE_DIR / "assrt_quota.json").read_text())
INVALID_TOKEN = json.loads((FIXTURE_DIR / "assrt_invalid_token.json").read_text())
SEARCH_RICK = json.loads((FIXTURE_DIR / "assrt_search_rick_morty.json").read_text())
SEARCH_PACK = json.loads((FIXTURE_DIR / "assrt_search_season_pack.json").read_text())
DETAIL_PACK = json.loads((FIXTURE_DIR / "assrt_detail_season_pack.json").read_text())
DETAIL_SINGLE = json.loads((FIXTURE_DIR / "assrt_detail_single_file.json").read_text())


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


class AssrtProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_token_is_required_and_not_returned_in_payload(self):
        provider = self.mod.AssrtProvider()

        with self.assertRaises(ValueError):
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng"}], {})

    def test_api_status_error_is_raised(self):
        with self.assertRaisesRegex(ValueError, "invalid token"):
            self.mod.check_api_status(INVALID_TOKEN)

    def test_search_builds_episode_query_and_returns_requested_languages(self):
        provider = self.mod.AssrtProvider()
        calls = []

        def stub(url, timeout=15, config=None):
            del timeout, config
            calls.append(url)
            return QUOTA if "/user/quota" in url else SEARCH_RICK

        provider._http_get_json = stub
        provider._sleep = lambda seconds: None
        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho", "country": "CN"}, {"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        search_params = _query(calls[1])
        self.assertEqual(search_params["q"], "Rick and Morty S07E10")
        self.assertEqual(search_params["is_file"], "1")
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"zho", "eng"})
        self.assertNotIn("secret-token", json.dumps(results, sort_keys=True))

    def test_search_uses_native_name_when_videoname_is_meaningless(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho", "country": "TW"}],
            {"token": "secret-token"},
        )

        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "71002")
        self.assertIn("720p.WEB", results[0]["release_info"])

    def test_search_marks_season_pack_as_episode_match(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_PACK
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 6, "episode": 2},
            [{"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        self.assertIn("episode", results[0]["matches"])

    def test_download_uses_single_file_detail_url(self):
        provider = self.mod.AssrtProvider()
        calls = []

        def json_stub(url, timeout=15, config=None):
            del timeout, config
            calls.append(url)
            return QUOTA if "/user/quota" in url else DETAIL_SINGLE

        provider._http_get_json = json_stub
        provider._http_get_bytes = lambda url, timeout=15, config=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nLine\r\n"
        provider._sleep = lambda seconds: None
        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "71001",
                "language_code": "chs",
                "filename": "rick.srt",
            },
            {"alpha3": "zho", "country": "CN"},
            {"token": "secret-token"},
        )

        self.assertEqual(_query(calls[1])["id"], "71001")
        decoded = base64.b64decode(result["content_b64"])
        self.assertNotIn(b"\r\n", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_selects_target_episode_file_from_season_pack(self):
        provider = self.mod.AssrtProvider()
        selected_urls = []

        def json_stub(url, timeout=15, config=None):
            del timeout, config
            return QUOTA if "/user/quota" in url else DETAIL_PACK

        def bytes_stub(url, timeout=15, config=None):
            del timeout, config
            selected_urls.append(url)
            return b"1\n00:00:01,000 --> 00:00:02,000\nEpisode two\n"

        provider._http_get_json = json_stub
        provider._http_get_bytes = bytes_stub
        provider._sleep = lambda seconds: None
        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "72001",
                "language_code": "eng",
                "season": 6,
                "episode": 2,
                "filename": "rick-pack.srt",
            },
            {"alpha3": "eng"},
            {"token": "secret-token"},
        )

        self.assertEqual(selected_urls[0], "https://assrt.test/download/rick-s06e02-eng.srt")
        self.assertIn(b"Episode two", base64.b64decode(result["content_b64"]))


if __name__ == "__main__":
    unittest.main()
