import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "shooter"
FIXTURES = ROOT / "tests" / "fixtures"
SHOOTER_HASH = (
    "11111111111111111111111111111111;"
    "22222222222222222222222222222222;"
    "33333333333333333333333333333333;"
    "44444444444444444444444444444444"
)


def load_provider_module():
    spec = importlib.util.spec_from_file_location("shooter_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHttpClient:
    def __init__(self, search_body=b"\xff", download_body=b""):
        self.search_body = search_body
        self.download_body = download_body
        self.posts = []
        self.gets = []

    def post(self, url, params):
        self.posts.append((url, dict(params)))
        return self.search_body

    def get(self, url):
        self.gets.append(url)
        return self.download_body


class ShooterProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_provider_module()

    def load_search_fixture(self):
        return (FIXTURES / "shooter_search_results.json").read_bytes()

    def make_provider(self, search_body=None, download_body=None):
        return self.module.ShooterProvider(
            http_client=FakeHttpClient(
                search_body=self.load_search_fixture() if search_body is None else search_body,
                download_body=b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
                if download_body is None
                else download_body,
            )
        )

    def test_search_posts_hash_path_language_and_returns_worker_candidates(self):
        provider = self.make_provider()
        video = {
            "kind": "movie",
            "name": "/media/Dune.2021.1080p.WEBRip.x264.mkv",
            "title": "Dune",
            "year": 2021,
            "hashes": {"shooter": SHOOTER_HASH},
        }

        results = provider.search(
            video=video,
            languages=[{"alpha3": "eng", "hi": False, "forced": False}],
            config={},
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(provider.http_client.posts[0][0], self.module.SHOOTER_API_URL)
        self.assertEqual(
            provider.http_client.posts[0][1],
            {
                "filehash": SHOOTER_HASH,
                "pathinfo": os.path.realpath(video["name"]),
                "format": "json",
                "lang": "eng",
            },
        )
        first = results[0]
        self.assertEqual(first["provider"], "shooter")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertEqual(first["release_info"], SHOOTER_HASH)
        self.assertEqual(first["matches"], ["hash"])
        self.assertTrue(first["hash_verifiable"])
        self.assertEqual(first["provider_payload"]["download_url"], json.loads(self.load_search_fixture())[0]["Files"][0]["Link"])
        self.assertEqual(first["provider_payload"]["filehash"], SHOOTER_HASH)

    def test_search_maps_chinese_language_to_shooter_code(self):
        provider = self.make_provider()

        results = provider.search(
            video={"kind": "episode", "name": "Show.S01E02.mkv", "hashes": {"shooter": SHOOTER_HASH}},
            languages=[{"alpha3": "zho"}],
            config={},
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(provider.http_client.posts[0][1]["lang"], "chn")
        self.assertEqual(results[0]["language"]["alpha3"], "zho")

    def test_search_returns_empty_without_supported_hash_language_or_media(self):
        provider = self.make_provider()

        self.assertEqual(provider.search({"kind": "movie", "hashes": {}}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(
            provider.search(
                {"kind": "movie", "hashes": {"shooter": SHOOTER_HASH}},
                [{"alpha3": "fra"}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "series", "hashes": {"shooter": SHOOTER_HASH}},
                [{"alpha3": "eng"}],
                {},
            ),
            [],
        )
        self.assertEqual(provider.http_client.posts, [])

    def test_search_computes_shooter_hash_from_existing_video_path(self):
        provider = self.make_provider(search_body=b"\xff")
        body = bytes((index % 197 for index in range(20000)))
        expected_hash = (
            "035864be44712dd0e000d54053a40b0e;"
            "8ac5ea5bfe13c6756988f875f010aade;"
            "df554e195fe156c079c6055a40117f0d;"
            "9dd9e190a937da95bc15ae883ed0d681"
        )

        with tempfile.NamedTemporaryFile(suffix=".mkv") as handle:
            handle.write(body)
            handle.flush()
            results = provider.search(
                video={"kind": "movie", "name": handle.name, "title": "Example", "year": 2024, "hashes": {}},
                languages=[{"alpha3": "eng"}],
                config={},
            )

        self.assertEqual(results, [])
        self.assertEqual(provider.http_client.posts[0][1]["filehash"], expected_hash)

    def test_search_computes_shooter_hash_from_path_field(self):
        provider = self.make_provider(search_body=b"\xff")
        body = bytes((index % 197 for index in range(20000)))

        with tempfile.NamedTemporaryFile(suffix=".mkv") as handle:
            handle.write(body)
            handle.flush()
            provider.search(
                video={"kind": "movie", "path": handle.name, "title": "Example", "year": 2024, "hashes": {}},
                languages=[{"alpha3": "eng"}],
                config={},
            )

        self.assertTrue(provider.http_client.posts)

    def test_search_skips_forced_or_hearing_impaired_requests(self):
        provider = self.make_provider()
        video = {"kind": "movie", "name": "Dune.mkv", "hashes": {"shooter": SHOOTER_HASH}}

        self.assertEqual(provider.search(video, [{"alpha3": "eng", "hi": True}], {}), [])
        self.assertEqual(provider.search(video, [{"alpha3": "zho", "forced": True}], {}), [])
        self.assertEqual(provider.http_client.posts, [])

    def test_search_treats_ff_byte_as_empty_response(self):
        provider = self.make_provider(search_body=b"\xff")

        results = provider.search(
            {"kind": "movie", "name": "Dune.mkv", "hashes": {"shooter": SHOOTER_HASH}},
            [{"alpha3": "eng"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_rejects_malformed_api_payload(self):
        provider = self.make_provider(search_body=b'{"Files": []}')

        with self.assertRaisesRegex(ValueError, "Shooter response"):
            provider.search(
                {"kind": "movie", "name": "Dune.mkv", "hashes": {"shooter": SHOOTER_HASH}},
                [{"alpha3": "eng"}],
                {},
            )

    def test_download_returns_base64_sha256_and_normalized_line_endings(self):
        provider = self.make_provider(download_body=b"1\r00:00:01,000 --> 00:00:02,000\rHello\r")
        payload = {
            "provider": "shooter",
            "schema": 1,
            "download_url": "https://www.shooter.cn/download/11111111111111111111111111111111.srt",
            "filehash": SHOOTER_HASH,
            "language": "eng",
        }

        result = provider.download(payload, {"alpha3": "eng"}, {})
        body = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)

        self.assertEqual(provider.http_client.gets, [payload["download_url"]])
        self.assertEqual(body, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])

    def test_download_returns_content_type_for_ass_payload(self):
        provider = self.make_provider(download_body=b"[Script Info]\r\nTitle: Example\r\n")
        payload = {
            "provider": "shooter",
            "schema": 1,
            "download_url": "https://www.shooter.cn/download/subtitle.ass",
            "filehash": SHOOTER_HASH,
            "language": "eng",
            "format": "ass",
        }

        result = provider.download(payload, {"alpha3": "eng"}, {})

        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_type"], "text/x-ssa")

    def test_download_requires_shooter_payload(self):
        provider = self.make_provider()

        with self.assertRaisesRegex(ValueError, "download_url"):
            provider.download({"provider": "shooter", "schema": 1}, {"alpha3": "eng"}, {})
        with self.assertRaisesRegex(ValueError, "provider"):
            provider.download({"provider": "other", "schema": 1, "download_url": "https://example.test/a.srt"}, {}, {})


if __name__ == "__main__":
    unittest.main()
