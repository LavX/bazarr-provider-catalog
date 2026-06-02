import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "supersubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "supersubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


MOVIE_DUNE_HTML = (FIXTURE_DIR / "supersubtitles_movie_dune.html").read_bytes()
AUTONAME_LA_BREA_JSON = (FIXTURE_DIR / "supersubtitles_autoname_la_brea.json").read_bytes()
EPISODE_LA_BREA_JSON = (FIXTURE_DIR / "supersubtitles_episode_la_brea_s02e13.json").read_bytes()
DETAIL_DUNE_HTML = (FIXTURE_DIR / "supersubtitles_detail_dune_2021.html").read_bytes()
DETAIL_LA_BREA_HTML = (FIXTURE_DIR / "supersubtitles_detail_la_brea.html").read_bytes()


class SuperSubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_movie_rows_extracts_language_release_forced_and_download(self):
        rows = self.mod.parse_movie_rows(MOVIE_DUNE_HTML)

        dune = [row for row in rows if row["subtitle_id"] == "1735404922"][0]
        self.assertEqual(dune["language"], "hun")
        self.assertEqual(dune["title"], "Dune")
        self.assertEqual(dune["local_title"], "Dűne")
        self.assertEqual(dune["year"], 2021)
        self.assertEqual(dune["releases"], ["WEBRip.1080p-HiDt", "MA.WEB-DL.1080p-HONE", "MA.WEB-DL.2160p-FLUX"])
        self.assertEqual(dune["uploader"], "Hegeman - the_russian")
        self.assertEqual(dune["page_url"], "https://feliratok.eu/index.php?tipus=adatlap&azon=a_1735404922")
        self.assertEqual(
            dune["download_url"],
            "https://feliratok.eu/index.php?action=letolt&fnev=Dune (2021) (1080p MA WEB-DL H265 SDR DDP Atmos 5.1 English - HONE).hun.srt&felirat=1735404922",
        )

        forced = [row for row in rows if row["subtitle_id"] == "1713331726"][0]
        self.assertTrue(forced["forced"])
        self.assertEqual(forced["language"], "hun")

    def test_parse_episode_rows_groups_duplicate_season_pack_releases(self):
        rows = self.mod.parse_episode_rows(
            EPISODE_LA_BREA_JSON,
            {"kind": "episode", "series": "La Brea", "season": 2, "episode": 13},
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["subtitle_id"], "1691315119")
        self.assertEqual(row["language"], "eng")
        self.assertEqual(row["title"], "La Brea")
        self.assertEqual(row["season"], 2)
        self.assertEqual(row["episode"], 13)
        self.assertTrue(row["is_pack"])
        self.assertIn("WEB.1080p-CAKES", row["releases"])
        self.assertIn("WEBRip.1080p-RARBG", row["releases"])

    def test_parse_episode_name_strips_episode_marker_before_release(self):
        title, release = self.mod._parse_episode_name(
            "Marvel's The Falcon and the Winter Soldier - 1x05 (WEB.2160p-KOGi)"
        )

        self.assertEqual(title, "Marvel's The Falcon and the Winter Soldier")
        self.assertEqual(release, "WEB.2160p-KOGi")

    def test_parse_autoname_results_finds_series_id_by_title_and_year(self):
        matches = self.mod.parse_autoname_results(AUTONAME_LA_BREA_JSON)

        self.assertEqual(self.mod.select_series_id(matches, "La Brea", 2021), "8142")
        self.assertIsNone(self.mod.select_series_id(matches, "La Brea", 2022))

    def test_parse_detail_page_extracts_imdb_id(self):
        self.assertEqual(self.mod.parse_detail_imdb_id(DETAIL_DUNE_HTML), "tt1160419")
        self.assertEqual(self.mod.parse_detail_imdb_id(DETAIL_LA_BREA_HTML), "tt11640018")

    def test_derive_movie_matches_uses_title_year_imdb_and_release(self):
        row = [item for item in self.mod.parse_movie_rows(MOVIE_DUNE_HTML) if item["subtitle_id"] == "1735404922"][0]
        row["imdb_id"] = "tt1160419"

        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "HONE",
                "resolution": "1080p",
                "source": "Web",
            },
            row,
        )

        self.assertEqual(matches[:3], ["title", "year", "imdb_id"])
        self.assertIn("release_group", matches)
        self.assertIn("resolution", matches)
        self.assertIn("source", matches)


class SuperSubtitlesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_returns_requested_forced_hungarian_variant(self):
        provider = self.mod.SuperSubtitlesProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "tab=film" in url:
                return MOVIE_DUNE_HTML
            if "a_1713331726" in url:
                return DETAIL_DUNE_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "hun", "alpha2": "hu", "forced": True}],
            {},
        )

        self.assertEqual(calls[0], "https://feliratok.eu/index.php?search=Dune&soriSorszam=&nyelv=&tab=film")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "supersubtitles")
        self.assertEqual(results[0]["language"], {"alpha3": "hun", "alpha2": "hu", "hi": False, "forced": True})
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1713331726")
        self.assertIn("imdb_id", results[0]["matches"])

    def test_search_episode_uses_autoname_xbmc_and_detail_imdb(self):
        provider = self.mod.SuperSubtitlesProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "action=autoname" in url:
                return AUTONAME_LA_BREA_JSON
            if "action=xbmc" in url:
                return EPISODE_LA_BREA_JSON
            if "a_1691315119" in url:
                return DETAIL_LA_BREA_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "La Brea",
                "season": 2,
                "episode": 13,
                "year": 2021,
                "series_imdb_id": "tt11640018",
                "release_group": "CAKES",
                "resolution": "720p",
                "source": "Web",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertIn("series_imdb_id", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["season"], 2)
        self.assertEqual(results[0]["provider_payload"]["episode"], 13)
        self.assertTrue(results[0]["provider_payload"]["is_pack"])
        self.assertEqual(results[0]["provider_payload"]["release_info"], "La Brea (WEB.720p-CAKES)")

    def test_search_episode_uses_matching_alternative_series_query(self):
        provider = self.mod.SuperSubtitlesProvider()
        alias_autoname = json.dumps([{"name": "A hasadék (2021)", "ID": "8142"}]).encode("utf-8")

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            if "action=autoname" in url and "term=La+Brea" in url:
                return b"[]"
            if "action=autoname" in url and "term=A+hasad" in url:
                return alias_autoname
            if "action=xbmc" in url:
                return EPISODE_LA_BREA_JSON
            if "a_1691315119" in url:
                return DETAIL_LA_BREA_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "La Brea",
                "alternative_series": ["A hasadék"],
                "season": 2,
                "episode": 13,
                "year": 2021,
                "series_imdb_id": "tt11640018",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1691315119")

    def test_search_episode_filters_season_fallback_to_requested_episode(self):
        provider = self.mod.SuperSubtitlesProvider()
        wrong_episode_json = json.dumps(
            {
                "0": {
                    "language": "Angol",
                    "nev": "La Brea - 2x12 (WEB.720p-CAKES)",
                    "fnev": "La.Brea.S02E12.720p.WEB.H264-CAKES.srt",
                    "felirat": "1691315118",
                    "evad": "2",
                    "ep": "12",
                    "feltolto": "J1GG4",
                    "evadpakk": "0",
                }
            }
        ).encode("utf-8")

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            if "action=autoname" in url:
                return AUTONAME_LA_BREA_JSON
            if "action=xbmc" in url and "rtol=13" in url:
                return b"{}"
            if "action=xbmc" in url:
                return wrong_episode_json
            if "a_1691315118" in url:
                return DETAIL_LA_BREA_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "La Brea",
                "season": 2,
                "episode": 13,
                "year": 2021,
                "series_imdb_id": "tt11640018",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_rejects_unsupported_language(self):
        provider = self.mod.SuperSubtitlesProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used"))

        self.assertEqual(provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "spa"}], {}), [])

    def test_search_rejects_unsupported_forced_or_hearing_impaired_variants(self):
        provider = self.mod.SuperSubtitlesProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used"))

        self.assertEqual(
            provider.search({"kind": "episode", "series": "La Brea", "season": 2, "episode": 13}, [{"alpha3": "eng", "forced": True}], {}),
            [],
        )
        self.assertEqual(
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "hun", "hi": True}], {}),
            [],
        )

    def test_download_selects_requested_episode_from_zip_pack(self):
        provider = self.mod.SuperSubtitlesProvider()
        archive = _zip_body(
            {
                "La.Brea.S02E12.720p.WEB.H264-CAKES.srt": b"wrong",
                "La.Brea.S02E13.720p.WEB.H264-CAKES.srt": b"right",
                "La.Brea.S02E13.1080p.WEB.H264-NTb.srt": b"right but lower release score",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://feliratok.eu/index.php?action=letolt&felirat=1691315119",
                "filename": "La.Brea.S02.WEB.WEBRip.WEB-DL.720p.1080p.ENG.zip",
                "season": 2,
                "episode": 13,
                "release_info": "La Brea S02E13 WEB.720p-CAKES",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"right")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_rejects_archive_without_requested_episode(self):
        provider = self.mod.SuperSubtitlesProvider()
        archive = _zip_body({"La.Brea.S02E12.720p.WEB.H264-CAKES.srt": b"wrong"})
        provider._http_get = lambda url, timeout=30, referer=None: archive

        with self.assertRaisesRegex(ValueError, "requested episode"):
            provider.download(
                {
                    "url": "https://feliratok.eu/index.php?action=letolt&felirat=1691315119",
                    "filename": "La.Brea.S02.WEB.WEBRip.WEB-DL.720p.1080p.ENG.zip",
                    "season": 2,
                    "episode": 13,
                    "release_info": "La Brea (WEB.720p-CAKES)",
                },
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_returns_direct_subtitle_body(self):
        result = self.mod.extract_download(
            b"1\n00:00:01,000 --> 00:00:02,000\nHello\n",
            {"filename": "Dune.srt"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["encoding"], "utf-8")

    def test_request_url_encodes_raw_spaces_without_breaking_query_separators(self):
        result = self.mod._request_url(
            "https://feliratok.eu/index.php?action=letolt&fnev=Dune (2021).hun.srt&felirat=1735404922"
        )

        self.assertEqual(
            result,
            "https://feliratok.eu/index.php?action=letolt&fnev=Dune%20%282021%29.hun.srt&felirat=1735404922",
        )


if __name__ == "__main__":
    unittest.main()
