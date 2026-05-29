import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "prijevodionline"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "prijevodionline_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INDEX_HTML = (FIXTURE_DIR / "prijevodionline_index_game_of_thrones.html").read_bytes()
SERIES_HTML = (FIXTURE_DIR / "prijevodionline_series_game_of_thrones.html").read_bytes()
SUBTITLES_HTML = (FIXTURE_DIR / "prijevodionline_subtitles_game_of_thrones_s01e01.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class PrijevodiOnlineParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_series_index_extracts_series_id_and_slug(self):
        rows = self.mod.parse_series_index(INDEX_HTML)

        self.assertEqual(rows[1]["series_id"], "935")
        self.assertEqual(rows[1]["title"], "Game of Thrones")
        self.assertEqual(rows[1]["slug"], "game-of-thrones")
        self.assertEqual(rows[1]["url"], "https://www.prijevodi-online.org/serije/view/935/game-of-thrones")

    def test_parse_series_page_extracts_key_and_episode_id(self):
        parsed = self.mod.parse_series_page(SERIES_HTML)

        self.assertEqual(parsed["key"], "ca7a167e13db896fe2324b2cbf10311f")
        self.assertEqual(parsed["episodes"][(1, 1)]["episode_id"], "33945")
        self.assertEqual(parsed["episodes"][(1, 1)]["title"], "Winter is Coming")
        self.assertEqual(parsed["episodes"][(2, 1)]["episode_id"], "43179")

    def test_parse_subtitle_rows_extracts_languages_releases_and_verified(self):
        rows = self.mod.parse_subtitle_rows(SUBTITLES_HTML)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["subtitle_id"], "18050")
        self.assertEqual(rows[0]["language"], "hrv")
        self.assertTrue(rows[0]["verified"])
        self.assertEqual(rows[0]["releases"], ["HDTV.XviD-FEVER", "720p.HDTV.X264-CTU"])
        self.assertEqual(rows[1]["language"], "srp")
        self.assertEqual(rows[2]["language"], "mne")


class PrijevodiOnlineProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_episode_languages(self):
        provider = self.mod.PrijevodiOnlineProvider()
        calls = []
        responses = {
            "https://www.prijevodi-online.org/serije/index/g": INDEX_HTML,
            "https://www.prijevodi-online.org/serije/view/935/game-of-thrones": SERIES_HTML,
            "https://www.prijevodi-online.org/prijevod/get/33945": SUBTITLES_HTML,
        }

        def get_stub(url, timeout=10, referer=None):
            del timeout, referer
            calls.append(("GET", url))
            return responses[url]

        def post_stub(url, data, timeout=10, referer=None):
            del timeout, referer
            calls.append(("POST", url, data))
            self.assertEqual(data, {"key": "ca7a167e13db896fe2324b2cbf10311f"})
            return responses[url]

        provider._http_get = get_stub
        provider._http_post = post_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "release_group": "CTU",
                "source": "HDTV",
            },
            [{"alpha3": "hrv", "alpha2": "hr"}, {"alpha3": "srp", "alpha2": "sr"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"hrv", "srp"})
        self.assertEqual(results[0]["provider_payload"]["episode_id"], "33945")
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(calls[-1][0], "POST")

    def test_search_hbs_request_maps_each_row_to_hbs(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=10, referer=None: (
            INDEX_HTML if "index" in url else SERIES_HTML
        )
        provider._http_post = lambda url, data, timeout=10, referer=None: SUBTITLES_HTML

        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1},
            [{"alpha3": "hbs", "alpha2": "sh"}],
            {},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"hbs"})
        self.assertEqual(len(results), 3)

    def test_search_ignores_movies_and_missing_episode_fields(self):
        provider = self.mod.PrijevodiOnlineProvider()

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Game of Thrones"}, [{"alpha3": "hrv"}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "episode", "series": "Game of Thrones", "season": 1}, [{"alpha3": "hrv"}], {}),
            [],
        )

    def test_download_selects_subtitle_from_zip_archive(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Game.of.Thrones.S01E02.HDTV.srt": "wrong episode",
                "Game.of.Thrones.S01E01.720p.HDTV.CTU.srt": "right subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        content = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/game-of-thrones-01x01-winter-is-coming-hdtv-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.zip",
                "season": 1,
                "episode": 1,
                "releases": ["720p.HDTV.X264-CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"right subtitle")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(content["format"], "srt")


if __name__ == "__main__":
    unittest.main()
