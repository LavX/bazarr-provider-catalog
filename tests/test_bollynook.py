import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "bollynook"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "bollynook_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "bollynook_search_pathaan.html").read_bytes()
DETAIL_HTML = (FIXTURE_DIR / "bollynook_detail_pathaan.html").read_bytes()


def _zip_body(name, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
    return stream.getvalue()


class BollyNookParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_result(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)
        self.assertEqual(rows[0]["movie_id"], "22997")
        self.assertEqual(rows[0]["title"], "Pathaan - 2023")
        self.assertEqual(rows[0]["language"], "eng")

    def test_parse_detail_extracts_download_link(self):
        detail = self.mod.parse_detail(DETAIL_HTML, "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/")
        self.assertEqual(detail["title"], "Pathaan")
        self.assertEqual(detail["year"], 2023)
        self.assertEqual(detail["language"], "eng")
        self.assertEqual(detail["uploader"], "Vukov Julijana")
        self.assertEqual(
            detail["download_url"],
            "https://www.bollynook.com/uploaded_pictures/content/titlovi/22997-pathaan-CD1-2023-eng.zip",
        )


class BollyNookProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_detail_and_returns_movie_result(self):
        provider = self.mod.BollyNookProvider()
        responses = {
            "https://www.bollynook.com/en/search/": SEARCH_HTML,
            "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/": DETAIL_HTML,
        }
        calls = []

        def stub(url, data=None, timeout=15, referer=None):
            del timeout
            calls.append((url, data, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Pathaan", "year": 2023},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[0][0], "https://www.bollynook.com/en/search/")
        self.assertEqual(calls[0][1], b"type=2&title=Pathaan&language=eng&submit=Search")
        self.assertEqual(results[0]["provider"], "bollynook")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["movie_id"], "22997")

    def test_download_extracts_zip_subtitle(self):
        provider = self.mod.BollyNookProvider()
        body = _zip_body("Pathaan.2023.eng.srt", b"1\n00:00:01,000 --> 00:00:02,000\nMovie line\n")
        provider._http_request = lambda url, data=None, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "bollynook",
                "schema": 1,
                "movie_id": "22997",
                "url": "https://www.bollynook.com/uploaded_pictures/content/titlovi/22997-pathaan-CD1-2023-eng.zip",
                "page_url": "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/",
                "filename": "22997-pathaan-CD1-2023-eng.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Movie line", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
