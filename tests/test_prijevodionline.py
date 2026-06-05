import base64
import hashlib
import importlib.util
import io
import json
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
        self.assertEqual(rows[2]["language"], "cnr")
        self.assertFalse(rows[2]["verified"])

    def test_index_url_normalizes_non_ascii_title_letters(self):
        self.assertEqual(
            self.mod._index_url("Élite"),
            "https://www.prijevodi-online.org/serije/index/e",
        )
        self.assertEqual(
            self.mod._index_url("Çukur"),
            "https://www.prijevodi-online.org/serije/index/c",
        )

    def test_parse_series_page_keeps_episode_zero_specials(self):
        body = (
            '<script>epizode.key = "ca7a167e13db896fe2324b2cbf10311f";</script>'
            '<h3 id="sezona-1">Sezona 1</h3>'
            '<div id="epizoda-90000">'
            '<ul class="epizoda actual">'
            '<li class="broj">0.</li>'
            '<li class="naziv"><a class="open" rel="/prijevod/get/90000" '
            'title="Download Special">Special</a></li>'
            '<li class="status">prevedeno</li>'
            "</ul></div>"
        )

        parsed = self.mod.parse_series_page(body)

        self.assertIn((1, 0), parsed["episodes"])
        self.assertEqual(parsed["episodes"][(1, 0)]["episode_id"], "90000")
        self.assertEqual(parsed["episodes"][(1, 0)]["title"], "Special")

    def test_find_series_matches_titles_that_drop_apostrophes(self):
        provider = self.mod.PrijevodiOnlineProvider()
        index_html = (
            '<table><tr id="serija-77">'
            '<td><a href="/serije/view/77/da-vincis-demons" '
            'title="Da Vincis Demons">Da Vincis Demons</a></td>'
            "</tr></table>"
        ).encode("utf-8")
        provider._http_get = lambda url, timeout=10, referer=None: index_html

        series = provider._find_series("Da Vinci's Demons")

        self.assertIsNotNone(series)
        self.assertEqual(series["series_id"], "77")
        self.assertEqual(series["slug"], "da-vincis-demons")

    def test_manifest_advertises_accepted_montenegrin_code(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())

        self.assertIn("cnr", manifest["languages"])
        self.assertNotIn("mne", manifest["languages"])
        # Every advertised code must be accepted by _requested_languages so the
        # marketplace never filters this provider out for a code it rejects.
        for code in manifest["languages"]:
            self.assertEqual(
                self.mod._requested_languages([{"alpha3": code}]),
                {code},
                msg=f"manifest advertises {code} but the provider rejects it",
            )


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

    def test_search_supports_standard_montenegrin_code(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=10, referer=None: (
            INDEX_HTML if "index" in url else SERIES_HTML
        )
        provider._http_post = lambda url, data, timeout=10, referer=None: SUBTITLES_HTML

        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1},
            [{"alpha3": "cnr", "alpha2": "me"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "cnr")
        self.assertEqual(results[0]["language"]["alpha2"], "me")

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

    def test_download_returns_zip_archive_bytes_for_host_extraction(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body(
            {
                "Game.of.Thrones.S01E02.HDTV.srt": "wrong episode",
                "Game.of.Thrones.S01E01.720p.HDTV.CTU.srt": "right subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/game-of-thrones-01x01-winter-is-coming-hdtv-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.zip",
                "subtitle_id": "18050",
                "season": 1,
                "episode": 1,
                "releases": ["720p.HDTV.X264-CTU"],
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        # Archive mode: the worker hands the raw bytes back, the host extracts.
        self.assertNotIn("content_b64", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_bytes_for_host_extraction(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = b"Rar!\x1a\x07\x00" + b"binary rar payload"
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got-s01e01-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.rar",
                "subtitle_id": "18050",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("encoding", result)

    def test_download_carries_none_episode_when_payload_has_no_episode(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = _zip_body({"some.subtitle.srt": "data"})
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/movie-hr",
                "filename": "prijevodionline.movie.hr.zip",
                "subtitle_id": "18050",
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertIsNone(result["episode"])

    def test_download_returns_content_for_direct_subtitle_body(self):
        provider = self.mod.PrijevodiOnlineProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        provider._http_get = lambda url, timeout=30, referer=None: body

        result = provider.download(
            {
                "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got-s01e01-hr",
                "filename": "prijevodionline.game-of-thrones.s01e01.hr.srt",
                "subtitle_id": "18050",
                "episode": 1,
            },
            {"alpha3": "hrv", "alpha2": "hr"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"   \n"

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got",
                    "subtitle_id": "18050",
                    "episode": 1,
                },
                {"alpha3": "hrv", "alpha2": "hr"},
                {},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.PrijevodiOnlineProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!DOCTYPE html><html><body>Not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.prijevodi-online.org/preuzmi-prijevod/epizoda/18050/got",
                    "subtitle_id": "18050",
                    "episode": 1,
                },
                {"alpha3": "hrv", "alpha2": "hr"},
                {},
            )


if __name__ == "__main__":
    unittest.main()
