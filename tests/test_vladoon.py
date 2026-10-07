import base64
import hashlib
import importlib.util
import io
import json
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "vladoon"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "vladoon_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_bytes(name):
    return (FIXTURE_DIR / name).read_bytes()


def _fixture_json(name):
    return json.loads(_fixture_bytes(name).decode("utf-8"))


def _episode_video(**overrides):
    video = {
        "kind": "episode",
        "series": "Breaking Bad",
        "title": "Pilot",
        "season": 1,
        "episode": 1,
        "year": 2008,
        "series_imdb_id": "tt0903747",
    }
    video.update(overrides)
    return video


def _movie_video(**overrides):
    video = {"kind": "movie", "title": "Dune", "year": 2021, "imdb_id": "tt1160419"}
    video.update(overrides)
    return video


def _zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()


def _breaking_bad_pack_zip():
    members = {}
    for episode in range(1, 8):
        name = f"breaking.bad.s01e{episode:02d}.720p.bluray.x264-reward.srt"
        members[name] = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"
    return _zip_bytes(members)


class VladoonTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()


class SearchQueryTests(VladoonTestCase):
    def test_movie_query_is_the_title(self):
        self.assertEqual(self.mod._search_query(_movie_video()), "Dune")

    def test_movie_query_without_title_is_none(self):
        self.assertIsNone(self.mod._search_query({"kind": "movie", "title": ""}))

    def test_episode_query_is_series_with_token(self):
        self.assertEqual(
            self.mod._search_query(_episode_video()), "Breaking Bad S01E01"
        )

    def test_episode_query_without_season_is_none(self):
        video = _episode_video()
        del video["season"]
        self.assertIsNone(self.mod._search_query(video))

    def test_unknown_kind_has_no_query(self):
        self.assertIsNone(self.mod._search_query({"kind": "other"}))
        self.assertIsNone(self.mod._search_query(None))


class EpisodeTupleTests(VladoonTestCase):
    def test_reads_padded_and_bare_tokens(self):
        self.assertEqual(self.mod._episode_tuple("show.s01e02.720p"), (1, 2))
        self.assertEqual(self.mod._episode_tuple("show.s1e2"), (1, 2))
        self.assertEqual(self.mod._episode_tuple("show.1x02"), (1, 2))

    def test_reads_nothing_without_a_token(self):
        self.assertIsNone(self.mod._episode_tuple("show.720p.bluray"))
        self.assertIsNone(self.mod._episode_tuple(None))

    def test_does_not_read_a_bare_number(self):
        self.assertIsNone(self.mod._episode_tuple("show.e02"))


class RequestedLanguageTests(VladoonTestCase):
    def test_bulgarian_alpha3_is_accepted(self):
        language = self.mod._requested_language([{"alpha3": "bul"}])
        self.assertEqual(
            language,
            {"alpha3": "bul", "alpha2": "bg", "hi": False, "forced": False},
        )

    def test_bulgarian_alpha2_is_accepted(self):
        language = self.mod._requested_language([{"alpha2": "bg"}])
        self.assertEqual(language["alpha3"], "bul")

    def test_other_languages_are_rejected(self):
        self.assertIsNone(self.mod._requested_language([{"alpha3": "eng"}]))
        self.assertIsNone(self.mod._requested_language([]))
        self.assertIsNone(self.mod._requested_language(None))

    def test_variant_requests_are_rejected(self):
        # The site carries no hearing-impaired or forced variants, so those
        # requests are left unsatisfied rather than answered with a plain
        # subtitle mislabelled as the requested variant.
        self.assertIsNone(self.mod._requested_language([{"alpha3": "bul", "hi": True}]))
        self.assertIsNone(self.mod._requested_language([{"alpha3": "bul", "forced": True}]))
        self.assertIsNotNone(self.mod._requested_language([{"alpha3": "bul", "hi": False}]))
        self.assertIsNotNone(self.mod._requested_language([{"alpha3": "bul"}]))

    def test_non_dict_entries_are_skipped(self):
        language = self.mod._requested_language(["bul", {"alpha3": "bul"}])
        self.assertEqual(language["alpha3"], "bul")


class ParseSearchResultsTests(VladoonTestCase):
    def test_episode_fixture_parses_the_season_pack(self):
        items = self.mod._parse_search_results(
            _fixture_bytes("vladoon_search_breaking_bad_s01e01.json")
        )

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], 20554)
        self.assertEqual(item["type"], "tv")
        self.assertEqual(item["season"], 1)
        self.assertIsNone(item["episode"])
        self.assertEqual(item["episode_type"], "season")
        self.assertEqual(item["imdb_id"], "tt0903747")
        self.assertIn("breaking.bad.s01e01.720p.bluray.x264-reward", item["release_names"])

    def test_movie_fixture_parses_every_dune_variant(self):
        items = self.mod._parse_search_results(_fixture_bytes("vladoon_search_dune.json"))

        self.assertGreaterEqual(len(items), 4)
        by_id = {str(item["id"]): item for item in items}
        self.assertEqual(by_id["10838"]["imdb_id"], "tt1160419")
        self.assertEqual(by_id["10837"]["imdb_id"], "tt0087182")
        self.assertEqual(by_id["15800"]["original_title"], "Dune: Part Two")
        # The site's title search also surfaces the Dune: Prophecy series.
        self.assertEqual(by_id["1374"]["type"], "tv")

    def test_missing_results_field_is_an_empty_list(self):
        body = json.dumps({"status": "ok"}).encode("utf-8")
        self.assertEqual(self.mod._parse_search_results(body), [])

    def test_items_without_a_usable_id_are_dropped(self):
        body = json.dumps(
            {
                "results": [
                    {"id": 1},
                    {"title": "no id"},
                    "junk",
                    {"id": "20554"},
                    {"id": "20/../x"},
                    {"id": "abc"},
                    {"id": None},
                ]
            }
        ).encode("utf-8")
        items = self.mod._parse_search_results(body)
        self.assertEqual([item["id"] for item in items], [1, "20554"])

    def test_unparsable_body_raises(self):
        with self.assertRaises(ValueError):
            self.mod._parse_search_results(b"<html>not json</html>")

    def test_non_list_results_raises(self):
        body = json.dumps({"results": {"id": 1}}).encode("utf-8")
        with self.assertRaises(ValueError):
            self.mod._parse_search_results(body)

    def test_non_object_body_raises(self):
        with self.assertRaises(ValueError):
            self.mod._parse_search_results(json.dumps([1, 2]).encode("utf-8"))


class MatchesMovieTests(VladoonTestCase):
    def setUp(self):
        super().setUp()
        self.items = {
            str(item["id"]): item
            for item in self.mod._parse_search_results(
                _fixture_bytes("vladoon_search_dune.json")
            )
        }

    def test_same_imdb_id_matches(self):
        self.assertTrue(self.mod._matches_movie(self.items["10838"], _movie_video()))

    def test_different_imdb_id_is_rejected(self):
        # The Dune search surfaces the 1984 production and the sequel; the
        # IMDb id is the discriminator.
        self.assertFalse(self.mod._matches_movie(self.items["10837"], _movie_video()))
        self.assertFalse(self.mod._matches_movie(self.items["15800"], _movie_video()))

    def test_item_type_must_be_movie(self):
        item = dict(self.items["10838"], type="tv")
        self.assertFalse(self.mod._matches_movie(item, _movie_video()))

    def test_video_without_imdb_id_keeps_every_movie_item(self):
        video = _movie_video()
        del video["imdb_id"]
        self.assertTrue(self.mod._matches_movie(self.items["10837"], video))

    def test_episode_video_skips_the_movie_check(self):
        self.assertTrue(self.mod._matches_movie(self.items["10838"], _episode_video()))


class MatchesEpisodeTests(VladoonTestCase):
    def setUp(self):
        super().setUp()
        self.pack = self.mod._parse_search_results(
            _fixture_bytes("vladoon_search_breaking_bad_s01e01.json")
        )[0]

    def test_season_pack_matches_the_requested_episode(self):
        self.assertTrue(self.mod._matches_episode(self.pack, _episode_video()))

    def test_pack_of_another_season_is_rejected(self):
        self.assertFalse(
            self.mod._matches_episode(self.pack, _episode_video(season=2))
        )

    def test_release_name_episode_match(self):
        item = {
            "id": 1,
            "type": "tv",
            "season": None,
            "episode": None,
            "release_names": ["breaking.bad.s01e01.720p.bluray.x264-reward"],
        }
        self.assertTrue(self.mod._matches_episode(item, _episode_video()))
        self.assertFalse(
            self.mod._matches_episode(item, _episode_video(season=1, episode=2))
        )

    def test_episode_fields_match(self):
        item = {"id": 1, "type": "tv", "season": 1, "episode": 1}
        self.assertTrue(self.mod._matches_episode(item, _episode_video()))

    def test_item_type_must_be_tv(self):
        item = dict(self.pack, type="movie")
        self.assertFalse(self.mod._matches_episode(item, _episode_video()))

    def test_series_imdb_mismatch_is_rejected(self):
        self.assertFalse(
            self.mod._matches_episode(self.pack, _episode_video(series_imdb_id="tt9999999"))
        )

    def test_movie_video_skips_the_episode_check(self):
        self.assertTrue(self.mod._matches_episode(self.pack, _movie_video()))


class SubtitleMemberTests(VladoonTestCase):
    def test_plain_members_are_subtitles(self):
        self.assertTrue(self.mod._is_subtitle_member("breaking.bad.s01e01.srt"))
        self.assertTrue(self.mod._is_subtitle_member("pack/Breaking.Bad.S01E01.SRT"))
        self.assertTrue(self.mod._is_subtitle_member("pack\\Breaking.Bad.S01E01.srt"))

    def test_directories_and_dot_members_are_not_subtitles(self):
        self.assertFalse(self.mod._is_subtitle_member("pack/"))
        self.assertFalse(self.mod._is_subtitle_member(".DS_Store"))
        self.assertFalse(self.mod._is_subtitle_member("__MACOSX/._breaking.bad.s01e01.srt"))
        self.assertFalse(self.mod._is_subtitle_member("readme.nfo"))
        self.assertFalse(self.mod._is_subtitle_member(""))

    def test_member_basename_handles_both_separators(self):
        self.assertEqual(
            self.mod._member_basename("pack/breaking.bad.s01e01.srt"),
            "breaking.bad.s01e01.srt",
        )
        self.assertEqual(
            self.mod._member_basename("pack\\breaking.bad.s01e01.srt"),
            "breaking.bad.s01e01.srt",
        )
        self.assertEqual(self.mod._member_basename("plain.srt"), "plain.srt")

    def test_item_id_validation(self):
        for value in (20554, "20554", " 20554 ", "007"):
            self.assertTrue(self.mod._valid_item_id(value))
        for value in (None, "", "abc", "20/../x", "20554/../../x", -1, 0, "0", "000", " 0 ", True, 2.5):
            self.assertFalse(self.mod._valid_item_id(value))

    def test_forced_member_detection(self):
        self.assertTrue(self.mod._is_forced_member("Show.S01E01.forced.srt"))
        self.assertTrue(self.mod._is_forced_member("pack/show.s01e01.forced.srt"))
        self.assertTrue(self.mod._is_forced_member("pack\\show.s01e01.FORCED.srt"))
        # Faithful quirk: any final segment ending in "forced" counts.
        self.assertTrue(self.mod._is_forced_member("show.s01e01.nonforced.srt"))
        # Faithful boundary: only the final segment counts, so a forced tag
        # anywhere earlier in the name is not detected.
        self.assertFalse(self.mod._is_forced_member("show.s01e01.forced.1080p.srt"))
        self.assertFalse(self.mod._is_forced_member("Show.S01E01.srt"))
        self.assertFalse(self.mod._is_forced_member("show.forcedly.srt"))


class TitlesMatchTests(VladoonTestCase):
    def test_same_title_matches(self):
        self.assertTrue(self.mod._titles_match("Dune", {"original_title": "Dune"}))
        self.assertTrue(self.mod._titles_match("Dune", {"title": "Dune"}))

    def test_punctuation_is_normalized_away(self):
        self.assertTrue(
            self.mod._titles_match("CSI Vegas", {"original_title": "CSI: Vegas"})
        )

    def test_sequel_title_does_not_match(self):
        self.assertFalse(
            self.mod._titles_match("Dune", {"original_title": "Dune: Part Two"})
        )
        self.assertFalse(
            self.mod._titles_match("Dune: Part One", {"original_title": "Dune"})
        )

    def test_different_title_does_not_match(self):
        self.assertFalse(self.mod._titles_match("Arrival", {"original_title": "Dune"}))
        self.assertFalse(self.mod._titles_match(None, {"original_title": "Dune"}))
        self.assertFalse(self.mod._titles_match("Dune", {}))


class DeriveMatchesTests(VladoonTestCase):
    def setUp(self):
        super().setUp()
        self.pack = self.mod._parse_search_results(
            _fixture_bytes("vladoon_search_breaking_bad_s01e01.json")
        )[0]
        self.dune_items = {
            str(item["id"]): item
            for item in self.mod._parse_search_results(
                _fixture_bytes("vladoon_search_dune.json")
            )
        }

    def test_season_pack_surfaces_the_requested_episode(self):
        video = _episode_video(
            source="Blu-ray",
            resolution="720p",
            video_codec="H.264",
            release_group="reward",
        )
        matches = self.mod._derive_matches(video, self.pack)

        for key in ("series", "season", "episode", "imdb_id", "source", "resolution", "video_codec", "release_group"):
            self.assertIn(key, matches)

    def test_pack_season_match_survives_an_episode_outside_the_pack(self):
        # The pack covers episodes 1 to 7, so S01E08 still matches season
        # without matching any single episode.
        video = _episode_video(episode=8)
        matches = self.mod._derive_matches(video, self.pack)
        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("imdb_id", matches)
        self.assertNotIn("episode", matches)

    def test_episode_year_match_survives_without_imdb(self):
        # The episode branch credits the year from the release names even
        # when neither side carries an IMDb id to match on.
        item = {
            "id": 1371,
            "type": "tv",
            "season": 1,
            "episode": None,
            "episode_type": "season",
            "original_title": "Dune: Prophecy",
            "release_names": ["Dune.Prophecy.2024.S01E06.720p.WEB-DL.DDP5.1.x264-iYi"],
        }
        video = {
            "kind": "episode",
            "series": "Dune: Prophecy",
            "title": "Sisterhood Above All",
            "season": 1,
            "episode": 6,
            "year": 2024,
        }
        matches = self.mod._derive_matches(video, item)
        self.assertIn("year", matches)
        self.assertIn("series", matches)
        self.assertNotIn("imdb_id", matches)

    def test_movie_item_surfaces_title_year_and_release_attributes(self):
        video = _movie_video(
            source="Blu-ray",
            resolution="1080p",
            video_codec="H.264",
            release_group="BGG",
        )
        matches = self.mod._derive_matches(video, self.dune_items["10838"])

        for key in ("title", "year", "imdb_id", "source", "resolution", "video_codec", "release_group"):
            self.assertIn(key, matches)

    def test_movie_of_another_year_has_no_year_match(self):
        matches = self.mod._derive_matches(_movie_video(), self.dune_items["10837"])
        self.assertIn("title", matches)
        self.assertNotIn("year", matches)
        self.assertNotIn("imdb_id", matches)

    def test_sequel_title_does_not_match(self):
        matches = self.mod._derive_matches(_movie_video(), self.dune_items["15800"])
        self.assertNotIn("title", matches)
        self.assertNotIn("imdb_id", matches)

    def test_list_valued_metadata_is_coerced(self):
        video = _episode_video(audio_codec=["DTS-HD", "MA"], source=["Blu-ray"])
        item = {
            "id": 1,
            "type": "tv",
            "season": 1,
            "episode": 1,
            "original_title": "Breaking Bad",
            "imdb_id": "tt0903747",
            "release_names": ["breaking.bad.s01e01.720p.bluray.dts-hd.ma.x264-reward"],
        }
        matches = self.mod._derive_matches(video, item)
        self.assertIn("source", matches)
        self.assertIn("audio_codec", matches)

    def test_release_group_does_not_manufacture_a_source_match(self):
        # The trailing release group (here ``BD``) is not a source claim:
        # this release is a WEB-DL, not a Blu-ray.
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Dune",
            "imdb_id": "tt1160419",
            "release_names": ["Dune.2021.1080p.WEB-DL.x264-BD"],
        }
        matches = self.mod._derive_matches(_movie_video(source="Blu-ray"), item)
        self.assertNotIn("source", matches)
        self.assertIn("source", self.mod._derive_matches(_movie_video(source="Web"), item))

        # A real mid-name source token still matches.
        item["release_names"] = ["Dune.2021.1080p.BluRay.x264-BD"]
        self.assertIn("source", self.mod._derive_matches(_movie_video(source="Blu-ray"), item))

    def test_title_words_do_not_manufacture_a_source_match(self):
        # A title word is not a source claim: this is a Blu-ray release of
        # a production titled "The Web", not a web release.
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "The Web",
            "imdb_id": "tt0000001",
            "release_names": ["The.Web.2024.1080p.BluRay.x264-GROUP"],
        }
        video = _movie_video(title="The Web", year=2024, source="Web")
        matches = self.mod._derive_matches(video, item)
        self.assertNotIn("source", matches)
        self.assertIn(
            "source", self.mod._derive_matches(_movie_video(source="Blu-ray"), item)
        )

    def test_dd_plus_is_eac3_not_ac3(self):
        # ``DD+5.1`` would tokenize as plain ``dd`` (AC3) although it means
        # Dolby Digital Plus, so it is rewritten to ``ddp`` before matching.
        # The rewrite must survive underscore separators too.
        for release in ("Dune.2021.DD+5.1.1080p.x264-GROUP", "Dune_2021_DD+5.1_1080p_x264-GROUP"):
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Dune",
                "release_names": [release],
            }
            self.assertIn(
                "audio_codec", self.mod._derive_matches(_movie_video(audio_codec="EAC3"), item), release
            )
            self.assertNotIn(
                "audio_codec", self.mod._derive_matches(_movie_video(audio_codec="AC3"), item), release
            )

    def test_release_attributes_match_within_one_release_only(self):
        # Two releases ending -FOO and -BAR must not combine into a match
        # for a video release group named FOO-BAR: upstream evaluates each
        # release independently and unions the resulting keys.
        item = {
            "id": 1,
            "type": "tv",
            "season": 1,
            "episode": 1,
            "original_title": "Breaking Bad",
            "imdb_id": "tt0903747",
            "release_names": [
                "breaking.bad.s01e01.720p.bluray.x264-FOO",
                "breaking.bad.s01e01.1080p.webrip.h264-BAR",
            ],
        }
        video = _episode_video(release_group="FOO-BAR", audio_codec="DTS-HD")
        matches = self.mod._derive_matches(video, item)
        self.assertNotIn("release_group", matches)
        self.assertNotIn("audio_codec", matches)

        item["release_names"] = ["breaking.bad.s01e01.720p.bluray.dts-hd.x264-FOO-BAR"]
        matches = self.mod._derive_matches(video, item)
        self.assertIn("release_group", matches)
        self.assertIn("audio_codec", matches)

    def test_streaming_service_aliases_match(self):
        item = {
            "id": 10838,
            "type": "movie",
            "original_title": "Dune",
            "imdb_id": "tt1160419",
            "release_names": ["Dune.2021.1080p.HMAX.WEB-DL.DDP5.1.Atmos.x264-CM"],
        }
        matches = self.mod._derive_matches(
            _movie_video(streaming_service="HBO Max"), item
        )
        self.assertIn("streaming_service", matches)

    def test_atmos_is_not_an_eac3_synonym(self):
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Dune",
            "release_names": ["Dune.2021.2160p.TrueHD.7.1.Atmos.H265-GROUP"],
        }
        matches = self.mod._derive_matches(_movie_video(audio_codec="EAC3"), item)
        self.assertNotIn("audio_codec", matches)
        self.assertIn("audio_codec", self.mod._derive_matches(_movie_video(audio_codec="TrueHD"), item))

    def test_audio_codec_channel_suffixes(self):
        # Release names run the channel count into the codec tag: DD5.1 is
        # AC3 and DDP5.1 is EAC3, and neither claims the other.
        for release, matched, unmatched in (
            ("Movie.DD5.1.1080p.x264-GROUP", "AC3", "EAC3"),
            ("Movie.DD.5.1.1080p.x264-GROUP", "AC3", "EAC3"),
            ("Movie.DDP5.1.1080p.x264-GROUP", "EAC3", "AC3"),
            ("Movie.EAC3.1080p.x264-GROUP", "EAC3", "AC3"),
            ("Movie.AC3.1080p.x264-GROUP", "AC3", "EAC3"),
        ):
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Dune",
                "release_names": [release],
            }
            self.assertIn(
                "audio_codec", self.mod._derive_matches(_movie_video(audio_codec=matched), item), release
            )
            self.assertNotIn(
                "audio_codec", self.mod._derive_matches(_movie_video(audio_codec=unmatched), item), release
            )

    def test_dolby_digital_plus_is_not_ac3(self):
        for release, matched, unmatched in (
            ("Dune.1984.Dolby.Digital.1080p.x264-GROUP", "AC3", "EAC3"),
            ("Dune.2021.Dolby.Digital.Plus.Atmos.1080p.x264-GROUP", "EAC3", "AC3"),
        ):
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Dune",
                "release_names": [release],
            }
            self.assertIn(
                "audio_codec", self.mod._derive_matches(_movie_video(audio_codec=matched), item), release
            )
            self.assertNotIn(
                "audio_codec", self.mod._derive_matches(_movie_video(audio_codec=unmatched), item), release
            )


class ComputeScoreTests(VladoonTestCase):
    def test_movie_imdb_match_scores_full(self):
        self.assertEqual(self.mod._compute_score(_movie_video(), ["title", "imdb_id"]), 100)

    def test_movie_title_only_scores_90(self):
        self.assertEqual(self.mod._compute_score(_movie_video(), ["title"]), 90)

    def test_unrelated_movie_scores_60(self):
        self.assertEqual(self.mod._compute_score(_movie_video(), []), 60)

    def test_episode_with_episode_scores_95(self):
        self.assertEqual(
            self.mod._compute_score(_episode_video(), ["series", "season", "episode"]),
            95,
        )

    def test_episode_season_only_scores_90(self):
        self.assertEqual(
            self.mod._compute_score(_episode_video(), ["series", "season"]), 90
        )

    def test_episode_series_only_scores_85(self):
        self.assertEqual(self.mod._compute_score(_episode_video(), ["series"]), 85)


class SearchTests(VladoonTestCase):
    def _provider_with_responses(self, responses):
        provider = self.mod.VladoonProvider()

        def stub(url, timeout=None, max_bytes=None):
            del timeout, max_bytes
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        return provider

    def test_episode_search_returns_the_pack(self):
        provider = self._provider_with_responses(
            {
                "https://vladoon.com/subs/search-subtitles?q=Breaking+Bad+S01E01": _fixture_bytes(
                    "vladoon_search_breaking_bad_s01e01.json"
                )
            }
        )

        results = provider.search(
            _episode_video(), [{"alpha3": "bul", "alpha2": "bg", "hi": False, "forced": False}], {}
        )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["provider"], "vladoon")
        self.assertEqual(result["id"], "vladoon-20554")
        self.assertEqual(result["language"], {"alpha3": "bul", "alpha2": "bg", "hi": False, "forced": False})
        self.assertEqual(result["page_link"], "https://vladoon.com/subs/download/20554")
        self.assertEqual(
            result["filename"], "breaking.bad.s01e01.720p.bluray.x264-reward.srt"
        )
        self.assertIn("breaking.bad.s01e01.720p.bluray.x264-reward", result["release_info"])
        self.assertEqual(result["score_out_of"], 100)
        self.assertFalse(result["hash_verifiable"])
        # The payload carries the requested episode so a season pack
        # download still picks the member the video asked for.
        self.assertEqual(
            result["provider_payload"],
            {
                "provider": "vladoon",
                "schema": 1,
                "item_id": "20554",
                "kind": "episode",
                "season": 1,
                "episode": 1,
            },
        )
        self.assertEqual(result["display"]["uploader"], "Vlad00n")

    def test_movie_search_filters_by_imdb_id(self):
        provider = self._provider_with_responses(
            {
                "https://vladoon.com/subs/search-subtitles?q=Dune": _fixture_bytes(
                    "vladoon_search_dune.json"
                )
            }
        )

        results = provider.search(
            _movie_video(), [{"alpha3": "bul"}], {}
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "vladoon-10838")
        self.assertEqual(results[0]["provider_payload"]["kind"], "movie")
        self.assertNotIn("season", results[0]["provider_payload"])
        self.assertNotIn("episode", results[0]["provider_payload"])

    def test_movie_video_without_imdb_keeps_every_movie_item(self):
        provider = self._provider_with_responses(
            {
                "https://vladoon.com/subs/search-subtitles?q=Dune": _fixture_bytes(
                    "vladoon_search_dune.json"
                )
            }
        )
        video = _movie_video()
        del video["imdb_id"]

        results = provider.search(video, [{"alpha3": "bul"}], {})

        self.assertGreater(len(results), 1)
        ids = {result["id"] for result in results}
        self.assertIn("vladoon-10838", ids)
        self.assertIn("vladoon-10837", ids)

    def test_other_language_returns_no_results_without_a_request(self):
        provider = self._provider_with_responses({})
        results = provider.search(_movie_video(), [{"alpha3": "eng"}], {})
        self.assertEqual(results, [])

    def test_video_without_a_query_returns_no_results(self):
        provider = self._provider_with_responses({})
        results = provider.search({"kind": "movie"}, [{"alpha3": "bul"}], {})
        self.assertEqual(results, [])

    def test_search_failure_propagates(self):
        provider = self._provider_with_responses({})
        provider._http_get = lambda url, timeout=None: (_ for _ in ()).throw(
            urllib.error.HTTPError(url, 500, "server error", None, None)
        )

        with self.assertRaises(urllib.error.HTTPError):
            provider.search(_movie_video(), [{"alpha3": "bul"}], {})

    def test_episode_search_downloads_the_requested_member(self):
        provider = self._provider_with_responses(
            {
                "https://vladoon.com/subs/search-subtitles?q=Breaking+Bad+S01E05": _fixture_bytes(
                    "vladoon_search_breaking_bad_s01e01.json"
                ),
                "https://vladoon.com/subs/download/20554": _breaking_bad_pack_zip(),
            }
        )

        results = provider.search(_episode_video(episode=5), [{"alpha3": "bul"}], {})
        self.assertEqual(len(results), 1)

        archive = provider.download(results[0]["provider_payload"], {"alpha3": "bul"}, {})
        self.assertEqual(
            archive["member"], "breaking.bad.s01e05.720p.bluray.x264-reward.srt"
        )

    def test_episode_outside_the_pack_defers_the_member_pick(self):
        provider = self._provider_with_responses(
            {
                "https://vladoon.com/subs/search-subtitles?q=Breaking+Bad+S01E08": _fixture_bytes(
                    "vladoon_search_breaking_bad_s01e01.json"
                ),
                "https://vladoon.com/subs/download/20554": _breaking_bad_pack_zip(),
            }
        )

        results = provider.search(_episode_video(episode=8), [{"alpha3": "bul"}], {})
        self.assertEqual(len(results), 1)

        archive = provider.download(results[0]["provider_payload"], {"alpha3": "bul"}, {})
        self.assertNotIn("member", archive)
        self.assertEqual(archive["season"], 1)
        self.assertEqual(archive["episode"], 8)


class DownloadTests(VladoonTestCase):
    def _provider_with_responses(self, responses):
        provider = self.mod.VladoonProvider()

        def stub(url, timeout=None, max_bytes=None):
            del timeout, max_bytes
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        return provider

    def _episode_payload(self, season=1, episode=1):
        return {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "20554",
            "kind": "episode",
            "season": season,
            "episode": episode,
        }

    def test_episode_download_pins_the_requested_member(self):
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": _breaking_bad_pack_zip()}
        )

        archive = provider.download(self._episode_payload(episode=1), {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "breaking.bad.s01e01.720p.bluray.x264-reward.srt"
        )
        self.assertNotIn("episode", archive)

    def test_episode_download_pins_each_requested_episode(self):
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": _breaking_bad_pack_zip()}
        )

        for episode in (2, 5, 7):
            archive = provider.download(self._episode_payload(episode=episode), {"alpha3": "bul"}, {})
            self.assertEqual(
                archive["member"],
                f"breaking.bad.s01e{episode:02d}.720p.bluray.x264-reward.srt",
            )

    def test_episode_download_defers_when_no_member_names_the_episode(self):
        body = _zip_bytes(
            {
                "episode one.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n",
                "episode two.srt": b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        archive = provider.download(self._episode_payload(episode=2), {"alpha3": "bul"}, {})

        self.assertNotIn("member", archive)
        self.assertEqual(archive["season"], 1)
        self.assertEqual(archive["episode"], 2)

    def test_movie_download_pins_the_first_member(self):
        # Upstream takes the first subtitle in the archive, in archive order.
        body = _zip_bytes(
            {
                "Dune.2021.1080p.HMAX.WEB-DL.DDP5.1.Atmos.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Dune.2021.1080p.BluRay.H264.AC3-BGG.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
                "Dune.2021.HDRip.x264.AAC-HUD.srt": b"1\n00:00:01,000 --> 00:00:02,000\nC\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {"provider": "vladoon", "schema": 1, "item_id": "10838", "kind": "movie"}

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Dune.2021.1080p.HMAX.WEB-DL.DDP5.1.Atmos.x264-CM.srt")

    def test_forced_members_are_skipped_for_normal_episodes(self):
        # A pinned member is read by the host as-is, so the provider must
        # skip forced-tagged members itself, exactly like the built-in.
        body = _zip_bytes(
            {
                "breaking.bad.s01e01.forced.srt": b"forced\r\n",
                "breaking.bad.s01e01.720p.bluray.x264-reward.srt": b"regular\r\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        archive = provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "breaking.bad.s01e01.720p.bluray.x264-reward.srt")

    def test_nonforced_suffix_members_are_skipped_like_forced(self):
        # The built-in rule matches any final segment ending in "forced",
        # quirk included, so "nonforced" as the final segment is skipped too.
        body = _zip_bytes(
            {
                "breaking.bad.s01e01.nonforced.srt": b"wrong\r\n",
                "breaking.bad.s01e01.720p.bluray.x264-reward.srt": b"right\r\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        archive = provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "breaking.bad.s01e01.720p.bluray.x264-reward.srt")

    def test_forced_only_episode_archive_is_rejected(self):
        # The host's single-member shortcut would serve a forced-only
        # archive to a normal episode request, so the worker rejects it
        # before handing the archive over.
        body = _zip_bytes({"breaking.bad.s01e01.forced.srt": b"forced\r\n"})
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        with self.assertRaises(ValueError):
            provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

    def test_archive_payload_hashes_the_body(self):
        body = _breaking_bad_pack_zip()
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        archive = provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

        self.assertEqual(archive["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(
            base64.b64decode(archive["archive_b64"].encode("ascii"), validate=True), body
        )

    def test_directory_tokens_do_not_select_the_episode(self):
        # A pack directory like ``S01E01-E07`` is a range marker for the
        # whole archive; only the member's own basename names its episode.
        body = _zip_bytes(
            {
                "Breaking.Bad.S01E01-E07/Breaking.Bad.S01E02.720p.srt": b"wrong\r\n",
                "Breaking.Bad.S01E01-E07/Breaking.Bad.S01E01.720p.srt": b"right\r\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        archive = provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Breaking.Bad.S01E01-E07/Breaking.Bad.S01E01.720p.srt"
        )

    def test_resource_forks_and_directories_are_ignored(self):
        body = _zip_bytes(
            {
                "__MACOSX/._breaking.bad.s01e01.srt": b"junk",
                ".DS_Store": b"junk",
                "breaking.bad.s01e01.720p.bluray.x264-reward.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHi\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        archive = provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "breaking.bad.s01e01.720p.bluray.x264-reward.srt")

    def test_movie_download_skips_resource_fork_members(self):
        body = _zip_bytes(
            {
                "._Dune.2021.srt": b"junk",
                "Dune.2021.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHi\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {"provider": "vladoon", "schema": 1, "item_id": "10838", "kind": "movie"}

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Dune.2021.srt")

    def test_resource_fork_only_archive_raises(self):
        body = _zip_bytes({"._Dune.2021.srt": b"junk", ".DS_Store": b"junk"})
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {"provider": "vladoon", "schema": 1, "item_id": "10838", "kind": "movie"}

        with self.assertRaises(ValueError):
            provider.download(payload, {"alpha3": "bul"}, {})

    def test_payload_without_a_usable_item_id_is_rejected(self):
        provider = self._provider_with_responses({})
        for item_id in ("abc", "20554/../../x", ""):
            payload = {
                "provider": "vladoon",
                "schema": 1,
                "item_id": item_id,
                "kind": "movie",
            }
            with self.assertRaises(ValueError):
                provider.download(payload, {"alpha3": "bul"}, {})

    def test_non_archive_body_raises(self):
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": b"<html>error page</html>"}
        )

        with self.assertRaises(ValueError):
            provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

    def test_archive_without_subtitle_members_raises(self):
        body = _zip_bytes({"readme.nfo": b"no subtitles here"})
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": body}
        )

        with self.assertRaises(ValueError):
            provider.download(self._episode_payload(), {"alpha3": "bul"}, {})

    def test_archive_with_too_many_members_is_rejected(self):
        # Mirrors the host's member-count guard: an archive above it would
        # be rejected host-side anyway.
        files = {
            f"pack/ep{i:05d}.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
            for i in range(self.mod.MAX_ARCHIVE_MEMBERS + 1)
        }
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/20554": _zip_bytes(files)}
        )
        payload = {"provider": "vladoon", "schema": 1, "item_id": "20554", "kind": "movie"}

        with self.assertRaisesRegex(ValueError, "too many members"):
            provider.download(payload, {"alpha3": "bul"}, {})

    def test_payload_from_another_provider_is_rejected(self):
        provider = self._provider_with_responses({})
        payload = {"provider": "other", "schema": 1, "item_id": "20554"}

        with self.assertRaises(ValueError):
            provider.download(payload, {"alpha3": "bul"}, {})

    def test_payload_without_item_id_is_rejected(self):
        provider = self._provider_with_responses({})
        with self.assertRaises(ValueError):
            provider.download({"provider": "vladoon", "kind": "movie"}, {"alpha3": "bul"}, {})

    def test_download_failure_propagates(self):
        provider = self._provider_with_responses({})
        provider._http_get = lambda url, timeout=None, max_bytes=None: (
            _ for _ in ()
        ).throw(urllib.error.HTTPError(url, 503, "unavailable", None, None))

        with self.assertRaises(urllib.error.HTTPError):
            provider.download(self._episode_payload(), {"alpha3": "bul"}, {})


class ResponseBoundsTests(VladoonTestCase):
    class _Response:
        def __init__(self, body):
            self._body = body

        def read(self, limit=-1):
            if limit is None or limit < 0:
                return self._body
            return self._body[:limit]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _provider_with_body(self, body):
        provider = self.mod.VladoonProvider()
        patcher = patch.object(
            self.mod.urllib.request, "urlopen", return_value=self._Response(body)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return provider

    def test_search_response_is_bounded(self):
        # Valid JSON with whitespace padding above the cap: without the
        # bound this parses cleanly, so the size error is the only failure.
        body = b'{"results": []} ' + b" " * (self.mod.MAX_SEARCH_BYTES - 16)
        self.assertEqual(len(body), self.mod.MAX_SEARCH_BYTES)
        oversized = b'{"results": []} ' + b" " * (self.mod.MAX_SEARCH_BYTES - 15)
        self.assertEqual(len(oversized), self.mod.MAX_SEARCH_BYTES + 1)

        provider = self._provider_with_body(oversized)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            provider.search(_movie_video(), [{"alpha3": "bul"}], {})

        provider = self._provider_with_body(body)
        self.assertEqual(provider.search(_movie_video(), [{"alpha3": "bul"}], {}), [])

    def test_download_response_is_bounded_at_the_host_archive_cap(self):
        # A real zip with leading padding: zipfile seeks the central
        # directory from the end, so both bodies are valid archives and the
        # size error is the only difference.
        def padded_zip(total):
            zip_data = _zip_bytes(
                {"pack.srt": b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"}
            )
            return b"\0" * (total - len(zip_data)) + zip_data

        payload = {"provider": "vladoon", "schema": 1, "item_id": "20554", "kind": "movie"}

        provider = self._provider_with_body(padded_zip(self.mod.MAX_DOWNLOAD_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            provider.download(payload, {"alpha3": "bul"}, {})

        provider = self._provider_with_body(padded_zip(self.mod.MAX_DOWNLOAD_BYTES))
        archive = provider.download(payload, {"alpha3": "bul"}, {})
        self.assertEqual(archive["member"], "pack.srt")


class SleepTests(VladoonTestCase):
    def test_no_delay_by_default(self):
        with patch.object(self.mod.time, "sleep") as sleep:
            self.mod._sleep({})
            self.mod._sleep(None)
            sleep.assert_not_called()

    def test_delay_is_applied_in_seconds(self):
        with patch.object(self.mod.time, "sleep") as sleep:
            self.mod._sleep({"request_delay_ms": 250})
            sleep.assert_called_once_with(0.25)

    def test_delay_is_capped_at_five_seconds(self):
        with patch.object(self.mod.time, "sleep") as sleep:
            self.mod._sleep({"request_delay_ms": 60000})
            sleep.assert_called_once_with(5.0)


if __name__ == "__main__":
    unittest.main()
