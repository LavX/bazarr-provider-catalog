import base64
import hashlib
import importlib.util
import io
import itertools
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "hdbits"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "hdbits_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_json(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


MOVIE_VIDEO = _fixture_json("hdbits_video_dune_2021.json")
EPISODE_VIDEO = _fixture_json("hdbits_video_chernobyl_s01e01.json")
MOVIE_TORRENTS = _fixture_json("hdbits_torrents_dune.json")
EPISODE_TORRENTS = _fixture_json("hdbits_torrents_chernobyl.json")
MOVIE_SUBS_1001 = _fixture_json("hdbits_subtitles_dune_1001.json")
MOVIE_SUBS_1002 = _fixture_json("hdbits_subtitles_dune_1002.json")
EPISODE_SUBS_2001 = _fixture_json("hdbits_subtitles_chernobyl_2001.json")
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nHDBits fixture.\n"


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class HDBitsLookupTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_lookup_uses_imdb_id_without_tt_prefix(self):
        lookup, matches, episode = self.mod.build_lookup(MOVIE_VIDEO)

        self.assertEqual(lookup, {"imdb": {"id": "1160419"}})
        self.assertIsNone(episode)
        self.assertEqual(matches, ["imdb_id", "title", "year"])

    def test_episode_lookup_uses_tvdb_id_and_season(self):
        lookup, matches, episode = self.mod.build_lookup(EPISODE_VIDEO)

        self.assertEqual(lookup, {"tvdb": {"id": 360893, "season": 1}})
        self.assertEqual(episode, 1)
        self.assertEqual(
            matches,
            ["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
        )

    def test_movie_lookup_empty_without_imdb_id(self):
        video = {**MOVIE_VIDEO, "imdb_id": "", "imdb": ""}

        lookup, matches, episode = self.mod.build_lookup(video)

        self.assertEqual(lookup, {})
        self.assertEqual(matches, [])
        self.assertIsNone(episode)

    def test_episode_lookup_empty_without_tvdb_id(self):
        video = {**EPISODE_VIDEO}
        video.pop("series_tvdb_id", None)
        video.pop("tvdb_id", None)
        video.pop("tvdb", None)

        lookup, matches, episode = self.mod.build_lookup(video)

        self.assertEqual(lookup, {})
        self.assertEqual(matches, [])
        self.assertIsNone(episode)

    def test_search_skips_api_when_required_id_missing(self):
        provider = self.mod.HDBitsProvider()

        def fail(*_args, **_kwargs):
            raise AssertionError("search must not call the HDBits API without an id")

        provider._post_json = fail
        results = provider.search(
            {**MOVIE_VIDEO, "imdb_id": "", "imdb": ""},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(results, [])


class HDBitsLanguageAndFilterTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_hdbits_special_language_codes_match_legacy_behavior(self):
        self.assertEqual(self.mod.hdbits_language_to_alpha3("uk"), "eng")
        self.assertEqual(self.mod.hdbits_language_to_alpha3("br"), "por")
        self.assertEqual(self.mod.hdbits_language_to_alpha3("gr"), "ell")
        self.assertEqual(self.mod.hdbits_language_to_alpha3("es"), "spa")

    def test_parse_subtitles_filters_extension_language_and_blocked_keywords(self):
        rows = self.mod.parse_subtitles(
            MOVIE_SUBS_1001["data"] + MOVIE_SUBS_1002["data"],
            requested_alpha3=[
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            ],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        # 502 is an HDBits "br" row, returned as Brazilian Portuguese.
        self.assertEqual([row["subtitle_id"] for row in rows], [501, 502])
        self.assertEqual([row["language"] for row in rows], ["eng", "por"])
        self.assertEqual([row["country_alpha2"] for row in rows], [None, "BR"])
        self.assertTrue(all("commentary" not in row["release_info"].lower() for row in rows))

    def test_parse_subtitles_accepts_sub_files(self):
        rows = self.mod.parse_subtitles(
            [
                {
                    "filename": "Dune.2021.1080p.BluRay.x264-GROUP.en.sub",
                    "id": 504,
                    "language": "uk",
                    "title": "Dune.2021.1080p.BluRay.x264-GROUP",
                }
            ],
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in rows], [504])

    def test_sub_files_are_selected_from_archives_and_keep_their_format(self):
        member = self.mod.select_subtitle_file(["readme.txt", "Dune.2021.en.sub"], {"language": {"alpha3": "eng"}})
        payload = self.mod.download_payload(b"{1}{25}Hello\n", {"filename": "Dune.2021.en.sub"})

        self.assertEqual(member, "Dune.2021.en.sub")
        self.assertEqual(payload["format"], "sub")

    def test_parse_subtitles_returns_brazilian_rows_for_plain_portuguese(self):
        rows = self.mod.parse_subtitles(
            MOVIE_SUBS_1001["data"] + MOVIE_SUBS_1002["data"],
            requested_alpha3=[
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt"},
            ],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        # The manifest declares only "por", so the host sends plain Portuguese.
        # The "br" row (502) answers it and keeps its Brazilian region.
        self.assertEqual([row["subtitle_id"] for row in rows], [501, 502])
        self.assertEqual([row["country_alpha2"] for row in rows], [None, "BR"])

    def test_parse_subtitles_keeps_plain_portuguese_rows_out_of_brazilian_requests(self):
        rows = self.mod.parse_subtitles(
            [{"filename": "Dune.2021.pt.srt", "id": 511, "language": "pt", "title": "Dune.2021"}],
            requested_alpha3=[{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual(rows, [])

    def test_every_advertised_language_is_reachable_from_an_hdbits_code(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        codes = set(self.mod.ALPHA2_TO_ALPHA3) | set(self.mod.SPECIAL_HDBITS_LANGUAGE)
        reachable = {self.mod.hdbits_language(code)[0] for code in codes}

        self.assertEqual(sorted(manifest["languages"]), self.mod.HDBITS_LANGUAGES)
        self.assertEqual(set(manifest["languages"]) - reachable, set())
        # "uk" is English on HDBits, so no row can be Ukrainian.
        self.assertNotIn("ukr", manifest["languages"])

    def test_parse_subtitles_allows_extraction_titles(self):
        rows = self.mod.parse_subtitles(
            [
                {
                    "filename": "Extraction.2020.1080p.en.srt",
                    "id": 801,
                    "language": "uk",
                    "title": "Extraction.2020.1080p",
                }
            ],
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in rows], [801])

    def test_episode_subtitles_skip_explicit_different_episode(self):
        rows = self.mod.parse_subtitles(
            EPISODE_SUBS_2001["data"],
            requested_alpha3={"eng", "ell"},
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        self.assertEqual([row["subtitle_id"] for row in rows], [601, 603])
        self.assertEqual({row["language"] for row in rows}, {"eng", "ell"})

    def test_parse_subtitles_preserves_forced_and_hi_variants(self):
        rows = [
            {
                "filename": "Dune.2021.Forced.en.srt",
                "id": 701,
                "language": "uk",
                "title": "Dune.2021.Forced",
            },
            {
                "filename": "Dune.2021.SDH.en.srt",
                "id": 702,
                "language": "uk",
                "title": "Dune.2021.SDH",
            },
            {
                "filename": "Dune.2021.en.srt",
                "id": 703,
                "language": "uk",
                "title": "Dune.2021",
            },
        ]

        normal = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )
        forced = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "forced": True}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )
        hi = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "hi": True}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in normal], [703])
        self.assertEqual([row["subtitle_id"] for row in forced], [701])
        self.assertTrue(forced[0]["forced"])
        self.assertEqual([row["subtitle_id"] for row in hi], [702])
        self.assertTrue(hi[0]["hearing_impaired"])

    def test_parse_subtitles_does_not_flag_hindi_as_hearing_impaired(self):
        rows = [
            {
                "filename": "Movie.2021.hi.srt",
                "id": 711,
                "language": "hi",
                "title": "Movie.2021",
            }
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "hin", "alpha2": "hi"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in parsed], [711])
        self.assertEqual(parsed[0]["language"], "hin")
        self.assertFalse(parsed[0]["hearing_impaired"])

    def test_parse_subtitles_rejects_episode_from_filename_tag(self):
        rows = [
            {
                "filename": "Chernobyl.S01E02.en.srt",
                "id": 721,
                "language": "uk",
                "title": "Chernobyl.S01.1080p.WEB-DL-GROUP",
            }
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        # Season-pack title hides the episode, but the filename tags S01E02 so an
        # S01E01 request must drop the row.
        self.assertEqual(parsed, [])

    def test_parse_subtitles_rejects_same_episode_from_another_season(self):
        rows = [
            {"filename": "Chernobyl.S02E01.en.srt", "id": 731, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.en.srt", "id": 732, "language": "uk", "title": "Chernobyl.S02E01.1080p"},
            {"filename": "Chernobyl.S01E01.en.srt", "id": 733, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E01.en.srt", "id": 734, "language": "uk", "title": "Chernobyl"},
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        # S02E01 is not S01E01. A bare E01 carries no season and still counts.
        self.assertEqual([row["subtitle_id"] for row in parsed], [733, 734])

    def test_parse_subtitles_reads_the_season_of_split_and_nxnn_markers(self):
        rows = [
            {"filename": "Chernobyl.S02.E01.en.srt", "id": 741, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.2x01.en.srt", "id": 742, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.Season.2.Episode.1.en.srt", "id": 743, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01.E01.en.srt", "id": 744, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.1x01.en.srt", "id": 745, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.Season.1.Episode.1.en.srt", "id": 746, "language": "uk", "title": "Chernobyl"},
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        # A separator between the season and the episode does not drop the season.
        self.assertEqual([row["subtitle_id"] for row in parsed], [744, 745, 746])

    def test_parse_subtitles_keeps_every_episode_of_a_multi_episode_file(self):
        rows = [
            {"filename": "Chernobyl.S01E01-E02.en.srt", "id": 751, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E01E02.en.srt", "id": 752, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E03-E04.en.srt", "id": 753, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E01-E02.en.srt", "id": 754, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E01.E02.en.srt", "id": 755, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E03.E04.en.srt", "id": 756, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E01-02.en.srt", "id": 757, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.1x01-02.en.srt", "id": 758, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.Season.1.Episode.1-2.en.srt", "id": 759, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S02E01-02.en.srt", "id": 760, "language": "uk", "title": "Chernobyl"},
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video={**EPISODE_VIDEO, "episode": 2},
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=2,
        )

        self.assertEqual([row["subtitle_id"] for row in parsed], [751, 752, 754, 755, 757, 758, 759])

    def test_parse_subtitles_does_not_span_the_gap_in_a_multi_episode_tag(self):
        rows = [
            {"filename": "Chernobyl.S01E01E10.en.zip", "id": 781, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E04.E06.en.zip", "id": 782, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E04.E06.en.rar", "id": 783, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E04-E06.en.zip", "id": 784, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E04.E05.en.zip", "id": 785, "language": "uk", "title": "Chernobyl"},
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video={**EPISODE_VIDEO, "episode": 5},
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=5,
        )

        # Only a range such as "E04-E06" holds the episodes between its ends.
        self.assertEqual([row["subtitle_id"] for row in parsed], [784, 785])

    def test_parse_subtitles_keeps_an_archive_whose_range_spans_the_episode(self):
        rows = [
            {"filename": "Chernobyl.1x01-10.en.zip", "id": 761, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.Season.1.Episode.1-10.en.zip", "id": 762, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E01-E10.en.zip", "id": 763, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E01-10.en.rar", "id": 764, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E01-E12.en.zip", "id": 765, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.2x01-10.en.zip", "id": 766, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S02E01-E10.en.zip", "id": 767, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E06-E10.en.zip", "id": 768, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.S01E01-E10.en.srt", "id": 769, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.1x01-10.en.srt", "id": 770, "language": "uk", "title": "Chernobyl"},
            # A number below the episode after it is no range.
            {"filename": "Chernobyl.S01E08.2.en.zip", "id": 771, "language": "uk", "title": "Chernobyl"},
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video={**EPISODE_VIDEO, "episode": 5},
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=5,
        )

        # The archive member is picked at download, while one subtitle file only
        # answers the episodes it lists.
        self.assertEqual([row["subtitle_id"] for row in parsed], [761, 762, 763, 764, 765])


class HDBitsSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_username_and_passkey(self):
        provider = self.mod.HDBitsProvider()

        with self.assertRaisesRegex(ValueError, "username"):
            provider.search(MOVIE_VIDEO, [{"alpha3": "eng"}], {"passkey": "secret"})
        with self.assertRaisesRegex(ValueError, "passkey"):
            provider.search(MOVIE_VIDEO, [{"alpha3": "eng"}], {"username": "user"})

    def test_movie_search_posts_authenticated_lookup_and_returns_results(self):
        provider = self.mod.HDBitsProvider()
        calls = []
        responses = {
            ("https://hdbits.org/api/torrents", json.dumps({"imdb": {"id": "1160419"}, "passkey": "secret", "username": "user"}, sort_keys=True)): MOVIE_TORRENTS,
            ("https://hdbits.org/api/subtitles", json.dumps({"passkey": "secret", "torrent_id": 1001, "username": "user"}, sort_keys=True)): MOVIE_SUBS_1001,
            ("https://hdbits.org/api/subtitles", json.dumps({"passkey": "secret", "torrent_id": 1002, "username": "user"}, sort_keys=True)): MOVIE_SUBS_1002,
        }

        def post_stub(url, payload, timeout=15):
            del timeout
            key = (url, json.dumps(payload, sort_keys=True))
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected POST: {key}")
            return responses[key]

        provider._post_json = post_stub
        results = provider.search(
            MOVIE_VIDEO,
            [
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            ],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 2)
        self.assertEqual([call[0] for call in calls], ["https://hdbits.org/api/torrents", "https://hdbits.org/api/subtitles", "https://hdbits.org/api/subtitles"])
        first = results[0]
        self.assertEqual(first["provider"], "hdbits")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("title", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertIn("source", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], 501)
        self.assertNotIn("passkey", first["provider_payload"])
        brazilian = next(item for item in results if item["provider_payload"]["subtitle_id"] == 502)
        self.assertEqual(brazilian["language"]["alpha3"], "por")
        self.assertEqual(brazilian["language"]["country_alpha2"], "BR")

    def test_search_surfaces_hdbits_api_errors(self):
        provider = self.mod.HDBitsProvider()
        provider._post_json = lambda url, payload, timeout=15: {"status": 5, "message": "bad passkey"}

        with self.assertRaisesRegex(ValueError, "bad passkey"):
            provider.search(
                MOVIE_VIDEO,
                [{"alpha3": "eng", "alpha2": "en"}],
                {"username": "user", "passkey": "secret", "request_delay_ms": 0},
            )

    def test_episode_search_filters_by_episode_and_tvdb_lookup(self):
        provider = self.mod.HDBitsProvider()
        calls = []
        responses = {
            ("https://hdbits.org/api/torrents", json.dumps({"passkey": "secret", "tvdb": {"id": 360893, "season": 1}, "username": "user"}, sort_keys=True)): EPISODE_TORRENTS,
            ("https://hdbits.org/api/subtitles", json.dumps({"passkey": "secret", "torrent_id": 2001, "username": "user"}, sort_keys=True)): EPISODE_SUBS_2001,
        }

        def post_stub(url, payload, timeout=15):
            del timeout
            key = (url, json.dumps(payload, sort_keys=True))
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected POST: {key}")
            return responses[key]

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(calls[0][0], "https://hdbits.org/api/torrents")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], 601)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)

    def test_episode_search_requires_row_or_torrent_to_name_the_episode(self):
        provider = self.mod.HDBitsProvider()
        torrents = {
            "data": [
                {"id": 3001, "name": "Chernobyl S01E01 1080p WEB-DL-GROUP"},
                {"id": 3002, "name": "Chernobyl S01E02 1080p WEB-DL-GROUP"},
                {"id": 3003, "name": "Chernobyl S01 1080p WEB-DL-GROUP"},
            ]
        }
        subtitles = {
            3001: {"data": [{"filename": "Chernobyl.en.srt", "id": 901, "language": "uk", "title": "Chernobyl"}]},
            3002: {"data": [{"filename": "Chernobyl.en.srt", "id": 902, "language": "uk", "title": "Chernobyl"}]},
            3003: {"data": [{"filename": "Chernobyl.en.srt", "id": 903, "language": "uk", "title": "Chernobyl"}]},
        }

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            return subtitles[payload["torrent_id"]]

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        # Only the S01E01 torrent verifies an unnumbered direct subtitle.
        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], [901])

    def test_episode_search_skips_torrent_for_another_season(self):
        provider = self.mod.HDBitsProvider()
        torrents = {"data": [{"id": 3101, "name": "Chernobyl S02E01 1080p WEB-DL-GROUP"}]}

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            raise AssertionError("a torrent for another season must not be scanned")

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_episode_search_skips_torrent_with_split_marker_for_another_season(self):
        provider = self.mod.HDBitsProvider()
        torrents = {"data": [{"id": 3102, "name": "Chernobyl S02.E01 1080p WEB-DL-GROUP"}]}

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            raise AssertionError("a torrent for another season must not be scanned")

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_episode_search_scans_a_torrent_whose_range_spans_the_episode(self):
        provider = self.mod.HDBitsProvider()
        torrents = {
            "data": [
                {"id": 3111, "name": "Chernobyl 1x01-10 1080p WEB-DL-GROUP"},
                {"id": 3112, "name": "Chernobyl Season 1 Episode 1-10 1080p WEB-DL-GROUP"},
                {"id": 3113, "name": "Chernobyl S01E01-E10 1080p WEB-DL-GROUP"},
                {"id": 3114, "name": "Chernobyl S01E06-E10 1080p WEB-DL-GROUP"},
                {"id": 3115, "name": "Chernobyl 2x01-10 1080p WEB-DL-GROUP"},
            ]
        }
        scanned = []

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            torrent_id = payload["torrent_id"]
            scanned.append(torrent_id)
            return {
                "data": [
                    {"filename": "Chernobyl.en.zip", "id": torrent_id * 10, "language": "uk", "title": "Chernobyl"},
                    {"filename": "Chernobyl.en.srt", "id": torrent_id * 10 + 1, "language": "uk", "title": "Chernobyl"},
                ]
            }

        provider._post_json = post_stub
        results = provider.search(
            {**EPISODE_VIDEO, "episode": 5},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        # A pack torrent is scanned, but it does not verify an unnumbered direct
        # subtitle, which may hold any of its episodes.
        self.assertEqual(scanned, [3111, 3112, 3113])
        self.assertEqual(
            sorted(item["provider_payload"]["subtitle_id"] for item in results), [31110, 31120, 31130]
        )

    def test_episode_search_skips_a_torrent_whose_tag_lists_other_episodes(self):
        provider = self.mod.HDBitsProvider()
        torrents = {
            "data": [
                {"id": 3121, "name": "Chernobyl S01E01E10 1080p WEB-DL-GROUP"},
                {"id": 3122, "name": "Chernobyl S01E04.E06 1080p WEB-DL-GROUP"},
                {"id": 3123, "name": "Chernobyl S01E04-E06 1080p WEB-DL-GROUP"},
            ]
        }
        scanned = []

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            scanned.append(payload["torrent_id"])
            return {"data": []}

        provider._post_json = post_stub
        provider.search(
            {**EPISODE_VIDEO, "episode": 5},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(scanned, [3123])

    def test_episode_search_does_not_read_a_codec_as_an_episode(self):
        provider = self.mod.HDBitsProvider()
        torrents = {"data": [{"id": 3103, "name": "Chernobyl S01 1080p BluRay DDP5.1x265-GROUP"}]}
        subtitles = {"data": [{"filename": "Chernobyl.S01.en.zip", "id": 921, "language": "uk", "title": "Chernobyl.S01"}]}
        provider._post_json = lambda url, payload, timeout=15: torrents if url == self.mod.TORRENTS_URL else subtitles

        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        # "5.1x265" is audio channels and a codec, not season 1 episode 265.
        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], [921])

    def test_episode_scores_leave_room_for_release_matches(self):
        provider = self.mod.HDBitsProvider()
        torrents = {"data": [{"id": 3201, "name": "Chernobyl S01E01 1080p WEB-DL-GROUP"}]}
        subtitles = {
            "data": [
                {"filename": "Chernobyl.S01E01.en.srt", "id": 941, "language": "uk", "title": "Chernobyl.S01E01.HDTV"},
                {"filename": "Chernobyl.S01E01.en.srt", "id": 942, "language": "uk", "title": "Chernobyl.S01E01.1080p.WEB-DL-GROUP"},
            ]
        }
        provider._post_json = lambda url, payload, timeout=15: torrents if url == self.mod.TORRENTS_URL else subtitles
        video = {**EPISODE_VIDEO, "resolution": "1080p", "release_group": "GROUP"}

        results = provider.search(
            video,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        scores = {item["provider_payload"]["subtitle_id"]: item["score"] for item in results}
        # Six identity matches alone used to reach 100 and tie every row.
        self.assertEqual(scores[941], 70)
        self.assertGreater(scores[942], scores[941])
        self.assertLess(scores[942], 100)


class HDBitsDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_direct_subtitle_with_passkey_from_config(self):
        provider = self.mod.HDBitsProvider()

        def get_stub(url, timeout=15):
            del timeout
            self.assertEqual(url, "https://hdbits.org/getdox.php?id=501&passkey=secret")
            return SRT_BODY

        provider._http_get = get_stub
        result = provider.download(
            {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": "movie.en.srt"},
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_matching_episode_from_zip(self):
        provider = self.mod.HDBitsProvider()
        zip_body = _zip_body(
            {
                "Chernobyl.S01E02.en.srt": b"wrong",
                "Chernobyl.S01E01.en.srt": SRT_BODY,
            }
        )
        provider._http_get = lambda url, timeout=15: zip_body

        result = provider.download(
            {
                "provider": "hdbits",
                "schema": 1,
                "subtitle_id": 601,
                "filename": "Chernobyl.S01E01.en.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(zip_body).hexdigest())
        self.assertEqual(result["season"], 1)
        self.assertEqual(result["episode"], 1)
        self.assertTrue(result["select_member"])
        self.assertNotIn("content_b64", result)

    def test_archive_selector_pins_member_by_requested_language(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1},
            {"alpha3": "ell", "alpha2": "el"},
            ["Chernobyl.S01E01.en.srt", "Chernobyl.S01E01.gr.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Chernobyl.S01E01.gr.srt"})

    def test_archive_selector_prefers_explicit_portuguese_region_independent_of_member_order(self):
        provider = self.mod.HDBitsProvider()
        portuguese = {"alpha3": "por", "alpha2": "pt"}
        brazilian_portuguese = {
            "alpha3": "por",
            "alpha2": "pt",
            "country_alpha2": "BR",
        }
        members = ["Show.S01E01.pt.srt", "Show.S01E01.br.srt"]

        for language, expected in (
            (portuguese, "Show.S01E01.pt.srt"),
            (brazilian_portuguese, "Show.S01E01.br.srt"),
        ):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(language=language, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": 1}, language, ordered_members, {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_rejects_explicit_brazilian_member_for_generic_portuguese(self):
        provider = self.mod.HDBitsProvider()
        for member in ("Show.S01E01.br.srt", "Show.S01E01.pt-BR.srt"):
            with self.subTest(member=member):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1},
                    {"alpha3": "por", "alpha2": "pt"},
                    [member],
                    {},
                )
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_uses_provider_country_when_host_language_has_no_region(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1, "country_alpha2": "BR"},
            {"alpha3": "por", "alpha2": "pt"},
            ["Show.S01E01.pt.srt", "Show.S01E01.br.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.br.srt"})

    def test_archive_selector_host_brazilian_language_overrides_stale_provider_country(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {
                "season": 1,
                "episode": 1,
                "language": "por",
                "country_alpha2": "PT",
            },
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            ["Show.S01E01.pt.srt", "Show.S01E01.br.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.br.srt"})

    def test_archive_selector_accepts_explicit_portuguese_region_for_plain_portuguese(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {},
            {"alpha3": "por", "alpha2": "pt"},
            ["Film.pt-PT.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Film.pt-PT.srt"})

    def test_archive_selector_rejects_explicit_other_portuguese_region(self):
        provider = self.mod.HDBitsProvider()
        cases = (
            (
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
                ["Film.pt-PT.srt"],
            ),
            (
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "PT"},
                ["Film.pt-BR.srt"],
            ),
        )

        for language, members in cases:
            with self.subTest(language=language, members=members):
                result = provider.select_archive_member({}, language, members, {})
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_preserves_provider_language_for_empty_host_language(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"language": "por", "country_alpha2": "BR"},
            {},
            ["Film.pt-PT.srt", "Film.pt-BR.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Film.pt-BR.srt"})

    def test_archive_selector_rejects_archive_missing_requested_episode(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1},
            {"alpha3": "eng", "alpha2": "en"},
            ["Chernobyl.S01E02.en.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_rejects_members_from_another_season(self):
        provider = self.mod.HDBitsProvider()
        for name in (
            "Show.S02.E01.en.srt",
            "Show.2x01.en.srt",
            "Show.Season.2.Episode.01.en.srt",
            "Season 2/Show.E01.en.srt",
            "Show.S02/01.en.srt",
            "Show.S02E01/Show.E01.en.srt",
        ):
            with self.subTest(name=name):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                )
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_pins_the_requested_season_in_either_member_order(self):
        provider = self.mod.HDBitsProvider()
        archives = (
            {1: "Show.S01.E01.en.srt", 2: "Show.S02.E01.en.srt"},
            {1: "Show.1x01.en.srt", 2: "Show.2x01.en.srt"},
            {1: "Show.Season.1.Episode.1.en.srt", 2: "Show.Season.2.Episode.1.en.srt"},
            {1: "Season 1/Show.E01.en.srt", 2: "Season 2/Show.E01.en.srt"},
            {1: "Season 1/01.en.srt", 2: "Season 2/01.en.srt"},
        )

        for members_by_season in archives:
            members = [members_by_season[2], members_by_season[1]]
            for season in (1, 2):
                for ordered_members in (members, list(reversed(members))):
                    with self.subTest(season=season, members=ordered_members):
                        result = provider.select_archive_member(
                            {"season": season, "episode": 1},
                            {"alpha3": "eng", "alpha2": "en"},
                            ordered_members,
                            {},
                        )
                        self.assertEqual(result, {"decision": "pin", "member": members_by_season[season]})

        # A folder that names two seasons gives no season, so it rejects neither.
        for season in (1, 2):
            with self.subTest(season=season):
                result = provider.select_archive_member(
                    {"season": season, "episode": 5}, {"alpha3": "eng", "alpha2": "en"}, ["Show.S01-S02/05.srt"], {}
                )
                self.assertEqual(result, {"decision": "pin", "member": "Show.S01-S02/05.srt"})

    def test_archive_selector_reads_the_episode_from_the_member_folder(self):
        provider = self.mod.HDBitsProvider()
        by_episode = {
            1: "Subs/Show.S01E01.1080p/2_English.srt",
            2: "Subs/Show.S01E02.1080p/2_English.srt",
        }
        members = [by_episode[2], by_episode[1]]
        for episode in (1, 2):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(episode=episode, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, ordered_members, {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": by_episode[episode]})

        # The "2" in the file name is a track index, not episode 2.
        result = provider.select_archive_member(
            {"season": 1, "episode": 2}, {"alpha3": "eng", "alpha2": "en"}, [by_episode[1]], {}
        )
        self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_lets_the_file_name_pick_within_a_multi_episode_folder(self):
        provider = self.mod.HDBitsProvider()
        packs = (
            {episode: f"Show.S01E01-E10/{episode:02d}.srt" for episode in range(1, 11)},
            {episode: f"Show.S01E01-10/{episode:02d}.srt" for episode in range(1, 11)},
            {1: "Show.S01E01E02/01.en.srt", 2: "Show.S01E01E02/02.en.srt"},
            {episode: f"Show.E01-E10/{episode:02d}.srt" for episode in range(1, 11)},
            {1: "Show.E01.E02/01.en.srt", 2: "Show.E01.E02/02.en.srt"},
            # In a pack a leading number names the episode, since every episode
            # would reuse the same track index.
            {episode: f"Show.S01E01-E10/{episode:02d}_English.srt" for episode in range(1, 11)},
            # A track index is never zero-padded, so "02_" names episode 2.
            {1: "Show.S01E01E02/01_English.srt", 2: "Show.S01E01E02/02_English.srt"},
            {1: "Show.S01E01-E02/01_English.srt", 2: "Show.S01E01-E02/02_English.srt"},
            # A per-episode folder inside a pack names the episode, whatever
            # track index its file name carries.
            {
                episode: f"Show.S01E01-E10/Show.S01E{episode:02d}.1080p/2_English.srt"
                for episode in (2, 5, 10)
            },
        )
        for by_episode in packs:
            members = list(by_episode.values())
            for episode in sorted({1, 2, 5, 10} & set(by_episode)):
                for ordered_members in (members, list(reversed(members))):
                    with self.subTest(episode=episode, members=ordered_members):
                        result = provider.select_archive_member(
                            {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, ordered_members, {}
                        )
                        self.assertEqual(result, {"decision": "pin", "member": by_episode[episode]})

        # A lone member still answers only the episode its file name gives.
        for episode, expected in ((5, "pin"), (6, "reject")):
            with self.subTest(episode=episode):
                result = provider.select_archive_member(
                    {"season": 1, "episode": episode},
                    {"alpha3": "eng", "alpha2": "en"},
                    ["Show.S01E01-E10/05.srt"],
                    {},
                )
                self.assertEqual(result["decision"], expected)

        # A folder for one double-episode video decides for both episodes, and a
        # track index such as "2_" there never picks one of them.
        for name in (
            "Show.S01E01E02/English.srt",
            "Show.S01E01E02/1_English.srt",
            "Show.S01E01E02/2_English.srt",
            "Show.S01E01E02/3_English.srt",
            "Subs/Show.S01E01E02.1080p.BluRay.x264-GRP/2_English.srt",
            "Show.S01E01-02/English.srt",
            "Show.E01.E02/English.srt",
        ):
            for episode in (1, 2):
                with self.subTest(name=name, episode=episode):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": name})

        # A pack folder names only its first and last episode, so a member whose
        # file name picks none of them is not pinned for any episode.
        members = [
            "Show.S01E01-E10/Show.Pilot.en.srt",
            "Show.S01E01-E10/Show.The.Return.en.srt",
            "Show.S01E01-E10/Show.Finale.en.srt",
        ]
        for episode in (1, 5, 10):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(episode=episode, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, ordered_members, {}
                    )
                    self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_reads_the_track_number_in_a_two_episode_range_folder(self):
        provider = self.mod.HDBitsProvider()
        packs = (
            {1: "Show.S01E01-E02/1_English.srt", 2: "Show.S01E01-E02/2_English.srt"},
            {1: "Show.S01E01-02/1_English.srt", 2: "Show.S01E01-02/2_English.srt"},
            {1: "Show.1x01-02/1_English.srt", 2: "Show.1x01-02/2_English.srt"},
            {1: "Show.E01-E02/1_English.srt", 2: "Show.E01-E02/2_English.srt"},
        )
        for by_episode in packs:
            members = list(by_episode.values())
            for episode in (1, 2):
                for ordered_members in (members, list(reversed(members))):
                    with self.subTest(episode=episode, members=ordered_members):
                        result = provider.select_archive_member(
                            {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, ordered_members, {}
                        )
                        self.assertEqual(result, {"decision": "pin", "member": by_episode[episode]})

        # Some tools name one double-episode video "S01E01-E02" as well, so a
        # lone track there still answers both of its episodes.
        for name in ("Show.S01E01-E02/2_English.srt", "Show.S01E01-02/1_English.srt"):
            for episode in (1, 2):
                with self.subTest(name=name, episode=episode):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": name})
            # Neither reading reaches an episode outside the folder.
            with self.subTest(name=name, episode=3):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 3}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                )
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_prefers_the_file_name_marker_over_its_folder(self):
        provider = self.mod.HDBitsProvider()
        members = ["Show.S01E01/notes.srt", "Show.S01E01/Show.S01E01.srt"]
        for ordered_members in (members, list(reversed(members))):
            with self.subTest(members=ordered_members):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1}, {"alpha3": "eng", "alpha2": "en"}, ordered_members, {}
                )
                self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01/Show.S01E01.srt"})

    def test_archive_selector_does_not_read_the_season_number_as_the_episode(self):
        provider = self.mod.HDBitsProvider()
        for name in ("Show.Season.1.Episode.05.en.srt", "Show.Season.1.05.en.srt"):
            with self.subTest(name=name):
                wrong = provider.select_archive_member(
                    {"season": 1, "episode": 1}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                )
                right = provider.select_archive_member(
                    {"season": 1, "episode": 5}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                )
                self.assertEqual(wrong, {"decision": "reject"})
                self.assertEqual(right, {"decision": "pin", "member": name})

    def test_archive_selector_keeps_every_episode_of_a_multi_episode_member(self):
        provider = self.mod.HDBitsProvider()
        for name in (
            "Show.S01E01-E02.en.srt",
            "Show.S01E01E02.en.srt",
            "Show.S01.E01.E02.en.srt",
            "Show.S01E01-02.en.srt",
            "Show.E01-02.en.srt",
            "Show.1x01-02.en.srt",
            "Show.Season.1.Episode.1-2.en.srt",
            "Show.E01-E02.en.srt",
            "Show.E01.E02.en.srt",
            "Show.E01.E02.E03.en.srt",
        ):
            for episode in (1, 2):
                with self.subTest(name=name, episode=episode):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": episode}, {"alpha3": "eng", "alpha2": "en"}, [name], {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": name})

    def test_archive_selector_prefers_the_requested_variant_in_any_member_order(self):
        provider = self.mod.HDBitsProvider()
        archives = (
            (
                {"season": 1, "episode": 1, "language": "eng"},
                {
                    (False, False): "Show.S01E01.en.srt",
                    (True, False): "Show.S01E01.en.sdh.srt",
                    (False, True): "Show.S01E01.en.forced.srt",
                },
            ),
            (
                {"season": 1, "episode": 1, "language": "eng"},
                {
                    (False, False): "Show.S01E01.en.srt",
                    (True, False): "Show.S01E01.en.hi.srt",
                    (False, True): "Show.S01E01.en.forced.srt",
                },
            ),
            (
                {"language": "eng"},
                {
                    (False, False): "Movie.2021.en.srt",
                    (True, False): "Movie.2021.en.SDH.srt",
                    (False, True): "Movie.2021.en.Forced.srt",
                },
            ),
        )

        for payload, members_by_variant in archives:
            for ordered_members in itertools.permutations(members_by_variant.values()):
                for (hi, forced), expected in members_by_variant.items():
                    language = {"alpha3": "eng", "alpha2": "en", "hi": hi, "forced": forced}
                    with self.subTest(members=ordered_members, hi=hi, forced=forced):
                        result = provider.select_archive_member(payload, language, list(ordered_members), {})
                        self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_reads_the_variant_from_the_provider_payload(self):
        provider = self.mod.HDBitsProvider()
        members = ["Show.S01E01.en.srt", "Show.S01E01.en.sdh.srt", "Show.S01E01.en.forced.srt"]
        for flags, expected in (
            ({"hi": True, "forced": False}, "Show.S01E01.en.sdh.srt"),
            ({"hi": False, "forced": True}, "Show.S01E01.en.forced.srt"),
        ):
            with self.subTest(flags=flags):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1, "language": "eng", **flags},
                    {"alpha3": "eng", "alpha2": "en"},
                    members,
                    {},
                )
                self.assertEqual(result, {"decision": "pin", "member": expected})

        # A flag the host sends wins over the one stored at search time.
        result = provider.select_archive_member(
            {"season": 1, "episode": 1, "language": "eng", "hi": True, "forced": False},
            {"alpha3": "eng", "alpha2": "en", "hi": False},
            members,
            {},
        )
        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.en.srt"})

    def test_archive_selector_prefers_a_full_hi_file_over_a_forced_one_for_a_plain_request(self):
        provider = self.mod.HDBitsProvider()
        members = ["Show.S01E01.en.forced.srt", "Show.S01E01.en.sdh.srt"]
        for ordered_members in (members, list(reversed(members))):
            with self.subTest(members=ordered_members):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1},
                    {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False},
                    ordered_members,
                    {},
                )
                self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.en.sdh.srt"})

    def test_archive_selector_keeps_hindi_hi_as_a_language_when_ranking_variants(self):
        provider = self.mod.HDBitsProvider()
        members = ["Show.S01E01.hi.sdh.srt", "Show.S01E01.hi.srt"]
        for hi, expected in ((False, "Show.S01E01.hi.srt"), (True, "Show.S01E01.hi.sdh.srt")):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(hi=hi, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": 1},
                        {"alpha3": "hin", "alpha2": "hi", "hi": hi, "forced": False},
                        ordered_members,
                        {},
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_variant_never_overrides_episode_or_language(self):
        provider = self.mod.HDBitsProvider()
        hi_english = {"alpha3": "eng", "alpha2": "en", "hi": True, "forced": False}
        forced_english = {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": True}
        for episode, language, members, expected in (
            (1, hi_english, ["Show.S01E02.en.sdh.srt", "Show.S01E01.en.srt"], "Show.S01E01.en.srt"),
            (1, hi_english, ["Show.S01E01.fr.sdh.srt", "Show.S01E01.en.srt"], "Show.S01E01.en.srt"),
            (1, forced_english, ["Show.S01E01.en.srt"], "Show.S01E01.en.srt"),
            # Both members hold the episode, so the surer episode match beats the tag.
            (1, hi_english, ["Show.E01.en.sdh.srt", "Show.S01E01.en.srt"], "Show.S01E01.en.srt"),
            (1, forced_english, ["Show.E01.en.forced.srt", "Show.S01E01.en.srt"], "Show.S01E01.en.srt"),
            (2, hi_english, ["Show.S01E01-02.en.sdh.srt", "Show.S01E02.en.srt"], "Show.S01E02.en.srt"),
        ):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(episode=episode, language=language, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": episode}, language, ordered_members, {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_does_not_read_audio_or_codec_numbers_as_the_episode(self):
        provider = self.mod.HDBitsProvider()
        english = {"alpha3": "eng", "alpha2": "en"}
        for episode, members in (
            (
                1,
                [
                    "Show.S01E02.1080p.WEB.DDP5.1.H.264-GRP.en.srt",
                    "Show.S01E03.1080p.WEB.DDP5.1.H.264-GRP.en.srt",
                ],
            ),
            (
                1,
                [
                    "Show.S01E01.1080p.WEB.DDP5.1.H.264-GRP.fr.srt",
                    "Show.S01E02.1080p.WEB.DDP5.1.H.264-GRP.en.srt",
                ],
            ),
            (7, ["Show.S01E02.1080p.BluRay.TrueHD.7.1.x264-GRP.srt"]),
            (5, ["Show.S01E02.1080p.BluRay.DTS-HD.MA.5.1.x264-GRP.srt"]),
            (1, ["Show.E02.1080p.WEB.DDP5.1.en.srt"]),
            (1, ["Show.S01E02.1.en.srt"]),
            (5, ["Show.1080p.BluRay.DTS-HD.MA.5.1.x264-GRP.en.srt"]),
            (1, ["Show.1080p.BluRay.DTS-HD.MA.5.1.x264-GRP.en.srt"]),
            (2, ["Show.1080p.WEB.AAC.2.0.en.srt"]),
            (1, ["05.1080p.WEB.DDP5.1.en.srt"]),
        ):
            with self.subTest(episode=episode, members=members):
                result = provider.select_archive_member({"season": 1, "episode": episode}, english, members, {})
                self.assertEqual(result, {"decision": "reject"})

        for episode, members, expected in (
            (
                2,
                [
                    "Show.S01E01.1080p.WEB.DDP5.1.H.264-GRP.en.srt",
                    "Show.S01E02.1080p.WEB.DDP5.1.H.264-GRP.en.srt",
                ],
                "Show.S01E02.1080p.WEB.DDP5.1.H.264-GRP.en.srt",
            ),
            (2, ["Show.S01E01-02.1080p.WEB.DDP5.1.en.srt"], "Show.S01E01-02.1080p.WEB.DDP5.1.en.srt"),
            (5, ["05.1080p.WEB.DDP5.1.en.srt"], "05.1080p.WEB.DDP5.1.en.srt"),
        ):
            with self.subTest(episode=episode, members=members):
                result = provider.select_archive_member({"season": 1, "episode": episode}, english, members, {})
                self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_reads_hi_after_a_spelled_out_language_as_the_variant(self):
        provider = self.mod.HDBitsProvider()
        members = ["Show.S01E01.English.srt", "Show.S01E01.English.HI.srt"]
        for hi, expected in ((True, "Show.S01E01.English.HI.srt"), (False, "Show.S01E01.English.srt")):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(hi=hi, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": 1},
                        {"alpha3": "eng", "alpha2": "en", "hi": hi, "forced": False},
                        ordered_members,
                        {},
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

        members = ["Show.S01E01.English.HI.srt", "Show.S01E01.hi.srt"]
        for ordered_members in (members, list(reversed(members))):
            with self.subTest(members=ordered_members):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1}, {"alpha3": "hin", "alpha2": "hi"}, ordered_members, {}
                )
                self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.hi.srt"})

    def test_archive_selector_counts_a_trailing_cc_tag_as_hearing_impaired(self):
        provider = self.mod.HDBitsProvider()
        for payload, members, hi_member, plain_member in (
            (
                {"season": 1, "episode": 1},
                ["Show.S01E01.en.srt", "Show.S01E01.en.cc.srt"],
                "Show.S01E01.en.cc.srt",
                "Show.S01E01.en.srt",
            ),
            # Inside a release name "CC" marks a Criterion release, not captions.
            (
                {},
                ["Movie.1998.CC.1080p.BluRay.x264-GRP.en.srt", "Movie.1998.CC.1080p.BluRay.x264-GRP.en.sdh.srt"],
                "Movie.1998.CC.1080p.BluRay.x264-GRP.en.sdh.srt",
                "Movie.1998.CC.1080p.BluRay.x264-GRP.en.srt",
            ),
        ):
            for hi, expected in ((True, hi_member), (False, plain_member)):
                for ordered_members in (members, list(reversed(members))):
                    with self.subTest(hi=hi, members=ordered_members):
                        result = provider.select_archive_member(
                            payload,
                            {"alpha3": "eng", "alpha2": "en", "hi": hi, "forced": False},
                            ordered_members,
                            {},
                        )
                        self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_reads_variant_tags_from_the_member_folder(self):
        provider = self.mod.HDBitsProvider()
        members_by_variant = {
            (False, False): "Subs/Show.S01E01.en.srt",
            (True, False): "Subs/SDH/Show.S01E01.en.srt",
            (False, True): "Subs/Forced/Show.S01E01.en.srt",
        }
        for ordered_members in itertools.permutations(members_by_variant.values()):
            for (hi, forced), expected in members_by_variant.items():
                with self.subTest(members=ordered_members, hi=hi, forced=forced):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": 1},
                        {"alpha3": "eng", "alpha2": "en", "hi": hi, "forced": forced},
                        list(ordered_members),
                        {},
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_reads_variant_tags_from_any_ancestor_folder(self):
        provider = self.mod.HDBitsProvider()
        members_by_variant = {
            (False, False): "Subs/Season 1/Show.S01E01.en.srt",
            (True, False): "Subs/SDH/Season 1/Show.S01E01.en.srt",
            (False, True): "Subs/Forced/Season 1/Show.S01E01.en.srt",
        }
        for ordered_members in itertools.permutations(members_by_variant.values()):
            for (hi, forced), expected in members_by_variant.items():
                with self.subTest(members=ordered_members, hi=hi, forced=forced):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": 1},
                        {"alpha3": "eng", "alpha2": "en", "hi": hi, "forced": forced},
                        list(ordered_members),
                        {},
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_rejects_only_explicit_other_languages(self):
        provider = self.mod.HDBitsProvider()
        for payload, members in (
            ({"season": 1, "episode": 1}, ["Show.S01E01.en.srt", "Show.S01E01.gr.srt"]),
            ({}, ["Movie.en.srt", "Movie.gr.srt"]),
        ):
            with self.subTest(payload=payload):
                result = provider.select_archive_member(payload, {"alpha3": "fra"}, members, {})
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_allows_unlabelled_without_pinning_other_language(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1}, {"alpha3": "fra"},
            ["Show.S01E01.en.srt", "Show.S01E01.srt"], {},
        )
        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.srt"})

    def test_archive_selector_does_not_treat_title_words_as_language_tags(self):
        provider = self.mod.HDBitsProvider()
        name = "How.to.Train.Your.Dragon.S01E01.srt"
        result = provider.select_archive_member(
            {"season": 1, "episode": 1}, {"alpha3": "fra"}, [name], {},
        )
        self.assertEqual(result, {"decision": "pin", "member": name})

    def test_archive_selector_distinguishes_hindi_from_english_hi_suffix(self):
        provider = self.mod.HDBitsProvider()
        payload = {"season": 1, "episode": 1}
        for name in ("Show.S01E01.en.HI.srt", "Show.S01E01.en.SDH.HI.srt"):
            with self.subTest(name=name):
                self.assertEqual(provider.select_archive_member(payload, {"alpha3": "hin"}, [name], {}), {"decision": "reject"})
        name = "Show.S01E01.hi.srt"
        self.assertEqual(provider.select_archive_member(payload, {"alpha3": "hin"}, [name], {}), {"decision": "pin", "member": name})

    def test_download_rejects_empty_response(self):
        provider = self.mod.HDBitsProvider()
        provider._http_get = lambda url, timeout=15: b""

        with self.assertRaisesRegex(RuntimeError, "empty"):
            provider.download(
                {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": "movie.en.srt"},
                {"alpha3": "eng", "alpha2": "en"},
                {"username": "user", "passkey": "secret"},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.HDBitsProvider()
        page = b"<!DOCTYPE html>\n<html><body>Invalid passkey</body></html>"
        provider._http_get = lambda url, timeout=15: page

        for filename in ("Chernobyl.S01.en.zip", "Chernobyl.S01.gr.rar", "movie.en.srt"):
            with self.subTest(filename=filename), self.assertRaisesRegex(RuntimeError, "HTML"):
                provider.download(
                    {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": filename},
                    {"alpha3": "eng", "alpha2": "en"},
                    {"username": "user", "passkey": "secret"},
                )

    def test_content_payload_omits_guessed_encoding(self):
        body = "Zażółć gęślą jaźń".encode("cp1250")

        result = self.mod._content_payload(body, "srt")

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_for_host_extraction(self):
        provider = self.mod.HDBitsProvider()
        raw_archive = b"rar bytes"
        provider._http_get = lambda url, timeout=15: raw_archive

        result = provider.download(
            {
                "provider": "hdbits",
                "schema": 1,
                "subtitle_id": 603,
                "filename": "Chernobyl.S01.rar",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), raw_archive)
