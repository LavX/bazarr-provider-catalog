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

    def test_download_returns_raw_zip_archive_with_episode(self):
        provider = self.mod.SuperSubtitlesProvider()
        archive = _zip_body(
            {
                "La.Brea.S02E12.720p.WEB.H264-CAKES.srt": b"wrong",
                "La.Brea.S02E13.720p.WEB.H264-CAKES.srt": b"right",
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

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 13)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_returns_raw_rar_archive_with_episode(self):
        provider = self.mod.SuperSubtitlesProvider()
        archive = b"Rar!\x1a\x07\x00" + b"binary rar payload"
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://feliratok.eu/index.php?action=letolt&felirat=1691315119",
                "filename": "La.Brea.S02E13.720p.WEB.H264-CAKES.rar",
                "season": 2,
                "episode": 13,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 13)
        self.assertNotIn("content_b64", result)

    def test_download_archive_for_movie_carries_no_episode(self):
        archive = _zip_body({"Dune.2021.1080p.srt": b"sub"})

        result = self.mod.extract_download(
            archive,
            {"filename": "Dune.2021.zip", "season": None, "episode": None},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertIsNone(result["episode"])

    def test_download_rejects_empty_body(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            self.mod.extract_download(b"", {"filename": "Dune.srt"})

    def test_download_rejects_html_error_page(self):
        with self.assertRaisesRegex(ValueError, "HTML"):
            self.mod.extract_download(
                b"<!DOCTYPE html><html><body>error</body></html>",
                {"filename": "Dune.zip"},
            )

    def test_download_returns_direct_subtitle_body(self):
        result = self.mod.extract_download(
            b"1\n00:00:01,000 --> 00:00:02,000\nHello\n",
            {"filename": "Dune.srt"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("encoding", result)

    def test_download_pins_zip_member_by_release_group_for_same_episode(self):
        # A pack with two release groups for the requested episode: the host's episode-only
        # pick cannot tell them apart, so the worker pins the scored release group member.
        archive = _zip_body(
            {
                "La.Brea.S02E13.720p.WEB.H264-CAKES.srt": b"cakes",
                "La.Brea.S02E13.1080p.WEB.H264-FLUX.srt": b"flux",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "La.Brea.S02.WEB.720p.ENG.zip",
                "season": 2,
                "episode": 13,
                "release_info": "La Brea (WEB.720p-CAKES)",
            },
        )

        self.assertEqual(result["member"], "La.Brea.S02E13.720p.WEB.H264-CAKES.srt")
        self.assertNotIn("episode", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())

    def test_download_pins_zip_member_with_separated_season_episode_token(self):
        archive = _zip_body(
            {
                "La.Brea.S02.E13.720p.WEB-CAKES.srt": b"cakes",
                "La.Brea.S02.E13.1080p.WEB-FLUX.srt": b"flux",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "La.Brea.S02.zip",
                "season": 2,
                "episode": 13,
                "release_info": "La Brea (WEB.720p-CAKES)",
            },
        )

        self.assertEqual(result["member"], "La.Brea.S02.E13.720p.WEB-CAKES.srt")
        self.assertNotIn("episode", result)

    def test_download_defers_when_no_release_field_breaks_the_tie(self):
        # Two members for the requested episode but nothing to disambiguate: defer to the
        # host episode pick rather than guess (which could deliver the wrong file).
        archive = _zip_body(
            {
                "La.Brea.S02E13.eng.srt": b"a",
                "La.Brea.S02E13.alt.srt": b"b",
            }
        )

        result = self.mod.extract_download(
            archive,
            {"filename": "La.Brea.S02.zip", "season": 2, "episode": 13, "release_info": "La Brea"},
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 13)

    def test_download_defers_for_single_episode_member(self):
        archive = _zip_body(
            {
                "La.Brea.S02E12.720p.WEB-CAKES.srt": b"wrong",
                "La.Brea.S02E13.720p.WEB-CAKES.srt": b"right",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "La.Brea.S02.zip",
                "season": 2,
                "episode": 13,
                "release_info": "La Brea (WEB.720p-CAKES)",
            },
        )

        # Only one member carries S02E13, so the host episode pick already lands there.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 13)

    def test_download_does_not_mispick_member_from_a_different_season(self):
        # A repeated episode number across seasons: requesting S02E05 must never pin S01E05.
        archive = _zip_body(
            {
                "Show.S01E05.720p.WEB-CAKES.srt": b"season1",
                "Show.S03E09.720p.WEB-CAKES.srt": b"season3",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "Show.complete.zip",
                "season": 2,
                "episode": 5,
                "release_info": "Show (WEB.720p-CAKES)",
            },
        )

        # No member carries S02E05; pinning S01E05 would hard-fail the host, so defer.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 5)

    def test_download_ignores_sidecar_and_directory_members(self):
        archive = _zip_body(
            {
                "subs/": b"",
                "__MACOSX/._La.Brea.S02E13.srt": b"junk",
                ".DS_Store": b"junk",
                "La.Brea.S02E13.720p.WEB-CAKES.srt": b"cakes",
                "La.Brea.S02E13.1080p.WEB-FLUX.srt": b"flux",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "La.Brea.S02.zip",
                "season": 2,
                "episode": 13,
                "release_info": "La Brea (WEB.720p-CAKES)",
            },
        )

        self.assertEqual(result["member"], "La.Brea.S02E13.720p.WEB-CAKES.srt")

    def test_download_resolution_token_does_not_match_episode_digits(self):
        # "720" (S07E20) must not be matched against "720p"; the episode guard must hold.
        archive = _zip_body(
            {
                "Show.S07E20.1080p.WEB-CAKES.srt": b"e20",
                "Show.S07E21.720p.WEB-CAKES.srt": b"e21",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "Show.S07.zip",
                "season": 7,
                "episode": 20,
                "release_info": "Show (WEB.720p-CAKES)",
            },
        )

        # Only S07E20 is a real episode match; the "720p" on the other member must not win.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 20)

    def test_download_pins_bare_episode_token_member(self):
        # SuperSubtitles packs often name members with a bare episode token and no season,
        # e.g. "Show.E05.720p-CAKES.srt". The requested episode must still match so the
        # release tie-breaker can pin the scored release instead of deferring to the host.
        archive = _zip_body(
            {
                "Show.E05.720p.WEB-CAKES.srt": b"cakes",
                "Show.E05.1080p.WEB-FLUX.srt": b"flux",
                "Show.E06.720p.WEB-CAKES.srt": b"e6",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "Show.complete.zip",
                "season": 1,
                "episode": 5,
                "release_info": "Show (WEB.720p-CAKES)",
            },
        )

        self.assertEqual(result["member"], "Show.E05.720p.WEB-CAKES.srt")

    def test_download_bare_episode_match_skipped_when_season_tokens_present(self):
        # When members carry season+episode tokens, the bare-eNN fallback must NOT run: a
        # season-2 request must never pin a same-numbered episode from another season.
        archive = _zip_body(
            {
                "Show.S01E05.720p.WEB-CAKES.srt": b"s1",
                "Show.S03E05.720p.WEB-CAKES.srt": b"s3",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "Show.complete.zip",
                "season": 2,
                "episode": 5,
                "release_info": "Show (WEB.720p-CAKES)",
            },
        )

        # No S02E05 member; bare matching is disabled because season tokens exist -> defer.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 5)

    def test_download_does_not_pin_forced_member_for_non_forced_request(self):
        # A non-forced request must never pin a member carrying a delimited "forced" token.
        # The forced member matches the requested release group/resolution/source, so without
        # forced-awareness it outscores the non-forced member and gets pinned, silently
        # delivering a forced subtitle (the host's exact "member in namelist" check honours
        # whatever we pin). The fix drops the forced member and defers to host episode pick.
        archive = _zip_body(
            {
                "La.Brea.S02E13.720p.WEB-CAKES.forced.srt": b"forced",
                "La.Brea.S02E13.eng.srt": b"normal",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "La.Brea.S02.zip",
                "season": 2,
                "episode": 13,
                "forced": False,
                "release_info": "La Brea (WEB.720p-CAKES)",
            },
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 13)

    def test_download_pins_forced_member_for_forced_request(self):
        # The mirror case: a forced request must still resolve the forced member.
        archive = _zip_body(
            {
                "La.Brea.S02E13.720p.WEB-CAKES.forced.srt": b"forced",
                "La.Brea.S02E13.1080p.WEB-FLUX.srt": b"normal",
            }
        )

        result = self.mod.extract_download(
            archive,
            {
                "filename": "La.Brea.S02.zip",
                "season": 2,
                "episode": 13,
                "forced": True,
                "release_info": "La Brea (WEB.720p-CAKES)",
            },
        )

        self.assertEqual(result["member"], "La.Brea.S02E13.720p.WEB-CAKES.forced.srt")

    def test_release_group_parses_last_hyphen_token_for_titled_release(self):
        # A hyphen in the title must not steal the release group: the LAST hyphen token wins.
        self.assertEqual(
            self.mod._release_group_from_text("Spider-Man.2002.720p.WEB-SPARKS"),
            "SPARKS",
        )
        self.assertEqual(
            self.mod._release_group_from_text("Spider-Man.2002.1080p.BluRay.x264-AMIABLE.mkv"),
            "AMIABLE",
        )

    def test_download_rar_archive_defers_to_episode_path(self):
        archive = b"Rar!\x1a\x07\x00" + b"binary rar payload"

        result = self.mod.extract_download(
            archive,
            {"filename": "La.Brea.S02E13.rar", "season": 2, "episode": 13, "release_info": "La Brea (WEB.720p-CAKES)"},
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 13)

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
