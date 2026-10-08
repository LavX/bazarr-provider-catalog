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

        for key in ("series", "season", "episode", "series_imdb_id", "source", "resolution", "video_codec", "release_group"):
            self.assertIn(key, matches)

    def test_pack_season_match_survives_an_episode_outside_the_pack(self):
        # The pack covers episodes 1 to 7, so S01E08 still matches season
        # without matching any single episode.
        video = _episode_video(episode=8)
        matches = self.mod._derive_matches(video, self.pack)
        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("series_imdb_id", matches)
        # A series-level id equality must not claim episode-level identity.
        self.assertNotIn("imdb_id", matches)
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

    def test_a_source_token_repeated_after_the_title_prefix_claims_its_source(self):
        # Only the title's own token occurrence is excluded, so the web
        # token in the source position of this WEB-DL release of a
        # production titled "The Web" is a real source claim.
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "The Web",
            "imdb_id": "tt0000001",
            "release_names": ["The.Web.2024.1080p.WEB-DL.x264-CM"],
        }
        video = _movie_video(title="The Web", year=2024, source="Web")
        matches = self.mod._derive_matches(video, item)
        self.assertIn("source", matches)

    def test_a_source_token_adjacent_to_the_title_claims_its_source(self):
        # The title occurrence is removed as a sequence, so the web token
        # of the source claim immediately following the title survives.
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "The Web",
            "imdb_id": "tt0000001",
            "release_names": ["The.Web.WEB-DL.x264-CM"],
        }
        video = _movie_video(title="The Web", year=2024, source="Web")
        matches = self.mod._derive_matches(video, item)
        self.assertIn("source", matches)

    def test_a_title_behind_a_leading_annotation_is_not_a_source_claim(self):
        # A release can carry a leading annotation before the title: the
        # title occurrence behind it is still excluded, so this Blu-ray
        # release of a production titled "The Web" must not claim the Web
        # source.
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "The Web",
            "imdb_id": "tt0000001",
            "release_names": ["[BG].The.Web.2024.1080p.BluRay.x264-GROUP"],
        }
        video = _movie_video(title="The Web", year=2024, source="Web")
        matches = self.mod._derive_matches(video, item)
        self.assertNotIn("source", matches)
        self.assertIn(
            "source", self.mod._derive_matches(_movie_video(source="Blu-ray"), item)
        )

    def test_a_release_group_fires_only_from_the_group_suffix(self):
        # The release group is the trailing hyphen-separated segment(s),
        # so a requested group token must not fire from a source position
        # mid-name, while a hyphenated group still matches its full suffix.
        video = _movie_video(release_group="WEB")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.1080p.WEB-DL.x264-OTHER"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.720p.BluRay.x264-WEB"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))
        video = _movie_video(release_group="FOO-BAR")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.1080p.BluRay.x264-FOO-BAR"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_release_group_span_never_reads_into_the_release_body(self):
        # A requested multi-segment group the trailing segments cannot
        # fill is rejected; it must not fire from the title or the body.
        video = _movie_video(release_group="FOO-BAR")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Foo Bar",
            "release_names": ["Foo.Bar.2024.1080p.BluRay-OTHER"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))
        # A name without a group segment carries no group at all, so a
        # source token in its tail is not the requested group.
        video = _movie_video(release_group="DL")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.WEB-DL.x264"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))
        # A segment that continues a compound source or audio tag is
        # release body, not group content.
        video = _movie_video(release_group="DL")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.WEB-DL.srt"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))
        video = _movie_video(release_group="HD")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.DTS-HD.srt"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))
        # Every adjacent pair inside a multi-chunk audio tag is a
        # compound boundary, so a name ending in the tag carries no
        # group and a requested group named after its last chunk never
        # fires.
        video = _movie_video(release_group="Plus")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.Dolby-Digital-Plus.srt"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))

    def test_a_channel_suffixed_audio_segment_is_not_group_content(self):
        # Release names run the channel count into the audio tag
        # (AAC2.0, DDP5.1, DTS-X.7.1), so a hyphen segment carrying one
        # is body content: the group behind it still fires.
        for release in (
            "Movie.2024.WEB-DL-AAC2.0-CM.srt",
            "Movie.2024.WEB-DL-DDP5.1-CM.srt",
            "Movie.2024.2160p.UHD.BluRay.REMUX.HEVC.DTS-X.7.1-FGT.srt",
        ):
            video = _movie_video(
                release_group="CM" if "-CM" in release else "FGT"
            )
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_long_form_source_segment_is_not_group_content(self):
        # A hyphen segment can carry the source tag itself (a name run
        # on hyphens instead of dots), and its long form is body
        # content: both the source and the group behind it fire.
        video = _movie_video(source="Blu-ray", release_group="CM")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.1080p-BluRay-CM.srt"],
        }
        matches = self.mod._derive_matches(video, item)
        self.assertIn("source", matches)
        self.assertIn("release_group", matches)
        video = _movie_video(source="Web", release_group="GRP")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie-2024-1080p-WEBRip-GRP.srt"],
        }
        matches = self.mod._derive_matches(video, item)
        self.assertIn("source", matches)
        self.assertIn("release_group", matches)

    def test_a_group_named_after_a_title_word_still_fires(self):
        # Only the title's own hyphen-split continuation stops the
        # span, not any token overlap, so a group genuinely named after
        # a title word fires, and its segment is dropped from source
        # matching instead of being read as a source claim.
        video = _movie_video(release_group="WEB")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "The Web",
            "release_names": ["The.Web.2024.1080p.BluRay.x264-WEB.srt"],
        }
        matches = self.mod._derive_matches(video, item)
        self.assertIn("release_group", matches)
        video = _movie_video(source="Web")
        self.assertNotIn("source", self.mod._derive_matches(video, item))
        video = _movie_video(release_group="DON")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Don't Look Up",
            "release_names": ["Dont.Look.Up.2021.1080p.BluRay.x264-DON.srt"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_disc_number_or_language_marker_behind_the_group_still_fires(self):
        # Chunks that trail the group without naming it (a disc number,
        # the site's language marker) do not defeat the match, but a
        # longer group it only prefixes does.
        for release, group, fires in (
            ("Movie.2004.DVDRip.XviD-DEiTY.CD1.srt", "DEiTY", True),
            ("Movie.2024.1080p.BluRay.x264-CM-BG.srt", "CM", True),
            ("Movie.2024.1080p.BluRay.x264-CM-OTHER.srt", "CM", False),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            check = self.assertIn if fires else self.assertNotIn
            check("release_group", self.mod._derive_matches(video, item), release)

    def test_a_dot_separated_group_still_fires(self):
        # The site's own release names sometimes separate the group
        # with dots instead of a hyphen; the same span walk runs over
        # the dot segments when no hyphen span exists.
        video = _movie_video(release_group="COLLECTIVE")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Dune: Part Two",
            "release_names": [
                "Dune.Part.Two.2024.1080p.HDTS.CLEAN.X264.COLLECTIVE"
            ],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))
        video = _movie_video(release_group="REWARD")
        item = {
            "id": 1,
            "type": "episode",
            "original_title": "Breaking Bad",
            "release_names": [
                "breaking.bad.s01e01.720p.bluray.x264-reward"
            ],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_profile_modifiers_are_not_group_content(self):
        # A hyphen segment naming the release's profile (Atmos, REMUX)
        # is body content, so the group behind it still fires.
        for release, group in (
            ("Movie.2024.WEB-DL-DDP5.1-Atmos-CM.srt", "CM"),
            ("Movie.2024.BluRay-REMUX-FGT.srt", "FGT"),
            ("Dune.2021.1080p.BluRay.REMUX.AVC.DTS-HD.MA.TrueHD.7.1.Atmos-FGT", "FGT"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Dune" if release.startswith("Dune") else "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_leading_zero_counter_is_not_a_year(self):
        # A four-digit chunk with a leading zero trails a group as a
        # counter instead of naming the production's year, so the group
        # segment stays a group and its tokens never claim a source.
        video = _movie_video(source="Blu-ray")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.WEB-DL.x264-BD.0001.srt"],
        }
        self.assertNotIn("source", self.mod._derive_matches(video, item))
        video = _movie_video(release_group="BD")
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_unlisted_compound_variants_stay_out_of_the_group(self):
        # Compound tag variants the tables name (DTS-ES, WEB-DLRip,
        # Dual-Audio) end the span at their own boundary, so the group
        # behind them still fires.
        for release, group in (
            ("Movie.2024.2160p.DTS-ES-GRP.srt", "GRP"),
            ("Movie.2024.1080p.WEB-DLRip-GRP.srt", "GRP"),
            ("Movie.2024.x264.Dual-Audio-GRP.srt", "GRP"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_annotations_dot_joined_behind_the_group_still_fire(self):
        # Chunks dot-joined behind the group inside its own segment
        # annotate the group rather than name it, so they do not defeat
        # the match.
        for release, group in (
            ("Movie.2024.1080p.BluRay.x264-CM.HI.srt", "CM"),
            ("Movie.2024.1080p.BluRay.x264-CM.Bulgarian.srt", "CM"),
            ("Movie.2024.1080p.BluRay.x264-EVO[TGx].srt", "EVO"),
            ("Movie.2004.DVDRip.XviD-DEiTY.Part1.srt", "DEiTY"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_dot_tailed_short_source_is_not_a_group(self):
        # A name run on dots never carries a short source token as its
        # group, so a trailing WEB or BD stays a source claim.
        for release, source in (
            ("Movie.2024.1080p.WEB.srt", "Web"),
            ("Movie.2024.1080p.BD.srt", "Blu-ray"),
        ):
            video = _movie_video(source=source)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn("source", self.mod._derive_matches(video, item), release)

    def test_a_space_separated_group_still_fires(self):
        # The site's release names sometimes separate every tag with
        # spaces; the span walk runs over the space segments when no
        # hyphen or dot span exists.
        video = _movie_video(release_group="WKD")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Devil In Dune",
            "release_names": ["Devil In Dune (2021) HDRip XviD WKD"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_counter_beside_a_short_source_is_not_a_year(self):
        # A four-digit chunk beside only a short source token counts
        # out the group instead of naming the production's year, so the
        # group segment stays a group and its tokens never claim a
        # source.
        video = _movie_video(source="Blu-ray")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.WEB-DL.x264-BD.1234.srt"],
        }
        self.assertNotIn("source", self.mod._derive_matches(video, item))
        video = _movie_video(release_group="BD")
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_dot_separated_compound_tag_still_evidences_a_group(self):
        # A compound tag boundary stops the walk as release tags, so
        # the group behind a dotted WEB.DL still fires.
        video = _movie_video(release_group="CM")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.WEB.DL.CM.srt"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_split_channel_fragment_is_not_group_content(self):
        # A dot or space segment that is only a channel fragment
        # (the 1 of a dotted DD5.1, a space-joined 5.1) names the audio
        # track, so the group behind it still fires.
        for release, group in (
            ("Movie.2024.1080p.WEB-DL.x264.DD5.1.GRP.srt", "GRP"),
            ("Devil In Dune (2021) HDRip XviD AC3 5.1 WKD", "WKD"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie" if release.startswith("Movie") else "Devil In Dune",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_dot_joined_annotation_behind_the_group_still_fires(self):
        # Chunks dot-joined behind the group inside its own segment
        # annotate the group rather than name it, even when the whole
        # name runs on dots.
        video = _movie_video(release_group="CM")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.1080p.BluRay.x264.CM.HI.srt"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_space_joined_name_with_dotted_tags_keeps_its_group(self):
        # A dot split of a name run on spaces shreds its tags across
        # segments, so the dot span is rejected and the space walk
        # behind it sees the tags whole.
        for release, group in (
            ("Devil In Dune (2021) HDRip H.264 AC3 5.1 WKD", "WKD"),
            ("Movie (2024) 1080p WEB H.264 DD5.1 WKD", "WKD"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie" if release.startswith("Movie") else "Devil In Dune",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_dotted_disc_number_behind_the_group_still_fires(self):
        # A short digit run behind a release group is a disc number,
        # not the channel half of an audio tag, so the group behind it
        # still fires.
        video = _movie_video(release_group="SPARKS")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.1080p.BluRay.x264.SPARKS.1.srt"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_profile_words_between_the_codec_and_the_group_are_body(self):
        # A segment naming the release's dynamic range or bit depth
        # (HDR, 10bit) is body content, so the group behind it fires.
        for release, group in (
            ("Movie.2024.2160p.WEB-DL.H265.HDR.GRP", "GRP"),
            ("Movie.2024.2160p.WEB-DL.H265.10bit.GRP", "GRP"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_an_edition_only_release_name_still_matches_its_edition(self):
        # A release name that carries no catalog tag is still scored on
        # its own name, so a free-form attribute still matches it.
        video = _movie_video(edition="IMAX")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie 2024 IMAX"],
        }
        self.assertIn("edition", self.mod._derive_matches(video, item))

    def test_a_split_channel_pair_is_not_group_content(self):
        # A channel count split off its audio tag by dots or spaces
        # stays audio content, even behind a modifier like MA, so the
        # group behind it still fires.
        for release, group in (
            ("Movie.2024.2160p.BluRay.x265.TrueHD.7.1.GRP", "GRP"),
            ("Movie.2024.2160p.BluRay.x264.DTS-HD.MA.5.1.GRP", "GRP"),
            ("Movie.2024.1080p.WEB-DL.AAC.2.0.GRP", "GRP"),
            ("Movie.2024.1080p.BluRay.DDP5.1.Atmos.7.1.GRP", "GRP"),
            ("Devil In Dune (2021) BluRay DTS-HD MA 5.1 WKD", "WKD"),
        ):
            video = _movie_video(release_group=group)
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie" if release.startswith("Movie") else "Devil In Dune",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_space_before_the_group_only_keeps_its_evidence(self):
        # When the walk runs out of segments, the first segment's own
        # tags are the evidence that the span trails a release, so a
        # lone space before the group keeps it while a bare title does
        # not.
        video = _movie_video(release_group="CM")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.BluRay.x264 CM.srt"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie CM.srt"],
        }
        self.assertNotIn("release_group", self.mod._derive_matches(video, item))

    def test_a_hyphenated_hearing_annotation_behind_the_group_still_fires(self):
        # A hearing annotation behind the group names the track, not the
        # group, so the group behind it still fires.
        for release in (
            "Movie.2024.1080p.BluRay.x264-CM-HI.srt",
            "Movie.2024.1080p.BluRay.x264-CM-SDH.srt",
        ):
            video = _movie_video(release_group="CM")
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_bracketed_marker_leaves_no_trailing_space(self):
        # The bracketed language marker is stripped and the leftover
        # separator characters with it, so the group behind the marker
        # still fires.
        for release in (
            "Movie.2024.1080p.BluRay.x264.SPARKS [BG].srt",
            "Movie.2024.1080p.BluRay.x264.SPARKS.[BG].srt",
            "Movie.2024.1080p.BluRay.x264-SPARKS-[BG].srt",
        ):
            video = _movie_video(release_group="SPARKS")
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            self.assertIn(
                "release_group", self.mod._derive_matches(video, item), release
            )

    def test_a_hyphenated_disc_tail_does_not_hide_a_dotted_group(self):
        # A hyphen span that is only a disc tail is not the group, so
        # the walk falls through to the dot segments where the group
        # lives.
        video = _movie_video(release_group="CM")
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Movie",
            "release_names": ["Movie.2024.BluRay.x264.CM-1.srt"],
        }
        self.assertIn("release_group", self.mod._derive_matches(video, item))

    def test_a_trailing_language_marker_is_not_release_content(self):
        # A member can trail with the site's language marker before or
        # inside the extension; the marker is dropped, so the group and
        # the source claims behind it still fire.
        video = _movie_video(release_group="CM", source="Blu-ray")
        for release in ("Movie.2024.1080p.BluRay.x264-CM.bg.srt", "Movie.2024.1080p.BluRay.x264-CM[BG].srt"):
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Movie",
                "release_names": [release],
            }
            matches = self.mod._derive_matches(video, item)
            self.assertIn("release_group", matches, release)
            self.assertIn("source", matches, release)

    def test_an_accented_title_excludes_both_its_release_spellings(self):
        # Both sides use the accent-folded normalizer, so a production
        # titled "Café Web" excludes its plain release spelling too, and
        # the title's web token is not a source claim in either shape;
        # the source position's web token still claims its source.
        for release in ("Cafe.Web.2024.1080p.BluRay.x264-GROUP", "Café.Web.2024.1080p.BluRay.x264-GROUP"):
            item = {
                "id": 1,
                "type": "movie",
                "original_title": "Café Web",
                "imdb_id": "tt0000001",
                "release_names": [release],
            }
            video = _movie_video(title="Café Web", year=2024, source="Web")
            self.assertNotIn("source", self.mod._derive_matches(video, item), release)
        item = {
            "id": 1,
            "type": "movie",
            "original_title": "Café Web",
            "imdb_id": "tt0000001",
            "release_names": ["Café.Web.2024.1080p.WEB-DL.x264-CM"],
        }
        video = _movie_video(title="Café Web", year=2024, source="Web")
        self.assertIn("source", self.mod._derive_matches(video, item))

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

    def test_movie_payload_carries_the_release_attributes(self):
        # The download call is separate, so the movie payload carries the
        # video's release attributes and the item's title (whose tokens are
        # excluded from source matching) for member ranking.
        provider = self._provider_with_responses(
            {
                "https://vladoon.com/subs/search-subtitles?q=Dune": _fixture_bytes(
                    "vladoon_search_dune.json"
                )
            }
        )

        results = provider.search(
            _movie_video(source="Blu-ray", video_codec="H.264"), [{"alpha3": "bul"}], {}
        )

        payload = results[0]["provider_payload"]
        self.assertEqual(payload["title"], "Dune")
        self.assertEqual(payload["video"], {"source": "Blu-ray", "video_codec": "H.264"})

        results = provider.search(_movie_video(), [{"alpha3": "bul"}], {})
        payload = results[0]["provider_payload"]
        self.assertEqual(payload["title"], "Dune")
        self.assertNotIn("video", payload)

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
        # A payload without release attributes (an older search's, or a
        # video that carries none) keeps archive order, upstream's rule.
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

    def test_movie_download_pins_the_attribute_matched_member(self):
        # The candidate's attribute matches are the union over the item's
        # releases, so the pick must deliver the variant that carries the
        # matched attributes, not the archive's first member.
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
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Dune",
            "video": {"source": "Blu-ray", "video_codec": "H.264"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Dune.2021.1080p.BluRay.H264.AC3-BGG.srt")

    def test_movie_download_prefers_the_more_matched_member(self):
        body = _zip_bytes(
            {
                "Dune.2021.1080p.WEB-DL.DDP5.1.Atmos.H264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Dune.2021.1080p.BluRay.DDP5.1.Atmos.H264-BGG.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Dune",
            "video": {"source": "Blu-ray", "audio_codec": "EAC3", "video_codec": "H.264"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Dune.2021.1080p.BluRay.DDP5.1.Atmos.H264-BGG.srt")

    def test_movie_download_breaks_attribute_ties_by_archive_order(self):
        # Both members carry the matched source, so the earlier member
        # in archive order wins.
        body = _zip_bytes(
            {
                "Dune.2021.1080p.BluRay.H264.AC3-BGG.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Dune.2021.2160p.BluRay.X264.AC3-HUD.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Dune",
            "video": {"source": "Blu-ray"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Dune.2021.1080p.BluRay.H264.AC3-BGG.srt")

    def test_movie_download_skips_forced_members(self):
        # The host reads a pinned member as-is, so a forced member must
        # not win the ranking for a normal request, exactly like the
        # episode path.
        body = _zip_bytes(
            {
                "Dune.2021.1080p.WEB-DL.DDP5.1.Atmos.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Dune.2021.1080p.BluRay.forced.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Dune",
            "video": {"source": "Blu-ray"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Dune.2021.1080p.WEB-DL.DDP5.1.Atmos.x264-CM.srt")

    def test_forced_only_movie_archive_is_rejected(self):
        body = _zip_bytes({"Dune.2021.1080p.BluRay.forced.srt": b"forced\r\n"})
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Dune",
            "video": {"source": "Blu-ray"},
        }

        with self.assertRaises(ValueError):
            provider.download(payload, {"alpha3": "bul"}, {})

    def test_movie_download_preserves_source_tokens_repeated_after_the_title_prefix(self):
        # Only the title's own consecutive token span is removed, so the
        # web token in the source position of a WEB-DL release of a
        # production titled "The Web" still claims its source and wins
        # the pick from second place.
        body = _zip_bytes(
            {
                "The.Web.2024.1080p.BluRay.x264-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "The.Web.2024.1080p.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "The Web",
            "video": {"source": "Web"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "The.Web.2024.1080p.WEB-DL.x264-CM.srt")

    def test_movie_download_excludes_a_title_behind_a_leading_annotation(self):
        # The title occurrence behind a leading annotation is excluded, so
        # the annotated Blu-ray member's title word must not fire the
        # requested Web source, tie the genuine WEBRip member and win by
        # archive order.
        body = _zip_bytes(
            {
                "[BG].The.Web.2024.1080p.BluRay.x264-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "The.Web.2024.1080p.WEBRip.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "The Web",
            "video": {"source": "Web"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "The.Web.2024.1080p.WEBRip.x264-CM.srt")

    def test_movie_download_excludes_an_accented_title_in_both_spellings(self):
        # The accent-folded exclusion applies to both release spellings of
        # an accented title, so the title's web token must not tie the
        # genuine WEBRip member into an archive-order win.
        for first, second in (
            ("Cafe.Web.2024.1080p.BluRay.x264-GROUP.srt", "Cafe.Web.2024.1080p.WEBRip.x264-CM.srt"),
            ("Café.Web.2024.1080p.BluRay.x264-GROUP.srt", "Café.Web.2024.1080p.WEBRip.x264-CM.srt"),
        ):
            body = _zip_bytes(
                {
                    first: b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                    second: b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
                }
            )
            provider = self._provider_with_responses(
                {"https://vladoon.com/subs/download/10838": body}
            )
            payload = {
                "provider": "vladoon",
                "schema": 1,
                "item_id": "10838",
                "kind": "movie",
                "title": "Café Web",
                "video": {"source": "Web"},
            }

            archive = provider.download(payload, {"alpha3": "bul"}, {})

            self.assertEqual(archive["member"], second)

    def test_movie_download_matches_the_release_group_only_in_the_suffix(self):
        # A group token in a source position mid-name must not fire the
        # requested release group, so the member carrying the true group
        # suffix wins the pick from second place.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.720p.BluRay.x264-WEB.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"release_group": "WEB"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.720p.BluRay.x264-WEB.srt")

    def test_movie_download_ranks_release_identity_above_generic_attributes(self):
        # Release identity (source, streaming service, release group)
        # outweighs the generic attributes, so a wrong-source member with
        # a fuller filename must not outrank the member that agrees with
        # the video's release.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.WEB-DL.H264.DTS-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {
                "source": "Blu-ray",
                "release_group": "GROUP",
                "resolution": "1080p",
                "video_codec": "H.264",
                "audio_codec": "DTS",
            },
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.BluRay-GROUP.srt")

    def test_movie_download_scores_a_members_release_directory(self):
        # Members are scored on their full archive path, so a per-release
        # directory can carry the variant's tags over a plain filename.
        body = _zip_bytes(
            {
                "Movie.2024.BluRay.x264-GROUP/Movie.bg.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.1080p.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {
                "source": "Blu-ray",
                "resolution": "1080p",
                "video_codec": "H.264",
            },
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.BluRay.x264-GROUP/Movie.bg.srt")

    def test_movie_download_cleans_each_path_component_separately(self):
        # Title and group tokens are cleaned per path component, so a
        # member whose directory repeats the title must not fire the
        # title's source claim from its basename, and a member whose
        # directory carries a group-like source token must not fire the
        # requested source from across the boundary.
        body = _zip_bytes(
            {
                "The.Web/The.Web.2024.1080p.BluRay.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "The.Web.2024.1080p.WEBRip.x264-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "The Web",
            "video": {"source": "Web"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "The.Web.2024.1080p.WEBRip.x264-GROUP.srt")

        body = _zip_bytes(
            {
                "Movie.2024.WEB-DL.x264-BD/Movie.2024.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay.x264-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.BluRay.x264-GROUP.srt")

    def test_movie_download_drops_a_hyphenated_directory_group_from_source_matching(self):
        # The group span is dropped from each component before source
        # matching, so a directory group like BD-FOO must not leave its
        # BD half reading as a Blu-ray source claim.
        body = _zip_bytes(
            {
                "Movie.2024.WEB-DL.x264-BD-FOO/Movie.2024.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay.x264-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.BluRay.x264-GROUP.srt")

    def test_movie_download_matches_the_group_behind_a_language_marker(self):
        # A trailing language marker is dropped, so the annotated group
        # member wins the pick from second place.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.BluRay.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.1080p.BluRay.x264-CM.bg.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"release_group": "CM", "source": "Blu-ray"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.1080p.BluRay.x264-CM.bg.srt")

    def test_movie_download_keeps_a_hyphenated_title_out_of_the_group_span(self):
        # The group span stops before any segment carrying a title
        # token, so a hyphenated production title never swallows the
        # release body: the member keeps both its BluRay source and its
        # CM group.
        body = _zip_bytes(
            {
                "Spider-Man.2024.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Spider-Man.2024.BluRay.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Spider-Man",
            "video": {"source": "Blu-ray", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Spider-Man.2024.BluRay.x264-CM.srt"
        )

    def test_movie_download_keeps_a_codec_segment_out_of_the_group_span(self):
        # A codec segment is body content, so even a name that runs its
        # tag separators as hyphens keeps the codec in the body and the
        # CM group firing: the WEB member's source and group beat the
        # Blu-ray member's lone group.
        body = _zip_bytes(
            {
                "Movie.2024.BluRay.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.WEB-DL-x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Web", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Movie.2024.WEB-DL-x264-CM.srt"
        )

    def test_movie_download_keeps_a_three_chunk_audio_tag_out_of_the_group_span(
        self,
    ):
        # Every adjacent pair inside a multi-chunk audio tag is a
        # compound boundary, so Dolby-Digital-Plus is all release body
        # and the CM group behind it still fires.
        body = _zip_bytes(
            {
                "Movie.2024.Dolby-Digital-Plus-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.Dolby-Digital-Plus-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"audio_codec": "EAC3", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Movie.2024.Dolby-Digital-Plus-CM.srt"
        )

    def test_movie_download_keeps_a_multi_segment_group_whole_in_source_cleaning(
        self,
    ):
        # Source cleanup drops the same complete group span the group
        # match compares, so a three-segment group never leaves its
        # head reading as a source claim: the genuine Blu-ray member
        # beats the WEB member.
        body = _zip_bytes(
            {
                "Movie.2024.WEB-DL.x264-BD-FOO-BAR.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay.x264-BD-FOO-BAR.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray", "release_group": "BD-FOO-BAR"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Movie.2024.BluRay.x264-BD-FOO-BAR.srt"
        )

    def test_movie_download_ranks_the_channel_suffixed_audio_member(self):
        # A hyphen segment carrying a channel-suffixed audio tag is body
        # content, so the WEB member keeps its source and its group and
        # beats the Blu-ray member's lone group.
        body = _zip_bytes(
            {
                "Movie.2024.BluRay.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.WEB-DL-AAC2.0-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Web", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Movie.2024.WEB-DL-AAC2.0-CM.srt"
        )

    def test_movie_download_ranks_the_hyphen_separated_source_member(self):
        # A hyphen segment carrying the long-form source tag is body
        # content, so the Blu-ray member keeps its source and its group
        # and beats the first WEB member.
        body = _zip_bytes(
            {
                "Movie.2024.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.1080p-BluRay-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Movie.2024.1080p-BluRay-CM.srt"
        )

    def test_movie_download_does_not_read_a_title_word_group_as_a_source(self):
        # A group genuinely named after a title word fires as a group
        # and is dropped from source matching, so the WEB-group member
        # claims no web source and loses to the genuine WEB-DL member.
        body = _zip_bytes(
            {
                "The.Web.2024.BluRay.x264-WEB.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "The.Web.2024.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "The Web",
            "video": {"source": "Web"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "The.Web.2024.WEB-DL.x264-CM.srt"
        )

    def test_movie_download_scores_the_innermost_component_with_release_tags(self):
        # A member is scored on its innermost path component that
        # carries release tags of its own, so a release directory's
        # identity does not override the variant the member's own
        # basename names: the Blu-ray basename beats the WEB-DL
        # resync filed first inside the same Blu-ray directory.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.BluRay.x264-SPARKS/Movie.2024.1080p.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.1080p.BluRay.x264-SPARKS/Movie.2024.1080p.BluRay.x264-SPARKS.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray", "release_group": "SPARKS"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"],
            "Movie.2024.1080p.BluRay.x264-SPARKS/Movie.2024.1080p.BluRay.x264-SPARKS.srt",
        )

    def test_movie_download_ranks_a_dot_tailed_source_member(self):
        # A trailing dotted WEB stays a source claim instead of turning
        # into a group, so the WEB member beats the Blu-ray member
        # filed first.
        body = _zip_bytes(
            {
                "Movie.2024.BluRay.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.WEB.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Web"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.WEB.srt")

    def test_movie_download_falls_back_to_the_directory_behind_a_bare_basename(
        self,
    ):
        # A bare basename annotated with the language word carries no
        # release identity of its own, so the member is scored on its
        # directory and beats the wrong-release member filed first.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.1080p.BluRay.x264-CM/Movie.Bulgarian.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {
                "source": "Blu-ray",
                "release_group": "CM",
                "resolution": "1080p",
                "video_codec": "H.264",
            },
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"],
            "Movie.2024.1080p.BluRay.x264-CM/Movie.Bulgarian.srt",
        )

    def test_movie_download_falls_back_to_the_directory_behind_a_year_only_basename(
        self,
    ):
        # A year alone does not carry release identity, so a bare
        # year-only basename belongs to the release its directory
        # names.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay.x264-GROUP/Movie.2024.bg.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray", "release_group": "GROUP"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"],
            "Movie.2024.BluRay.x264-GROUP/Movie.2024.bg.srt",
        )

    def test_movie_download_ranks_the_dotted_compound_group_member(self):
        # A compound tag boundary counts as release tags in the dot
        # walk too, so the dotted WEB.DL member keeps its source and
        # its group and beats the OTHER member filed first.
        body = _zip_bytes(
            {
                "Movie.2024.WEB.DL.OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.WEB.DL.CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Web", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.WEB.DL.CM.srt")

    def test_movie_download_falls_back_to_the_directory_behind_a_year_and_language_basename(
        self,
    ):
        # A year alone does not evidence a group, so a basename carrying
        # only the year and the language word falls back to its
        # directory instead of being read as its own release.
        body = _zip_bytes(
            {
                "Movie.2024.1080p.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay.x264-CM/Movie.2024.Bulgarian.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"],
            "Movie.2024.BluRay.x264-CM/Movie.2024.Bulgarian.srt",
        )

    def test_movie_download_falls_back_to_the_directory_behind_a_hyphenated_marker(
        self,
    ):
        # A bare basename trailing with the site's language marker
        # behind a hyphen carries no release identity of its own, so the
        # member is scored on its directory and beats the wrong-release
        # member filed first; the Cyrillic language word behaves the
        # same.
        for basename in ("Movie.2024-BG.srt", "Movie.2024.Български.srt"):
            body = _zip_bytes(
                {
                    "Movie.2024.1080p.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                    f"Movie.2024.BluRay.x264-CM/{basename}": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
                }
            )
            provider = self._provider_with_responses(
                {"https://vladoon.com/subs/download/10838": body}
            )
            payload = {
                "provider": "vladoon",
                "schema": 1,
                "item_id": "10838",
                "kind": "movie",
                "title": "Movie",
                "video": {"source": "Blu-ray", "release_group": "CM"},
            }

            archive = provider.download(payload, {"alpha3": "bul"}, {})

            self.assertEqual(
                archive["member"], f"Movie.2024.BluRay.x264-CM/{basename}"
            )

    def test_movie_download_ranks_the_channel_suffixed_audio_codec_member(self):
        # The release-tag gate strips channel digits as the audio
        # matcher does, so a basename carrying only a channel-suffixed
        # audio tag is scored on its own name and the DDP member beats
        # the AAC member filed first.
        body = _zip_bytes(
            {
                "Movie.2024.AAC2.0.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.DDP5.1.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"audio_codec": "EAC3"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.DDP5.1.srt")

    def test_movie_download_falls_back_past_a_title_word_tag_chunk(self):
        # A single-word title whose word is itself a tag chunk (Ray is
        # a Blu-ray chunk) does not evidence a group behind the title:
        # the first-segment evidence is read behind the title's own
        # occurrence, so the bare basename falls back to its directory.
        body = _zip_bytes(
            {
                "Ray.2004.1080p.WEB-DL.x264-OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Ray.2004.1080p.BluRay.x264-CM/Ray.Bulgarian.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Ray",
            "video": {
                "source": "Blu-ray",
                "release_group": "CM",
                "resolution": "1080p",
                "video_codec": "H.264",
            },
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"],
            "Ray.2004.1080p.BluRay.x264-CM/Ray.Bulgarian.srt",
        )

    def test_movie_download_ranks_the_free_form_codec_basename_member(self):
        # The codec table covers the AV1 codec, so a basename naming
        # one is the variant of its own: for an AV1 video the AV1
        # basename beats the H.264 basename filed first even though the
        # shared directory carries the release tags, and for an
        # H.264 video the AV1 basename does not inherit the directory's
        # H.264 claim, so the H.264 basename beats the AV1 basename
        # filed first.
        members = {
            "Movie.2024.BluRay.H264-CM/Movie.2024.H264.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
            "Movie.2024.BluRay.H264-CM/Movie.2024.AV1.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            "Movie.2024.BluRay.H264-CM/Movie.2024.VP9.srt": b"1\n00:00:01,000 --> 00:00:02,000\nC\n",
        }
        for video_codec, winner in (
            ("AV1", "Movie.2024.BluRay.H264-CM/Movie.2024.AV1.srt"),
            ("H.264", "Movie.2024.BluRay.H264-CM/Movie.2024.H264.srt"),
            ("VP9", "Movie.2024.BluRay.H264-CM/Movie.2024.VP9.srt"),
        ):
            with self.subTest(video_codec=video_codec):
                provider = self._provider_with_responses(
                    {
                        "https://vladoon.com/subs/download/10838": _zip_bytes(
                            dict(members)
                        )
                    }
                )
                payload = {
                    "provider": "vladoon",
                    "schema": 1,
                    "item_id": "10838",
                    "kind": "movie",
                    "title": "Movie",
                    "video": {"video_codec": video_codec},
                }

                archive = provider.download(payload, {"alpha3": "bul"}, {})

                self.assertEqual(archive["member"], winner)

    def test_movie_download_ranks_the_annotated_group_member(self):
        # A group name with annotations joined behind it
        # (...x264-CM.REPACK) is group content: the trailing repack
        # annotation does not turn the whole segment into body content,
        # so the member keeps its CM group and the Blu-ray member beats
        # the web member filed first.
        body = _zip_bytes(
            {
                "Movie.2024.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Movie.2024.BluRay.x264-CM.REPACK.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Movie",
            "video": {"source": "Blu-ray", "release_group": "CM"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "Movie.2024.BluRay.x264-CM.REPACK.srt")

    def test_movie_download_ranks_the_modifier_tailed_group_member(self):
        # A trailing segment that is only profile-modifier chunks
        # annotates the release behind the group (...x264.CM.REPACK,
        # ...x264-CM-REPACK), so the member keeps its CM group and the
        # Blu-ray member beats the web member filed first, whether the
        # modifier rides the group's dot separator or its own hyphen
        # segment.
        for winner in (
            "Movie.2024.1080p.BluRay.x264.CM.REPACK.srt",
            "Movie.2024.BluRay.x264-CM-REPACK.srt",
        ):
            with self.subTest(winner=winner):
                body = _zip_bytes(
                    {
                        "Movie.2024.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                        winner: b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
                    }
                )
                provider = self._provider_with_responses(
                    {"https://vladoon.com/subs/download/10838": body}
                )
                payload = {
                    "provider": "vladoon",
                    "schema": 1,
                    "item_id": "10838",
                    "kind": "movie",
                    "title": "Movie",
                    "video": {"source": "Blu-ray", "release_group": "CM"},
                }

                archive = provider.download(payload, {"alpha3": "bul"}, {})

                self.assertEqual(archive["member"], winner)

    def test_movie_download_ranks_the_combined_annotation_group_member(self):
        # Annotations stack behind a group name (CM.REPACK.HI, whether
        # the stack rides the group's own hyphen segment or dot
        # segments of its own), so the whole stack stays span content
        # and the member keeps its CM group: the Blu-ray member beats
        # the web member filed first.
        for winner in (
            "Movie.2024.BluRay.x264-CM.REPACK.HI.srt",
            "Movie.2024.1080p.BluRay.x264.CM.REPACK.HI.srt",
            "Movie.2024.BluRay.x264-CM.REPACK.CD1.srt",
        ):
            with self.subTest(winner=winner):
                body = _zip_bytes(
                    {
                        "Movie.2024.WEB-DL.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                        winner: b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
                    }
                )
                provider = self._provider_with_responses(
                    {"https://vladoon.com/subs/download/10838": body}
                )
                payload = {
                    "provider": "vladoon",
                    "schema": 1,
                    "item_id": "10838",
                    "kind": "movie",
                    "title": "Movie",
                    "video": {"source": "Blu-ray", "release_group": "CM"},
                }

                archive = provider.download(payload, {"alpha3": "bul"}, {})

                self.assertEqual(archive["member"], winner)

    def test_movie_download_ranks_the_note_tailed_group_member_of_a_space_run_name(self):
        # A name run on spaces (the captured Devil In Dune release)
        # keeps its group when a note is dot-joined behind it: the dot
        # walk's shred of the name hands off to the space walk when the
        # stopped segment runs on spaces too, so the WKD member beats
        # the filed-first web member.
        body = _zip_bytes(
            {
                "Devil.In.Dune.2021.CHINESE.1080p.WEB-DL.x264-Mkvking.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "Devil In Dune (2021) HDRip XviD WKD.CD1.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "Devil In Dune",
            "video": {"release_group": "WKD"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(
            archive["member"], "Devil In Dune (2021) HDRip XviD WKD.CD1.srt"
        )

    def test_movie_download_excludes_title_tokens_from_member_source_ranking(self):
        # A title word is not a source claim. For a production titled
        # "The Web", the Blu-ray member's only web claim is the title
        # word itself, while the WEBRip member carries a real web claim,
        # so the WEBRip member must win the pick even from archive second
        # place. With the title exclusion removed, the Blu-ray member's
        # title word fires too, the counts tie and archive order would
        # hand the pick to the wrong-source first member.
        body = _zip_bytes(
            {
                "The.Web.2024.1080p.BluRay.x264-GROUP.srt": b"1\n00:00:01,000 --> 00:00:02,000\nA\n",
                "The.Web.2024.1080p.WEBRip.x264-CM.srt": b"1\n00:00:01,000 --> 00:00:02,000\nB\n",
            }
        )
        provider = self._provider_with_responses(
            {"https://vladoon.com/subs/download/10838": body}
        )
        payload = {
            "provider": "vladoon",
            "schema": 1,
            "item_id": "10838",
            "kind": "movie",
            "title": "The Web",
            "video": {"source": "Web"},
        }

        archive = provider.download(payload, {"alpha3": "bul"}, {})

        self.assertEqual(archive["member"], "The.Web.2024.1080p.WEBRip.x264-CM.srt")

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
