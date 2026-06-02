import base64
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "napisy24"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "napisy24_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _video(name):
    return json.loads((FIXTURE_DIR / name).read_text())


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class Napisy24ProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_posts_hash_lookup_and_embeds_zip_payload(self):
        provider = self.mod.Napisy24Provider()
        archive_body = _zip_body(
            {
                "Dune.Part.One.2021.pl.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nLinia\r\n",
            }
        )
        metadata = "OK-2|napisId:987|imdb:1160419".encode("ascii")
        response_body = metadata + b"||" + archive_body
        calls = []

        def post(url, data, headers=None, timeout=10):
            calls.append((url, dict(data), dict(headers or {}), timeout))
            return self.mod.HttpResponse(200, response_body, {})

        provider._http_post = post
        results = provider.search(
            _video("napisy24_video_dune_2021.json"),
            [{"alpha3": "pol", "alpha2": "pl"}],
            {},
        )

        self.assertEqual(calls[0][0], "http://napisy24.pl/run/CheckSubAgent.php")
        self.assertEqual(
            calls[0][1],
            {
                "postAction": "CheckSub",
                "ua": "subliminal",
                "ap": "lanimilbus",
                "fs": "1234567890",
                "fh": "0123456789abcdef",
                "fn": "Dune.Part.One.2021.1080p.WEB-DL-NTb.mkv",
                "n24pref": "1",
            },
        )
        self.assertEqual(results[0]["provider"], "napisy24")
        self.assertEqual(results[0]["language"], {"alpha3": "pol", "alpha2": "pl", "hi": False, "forced": False})
        self.assertEqual(results[0]["provider_payload"]["napis_id"], "987")
        self.assertIn("hash", results[0]["matches"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertNotIn("subliminal", json.dumps(results[0]))
        self.assertNotIn("lanimilbus", json.dumps(results[0]))

    def test_search_uses_configured_credentials_only_when_both_are_present(self):
        provider = self.mod.Napisy24Provider()
        archive_body = _zip_body({"movie.srt": b"line\n"})
        seen = []

        def post(url, data, headers=None, timeout=10):
            del url, headers, timeout
            seen.append((data["ua"], data["ap"]))
            return self.mod.HttpResponse(200, b"OK-2|napisId:1|imdb:1160419||" + archive_body, {})

        provider._http_post = post
        video = _video("napisy24_video_dune_2021.json")

        provider.search(video, [{"alpha3": "pol"}], {"username": "user"})
        provider.search(video, [{"alpha3": "pol"}], {"username": "user", "password": "pass"})

        self.assertEqual(seen, [("subliminal", "lanimilbus"), ("user", "pass")])

    def test_search_returns_empty_for_statuses_without_database_subtitle(self):
        provider = self.mod.Napisy24Provider()
        responses = [
            b"OK-0",
            b"OK-1|imdb:1160419",
            b"OK-3|napisId:1|imdb:1160419||" + _zip_body({"movie.srt": b"line\n"}),
        ]

        def post(url, data, headers=None, timeout=10):
            del url, data, headers, timeout
            return self.mod.HttpResponse(200, responses.pop(0), {})

        provider._http_post = post
        video = _video("napisy24_video_dune_2021.json")

        self.assertEqual(provider.search(video, [{"alpha3": "pol"}], {}), [])
        self.assertEqual(provider.search(video, [{"alpha3": "pol"}], {}), [])
        self.assertEqual(provider.search(video, [{"alpha3": "pol"}], {}), [])

    def test_search_reports_login_error(self):
        provider = self.mod.Napisy24Provider()
        provider._http_post = lambda url, data, headers=None, timeout=10: self.mod.HttpResponse(
            200,
            b"login error",
            {},
        )

        with self.assertRaisesRegex(PermissionError, "Login failed"):
            provider.search(
                _video("napisy24_video_dune_2021.json"),
                [{"alpha3": "pol"}],
                {"username": "bad", "password": "bad"},
            )

    def test_search_skips_when_hash_or_language_is_missing(self):
        provider = self.mod.Napisy24Provider()
        provider._http_post = lambda url, data, headers=None, timeout=10: (_ for _ in ()).throw(
            AssertionError("network should not be called")
        )
        video = _video("napisy24_video_dune_2021.json")

        self.assertEqual(provider.search(video, [{"alpha3": "eng"}], {}), [])
        video["hashes"] = {}
        self.assertEqual(provider.search(video, [{"alpha3": "pol"}], {}), [])

    def test_search_computes_hash_from_existing_video_path(self):
        provider = self.mod.Napisy24Provider()
        body = bytes((index % 251 for index in range(150000)))
        calls = []

        def post(url, data, headers=None, timeout=10):
            del url, headers, timeout
            calls.append(dict(data))
            return self.mod.HttpResponse(200, b"OK-0", {})

        provider._http_post = post
        with tempfile.NamedTemporaryFile(suffix=".mkv") as handle:
            handle.write(body)
            handle.flush()
            results = provider.search(
                {
                    "kind": "movie",
                    "title": "Example",
                    "year": 2024,
                    "name": handle.name,
                    "hashes": {},
                },
                [{"alpha3": "pol"}],
                {},
            )

        self.assertEqual(results, [])
        self.assertEqual(calls[0]["fh"], "661cd19248ffe906")
        self.assertEqual(calls[0]["fs"], "150000")

    def test_download_extracts_embedded_zip_content(self):
        archive_body = _zip_body(
            {
                "readme.txt": b"ignore\n",
                "Dune.Part.One.2021.pl.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nLinia\r\n",
            }
        )
        payload = {
            "archive_b64": base64.b64encode(archive_body).decode("ascii"),
            "filename": "napisy24.987.zip",
        }
        result = self.mod.Napisy24Provider().download(payload, {"alpha3": "pol"}, {})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nLinia\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
