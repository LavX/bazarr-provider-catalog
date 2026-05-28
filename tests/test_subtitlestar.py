import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subtitlestar"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subtitlestar_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildQueriesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_with_year_emits_precise_then_loose(self):
        queries = self.mod.build_queries(
            {"kind": "movie", "title": "Dune", "year": 2021}
        )
        self.assertEqual(queries, ["Dune 2021", "Dune"])

    def test_movie_without_year_emits_single_query(self):
        queries = self.mod.build_queries({"kind": "movie", "title": "Inception"})
        self.assertEqual(queries, ["Inception"])

    def test_episode_emits_series_only(self):
        queries = self.mod.build_queries(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2}
        )
        self.assertEqual(queries, ["Breaking Bad"])

    def test_unknown_kind_returns_empty(self):
        self.assertEqual(self.mod.build_queries({"kind": "trailer"}), [])

    def test_missing_required_fields_returns_empty(self):
        self.assertEqual(self.mod.build_queries({"kind": "movie"}), [])
        self.assertEqual(
            self.mod.build_queries({"kind": "episode", "series": ""}), []
        )


SEARCH_HTML = b"""
<html>
<body>
<a href="https://subtitlestar.com/persian-subtitles-dune-2021/">
<img alt="Dune 2021" src="x.jpg">
</a>
<a href="https://subtitlestar.com/persian-subtitles-inception-2010/">
<h2>Inception 2010</h2>
</a>
</body>
</html>
"""

DETAIL_HTML = """
<html>
<head><title>Dune 2021 - SubtitleStar</title></head>
<body>
<a href="https://www.imdb.com/title/tt1160419/">IMDB</a>
<span><i class="icon-years"></i><a>2021</a></span>
<span><b>کیفیت :</b>WebDL,BluRay</span>
<a id="link-download" href="https://dl2.subtitlestar.com/dlsub/dune-2021.zip" class="dlbtn">Download</a>
</body>
</html>
""".encode('utf-8')

DETAIL_HTML = """
<html>
<head><title>Dune 2021 - SubtitleStar</title></head>
<body>
<a href="https://www.imdb.com/title/tt1160419/">IMDB</a>
<span><i class="icon-years"></i><a>2021</a></span>
<span><b>کیفیت :</b>WebDL,BluRay</span>
<a id="link-download" href="https://dl2.subtitlestar.com/dlsub/dune-2021.zip" class="dlbtn">Download</a>
</body>
</html>
""".encode('utf-8')


class ParseSearchResultsTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_returns_candidates(self):
        results = self.mod.parse_search_results(SEARCH_HTML)
        self.assertGreaterEqual(len(results), 1)

    def test_returns_single_quoted_candidates(self):
        html = b"""
        <html>
        <body>
        <a href='https://subtitlestar.com/persian-subtitles-dune-2021/'>
        <img alt='Dune 2021' src='x.jpg'>
        </a>
        </body>
        </html>
        """

        results = self.mod.parse_search_results(html)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Dune 2021")

    def test_each_candidate_has_url_and_title(self):
        results = self.mod.parse_search_results(SEARCH_HTML)
        for result in results:
            self.assertIn("detail_url", result)
            self.assertIn("title", result)
            self.assertTrue(result["detail_url"].startswith("https://subtitlestar.com/persian-subtitles-"))

    def test_handles_empty_html(self):
        self.assertEqual(self.mod.parse_search_results(b""), [])


class ParseDetailPageTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_extracts_metadata(self):
        details = self.mod.parse_detail_page(DETAIL_HTML)
        self.assertIsNotNone(details)
        self.assertEqual(details["imdb_id"], "tt1160419")
        self.assertEqual(details["year"], "2021")
        self.assertIn("WebDL", details["quality"])

    def test_extracts_download_urls(self):
        details = self.mod.parse_detail_page(DETAIL_HTML)
        self.assertGreater(len(details["downloads"]), 0)
        self.assertTrue(details["downloads"][0].endswith(".zip"))

    def test_extracts_direct_download_host_links_without_button_class(self):
        html = """
        <html>
        <body>
        <a href="https://dl.subtitlestar.com/dlsub/dune-2021.zip">Download subtitle</a>
        <a href="https://dl.subtitlestar.com/trailers/dune-2021.mp4">Trailer</a>
        </body>
        </html>
        """.encode("utf-8")

        details = self.mod.parse_detail_page(html)

        self.assertEqual(
            details["downloads"],
            ["https://dl.subtitlestar.com/dlsub/dune-2021.zip"],
        )

    def test_skips_unsupported_archive_download_links(self):
        html = """
        <html>
        <body>
        <a href="https://dl.subtitlestar.com/dlsub/movie.rar" class="dlbtn">RAR</a>
        <a href="https://dl.subtitlestar.com/dlsub/movie.7z" class="dlbtn">7z</a>
        <a href="https://dl.subtitlestar.com/dlsub/movie.zip">ZIP</a>
        </body>
        </html>
        """.encode("utf-8")

        details = self.mod.parse_detail_page(html)

        self.assertEqual(
            details["downloads"],
            ["https://dl.subtitlestar.com/dlsub/movie.zip"],
        )

    def test_skips_dlbtn_links_without_download_paths(self):
        html = """
        <html>
        <body>
        <a class="dlbtn" href="#">Broken button</a>
        <a id="link-download" href="/not-a-download">Broken id</a>
        <a class="dlbtn" href="https://dl.subtitlestar.com/dlsub/movie.zip">ZIP</a>
        </body>
        </html>
        """.encode("utf-8")

        details = self.mod.parse_detail_page(html)

        self.assertEqual(
            details["downloads"],
            ["https://dl.subtitlestar.com/dlsub/movie.zip"],
        )

    def test_normalizes_dlsub_relative_links_once(self):
        html = """
        <html>
        <body>
        <a href="dlsub/movie.zip">Download subtitle</a>
        </body>
        </html>
        """.encode("utf-8")

        details = self.mod.parse_detail_page(html)

        self.assertEqual(
            details["downloads"],
            ["https://dl2.subtitlestar.com/dlsub/movie.zip"],
        )

    def test_normalizes_extra_slash_dlsub_relative_links(self):
        html = """
        <html>
        <body>
        <a href="dlsub//movie.zip">Download subtitle</a>
        </body>
        </html>
        """.encode("utf-8")

        details = self.mod.parse_detail_page(html)

        self.assertEqual(
            details["downloads"],
            ["https://dl2.subtitlestar.com/dlsub/movie.zip"],
        )

    def test_handles_empty_html(self):
        self.assertIsNone(self.mod.parse_detail_page(b""))


class ComputeScoreTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_title_plus_year_scores_100(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Dune", "year": 2021},
            "Dune 2021 1080p BluRay",
        )
        self.assertEqual(score, 100)

    def test_movie_year_string_is_compared_as_full_year(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Dune", "year": "2021 "},
            "Dune 2021 1080p BluRay",
        )
        self.assertEqual(score, 100)

    def test_movie_title_only_scores_90(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Dune", "year": 2021},
            "Dune Persian Subtitle",
        )
        self.assertEqual(score, 90)

    def test_episode_series_scores_85(self):
        score = self.mod.compute_score(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking Bad Complete Series",
        )
        self.assertEqual(score, 85)

    def test_unrelated_scores_60(self):
        score = self.mod.compute_score(
            {"kind": "movie", "title": "Dune", "year": 2021},
            "Inception 2010",
        )
        self.assertEqual(score, 60)


class DeriveMatchesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_emits_title_and_year(self):
        matches = self.mod.derive_matches(
            {"kind": "movie", "title": "Dune", "year": 2021},
            "Dune 2021 1080p BluRay",
        )
        self.assertIn("title", matches)
        self.assertIn("year", matches)

    def test_episode_emits_series(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Breaking Bad", "season": 1, "episode": 2},
            "Breaking Bad Complete Series",
        )
        self.assertIn("series", matches)

    def test_episode_unpadded_marker_emits_season_and_episode(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 2},
            "Show S1E02 720p",
        )

        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_episode_marker_requires_matching_season(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 5},
            "Show S02E05 720p",
        )

        self.assertIn("series", matches)
        self.assertNotIn("season", matches)
        self.assertNotIn("episode", matches)

    def test_episode_marker_requires_matching_season_with_separator(self):
        matches = self.mod.derive_matches(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 2},
            "Show S02 E02 720p",
        )

        self.assertIn("series", matches)
        self.assertNotIn("season", matches)
        self.assertNotIn("episode", matches)

    def test_required_match_rejects_different_explicit_episode(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 2}
        candidate_title = "Show S01E03 720p"
        matches = self.mod.derive_matches(video, candidate_title)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertNotIn("episode", matches)
        self.assertFalse(self.mod._has_required_match(video, matches, candidate_title))

    def test_episode_marker_accepts_chained_multi_episode_release(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 2}
        candidate_title = "Show S01E01E02 720p"
        matches = self.mod.derive_matches(video, candidate_title)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)
        self.assertTrue(self.mod._has_required_match(video, matches, candidate_title))

    def test_episode_marker_accepts_range_covering_request(self):
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 5}
        candidate_title = "Show S01E01-E08 720p"
        matches = self.mod.derive_matches(video, candidate_title)

        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)
        self.assertTrue(self.mod._has_required_match(video, matches, candidate_title))


class SubtitlestarProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_stub(self, responses):
        provider = self.mod.SubtitlestarProvider()
        called = []

        def stub(url, timeout=15):
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        return provider, called

    def test_search_returns_persian_results(self):
        search_url = "https://subtitlestar.com/?s=Dune%202021&post_type=post"
        detail_url = "https://subtitlestar.com/persian-subtitles-dune-2021/"
        responses = {
            search_url: SEARCH_HTML,
            detail_url: DETAIL_HTML,
        }
        provider, _ = self._provider_with_stub(responses)
        results = provider.search(
            video={"kind": "movie", "title": "Dune", "year": 2021},
            languages=[{"alpha3": "fas", "alpha2": "fa"}],
            config={},
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["provider"], "subtitlestar")
        self.assertEqual(results[0]["language"]["alpha3"], "fas")

    def test_search_skips_non_persian_requests(self):
        provider, _ = self._provider_with_stub({})
        results = provider.search(
            video={"kind": "movie", "title": "Dune", "year": 2021},
            languages=[{"alpha3": "eng", "alpha2": "en"}],
            config={},
        )
        self.assertEqual(results, [])

    def test_search_surfaces_search_endpoint_failures(self):
        provider = self.mod.SubtitlestarProvider()

        def fail(url, timeout=15):
            raise TimeoutError("search endpoint timed out")

        provider._http_get = fail

        with self.assertRaises(TimeoutError):
            provider.search(
                video={"kind": "movie", "title": "Dune", "year": 2021},
                languages=[{"alpha3": "fas", "alpha2": "fa"}],
                config={},
            )

    def test_search_filters_movies_without_required_year_match(self):
        search_url = "https://subtitlestar.com/?s=Dune%202021&post_type=post"
        fallback_search_url = "https://subtitlestar.com/?s=Dune&post_type=post"
        detail_url = "https://subtitlestar.com/persian-subtitles-dune-1984/"
        search_html = b"""
        <html>
        <body>
        <a href="https://subtitlestar.com/persian-subtitles-dune-1984/">
        <img alt="Dune 1984" src="x.jpg">
        </a>
        </body>
        </html>
        """
        detail_html = """
        <html>
        <head><title>Dune 1984 - SubtitleStar</title></head>
        <body>
        <span><i class="icon-years"></i><a>1984</a></span>
        <a href="https://dl.subtitlestar.com/dlsub/dune-1984.zip">Download</a>
        </body>
        </html>
        """.encode("utf-8")
        provider, _ = self._provider_with_stub(
            {
                search_url: search_html,
                fallback_search_url: b"<html><body></body></html>",
                detail_url: detail_html,
            }
        )

        results = provider.search(
            video={"kind": "movie", "title": "Dune", "year": 2021},
            languages=[{"alpha3": "fas", "alpha2": "fa"}],
            config={},
        )

        self.assertEqual(results, [])

    def test_search_keeps_title_match_when_candidate_year_missing(self):
        search_url = "https://subtitlestar.com/?s=Dune%202021&post_type=post"
        fallback_search_url = "https://subtitlestar.com/?s=Dune&post_type=post"
        detail_url = "https://subtitlestar.com/persian-subtitles-dune/"
        search_html = b"""
        <html>
        <body>
        <a href="https://subtitlestar.com/persian-subtitles-dune/">
        <img alt="Dune" src="x.jpg">
        </a>
        </body>
        </html>
        """
        detail_html = """
        <html>
        <head><title>Dune - SubtitleStar</title></head>
        <body>
        <a href="https://dl.subtitlestar.com/dlsub/dune.zip">Download</a>
        </body>
        </html>
        """.encode("utf-8")
        provider, _ = self._provider_with_stub(
            {
                search_url: search_html,
                fallback_search_url: b"<html><body></body></html>",
                detail_url: detail_html,
            }
        )

        results = provider.search(
            video={"kind": "movie", "title": "Dune", "year": 2021},
            languages=[{"alpha3": "fas", "alpha2": "fa"}],
            config={},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["matches"], ["title"])

    def test_search_keeps_year_like_number_from_title(self):
        search_url = "https://subtitlestar.com/?s=Blade%20Runner%202049%202017&post_type=post"
        fallback_search_url = "https://subtitlestar.com/?s=Blade%20Runner%202049&post_type=post"
        detail_url = "https://subtitlestar.com/persian-subtitles-blade-runner-2049/"
        search_html = b"""
        <html>
        <body>
        <a href="https://subtitlestar.com/persian-subtitles-blade-runner-2049/">
        <img alt="Blade Runner 2049" src="x.jpg">
        </a>
        </body>
        </html>
        """
        detail_html = """
        <html>
        <head><title>Blade Runner 2049 - SubtitleStar</title></head>
        <body>
        <a href="https://dl.subtitlestar.com/dlsub/blade-runner-2049.zip">Download</a>
        </body>
        </html>
        """.encode("utf-8")
        provider, _ = self._provider_with_stub(
            {
                search_url: search_html,
                fallback_search_url: b"<html><body></body></html>",
                detail_url: detail_html,
            }
        )

        results = provider.search(
            video={"kind": "movie", "title": "Blade Runner 2049", "year": 2017},
            languages=[{"alpha3": "fas", "alpha2": "fa"}],
            config={},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["matches"], ["title"])

    def test_search_scores_against_detail_title_when_search_title_missing(self):
        search_url = "https://subtitlestar.com/?s=Dune%202021&post_type=post"
        fallback_search_url = "https://subtitlestar.com/?s=Dune&post_type=post"
        detail_url = "https://subtitlestar.com/persian-subtitles-dune-2021/"
        search_html = b"""
        <html>
        <body>
        <a href="https://subtitlestar.com/persian-subtitles-dune-2021/">
        <img alt="" src="x.jpg">
        </a>
        </body>
        </html>
        """
        detail_html = """
        <html>
        <head><title>Dune 2021 - SubtitleStar</title></head>
        <body>
        <span><i class="icon-years"></i><a>2021</a></span>
        <a href="https://dl.subtitlestar.com/dlsub/dune-2021.zip">Download</a>
        </body>
        </html>
        """.encode("utf-8")
        provider, _ = self._provider_with_stub(
            {
                search_url: search_html,
                fallback_search_url: b"<html><body></body></html>",
                detail_url: detail_html,
            }
        )

        results = provider.search(
            video={"kind": "movie", "title": "Dune", "year": 2021},
            languages=[{"alpha3": "fas", "alpha2": "fa"}],
            config={},
        )

        self.assertEqual(len(results), 1)
        self.assertIn("title", results[0]["matches"])

    def test_search_strips_query_string_from_advertised_filename(self):
        search_url = "https://subtitlestar.com/?s=Dune%202021&post_type=post"
        fallback_search_url = "https://subtitlestar.com/?s=Dune&post_type=post"
        detail_url = "https://subtitlestar.com/persian-subtitles-dune-2021/"
        search_html = b"""
        <html>
        <body>
        <a href="https://subtitlestar.com/persian-subtitles-dune-2021/">
        <img alt="Dune 2021" src="x.jpg">
        </a>
        </body>
        </html>
        """
        detail_html = """
        <html>
        <head><title>Dune 2021 - SubtitleStar</title></head>
        <body>
        <span><i class="icon-years"></i><a>2021</a></span>
        <a href="https://dl.subtitlestar.com/dlsub/dune.vtt?download=1">Download</a>
        </body>
        </html>
        """.encode("utf-8")
        provider, _ = self._provider_with_stub(
            {
                search_url: search_html,
                fallback_search_url: b"<html><body></body></html>",
                detail_url: detail_html,
            }
        )

        results = provider.search(
            video={"kind": "movie", "title": "Dune", "year": 2021},
            languages=[{"alpha3": "fas", "alpha2": "fa"}],
            config={},
        )

        self.assertEqual(results[0]["filename"], "subtitlestar.dune.vtt")


class SubtitlestarProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider_with_body(self, body):
        provider = self.mod.SubtitlestarProvider()
        provider._http_get = lambda url, timeout=15: body
        return provider

    def test_download_returns_base64_for_zip(self):
        import io
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("dune.srt", "1\n00:00:01,000 --> 00:00:02,500\nHello\n")
        body = zip_buffer.getvalue()
        provider = self._provider_with_body(body)
        result = provider.download(
            provider_payload={
                "provider": "subtitlestar",
                "download_url": "https://dl2.subtitlestar.com/dlsub/dune.zip",
            },
            language={"alpha3": "fas"},
            config={},
        )
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])
        self.assertEqual(base64.b64decode(result["content_b64"]), b"1\n00:00:01,000 --> 00:00:02,500\nHello\n")

    def test_download_marks_empty_body(self):
        provider = self._provider_with_body(b"")
        result = provider.download(
            provider_payload={"provider": "subtitlestar", "download_url": "x"},
            language={"alpha3": "fas"},
            config={},
        )
        self.assertTrue(result["empty"])

    def test_download_keeps_direct_subtitle_format_with_query_string(self):
        provider = self._provider_with_body(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n")
        result = provider.download(
            provider_payload={
                "provider": "subtitlestar",
                "download_url": "https://dl.subtitlestar.com/dlsub/episode.vtt?download=1",
            },
            language={"alpha3": "fas"},
            config={},
        )

        self.assertEqual(result["format"], "vtt")
        self.assertEqual(result["content_type"], "text/vtt")

    def test_download_merges_multipart_movie_zip_subtitles(self):
        import io
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Movie.CD1.srt", "1\n00:00:01,000 --> 00:00:02,000\nPart one\n")
            zf.writestr("Movie.CD2.srt", "1\n00:10:01,000 --> 00:10:02,000\nPart two\n")
        body = zip_buffer.getvalue()
        provider = self._provider_with_body(body)

        result = provider.download(
            provider_payload={
                "download_url": "https://dl.subtitlestar.com/dlsub/movie.zip",
                "video": {"kind": "movie", "title": "Movie", "year": 2024},
            },
            language={"alpha3": "fas", "alpha2": "fa"},
            config={},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Part one", decoded)
        self.assertIn(b"Part two", decoded)

    def test_download_rejects_html_body_from_zip_url(self):
        provider = self._provider_with_body(b"<html><title>blocked</title></html>")

        with self.assertRaises(ValueError):
            provider.download(
                provider_payload={
                    "download_url": "https://dl.subtitlestar.com/dlsub/movie.zip",
                },
                language={"alpha3": "fas", "alpha2": "fa"},
                config={},
            )

    def test_download_marks_empty_archive_entry(self):
        import io
        import zipfile
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zf:
            zf.writestr("Show.S01E05.srt", b"")
        provider = self._provider_with_body(zip_buffer.getvalue())

        result = provider.download(
            provider_payload={
                "download_url": "https://dl.subtitlestar.com/dlsub/show.zip",
                "video": {"kind": "episode", "season": 1, "episode": 5},
            },
            language={"alpha3": "fas", "alpha2": "fa"},
            config={},
        )

        self.assertTrue(result["empty"])

    def test_download_rejects_direct_wrong_episode_filename(self):
        provider = self._provider_with_body(b"1\n00:00:01,000 --> 00:00:02,000\nWrong\n")

        with self.assertRaises(ValueError):
            provider.download(
                provider_payload={
                    "download_url": "https://dl.subtitlestar.com/dlsub/Show.S01E03.srt",
                    "video": {"kind": "episode", "season": 1, "episode": 2},
                },
                language={"alpha3": "fas", "alpha2": "fa"},
                config={},
            )


class SelectSubtitleFileTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_rejects_episode_only_match_when_explicit_season_conflicts(self):
        with self.assertRaises(ValueError):
            self.mod.select_subtitle_file(
                ["Show.S01E01.srt", "Show.S01E02.srt"],
                {"kind": "episode", "season": 2, "episode": 1},
            )

    def test_rejects_episode_only_match_when_separated_season_conflicts(self):
        with self.assertRaises(ValueError):
            self.mod.select_subtitle_file(
                ["Show.S01.E01.srt", "Show.S01.E02.srt"],
                {"kind": "episode", "season": 2, "episode": 1},
            )

    def test_selects_episode_from_1x02_filename(self):
        selected = self.mod.select_subtitle_file(
            ["Show.1x01.srt", "Show.1x02.srt"],
            {"kind": "episode", "season": 1, "episode": 2},
        )

        self.assertEqual(selected, "Show.1x02.srt")

    def test_skips_appledouble_sidecar_files(self):
        selected = self.mod.select_subtitle_file(
            ["__MACOSX/._Show.S01E02.srt", "Show.S01E02.srt"],
            {"kind": "episode", "season": 1, "episode": 2},
        )

        self.assertEqual(selected, "Show.S01E02.srt")

    def test_keeps_single_generic_episode_file(self):
        selected = self.mod.select_subtitle_file(
            ["subtitle.srt"],
            {"kind": "episode", "season": 1, "episode": 2},
        )

        self.assertEqual(selected, "subtitle.srt")

    def test_rejects_single_wrong_episode_file(self):
        with self.assertRaises(ValueError):
            self.mod.select_subtitle_file(
                ["Show.S01E03.srt"],
                {"kind": "episode", "season": 1, "episode": 2},
            )

    def test_selects_three_digit_episode_file(self):
        selected = self.mod.select_subtitle_file(
            ["Show.S01E099.srt", "Show.S01E100.srt"],
            {"kind": "episode", "season": 1, "episode": 100},
        )

        self.assertEqual(selected, "Show.S01E100.srt")

    def test_rejects_three_digit_wrong_episode_file(self):
        with self.assertRaises(ValueError):
            self.mod.select_subtitle_file(
                ["Show.S01E101.srt"],
                {"kind": "episode", "season": 1, "episode": 100},
            )

    def test_rejects_folder_season_conflict(self):
        with self.assertRaises(ValueError):
            self.mod.select_subtitle_file(
                ["Season 02/Show.E05.srt"],
                {"kind": "episode", "season": 1, "episode": 5},
            )

    def test_selects_multipart_episode_group(self):
        selected = self.mod.select_subtitle_files(
            [
                "Show.S01E05.CD1.srt",
                "Show.S01E05.CD2.srt",
                "Show.S01E06.CD1.srt",
            ],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, ["Show.S01E05.CD1.srt", "Show.S01E05.CD2.srt"])

    def test_selects_chained_multi_episode_file(self):
        selected = self.mod.select_subtitle_file(
            ["Show.S01E01E02.srt"],
            {"kind": "episode", "season": 1, "episode": 2},
        )

        self.assertEqual(selected, "Show.S01E01E02.srt")

    def test_selects_episode_range_file(self):
        selected = self.mod.select_subtitle_file(
            ["Show.S01E01-E08.srt"],
            {"kind": "episode", "season": 1, "episode": 5},
        )

        self.assertEqual(selected, "Show.S01E01-E08.srt")


if __name__ == "__main__":
    unittest.main()
