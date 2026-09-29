import base64
import email.message
import hashlib
import http.client
import http.server
import importlib.util
import io
import socket
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subtitlecat"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subtitlecat_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildQueriesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_with_year_emits_precise_then_loose(self):
        queries = self.mod.build_queries(
            {"kind": "movie", "title": "Interstellar", "year": 2014}
        )
        self.assertEqual(queries, ["Interstellar 2014", "Interstellar"])

    def test_movie_without_year_emits_single_query(self):
        queries = self.mod.build_queries({"kind": "movie", "title": "Memento"})
        self.assertEqual(queries, ["Memento"])

    def test_episode_emits_precise_then_loose(self):
        queries = self.mod.build_queries(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2}
        )
        self.assertEqual(queries, ["Breaking Bad S01E02", "Breaking Bad"])

    def test_episode_pads_double_digit_numbers(self):
        queries = self.mod.build_queries(
            {"kind": "episode", "series": "Mr. Robot", "season": 10, "episode": 11}
        )
        self.assertEqual(queries, ["Mr. Robot S10E11", "Mr. Robot"])

    def test_unknown_kind_returns_empty(self):
        self.assertEqual(self.mod.build_queries({"kind": "trailer"}), [])

    def test_missing_required_fields_returns_empty(self):
        self.assertEqual(self.mod.build_queries({"kind": "movie"}), [])
        self.assertEqual(
            self.mod.build_queries({"kind": "episode", "series": "X"}), []
        )


SEARCH_FIXTURE = (FIXTURE_DIR / "subtitlecat_search_interstellar.html").read_bytes()
DETAIL_FIXTURE = (FIXTURE_DIR / "subtitlecat_detail_interstellar.html").read_bytes()


class ParseSearchResultsTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_returns_at_least_three_candidates(self):
        results = self.mod.parse_search_results(SEARCH_FIXTURE)
        self.assertGreaterEqual(len(results), 3)

    def test_each_candidate_has_expected_shape(self):
        results = self.mod.parse_search_results(SEARCH_FIXTURE)
        first = results[0]
        self.assertIn("detail_id", first)
        self.assertIn("detail_url", first)
        self.assertIn("title", first)
        self.assertTrue(
            first["detail_url"].startswith("https://www.subtitlecat.com/subs/")
        )
        self.assertTrue(first["detail_url"].endswith(".html"))
        self.assertRegex(first["detail_id"], r"^\d+$")
        self.assertTrue(first["title"])

    def test_candidates_are_unique_by_detail_id(self):
        results = self.mod.parse_search_results(SEARCH_FIXTURE)
        ids = [r["detail_id"] for r in results]
        self.assertEqual(len(ids), len(set(ids)))

    def test_handles_empty_or_unrelated_html(self):
        self.assertEqual(self.mod.parse_search_results(b"<html></html>"), [])
        self.assertEqual(self.mod.parse_search_results(b""), [])


class ParseDetailLanguagesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_extracts_download_anchors_only(self):
        source_lang, downloads = self.mod.parse_detail_languages(DETAIL_FIXTURE)
        self.assertIsInstance(downloads, dict)
        self.assertGreater(len(downloads), 0)
        for code, url in downloads.items():
            self.assertRegex(code, r"^[a-z]{2,3}$")
            self.assertTrue(url.startswith("https://www.subtitlecat.com/subs/"))
            # URL may end with the raw anchor id (e.g. ``-fil.srt``) while
            # the dict key is the canonical alpha2 (``tl``); we only assert
            # the file is an .srt under /subs/.
            self.assertTrue(url.endswith(".srt"))

    def test_excludes_translate_only_languages(self):
        # Languages with only <button id="xx">Translate</button> must not
        # appear in the downloads dict. Afrikaans (af) is translate-only in
        # the captured fixture.
        _, downloads = self.mod.parse_detail_languages(DETAIL_FIXTURE)
        self.assertNotIn("af", downloads)

    def test_detects_source_language_from_filename_hint(self):
        # The fixture's filename contains "English-orig.srt" so source is en.
        source_lang, _ = self.mod.parse_detail_languages(DETAIL_FIXTURE)
        self.assertEqual(source_lang, "en")

    def test_empty_html_returns_empty(self):
        source_lang, downloads = self.mod.parse_detail_languages(b"")
        self.assertIsNone(source_lang)
        self.assertEqual(downloads, {})


class HyphenatedLanguageDownloadTests(unittest.TestCase):
    """Regional tags such as zh-CN must not be silently dropped."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_regional_tag_collapses_to_base_alpha2(self):
        html = (
            b'<html><body>'
            b'<a id="download_zh-CN" href="/subs/1/foo-zh-CN.srt">Chinese (Simplified)</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("zh", downloads)
        self.assertTrue(downloads["zh"].endswith("foo-zh-CN.srt"))

    def test_plain_two_letter_codes_still_parsed(self):
        html = (
            b'<html><body>'
            b'<a id="download_en" href="/subs/2/bar-en.srt">English</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("en", downloads)

    def test_filipino_alpha3_canonicalises_to_tl(self):
        html = (
            b'<html><body>'
            b'<a id="download_fil" href="/subs/3/baz-fil.srt">Filipino</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("tl", downloads)
        self.assertNotIn("fil", downloads)

    def test_deprecated_hebrew_iw_canonicalises_to_he(self):
        html = (
            b'<html><body>'
            b'<a id="download_iw" href="/subs/4/foo-iw.srt">Hebrew</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("he", downloads)

    def test_exact_alpha2_overrides_earlier_regional_variant(self):
        # If a page lists both ``pt-BR`` and ``pt`` download anchors for the
        # same base language, the exact base anchor must be the one stored
        # in the downloads dict regardless of order.
        html = (
            b'<html><body>'
            b'<a id="download_pt-BR" href="/subs/1/foo-pt-BR.srt">PT-BR</a>'
            b'<a id="download_pt" href="/subs/1/foo-pt.srt">PT</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("pt", downloads)
        self.assertTrue(downloads["pt"].endswith("foo-pt.srt"))

    def test_exact_alpha2_wins_when_listed_after_regional(self):
        html = (
            b'<html><body>'
            b'<a id="download_pt" href="/subs/1/foo-pt.srt">PT</a>'
            b'<a id="download_pt-BR" href="/subs/1/foo-pt-BR.srt">PT-BR</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("pt", downloads)
        self.assertTrue(downloads["pt"].endswith("foo-pt.srt"))

    def test_only_regional_variant_present_first_wins(self):
        # Pre-existing behaviour: with no exact base anchor on the page, the
        # first regional variant for that base is the one kept.
        html = (
            b'<html><body>'
            b'<a id="download_pt-BR" href="/subs/1/foo-pt-BR.srt">PT-BR</a>'
            b'<a id="download_pt-PT" href="/subs/1/foo-pt-PT.srt">PT-PT</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("pt", downloads)
        self.assertTrue(downloads["pt"].endswith("foo-pt-BR.srt"))

    def test_alpha3_fre_canonicalises_to_fr(self):
        html = (
            b'<html><body>'
            b'<a id="download_fre" href="/subs/5/foo-fre.srt">French</a>'
            b'</body></html>'
        )
        _, downloads = self.mod.parse_detail_languages(html)
        self.assertIn("fr", downloads)


class NormalizationNonLatinTests(unittest.TestCase):
    """Non-Latin titles must survive normalization so matches still fire."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_cjk_title_tokens_preserved(self):
        tokens = self.mod._normalize_tokens("流浪地球")
        self.assertEqual(tokens, ["流浪地球"])

    def test_cjk_title_scores_full_match(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "流浪地球", "year": 2019},
            "流浪地球 2019 1080p BluRay x264",
        )
        self.assertEqual(score, 100)

    def test_cyrillic_series_match(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Кухня", "season": 1, "episode": 2},
            "Кухня S01E02 HDTV",
        )
        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_latin_diacritic_still_folded(self):
        # Existing behaviour: Café == cafe after NFKD + lowercase.
        tokens = self.mod._normalize_tokens("Café Society")
        self.assertEqual(tokens, ["cafe", "society"])


class ListValuedMetadataTests(unittest.TestCase):
    """Subliminal sometimes passes list-valued fields; the provider must
    coerce them to a string rather than crashing with ``TypeError: cannot
    use 'list' as a dict key``.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def test_audio_codec_as_list_does_not_raise(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Two Witches",
                "year": 2021,
                "audio_codec": ["DTS-HD", "MA"],
            },
            "Two.Witches.2021.BluRay.1080p.DTS-HD.MA.5.1.x264-MTeam",
        )
        self.assertIn("audio_codec", matches)

    def test_source_as_list_does_not_raise(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "X",
                "year": 2021,
                "source": ["Blu-ray"],
            },
            "X.2021.BluRay.1080p.x264",
        )
        self.assertIn("source", matches)

    def test_video_codec_as_list_does_not_raise(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "X",
                "year": 2021,
                "video_codec": ["H.264"],
            },
            "X.2021.x264",
        )
        self.assertIn("video_codec", matches)

    def test_title_as_list_does_not_raise_in_build_queries(self):
        # Defensive: even if title arrives wrapped in a list, build_queries
        # should not raise, it should just return an empty or coerced
        # result. The first element is what would typically be intended.
        queries = self.mod.build_queries(
            {"kind": "movie", "title": ["Interstellar"], "year": 2014}
        )
        self.assertEqual(queries, ["Interstellar 2014", "Interstellar"])


class UnpaddedEpisodeTagTests(unittest.TestCase):
    """Releases that emit ``S1E2`` (no zero padding) must still score."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_unpadded_episode_scores_95(self):
        score = self.mod.compute_score(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S1E2.1080p.BluRay.x265",
        )
        self.assertEqual(score, 95)

    def test_mixed_padding_scores_95(self):
        score = self.mod.compute_score(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S01E2.1080p.BluRay.x265",
        )
        self.assertEqual(score, 95)

    def test_derive_matches_picks_up_unpadded_season_and_episode(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S1E2.HDTV",
        )
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_season_match_does_not_fire_on_higher_season(self):
        # season=1 must not match when the release is S12E03.
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S12E03.HDTV",
        )
        self.assertNotIn("season", matches)
        self.assertNotIn("episode", matches)

    def test_episode_match_rejects_prefix_e2_inside_e20(self):
        # P1 from Codex: episode 2 must not match a release tagged S01E20.
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S01E20.1080p.WEB-DL",
        )
        self.assertNotIn("episode", matches)
        score = self.mod.compute_score(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S01E20.1080p.WEB-DL",
        )
        self.assertEqual(score, 85)  # series matched, not the episode tag

    def test_episode_match_rejects_prefix_e2_inside_e21_unpadded(self):
        # Also covers the unpadded boundary case ``S1E21`` -> not episode 2.
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking.Bad.S1E21.HDTV",
        )
        self.assertNotIn("episode", matches)


class OrigSourceDetectionTests(unittest.TestCase):
    """Source-language detection used to gate the include_machine_translated
    filter. Codex P2: also recognise ``-XX-orig.srt`` URLs that lack a named
    language token, otherwise MT filtering silently no-ops.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def test_named_language_orig_filename_detected(self):
        html = b'<a href="/subs/1/Foo.English-orig.srt">orig</a>'
        self.assertEqual(self.mod._detect_source_language(html), "en")

    def test_code_only_orig_filename_detected(self):
        html = b'<a href="/subs/1/Foo-en-orig.srt">orig</a>'
        self.assertEqual(self.mod._detect_source_language(html), "en")

    def test_regional_code_orig_filename_canonicalised(self):
        html = b'<a href="/subs/1/Foo-zh-CN-orig.srt">orig</a>'
        self.assertEqual(self.mod._detect_source_language(html), "zh")

    def test_deprecated_iw_orig_filename_mapped_to_he(self):
        html = b'<a href="/subs/1/Foo-iw-orig.srt">orig</a>'
        self.assertEqual(self.mod._detect_source_language(html), "he")

    def test_no_orig_marker_returns_none(self):
        self.assertIsNone(
            self.mod._detect_source_language(b'<a href="/subs/1/Foo.srt">x</a>')
        )


class ComputeScoreTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_title_plus_year_scores_100(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Interstellar", "year": 2014},
            "Interstellar.2014.1080p.BluRay.x264.YIFY",
        )
        self.assertEqual(score, 100)

    def test_movie_title_only_match_scores_90(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Interstellar", "year": 2014},
            "Interstellar (English)",
        )
        self.assertEqual(score, 90)

    def test_episode_with_tag_scores_95(self):
        score = self.mod.compute_score(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 1,
                "episode": 2,
            },
            "Breaking.Bad.S01E02.1080p.BluRay.x265",
        )
        self.assertEqual(score, 95)

    def test_episode_series_only_scores_85(self):
        score = self.mod.compute_score(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 1,
                "episode": 2,
            },
            "Breaking.Bad.S03E01.HDTV",
        )
        self.assertEqual(score, 85)

    def test_unrelated_title_scores_60(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Interstellar", "year": 2014},
            "Total.Recall.1990.720p.BluRay",
        )
        self.assertEqual(score, 60)


class DeriveMatchesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_emits_title_year_source_video_codec_for_full_match(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Interstellar",
                "year": 2014,
                "source": "Blu-ray",
                "video_codec": "H.264",
            },
            "Interstellar.2014.1080p.BluRay.x264.YIFY",
        )
        self.assertIn("title", matches)
        self.assertIn("year", matches)
        self.assertIn("source", matches)
        self.assertIn("video_codec", matches)

    def test_release_group_matches_via_multi_token_fallback(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Two Witches",
                "year": 2021,
                "release_group": "MTeam",
            },
            "Two.Witches.2021.BluRay.1080p.DTS-HD.MA.5.1.x264-MTeam",
        )
        self.assertIn("release_group", matches)

    def test_audio_codec_dts_hd_ma_matches_via_multi_token_fallback(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Two Witches",
                "year": 2021,
                "audio_codec": "DTS-HD MA",
            },
            "Two.Witches.2021.BluRay.1080p.DTS-HD.MA.5.1.x264-MTeam",
        )
        self.assertIn("audio_codec", matches)

    def test_audio_codec_canonical_dts_hd_uses_synonym_table(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Two Witches",
                "year": 2021,
                "audio_codec": "DTS-HD",
            },
            "Two.Witches.2021.BluRay.1080p.DTS-HD.MA.5.1.x264-MTeam",
        )
        self.assertIn("audio_codec", matches)

    def test_resolution_match_lowercased(self):
        matches = self.mod.derive_matches(
            {"kind": "movie", "title": "Two Witches", "resolution": "1080p"},
            "Two.Witches.2021.1080p.BluRay.x264-RARBG",
        )
        self.assertIn("resolution", matches)

    def test_release_group_does_not_match_when_absent(self):
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Two Witches",
                "release_group": "RARBG",
            },
            "Two.Witches.2021.BluRay.1080p.x264-MTeam",
        )
        self.assertNotIn("release_group", matches)

    def test_episode_emits_series_season_episode_and_tokens(self):
        matches = self.mod.derive_matches(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 1,
                "episode": 2,
                "source": "Blu-ray",
                "video_codec": "H.265",
            },
            "Breaking.Bad.S01E02.1080p.BluRay.x265",
        )
        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)
        self.assertIn("source", matches)
        self.assertIn("video_codec", matches)


class MultiTokenPresentTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_empty_value_is_false(self):
        tokens = self.mod._release_tokens("anything")
        self.assertFalse(self.mod._multi_token_present(tokens, ""))
        self.assertFalse(self.mod._multi_token_present(tokens, None))

    def test_all_chunks_present(self):
        tokens = self.mod._release_tokens("Two.Witches.DTS-HD.MA.1080p")
        self.assertTrue(self.mod._multi_token_present(tokens, "DTS-HD MA"))

    def test_missing_chunk_fails(self):
        tokens = self.mod._release_tokens("Two.Witches.AAC.1080p")
        self.assertFalse(self.mod._multi_token_present(tokens, "DTS-HD MA"))


class SubtitlecatProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_stub(self, responses):
        provider = self.mod.SubtitlecatProvider()
        called = []

        def stub(url, timeout=15):
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub  # noqa: SLF001 - test override
        return provider, called

    def test_search_results_include_page_link_for_clickthrough(self):
        search_url = (
            "https://www.subtitlecat.com/index.php?search=Interstellar%202014"
        )
        empty_detail = b"<html><body></body></html>"
        responses = {search_url: SEARCH_FIXTURE}
        for entry in self.mod.parse_search_results(SEARCH_FIXTURE):
            responses[entry["detail_url"]] = empty_detail
        detail_url = (
            "https://www.subtitlecat.com/subs/1459/"
            "Interstellar_2014_Bluray_720p_AAC_HEVC_x265.English.html"
        )
        responses[detail_url] = DETAIL_FIXTURE

        provider, _ = self._provider_with_stub(responses)
        results = provider.search(
            video={"kind": "movie", "title": "Interstellar", "year": 2014},
            languages=[
                {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}
            ],
            config={"include_machine_translated": True, "request_delay_ms": 0},
        )
        self.assertGreater(len(results), 0)
        for item in results:
            self.assertIn("page_link", item)
            self.assertTrue(item["page_link"].startswith("https://www.subtitlecat.com/subs/"))
            self.assertEqual(item["page_link"], item["display"]["detail_url"])

    def test_search_returns_only_languages_with_download_anchors(self):
        search_url = (
            "https://www.subtitlecat.com/index.php?search=Interstellar%202014"
        )
        empty_detail = b"<html><body></body></html>"
        responses = {search_url: SEARCH_FIXTURE}
        mod = self.mod
        for entry in mod.parse_search_results(SEARCH_FIXTURE):
            responses[entry["detail_url"]] = empty_detail
        # Override the one detail URL we have a real fixture for.
        detail_url = (
            "https://www.subtitlecat.com/subs/1459/"
            "Interstellar_2014_Bluray_720p_AAC_HEVC_x265.English.html"
        )
        responses[detail_url] = DETAIL_FIXTURE

        provider, _ = self._provider_with_stub(responses)
        results = provider.search(
            video={"kind": "movie", "title": "Interstellar", "year": 2014},
            languages=[
                {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}
            ],
            config={"include_machine_translated": True, "request_delay_ms": 0},
        )
        self.assertGreater(len(results), 0)
        for item in results:
            self.assertEqual(item["provider"], "subtitlecat")
            self.assertEqual(item["language"]["alpha3"], "eng")
            self.assertTrue(
                item["provider_payload"]["subtitle_url"].endswith("-en.srt")
            )
            self.assertGreaterEqual(item["score"], 90)

    def test_search_falls_back_to_loose_query_on_zero_hits(self):
        empty_search = b"<html><body>No results</body></html>"
        precise = (
            "https://www.subtitlecat.com/index.php?search=Obscure%20Film%201999"
        )
        loose = "https://www.subtitlecat.com/index.php?search=Obscure%20Film"
        responses = {precise: empty_search, loose: empty_search}
        provider, called = self._provider_with_stub(responses)
        results = provider.search(
            video={"kind": "movie", "title": "Obscure Film", "year": 1999},
            languages=[{"alpha3": "eng", "alpha2": "en"}],
            config={"include_machine_translated": True, "request_delay_ms": 0},
        )
        self.assertEqual(results, [])
        self.assertEqual(called, [precise, loose])

    def test_search_skips_machine_translated_when_flag_off(self):
        search_url = (
            "https://www.subtitlecat.com/index.php?search=Interstellar%202014"
        )
        loose_url = (
            "https://www.subtitlecat.com/index.php?search=Interstellar"
        )
        detail_url = (
            "https://www.subtitlecat.com/subs/1459/"
            "Interstellar_2014_Bluray_720p_AAC_HEVC_x265.English.html"
        )
        empty_detail = b"<html><body></body></html>"
        empty_search = b"<html><body>No results</body></html>"
        responses = {
            search_url: SEARCH_FIXTURE,
            loose_url: empty_search,
            detail_url: DETAIL_FIXTURE,
        }
        for entry in self.mod.parse_search_results(SEARCH_FIXTURE):
            responses.setdefault(entry["detail_url"], empty_detail)

        provider, _ = self._provider_with_stub(responses)
        # Requesting Spanish: the fixture's source is English, so Spanish
        # download anchors (if present) would be machine-translated. With the
        # flag off, the precise query yields zero usable results, so the
        # provider falls back to the loose query which here also returns
        # nothing.
        results = provider.search(
            video={"kind": "movie", "title": "Interstellar", "year": 2014},
            languages=[{"alpha3": "spa", "alpha2": "es"}],
            config={"include_machine_translated": False, "request_delay_ms": 0},
        )
        for item in results:
            self.assertEqual(item["language"]["alpha3"], "spa")
        self.assertEqual(results, [])

    def test_search_dedup_runs_before_per_query_cap(self):
        # Precise page returns one row; the loose page repeats that row plus
        # MAX_CANDIDATES_PER_QUERY-1 new ones. With dedup applied before the
        # cap, the loose pass should still surface a new candidate.
        precise_url = (
            "https://www.subtitlecat.com/index.php?search=Foo%20Bar%202024"
        )
        loose_url = "https://www.subtitlecat.com/index.php?search=Foo%20Bar"
        precise_html = (
            b'<html><body>'
            b'<a href="/subs/1/Foo_Bar_2024_A.html">Foo Bar 2024 A</a>'
            b'</body></html>'
        )
        rows = b'<a href="/subs/1/Foo_Bar_2024_A.html">Foo Bar 2024 A</a>'
        for n in range(self.mod.MAX_CANDIDATES_PER_QUERY):
            rows += (
                f'<a href="/subs/{100 + n}/Foo_Bar_loose_{n}.html">L{n}</a>'
                .encode()
            )
        loose_html = b'<html><body>' + rows + b'</body></html>'
        empty_detail = b'<html><body></body></html>'
        # Detail-page response that has a real English download anchor so
        # at least one candidate produces a usable result on each pass.
        detail_with_en = (
            b'<html><body>'
            b'<a id="download_en" href="/subs/100/Foo-en.srt">EN</a>'
            b'English-orig.srt'
            b'</body></html>'
        )
        responses = {
            precise_url: precise_html,
            loose_url: loose_html,
            # Precise page's single candidate has no usable language, so we
            # know the loose-fallback ran for the assertion.
            "https://www.subtitlecat.com/subs/1/Foo_Bar_2024_A.html": empty_detail,
            "https://www.subtitlecat.com/subs/100/Foo_Bar_loose_0.html": detail_with_en,
        }
        for n in range(1, self.mod.MAX_CANDIDATES_PER_QUERY):
            responses[
                f"https://www.subtitlecat.com/subs/{100 + n}/Foo_Bar_loose_{n}.html"
            ] = empty_detail
        provider, _ = self._provider_with_stub(responses)
        results = provider.search(
            video={"kind": "movie", "title": "Foo Bar", "year": 2024},
            languages=[{"alpha3": "eng", "alpha2": "en"}],
            config={"include_machine_translated": True, "request_delay_ms": 0},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha2"], "en")

    def test_search_continues_when_a_detail_page_raises(self):
        search_url = (
            "https://www.subtitlecat.com/index.php?search=Alpha%202024"
        )
        search_html = (
            b'<html><body>'
            b'<a href="/subs/700/Alpha_2024_BAD.html">Alpha 2024 BAD</a>'
            b'<a href="/subs/701/Alpha_2024_OK.html">Alpha 2024 OK</a>'
            b'</body></html>'
        )
        good_detail = (
            b'<html><body>'
            b'<a id="download_en" href="/subs/701/alpha-en.srt">EN</a>'
            b'English-orig.srt'
            b'</body></html>'
        )
        responses = {
            search_url: search_html,
            "https://www.subtitlecat.com/subs/701/Alpha_2024_OK.html": good_detail,
            # /subs/700/... intentionally absent so the stub raises on it.
        }

        provider = self.mod.SubtitlecatProvider()
        called = []

        def stub(url, timeout=15):
            called.append(url)
            if url in responses:
                return responses[url]
            raise OSError("boom")

        provider._http_get = stub  # noqa: SLF001
        results = provider.search(
            video={"kind": "movie", "title": "Alpha", "year": 2024},
            languages=[{"alpha3": "eng", "alpha2": "en"}],
            config={"include_machine_translated": True, "request_delay_ms": 0},
        )
        # The first candidate raised; the search must still surface the
        # second candidate's results instead of bubbling the exception.
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha2"], "en")

    def test_search_falls_back_when_precise_results_all_filtered(self):
        precise_url = (
            "https://www.subtitlecat.com/index.php?search=Movie%20X%202021"
        )
        loose_url = (
            "https://www.subtitlecat.com/index.php?search=Movie%20X"
        )
        precise_search = (
            b'<html><body>'
            b'<a href="/subs/9001/Movie_X_2021.English.html">Movie X 2021</a>'
            b"</body></html>"
        )
        # Precise candidate's detail page has only an English download anchor,
        # but the request is for German with MT off and source=en, so this
        # precise candidate gets filtered. The loose query returns a candidate
        # that actually has a German download.
        precise_detail = (
            b'<html><body>'
            b'<a id="download_en" href="/subs/9001/movie-x-en.srt">EN</a>'
            b'English-orig.srt'
            b"</body></html>"
        )
        loose_search = (
            b'<html><body>'
            b'<a href="/subs/9002/Movie_X_2021_de.html">Movie X 2021 German</a>'
            b"</body></html>"
        )
        loose_detail = (
            b'<html><body>'
            b'<a id="download_de" href="/subs/9002/movie-x-de.srt">DE</a>'
            b'German-orig.srt'
            b"</body></html>"
        )
        responses = {
            precise_url: precise_search,
            loose_url: loose_search,
            "https://www.subtitlecat.com/subs/9001/Movie_X_2021.English.html": precise_detail,
            "https://www.subtitlecat.com/subs/9002/Movie_X_2021_de.html": loose_detail,
        }
        provider, called = self._provider_with_stub(responses)
        results = provider.search(
            video={"kind": "movie", "title": "Movie X", "year": 2021},
            languages=[{"alpha3": "deu", "alpha2": "de"}],
            config={"include_machine_translated": False, "request_delay_ms": 0},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha2"], "de")
        # Both precise and loose search URLs must have been called.
        self.assertIn(precise_url, called)
        self.assertIn(loose_url, called)


class _FakeClock:
    """A monotonic clock the tests move by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _TimedSite:
    """Serves canned pages, each one taking a set time on the fake clock.

    A page slower than the request's timeout spends the whole timeout and then
    raises, the way a socket read does. A URL mapped to an exception raises it.
    """

    def __init__(self, clock, pages, latency=None, default_latency=1.0):
        self.clock = clock
        self.pages = pages
        self.latency = latency or {}
        self.default_latency = default_latency
        self.calls = []

    def get(self, url, timeout=15):
        self.calls.append({"url": url, "timeout": timeout, "started": self.clock()})
        seconds = self.latency.get(url, self.default_latency)
        if seconds > timeout:
            self.clock.advance(timeout)
            raise TimeoutError("timed out")
        self.clock.advance(seconds)
        if url not in self.pages:
            raise AssertionError(f"unexpected URL: {url}")
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    def urls(self):
        return [call["url"] for call in self.calls]



class _Drip:
    """A 200 body sent in `pieces` parts, each arriving `seconds` after the last."""

    def __init__(self, body, pieces, seconds):
        size = max(1, -(-len(body) // pieces))
        self.pieces = [body[start:start + size] for start in range(0, len(body), size)]
        self.seconds = seconds


class _FakeSocket:
    """The socket behind a response, keeping the read timeout set on it."""

    def __init__(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value


class _DrippingBody:
    """A response body that arrives one piece at a time on the fake clock.

    Each piece comes inside the socket's read timeout, so no single read times
    out however long the whole body takes, the way a server trickling bytes
    behaves. A piece slower than the socket's current timeout spends that
    timeout and raises, as a real socket read does. The socket sits where
    http.client keeps it, behind the response's file.
    """

    closed = False

    def __init__(self, clock, drip, timeout):
        self.clock = clock
        self.pieces = list(drip.pieces)
        self.seconds = drip.seconds
        self.raw = types.SimpleNamespace(_sock=_FakeSocket(timeout))

    def read1(self, size=-1):
        if not self.pieces:
            return b""
        sock = self.raw._sock
        if sock.timeout is not None and self.seconds > sock.timeout:
            self.clock.advance(sock.timeout)
            raise socket.timeout("timed out")
        self.clock.advance(self.seconds)
        return self.pieces.pop(0)

    def read(self, size=-1):
        body = b""
        piece = self.read1()
        while piece:
            body += piece
            piece = self.read1()
        return body

    def close(self):
        self.closed = True


class _TimedHost(urllib.request.BaseHandler):
    """Answers https requests on the fake clock, behind urllib's own redirects.

    Added to the provider's real opener, ahead of its HTTPS handler. A route is
    (seconds, status, value): value is a redirect's Location, a 200's body, or
    a _Drip for a 200 whose body arrives in timed pieces. A redirect whose own
    body trickles in takes (Location, _Drip). A route slower than
    the request's timeout spends the whole timeout and then raises, the way a
    socket read does.
    """

    handler_order = 100

    def __init__(self, clock, routes):
        self.clock = clock
        self.routes = routes
        self.calls = []

    def https_open(self, request):
        url = request.full_url
        self.calls.append({"url": url, "timeout": request.timeout, "started": self.clock()})
        seconds, status, value = self.routes[url]
        if seconds > request.timeout:
            self.clock.advance(request.timeout)
            raise socket.timeout("timed out")
        self.clock.advance(seconds)
        headers = email.message.Message()
        if isinstance(value, _Drip):
            body = _DrippingBody(self.clock, value, request.timeout)
        elif status != 200 and isinstance(value, tuple):
            # A redirect whose own body trickles in: (Location, _Drip).
            headers["Location"] = value[0]
            body = _DrippingBody(self.clock, value[1], request.timeout)
        elif status != 200:
            headers["Location"] = value
            body = io.BytesIO(b"")
        else:
            body = io.BytesIO(value)
        response = urllib.response.addinfourl(body, headers, url, status)
        response.msg = "OK" if status == 200 else "Moved"
        return response

    def urls(self):
        return [call["url"] for call in self.calls]

class _FakeConnectedSocket:
    """A connected socket on the fake clock, keeping the timeout set on it.

    The response's first bytes take `response_seconds` to arrive. A wait
    longer than the socket's timeout spends the timeout and raises, as a
    real socket read does.
    """

    def __init__(self, clock, timeout, response_seconds):
        self.clock = clock
        self.timeout = timeout
        self.response_seconds = response_seconds
        self.timeout_when_read = None

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return self.timeout

    def setsockopt(self, *args):
        pass

    def sendall(self, data):
        pass

    def makefile(self, mode):
        return _FirstByteWait(self, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    def close(self):
        pass



class _FirstByteWait(io.BytesIO):
    """A response file whose first line waits on its socket's timeout."""

    def __init__(self, sock, body):
        super().__init__(body)
        self.sock = sock
        self.waited = False

    def readline(self, size=-1):
        if not self.waited:
            self.waited = True
            sock = self.sock
            sock.timeout_when_read = sock.timeout
            if sock.timeout is not None and sock.response_seconds > sock.timeout:
                sock.clock.advance(sock.timeout)
                raise socket.timeout("timed out")
            sock.clock.advance(sock.response_seconds)
        return super().readline(size)

class _FakeTLSContext:
    """Stands in for ssl.SSLContext: the handshake takes `seconds` on the fake clock."""

    def __init__(self, clock, seconds):
        self.clock = clock
        self.seconds = seconds
        self.handshake_timeouts = []

    def wrap_socket(self, sock, server_hostname=None):
        timeout = sock.gettimeout()
        self.handshake_timeouts.append(timeout)
        if timeout is not None and self.seconds > timeout:
            self.clock.advance(timeout)
            raise socket.timeout("_ssl.c: The handshake operation timed out")
        self.clock.advance(self.seconds)
        return sock


class _StallingServer:
    """A local HTTP server: /fast answers at once, /slow holds the request."""

    def __init__(self):
        release = self.release = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/slow":
                    release.wait(5)
                body = b"<html>ok</html>"
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The client gave up at its deadline.

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

def _search_page(prefix, first_id, count):
    rows = b"".join(
        f'<a href="/subs/{first_id + n}/{prefix}_{n}.html">{prefix} {n}</a>'.encode()
        for n in range(count)
    )
    return b"<html><body>" + rows + b"</body></html>"


def _detail_page(detail_id, alpha2):
    if alpha2 is None:
        return b"<html><body></body></html>"
    return (
        f'<html><body><a id="download_{alpha2}" '
        f'href="/subs/{detail_id}/sub-{alpha2}.srt">{alpha2}</a>'
        f"English-orig.srt</body></html>"
    ).encode()


class SubtitlecatSearchBudgetTests(unittest.TestCase):
    EPISODE = {"kind": "episode", "series": "Lioness", "season": 3, "episode": 8}
    PRECISE = "https://www.subtitlecat.com/index.php?search=Lioness%20S03E08"
    LOOSE = "https://www.subtitlecat.com/index.php?search=Lioness"
    ENGLISH = [{"alpha3": "eng", "alpha2": "en"}]
    CONFIG = {"include_machine_translated": True, "request_delay_ms": 0}

    def setUp(self):
        self.mod = _load_provider_module()
        self.clock = _FakeClock()
        self.started = self.clock()
        self.deadline = self.started + self.mod.SEARCH_BUDGET_SECONDS

    def _site(self, precise_count, precise_alpha2, loose_count=0, loose_alpha2="en", **kwargs):
        pages = {
            self.PRECISE: _search_page("Lioness_S03E08", 100, precise_count),
            self.LOOSE: _search_page("Lioness", 200, loose_count),
        }
        for n in range(precise_count):
            url = f"https://www.subtitlecat.com/subs/{100 + n}/Lioness_S03E08_{n}.html"
            pages[url] = _detail_page(100 + n, precise_alpha2)
        for n in range(loose_count):
            url = f"https://www.subtitlecat.com/subs/{200 + n}/Lioness_{n}.html"
            pages[url] = _detail_page(200 + n, loose_alpha2)
        return _TimedSite(self.clock, pages, **kwargs)

    def _provider(self, site):
        provider = self.mod.SubtitlecatProvider()
        provider._monotonic = self.clock  # noqa: SLF001 - test clock
        provider._http_get = site.get  # noqa: SLF001 - test override
        return provider

    def _assert_requests_fit_the_budget(self, site):
        self.assertLessEqual(self.clock() - self.started, self.mod.SEARCH_BUDGET_SECONDS)
        for call in site.calls:
            remaining = self.deadline - call["started"]
            self.assertGreater(remaining, 0, call["url"])
            self.assertGreater(call["timeout"], 0, call["url"])
            self.assertLessEqual(call["timeout"], remaining, call["url"])
            self.assertLessEqual(call["timeout"], self.mod.HTTP_TIMEOUT_SECONDS, call["url"])

    def test_budget_ends_inside_the_hosts_per_provider_limit(self):
        # Discover stops waiting at 20 seconds on an install set to the older
        # limit, and a real request runs a second or two past its socket
        # timeout because DNS, connect and TLS are timed separately.
        self.assertLessEqual(self.mod.SEARCH_BUDGET_SECONDS, 15)

    def test_timed_out_covers_read_and_connect_timeouts_only(self):
        timed_out = self.mod._timed_out  # noqa: SLF001
        self.assertTrue(timed_out(TimeoutError("timed out")))
        self.assertTrue(timed_out(socket.timeout("timed out")))
        self.assertTrue(timed_out(urllib.error.URLError(TimeoutError("timed out"))))
        self.assertFalse(timed_out(urllib.error.URLError(ConnectionRefusedError())))
        self.assertFalse(
            timed_out(urllib.error.HTTPError("https://x", 503, "Busy", None, None))
        )
        self.assertFalse(timed_out(ValueError("bad page")))

    def test_search_stops_at_the_budget_and_returns_partial_results(self):
        site = self._site(precise_count=10, precise_alpha2="en", default_latency=2.0)
        results = self._provider(site).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self._assert_requests_fit_the_budget(site)
        detail_calls = site.calls[1:]
        answered = [
            call["url"] for call in detail_calls if call["timeout"] >= 2.0
        ]
        # Every page that answered in time produced its result, and the search
        # stopped before reaching the last candidates.
        self.assertTrue(results)
        self.assertLess(len(detail_calls), 10)
        self.assertEqual([item["page_link"] for item in results], answered)
        self.assertNotIn(self.LOOSE, site.urls())

    def test_search_skips_the_loose_query_when_the_budget_is_spent(self):
        site = self._site(precise_count=10, precise_alpha2="de", loose_count=3, default_latency=2.0)
        results = self._provider(site).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual(results, [])
        self.assertNotIn(self.LOOSE, site.urls())
        self._assert_requests_fit_the_budget(site)

    def test_measured_lioness_search_returns_inside_the_budget(self):
        # Latencies measured against the live site: three precise hits without
        # the requested language, then a loose page that fills the cap.
        latency = {
            self.PRECISE: 0.51,
            self.LOOSE: 1.68,
            "https://www.subtitlecat.com/subs/100/Lioness_S03E08_0.html": 2.40,
            "https://www.subtitlecat.com/subs/101/Lioness_S03E08_1.html": 0.67,
            "https://www.subtitlecat.com/subs/102/Lioness_S03E08_2.html": 0.57,
        }
        site = self._site(
            precise_count=3,
            precise_alpha2="de",
            loose_count=self.mod.MAX_CANDIDATES_PER_QUERY,
            latency=latency,
            default_latency=1.21,
        )
        results = self._provider(site).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self._assert_requests_fit_the_budget(site)
        self.assertIn(self.LOOSE, site.urls())
        self.assertTrue(results)
        self.assertTrue(all(item["language"]["alpha2"] == "en" for item in results))

    def test_loose_search_page_timing_out_ends_with_nothing_found(self):
        site = self._site(
            precise_count=2,
            precise_alpha2="de",
            loose_count=2,
            latency={self.LOOSE: 60.0},
        )
        results = self._provider(site).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual(results, [])
        self.assertEqual(site.urls()[-1], self.LOOSE)
        self._assert_requests_fit_the_budget(site)

    def test_loose_search_page_failing_another_way_still_raises(self):
        # Only running out of time ends the loose query quietly. A server
        # error or a refused connection there is still the site failing.
        for error in (
            urllib.error.HTTPError(self.LOOSE, 500, "Server Error", None, None),
            urllib.error.URLError(ConnectionRefusedError()),
        ):
            with self.subTest(error=error):
                site = self._site(precise_count=2, precise_alpha2="de", loose_count=2)
                site.pages[self.LOOSE] = error

                with self.assertRaises(type(error)):
                    self._provider(site).search(self.EPISODE, self.ENGLISH, self.CONFIG)
                self.assertEqual(site.urls()[-1], self.LOOSE)

    def test_first_search_page_timing_out_still_raises(self):
        site = self._site(precise_count=2, precise_alpha2="en", latency={self.PRECISE: 60.0})

        with self.assertRaises(TimeoutError):
            self._provider(site).search(self.EPISODE, self.ENGLISH, self.CONFIG)
        self.assertEqual(site.urls(), [self.PRECISE])
        self._assert_requests_fit_the_budget(site)

    def test_request_delay_never_sleeps_past_the_budget(self):
        site = self._site(precise_count=10, precise_alpha2="en", default_latency=1.0)
        provider = self._provider(site)
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            self.clock.advance(seconds)

        config = dict(self.CONFIG, request_delay_ms=5000)
        with mock.patch.object(self.mod.time, "sleep", side_effect=fake_sleep):
            results = provider.search(self.EPISODE, self.ENGLISH, config)

        self.assertTrue(results)
        self.assertTrue(sleeps)
        self._assert_requests_fit_the_budget(site)


class SubtitlecatResponseDeadlineTests(unittest.TestCase):
    """The real _http_get, behind urllib's redirects, on the fake clock."""

    EPISODE = {"kind": "episode", "series": "Lioness", "season": 3, "episode": 8}
    PRECISE = "https://www.subtitlecat.com/index.php?search=Lioness%20S03E08"
    ENGLISH = [{"alpha3": "eng", "alpha2": "en"}]
    CONFIG = {"include_machine_translated": True, "request_delay_ms": 0}
    SUBTITLE = "1\n00:00:01,000 --> 00:00:02,500\nHello world.\n".encode("utf-8")

    def setUp(self):
        self.mod = _load_provider_module()
        self.clock = _FakeClock()
        self.started = self.clock()

    def _provider_behind(self, host):
        provider = self.mod.SubtitlecatProvider()
        provider._monotonic = self.clock  # noqa: SLF001 - test clock
        provider._opener.add_handler(host)  # noqa: SLF001 - fake site
        return provider

    def _detail_url(self, n):
        return f"https://www.subtitlecat.com/subs/{100 + n}/Lioness_S03E08_{n}.html"

    def _assert_inside_the_budget(self):
        self.assertLessEqual(self.clock() - self.started, self.mod.SEARCH_BUDGET_SECONDS)

    def test_search_page_trickling_in_ends_by_the_deadline(self):
        # A socket timeout bounds each read, not the body, so a page whose
        # bytes keep arriving inside it must still end at the budget.
        page = _search_page("Lioness_S03E08", 100, 2)
        host = _TimedHost(self.clock, {self.PRECISE: (1.0, 200, _Drip(page, 30, 4.0))})

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual(host.urls(), [self.PRECISE])
        self._assert_inside_the_budget()

    def test_detail_page_trickling_in_keeps_the_finished_results(self):
        host = _TimedHost(
            self.clock,
            {
                self.PRECISE: (0.5, 200, _search_page("Lioness_S03E08", 100, 2)),
                self._detail_url(0): (1.0, 200, _detail_page(100, "en")),
                self._detail_url(1): (1.0, 200, _Drip(_detail_page(101, "en"), 30, 4.0)),
            },
        )

        results = self._provider_behind(host).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual([item["page_link"] for item in results], [self._detail_url(0)])
        self.assertEqual(host.urls(), [self.PRECISE, self._detail_url(0), self._detail_url(1)])
        self._assert_inside_the_budget()

    def test_search_redirect_hop_stays_inside_the_budget(self):
        # urllib opens a redirect's target with the first hop's timeout, so a
        # slow redirect followed by a silent page must not get a second full
        # wait.
        target = "https://www.subtitlecat.com/index.php?search=Lioness+S03E08"
        host = _TimedHost(
            self.clock,
            {self.PRECISE: (10.0, 301, target), target: (600.0, 200, b"")},
        )

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual(host.urls(), [self.PRECISE, target])
        deadline = self.started + self.mod.SEARCH_BUDGET_SECONDS
        self.assertLessEqual(host.calls[1]["timeout"], deadline - host.calls[1]["started"])
        self._assert_inside_the_budget()

    def test_redirect_body_trickling_in_ends_by_the_deadline(self):
        # urllib reads a redirect's own body before following it, with the
        # first hop's socket timeout, so that body must end at the budget too.
        target = "https://www.subtitlecat.com/index.php?search=Lioness+S03E08"
        host = _TimedHost(
            self.clock,
            {
                self.PRECISE: (1.0, 301, (target, _Drip(b"<html>moved</html>" * 20, 30, 4.0))),
                target: (0.5, 200, _search_page("Lioness_S03E08", 100, 1)),
            },
        )

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual(host.urls(), [self.PRECISE])
        self._assert_inside_the_budget()

    def test_redirect_body_inside_the_budget_is_followed(self):
        target = "https://www.subtitlecat.com/index.php?search=Lioness+S03E08"
        host = _TimedHost(
            self.clock,
            {
                self.PRECISE: (0.5, 301, (target, _Drip(b"<html>moved</html>", 3, 0.5))),
                target: (0.5, 200, _search_page("Lioness_S03E08", 100, 1)),
                self._detail_url(0): (0.5, 200, _detail_page(100, "en")),
            },
        )

        results = self._provider_behind(host).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual([item["page_link"] for item in results], [self._detail_url(0)])
        self.assertEqual(host.urls(), [self.PRECISE, target, self._detail_url(0)])

    def test_page_sent_in_pieces_inside_the_budget_is_read_whole(self):
        host = _TimedHost(
            self.clock,
            {
                self.PRECISE: (0.5, 200, _Drip(_search_page("Lioness_S03E08", 100, 1), 4, 1.0)),
                self._detail_url(0): (0.5, 200, _Drip(_detail_page(100, "en"), 3, 1.0)),
            },
        )

        results = self._provider_behind(host).search(self.EPISODE, self.ENGLISH, self.CONFIG)

        self.assertEqual([item["page_link"] for item in results], [self._detail_url(0)])

    def test_download_trickling_in_ends_inside_its_timeout(self):
        url = "https://www.subtitlecat.com/subs/1/x-en.srt"
        host = _TimedHost(self.clock, {url: (1.0, 200, _Drip(self.SUBTITLE * 20, 40, 5.0))})
        payload = {"provider": "subtitlecat", "schema": 1, "subtitle_url": url, "language": "eng"}

        with self.assertRaises(TimeoutError):
            self._provider_behind(host).download(payload, self.ENGLISH[0], {})

        self.assertLessEqual(self.clock() - self.started, self.mod.HTTP_TIMEOUT_SECONDS)

    def test_download_reads_the_whole_file(self):
        url = "https://www.subtitlecat.com/subs/1/x-en.srt"
        host = _TimedHost(self.clock, {url: (0.5, 200, _Drip(self.SUBTITLE, 3, 0.5))})
        payload = {"provider": "subtitlecat", "schema": 1, "subtitle_url": url, "language": "eng"}

        result = self._provider_behind(host).download(payload, self.ENGLISH[0], {})

        self.assertEqual(base64.b64decode(result["content_b64"]), self.SUBTITLE)


class SubtitlecatConnectionDeadlineTests(unittest.TestCase):
    """Connecting, the TLS handshake and the wait for the response each get
    only what is left before the deadline, not a fresh full timeout."""

    HOST = "www.subtitlecat.com"

    def setUp(self):
        self.mod = _load_provider_module()
        self.clock = _FakeClock()
        self.started = self.clock()
        self.deadline = self.started + 15.0

    def _connection(self, connect_seconds, handshake_seconds, response_seconds):
        cls = self.mod._deadline_connection(http.client.HTTPSConnection, self.deadline, self.clock)
        self.tls = _FakeTLSContext(self.clock, handshake_seconds)
        connection = cls(self.HOST, timeout=15.0, context=self.tls)
        self.connect_timeouts = []
        self.sockets = []

        def fake_create_connection(address, timeout=None, source_address=None):
            self.connect_timeouts.append(timeout)
            if connect_seconds > timeout:
                self.clock.advance(timeout)
                raise socket.timeout("timed out")
            self.clock.advance(connect_seconds)
            sock = _FakeConnectedSocket(self.clock, timeout, response_seconds)
            self.sockets.append(sock)
            return sock

        connection._create_connection = fake_create_connection
        return connection

    def _get(self, connection):
        connection.request("GET", "/search")
        return connection.getresponse().read()

    def test_each_step_gets_only_the_time_left(self):
        connection = self._connection(2.0, 5.0, 6.0)

        self.assertEqual(self._get(connection), b"ok")

        self.assertEqual(self.connect_timeouts, [15.0])
        self.assertEqual(self.tls.handshake_timeouts, [13.0])
        self.assertEqual(self.sockets[0].timeout_when_read, 8.0)

    def test_slow_handshake_then_slow_response_ends_by_the_deadline(self):
        # Measured on the GreekSubtitles origin: a TLS handshake of five to
        # six seconds, before the page itself. Each wait fits a 15 second
        # timeout, but together they ran past the deadline.
        connection = self._connection(1.0, 6.0, 10.0)

        with self.assertRaises(TimeoutError):
            self._get(connection)

        self.assertLessEqual(self.clock() - self.started, 15.0)

    def test_no_time_left_after_connecting_stops_before_the_handshake(self):
        connection = self._connection(15.0, 1.0, 1.0)

        with self.assertRaises(TimeoutError):
            self._get(connection)

        self.assertEqual(self.tls.handshake_timeouts, [])

    def test_requests_with_a_deadline_open_deadline_connections(self):
        provider = self.mod.SubtitlecatProvider()
        for scheme, base in (("https", http.client.HTTPSConnection), ("http", http.client.HTTPConnection)):
            with self.subTest(scheme=scheme):
                handler = next(
                    item for item in provider._opener.handlers if hasattr(item, scheme + "_open")
                )
                request = urllib.request.Request(f"{scheme}://{self.HOST}/")
                with mock.patch.object(handler, "do_open", return_value="response") as do_open:
                    getattr(handler, scheme + "_open")(request)
                    self.assertIs(do_open.call_args.args[0], base)
                    request.deadline = self.deadline
                    getattr(handler, scheme + "_open")(request)
                    opened = do_open.call_args.args[0]
                self.assertTrue(issubclass(opened, base))
                self.assertIsNot(opened, base)


class SubtitlecatLocalServerDeadlineTests(unittest.TestCase):
    """The real opener and http.client against a local server."""

    def setUp(self):
        self.mod = _load_provider_module()
        self.server = _StallingServer()
        self.addCleanup(self.server.close)

    def test_answer_inside_the_deadline_is_read(self):
        provider = self.mod.SubtitlecatProvider()

        self.assertEqual(provider._http_get(self.server.url + "/fast", timeout=5), b"<html>ok</html>")

    def test_held_request_ends_at_the_deadline(self):
        provider = self.mod.SubtitlecatProvider()
        started = time.monotonic()

        with self.assertRaises((TimeoutError, urllib.error.URLError)) as caught:
            provider._http_get(self.server.url + "/slow", timeout=0.3)

        self.assertTrue(self.mod._timed_out(caught.exception))
        self.assertLess(time.monotonic() - started, 2.0)


class SubtitlecatProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_body(self, body):
        provider = self.mod.SubtitlecatProvider()
        provider._http_get = lambda url, timeout=15: body  # noqa: SLF001
        return provider

    def test_download_returns_base64_plus_sha256_for_utf8_body(self):
        body = "1\n00:00:01,000 --> 00:00:02,500\nHello world.\n".encode("utf-8")
        provider = self._provider_with_body(body)
        result = provider.download(
            provider_payload={
                "provider": "subtitlecat",
                "schema": 1,
                "subtitle_url": "https://www.subtitlecat.com/subs/1/x-en.srt",
                "language": "eng",
            },
            language={"alpha3": "eng", "alpha2": "en"},
            config={},
        )
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertEqual(result["encoding"], "utf-8")
        self.assertFalse(result["empty"])
        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(
            result["content_sha256"], hashlib.sha256(body).hexdigest()
        )

    def test_download_marks_empty_body(self):
        provider = self._provider_with_body(b"")
        result = provider.download(
            provider_payload={"provider": "subtitlecat", "subtitle_url": "x"},
            language={"alpha3": "eng"},
            config={},
        )
        self.assertTrue(result["empty"])
        self.assertEqual(result["content_b64"], "")

    def test_download_falls_back_to_latin1_on_invalid_utf8(self):
        body = "Acentüádo".encode("latin-1")
        provider = self._provider_with_body(body)
        result = provider.download(
            provider_payload={"provider": "subtitlecat", "subtitle_url": "x"},
            language={"alpha3": "spa"},
            config={},
        )
        self.assertEqual(result["encoding"], "latin-1")
        self.assertEqual(base64.b64decode(result["content_b64"]), body)


if __name__ == "__main__":
    unittest.main()
