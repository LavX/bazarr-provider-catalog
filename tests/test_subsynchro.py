import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsynchro"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subsynchro_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_files(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


FILM_HTML = (FIXTURE_DIR / "subsynchro_film_the_plastic_detox.html").read_bytes()
RELEASE_HTML = (FIXTURE_DIR / "subsynchro_release_the_plastic_detox.html").read_bytes()
SEARCH_HTML = (FIXTURE_DIR / "subsynchro_search_dune.html").read_bytes()
VIDEO = json.loads((FIXTURE_DIR / "subsynchro_video_the_plastic_detox.json").read_text(encoding="utf-8"))
LEGACY_JSON = json.dumps(
    {
        "status": 200,
        "data": [
            {
                "titre": "The Plastic Detox",
                "titre_original": "The Plastic Detox",
                "release": "The.Plastic.Detox.2026.1080p.WEB.H264-EDITH",
                "filename": "The.Plastic.Detox.2026.1080p.WEB.h264-EDITH.srt",
                "telechargement": "https://www.subsynchro.com/download.php?id=986",
                "fichier": "zip",
            }
        ],
    }
).encode("utf-8")


class SubsynchroParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_film_releases_extracts_release_page(self):
        rows = self.mod.parse_film_releases(FILM_HTML)

        self.assertEqual(rows[0]["release_id"], "163562")
        self.assertEqual(rows[0]["movie_title"], "The Plastic Detox")
        self.assertEqual(rows[0]["movie_year"], 2025)
        self.assertEqual(rows[0]["release_info"], "The.Plastic.Detox.2026.1080p.WEB.H264-EDITH")
        self.assertEqual(rows[0]["release_group"], "EDITH")
        self.assertEqual(rows[0]["file_count"], 1)
        self.assertEqual(
            rows[0]["release_url"],
            "https://www.subsynchro.com/2025/the-plastic-detox/the-plastic-detox-2026-1080p-web-h264-edith.html",
        )

    def test_parse_release_files_extracts_download_link(self):
        rows = self.mod.parse_release_files(
            RELEASE_HTML,
            {
                "release_id": "163562",
                "release_info": "The.Plastic.Detox.2026.1080p.WEB.H264-EDITH",
                "release_url": "https://www.subsynchro.com/2025/the-plastic-detox/the-plastic-detox-2026-1080p-web-h264-edith.html",
            },
        )

        self.assertEqual(rows[0]["file_id"], "986")
        self.assertEqual(rows[0]["filename"], "The.Plastic.Detox.2026.1080p.WEB.h264-EDITH.srt")
        self.assertEqual(rows[0]["format"], "srt")
        self.assertEqual(rows[0]["downloads"], 195)
        self.assertEqual(
            rows[0]["download_url"],
            "https://www.subsynchro.com/telecharger-le-fichier-986-the-plastic-detox-2026-1080p-web-h264-edith-srt.html",
        )

    def test_parse_search_results_extracts_movie_pages(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)

        self.assertEqual(rows[-1]["title"], "Dune")
        self.assertEqual(rows[-1]["year"], 2021)
        self.assertEqual(rows[-1]["url"], "https://www.subsynchro.com/2021/20176-dune.html")

    def test_parse_legacy_ajax_results_preserves_old_endpoint_shape(self):
        rows = self.mod.parse_legacy_ajax_results(LEGACY_JSON, VIDEO)

        self.assertEqual(rows[0]["filename"], "The.Plastic.Detox.2026.1080p.WEB.h264-EDITH.srt")
        self.assertEqual(rows[0]["download_url"], "https://www.subsynchro.com/download.php?id=986")
        self.assertIn("title", rows[0]["matches"])
        self.assertIn("year", rows[0]["matches"])


class SubsynchroProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_posts_current_site_and_fetches_release_page(self):
        provider = self.mod.SubsynchroProvider()
        calls = []
        responses = {
            self.mod.SEARCH_URL: FILM_HTML,
            "https://www.subsynchro.com/2025/the-plastic-detox/the-plastic-detox-2026-1080p-web-h264-edith.html": RELEASE_HTML,
        }

        def stub(url, data=None, timeout=15, referer=None):
            del timeout
            calls.append((url, data, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_request = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "fra", "alpha2": "fr"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[0], (self.mod.SEARCH_URL, b"q=The+Plastic+Detox", None))
        self.assertEqual(calls[1][2], self.mod.SEARCH_URL)
        self.assertEqual(results[0]["provider"], "subsynchro")
        self.assertEqual(results[0]["language"]["alpha3"], "fra")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["file_id"], "986")
        self.assertEqual(results[0]["provider_payload"]["schema"], 1)

    def test_search_uses_legacy_ajax_fallback_when_current_site_has_no_match(self):
        provider = self.mod.SubsynchroProvider()
        calls = []

        def stub(url, data=None, timeout=15, referer=None):
            del timeout, referer
            calls.append((url, data))
            if url == self.mod.SEARCH_URL:
                return b"<html><body>No matching film page here</body></html>"
            if url.startswith(self.mod.LEGACY_AJAX_URL):
                return LEGACY_JSON
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_request = stub
        results = provider.search(
            VIDEO,
            [{"alpha3": "fra", "alpha2": "fr"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[0], (self.mod.SEARCH_URL, b"q=The+Plastic+Detox"))
        self.assertTrue(calls[1][0].startswith(self.mod.LEGACY_AJAX_URL))
        self.assertEqual(results[0]["provider_payload"]["url"], "https://www.subsynchro.com/download.php?id=986")

    def test_search_rejects_unsupported_media_or_language(self):
        provider = self.mod.SubsynchroProvider()
        provider._http_request = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used"))

        self.assertEqual(
            provider.search({"kind": "episode", "series": "Any", "season": 1, "episode": 1}, [{"alpha3": "fra"}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "movie", "title": "Any"}, [{"alpha3": "eng"}], {}),
            [],
        )

    def test_download_extracts_first_visible_subtitle_file_from_zip(self):
        provider = self.mod.SubsynchroProvider()
        body = _zip_files(
            {
                ".hidden.srt": b"hidden",
                "readme.nfo": b"nfo",
                "The.Plastic.Detox.2026.1080p.WEB.h264-EDITH.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nBonjour\r\n",
                "poster.jpg": b"image",
            }
        )
        provider._http_request = lambda url, data=None, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "subsynchro",
                "schema": 1,
                "url": "https://www.subsynchro.com/telecharger-le-fichier-986-example.html",
                "page_url": "https://www.subsynchro.com/2025/the-plastic-detox/release.html",
                "filename": "The.Plastic.Detox.2026.1080p.WEB.h264-EDITH.srt",
            },
            {"alpha3": "fra", "alpha2": "fr"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nBonjour\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_rejects_html_body_when_not_zip(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"<html><title>blocked</title></html>", {"filename": "subtitle.zip"})


if __name__ == "__main__":
    unittest.main()
