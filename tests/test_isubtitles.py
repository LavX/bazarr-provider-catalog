import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "isubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "isubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "isubtitles_search_chernobyl.html").read_bytes()
ENGLISH_HTML = (FIXTURE_DIR / "isubtitles_chernobyl_english.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class ISubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_title_page(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)
        self.assertEqual(rows[1]["title"], "Chernobyl - (2019)")
        self.assertEqual(rows[1]["slug"], "chernobyl")
        self.assertEqual(rows[1]["url"], "https://isubtitles.org/chernobyl-subtitles")

    def test_parse_subtitle_rows_extracts_download_rows(self):
        rows = self.mod.parse_subtitle_rows(ENGLISH_HTML)
        self.assertEqual(rows[0]["subtitle_id"], "1415636")
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["file_count"], 1)
        self.assertEqual(rows[0]["size"], "15.7KB")
        self.assertIn("s01e01", rows[0]["release_info"].lower())

    def test_parse_subtitle_rows_maps_brazillian_portuguese_slug(self):
        body = b"""
          <tr>
            <td data-title="Release / Movie"><a>Movie.2024.WEBRip</a></td>
            <td data-title="File">1</td>
            <td data-title="Size">20KB</td>
            <td data-title="Created">today</td>
            <td data-title="Comment"></td>
            <td><a href="/download/movie/brazillian-portuguese/123">download</a></td>
          </tr>
        """

        rows = self.mod.parse_subtitle_rows(body)

        self.assertEqual(rows[0]["language"], "por")

    def test_parse_subtitle_rows_maps_big5_chinese_slug(self):
        body = b"""
          <tr>
            <td data-title="Release / Movie"><a>Movie.2024.WEBRip</a></td>
            <td data-title="File">1</td>
            <td data-title="Size">20KB</td>
            <td data-title="Created">today</td>
            <td data-title="Comment"></td>
            <td><a href="/download/movie/big-5-code/124">download</a></td>
          </tr>
        """

        rows = self.mod.parse_subtitle_rows(body)

        self.assertEqual(rows[0]["language"], "zho")

    def test_parse_subtitle_rows_maps_browse_language_slugs(self):
        cases = {
            "albanian": "sqi",
            "azerbaijani": "aze",
            "belarusian": "bel",
            "bosnian": "bos",
            "burmese": "mya",
            "cambodian-khmer": "khm",
            "catalan": "cat",
            "estonian": "est",
            "georgian": "kat",
            "icelandic": "isl",
            "japanese": "jpn",
            "kannada": "kan",
            "kurdish": "kur",
            "latvian": "lav",
            "lithuanian": "lit",
            "macedonian": "mkd",
            "malayalam": "mal",
            "pashto": "pus",
            "serbian": "srp",
            "slovak": "slk",
            "slovenian": "slv",
            "tagalog": "fil",
            "telugu": "tel",
            "urdu": "urd",
            "ukranian": "ukr",
        }
        for language_slug, alpha3 in cases.items():
            with self.subTest(language_slug=language_slug):
                body = f"""
                  <tr>
                    <td data-title="Release / Movie"><a>Movie.2024.WEBRip</a></td>
                    <td data-title="File">1</td>
                    <td data-title="Size">20KB</td>
                    <td data-title="Created">today</td>
                    <td data-title="Comment"></td>
                    <td><a href="/download/movie/{language_slug}/124">download</a></td>
                  </tr>
                """.encode("utf-8")

                rows = self.mod.parse_subtitle_rows(body)

                self.assertEqual(rows[0]["language"], alpha3)

    def test_derive_matches_accepts_1x_episode_tags(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            "Chernobyl [1x01] WEBRip",
        )

        self.assertIn("episode", matches)

    def test_derive_matches_accepts_separated_episode_tags(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            "Chernobyl S01.E01 WEBRip",
        )

        self.assertIn("episode", matches)

    def test_derive_matches_accepts_combined_episode_tags(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            "Chernobyl S01E01E02 WEBRip",
        )

        self.assertIn("episode", matches)

    def test_derive_matches_accepts_season_word_e_episode_tags(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            "Chernobyl Season 1 E01 WEBRip",
        )

        self.assertIn("episode", matches)

    def test_derive_matches_accepts_dash_multi_episode_tags(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 2},
            "Chernobyl S01E01-02 WEBRip",
        )

        self.assertIn("episode", matches)

    def test_derive_matches_accepts_slash_multi_episode_tags(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 2},
            "Chernobyl S01E01/02 WEBRip",
        )

        self.assertIn("episode", matches)

    def test_derive_matches_accepts_season_word_packs(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Succession", "season": 1, "episode": 1},
            "Succession Season 1 Complete 720p WEB",
        )

        self.assertIn("season", matches)

    def test_movie_row_rejects_mismatched_candidate_year(self):
        self.assertFalse(
            self.mod._row_matches_video(
                {"kind": "movie", "title": "Suspiria", "year": 2018},
                {"release_info": "Suspiria.1977.1080p", "comment": "", "file_count": 1},
                {"title": "Suspiria - (1977)", "year": 1977},
            )
        )

    def test_episode_row_rejects_explicit_mismatched_episode_pack(self):
        self.assertFalse(
            self.mod._row_matches_video(
                {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
                {"release_info": "Chernobyl S01E02 WEBRip", "comment": "", "file_count": 2},
                {"title": "Chernobyl - (2019)", "year": 2019},
            )
        )

    def test_episode_row_rejects_season_word_mismatched_episode(self):
        self.assertFalse(
            self.mod._row_matches_video(
                {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
                {"release_info": "Chernobyl Season 1 E02 WEBRip", "comment": "", "file_count": 2},
                {"title": "Chernobyl - (2019)", "year": 2019},
            )
        )

    def test_hearing_impaired_detection_uses_whole_word_tags(self):
        self.assertFalse(
            self.mod._looks_hearing_impaired(
                {"release_info": "Pathaan Hindi WEBRip", "comment": "high quality"}
            )
        )
        self.assertTrue(
            self.mod._looks_hearing_impaired(
                {"release_info": "Chernobyl HI WEBRip", "comment": ""}
            )
        )

    def test_rank_title_pages_ignores_non_numeric_year(self):
        pages = [{"title": "Chernobyl - (2019)", "year": 2019}]
        ranked = self.mod._rank_title_pages(
            {"kind": "movie", "title": "Chernobyl", "year": "N/A"},
            pages,
        )

        self.assertEqual(ranked, pages)


class ISubtitlesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_language_page_and_returns_requested_episode(self):
        provider = self.mod.ISubtitlesProvider()
        responses = {
            "https://isubtitles.org/search?kwd=Chernobyl+S01E01": SEARCH_HTML,
            "https://isubtitles.org/chernobyl/english": ENGLISH_HTML,
        }
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item[0] for item in called], list(responses))
        self.assertEqual(results[0]["provider"], "isubtitles")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertIn("episode", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1415636")

    def test_download_extracts_matching_subtitle_from_zip(self):
        provider = self.mod.ISubtitlesProvider()
        body = _zip_body(
            {
                "Chernobyl.S01E01.WEBRip.x264-ION10.srt": b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n",
                "Chernobyl.S01E02.WEBRip.x264-ION10.srt": b"1\nEpisode two\n",
            }
        )
        provider._http_get = lambda url, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "isubtitles",
                "schema": 1,
                "subtitle_id": "1415636",
                "url": "https://isubtitles.org/download/chernobyl/english/1415636",
                "page_url": "https://isubtitles.org/chernobyl/english/1415636",
                "filename": "chernobyl.s01e01.720p.webrip.x264-tbs.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Episode one", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_selects_1x_episode_file_from_zip(self):
        body = _zip_body(
            {
                "Chernobyl.S01E02.WEBRip.x264-ION10.srt": b"1\nEpisode two\n",
                "Chernobyl.1x01.WEBRip.x264-ION10.srt": b"1\nEpisode one\n",
            }
        )

        result = self.mod.extract_download(body, {"season": 1, "episode": 1})

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Episode one", decoded)

    def test_download_rejects_single_file_archive_with_wrong_episode(self):
        body = _zip_body(
            {
                "Chernobyl.S01E02.WEBRip.x264-ION10.srt": b"1\nEpisode two\n",
            }
        )

        with self.assertRaises(ValueError):
            self.mod.extract_download(body, {"season": 1, "episode": 1})

    def test_download_avoids_cross_season_episode_fallback(self):
        body = _zip_body(
            {
                "Chernobyl.S02.E01.WEBRip.x264-ION10.srt": b"1\nSeason two\n",
                "Chernobyl.S01.E01.WEBRip.x264-ION10.srt": b"1\nSeason one\n",
            }
        )

        result = self.mod.extract_download(body, {"season": 1, "episode": 1})

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Season one", decoded)
        self.assertNotIn(b"Season two", decoded)

    def test_download_selects_numeric_episode_file_from_season_pack(self):
        body = _zip_body(
            {
                "01.srt": b"1\nEpisode one\n",
                "02.srt": b"1\nEpisode two\n",
            }
        )

        result = self.mod.extract_download(body, {"season": 1, "episode": 1})

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Episode one", decoded)
        self.assertNotIn(b"Episode two", decoded)

    def test_download_scores_season_folder_paths(self):
        body = _zip_body(
            {
                "Season 2/01.srt": b"1\nSeason two\n",
                "Season 1/01.srt": b"1\nSeason one\n",
            }
        )

        result = self.mod.extract_download(body, {"season": 1, "episode": 1})

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Season one", decoded)
        self.assertNotIn(b"Season two", decoded)

    def test_download_rejects_html_body_when_not_zip_or_subtitle(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(
                b"<html><title>challenge</title></html>",
                {"filename": "isubtitles.bad.zip"},
            )


if __name__ == "__main__":
    unittest.main()
