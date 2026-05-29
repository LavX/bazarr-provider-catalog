import base64
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subtis"
FIXTURES = ROOT / "tests" / "fixtures"


def load_provider_module():
    spec = importlib.util.spec_from_file_location("subtis_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHttpClient:
    def __init__(self, json_by_url=None, bytes_by_url=None):
        self.json_by_url = dict(json_by_url or {})
        self.bytes_by_url = dict(bytes_by_url or {})
        self.json_gets = []
        self.bytes_gets = []

    def get_json(self, url):
        self.json_gets.append(url)
        value = self.json_by_url.get(url)
        if isinstance(value, Exception):
            raise value
        return value

    def get_bytes(self, url):
        self.bytes_gets.append(url)
        value = self.bytes_by_url.get(url)
        if isinstance(value, Exception):
            raise value
        return value


class SubtisProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_provider_module()

    def load_response(self):
        return json.loads((FIXTURES / "subtis_search_response.json").read_text(encoding="utf-8"))

    def load_video(self):
        return json.loads((FIXTURES / "subtis_video_man_of_steel.json").read_text(encoding="utf-8"))

    def make_provider(self, json_by_url=None, bytes_by_url=None):
        return self.module.SubtisProvider(http_client=FakeHttpClient(json_by_url, bytes_by_url))

    def test_search_uses_hash_first_and_returns_worker_candidate(self):
        video = self.load_video()
        hash_url = "https://api.subt.is/v1/subtitle/find/file/hash/5b8f8f4e41ccb21e"
        provider = self.make_provider({hash_url: self.load_response()})

        results = provider.search(video, [{"alpha3": "spa", "alpha2": "es"}], {})

        self.assertEqual(provider.http_client.json_gets, [hash_url])
        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["provider"], "subtis")
        self.assertEqual(candidate["language"]["alpha3"], "spa")
        self.assertEqual(candidate["matches"], ["hash"])
        self.assertEqual(candidate["release_info"], "Man of Steel 2013 720p BluRay x264-FELONY")
        self.assertTrue(candidate["hash_verifiable"])
        self.assertEqual(candidate["provider_payload"]["method"], "hash")
        self.assertEqual(
            candidate["provider_payload"]["download_url"],
            self.load_response()["subtitle"]["subtitle_link"],
        )

    def test_search_falls_back_to_alternative_and_marks_fuzzy(self):
        video = self.load_video()
        video["hashes"] = {}
        video["size"] = 0
        name_url = "https://api.subt.is/v1/subtitle/find/file/name/man.of.steel.2013.720p.bluray.x264-felony.mkv"
        alternative_url = "https://api.subt.is/v1/subtitle/file/alternative/man.of.steel.2013.720p.bluray.x264-felony.mkv"
        provider = self.make_provider(
            {
                name_url: None,
                alternative_url: self.load_response(),
            }
        )

        results = provider.search(video, [{"alpha3": "spa"}], {})

        self.assertEqual(provider.http_client.json_gets, [name_url, alternative_url])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["method"], "alternative")
        self.assertEqual(results[0]["release_info"], "Man of Steel 2013 720p BluRay x264-FELONY [fuzzy match]")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])

    def test_search_uses_size_between_hash_and_filename(self):
        video = self.load_video()
        video["hashes"] = {}
        bytes_url = "https://api.subt.is/v1/subtitle/find/file/bytes/7033732714"
        provider = self.make_provider({bytes_url: self.load_response()})

        results = provider.search(video, [{"alpha3": "spa"}], {})

        self.assertEqual(provider.http_client.json_gets, [bytes_url])
        self.assertEqual(results[0]["provider_payload"]["method"], "bytes")

    def test_search_returns_empty_for_episode_or_non_spanish_language(self):
        provider = self.make_provider()
        video = self.load_video()

        self.assertEqual(provider.search({**video, "kind": "episode"}, [{"alpha3": "spa"}], {}), [])
        self.assertEqual(provider.search(video, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.http_client.json_gets, [])

    def test_search_skips_forced_or_hearing_impaired_spanish_requests(self):
        provider = self.make_provider()
        video = self.load_video()

        self.assertEqual(
            provider.search(video, [{"alpha3": "spa", "alpha2": "es", "hi": True}], {}),
            [],
        )
        self.assertEqual(
            provider.search(video, [{"alpha3": "spa", "alpha2": "es", "forced": True}], {}),
            [],
        )
        self.assertEqual(provider.http_client.json_gets, [])

    def test_search_continues_cascade_after_api_error(self):
        video = self.load_video()
        hash_url = "https://api.subt.is/v1/subtitle/find/file/hash/5b8f8f4e41ccb21e"
        bytes_url = "https://api.subt.is/v1/subtitle/find/file/bytes/7033732714"
        provider = self.make_provider(
            {
                hash_url: ValueError("invalid json"),
                bytes_url: self.load_response(),
            }
        )

        results = provider.search(video, [{"alpha3": "spa"}], {})

        self.assertEqual(provider.http_client.json_gets, [hash_url, bytes_url])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["method"], "bytes")

    def test_search_ignores_payload_without_subtitle_link(self):
        video = self.load_video()
        hash_url = "https://api.subt.is/v1/subtitle/find/file/hash/5b8f8f4e41ccb21e"
        bytes_url = "https://api.subt.is/v1/subtitle/find/file/bytes/7033732714"
        name_url = "https://api.subt.is/v1/subtitle/find/file/name/man.of.steel.2013.720p.bluray.x264-felony.mkv"
        alternative_url = "https://api.subt.is/v1/subtitle/file/alternative/man.of.steel.2013.720p.bluray.x264-felony.mkv"
        provider = self.make_provider(
            {
                hash_url: {"subtitle": {}, "title": {"title_name": "Man of Steel"}},
                bytes_url: None,
                name_url: None,
                alternative_url: None,
            }
        )

        self.assertEqual(provider.search(video, [{"alpha3": "spa"}], {}), [])

    def test_download_returns_direct_subtitle_bytes(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHola\r\n"
        url = "https://api.subt.is/v1/subtitle/download/man-of-steel-es.srt"
        provider = self.make_provider(bytes_by_url={url: body})

        result = provider.download({"provider": "subtis", "schema": 1, "download_url": url, "format": "srt"}, {}, {})
        decoded = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)

        self.assertEqual(provider.http_client.bytes_gets, [url])
        self.assertEqual(decoded, body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])

    def test_download_requires_subtis_payload(self):
        provider = self.make_provider()

        with self.assertRaisesRegex(ValueError, "download_url"):
            provider.download({"provider": "subtis", "schema": 1}, {}, {})
        with self.assertRaisesRegex(ValueError, "provider"):
            provider.download({"provider": "other", "schema": 1, "download_url": "https://example.test/a.srt"}, {}, {})


if __name__ == "__main__":
    unittest.main()
