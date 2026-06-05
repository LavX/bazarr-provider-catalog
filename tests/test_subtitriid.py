import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subtitriid"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subtitriid_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


SEARCH_HTML = (FIXTURE_DIR / "subtitriid_search_inception.html").read_bytes()
DETAIL_HTML = (FIXTURE_DIR / "subtitriid_detail_inception.html").read_bytes()


class SubtitriIdParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_build_queries_keeps_movie_title_and_alternatives(self):
        queries = self.mod.build_queries(
            {
                "kind": "movie",
                "title": "Inception",
                "alternative_titles": ["Pirmsākums", "Inception", ""],
            }
        )

        self.assertEqual(queries, ["Inception", "Pirmsākums"])

    def test_parse_search_results_extracts_result_blocks(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], "406")
        self.assertEqual(rows[0]["title"], "Inception")
        self.assertEqual(
            rows[0]["page_url"],
            "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406",
        )

    def test_parse_detail_page_extracts_movie_metadata_and_download(self):
        row = self.mod.parse_detail_page(DETAIL_HTML, "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406")

        self.assertEqual(row["entry_id"], "406")
        self.assertEqual(row["title"], "Inception")
        self.assertEqual(row["local_title"], "Pirmsākums")
        self.assertEqual(row["year"], 2010)
        self.assertEqual(row["imdb_id"], "tt1375666")
        self.assertEqual(row["download_count"], 1258)
        self.assertEqual(row["download_url"], "https://subtitri.do.am/load/0-0-0-406-20")

    def test_parse_detail_page_derives_entry_id_from_download_link_without_page_url(self):
        row = self.mod.parse_detail_page(DETAIL_HTML)

        self.assertEqual(row["entry_id"], "406")

    def test_derive_matches_uses_title_year_and_imdb(self):
        row = self.mod.parse_detail_page(DETAIL_HTML, "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406")

        matches = self.mod.derive_matches({"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"}, row)

        self.assertEqual(matches, ["title", "year", "imdb_id"])

    def test_derive_matches_accepts_local_title_as_alternative(self):
        row = self.mod.parse_detail_page(DETAIL_HTML, "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406")

        matches = self.mod.derive_matches({"kind": "movie", "title": "Pirmsākums", "year": 2010}, row)

        self.assertIn("title", matches)
        self.assertIn("year", matches)

    def test_parse_titles_keeps_literal_slash_titles_intact(self):
        for raw_title in ("Face/Off", "Frost/Nixon"):
            detail = DETAIL_HTML.replace(b"Pirms\xc4\x81kums / Inception", raw_title.encode("utf-8"))
            row = self.mod.parse_detail_page(
                detail,
                "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406",
            )

            self.assertEqual(row["title"], raw_title)
            self.assertEqual(row["local_title"], "")
            self.assertIn("title", self.mod.derive_matches({"kind": "movie", "title": raw_title}, row))

    def test_parse_titles_splits_spaced_local_original_separator(self):
        title, local_title = self.mod._parse_titles(
            '<h1 class="main-header"><KBD> Pirmsākums / Inception </KBD></h1>'
        )

        self.assertEqual(title, "Inception")
        self.assertEqual(local_title, "Pirmsākums")


class SubtitriIdProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_latvian_movie_result(self):
        provider = self.mod.SubtitriIdProvider()
        calls = []

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if url.startswith("https://subtitri.do.am/search/"):
                return SEARCH_HTML
            if url == "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406":
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "lav", "alpha2": "lv", "hi": False, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[0], "https://subtitri.do.am/search/?q=Inception")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider"], "subtitriid")
        self.assertEqual(results[0]["language"], {"alpha3": "lav", "alpha2": "lv", "hi": False, "forced": False})
        self.assertEqual(results[0]["provider_payload"]["entry_id"], "406")
        self.assertEqual(results[0]["provider_payload"]["url"], "https://subtitri.do.am/load/0-0-0-406-20")
        self.assertIn("imdb_id", results[0]["matches"])

    def test_search_skips_unverified_forced_or_hi_variants(self):
        provider = self.mod.SubtitriIdProvider()
        provider._http_get = lambda url, timeout=15, referer=None: DETAIL_HTML if "4-1-0-406" in url else SEARCH_HTML

        forced_results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "lav", "alpha2": "lv", "hi": False, "forced": True}],
            {},
        )
        hi_results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "lav", "alpha2": "lv", "hi": True, "forced": False}],
            {},
        )

        self.assertEqual(forced_results, [])
        self.assertEqual(hi_results, [])

    def test_search_checks_alternative_titles_even_after_primary_title_matches(self):
        provider = self.mod.SubtitriIdProvider()
        calls = []
        alt_search_html = SEARCH_HTML.replace(b"4-1-0-406", b"4-1-0-407")
        alt_detail_html = DETAIL_HTML.replace(b"406", b"407")

        def get_stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if url == "https://subtitri.do.am/search/?q=Inception":
                return SEARCH_HTML
            if url == "https://subtitri.do.am/search/?q=Pirms%C4%81kums":
                return alt_search_html
            if url == "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406":
                return DETAIL_HTML
            if url == "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-407":
                return alt_detail_html
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = get_stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "alternative_titles": ["Pirmsākums"],
                "year": 2010,
                "imdb_id": "tt1375666",
            },
            [{"alpha3": "lav", "alpha2": "lv"}],
            {"request_delay_ms": 0},
        )

        self.assertIn("https://subtitri.do.am/search/?q=Pirms%C4%81kums", calls)
        self.assertEqual([result["provider_payload"]["entry_id"] for result in results], ["406", "407"])

    def test_search_accepts_legacy_lva_language_alias(self):
        provider = self.mod.SubtitriIdProvider()
        provider._http_get = lambda url, timeout=15, referer=None: DETAIL_HTML if "4-1-0-406" in url else SEARCH_HTML

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "lva", "alpha2": "lv"}],
            {},
        )

        self.assertEqual(results[0]["language"]["alpha3"], "lav")

    def test_search_rejects_unsupported_language_and_episode(self):
        provider = self.mod.SubtitriIdProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used"))

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "episode", "series": "Inception"}, [{"alpha3": "lav"}], {}), [])

    def test_download_extracts_zip_subtitle(self):
        archive = _zip_body(
            {
                "poster.jpg": b"not a subtitle",
                "Inception.2010.lv.srt": b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n",
            }
        )
        provider = self.mod.SubtitriIdProvider()
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "url": "https://subtitri.do.am/load/0-0-0-406-20",
                "page_url": "https://subtitri.do.am/load/subtitri_2010_gada/inception_2010/4-1-0-406",
                "filename": "subtitriid.inception.2010.lv.zip",
            },
            {"alpha3": "lav", "alpha2": "lv"},
            {},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_concatenates_multipart_movie_subtitles(self):
        archive = _zip_body(
            {
                "Inception.CD2.srt": b"2\n00:00:03,000 --> 00:00:04,000\nOtra puse\n",
                "Inception.CD1.srt": b"1\n00:00:01,000 --> 00:00:02,000\nPirma puse\n",
            }
        )

        result = self.mod.extract_download(archive, {"filename": "subtitriid.inception.2010.lv.zip"})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(
            decoded,
            b"1\n00:00:01,000 --> 00:00:02,000\nPirma puse\n\n2\n00:00:03,000 --> 00:00:04,000\nOtra puse\n",
        )
        self.assertEqual(result["format"], "srt")

    def test_download_skips_vobsub_pair_sub_file(self):
        archive = _zip_body(
            {
                "Inception.sub": b"\x00\x01binary vobsub payload",
                "Inception.idx": b"# VobSub index file",
                "Inception.srt": b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n",
            }
        )

        result = self.mod.extract_download(archive, {"filename": "subtitriid.inception.2010.lv.zip"})

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n")
        self.assertEqual(result["format"], "srt")

    def test_download_rejects_html_error_page(self):
        html_error = (
            b"<!DOCTYPE html>\n"
            b"<!-- uCoz served this error page -->\n"
            b"<html><head><title>404</title></head>"
            b"<body>File not found</body></html>\n"
        )

        # The HTML comment ends in --> so the old subtitle-cue heuristic accepted it.
        self.assertTrue(self.mod._looks_like_subtitle(html_error))
        with self.assertRaises(ValueError):
            self.mod.extract_download(html_error, {"filename": "subtitriid.inception.2010.lv.zip"})

    def test_download_returns_direct_subtitle_body(self):
        result = self.mod.extract_download(
            b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n",
            {"filename": "inception.lv.srt"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nSveiki\n")
        self.assertEqual(result["encoding"], "utf-8")


if __name__ == "__main__":
    unittest.main()
