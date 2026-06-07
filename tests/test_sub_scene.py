"""Tests for subscene_best provider."""

import base64
import hashlib
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "providers" / "sub_scene"))

import provider as subscene_module
from provider import (
    LANGUAGE_MAP,
    SubSceneProvider,
    SubsceneDetailParser,
    SubsceneSearchParser,
    SubsceneSubtitleParser,
    _build_search_queries,
    _calculate_score,
    _get_language_code,
    _select_episode_file,
)


class TestDependencyLocks(unittest.TestCase):
    def test_binary_dependency_hashes_cover_bazarr_plus_python_matrix(self):
        manifest_path = Path(__file__).parent.parent / "providers" / "sub_scene" / "provider.json"
        manifest = json.loads(manifest_path.read_text())
        requirements = {
            item["name"]: item
            for item in manifest["dependencies"]["requirements"]
        }

        self.assertGreaterEqual(len(requirements["aiohttp"]["hashes"]), 6)
        self.assertGreaterEqual(len(requirements["cffi"]["hashes"]), 6)


class FakeUrlopenResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class TestLanguageMap(unittest.TestCase):
    def test_common_languages_mapped(self):
        self.assertEqual(LANGUAGE_MAP["English"], "eng")
        self.assertEqual(LANGUAGE_MAP["Vietnamese"], "vie")
        self.assertEqual(LANGUAGE_MAP["Arabic"], "ara")
        self.assertEqual(LANGUAGE_MAP["Chinese BG code"], "zho")
        self.assertEqual(LANGUAGE_MAP["Big 5 code"], "zho")

    def test_get_language_code(self):
        self.assertEqual(_get_language_code("English"), "eng")
        self.assertEqual(_get_language_code("Vietnamese"), "vie")
        self.assertIsNone(_get_language_code("Unknown Language"))


class TestBuildSearchQueries(unittest.TestCase):
    def test_movie_with_year(self):
        video = {"kind": "movie", "title": "Dune", "year": 2021}
        queries = _build_search_queries(video)
        self.assertEqual(queries, ["Dune 2021", "Dune"])

    def test_movie_without_year(self):
        video = {"kind": "movie", "title": "Inception"}
        queries = _build_search_queries(video)
        self.assertEqual(queries, ["Inception"])

    def test_episode(self):
        video = {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1}
        queries = _build_search_queries(video)
        self.assertEqual(queries, ["Breaking Bad"])

    def test_empty_video(self):
        self.assertEqual(_build_search_queries({}), [])
        self.assertEqual(_build_search_queries({"kind": "movie"}), [])


class TestCalculateScore(unittest.TestCase):
    def test_movie_year_match(self):
        video = {"kind": "movie", "year": 2021}
        subtitle = {"release": "Dune.2021.1080p.BluRay"}
        score = _calculate_score(video, subtitle)
        self.assertGreaterEqual(score, 80)

    def test_movie_source_match(self):
        video = {"kind": "movie", "year": 2021, "source": "BluRay"}
        subtitle = {"release": "Dune.2021.1080p.BluRay"}
        score = _calculate_score(video, subtitle)
        self.assertGreaterEqual(score, 90)

    def test_episode_season_episode_match(self):
        video = {"kind": "episode", "season": 1, "episode": 5}
        subtitle = {"release": "Breaking.Bad.S01E05.720p"}
        score = _calculate_score(video, subtitle)
        self.assertGreaterEqual(score, 90)

    def test_episode_season_only(self):
        video = {"kind": "episode", "season": 1, "episode": 5}
        subtitle = {"release": "Breaking.Bad.S01.720p"}
        score = _calculate_score(video, subtitle)
        self.assertGreaterEqual(score, 75)

    def test_episode_match_rejects_conflicting_season_word(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1},
            "Breaking Bad",
            {"release": "Breaking Bad Season 5 2012 720p E01-E08"},
        )

        self.assertIn("series", matches)
        self.assertNotIn("season", matches)
        self.assertNotIn("episode", matches)

    def test_episode_match_accepts_matching_season_word(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1},
            "Breaking Bad",
            {"release": "Breaking Bad Season 1 720p E01-E08"},
        )

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_episode_match_uses_page_title_for_season_pack(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Burn Notice", "season": 3, "episode": 5},
            "Burn Notice - Third Season",
            {"release": "DVDRip.XviD-GROUPS"},
        )

        self.assertIn("series", matches)
        self.assertIn("season", matches)

    def test_episode_match_accepts_range_covering_requested_episode(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 5},
            "Breaking Bad",
            {"release": "Breaking Bad Season 1 720p E01-E08"},
        )

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_episode_match_accepts_1x_marker(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 5},
            "Show",
            {"release": "Show.1x05.HDTV"},
        )

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_required_match_rejects_wrong_1x_episode(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 5}
        subtitle = {"release": "Show.1x06.HDTV"}
        matches = subscene_module._derive_matches(video, "Show", subtitle)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertNotIn("episode", matches)
        self.assertFalse(subscene_module._has_required_match(video, matches, subtitle))

    def test_required_match_rejects_wrong_episode_page_title(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 5}
        subtitle = {"release": "DVDRip.XviD-GROUP"}
        matches = subscene_module._derive_matches(video, "Show S01E01", subtitle)

        self.assertIn("series", matches)
        self.assertNotIn("season", matches)
        self.assertNotIn("episode", matches)
        self.assertFalse(subscene_module._has_required_match(video, matches, subtitle))

    def test_episode_match_accepts_three_digit_episode_marker(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 100},
            "Show",
            {"release": "Show.S01E100.HDTV"},
        )

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_required_match_rejects_wrong_three_digit_episode(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 100}
        subtitle = {"release": "Show.S01E101.HDTV"}
        matches = subscene_module._derive_matches(video, "Show", subtitle)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertNotIn("episode", matches)
        self.assertFalse(subscene_module._has_required_match(video, matches, subtitle))

    def test_episode_match_accepts_page_title_range(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 5}
        subtitle = {"release": "DVDRip.XviD-GROUP"}
        matches = subscene_module._derive_matches(video, "Show S01E01-E08", subtitle)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)
        self.assertTrue(subscene_module._has_required_match(video, matches, subtitle))

    def test_episode_match_accepts_ordinal_season_beyond_tenth(self):
        matches = subscene_module._derive_matches(
            {"kind": "episode", "series": "Show", "season": 11, "episode": 5},
            "Show - Eleventh Season",
            {"release": "DVDRip.XviD-GROUPS"},
        )

        self.assertIn("series", matches)
        self.assertIn("season", matches)

    def test_episode_match_accepts_chained_marker(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 6}
        subtitle = {"release": "Show.S01E05E06.HDTV"}
        matches = subscene_module._derive_matches(video, "Show", subtitle)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)
        self.assertTrue(subscene_module._has_required_match(video, matches, subtitle))


class TestSelectEpisodeFile(unittest.TestCase):
    def test_selects_requested_episode_from_multi_file_zip(self):
        selected = _select_episode_file(
            ["Show.S01E01.srt", "Show.S01E05.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )
        self.assertEqual(selected, "Show.S01E05.srt")

    def test_unpadded_season_episode_match_requires_episode_boundary(self):
        selected = _select_episode_file(
            ["Show.S1E20.srt", "Show.S1E02.srt"],
            {"kind": "episode", "season": 1, "episode": 2},
        )
        self.assertEqual(selected, "Show.S1E02.srt")

    def test_returns_none_when_episode_archive_has_no_match(self):
        selected = _select_episode_file(
            ["Show.S01E01.srt", "Show.S01E02.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )
        self.assertIsNone(selected)

    def test_rejects_separated_season_episode_conflict(self):
        selected = _select_episode_file(
            ["Show.S01.E05.srt", "Show.S01.E06.srt"],
            {"kind": "episode", "season": 2, "episode": 5},
        )
        self.assertIsNone(selected)

    def test_selects_separated_season_episode_match(self):
        selected = _select_episode_file(
            ["Show.S01.E04.srt", "Show.S01.E05.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )
        self.assertEqual(selected, "Show.S01.E05.srt")

    def test_selects_1x_episode_file(self):
        selected = _select_episode_file(
            ["Show.1x04.srt", "Show.1x05.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, "Show.1x05.srt")

    def test_rejects_conflicting_season_folder_before_episode_fallback(self):
        selected = _select_episode_file(
            ["Season 02/Show.E05.srt", "Season 01/Show.E05.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, "Season 01/Show.E05.srt")

    def test_keeps_single_generic_episode_file(self):
        selected = _select_episode_file(
            ["subtitle.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, "subtitle.srt")

    def test_selects_three_digit_episode_file(self):
        selected = _select_episode_file(
            ["Show.S01E101.srt", "Show.S01E100.srt"],
            {"kind": "episode", "season": 1, "episode": 100},
        )

        self.assertEqual(selected, "Show.S01E100.srt")

    def test_selects_episode_range_file(self):
        selected = _select_episode_file(
            ["Show.S01E01-E08.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, "Show.S01E01-E08.srt")

    def test_selects_chained_episode_file(self):
        selected = _select_episode_file(
            ["Show.S01E05E06.srt"],
            {"kind": "episode", "season": 1, "episode": 6},
        )

        self.assertEqual(selected, "Show.S01E05E06.srt")

    def test_rejects_range_file_from_different_attached_season(self):
        selected = _select_episode_file(
            [
                "Season 01-02/Show.S02E01-E08.srt",
                "Show.S01E05.srt",
            ],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, "Show.S01E05.srt")


class TestDownloadSubtitle(unittest.TestCase):
    def _assert_archive_payload(self, result, body, member):
        # Host-side extraction (Provider Hub v1.1+): the worker forwards the raw archive
        # bytes and the selected member. No worker-side extraction or encoding guessing.
        self.assertIsNotNone(result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], member)
        self.assertFalse(result["empty"])
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)
        self.assertNotIn("episode", result)

    def test_selects_supported_non_srt_member_from_zip(self):
        content = b"[Script Info]\nTitle: Test\n"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Show.S01E05.ass", content)
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self._assert_archive_payload(result, body, "Show.S01E05.ass")

    def test_selects_sami_member_from_zip(self):
        content = b"\xef\xbb\xbf<SAMI>\r\n<BODY>\r\n<SYNC Start=1000><P>Hello</P>"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Dune.2021.1080p.WEBRip.x264-RARBG.smi", content)
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/2604448",
                video={"kind": "movie", "title": "Dune: Part One", "year": 2021},
                config={},
                state={},
            )

        self._assert_archive_payload(result, body, "Dune.2021.1080p.WEBRip.x264-RARBG.smi")

    def test_ignores_appledouble_sidecar_entries(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("__MACOSX/._Show.S01E05.srt", b"sidecar")
            zf.writestr("Show.S01E05.srt", b"actual subtitle")
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self._assert_archive_payload(result, body, "Show.S01E05.srt")

    def _assert_content_payload(self, result, content, subtitle_format):
        # Multipart subtitles are concatenated worker-side and returned as direct content,
        # because the single-member host contract would deliver only one disc.
        self.assertIsNotNone(result)
        self.assertEqual(base64.b64decode(result["content_b64"]), content)
        self.assertEqual(result["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["format"], subtitle_format)
        self.assertFalse(result["empty"])
        self.assertNotIn("archive_b64", result)
        self.assertNotIn("member", result)
        self.assertNotIn("episode", result)
        self.assertNotIn("encoding", result)

    def test_concatenates_multipart_movie_members(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Movie.CD1.srt", b"part one")
            zf.writestr("Movie.CD2.srt", b"part two")
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "movie", "title": "Movie", "year": 2024},
                config={},
                state={},
            )

        # Both discs survive: CD1 and CD2 are joined in part order, not just CD1 pinned.
        self._assert_content_payload(result, b"part one\n\npart two", "srt")

    def test_concatenates_multipart_in_part_order(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            # Written out of order to prove part-number ordering, not archive order.
            zf.writestr("Movie.CD2.srt", b"part two")
            zf.writestr("Movie.CD1.srt", b"part one")
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "movie", "title": "Movie", "year": 2024},
                config={},
                state={},
            )

        self._assert_content_payload(result, b"part one\n\npart two", "srt")

    def test_concatenates_multipart_non_srt_members(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Movie.CD1.ass", b"[Script Info]\npart one")
            zf.writestr("Movie.CD2.ass", b"[Script Info]\npart two")
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "movie", "title": "Movie", "year": 2024},
                config={},
                state={},
            )

        self._assert_content_payload(
            result,
            b"[Script Info]\npart one\n\n[Script Info]\npart two",
            "ass",
        )

    def test_single_member_still_pins_member(self):
        # A non-multipart zip keeps the host member-pin path; concatenation must not kick in.
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Movie.2024.1080p.srt", b"only subtitle")
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "movie", "title": "Movie", "year": 2024},
                config={},
                state={},
            )

        self._assert_archive_payload(result, body, "Movie.2024.1080p.srt")

    def test_episode_multipart_does_not_shadow_matching_single_file(self):
        # A stray CD1/CD2 pair for the wrong episode must not be concatenated when a single
        # file matches the requested episode; the matching single member is pinned instead.
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Show.S01E01.CD1.srt", b"wrong ep part one")
            zf.writestr("Show.S01E01.CD2.srt", b"wrong ep part two")
            zf.writestr("Show.S01E05.srt", b"right episode")
        body = zip_buffer.getvalue()

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self._assert_archive_payload(result, body, "Show.S01E05.srt")

    def test_multipart_rar_defers_to_host_episode(self):
        # RAR is not stdlib-listable, so a multipart rar cannot be concatenated worker-side
        # and must defer to the host episode path rather than pin a guessed member.
        body = b"Rar!\x1a\x07\x00" + b"CD1/CD2 multipart rar payload"

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self.assertIsNotNone(result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["episode"], 5)
        self.assertNotIn("member", result)
        self.assertNotIn("content_b64", result)

    def test_forwards_rar_archive_with_episode(self):
        body = b"Rar!\x1a\x07\x00rar archive payload"

        with patch("provider._http_get", return_value=body):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self.assertIsNotNone(result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 5)
        self.assertFalse(result["empty"])
        self.assertNotIn("member", result)

    def test_rejects_empty_body(self):
        with patch("provider._http_get", return_value=b""):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self.assertIsNone(result)

    def test_rejects_html_error_body(self):
        with patch("provider._http_get", return_value=b"<!DOCTYPE html><html><body>error</body></html>"):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self.assertIsNone(result)

    def test_rejects_zip_with_no_selectable_member(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Show.S01E01.srt", b"wrong episode")
            zf.writestr("Show.S01E02.srt", b"wrong episode")

        with patch("provider._http_get", return_value=zip_buffer.getvalue()):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "episode", "season": 1, "episode": 5},
                config={},
                state={},
            )

        self.assertIsNone(result)

    def test_rejects_paired_vobsub_sub_member(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("movie.sub", b"\x00\x01binary vobsub")
            zf.writestr("movie.idx", b"VobSub index")

        with patch("provider._http_get", return_value=zip_buffer.getvalue()):
            result = subscene_module._download_subtitle(
                "/download/123",
                video={"kind": "movie", "title": "Movie", "year": 2024},
                config={},
                state={},
            )

        self.assertIsNone(result)


class TestSubsceneSearchParser(unittest.TestCase):
    def test_parse_search_results(self):
        html = """
        <html>
        <body>
          <div class="search-result">
            <div class="title">
                <a href="/subscene/12345">Dune (2021)</a>
            </div>
            <div class="title">
                <a href="/subscene/67890">Dune Part Two (2024)</a>
            </div>
          </div>
        </body>
        </html>
        """
        parser = SubsceneSearchParser()
        parser.feed(html)
        
        self.assertEqual(len(parser.results), 2)
        self.assertEqual(parser.results[0]["url"], "/subscene/12345")
        self.assertEqual(parser.results[0]["title"], "Dune (2021)")

    def test_parse_empty_results(self):
        html = "<html><body></body></html>"
        parser = SubsceneSearchParser()
        parser.feed(html)
        self.assertEqual(len(parser.results), 0)

    def test_ignores_homepage_cards_outside_search_results(self):
        html = """
        <html>
        <body>
            <section class="popular">
                <div class="title">
                    <a href="/subscene/159141">Thunderbolts (Thunderbolts*)</a>
                </div>
            </section>
        </body>
        </html>
        """
        parser = SubsceneSearchParser()
        parser.feed(html)

        self.assertEqual(parser.results, [])


class TestSubsceneDetailParser(unittest.TestCase):
    def test_parse_subtitle_table(self):
        html = """
        <html>
        <body>
            <table>
                <tbody>
                    <tr>
                        <td class="a1">
                            <a href="/subtitle/123">
                                <span class="l r positive-icon">English</span>
                                <span class="new">Dune.2021.1080p.BluRay</span>
                            </a>
                        </td>
                        <td class="a40"></td>
                    </tr>
                    <tr>
                        <td class="a1">
                            <a href="/subtitle/456">
                                <span class="l r">Vietnamese</span>
                                <span class="new">Dune.2021.720p.WEB</span>
                            </a>
                        </td>
                        <td class="a40"></td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """
        parser = SubsceneDetailParser()
        parser.feed(html)
        
        self.assertEqual(len(parser.subtitles), 2)
        self.assertEqual(parser.subtitles[0]["url"], "/subtitle/123")
        self.assertEqual(parser.subtitles[0]["language"], "English")
        self.assertEqual(parser.subtitles[0]["release"], "Dune.2021.1080p.BluRay")

    def test_parse_empty_table(self):
        html = "<html><body><table><tbody></tbody></table></body></html>"
        parser = SubsceneDetailParser()
        parser.feed(html)
        self.assertEqual(len(parser.subtitles), 0)

    def test_parse_unmarked_release_span(self):
        html = """
        <html>
        <body>
            <table>
                <tbody>
                    <tr>
                        <td class="a1">
                            <a href="/subtitle/789">
                                <span class="l r">English</span>
                                <span>Dune.2021.1080p.BluRay</span>
                            </a>
                        </td>
                        <td class="a40"></td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """
        parser = SubsceneDetailParser()
        parser.feed(html)

        self.assertEqual(len(parser.subtitles), 1)
        self.assertEqual(parser.subtitles[0]["release"], "Dune.2021.1080p.BluRay")


class TestCloudflareHttp(unittest.TestCase):
    def test_detects_cloudflare_challenge_header_and_body(self):
        self.assertTrue(
            subscene_module._is_cloudflare_challenge(
                403,
                {"cf-mitigated": "challenge"},
                b"<html><title>Just a moment...</title></html>",
            )
        )
        self.assertTrue(
            subscene_module._is_cloudflare_challenge(
                503,
                {},
                b"<script>window._cf_chl_opt = {}</script>",
            )
        )
        self.assertFalse(
            subscene_module._is_cloudflare_challenge(
                200,
                {},
                b"<html><body>Search results</body></html>",
            )
        )

    def test_http_get_uses_ai_cloudscraper_by_default(self):
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.content = b"<html>ok</html>"
        scraper = MagicMock()
        scraper.get.return_value = response

        with patch("provider.cloudscraper.create_scraper", return_value=scraper) as create_scraper:
            body = subscene_module._http_get(
                "https://sub-scene.com/search?query=Dune",
                config={},
                state={},
            )

        self.assertEqual(body, b"<html>ok</html>")
        create_scraper.assert_called_once_with(
            browser={"custom": subscene_module.USER_AGENT},
            interpreter="native",
            enable_cookie_persistence=False,
            debug=False,
        )
        scraper.get.assert_called_once()
        self.assertEqual(scraper.get.call_args.kwargs["timeout"], 30)
        self.assertIn("User-Agent", scraper.get.call_args.kwargs["headers"])

    def test_http_get_retries_without_cookie_persistence_for_legacy_ai_cloudscraper(self):
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.content = b"<html>ok</html>"
        scraper = MagicMock()
        scraper.get.return_value = response

        with patch(
            "provider.cloudscraper.create_scraper",
            side_effect=[
                TypeError("unexpected keyword argument 'enable_cookie_persistence'"),
                scraper,
            ],
        ) as create_scraper:
            body = subscene_module._http_get(
                "https://sub-scene.com/search?query=Dune",
                config={},
                state={},
            )

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(create_scraper.call_count, 2)
        self.assertEqual(create_scraper.call_args_list[0].kwargs["enable_cookie_persistence"], False)
        self.assertNotIn("enable_cookie_persistence", create_scraper.call_args_list[1].kwargs)
        scraper.get.assert_called_once()

    def test_http_get_solves_anubis_inline_before_retrying_original_url(self):
        challenge_response = MagicMock()
        challenge_response.status_code = 401
        challenge_response.headers = {}
        challenge_response.content = b'<script id="anubis_challenge">{}</script>'
        challenge_response.text = '<script id="anubis_challenge">{}</script>'
        challenge_response.url = "https://sub-scene.com/.within.website/?redir=/search"
        solved_response = MagicMock()
        solved_response.status_code = 200
        solved_response.headers = {}
        solved_response.content = b"<html>ok</html>"
        solved_response.text = "<html>ok</html>"
        solved_response.url = "https://sub-scene.com/search?query=Dune"
        scraper = MagicMock()
        scraper.get.side_effect = [challenge_response, solved_response]
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        with patch("provider.cloudscraper.create_scraper", return_value=scraper), patch(
            "provider.solve_anubis_challenge",
            side_effect=fake_solve,
        ):
            body = subscene_module._http_get(
                "https://sub-scene.com/search?query=Dune",
                config={},
                state={},
            )

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        self.assertEqual(scraper.get.call_args_list[0].args[0], "https://sub-scene.com/search?query=Dune")
        self.assertEqual(scraper.get.call_args_list[1].args[0], "https://sub-scene.com/search?query=Dune")
        self.assertIs(solved_calls[0][0], scraper)
        self.assertEqual(solved_calls[0][1], "https://sub-scene.com/.within.website/?redir=/search")
        self.assertEqual(solved_calls[0][2], "https://sub-scene.com/search?query=Dune")

    def test_http_get_detects_embedded_anubis_body_before_returning_html(self):
        challenge_response = MagicMock()
        challenge_response.status_code = 200
        challenge_response.headers = {}
        challenge_response.content = b'<script id="anubis_challenge">{}</script>'
        challenge_response.text = '<script id="anubis_challenge">{}</script>'
        challenge_response.url = "https://sub-scene.com/search?query=Dune"
        solved_response = MagicMock()
        solved_response.status_code = 200
        solved_response.headers = {}
        solved_response.content = b"<html>ok</html>"
        solved_response.text = "<html>ok</html>"
        solved_response.url = "https://sub-scene.com/search?query=Dune"
        scraper = MagicMock()
        scraper.get.side_effect = [challenge_response, solved_response]
        solved_calls = []

        def fake_solve(active_scraper, challenge_url, original_url, timeout):
            solved_calls.append((active_scraper, challenge_url, original_url, timeout))
            return {"techaro.lol-anubis-auth": "ok"}

        with patch("provider.cloudscraper.create_scraper", return_value=scraper), patch(
            "provider.solve_anubis_challenge",
            side_effect=fake_solve,
        ):
            body = subscene_module._http_get(
                "https://sub-scene.com/search?query=Dune",
                config={},
                state={},
            )

        self.assertEqual(body, b"<html>ok</html>")
        self.assertEqual(scraper.get.call_count, 2)
        self.assertEqual(solved_calls[0][1], "https://sub-scene.com/search?query=Dune")
        self.assertEqual(solved_calls[0][2], "https://sub-scene.com/search?query=Dune")

    def test_solve_pow_treats_difficulty_as_leading_zero_bits(self):
        class FakeHash:
            def __init__(self, digest):
                self.digest = digest

            def hexdigest(self):
                return self.digest

        digests = ["04" + "f" * 62, "00000" + "f" * 59]
        calls = []

        def fake_sha256(data):
            calls.append(data)
            return FakeHash(digests[len(calls) - 1])

        with patch("provider.hashlib.sha256", side_effect=fake_sha256):
            nonce, digest = subscene_module._solve_pow("random", 5)

        self.assertEqual(nonce, 0)
        self.assertEqual(digest, "04" + "f" * 62)

    def test_extract_anubis_challenge_reads_rules_wrapper(self):
        challenge = subscene_module._extract_anubis_challenge(
            '<script id="anubis_challenge">'
            + json.dumps(
                {
                    "rules": {"algorithm": "fast", "difficulty": 8},
                    "challenge": {"id": "abc", "randomData": "random"},
                }
            )
            + "</script>"
        )

        self.assertEqual(challenge["id"], "abc")
        self.assertEqual(challenge["randomData"], "random")
        self.assertEqual(challenge["difficulty"], 8)
        self.assertEqual(challenge["method"], "fast")

    def test_extract_anubis_challenge_accepts_string_challenge(self):
        challenge = subscene_module._extract_anubis_challenge(
            '<script id="anubis_challenge">'
            + json.dumps(
                {
                    "rules": {"algorithm": "slow", "difficulty": 7},
                    "challenge": "random-string",
                }
            )
            + "</script>"
        )

        self.assertEqual(challenge["id"], "random-string")
        self.assertEqual(challenge["randomData"], "random-string")
        self.assertEqual(challenge["difficulty"], 7)
        self.assertEqual(challenge["method"], "slow")

    def test_extract_anubis_challenge_unescapes_meta_refresh_url(self):
        challenge = subscene_module._extract_anubis_challenge(
            '<meta http-equiv="refresh" content="0; url=/.within.website/x/cmd/anubis/api/pass-challenge?foo=1&amp;bar=2">'
        )

        self.assertEqual(
            challenge["redirect_url"],
            "/.within.website/x/cmd/anubis/api/pass-challenge?foo=1&bar=2",
        )

    def test_solve_anubis_challenge_honors_refresh_header(self):
        class Cookie:
            name = "techaro.lol-anubis-auth"
            value = "ok"

        class CookieJar(list):
            def update(self, cookies):
                self.extend(cookies)

        first_response = MagicMock()
        first_response.text = ""
        first_response.headers = {
            "Refresh": "0; url=/.within.website/x/cmd/anubis/api/pass-challenge?foo=1"
        }
        first_response.cookies = []
        solved_response = MagicMock()
        solved_response.cookies = [Cookie()]
        session = MagicMock()
        session.get.side_effect = [first_response, solved_response]
        session.cookies = CookieJar()

        with patch("provider.time.sleep"):
            cookies = subscene_module.solve_anubis_challenge(
                session,
                "https://sub-scene.com/.within.website/?redir=/search",
                "https://sub-scene.com/search?query=Dune",
                timeout=30,
            )

        self.assertEqual(cookies, {"techaro.lol-anubis-auth": "ok"})
        self.assertEqual(
            session.get.call_args_list[1].args[0],
            "https://sub-scene.com/.within.website/x/cmd/anubis/api/pass-challenge?foo=1",
        )

    def test_http_get_uses_flaresolverr_fallback_after_cloudflare_challenge(self):
        challenge_response = MagicMock()
        challenge_response.status_code = 403
        challenge_response.headers = {"cf-mitigated": "challenge"}
        challenge_response.content = b"<html><title>Just a moment...</title></html>"
        scraper = MagicMock()
        scraper.get.return_value = challenge_response
        state = {}
        flaresolverr_payload = {
            "status": "ok",
            "solution": {
                "status": 200,
                "response": "<html>normal search results</html>",
                "cookies": [
                    {"name": "cf_clearance", "value": "token"},
                    {"name": "session", "value": "abc"},
                ],
                "userAgent": "Mozilla/5.0 solved",
            },
        }

        with patch("provider.cloudscraper.create_scraper", return_value=scraper), patch(
            "provider.urllib.request.urlopen",
            return_value=FakeUrlopenResponse(json.dumps(flaresolverr_payload).encode("utf-8")),
        ) as urlopen:
            body = subscene_module._http_get(
                "https://sub-scene.com/search?query=Dune",
                config={
                    "flaresolverr_url": "http://flaresolverr:8191/v1",
                    "flaresolverr_timeout_ms": 45000,
                },
                state=state,
            )

        self.assertEqual(body, b"<html>normal search results</html>")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://flaresolverr:8191/v1")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["cmd"], "request.get")
        self.assertEqual(payload["maxTimeout"], 10000)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)
        self.assertEqual(state["flaresolverr_cookies"]["cf_clearance"], "token")
        self.assertEqual(state["flaresolverr_user_agent"], "Mozilla/5.0 solved")

    def test_http_get_raises_visible_error_without_flaresolverr_fallback(self):
        challenge_response = MagicMock()
        challenge_response.status_code = 403
        challenge_response.headers = {"cf-mitigated": "challenge"}
        challenge_response.content = b"<html><title>Just a moment...</title></html>"
        scraper = MagicMock()
        scraper.get.return_value = challenge_response

        with patch("provider.cloudscraper.create_scraper", return_value=scraper):
            with self.assertRaises(subscene_module.CloudflareBlockedError):
                subscene_module._http_get(
                    "https://sub-scene.com/search?query=Dune",
                    config={},
                    state={},
                )

    def test_search_subscene_surfaces_search_endpoint_failures(self):
        with patch("provider._http_get", side_effect=TimeoutError("search timed out")):
            with self.assertRaises(TimeoutError):
                subscene_module._search_subscene(
                    "Dune",
                    delay_ms=0,
                    config={},
                    state={},
                )


class TestSubsceneSubtitleParser(unittest.TestCase):
    def test_parse_download_url(self):
        html = """
        <html>
        <body>
            <div class="download">
                <a href="/download/123">Download</a>
            </div>
            <div class="title">Dune (2021)</div>
            <div class="release">
                <b>Release Info:</b>
                <span class="new">Dune.2021.1080p.BluRay.x264</span>
            </div>
        </body>
        </html>
        """
        parser = SubsceneSubtitleParser()
        parser.feed(html)
        
        self.assertEqual(parser.download_url, "/download/123")
        self.assertEqual(parser.title, "Dune (2021)")


class TestSubSceneProvider(unittest.TestCase):
    def setUp(self):
        self.provider = SubSceneProvider()

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_movie(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/12345", "title": "Dune (2021)"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Dune.2021.1080p.BluRay",
                "hi": False,
            },
            {
                "url": "/subtitle/456",
                "language": "Vietnamese",
                "release": "Dune.2021.720p.WEB",
                "hi": False,
            },
        ]

        video = {"kind": "movie", "title": "Dune", "year": 2021}
        languages = ["eng", "vie"]
        config = {"request_delay_ms": 0}

        results = self.provider.search(video, languages, config)
        
        self.assertGreater(len(results), 0)
        self.assertTrue(any(r["language"]["alpha3"] == "eng" for r in results))
        self.assertTrue(any(r["language"]["alpha3"] == "vie" for r in results))

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_populates_movie_match_keys(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/12345", "title": "Dune (2021)"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Dune.2021.1080p.BluRay",
                "hi": False,
            },
        ]

        results = self.provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "source": "BluRay",
                "resolution": "1080p",
            },
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertIn("source", results[0]["matches"])
        self.assertIn("resolution", results[0]["matches"])
        self.assertEqual(results[0]["language"]["alpha2"], "en")

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_continues_after_unmatched_first_query(self, mock_detail, mock_search):
        mock_search.side_effect = [
            [{"url": "/subscene/159141", "title": "Thunderbolts (Thunderbolts*)"}],
            [{"url": "/subscene/155629", "title": "Dune (2021)"}],
        ]
        mock_detail.side_effect = [
            [
                {
                    "url": "/subtitle/3363858",
                    "language": "English",
                    "release": "Thunderbolts.2025.1080p.WEBRip",
                    "hi": False,
                },
            ],
            [
                {
                    "url": "/subtitle/3339944",
                    "language": "English",
                    "release": "Dune.2021.1080p.BluRay",
                    "hi": False,
                },
            ],
        ]

        results = self.provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["url"], "/subtitle/3339944")
        self.assertIn("year", results[0]["matches"])
        self.assertEqual(mock_search.call_count, 2)

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_populates_episode_match_keys(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/12345", "title": "Breaking Bad"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Breaking.Bad.S01E05.720p",
                "hi": False,
            },
        ]

        results = self.provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 5},
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_skips_episode_candidates_without_episode_match(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/45027", "title": "Breaking Bad - No Half Measures"},
            {"url": "/subscene/154850", "title": "Breaking Bad"},
        ]
        mock_detail.side_effect = [
            [
                {
                    "url": "/subtitle/1",
                    "language": "English",
                    "release": "No Half Measures - Creating the Final Season of Breaking Bad",
                    "hi": False,
                },
            ],
            [
                {
                    "url": "/subtitle/2",
                    "language": "English",
                    "release": "Breaking.Bad.S01E01.720p.HDTV",
                    "hi": False,
                },
            ],
        ]

        results = self.provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 1},
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["url"], "/subtitle/2")
        self.assertIn("episode", results[0]["matches"])

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_allows_season_pack_episode_candidates(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/154850", "title": "Breaking Bad"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/season-pack",
                "language": "English",
                "release": "Breaking.Bad.S01.720p.BluRay",
                "hi": False,
            },
        ]

        results = self.provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 5},
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertIn("season", results[0]["matches"])
        self.assertNotIn("episode", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["url"], "/subtitle/season-pack")

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_deduplicates_after_match_filtering(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/154850", "title": "Breaking Bad"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Breaking.Bad.S02E05.720p.HDTV",
                "hi": False,
            },
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Breaking.Bad.S01E05.720p.HDTV",
                "hi": False,
            },
        ]

        results = self.provider.search(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 5},
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["release_info"], "Breaking.Bad.S01E05.720p.HDTV")
        self.assertEqual(results[0]["provider_payload"]["url"], "/subtitle/123")

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_deduplicates_repeated_subtitle_links(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/12345", "title": "Dune (2021)"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Dune.2021.1080p.BluRay",
                "hi": False,
            },
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Dune.2021.720p.WEB",
                "hi": False,
            },
        ]

        results = self.provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            ["eng"],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["url"], "/subtitle/123")

    @patch("provider._search_subscene")
    @patch("provider._get_detail_page")
    def test_search_passes_cloudflare_config_to_helpers(self, mock_detail, mock_search):
        mock_search.return_value = [
            {"url": "/subscene/12345", "title": "Dune (2021)"}
        ]
        mock_detail.return_value = [
            {
                "url": "/subtitle/123",
                "language": "English",
                "release": "Dune.2021.1080p.BluRay",
                "hi": False,
            },
        ]
        config = {
            "request_delay_ms": 0,
            "flaresolverr_url": "http://flaresolverr:8191/v1",
        }

        results = self.provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            ["eng"],
            config,
        )

        self.assertEqual(len(results), 1)
        mock_search.assert_called_once_with("Dune 2021", 0, config, self.provider._http_state)
        mock_detail.assert_called_once_with(
            "/subscene/12345",
            0,
            config,
            self.provider._http_state,
        )

    @patch("provider._search_subscene")
    def test_search_no_results(self, mock_search):
        mock_search.return_value = []

        video = {"kind": "movie", "title": "NonExistentMovie12345", "year": 2099}
        languages = ["eng"]
        config = {"request_delay_ms": 0}

        results = self.provider.search(video, languages, config)
        self.assertEqual(len(results), 0)

    @patch("provider._get_subtitle_detail")
    def test_download_returns_archive_payload(self, mock_detail):
        mock_detail.return_value = {
            "download_url": "/download/123",
            "title": "Dune (2021)",
            "release_info": ["Dune.2021.1080p.BluRay"],
            "hearing_impaired": False,
        }

        srt_content = b"1\n00:00:01,000 --> 00:00:02,000\nTest subtitle"
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("test.srt", srt_content)
        body = zip_buffer.getvalue()

        subtitle = {"url": "/subtitle/123", "video": {"kind": "movie", "title": "Dune", "year": 2021}}
        config = {"request_delay_ms": 0}

        with patch("provider._http_get", return_value=body):
            result = self.provider.download(subtitle, None, config)

        self.assertIsNotNone(result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "test.srt")
        self.assertFalse(result["empty"])
        # No worker-side extraction or encoding guessing.
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)


if __name__ == "__main__":
    unittest.main()
