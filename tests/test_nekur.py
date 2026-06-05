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
        # The host needs episode (and season) to pick the archive member; movies carry None.
        self.assertIsNone(results[0]["provider_payload"]["episode"])
        self.assertIsNone(results[0]["provider_payload"]["season"])

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

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.NekurProvider()
        body = _zip_body(
            {
                "Dune.Part.Two.2024.lv.srt": "wrong movie",
                "Dune.Part.One.2021.BD.lv.srt": "right subtitle",
            }
        )
        provider._http_get = lambda url, timeout=10: (body, {})

        content = provider.download(
            {
                "download_url": "https://subtitri.nekur.net/filmu-subtitri/download/51fcaecad656f7e9894c70d0bab7a3dc",
                "filename": "nekur.dune-part-one.2021.zip",
                "title": "Dune: Part One",
                "year": 2021,
                "episode": None,
            },
            {"alpha3": "lav", "alpha2": "lv"},
            {},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertIsNone(content["episode"])
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", content)
        self.assertNotIn("member", content)
        self.assertNotIn("encoding", content)

    def test_download_rar_archive_returns_raw_archive_for_host(self):
        provider = self.mod.NekurProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=10: (body, {})

        content = provider.download(
            {
                "download_url": "https://subtitri.nekur.net/filmu-subtitri/download/abc",
                "filename": "nekur.dune-part-one.2021.zip",
                "title": "Dune: Part One",
                "year": 2021,
                "episode": None,
            },
            {"alpha3": "lav", "alpha2": "lv"},
            {},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertIsNone(content["episode"])
        self.assertNotIn("content_b64", content)
        self.assertNotIn("encoding", content)

    def test_extract_download_carries_episode_through_for_host(self):
        # Movies leave episode None, but download() must forward whatever the
        # payload holds so the host can pick the right archive member.
        body = _zip_body({"Show.S01E07.lv.srt": "episode subtitle"})

        content = self.mod.extract_download(
            body,
            "nekur.show.zip",
            {"episode": 7},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertEqual(content["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(content["episode"], 7)

    def test_download_archive_episode_is_none_for_movie(self):
        body = _zip_body({"Dune.Part.One.2021.lv.srt": "movie subtitle"})

        content = self.mod.extract_download(
            body,
            "nekur.dune-part-one.2021.zip",
            {"title": "Dune: Part One", "year": 2021, "episode": None},
        )

        self.assertEqual(base64.b64decode(content["archive_b64"]), body)
        self.assertIsNone(content["episode"])

    def test_download_rejects_empty_body(self):
        provider = self.mod.NekurProvider()
        provider._http_get = lambda url, timeout=10: (b"", {})

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "download_url": "https://subtitri.nekur.net/filmu-subtitri/download/abc",
                    "filename": "nekur.dune-part-one.2021.zip",
                },
                {"alpha3": "lav", "alpha2": "lv"},
                {},
            )

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
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", content)

    def test_download_keeps_direct_subtitle_despite_zip_filename(self):
        # Regression: the synthetic ".zip" filename must not reject a direct
        # subtitle. The real format comes from the Content-Disposition header.
        provider = self.mod.NekurProvider()
        subtitle = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nSveiki\r\n"
        provider._http_get = lambda url, timeout=10: (
            subtitle,
            {"Content-Disposition": 'attachment; filename="Dune.Part.One.2021.lv.srt"'},
        )

        content = provider.download(
            {
                "download_url": "https://subtitri.nekur.net/filmu-subtitri/download/abc",
                "filename": "nekur.dune-part-one.2021.lv.zip",
                "title": "Dune: Part One",
                "year": 2021,
            },
            {"alpha3": "lav", "alpha2": "lv"},
            {},
        )

        data = base64.b64decode(content["content_b64"])
        self.assertEqual(data, subtitle)
        self.assertEqual(content["format"], "srt")
        self.assertFalse(content["empty"])
        self.assertNotIn("archive_b64", content)

    def test_download_sniffs_direct_subtitle_without_disposition(self):
        # Even without a usable filename, a body that is plainly SubRip must be
        # accepted rather than rejected because of the ".zip" extension.
        provider = self.mod.NekurProvider()
        subtitle = b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n"
        provider._http_get = lambda url, timeout=10: (subtitle, {})

        content = provider.download(
            {
                "download_url": "https://subtitri.nekur.net/filmu-subtitri/download/abc",
                "filename": "nekur.dune-part-one.2021.lv.zip",
            },
            {"alpha3": "lav", "alpha2": "lv"},
            {},
        )

        data = base64.b64decode(content["content_b64"])
        self.assertEqual(data, subtitle)
        self.assertEqual(content["format"], "srt")


if __name__ == "__main__":
    unittest.main()
