import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "bollynook"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "bollynook_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "bollynook_search_pathaan.html").read_bytes()
DETAIL_HTML = (FIXTURE_DIR / "bollynook_detail_pathaan.html").read_bytes()


def _zip_body(name, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, body)
    return stream.getvalue()


def _zip_files(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


def _search_row(movie_id, title, language_code="eng"):
    slug = title.lower().replace(" ", "-").replace("/", "-")
    return f"""
    <li>
      Movie:&nbsp;
      <a href="/en/bollywood-movie-subtitles/{movie_id}/{slug}/">{title}</a>
      <img src="/images/zastave/{language_code}.png" />
      Published: 01.01.2024.
    </li>
    """.encode("utf-8")


def _search_page(*rows):
    return b"<ul>" + b"".join(rows) + b"</ul>"


def _detail_page(title, year, language_code="eng", movie_id="22997"):
    return f"""
    <h1>{title}</h1>
    <div>Year: <strong>{year}</strong></div>
    <img src="/images/zastave/{language_code}.png" />
    <a class="downloads" href="/uploaded_pictures/content/titlovi/{movie_id}-{title.lower()}-{language_code}.zip">Download</a>
    """.encode("utf-8")


class BollyNookParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_result(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)
        self.assertEqual(rows[0]["movie_id"], "22997")
        self.assertEqual(rows[0]["title"], "Pathaan - 2023")
        self.assertEqual(rows[0]["language"], "eng")

    def test_parse_search_results_maps_extra_site_language_codes(self):
        cases = {
            "alb": "sqi",
            "ale": "aze",
            "arm": "hye",
            "arg": "arg",
            "bel": "bel",
            "bos": "bos",
            "gle": "gle",
            "mac": "mkd",
        }
        for site_code, alpha3 in cases.items():
            with self.subTest(site_code=site_code):
                rows = self.mod.parse_search_results(
                    _search_page(_search_row("22997", "Pathaan - 2023", site_code))
                )
                self.assertEqual(rows[0]["language"], alpha3)

    def test_parse_detail_extracts_download_link(self):
        detail = self.mod.parse_detail(DETAIL_HTML, "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/")
        self.assertEqual(detail["title"], "Pathaan")
        self.assertEqual(detail["year"], 2023)
        self.assertEqual(detail["language"], "eng")
        self.assertEqual(detail["uploader"], "Vukov Julijana")
        self.assertEqual(
            detail["download_url"],
            "https://www.bollynook.com/uploaded_pictures/content/titlovi/22997-pathaan-CD1-2023-eng.zip",
        )

    def test_movie_with_year_emits_precise_then_loose_query(self):
        self.assertEqual(
            self.mod.build_queries({"kind": "movie", "title": "Pathaan", "year": 2023}),
            ["Pathaan 2023", "Pathaan"],
        )

    def test_row_matches_rejects_wrong_movie_year(self):
        self.assertFalse(
            self.mod._row_matches_video(
                {"kind": "movie", "title": "Suspiria", "year": 2018},
                {"title": "Suspiria - 1977", "year": 1977},
                {"title": "Suspiria", "year": 1977},
            )
        )


class BollyNookProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fetches_detail_and_returns_movie_result(self):
        provider = self.mod.BollyNookProvider()
        responses = {
            "https://www.bollynook.com/en/search/": SEARCH_HTML,
            "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/": DETAIL_HTML,
        }
        calls = []

        def stub(url, data=None, timeout=15, referer=None):
            del timeout
            calls.append((url, data, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Pathaan", "year": 2023},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[0][0], "https://www.bollynook.com/en/search/")
        self.assertEqual(calls[0][1], b"type=2&title=Pathaan+2023&language=eng&submit=Search")
        self.assertEqual(results[0]["provider"], "bollynook")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("year", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["movie_id"], "22997")

    def test_search_uses_extra_language_site_code(self):
        provider = self.mod.BollyNookProvider()
        responses = {
            "https://www.bollynook.com/en/search/": _search_page(
                _search_row("22997", "Pathaan - 2023", "alb")
            ),
            "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan---2023/": _detail_page(
                "Pathaan", 2023, "alb", "22997"
            ),
        }
        calls = []

        def stub(url, data=None, timeout=15, referer=None):
            del timeout
            calls.append((url, data, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Pathaan", "year": 2023},
            [{"alpha3": "sqi", "alpha2": "sq"}],
            {"request_delay_ms": 0},
        )

        self.assertIn(b"language=alb", calls[0][1])
        self.assertEqual(results[0]["language"]["alpha3"], "sqi")

    def test_search_returns_requested_languages_across_candidates(self):
        provider = self.mod.BollyNookProvider()
        search_body = _search_page(
            _search_row("22997", "Pathaan - 2023", "eng"),
            _search_row("22998", "Pathaan - 2023", "hin"),
        )
        responses = {
            "https://www.bollynook.com/en/search/": search_body,
            "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan---2023/": _detail_page(
                "Pathaan", 2023, "eng", "22997"
            ),
            "https://www.bollynook.com/en/bollywood-movie-subtitles/22998/pathaan---2023/": _detail_page(
                "Pathaan", 2023, "hin", "22998"
            ),
        }

        def stub(url, data=None, timeout=15, referer=None):
            del data, timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Pathaan", "year": 2023},
            [{"alpha3": "eng", "alpha2": "en"}, {"alpha3": "hin", "alpha2": "hi"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([result["language"]["alpha3"] for result in results], ["eng", "hin"])

    def test_search_skips_failed_detail_fetch(self):
        provider = self.mod.BollyNookProvider()
        search_body = _search_page(
            _search_row("22997", "Pathaan - 2023", "eng"),
            _search_row("22998", "Pathaan - 2023", "eng"),
        )
        valid_url = "https://www.bollynook.com/en/bollywood-movie-subtitles/22998/pathaan---2023/"

        def stub(url, data=None, timeout=15, referer=None):
            del data, timeout, referer
            if url == "https://www.bollynook.com/en/search/":
                return search_body
            if url == valid_url:
                return _detail_page("Pathaan", 2023, "eng", "22998")
            raise OSError("detail fetch failed")

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Pathaan", "year": 2023},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([result["provider_payload"]["movie_id"] for result in results], ["22998"])

    def test_search_skips_malformed_detail_page(self):
        provider = self.mod.BollyNookProvider()
        search_body = _search_page(
            _search_row("22997", "Pathaan - 2023", "eng"),
            _search_row("22998", "Pathaan - 2023", "eng"),
        )
        malformed_url = "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan---2023/"
        valid_url = "https://www.bollynook.com/en/bollywood-movie-subtitles/22998/pathaan---2023/"

        def stub(url, data=None, timeout=15, referer=None):
            del data, timeout, referer
            if url == "https://www.bollynook.com/en/search/":
                return search_body
            if url == malformed_url:
                return b"<h1>Pathaan</h1>"
            if url == valid_url:
                return _detail_page("Pathaan", 2023, "eng", "22998")
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_request = stub
        results = provider.search(
            {"kind": "movie", "title": "Pathaan", "year": 2023},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual([result["provider_payload"]["movie_id"] for result in results], ["22998"])

    def test_download_returns_zip_archive_for_host_extraction(self):
        provider = self.mod.BollyNookProvider()
        body = _zip_body("Pathaan.2023.eng.srt", b"1\n00:00:01,000 --> 00:00:02,000\nMovie line\n")
        provider._http_request = lambda url, data=None, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "bollynook",
                "schema": 1,
                "movie_id": "22997",
                "url": "https://www.bollynook.com/uploaded_pictures/content/titlovi/22997-pathaan-CD1-2023-eng.zip",
                "page_url": "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/",
                "filename": "22997-pathaan-CD1-2023-eng.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Pathaan.2023.eng.srt")
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_selects_zip_member_for_requested_language(self):
        provider = self.mod.BollyNookProvider()
        body = _zip_files(
            {
                "Pathaan.2023.hin.srt": b"1\nHindi line\n",
                "Pathaan.2023.eng.srt": b"1\nEnglish line\n",
            }
        )
        provider._http_request = lambda url, data=None, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "bollynook",
                "schema": 1,
                "movie_id": "22997",
                "url": "https://www.bollynook.com/uploaded_pictures/content/titlovi/22997-pathaan.zip",
                "page_url": "https://www.bollynook.com/en/bollywood-movie-subtitles/22997/pathaan/",
                "filename": "22997-pathaan.zip",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Pathaan.2023.eng.srt")

    def test_download_returns_direct_subtitle_body_as_content(self):
        provider = self.mod.BollyNookProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nDirect line\n"
        provider._http_request = lambda url, data=None, timeout=15, referer=None: body

        result = provider.download(
            {
                "provider": "bollynook",
                "schema": 1,
                "movie_id": "22997",
                "url": "https://www.bollynook.com/uploaded_pictures/content/titlovi/22997-pathaan.srt",
                "filename": "22997-pathaan.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("archive_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.BollyNookProvider()
        provider._http_request = lambda url, data=None, timeout=15, referer=None: b""
        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "bollynook", "url": "https://www.bollynook.com/x.zip"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.BollyNookProvider()
        provider._http_request = (
            lambda url, data=None, timeout=15, referer=None: b"<!DOCTYPE html><html><body>error</body></html>"
        )
        with self.assertRaises(ValueError):
            provider.download(
                {"provider": "bollynook", "url": "https://www.bollynook.com/x.zip"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )


if __name__ == "__main__":
    unittest.main()
