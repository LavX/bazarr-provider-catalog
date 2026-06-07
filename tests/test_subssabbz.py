import base64
import hashlib
import importlib.util
import io
import unittest
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subssabbz"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("subssabbz_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_INCEPTION_EN_HTML = (FIXTURE_DIR / "subssabbz_search_inception_en.html").read_bytes()
SEARCH_INCEPTION_BG_HTML = (FIXTURE_DIR / "subssabbz_search_inception_bg.html").read_bytes()
SEARCH_GOT_BG_HTML = (FIXTURE_DIR / "subssabbz_search_game_of_thrones_bg.html").read_bytes()


DAREDEVIL_EN_HTML = b"""
<!doctype html>
<html>
<body>
<table>
  <tr class="subs-row">
    <td class="c1field">TV</td>
    <td class="c1trailer">&nbsp;</td>
    <td class="c1notice">&nbsp;</td>
    <td class="c2field">
      <a href="http://subs.sab.bz/index.php?s=session&amp;act=download&amp;attach_id=88001"
         onMouseover="ddrivetip('&lt;b&gt;release&lt;/b&gt;: Daredevil.S01E01.WEB-DL')">Daredevil</a> (2015) [1x1]
    </td>
    <td>10 Apr 2015</td>
    <td>English</td>
    <td>1</td>
    <td>23.976</td>
    <td>translator</td>
    <td><a href="http://www.imdb.com/title/tt3322312/">URL</a></td>
    <td>1200</td>
    <td><img alt="Rating: 5" title="Rating: 5" /></td>
  </tr>
</table>
</body>
</html>
"""

SONS_OF_ANARCHY_EN_HTML = b"""
<!doctype html>
<html>
<body>
<table>
  <tr class="subs-row">
    <td class="c1field">TV</td>
    <td class="c1trailer">&nbsp;</td>
    <td class="c1notice">&nbsp;</td>
    <td class="c2field">
      <a href="http://subs.sab.bz/index.php?s=session&amp;act=download&amp;attach_id=88002"
         onMouseover="ddrivetip('&lt;b&gt;release&lt;/b&gt;: Sons.of.Anarchy.S05E11')">Sons of Anarchy - 05x11</a> (2012)
    </td>
    <td>21 Nov 2012</td>
    <td>English</td>
    <td>1</td>
    <td>23.976</td>
    <td>translator</td>
    <td><a href="http://www.imdb.com/title/tt1124373/">URL</a></td>
    <td>1200</td>
    <td><img alt="Rating: 5" title="Rating: 5" /></td>
  </tr>
</table>
</body>
</html>
"""


GREYS_ANATOMY_EN_HTML = b"""
<!doctype html>
<html>
<body>
<table>
  <tr class="subs-row">
    <td class="c1field">TV</td>
    <td class="c1trailer">&nbsp;</td>
    <td class="c1notice">&nbsp;</td>
    <td class="c2field">
      <a href="http://subs.sab.bz/index.php?s=session&amp;act=download&amp;attach_id=88003"
         onMouseover="ddrivetip('&lt;b&gt;release&lt;/b&gt;: Greys.Anatomy.S01E01')">Grey's Anatomy</a> (2005) [1x1]
    </td>
    <td>27 Mar 2005</td>
    <td>English</td>
    <td>1</td>
    <td>23.976</td>
    <td>translator</td>
    <td><a href="http://www.imdb.com/title/tt0413573/">URL</a></td>
    <td>1200</td>
    <td><img alt="Rating: 5" title="Rating: 5" /></td>
  </tr>
</table>
</body>
</html>
"""


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SubsSabBzParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_english_movie_row(self):
        rows = self.mod.parse_search_results(SEARCH_INCEPTION_EN_HTML)

        self.assertEqual(rows[0]["download_url"], "http://subs.sab.bz/index.php?act=download&attach_id=51168")
        self.assertEqual(rows[0]["title"], "Inception")
        self.assertEqual(rows[0]["year"], 2010)
        self.assertEqual(rows[0]["language"], "eng")
        self.assertEqual(rows[0]["num_cds"], 2)
        self.assertEqual(rows[0]["fps"], 23.976)
        self.assertEqual(rows[0]["imdb_id"], "tt1375666")
        self.assertEqual(rows[0]["uploader"], "ZIL")

    def test_parse_search_results_extracts_bulgarian_row(self):
        rows = self.mod.parse_search_results(SEARCH_INCEPTION_BG_HTML)

        self.assertEqual(rows[0]["language"], "bul")
        self.assertEqual(rows[0]["download_url"], "http://subs.sab.bz/index.php?act=download&attach_id=51764")

    def test_parse_search_results_extracts_tv_marker_from_title_text(self):
        rows = self.mod.parse_search_results(SONS_OF_ANARCHY_EN_HTML)

        self.assertEqual(rows[0]["title"], "Sons of Anarchy")
        self.assertEqual(rows[0]["season"], 5)
        self.assertEqual(rows[0]["episode"], 11)

    def test_extract_archive_files_reads_zip_members(self):
        body = _zip_body(
            {
                "Inception.2010.DVDRip.XviD.AC3-ViSiON.srt": "subtitle",
                "README.txt": "ignored",
            }
        )

        rows = self.mod.extract_archive_files(body)

        self.assertEqual(rows[0]["filename"], "Inception.2010.DVDRip.XviD.AC3-ViSiON.srt")
        self.assertEqual(rows[0]["content"], b"subtitle")


class SubsSabBzProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_posts_language_and_returns_archive_files(self):
        provider = self.mod.SubsSabBzProvider()
        calls = []
        archive = _zip_body({"Inception.DVDRiP.XviD-ARROW.CD1.srt": "english"})

        def post_stub(url, data, timeout=30, referer=None):
            del timeout, referer
            calls.append((url, data))
            return SEARCH_INCEPTION_EN_HTML

        provider._http_post = post_stub
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
                "release_group": "ARROW",
                "fps": 23.976,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0][1]["select-language"], "1")
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["download_url"], "http://subs.sab.bz/index.php?act=download&attach_id=51168")

    def test_search_episode_filters_archive_members_by_episode(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "Game.of.Thrones.S01E02.720p.HDTV.x264-CTU.srt": "wrong episode",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right episode",
            }
        )
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_GOT_BG_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
                "release_group": "CTU",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertIn("episode", results[0]["matches"])
        self.assertIn("series_imdb_id", results[0]["matches"])

    def test_search_episode_uses_normalized_series_title_for_row_filter(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Daredevil.S01E01.WEB-DL.srt": "subtitle"})
        calls = []

        def post_stub(url, data, timeout=30, referer=None):
            del url, timeout, referer
            calls.append(data)
            return DAREDEVIL_EN_HTML

        provider._http_post = post_stub
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Marvel's Daredevil",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt3322312",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0]["movie"], "Daredevil")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Daredevil.S01E01.WEB-DL.srt")

    def test_search_keeps_apostrophe_titles_for_row_matching(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Greys.Anatomy.S01E01.srt": "subtitle"})
        calls = []

        def post_stub(url, data, timeout=30, referer=None):
            del url, timeout, referer
            calls.append(data)
            return GREYS_ANATOMY_EN_HTML

        provider._http_post = post_stub
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Grey's Anatomy",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0413573",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        # The POST query drops the apostrophe, but the row filter must keep it so the
        # apostrophe-bearing site row still tokenizes to the requested series.
        self.assertEqual(calls[0]["movie"], "Greys Anatomy")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Greys.Anatomy.S01E01.srt")
        self.assertIn("series", results[0]["matches"])

    def test_search_accepts_generic_member_with_release_tags(self):
        provider = self.mod.SubsSabBzProvider()
        # No SxxEyy marker, only release tags that must not be parsed as 7x20 / 2x64.
        archive = _zip_body({"Game.of.Thrones.720p.HDTV.x264-CTU.srt": "subtitle"})
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_GOT_BG_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Game.of.Thrones.720p.HDTV.x264-CTU.srt")
        # Release tags must not be mistaken for season/episode markers.
        self.assertEqual(self.mod._season_episode_from_filename("Game.of.Thrones.720p.HDTV.x264-CTU.srt"), (None, None))

    def test_search_keeps_multi_cd_members_distinct(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "CD1/Inception.srt": "part one",
                "CD2/Inception.srt": "part two",
            }
        )
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_INCEPTION_EN_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        filenames = sorted(item["filename"] for item in results)
        self.assertEqual(filenames, ["CD1/Inception.srt", "CD2/Inception.srt"])
        # The two discs must keep distinct result ids so the host can stage both members.
        first = next(item for item in results if item["filename"] == "CD1/Inception.srt")
        second = next(item for item in results if item["filename"] == "CD2/Inception.srt")
        self.assertNotEqual(first["id"], second["id"])

    def test_search_result_id_includes_hi_and_forced_flags(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Inception.DVDRiP.XviD-ARROW.srt": "english"})
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_INCEPTION_EN_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "eng", "alpha2": "en", "hi": True},
                {"alpha3": "eng", "alpha2": "en", "forced": True},
            ],
            {},
        )

        ids = [item["id"] for item in results]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

    def test_search_episode_filters_ambiguous_numeric_archive_members(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "01.srt": "right episode",
                "02.srt": "wrong episode",
            }
        )
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_GOT_BG_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual([item["filename"] for item in results], ["01.srt"])

    def test_search_preserves_hi_and_forced_requested_flags(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Inception.DVDRiP.XviD-ARROW.srt": "english"})
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_INCEPTION_EN_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en", "hi": True, "forced": True}],
            {},
        )

        self.assertTrue(results[0]["language"]["hi"])
        self.assertTrue(results[0]["language"]["forced"])

    def test_search_ignores_unsupported_language_and_incomplete_video(self):
        provider = self.mod.SubsSabBzProvider()

        self.assertEqual(provider.search({"kind": "movie", "title": "Inception"}, [{"alpha3": "fra"}], {}), [])
        self.assertEqual(provider.search({"kind": "episode", "series": "Game of Thrones"}, [{"alpha3": "bul"}], {}), [])

    def test_http_post_retries_temporary_403_response(self):
        provider = self.mod.SubsSabBzProvider()
        sleeps = []
        original_sleep = self.mod.time.sleep

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b"ok"

        class Opener:
            def __init__(self):
                self.calls = 0

            def open(self, request, timeout=30):
                del request, timeout
                self.calls += 1
                if self.calls < 3:
                    raise urllib.error.HTTPError(
                        self.mod.SEARCH_URL,
                        403,
                        "Forbidden",
                        hdrs=None,
                        fp=io.BytesIO(b""),
                    )
                return Response()

        opener = Opener()
        opener.mod = self.mod
        provider._opener = opener
        self.mod.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            body = provider._http_post(self.mod.SEARCH_URL, {"act": "search"})
        finally:
            self.mod.time.sleep = original_sleep

        self.assertEqual(body, b"ok")
        self.assertEqual(opener.calls, 3)
        self.assertEqual(sleeps, [10, 10])

    def test_search_payload_uses_site_language_selector_ids(self):
        self.assertEqual(self.mod._search_payload({"kind": "movie"}, "Inception", "eng")["select-language"], "1")
        self.assertEqual(self.mod._search_payload({"kind": "movie"}, "Inception", "bul")["select-language"], "2")

    def test_download_returns_zip_archive_bytes_with_episode(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Game.of.Thrones.S01E01.srt": "subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                "filename": "Game.of.Thrones.S01E01.srt",
                "episode": 1,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 1)
        # Host-side extraction: the worker must not return decoded content or an encoding.
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_bytes_with_episode(self):
        provider = self.mod.SubsSabBzProvider()
        archive = b"Rar!\x1a\x07\x00" + b"payload-bytes"
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88002",
                "filename": "Game.of.Thrones.S01E02.srt",
                "episode": 2,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 2)
        self.assertNotIn("content_b64", result)

    def test_download_carries_no_episode_for_movies(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Inception.2010.DVDRip.srt": "subtitle"})
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=51168",
                "filename": "Inception.2010.DVDRip.srt",
                "episode": None,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertIsNone(result["episode"])

    def test_download_returns_content_for_direct_subtitle_body(self):
        provider = self.mod.SubsSabBzProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"1\r\nHello\r\n"

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=51168",
                "filename": "Inception.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )
        data = base64.b64decode(result["content_b64"])

        self.assertEqual(data, b"1\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["format"], "srt")
        # The worker no longer guesses an encoding; the host runs chardet.
        self.assertNotIn("encoding", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.SubsSabBzProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b""

        with self.assertRaises(ValueError):
            provider.download(
                {"download_url": "http://subs.sab.bz/index.php?act=download&attach_id=1"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_rejects_html_error_body(self):
        provider = self.mod.SubsSabBzProvider()
        provider._http_get = lambda url, timeout=30, referer=None: b"<!doctype html><html><body>error</body></html>"

        with self.assertRaises(ValueError):
            provider.download(
                {"download_url": "http://subs.sab.bz/index.php?act=download&attach_id=1"},
                {"alpha3": "eng", "alpha2": "en"},
                {},
            )

    def test_download_pins_exact_member_for_multi_member_zip(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right",
                "Game.of.Thrones.S01E02.720p.HDTV.x264-CTU.srt": "wrong episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                "filename": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "release_info": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertNotIn("episode", result)
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())

    def test_download_pins_release_winner_when_filename_absent(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "ctu",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-IMMERSE.srt": "immerse",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                # Search picked the CTU member; its exact filename narrows the pack even
                # though the host only sees one episode across both members.
                "filename": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.different.srt",
                "release_info": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.different.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")
        self.assertNotIn("episode", result)

    def test_download_defers_when_release_tie_is_ambiguous(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "ctu",
                "Game.of.Thrones.S01E01.1080p.WEB.x265-NTb.srt": "ntb",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                # Filename is not a member and release_info shares no release tag with
                # either member: no confident winner, so defer to host episode selection.
                "filename": "Game.of.Thrones.S01E01.srt",
                "release_info": "Game.of.Thrones.S01E01.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 1)

    def test_download_does_not_pin_wrong_season_for_repeated_episode(self):
        provider = self.mod.SubsSabBzProvider()
        # Season pack repeats episode 5 across seasons: episode-only must not cross seasons.
        archive = _zip_body(
            {
                "Show.S01E05.720p.HDTV.x264-CTU.srt": "season one",
                "Show.S02E05.720p.HDTV.x264-CTU.srt": "season two",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                # Filename not present (forces scoring); both members share the CTU tag, so
                # only the season+episode narrowing keeps the right one and breaks the tie.
                "filename": "Show.S02E05.HDTV.srt",
                "release_info": "Show.S02E05.720p.HDTV.x264-CTU.srt",
                "season": 2,
                "episode": 5,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(result["member"], "Show.S02E05.720p.HDTV.x264-CTU.srt")

    def test_download_ignores_sidecar_and_release_token_collisions(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "__MACOSX/._Game.of.Thrones.S01E01.srt": "macos sidecar",
                "Game.of.Thrones.S07E20.720p.HDTV.x264-CTU.srt": "wrong episode",
                "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "right episode",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                # "720p" / "x264" must not be read as S07E20 / 2x64; the sidecar must be
                # excluded; the right episode must win on its exact filename.
                "filename": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "release_info": "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt")

    def test_download_does_not_pin_forced_member_for_non_forced_request(self):
        provider = self.mod.SubsSabBzProvider()
        # The forced member shares every release tag with the request, so without a forced
        # penalty its token overlap outranks the plain member and the host would silently
        # deliver a foreign-parts-only track for a NON-forced request.
        archive = _zip_body(
            {
                "Show.S01E01.720p.WEB.x264-CAKES.forced.srt": "forced foreign parts",
                "Show.S01E01.WEB.srt": "full subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                # Filename is not an exact member, so member selection falls to release scoring.
                "filename": "Show.S01E01.720p.WEB.x264-CAKES.different.srt",
                "release_info": "Show.S01E01.720p.WEB.x264-CAKES.srt",
                "season": 1,
                "episode": 1,
                "forced": False,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        # Must pin the non-forced member (or defer), never the forced one.
        self.assertEqual(result.get("member"), "Show.S01E01.WEB.srt")
        self.assertNotEqual(result.get("member"), "Show.S01E01.720p.WEB.x264-CAKES.forced.srt")

    def test_download_pins_forced_member_for_forced_request(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body(
            {
                "Show.S01E01.720p.WEB.x264-CAKES.forced.srt": "forced foreign parts",
                "Show.S01E01.WEB.srt": "full subtitle",
            }
        )
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88001",
                # Not an exact member, so release scoring decides; a forced request must keep
                # the forced member in play and pin it.
                "filename": "Show.S01E01.720p.WEB.x264-CAKES.forced.extra.srt",
                "release_info": "Show.S01E01.720p.WEB.x264-CAKES.forced.srt",
                "season": 1,
                "episode": 1,
                "forced": True,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertEqual(result.get("member"), "Show.S01E01.720p.WEB.x264-CAKES.forced.srt")

    def test_member_has_episode_token_matching(self):
        # Delimited SxxExx with optional separators, NxNN, and the contiguous code form.
        self.assertTrue(self.mod._member_has_episode("Show.S01E02.srt", 1, 2))
        self.assertTrue(self.mod._member_has_episode("Show.S01.E02.srt", 1, 2))
        self.assertTrue(self.mod._member_has_episode("Show.S01 E02.srt", 1, 2))
        self.assertTrue(self.mod._member_has_episode("Show.1x02.srt", 1, 2))
        self.assertTrue(self.mod._member_has_episode("Show.102.srt", 1, 2))
        # Release tags must not be misread as episode codes.
        self.assertFalse(self.mod._member_has_episode("Show.720p.x264.srt", 7, 20))
        self.assertFalse(self.mod._member_has_episode("Show.x264.srt", 2, 64))
        # Wrong season for the same episode number must not match.
        self.assertFalse(self.mod._member_has_episode("Show.S02E05.srt", 1, 5))

    def test_download_defers_for_rar_archive(self):
        provider = self.mod.SubsSabBzProvider()
        archive = b"Rar!\x1a\x07\x00" + b"payload"
        provider._http_get = lambda url, timeout=30, referer=None: archive

        result = provider.download(
            {
                "download_url": "http://subs.sab.bz/index.php?act=download&attach_id=88002",
                "filename": "Game.of.Thrones.S01E01.srt",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "bul", "alpha2": "bg"},
            {},
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 1)

    def test_search_stores_episode_in_provider_payload(self):
        provider = self.mod.SubsSabBzProvider()
        archive = _zip_body({"Game.of.Thrones.S01E01.720p.HDTV.x264-CTU.srt": "subtitle"})
        provider._http_post = lambda url, data, timeout=30, referer=None: SEARCH_GOT_BG_HTML
        provider._http_get = lambda url, timeout=30, referer=None: archive

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(results[0]["provider_payload"]["season"], 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)


if __name__ == "__main__":
    unittest.main()
