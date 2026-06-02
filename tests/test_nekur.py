import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "nekur"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "nekur_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "nekur_search_dune.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class NekurParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_table_rows(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["title"], "Dune: Part One")
        self.assertEqual(first["year"], 2021)
        self.assertEqual(first["imdb_id"], "tt1160419")
        self.assertEqual(first["fps"], "23.976")
        self.assertEqual(first["notes"], "DVD, BD")
        self.assertEqual(
            first["download_url"],
            "https://subtitri.nekur.net/filmu-subtitri/download/51fcaecad656f7e9894c70d0bab7a3dc",
        )


class NekurProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_latvian_movie_match_by_imdb(self):
        provider = self.mod.NekurProvider()
        queries = []

        def stub(title, timeout=10):
            del timeout
            queries.append(title)
            return SEARCH_HTML

        provider._http_post_search = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "source": "Blu-ray",
            },
            [{"alpha3": "lav", "alpha2": "lv"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(queries, ["Dune"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "lav")
        self.assertEqual(results[0]["provider_payload"]["imdb_id"], "tt1160419")
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])

    def test_search_uses_alternative_titles_and_deduplicates(self):
        provider = self.mod.NekurProvider()
        queries = []

        def stub(title, timeout=10):
            del timeout
            queries.append(title)
            return SEARCH_HTML

        provider._http_post_search = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Missing Title",
                "alternative_titles": ["Dune"],
                "year": 2021,
                "imdb_id": "tt1160419",
            },
            [{"alpha3": "lav", "alpha2": "lv"}],
            {},
        )

        self.assertEqual(queries, ["Missing Title", "Dune"])
        self.assertEqual(len(results), 1)

    def test_search_matches_alternative_title_without_imdb_id(self):
        provider = self.mod.NekurProvider()
        queries = []

        def stub(title, timeout=10):
            del timeout
            queries.append(title)
            return SEARCH_HTML

        provider._http_post_search = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Missing Title",
                "alternative_titles": ["Dune: Part One"],
                "year": 2021,
            },
            [{"alpha3": "lav", "alpha2": "lv"}],
            {},
        )

        self.assertEqual(queries, ["Missing Title", "Dune: Part One"])
        self.assertEqual(len(results), 1)
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])

    def test_search_ignores_episodes_and_unrequested_languages(self):
        provider = self.mod.NekurProvider()

        self.assertEqual(
            provider.search({"kind": "episode", "series": "Dune", "season": 1, "episode": 1}, [{"alpha3": "lav"}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng"}], {}),
            [],
        )

    def test_download_selects_matching_subtitle_from_zip(self):
        provider = self.mod.NekurProvider()
        body = _zip_body(
            {
                "Dune.Part.Two.2024.lv.srt": "wrong movie",
                "Dune.Part.One.2021.BD.lv.srt": "right subtitle",
                "Dune.Part.One.2021.BD.forced.lv.srt": "forced subtitle",
            }
        )
        provider._http_get = lambda url, timeout=10: body

        content = provider.download(
            {
                "download_url": "https://subtitri.nekur.net/filmu-subtitri/download/51fcaecad656f7e9894c70d0bab7a3dc",
                "filename": "nekur.dune-part-one.2021.zip",
                "title": "Dune: Part One",
                "year": 2021,
                "notes": "DVD, BD",
            },
            {"alpha3": "lav", "alpha2": "lv"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"right subtitle")
        self.assertEqual(content["format"], "srt")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertFalse(content["empty"])

    def test_download_merges_multipart_zip_subtitles(self):
        body = _zip_body(
            {
                "Dune.Part.One.2021.CD1.lv.srt": b"1\n00:00:01,000 --> 00:00:02,000\nPart one\n",
                "Dune.Part.One.2021.CD2.lv.srt": b"1\n00:10:01,000 --> 00:10:02,000\nPart two\n",
            }
        )

        content = self.mod.extract_download(
            body,
            "nekur.dune-part-one.2021.zip",
            {"title": "Dune: Part One", "year": 2021},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertIn(b"Part one", data)
        self.assertIn(b"Part two", data)
        self.assertEqual(content["format"], "srt")

    def test_download_rejects_html_error_response(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"<html><title>Error</title><body>Download unavailable</body></html>",
                "nekur.dune-part-one.2021.zip",
                {},
            )

    def test_extract_download_accepts_direct_subtitle_file(self):
        content = self.mod.extract_download(b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n", "direct.srt", {})

        data = base64.b64decode(content["content_b64"])
        self.assertTrue(data.startswith(b"1\n00:00:01"))
        self.assertEqual(content["content_type"], "application/x-subrip")


if __name__ == "__main__":
    unittest.main()
