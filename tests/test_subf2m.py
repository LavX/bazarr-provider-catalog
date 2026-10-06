import base64
import hashlib
import importlib.util
import io
import urllib.error
import urllib.parse
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subf2m"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subf2m_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_DUNE = (FIXTURE_DIR / "subf2m_search_dune.html").read_bytes()
SEARCH_CHERNOBYL = (FIXTURE_DIR / "subf2m_search_chernobyl.html").read_bytes()
DETAIL_DUNE_EN = (FIXTURE_DIR / "subf2m_detail_dune_english.html").read_bytes()
DETAIL_CHERNOBYL_EN = (FIXTURE_DIR / "subf2m_detail_chernobyl_english.html").read_bytes()
LANG_MATRIX_EN = (FIXTURE_DIR / "subf2m_lang_the_matrix_english.html").read_bytes()
LANG_MATRIX_1999_EN = (FIXTURE_DIR / "subf2m_lang_the_matrix_1999_english.html").read_bytes()
LANG_CHERNOBYL_EN = (FIXTURE_DIR / "subf2m_lang_chernobyl_english.html").read_bytes()
DOWNLOAD_GATE_DUNE = (FIXTURE_DIR / "subf2m_download_gate_dune.html").read_bytes()


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class _StubErrors:
    """Synthetic HTTPError objects, closed together so their base-class
    finalizer does not warn about implicit cleanup."""

    def __init__(self):
        self.errors = []

    def http_error(self, url, code, message):
        error = urllib.error.HTTPError(url, code, message, None, None)
        self.errors.append(error)
        return error

    def close(self):
        for error in self.errors:
            error.close()


class SubF2MParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_links_and_titles(self):
        rows = self.mod.parse_search_results(SEARCH_DUNE)

        self.assertEqual(rows[2]["path"], "/subtitles/dune-2021")
        self.assertEqual(rows[2]["title"], "Dune: Part One (2021)")
        self.assertEqual(rows[2]["year"], 2021)

    def test_rank_movie_paths_prefers_matching_year_and_title(self):
        paths = self.mod.rank_movie_paths(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021},
            self.mod.parse_search_results(SEARCH_DUNE),
        )

        self.assertEqual(paths[0]["path"], "/subtitles/dune-2021")

    def test_rank_episode_paths_accepts_worded_season(self):
        paths = self.mod.rank_episode_paths(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "year": 2019},
            self.mod.parse_search_results(SEARCH_CHERNOBYL),
        )

        self.assertEqual(paths[0]["path"], "/subtitles/chernobyl")

    def test_rank_episode_paths_rejects_conflicting_year(self):
        paths = self.mod.rank_episode_paths(
            {"kind": "episode", "series": "The Office", "season": 1, "year": 2005},
            [
                {"path": "/subtitles/the-office-uk", "title": "The Office - First Season (2001)", "year": 2001, "season": 1, "index": 0},
                {"path": "/subtitles/the-office-us", "title": "The Office - First Season (2005)", "year": 2005, "season": 1, "index": 1},
            ],
        )

        self.assertEqual([path["path"] for path in paths], ["/subtitles/the-office-us"])

    def test_parse_subtitle_page_filters_episode_rows(self):
        rows = self.mod.parse_subtitle_page(
            DETAIL_CHERNOBYL_EN,
            "eng",
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
        )

        self.assertEqual([row["subtitle_id"] for row in rows], ["2647618", "2956831"])
        self.assertIn("S01E01", rows[0]["release_info"])
        self.assertIn("COMPLETE.SEASON.01", rows[1]["release_info"])

    def test_parse_subtitle_page_marks_forced_and_hi_rows(self):
        forced_rows = self.mod.parse_subtitle_page(
            DETAIL_CHERNOBYL_EN,
            "eng",
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
        )
        hi_rows = self.mod.parse_subtitle_page(
            DETAIL_DUNE_EN,
            "eng",
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
        )

        self.assertTrue(forced_rows[0]["forced"])
        self.assertFalse(forced_rows[0]["hearing_impaired"])
        self.assertTrue(hi_rows[0]["hearing_impaired"])
        self.assertFalse(hi_rows[0]["forced"])
        self.assertTrue(self.mod._looks_hearing_impaired("HI only"))

    def test_parse_subtitle_page_accepts_plain_season_pack(self):
        body = DETAIL_CHERNOBYL_EN.replace(
            b"Chernobyl.2019.COMPLETE.SEASON.01.1080p.Blu-ray.x265.10bit.AC3",
            b"Chernobyl.S01.1080p.Blu-ray.x265.10bit.AC3",
        ).replace(b"Complete season pack", b"Season pack")

        rows = self.mod.parse_subtitle_page(
            body,
            "eng",
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
        )

        self.assertIn("2956831", [row["subtitle_id"] for row in rows])

    def test_parse_subtitle_page_rejects_wrong_imdb_id(self):
        rows = self.mod.parse_subtitle_page(
            DETAIL_DUNE_EN,
            "eng",
            {"kind": "movie", "title": "Arrival", "year": 2016, "imdb_id": "tt2543164"},
        )

        self.assertEqual(rows, [])

    def test_parse_subtitle_page_only_marks_imdb_when_page_confirms_it(self):
        # The page omits the IMDb link, so an expected id from the request must
        # not inflate the row into an exact-id match.
        body = DETAIL_DUNE_EN.replace(b"imdb.com/title/tt1160419", b"example.com/no-imdb")

        rows = self.mod.parse_subtitle_page(
            body,
            "eng",
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
        )

        self.assertTrue(rows)
        self.assertTrue(all(not row["imdb_matched"] for row in rows))

        confirmed = self.mod.parse_subtitle_page(
            DETAIL_DUNE_EN,
            "eng",
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
        )

        self.assertTrue(confirmed[0]["imdb_matched"])

    def test_requested_languages_keeps_same_language_variants(self):
        requested = self.mod._requested_languages(
            [
                {"alpha3": "eng", "alpha2": "en", "forced": False},
                {"alpha3": "eng", "alpha2": "en", "forced": True},
            ]
        )

        self.assertEqual(len(requested), 2)
        self.assertEqual({meta["forced"] for meta in requested}, {False, True})

    def test_parse_download_button_extracts_absolute_download_url(self):
        url = self.mod.parse_download_url(
            DOWNLOAD_GATE_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english/3331049",
        )

        self.assertEqual(
            url,
            "https://subf2m.co/subtitles/dune-2021/english/3331049/download",
        )


class SubF2MProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_fetches_language_page_and_returns_payload(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english": DETAIL_DUNE_EN,
        }
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls, list(responses))
        self.assertEqual(results[0]["provider"], "subf2m")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertTrue(results[0]["language"]["hi"])
        self.assertTrue(results[0]["hearing_impaired"])
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "3331049")

    def test_search_movie_tries_imdb_only_after_all_title_queries_fail(self):
        provider = self.mod.SubF2MProvider()
        search_queries = []
        imdb_result = (
            b'<div class="title"><a href="/subtitles/dune-2021">'
            b'Dune: Part One (2021)</a></div>'
        )

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            parsed = urllib.parse.urlsplit(url)
            if parsed.path.endswith("/searchbytitle"):
                query = urllib.parse.parse_qs(parsed.query)["query"][0]
                search_queries.append(query)
                return imdb_result if query == "tt1160419" else b""
            if url == "https://subf2m.co/subtitles/dune-2021/english":
                return DETAIL_DUNE_EN
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(search_queries, ["Dune: Part One", "Dune", "tt1160419"])
        self.assertTrue(results)
        self.assertTrue(all("imdb_id" in item["matches"] for item in results))

    def test_imdb_fallback_keeps_localized_title_result_until_detail_verification(self):
        provider = self.mod.SubF2MProvider()
        search_queries = []
        imdb_result = (
            b'<div class="title"><a href="/subtitles/life-is-beautiful">'
            b'Life Is Beautiful</a></div>'
        )
        detail = DETAIL_DUNE_EN.replace(b"tt1160419", b"tt0118799")

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            parsed = urllib.parse.urlsplit(url)
            if parsed.path.endswith("/searchbytitle"):
                query = urllib.parse.parse_qs(parsed.query)["query"][0]
                search_queries.append(query)
                return imdb_result if query == "tt0118799" else b""
            if url == "https://subf2m.co/subtitles/life-is-beautiful/english":
                return detail
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "La Vita e Bella", "year": 1997, "imdb_id": "tt0118799"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(search_queries, ["La Vita e Bella", "tt0118799"])
        self.assertTrue(results)
        self.assertTrue(all("imdb_id" in item["matches"] for item in results))
        self.assertEqual(results[0]["display"]["title"], "Life Is Beautiful")

    def test_imdb_fallback_rejects_detail_page_with_wrong_imdb_id(self):
        provider = self.mod.SubF2MProvider()
        imdb_result = (
            b'<div class="title"><a href="/subtitles/life-is-beautiful">'
            b'La Vita e Bella</a></div>'
        )

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            parsed = urllib.parse.urlsplit(url)
            if parsed.path.endswith("/searchbytitle"):
                query = urllib.parse.parse_qs(parsed.query)["query"][0]
                return imdb_result if query == "tt0118799" else b""
            if url == "https://subf2m.co/subtitles/life-is-beautiful/english":
                return DETAIL_DUNE_EN.replace(b"tt1160419", b"tt2543164")
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "La Vita e Bella", "year": 1997, "imdb_id": "tt0118799"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_imdb_fallback_rejects_detail_page_without_imdb_id(self):
        provider = self.mod.SubF2MProvider()
        imdb_result = (
            b'<div class="title"><a href="/subtitles/life-is-beautiful">'
            b'La Vita e Bella</a></div>'
        )

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            parsed = urllib.parse.urlsplit(url)
            if parsed.path.endswith("/searchbytitle"):
                query = urllib.parse.parse_qs(parsed.query)["query"][0]
                return imdb_result if query == "tt0118799" else b""
            if url == "https://subf2m.co/subtitles/life-is-beautiful/english":
                return DETAIL_DUNE_EN.replace(b"imdb.com/title/tt1160419", b"example.com/no-imdb")
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "La Vita e Bella", "year": 1997, "imdb_id": "tt0118799"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_search_movie_without_imdb_keeps_title_query(self):
        provider = self.mod.SubF2MProvider()
        search_queries = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            parsed = urllib.parse.urlsplit(url)
            if parsed.path.endswith("/searchbytitle"):
                query = urllib.parse.parse_qs(parsed.query)["query"][0]
                search_queries.append(query)
                return SEARCH_DUNE
            if url == "https://subf2m.co/subtitles/dune-2021/english":
                return DETAIL_DUNE_EN
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(search_queries, ["Dune: Part One"])
        self.assertTrue(results)

    def test_slug_candidates_orders_year_variant_first(self):
        candidates = self.mod.slug_candidates({"kind": "movie", "title": "The Matrix", "year": 1999})
        self.assertEqual(candidates, ["the-matrix-1999", "the-matrix"])

        candidates = self.mod.slug_candidates({"kind": "episode", "series": "Chernobyl", "year": 2019})
        self.assertEqual(candidates, ["chernobyl-2019", "chernobyl"])

        self.assertEqual(self.mod.slug_candidates({"kind": "movie", "title": "Interstellar"}), ["interstellar"])
        self.assertEqual(self.mod.slug_candidates({"kind": "movie", "title": "", "year": 1999}), [])

    def test_parse_page_title_strips_site_prefix(self):
        self.assertEqual(self.mod.parse_page_title(b"<title>Subtitles for The Matrix</title>"), "The Matrix")
        self.assertEqual(self.mod.parse_page_title(b"<title>Subtitles for Dune: Part One</title>"), "Dune: Part One")
        self.assertEqual(self.mod.parse_page_title(b"<title>Subtitles for Chernobyl - First Season</title>"), "Chernobyl - First Season")
        self.assertEqual(self.mod.parse_page_title(b""), "")

    def test_page_identity_confirmed_without_imdb_uses_page_title(self):
        video_match = {"kind": "movie", "title": "The Matrix", "year": 1999}
        video_mismatch = {"kind": "movie", "title": "The Matrix Reloaded", "year": 2003}
        self.assertTrue(self.mod._page_identity_confirmed(LANG_MATRIX_EN, video_match))
        self.assertFalse(self.mod._page_identity_confirmed(LANG_MATRIX_EN, video_mismatch))

        episode_match = {"kind": "episode", "series": "Chernobyl", "season": 1}
        self.assertTrue(self.mod._page_identity_confirmed(LANG_CHERNOBYL_EN, episode_match))

        video_with_imdb = {"kind": "movie", "title": "The Matrix", "year": 1999, "imdb_id": "tt0133093"}
        self.assertTrue(self.mod._page_identity_confirmed(LANG_MATRIX_EN, video_with_imdb))
        self.assertFalse(
            self.mod._page_identity_confirmed(
                LANG_MATRIX_EN, {"kind": "movie", "title": "The Matrix", "imdb_id": "tt9999999"}
            )
        )

    def test_search_falls_back_to_slug_when_search_endpoint_fails(self):
        provider = self.mod.SubF2MProvider()
        errors = _StubErrors()
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, config
            calls.append((url, referer))
            if "/searchbytitle" in url:
                raise errors.http_error(url, 500, "Internal Server Error")
            if url == "https://subf2m.co/subtitles/the-matrix-1999/english":
                return LANG_MATRIX_1999_EN
            if url == "https://subf2m.co/subtitles/the-matrix/english":
                return LANG_MATRIX_EN
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        try:
            results = provider.search(
                {"kind": "movie", "title": "The Matrix", "year": 1999, "imdb_id": "tt0133093"},
                [{"alpha3": "eng", "alpha2": "en"}],
                {"request_delay_ms": 0},
            )
        finally:
            errors.close()

        self.assertEqual(
            [url for url, _ in calls],
            [
                "https://subf2m.co/subtitles/searchbytitle?query=The%20Matrix&l=",
                "https://subf2m.co/subtitles/searchbytitle?query=tt0133093&l=",
                "https://subf2m.co/subtitles/the-matrix-1999/english",
                "https://subf2m.co/subtitles/the-matrix/english",
            ],
        )
        self.assertEqual(calls[2][1], "https://subf2m.co")
        self.assertTrue(results)
        self.assertEqual(results[0]["provider"], "subf2m")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "3732233")
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertEqual(results[0]["display"]["title"], "The Matrix")
        self.assertEqual(results[0]["provider_payload"]["page_url"], "https://subf2m.co/subtitles/the-matrix/english/3732233")

    def test_search_slug_fallback_resolves_episode_series_slug(self):
        provider = self.mod.SubF2MProvider()
        errors = _StubErrors()
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            calls.append(url)
            if "/searchbytitle" in url:
                raise errors.http_error(url, 500, "Internal Server Error")
            if url == "https://subf2m.co/subtitles/chernobyl-2019/english":
                raise errors.http_error(url, 404, "Not Found")
            if url == "https://subf2m.co/subtitles/chernobyl/english":
                return LANG_CHERNOBYL_EN
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        try:
            results = provider.search(
                {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1, "year": 2019, "series_imdb_id": "tt7366338"},
                [{"alpha3": "eng", "alpha2": "en"}],
                {"request_delay_ms": 0},
            )
        finally:
            errors.close()

        self.assertEqual(
            calls,
            [
                "https://subf2m.co/subtitles/searchbytitle?query=Chernobyl&l=",
                "https://subf2m.co/subtitles/chernobyl-2019/english",
                "https://subf2m.co/subtitles/chernobyl/english",
            ],
        )
        self.assertTrue(results)
        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["3093583", "3067660"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_slug_fallback_accepts_title_match_without_imdb(self):
        provider = self.mod.SubF2MProvider()
        errors = _StubErrors()

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if "/searchbytitle" in url:
                raise errors.http_error(url, 500, "Internal Server Error")
            if url == "https://subf2m.co/subtitles/the-matrix-1999/english":
                return LANG_MATRIX_1999_EN
            if url == "https://subf2m.co/subtitles/the-matrix/english":
                return LANG_MATRIX_EN
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        try:
            results = provider.search(
                {"kind": "movie", "title": "The Matrix", "year": 1999},
                [{"alpha3": "eng", "alpha2": "en"}],
                {"request_delay_ms": 0},
            )
        finally:
            errors.close()

        self.assertTrue(results)
        self.assertNotIn("imdb_id", results[0]["matches"])

    def test_search_slug_fallback_requires_page_identity(self):
        provider = self.mod.SubF2MProvider()
        errors = _StubErrors()
        page_without_imdb = LANG_MATRIX_EN.replace(b"imdb.com/title/tt0133093", b"example.com/title/none")

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if "/searchbytitle" in url:
                raise errors.http_error(url, 500, "Internal Server Error")
            if url == "https://subf2m.co/subtitles/the-matrix-1999/english":
                raise errors.http_error(url, 404, "Not Found")
            if url == "https://subf2m.co/subtitles/the-matrix/english":
                return page_without_imdb
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                provider.search(
                    {"kind": "movie", "title": "The Matrix", "year": 1999, "imdb_id": "tt0133093"},
                    [{"alpha3": "eng", "alpha2": "en"}],
                    {"request_delay_ms": 0},
                )
            self.assertEqual(raised.exception.code, 500)
        finally:
            errors.close()

    def test_search_slug_fallback_raises_original_error_when_no_candidate_exists(self):
        provider = self.mod.SubF2MProvider()
        errors = _StubErrors()

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if "/searchbytitle" in url:
                raise errors.http_error(url, 500, "Internal Server Error")
            raise errors.http_error(url, 404, "Not Found")

        provider._http_get = stub
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                provider.search(
                    {"kind": "movie", "title": "The Matrix", "year": 1999, "imdb_id": "tt0133093"},
                    [{"alpha3": "eng", "alpha2": "en"}],
                    {"request_delay_ms": 0},
                )
            self.assertEqual(raised.exception.code, 500)
        finally:
            errors.close()

    def test_search_slug_fallback_raises_original_error_when_site_unreachable(self):
        provider = self.mod.SubF2MProvider()
        errors = _StubErrors()

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, referer, config
            if "/searchbytitle" in url:
                raise errors.http_error(url, 500, "Internal Server Error")
            raise urllib.error.URLError("connection refused")

        provider._http_get = stub
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                provider.search(
                    {"kind": "movie", "title": "The Matrix", "year": 1999, "imdb_id": "tt0133093"},
                    [{"alpha3": "eng", "alpha2": "en"}],
                    {"request_delay_ms": 0},
                )
            self.assertEqual(raised.exception.code, 500)
        finally:
            errors.close()

    def test_search_filters_rows_by_requested_forced_flag(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Chernobyl&l=": SEARCH_CHERNOBYL,
            "https://subf2m.co/subtitles/chernobyl/english": DETAIL_CHERNOBYL_EN,
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1, "year": 2019, "series_imdb_id": "tt7366338"},
            [{"alpha3": "eng", "alpha2": "en", "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["2956831"])

    def test_search_filters_rows_by_requested_hi_flag(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/searchbytitle?query=Dune&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/searchbytitle?query=tt1160419&l=": b"",
            "https://subf2m.co/subtitles/dune-2021/english": DETAIL_DUNE_EN,
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_search_episode_returns_episode_and_season_pack(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Chernobyl&l=": SEARCH_CHERNOBYL,
            "https://subf2m.co/subtitles/chernobyl/english": DETAIL_CHERNOBYL_EN,
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1, "year": 2019, "series_imdb_id": "tt7366338"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["2647618", "2956831"])
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("season", results[1]["matches"])
        self.assertTrue(results[0]["language"]["forced"])
        self.assertFalse(results[1]["language"]["forced"])

    def test_search_maps_brazilian_portuguese_country_variant(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/brazillian-portuguese": DETAIL_DUNE_EN.replace(
                b"/subtitles/dune-2021/english/3331049",
                b"/subtitles/dune-2021/brazillian-portuguese/2706706",
            ),
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["provider_payload"]["language_path"], "brazillian-portuguese")

    def test_search_maps_brazilian_portuguese_country_alpha2(self):
        provider = self.mod.SubF2MProvider()
        responses = {
            "https://subf2m.co/subtitles/searchbytitle?query=Dune%3A%20Part%20One&l=": SEARCH_DUNE,
            "https://subf2m.co/subtitles/dune-2021/brazillian-portuguese": DETAIL_DUNE_EN.replace(
                b"/subtitles/dune-2021/english/3331049",
                b"/subtitles/dune-2021/brazillian-portuguese/2706706",
            ),
        }

        provider._http_get = lambda url, timeout=15, referer=None, config=None: responses[url]
        results = provider.search(
            {"kind": "movie", "title": "Dune: Part One", "year": 2021, "imdb_id": "tt1160419"},
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["alpha2"], "pt")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")
        self.assertEqual(results[0]["provider_payload"]["language_path"], "brazillian-portuguese")
        self.assertEqual(results[0]["provider_payload"]["country_alpha2"], "BR")

    def test_download_follows_detail_gate_and_returns_archive(self):
        provider = self.mod.SubF2MProvider()
        archive_body = _zip_body(
            {
                "Dune.Part.One.2021.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nMovie line\n",
            }
        )
        responses = {
            "https://subf2m.co/subtitles/dune-2021/english/3331049": DOWNLOAD_GATE_DUNE,
            "https://subf2m.co/subtitles/dune-2021/english/3331049/download": archive_body,
        }
        calls = []

        def stub(url, timeout=15, referer=None, config=None):
            del timeout, config
            calls.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        result = provider.download(
            {
                "provider": "subf2m",
                "schema": 1,
                "page_url": "https://subf2m.co/subtitles/dune-2021/english/3331049",
                "filename": "subf2m.dune.english.3331049.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[1][1], "https://subf2m.co/subtitles/dune-2021/english/3331049")
        self.assertNotIn("content_b64", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive_body).hexdigest())
        self.assertEqual(result["member"], "Dune.Part.One.2021.en.srt")

    def test_download_returns_archive_with_episode_member(self):
        archive_body = _zip_body(
            {
                "Chernobyl.S01E02.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nEpisode two\n",
                "Chernobyl.S01E01.en.srt": b"1\n00:00:01,000 --> 00:00:02,000\nEpisode one\n",
            }
        )

        result = self.mod.build_download_payload(
            archive_body,
            {"filename": "chernobyl.zip", "season": 1, "episode": 1},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive_body).hexdigest())
        self.assertEqual(result["member"], "Chernobyl.S01E01.en.srt")

    def test_download_returns_rar_archive_for_host_picking(self):
        rar_body = b"Rar!\x1a\x07\x00" + b"\x00" * 64

        result = self.mod.build_download_payload(
            rar_body,
            {"filename": "chernobyl.rar", "season": 1, "episode": 1},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), rar_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(rar_body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("member", result)

    def test_download_keeps_direct_subtitle_body_as_content(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nPlain srt\n"

        result = self.mod.build_download_payload(body, {"filename": "dune.srt"})

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("archive_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_and_html_bodies(self):
        with self.assertRaises(ValueError):
            self.mod.build_download_payload(b"", {"page_url": "https://subf2m.co/x"})
        with self.assertRaises(ValueError):
            self.mod.build_download_payload(
                b"<!DOCTYPE html><html><body>error</body></html>",
                {"page_url": "https://subf2m.co/x"},
            )


if __name__ == "__main__":
    unittest.main()
