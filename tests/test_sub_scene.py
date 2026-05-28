"""Tests for subscene_best provider."""

import base64
import io
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "providers" / "sub_scene"))

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


class TestLanguageMap(unittest.TestCase):
    def test_common_languages_mapped(self):
        self.assertEqual(LANGUAGE_MAP["English"], "eng")
        self.assertEqual(LANGUAGE_MAP["Vietnamese"], "vie")
        self.assertEqual(LANGUAGE_MAP["Arabic"], "ara")
        self.assertEqual(LANGUAGE_MAP["Chinese BG code"], "zho")

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


class TestSubsceneSearchParser(unittest.TestCase):
    def test_parse_search_results(self):
        html = """
        <html>
        <body>
            <div class="title">
                <a href="/subscene/12345">Dune (2021)</a>
            </div>
            <div class="title">
                <a href="/subscene/67890">Dune Part Two (2024)</a>
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
    def test_search_no_results(self, mock_search):
        mock_search.return_value = []

        video = {"kind": "movie", "title": "NonExistentMovie12345", "year": 2099}
        languages = ["eng"]
        config = {"request_delay_ms": 0}

        results = self.provider.search(video, languages, config)
        self.assertEqual(len(results), 0)

    @patch("provider._get_subtitle_detail")
    @patch("provider._download_subtitle")
    def test_download(self, mock_download, mock_detail):
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
        
        mock_download.return_value = {
            "content": srt_content,
            "filename": "test.srt",
            "format": "srt",
        }

        subtitle = {"url": "/subtitle/123"}
        config = {"request_delay_ms": 0}

        result = self.provider.download(subtitle, None, config)
        self.assertIsNotNone(result)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertEqual(base64.b64decode(result["content_b64"]), srt_content)
        self.assertFalse(result["empty"])


if __name__ == "__main__":
    unittest.main()
