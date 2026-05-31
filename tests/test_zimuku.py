import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "zimuku"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("zimuku_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHALLENGE_HTML = (FIXTURE_DIR / "zimuku_challenge.html").read_bytes()
SEARCH_HTML = (FIXTURE_DIR / "zimuku_search_game_of_thrones.html").read_bytes()
EPISODE_HTML = (FIXTURE_DIR / "zimuku_episode_game_of_thrones.html").read_bytes()
DETAIL_HTML = (FIXTURE_DIR / "zimuku_detail_page.html").read_bytes()
DOWNLOAD_HTML = (FIXTURE_DIR / "zimuku_download_page.html").read_bytes()


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(".hidden.srt", "ignore")
        archive.writestr("Game.of.Thrones.S01E01.HDTV.XviD-FEVER.cht.ass", "traditional")
        archive.writestr("Game.of.Thrones.S01E01.HDTV.XviD-FEVER.chs&eng.srt", "simplified bilingual")
    return stream.getvalue()


class ZimukuParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_yunsuo_challenge_extracts_verify_path_and_image(self):
        challenge = self.mod.parse_yunsuo_challenge(CHALLENGE_HTML)

        self.assertEqual(challenge["verify_prefix"], "/search?q=Dune 2021&security_verify_img=")
        self.assertEqual(challenge["image_b64"], "Qk1GQUtFQk1Q")
        self.assertEqual(self.mod.string_to_hex("1234"), "31323334")

    def test_parse_search_results_filters_requested_episode_season(self):
        rows = self.mod.parse_search_results(SEARCH_HTML, {"kind": "episode", "series": "Game of Thrones", "season": 1, "year": 2011})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "权力的游戏 Game of Thrones 第一季 (2011)")
        self.assertEqual(rows[0]["year"], 2011)
        self.assertEqual(rows[0]["url"], "https://srtku.com/detail/game-of-thrones-season-1.html")

    def test_parse_episode_page_expands_language_rows(self):
        rows = self.mod.parse_episode_page(EPISODE_HTML, 2011)

        self.assertEqual([row["language"] for row in rows], ["zho", "zho-TW", "eng"])
        self.assertEqual(rows[0]["release_info"], "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.CHS.CHT")
        self.assertEqual(rows[0]["year"], 2011)
        self.assertEqual(rows[2]["detail_url"], "https://srtku.com/subtitle/game-of-thrones-s01e01-eng.html")

    def test_extract_download_prefers_requested_archive_language(self):
        simplified = self.mod.extract_download(_zip_body(), {"language": "zho", "filename": "archive.zip"})
        traditional = self.mod.extract_download(_zip_body(), {"language": "zho-TW", "filename": "archive.zip"})

        self.assertIn("simplified bilingual", self.mod._decode_payload_text(simplified))
        self.assertIn("traditional", self.mod._decode_payload_text(traditional))


class ZimukuProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_solves_yunsuo_challenge_and_returns_requested_language(self):
        provider = self.mod.ZimukuProvider()
        calls = []
        responses = {
            "https://srtku.com/search?q=Game+of+Thrones.S01": [
                self.mod.HttpResponse(404, CHALLENGE_HTML, {}, "https://srtku.com/search?q=Game+of+Thrones.S01"),
                self.mod.HttpResponse(200, SEARCH_HTML, {}, "https://srtku.com/search?q=Game+of+Thrones.S01"),
            ],
            "https://srtku.com/search?q=Dune 2021&security_verify_img=31323334": [
                self.mod.HttpResponse(302, b"", {"Location": "/search?q=Game+of+Thrones.S01"}, "https://srtku.com/search?q=Dune 2021&security_verify_img=31323334"),
            ],
            "https://srtku.com/detail/game-of-thrones-season-1.html": [
                self.mod.HttpResponse(200, EPISODE_HTML, {}, "https://srtku.com/detail/game-of-thrones-season-1.html"),
            ],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            calls.append(url)
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        provider._solve_yunsuo_image = lambda challenge, config: "1234"
        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1, "year": 2011, "release_group": "FEVER"},
            [{"alpha3": "zho", "alpha2": "zh"}],
            {"request_delay_ms": 0},
        )

        self.assertIn("https://srtku.com/search?q=Dune 2021&security_verify_img=31323334", calls)
        self.assertEqual({item["provider_payload"]["language"] for item in results}, {"zho", "zho-TW"})
        self.assertTrue(all(item["provider"] == "zimuku" for item in results))

    def test_download_follows_detail_and_download_pages(self):
        provider = self.mod.ZimukuProvider()
        calls = []
        responses = {
            "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html": self.mod.HttpResponse(200, DETAIL_HTML, {}, "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html"),
            "https://srtku.com/download/dune-2021.html": self.mod.HttpResponse(200, DOWNLOAD_HTML, {}, "https://srtku.com/download/dune-2021.html"),
            "https://srtku.com/download/file/dune-2021.zip": self.mod.HttpResponse(200, _zip_body(), {"Content-Disposition": "attachment; filename=dune.zip"}, "https://srtku.com/download/file/dune-2021.zip"),
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, allow_redirects
            calls.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get_response = response_stub
        result = provider.download(
            {
                "detail_url": "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html",
                "language": "zho-TW",
                "filename": "Game.of.Thrones.S01E01.zip",
            },
            {"alpha3": "zho", "alpha2": "zh", "country": "TW"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[1][1], "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html")
        self.assertEqual(calls[2][1], "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html")
        self.assertIn("traditional", self.mod._decode_payload_text(result))


if __name__ == "__main__":
    unittest.main()
