import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "animekalesi"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "animekalesi_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERIES_HTML = (FIXTURE_DIR / "animekalesi_series_list.html").read_bytes()
LISTING_HTML = (FIXTURE_DIR / "animekalesi_subtitles_jujutsu_kaisen_2.html").read_bytes()
EPISODE_HTML = (FIXTURE_DIR / "animekalesi_episode_jujutsu_kaisen_2_e01.html").read_bytes()


class AnimeKalesiParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_normalize_series_name_folds_turkish_characters(self):
        self.assertEqual(
            self.mod.normalize_series_name("İĞÜŞÖÇ ı + Jujutsu: Kaisen!!"),
            "igusoc i jujutsu kaisen",
        )

    def test_parse_series_index_extracts_bolumler_rows(self):
        rows = self.mod.parse_series_index(SERIES_HTML)

        self.assertEqual(rows[0]["title"], "Ignored Anime")
        self.assertEqual(
            rows[1],
            {
                "title": "Jujutsu Kaisen 2",
                "url": "https://www.animekalesi.com/bolumler-347-jujutsu-kaisen-2.html",
            },
        )

    def test_select_series_prefers_exact_then_normalized_partial_match(self):
        rows = self.mod.parse_series_index(SERIES_HTML)

        exact = self.mod.select_series(rows, "Jujutsu Kaisen 2")
        partial = self.mod.select_series(rows, "Jujutsu Kaisen 2 TV")

        self.assertEqual(exact["title"], "Jujutsu Kaisen 2")
        self.assertEqual(partial["title"], "Jujutsu Kaisen 2")

    def test_select_series_prefers_most_specific_partial_match(self):
        rows = [
            {"title": "Boku no Hero Academia", "url": "https://example.test/base"},
            {"title": "Boku no Hero Academia 2", "url": "https://example.test/season-2"},
            {"title": "Boku no Hero Academia 3", "url": "https://example.test/season-3"},
        ]

        selected = self.mod.select_series(rows, "Boku no Hero Academia 2nd Season")

        self.assertEqual(selected["title"], "Boku no Hero Academia 2")

    def test_parse_subtitle_listing_extracts_turkish_episode_links(self):
        rows = self.mod.parse_subtitle_listing(LISTING_HTML)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["season"], 1)
        self.assertEqual(rows[0]["episode"], 1)
        self.assertEqual(
            rows[0]["url"],
            "https://www.animekalesi.com/indir_bolum-8993-jujutsu-kaisen-2-1-bolum.html",
        )
        self.assertEqual(rows[2]["season"], 2)
        self.assertEqual(rows[2]["episode"], 12)

    def test_parse_episode_page_extracts_download_link_and_translator(self):
        data = self.mod.parse_episode_page(EPISODE_HTML)

        self.assertEqual(
            data["download_url"],
            "https://www.animekalesi.com/sa-8993-test-token",
        )
        self.assertEqual(data["uploader"], "AnimeKalesi Ekibi")


class AnimeKalesiProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_matching_turkish_episode(self):
        provider = self.mod.AnimeKalesiProvider()
        responses = {
            "https://www.animekalesi.com/tum-anime-serileri.html": SERIES_HTML,
            "https://www.animekalesi.com/altyazib-347-jujutsu-kaisen-2.html": LISTING_HTML,
            "https://www.animekalesi.com/indir_bolum-8993-jujutsu-kaisen-2-1-bolum.html": EPISODE_HTML,
        }
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Jujutsu Kaisen 2", "season": 1, "episode": 1},
            [{"alpha3": "tur", "alpha2": "tr"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, list(responses))
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "animekalesi")
        self.assertEqual(first["language"]["alpha3"], "tur")
        self.assertIn("series", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertEqual(first["release_info"], "Jujutsu Kaisen 2 - S01E01 by AnimeKalesi Ekibi")
        self.assertEqual(first["provider_payload"]["episode"], 1)
        self.assertEqual(
            first["provider_payload"]["download_url"],
            "https://www.animekalesi.com/sa-8993-test-token",
        )

    def test_search_rejects_unsupported_media_language_or_missing_episode(self):
        provider = self.mod.AnimeKalesiProvider()

        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Jujutsu Kaisen 0", "year": 2021},
                [{"alpha3": "tur", "alpha2": "tr"}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "episode", "series": "Jujutsu Kaisen 2", "season": 1, "episode": 1},
                [{"alpha3": "eng", "alpha2": "en"}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "episode", "series": "Jujutsu Kaisen 2", "season": 1},
                [{"alpha3": "tur", "alpha2": "tr"}],
                {},
            ),
            [],
        )


class AnimeKalesiProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_direct_subtitle_with_normalized_line_endings(self):
        provider = self.mod.AnimeKalesiProvider()
        provider._http_get = lambda url, timeout=15, referer=None: (
            b"1\r\n00:00:01,000 --> 00:00:02,000\r\nMerhaba.\r\n"
        )

        result = provider.download(
            {
                "provider": "animekalesi",
                "schema": 1,
                "download_url": "https://www.animekalesi.com/sa-8993-test-token",
                "filename": "Jujutsu.Kaisen.2.S01E01.srt",
            },
            {"alpha3": "tur", "alpha2": "tr"},
            {},
        )

        body = base64.b64decode(result["content_b64"])
        self.assertEqual(body, b"1\n00:00:01,000 --> 00:00:02,000\nMerhaba.\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())

    def test_download_returns_zip_archive_with_selected_member(self):
        archive_body = io.BytesIO()
        with zipfile.ZipFile(archive_body, "w") as archive:
            archive.writestr("Jujutsu.Kaisen.2.S01E02.srt", "wrong episode")
            archive.writestr("Jujutsu.Kaisen.2.S01E01.ass", "[Script Info]\r\nTitle: ok\r\n")
        body = archive_body.getvalue()

        provider = self.mod.AnimeKalesiProvider()
        provider._http_get = lambda url, timeout=15, referer=None: body
        result = provider.download(
            {
                "provider": "animekalesi",
                "schema": 1,
                "download_url": "https://www.animekalesi.com/sa-8993-test-token",
                "filename": "Jujutsu.Kaisen.2.S01E01.zip",
                "season": 1,
                "episode": 1,
                "release_info": "Jujutsu Kaisen 2 - S01E01",
            },
            {"alpha3": "tur", "alpha2": "tr"},
            {},
        )

        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Jujutsu.Kaisen.2.S01E01.ass")

    def test_download_rejects_zip_with_only_other_episode_files(self):
        archive_body = io.BytesIO()
        with zipfile.ZipFile(archive_body, "w") as archive:
            archive.writestr("Jujutsu.Kaisen.2.S01E02.srt", "wrong episode")
            archive.writestr("Jujutsu.Kaisen.2.S01E03.ass", "[Script Info]\r\nTitle: wrong\r\n")

        provider = self.mod.AnimeKalesiProvider()
        provider._http_get = lambda url, timeout=15, referer=None: archive_body.getvalue()

        with self.assertRaisesRegex(ValueError, "matching episode"):
            provider.download(
                {
                    "provider": "animekalesi",
                    "schema": 1,
                    "download_url": "https://www.animekalesi.com/sa-8993-test-token",
                    "filename": "Jujutsu.Kaisen.2.S01E01.zip",
                    "season": 1,
                    "episode": 1,
                    "release_info": "Jujutsu Kaisen 2 - S01E01",
                },
                {"alpha3": "tur", "alpha2": "tr"},
                {},
            )

    def test_download_rejects_html_direct_download_body(self):
        provider = self.mod.AnimeKalesiProvider()
        provider._http_get = lambda url, timeout=15, referer=None: (
            b"<!doctype html><html><body>expired token</body></html>"
        )

        with self.assertRaisesRegex(ValueError, "supported subtitle"):
            provider.download(
                {
                    "provider": "animekalesi",
                    "schema": 1,
                    "download_url": "https://www.animekalesi.com/sa-8993-test-token",
                    "filename": "Jujutsu.Kaisen.2.S01E01.srt",
                },
                {"alpha3": "tur", "alpha2": "tr"},
                {},
            )

    def test_download_detects_ass_direct_download_body(self):
        provider = self.mod.AnimeKalesiProvider()
        provider._http_get = lambda url, timeout=15, referer=None: (
            b"\xef\xbb\xbf[Script Info]\r\nTitle: ok\r\n[Events]\r\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Merhaba\r\n"
        )

        result = provider.download(
            {
                "provider": "animekalesi",
                "schema": 1,
                "download_url": "https://www.animekalesi.com/sa-8993-test-token",
                "filename": "Jujutsu.Kaisen.2.S01E01.srt",
            },
            {"alpha3": "tur", "alpha2": "tr"},
            {},
        )

        body = base64.b64decode(result["content_b64"])
        self.assertTrue(body.startswith(b"\xef\xbb\xbf[Script Info]\n"))
        self.assertEqual(result["format"], "ass")


if __name__ == "__main__":
    unittest.main()
