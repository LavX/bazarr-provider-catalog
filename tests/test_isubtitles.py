import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "isubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "isubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "isubtitles_search_chernobyl.html").read_bytes()
ENGLISH_HTML = (FIXTURE_DIR / "isubtitles_chernobyl_english.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class ISubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_title_page(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)
        self.assertEqual(rows[1]["title"], "Chernobyl - (2019)")
        self.assertEqual(rows[1]["slug"], "chernobyl")
        self.assertEqual(rows[1]["url"], "https://isubtitles.org/chernobyl-subtitles")

    def test_parse_subtitle_rows_extracts_download_rows(self):
        rows = self.mod.parse_subtitle_rows(ENGLISH_HTML)
        self.assertEqual(rows[0]["subtitle_id"], "1415636")
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["file_count"], 1)
        self.assertEqual(rows[0]["size"], "15.7KB")
        self.assertIn("s01e01", rows[0]["release_info"].lower())


class ISubtitlesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_language_page_and_returns_requested_episode(self):
        provider = self.mod.ISubtitlesProvider()
        responses = {
            "https://isubtitles.org/search?kwd=Chernobyl+S01E01": SEARCH_HTML,
            "https://isubtitles.org/chernobyl/english": ENGLISH_HTML,
        }
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item[0] for item in called], list(responses))
        self.assertEqual(results[0]["provider"], "isubtitles")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertIn("episode", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1415636")

    def test_download_extracts_matching_subtitle_from_zip(self):
        provider = self.mod.ISubtitlesProvider()
        body = _zip_body(
            {
                "Chernobyl.S01E01.WEBRip.x264-ION10.srt": b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n",
                "Chernobyl.S01E02.WEBRip.x264-ION10.srt": b"1\nEpisode two\n",
            }
        )
        provider._http_get = lambda url, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "isubtitles",
                "schema": 1,
                "subtitle_id": "1415636",
                "url": "https://isubtitles.org/download/chernobyl/english/1415636",
                "page_url": "https://isubtitles.org/chernobyl/english/1415636",
                "filename": "chernobyl.s01e01.720p.webrip.x264-tbs.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Episode one", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
