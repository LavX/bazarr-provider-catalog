import base64
import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsunacs"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("subsunacs_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_DUNE_EN = (FIXTURE_DIR / "subsunacs_search_dune_en.html").read_bytes()
SEARCH_GOT_BG = (FIXTURE_DIR / "subsunacs_search_game_of_thrones_bg.html").read_bytes()
DETAIL_DUNE = (FIXTURE_DIR / "subsunacs_detail_dune.html").read_bytes()
DETAIL_GOT = (FIXTURE_DIR / "subsunacs_detail_game_of_thrones.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SubsUnacsParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_public_rows(self):
        rows = self.mod.parse_search_results(SEARCH_DUNE_EN)

        self.assertEqual(rows[0]["title"], "Dune")
        self.assertEqual(rows[0]["year"], 2021)
        self.assertEqual(rows[0]["download_url"], "https://subsunacs.net/subtitles/Dune-144478/!")
        self.assertEqual(rows[0]["num_cds"], 1)
        self.assertEqual(rows[0]["fps"], 23.976)
        self.assertEqual(rows[0]["rating"], None)
        self.assertEqual(rows[0]["uploader"], "subs")

    def test_parse_detail_entries_filters_site_readme_files(self):
        entries = self.mod.parse_detail_entries(DETAIL_DUNE)

        self.assertEqual([entry["filename"] for entry in entries], [
            "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt",
            "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX_FORCED.srt",
        ])
        self.assertEqual(entries[0]["entry_url"], "https://subsunacs.net/getentry.php?id=144478&ei=0")

    def test_extract_archive_files_reads_zip_and_filters_ignored_txt(self):
        body = _zip_body(
            {
                "Movie.srt": "subtitle",
                "subsunacs.net_144478.txt": "ignored",
            }
        )

        files = self.mod.extract_archive_files(body)

        self.assertEqual(files, [{"filename": "Movie.srt", "content": b"subtitle"}])

    def test_extract_archive_files_ignores_oversized_file_lists(self):
        body = _zip_body({f"readme-{index}.txt": "ignored" for index in range(self.mod.ARCHIVE_FILE_COUNT_LIMIT + 1)})

        self.assertEqual(self.mod.extract_archive_files(body), [])

    def test_collect_extracted_files_repairs_unreadable_archive_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Show.S01E01.en.srt"
            path.write_bytes(b"subtitle")
            path.chmod(0)
            try:
                files = self.mod._collect_extracted_subtitle_files(temp_dir)
            finally:
                path.chmod(0o600)

        self.assertEqual(files, [("Show.S01E01.en.srt", b"subtitle")])


class SubsUnacsProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_posts_expected_payload_and_returns_detail_entries(self):
        provider = self.mod.SubsUnacsProvider()
        calls = []

        def post_stub(url, data, timeout=10, referer=None):
            del timeout, referer
            calls.append((url, data))
            return SEARCH_DUNE_EN

        def get_stub(url, timeout=10, referer=None):
            del timeout, referer
            self.assertEqual(url, "https://subsunacs.net/subtitles/Dune-144478/!")
            return DETAIL_DUNE

        provider._http_post = post_stub
        provider._http_get = get_stub

        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "source": "WEBRip",
                "resolution": "1080p",
                "release_group": "SHITBOX",
                "fps": 23.976,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0][0], "https://subsunacs.net/search.php")
        self.assertEqual(calls[0][1]["m"], "Dune")
        self.assertEqual(calls[0][1]["l"], 1)
        self.assertEqual(calls[0][1]["y"], 2021)
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertEqual(results[0]["filename"], "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["entry_url"], "https://subsunacs.net/getentry.php?id=144478&ei=0")

    def test_search_episode_formats_query_and_filters_detail_entries_by_episode(self):
        provider = self.mod.SubsUnacsProvider()
        calls = []

        provider._http_post = lambda url, data, timeout=10, referer=None: calls.append(data) or SEARCH_GOT_BG
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_GOT

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "source": "HDTV",
                "release_group": "fever",
                "fps": 23.976,
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(calls[0]["m"], "Game of Thrones 01 01")
        self.assertEqual(calls[0]["l"], 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "game.of.thrones.s01e01.hdtv.xvid-fever.srt")
        self.assertIn("episode", results[0]["matches"])

    def test_search_applies_legacy_movie_title_aliases(self):
        provider = self.mod.SubsUnacsProvider()
        calls = []
        provider._http_post = lambda url, data, timeout=10, referer=None: calls.append(data) or b""
        provider._http_get = lambda url, timeout=10, referer=None: b""

        provider.search(
            {"kind": "movie", "title": "Bill & Ted Face the Music", "year": 2020},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0]["m"], "Bill Ted Face the Music")

    def test_download_fetches_direct_entry_and_normalizes_line_endings(self):
        provider = self.mod.SubsUnacsProvider()
        provider._http_get = lambda url, timeout=10, referer=None: b"1\r\nText\r\n"

        content = provider.download(
            {
                "entry_url": "https://subsunacs.net/getentry.php?id=144478&ei=0",
                "filename": "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"1\nText\n")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(content["format"], "srt")


if __name__ == "__main__":
    unittest.main()
