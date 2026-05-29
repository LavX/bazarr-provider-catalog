import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "yifysubtitles"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("yifysubtitles_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOVIE_HTML = b"""
<table class="table other-subs">
  <tbody>
    <tr data-id="364913">
      <td class="rating-cell"><span class="label label-success">7</span></td>
      <td class="flag-cell"><span class="flag flag-gb"></span><span class="sub-lang">English</span></td>
      <td>
        <a href="/subtitles/dune-part-one-2021-english-yify-364913">
          <span class="text-muted">subtitle</span> Dune.2021.1080p.HMAX.WEBRip.DDP5.1.Atmos.x264-CM
        </a>
      </td>
      <td class="other-cell"></td>
      <td class="uploader-cell"><a href="/user/TeTeT">TeTeT</a></td>
    </tr>
    <tr data-id="364982">
      <td class="rating-cell"><span class="label label-success">9</span></td>
      <td class="flag-cell"><span class="flag flag-es"></span><span class="sub-lang">Spanish</span></td>
      <td>
        <a href="/subtitles/dune-part-one-2021-spanish-yify-364982">
          <span class="text-muted">subtitle</span> Dune (2021) [1080p] [WEBRip] [YTS.MX]
        </a>
      </td>
      <td class="other-cell"><span class="hi-subtitle" title="hearing impaired"></span></td>
      <td class="uploader-cell">uploader</td>
    </tr>
  </tbody>
</table>
"""
DETAIL_HTML = b"""
<a class="btn-icon download-subtitle" href="/subtitle/dune-2021-english-yify-364913.zip">
  <span class="title">DOWNLOAD SUBTITLE</span>
</a>
"""
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nYIFY subtitle.\n"


def _zip_with(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class YifyParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_movie_page_extracts_rows(self):
        rows = self.mod.parse_movie_page(MOVIE_HTML)

        self.assertEqual(len(rows), 2)
        english = rows[0]
        self.assertEqual(english["subtitle_id"], "364913")
        self.assertEqual(english["language"], "eng")
        self.assertFalse(english["hi"])
        self.assertEqual(english["rating"], 7)
        self.assertEqual(english["uploader"], "TeTeT")
        self.assertEqual(
            english["page_url"],
            "https://yifysubtitles.ch/subtitles/dune-part-one-2021-english-yify-364913",
        )
        self.assertIn("WEBRip", english["release"])
        self.assertTrue(rows[1]["hi"])
        self.assertEqual(rows[1]["language"], "spa")

    def test_parse_download_url(self):
        self.assertEqual(
            self.mod.parse_download_url(DETAIL_HTML),
            "https://yifysubtitles.ch/subtitle/dune-2021-english-yify-364913.zip",
        )

    def test_derive_matches_movie(self):
        row = self.mod.parse_movie_page(MOVIE_HTML)[0]
        matches = self.mod.derive_matches(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "CM",
                "resolution": "1080p",
                "source": "Web",
            },
            row,
        )

        self.assertEqual(set(matches), {"title", "imdb_id", "release_group", "resolution", "source"})


class YifyProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_filters_language_and_preserves_requested_variant(self):
        provider = self.mod.YifySubtitlesProvider()
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url == "https://yifysubtitles.ch/movie-imdb/tt1160419":
                return MOVIE_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "release_group": "CM",
                "resolution": "1080p",
                "source": "Web",
            },
            [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(called, [("https://yifysubtitles.ch/movie-imdb/tt1160419", "https://yifysubtitles.ch/")])
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["provider"], "yifysubtitles")
        self.assertEqual(result["language"], {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False})
        self.assertEqual(result["provider_payload"]["page_url"], rows_page_url())
        self.assertIn("release_group", result["matches"])

    def test_search_matches_hearing_impaired_variant_only_when_requested(self):
        provider = self.mod.YifySubtitlesProvider()
        provider._http_get = lambda url, timeout=15, referer=None: MOVIE_HTML

        normal = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "spa", "alpha2": "es", "hi": False}],
            {},
        )
        hi = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "spa", "alpha2": "es", "hi": True}],
            {},
        )

        self.assertEqual(normal, [])
        self.assertEqual(len(hi), 1)
        self.assertTrue(hi[0]["language"]["hi"])

    def test_search_skips_forced_variant_without_network(self):
        provider = self.mod.YifySubtitlesProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected network"))

        results = provider.search(
            {"kind": "movie", "title": "Dune", "imdb_id": "tt1160419"},
            [{"alpha3": "eng", "alpha2": "en", "forced": True}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_skips_non_movie_or_missing_imdb_without_network(self):
        provider = self.mod.YifySubtitlesProvider()
        provider._http_get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected network"))

        self.assertEqual(provider.search({"kind": "episode", "series": "Dune"}, [{"alpha3": "eng"}], {}), [])
        self.assertEqual(provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng"}], {}), [])

    def test_search_returns_empty_for_missing_imdb_page(self):
        provider = self.mod.YifySubtitlesProvider()

        def raise_not_found(url, timeout=15, referer=None):
            del timeout, referer
            raise self.mod.urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        provider._http_get = raise_not_found
        results = provider.search(
            {"kind": "movie", "title": "Missing", "imdb_id": "tt0000000"},
            [{"alpha3": "eng"}],
            {},
        )

        self.assertEqual(results, [])

    def test_download_follows_detail_page_and_extracts_zip(self):
        archive = _zip_with({"Dune.2021.1080p.HMAX.WEBRip.DDP5.1.Atmos.x264-CM-English.srt": SRT_BODY})
        provider = self.mod.YifySubtitlesProvider()

        def stub(url, timeout=15, referer=None):
            del timeout
            if url == rows_page_url():
                return DETAIL_HTML
            if url == "https://yifysubtitles.ch/subtitle/dune-2021-english-yify-364913.zip":
                return archive
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        result = provider.download(
            {
                "page_url": rows_page_url(),
                "release": "Dune.2021.1080p.HMAX.WEBRip.DDP5.1.Atmos.x264-CM",
                "filename": "yifysubtitles.364913.en.zip",
            },
            {"alpha3": "eng"},
            {},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(data, SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["format"], "srt")


def rows_page_url():
    return "https://yifysubtitles.ch/subtitles/dune-part-one-2021-english-yify-364913"


if __name__ == "__main__":
    unittest.main()
