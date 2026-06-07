import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subtitrarinoi"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subtitrarinoi_provider", PROVIDER_DIR / "provider.py"
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


BREAKING_BAD_HTML = (FIXTURE_DIR / "subtitrarinoi_search_breaking_bad.html").read_bytes()
INCEPTION_HTML = (FIXTURE_DIR / "subtitrarinoi_search_inception.html").read_bytes()


class SubtitrariNoiParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_build_query_params_prefers_imdb_id_without_tt_prefix(self):
        params = self.mod.build_query_params({"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"})

        self.assertEqual(params["search_q"], "1")
        self.assertEqual(params["tip"], "2")
        self.assertEqual(params["an"], "Toti anii")
        self.assertEqual(params["gen"], "Toate")
        self.assertEqual(params["cautare"], "1375666")
        self.assertEqual(params["query_q"], "1375666")

    def test_build_query_params_prefers_series_imdb_for_episode_queries(self):
        params = self.mod.build_query_params(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 1,
                "episode": 1,
                "imdb_id": "tt0959621",
                "series_imdb_id": "tt0903747",
            }
        )

        self.assertEqual(params["cautare"], "0903747")
        self.assertEqual(params["query_q"], "0903747")

    def test_parse_search_results_extracts_row_and_following_comments(self):
        rows = self.mod.parse_search_results(BREAKING_BAD_HTML)

        self.assertEqual(rows[0]["subtitle_id"], "74168")
        self.assertEqual(rows[0]["title"], "Breaking Bad")
        self.assertEqual(rows[0]["year"], 2008)
        self.assertEqual(rows[0]["imdb_id"], "tt0903747")
        self.assertEqual(rows[0]["uploader"], "Anonim")
        self.assertEqual(rows[0]["download_count"], 913)
        self.assertEqual(rows[0]["comments"], "Sezoanele 1-5 complete.")
        self.assertEqual(
            rows[0]["download_url"],
            "https://www.subtitrari-noi.ro/74168-https://subtitrari-noi.ro/Arhive/new_archieve_1776587148_Breaking_Bad_(2008).zip",
        )

    def test_parse_search_results_keeps_multiple_matching_movies(self):
        rows = self.mod.parse_search_results(INCEPTION_HTML)

        self.assertEqual([row["subtitle_id"] for row in rows], ["75177", "72779"])
        self.assertEqual(rows[0]["comments"], "Inception 2010 2160p UHD BluRay HYBRiD REMUX x265 10bit HDR TrueHD 7.1 Atmos-Flights si WEB-DL.")

    def test_derive_episode_matches_legacy_imdb_and_season_behavior(self):
        row = self.mod.parse_search_results(BREAKING_BAD_HTML)[0]

        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            row,
        )

        self.assertIn("series", matches)
        self.assertIn("imdb_id", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_derive_episode_rejects_wrong_season_comment(self):
        row = self.mod.parse_search_results(BREAKING_BAD_HTML)[0]
        row["comments"] = "Sezonul 2 ep. 1-7"

        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            row,
        )

        self.assertNotIn("season", matches)
        self.assertNotIn("episode", matches)

    def test_derive_episode_honors_romanian_episode_ranges(self):
        row = self.mod.parse_search_results(BREAKING_BAD_HTML)[0]
        row["comments"] = "S01, episoadele 1-8"

        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 9, "series_imdb_id": "tt0903747"},
            row,
        )

        self.assertIn("season", matches)
        self.assertNotIn("episode", matches)


class SubtitrariNoiProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_movie_result_and_orders_by_download_count(self):
        provider = self.mod.SubtitrariNoiProvider()
        calls = []

        def post_stub(url, data, timeout=15, referer=None):
            del timeout, referer
            calls.append((url, data))
            return INCEPTION_HTML

        provider._http_post = post_stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666", "release_group": "Flights"},
            [{"alpha3": "ron", "alpha2": "ro", "hi": False, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[0][0], self.mod.API_URL)
        self.assertEqual(calls[0][1]["cautare"], "1375666")
        self.assertEqual(results[0]["provider"], "subtitrarinoi")
        self.assertEqual(results[0]["language"], {"alpha3": "ron", "alpha2": "ro", "hi": False, "forced": False})
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "75177")

    def test_search_rejects_episode_rows_without_matching_season(self):
        provider = self.mod.SubtitrariNoiProvider()
        row = self.mod.parse_search_results(BREAKING_BAD_HTML)[0]
        row["comments"] = "Sezonul 2 ep. 1-7"
        html = self._search_html_from_row(row)
        provider._http_post = lambda url, data, timeout=15, referer=None: html

        results = provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_rejects_episode_rows_pointing_at_a_different_episode(self):
        provider = self.mod.SubtitrariNoiProvider()
        row = self.mod.parse_search_results(BREAKING_BAD_HTML)[0]
        row["comments"] = "Sezonul 1 ep. 2"
        html = self._search_html_from_row(row)
        provider._http_post = lambda url, data, timeout=15, referer=None: html

        results = provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_does_not_label_normal_rows_as_forced_or_hi(self):
        provider = self.mod.SubtitrariNoiProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: BREAKING_BAD_HTML

        forced_results = provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            [{"alpha3": "ron", "alpha2": "ro", "hi": False, "forced": True}],
            {},
        )
        hi_results = provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            [{"alpha3": "ron", "alpha2": "ro", "hi": True, "forced": False}],
            {},
        )

        self.assertEqual(forced_results, [])
        self.assertEqual(hi_results, [])

    def test_search_returns_episode_result_for_normal_language_variant(self):
        provider = self.mod.SubtitrariNoiProvider()
        provider._http_post = lambda url, data, timeout=15, referer=None: BREAKING_BAD_HTML

        results = provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1, "series_imdb_id": "tt0903747"},
            [{"alpha3": "ron", "alpha2": "ro", "hi": False, "forced": False}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["forced"], False)
        self.assertIn("episode", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)
        self.assertTrue(results[0]["provider_payload"]["filename"].endswith(".zip"))

    def test_search_preserves_direct_subtitle_download_extension(self):
        provider = self.mod.SubtitrariNoiProvider()
        row = self.mod.parse_search_results(INCEPTION_HTML)[0]
        row["download_url"] = "https://www.subtitrari-noi.ro/75177-inception.srt"
        row["filename"] = "75177-inception.srt"
        html = self._search_html_from_row(row)
        provider._http_post = lambda url, data, timeout=15, referer=None: html

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "ron", "alpha2": "ro"}],
            {},
        )

        self.assertEqual(results[0]["filename"], "75177-inception.srt")
        self.assertEqual(results[0]["provider_payload"]["filename"], "75177-inception.srt")

    def test_search_rejects_unsupported_language_and_missing_title(self):
        provider = self.mod.SubtitrariNoiProvider()
        provider._http_post = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used"))

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "movie"}, [{"alpha3": "ron"}], {}), [])

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.SubtitrariNoiProvider()
        archive = _zip_body(
            {
                "Sezonul 1/Breaking.Bad.S01E02.720p.BluRay.x264.DTS-SYLER.srt": b"wrong episode",
                "Sezonul 1/Breaking.Bad.S01E01.2160p.NF.WEB-DL.DTS-HD.MA.5.1.HEVC-CRFW.srt": b"right episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.breaking-bad.s01e01.ro.zip",
                "season": 1,
                "episode": 1,
                "release_info": "Sezoanele 1-5 complete. CRFW WEB-DL 2160p",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 1)
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", result)
        self.assertNotIn("member", result)
        self.assertNotIn("encoding", result)

    def test_download_zip_pins_member_for_release_winner_in_season_pack(self):
        provider = self.mod.SubtitrariNoiProvider()
        # A season pack that bundles two releases of the requested S02E05; only one matches
        # the scored release_info, so the worker pins it instead of letting the host guess.
        archive = _zip_body(
            {
                "Sezonul 2/Show.S02E05.720p.WEB-DL.x264-AAA.srt": b"aaa release",
                "Sezonul 2/Show.S02E05.1080p.BluRay.x264-FLUX.srt": b"flux release",
                "Sezonul 1/Show.S01E05.720p.WEB-DL.x264-AAA.srt": b"wrong season",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s02e05.ro.zip",
                "season": 2,
                "episode": 5,
                "release_info": "Sezonul 2 complet. 1080p BluRay FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["member"], "Sezonul 2/Show.S02E05.1080p.BluRay.x264-FLUX.srt")
        self.assertNotIn("episode", result)
        self.assertNotIn("content_b64", result)

    def test_download_zip_pins_member_with_separated_episode_token(self):
        provider = self.mod.SubtitrariNoiProvider()
        # Two S01.E03 releases use a separator between the season and episode parts; the
        # requested S01E03 winner must still be matched and pinned by release overlap, while
        # the separated S01.E02 token must be excluded as a different episode.
        archive = _zip_body(
            {
                "Show.S01.E02.1080p.WEB.x264-FLUX.srt": b"wrong episode",
                "Show.S01.E03.720p.WEB.x264-AAA.srt": b"other release",
                "Show.S01.E03.1080p.WEB.x264-FLUX.srt": b"right episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s01e03.ro.zip",
                "season": 1,
                "episode": 3,
                "release_info": "Sezonul 1 complet. WEB FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["member"], "Show.S01.E03.1080p.WEB.x264-FLUX.srt")
        self.assertNotIn("episode", result)

    def test_download_zip_defers_when_release_overlap_is_tied(self):
        provider = self.mod.SubtitrariNoiProvider()
        # Two releases for the requested S02E05, but the release_info does not single one
        # out (no overlapping token). Pinning the wrong member would hard-fail the host, so
        # the worker defers to host-side episode selection instead.
        archive = _zip_body(
            {
                "Sezonul 2/Show.S02E05.720p.WEB-DL.x264-AAA.srt": b"aaa",
                "Sezonul 2/Show.S02E05.1080p.BluRay.x264-FLUX.srt": b"flux",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s02e05.ro.zip",
                "season": 2,
                "episode": 5,
                "release_info": "Sezonul 2 complet.",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["episode"], 5)
        self.assertNotIn("member", result)

    def test_download_zip_defers_when_only_short_token_differs(self):
        provider = self.mod.SubtitrariNoiProvider()
        # Two releases of the requested S02E05 whose meaningful release fields are identical;
        # they differ only by a stray one-char token (a "2" part marker on one member). The
        # release_info shares that "2", so the old length-blind scorer let the bare short
        # token be the sole differentiator and pinned the wrong member. A short token must
        # not decide the winner, so the worker defers to host-side episode selection.
        archive = _zip_body(
            {
                "Show.S02E05.1080p.BluRay.x264-FLUX.srt": b"flux a",
                "Show.S02E05.1080p.BluRay.x264-FLUX.2.srt": b"flux b",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s02e05.ro.zip",
                "season": 2,
                "episode": 5,
                "release_info": "Sezonul 2 complet. 1080p BluRay FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["episode"], 5)
        self.assertNotIn("member", result)

    def test_download_zip_defers_when_requested_episode_absent(self):
        provider = self.mod.SubtitrariNoiProvider()
        # The pack carries S02E05/S02E06 but not the requested S02E07. Pinning a wrong
        # episode would silently deliver the wrong subtitle, so defer to host episode
        # selection, which fails loudly on a true no-match.
        archive = _zip_body(
            {
                "Sezonul 2/Show.S02E05.1080p.BluRay.x264-FLUX.srt": b"e05",
                "Sezonul 2/Show.S02E06.1080p.BluRay.x264-FLUX.srt": b"e06",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s02e07.ro.zip",
                "season": 2,
                "episode": 7,
                "release_info": "Sezonul 2 complet. 1080p BluRay FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["episode"], 7)
        self.assertNotIn("member", result)

    def test_download_zip_does_not_match_episode_inside_resolution_token(self):
        provider = self.mod.SubtitrariNoiProvider()
        # The "720" in "720p" must not be read as S07E20, and "264" in "x264" must not be
        # read as S02E64. With genuine S07E20 absent, the worker defers rather than pinning
        # a wrong member off a resolution/codec substring.
        archive = _zip_body(
            {
                "Show.S01E20.720p.x264-AAA.srt": b"a",
                "Show.S01E21.720p.x264-FLUX.srt": b"b",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s07e20.ro.zip",
                "season": 7,
                "episode": 20,
                "release_info": "Sezonul 7 complet. 720p FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["episode"], 20)
        self.assertNotIn("member", result)

    def test_download_zip_ignores_sidecar_and_macosx_members(self):
        provider = self.mod.SubtitrariNoiProvider()
        # __MACOSX/dotfile sidecars and directory entries must be excluded from selection;
        # the genuine S01E03 member is the only real candidate, so the lone match defers to
        # host episode selection (no spurious pin off a sidecar).
        archive = _zip_body(
            {
                "Sezonul 1/": b"",
                "__MACOSX/Sezonul 1/._Show.S01E03.1080p.WEB.x264-FLUX.srt": b"junk",
                "Sezonul 1/.DS_Store": b"junk",
                "Sezonul 1/Show.S01E03.1080p.WEB.x264-FLUX.srt": b"real",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                "filename": "subtitrarinoi.show.s01e03.ro.zip",
                "season": 1,
                "episode": 3,
                "release_info": "Sezonul 1 complet. WEB FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["episode"], 3)
        self.assertNotIn("member", result)

    def test_download_rar_multi_member_defers_to_host_episode(self):
        provider = self.mod.SubtitrariNoiProvider()
        # RAR is not stdlib-listable, so even a multi-member pack must defer to the host
        # rather than guessing a member worker-side.
        archive = b"Rar!\x1a\x07\x00" + b"\x00" * 64
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.rar",
                "filename": "subtitrarinoi.show.s02e05.ro.rar",
                "season": 2,
                "episode": 5,
                "release_info": "Sezonul 2 complet. 1080p BluRay FLUX",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(result["episode"], 5)
        self.assertNotIn("member", result)

    def test_download_rar_archive_returns_raw_archive_for_host(self):
        provider = self.mod.SubtitrariNoiProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        archive = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/74168-example.rar",
                "filename": "subtitrarinoi.breaking-bad.s01e07.ro.rar",
                "season": 1,
                "episode": 7,
                "release_info": "Breaking Bad S01E07",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 7)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.SubtitrariNoiProvider()
        archive = _zip_body({"Inception.2010.720p.BluRay.srt": b"movie subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://www.subtitrari-noi.ro/75177-inception.zip",
                "filename": "subtitrarinoi.inception.ro.zip",
            },
            {"alpha3": "ron", "alpha2": "ro"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertIsNone(result["episode"])
        self.assertNotIn("content_b64", result)

    def test_download_returns_direct_subtitle_body(self):
        result = self.mod.extract_download(
            b"1\n00:00:01,000 --> 00:00:02,000\nSalut\n",
            {"filename": "movie.srt"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nSalut\n")
        self.assertEqual(result["format"], "srt")
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", result)
        self.assertNotIn("archive_b64", result)

    def test_download_rejects_unavailable_text_for_archive_payload(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"Ne pare rau, subtitrarea nu este disponibila momentan.",
                {"filename": "movie.zip"},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.SubtitrariNoiProvider()
        provider._http_get = lambda url, timeout=30, referer=None: (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.subtitrari-noi.ro/74168-example.html",
                    "filename": "subtitrarinoi-74168.zip",
                },
                {"alpha3": "ron", "alpha2": "ro"},
                {},
            )

    def test_download_rejects_empty_body(self):
        provider = self.mod.SubtitrariNoiProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b""

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "url": "https://www.subtitrari-noi.ro/74168-example.zip",
                    "filename": "subtitrarinoi-74168.zip",
                },
                {"alpha3": "ron", "alpha2": "ro"},
                {},
            )

    def _search_html_from_row(self, row):
        return f"""
        <div id="round">
          <div id="content-main">
            <a href="{row['page_link']}">{row['title']} ({row['year']})</a>
          </div>
          <p>Uploader: {row['uploader']}</p>
          <p>Traducator: {row['translator']}</p>
          <p>Descarcari: {row['download_count']}</p>
          <p><a href="https://www.imdb.com/title/{row['imdb_id']}/">IMDb</a></p>
          <div style="font-weight:bold;font-style:italic">{row['comments']}</div>
          <p class="buton"><a href="{row['download_url']}">Download</a></p>
        </div>
        """.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
