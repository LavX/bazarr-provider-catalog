import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "titrari"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("titrari_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOME_HTML = b"""
    <a href=index.php?page=lista>Lista alfabetica</a>
    <a href=index.php?page=cautamainaltaparte>Cautare avansata</a>
"""
MOVIE_DUNE_HTML = (FIXTURE_DIR / "titrari_search_dune_2021.html").read_bytes()
EPISODE_CHERNOBYL_HTML = (FIXTURE_DIR / "titrari_search_chernobyl_s01.html").read_bytes()
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nTitrari subtitle.\n"


def _zip_with(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class TitrariParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_advanced_search_page_param(self):
        self.assertEqual(self.mod.parse_advanced_search_page_param(HOME_HTML), "cautamainaltaparte")
        self.assertEqual(self.mod.parse_advanced_search_page_param(b"<html></html>"), "numaicautamcaneiesepenas")

    def test_build_search_url_uses_imdb_language_and_media_type(self):
        movie_url = self.mod.build_search_url(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            {"alpha3": "ron", "alpha2": "ro"},
            "cautamainaltaparte",
        )
        episode_url = self.mod.build_search_url(
            {"kind": "episode", "series": "Chernobyl", "series_imdb_id": "tt7366338"},
            {"alpha3": "ron", "alpha2": "ro"},
            "cautamainaltaparte",
        )

        self.assertEqual(
            movie_url,
            "https://www.titrari.ro/index.php?page=cautamainaltaparte&z7=&z2=&z5=1160419&z3=-1&z4=-1&z8=1&z9=All&z11=1&z6=0",
        )
        self.assertEqual(
            episode_url,
            "https://www.titrari.ro/index.php?page=cautamainaltaparte&z7=&z2=&z5=7366338&z3=-1&z4=-1&z8=1&z9=All&z11=2&z6=0",
        )

    def test_parse_movie_results_extracts_download_metadata(self):
        rows = self.mod.parse_search_results(MOVIE_DUNE_HTML)
        by_id = {row["subtitle_id"]: row for row in rows}

        self.assertIn("124379", by_id)
        first = by_id["124379"]
        self.assertEqual(first["title"], "Dune")
        self.assertEqual(first["year"], 2021)
        self.assertEqual(first["language"], "ron")
        self.assertEqual(first["imdb_id"], "tt1160419")
        self.assertEqual(first["download_url"], "https://www.titrari.ro/get.php?id=124379")
        self.assertEqual(first["page_url"], "https://www.titrari.ro/index.php?page=cautamainaltaparte&z10=124379")
        self.assertEqual(first["uploader"], "alex87 @ CzTeam")
        self.assertEqual(first["translator"], "jarvis")
        self.assertEqual(first["downloads"], 6464)
        self.assertIn("WEB-DL", first["comments"])

    def test_parse_episode_results_marks_single_episode_and_pack(self):
        rows = self.mod.parse_search_results(EPISODE_CHERNOBYL_HTML)
        by_id = {row["subtitle_id"]: row for row in rows}

        single = by_id["141103"]
        self.assertEqual(single["title"], "Chernobyl")
        self.assertEqual(single["year"], 2019)
        self.assertEqual(single["season"], 1)
        self.assertEqual(single["episode"], 1)
        self.assertFalse(single["is_pack"])
        self.assertEqual(single["downloads"], 7)

        pack = by_id["116927"]
        self.assertEqual(pack["title"], "Chernobyl")
        self.assertEqual(pack["season"], 1)
        self.assertIsNone(pack["episode"])
        self.assertTrue(pack["is_pack"])
        self.assertEqual(pack["downloads"], 11489)

    def test_derive_matches_movie(self):
        rows = self.mod.parse_search_results(MOVIE_DUNE_HTML)
        row = next(item for item in rows if item["subtitle_id"] == "124410")
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "FLUX",
                "resolution": "1080p",
                "source": "Web",
            },
            row,
        )

        self.assertEqual(
            set(matches),
            {"title", "year", "imdb_id", "release_group", "resolution", "source"},
        )

    def test_derive_matches_episode(self):
        rows = self.mod.parse_search_results(EPISODE_CHERNOBYL_HTML)
        row = next(item for item in rows if item["subtitle_id"] == "141103")
        matches = self.mod.derive_matches(
            {
                "kind": "episode",
                "series": "Chernobyl",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt7366338",
                "resolution": "1080p",
                "source": "BluRay",
            },
            row,
        )

        self.assertEqual(
            set(matches),
            {"series", "season", "episode", "series_imdb_id", "resolution", "source"},
        )

    def test_derive_matches_rejects_pack_ranges_outside_requested_episode(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            {
                "title": "Chernobyl",
                "season": 1,
                "episode": None,
                "is_pack": True,
                "comments": "Episoadele 3-5",
            },
        )

        self.assertEqual(set(matches), {"series", "season"})


class TitrariProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_dynamic_page_and_preserves_requested_variant(self):
        provider = self.mod.TitrariProvider()
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url == "https://www.titrari.ro/":
                return HOME_HTML
            if url == (
                "https://www.titrari.ro/index.php?page=cautamainaltaparte&z7=&z2=&z5=1160419"
                "&z3=-1&z4=-1&z8=1&z9=All&z11=1&z6=0"
            ):
                return MOVIE_DUNE_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "FLUX",
                "resolution": "1080p",
                "source": "Web",
            },
            [{"alpha3": "ron", "alpha2": "ro", "hi": True, "forced": True}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(
            [item[0] for item in called],
            [
                "https://www.titrari.ro/",
                (
                    "https://www.titrari.ro/index.php?page=cautamainaltaparte&z7=&z2=&z5=1160419"
                    "&z3=-1&z4=-1&z8=1&z9=All&z11=1&z6=0"
                ),
            ],
        )
        self.assertEqual(results[0]["provider"], "titrari")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "124410")
        self.assertEqual(results[0]["language"], {"alpha3": "ron", "alpha2": "ro", "hi": True, "forced": True})
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://www.titrari.ro/get.php?id=124410")
        self.assertIn("release_group", results[0]["matches"])

    def test_search_skips_unsupported_language_without_network(self):
        provider = self.mod.TitrariProvider()

        def stub(url, timeout=15, referer=None):
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "spa", "alpha2": "es"}],
            {},
        )

        self.assertEqual(results, [])


RAR_BODY = b"Rar!\x1a\x07\x00" + b"chernobyl-archive-payload-bytes"


class TitrariDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_direct_subtitle_payload(self):
        provider = self.mod.TitrariProvider()
        provider._http_get = lambda url, timeout=15, referer=None: SRT_BODY

        result = provider.download(
            {"download_url": "https://www.titrari.ro/get.php?id=124379", "filename": "dune.srt"},
            {"alpha3": "ron"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertFalse(result["empty"])
        self.assertEqual(data, SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)

    def test_download_returns_zip_archive_bytes_for_host_extraction(self):
        body = _zip_with(
            {
                "Chernobyl.S01E02.srt": b"other episode",
                "Chernobyl.S01E01.1080p.BluRay.srt": SRT_BODY,
            }
        )
        result = self.mod.extract_download(body, {"season": 1, "episode": 1, "filename": "chernobyl.zip"})

        self.assertNotIn("content_b64", result)
        self.assertEqual(base64.b64decode(result["archive_b64"].encode("ascii"), validate=True), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_bytes_for_host_extraction(self):
        result = self.mod.extract_download(RAR_BODY, {"season": 1, "episode": 1, "filename": "chernobyl.rar"})

        self.assertNotIn("content_b64", result)
        self.assertEqual(base64.b64decode(result["archive_b64"].encode("ascii"), validate=True), RAR_BODY)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(RAR_BODY).hexdigest())
        self.assertEqual(result["episode"], 1)

    def test_download_archive_episode_is_none_for_movies(self):
        body = _zip_with({"Dune.2021.1080p.srt": SRT_BODY})
        result = self.mod.extract_download(body, {"episode": None, "filename": "dune.zip"})

        self.assertEqual(base64.b64decode(result["archive_b64"].encode("ascii"), validate=True), body)
        self.assertIsNone(result["episode"])

    def test_download_passes_episode_from_payload_through_http(self):
        body = _zip_with({"Chernobyl.S01E01.srt": SRT_BODY})
        provider = self.mod.TitrariProvider()
        provider._http_get = lambda url, timeout=15, referer=None: body

        result = provider.download(
            {
                "download_url": "https://www.titrari.ro/get.php?id=141103",
                "filename": "chernobyl.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "ron"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"].encode("ascii"), validate=True), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)

    def test_download_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"", {"filename": "chernobyl.zip"})

    def test_download_rejects_html_error_page(self):
        body = b"<!DOCTYPE html><html><body>Eroare</body></html>"
        with self.assertRaisesRegex(ValueError, "HTML"):
            self.mod.extract_download(body, {"filename": "chernobyl.zip"})


class TitrariSearchPayloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_payload_carries_season_and_episode(self):
        rows = self.mod.parse_search_results(EPISODE_CHERNOBYL_HTML)
        row = next(item for item in rows if item["subtitle_id"] == "141103")
        result = self.mod._result_from_row(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            row,
            {"alpha3": "ron", "alpha2": "ro", "hi": False, "forced": False},
        )

        payload = result["provider_payload"]
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 1)


if __name__ == "__main__":
    unittest.main()
