import base64
import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsunacs"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("subsunacs_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_DUNE_EN = (FIXTURE_DIR / "subsunacs_search_dune_en.html").read_bytes()
SEARCH_GOT_BG = (FIXTURE_DIR / "subsunacs_search_game_of_thrones_bg.html").read_bytes()
DETAIL_DUNE = (FIXTURE_DIR / "subsunacs_detail_dune.html").read_bytes()
DETAIL_GOT = (FIXTURE_DIR / "subsunacs_detail_game_of_thrones.html").read_bytes()

SEARCH_BACK_TO_FUTURE_3_EN = b"""
<!doctype html>
<html>
<body>
<table>
  <tr bgcolor="#333333" onmouseover="this.style.backgroundColor='#454545'">
    <td class="tdMovie">
      <a href="/subtitles/Back_To_The_Future_3-10001/" class="tooltip"
         title="&lt;div&gt;&lt;b&gt;Movie: Back to the Future 3&lt;/b&gt;&lt;br&gt;Back.to.the.Future.3.1990.1080p.BluRay&lt;/div&gt;">Back to the Future 3</a>
      <span class="smGray">&nbsp;(1990)</span><span class="smSilver"> English</span>
    </td>
    <td>1</td>
    <td>23.976</td>
    <td><a href="/subtitles/Back_To_The_Future_3-10001/!" target="_blank">----</a></td>
    <td><a href="/subtitles/Back_To_The_Future_3-10001/!#comments" target="_blank">0</a></td>
    <td><a href="/search.php?t=1&amp;memid=154075">subs</a></td>
    <td>12176</td>
    <td><a href="/subtitles/Back_To_The_Future_3-10001/!" target="_blank"><span class="sm">download</span></a></td>
    <td align="center">---</td>
    <td><input class="filesChk" type="checkbox" name="ids[]" value="10001" /></td>
  </tr>
</table>
</body>
</html>
"""

SEARCH_GOT_WRONG_EPISODE_BG = b"""
<!doctype html>
<html>
<body>
<table>
  <tr bgcolor="#333333" onmouseover="this.style.backgroundColor='#454545'">
    <td class="tdMovie">
      <a href="/subtitles/Game_Of_Thrones_01x02-70363/" class="tooltip"
         title="&lt;div&gt;&lt;b&gt;Movie: Game Of Thrones - 01x02&lt;/b&gt;&lt;br&gt;generic.release&lt;/div&gt;">Game Of Thrones - 01x02</a>
      <span class="smGray">&nbsp;(2011)</span>
    </td>
    <td>1</td>
    <td>23.976</td>
    <td><a href="/subtitles/Game_Of_Thrones_01x02-70363/!" target="_blank">----</a></td>
    <td><a href="/subtitles/Game_Of_Thrones_01x02-70363/!#comments" target="_blank">0</a></td>
    <td><a href="/search.php?t=1&amp;u=naliareev">naliareev</a></td>
    <td>24533</td>
    <td><a href="/subtitles/Game_Of_Thrones_01x02-70363/!" target="_blank"><span class="sm">download</span></a></td>
    <td align="center">---</td>
    <td><input class="filesChk" type="checkbox" name="ids[]" value="70363" /></td>
  </tr>
</table>
</body>
</html>
"""

DETAIL_GENERIC_EPISODE = b"""
<!doctype html>
<html>
<body>
<div class="rarview">
  <label><a href="/getentry.php?id=70363&amp;ei=0">generic.release.srt</a></label>
</div>
</body>
</html>
"""

DETAIL_SINGLE_FILE = b"""
<!doctype html>
<html>
<body>
<div class="rarview">
  <label><a href="/getentry.php?id=144478&amp;ei=0">Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt</a></label>
</div>
</body>
</html>
"""

SEARCH_SHOW_S01E05_BG = b"""
<!doctype html>
<html>
<body>
<table>
  <tr bgcolor="#333333" onmouseover="this.style.backgroundColor='#454545'">
    <td class="tdMovie">
      <a href="/subtitles/Some_Show_01x05-80808/" class="tooltip"
         title="&lt;div&gt;&lt;b&gt;Movie: Some Show - 01x05&lt;/b&gt;&lt;br&gt;some.show.release&lt;/div&gt;">Some Show - 01x05</a>
      <span class="smGray">&nbsp;(2015)</span>
    </td>
    <td>1</td>
    <td>23.976</td>
    <td><a href="/subtitles/Some_Show_01x05-80808/!" target="_blank">----</a></td>
    <td><a href="/subtitles/Some_Show_01x05-80808/!#comments" target="_blank">0</a></td>
    <td><a href="/search.php?t=1&amp;u=naliareev">naliareev</a></td>
    <td>24533</td>
    <td><a href="/subtitles/Some_Show_01x05-80808/!" target="_blank"><span class="sm">download</span></a></td>
    <td align="center">---</td>
    <td><input class="filesChk" type="checkbox" name="ids[]" value="80808" /></td>
  </tr>
</table>
</body>
</html>
"""

DETAIL_GENERIC_RESOLUTION_FILE = b"""
<!doctype html>
<html>
<body>
<div class="rarview">
  <label><a href="/getentry.php?id=80808&amp;ei=0">Some.Show.720p.HDTV.x264.srt</a></label>
</div>
</body>
</html>
"""

DETAIL_FORCED_ONLY = b"""
<!doctype html>
<html>
<body>
<div class="rarview">
  <label><a href="/getentry.php?id=144478&amp;ei=0">Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX_FORCED.srt</a></label>
</div>
</body>
</html>
"""


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SubsUnacsParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_public_rows(self):
        rows = self.mod.parse_search_results(SEARCH_DUNE_EN)

        self.assertEqual(rows[0]["title"], "Dune")
        self.assertEqual(rows[0]["year"], 2021)
        self.assertEqual(rows[0]["download_url"], "https://subsunacs.net/subtitles/Dune-144478/!")
        self.assertEqual(rows[0]["num_cds"], 1)
        self.assertEqual(rows[0]["fps"], 23.976)
        self.assertEqual(rows[0]["rating"], None)
        self.assertEqual(rows[0]["uploader"], "subs")

    def test_parse_detail_entries_filters_site_readme_files(self):
        entries = self.mod.parse_detail_entries(DETAIL_DUNE)

        self.assertEqual([entry["filename"] for entry in entries], [
            "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt",
            "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX_FORCED.srt",
        ])
        self.assertEqual(entries[0]["entry_url"], "https://subsunacs.net/getentry.php?id=144478&ei=0")

    def test_extract_archive_files_reads_zip_and_filters_ignored_txt(self):
        body = _zip_body(
            {
                "Movie.srt": "subtitle",
                "subsunacs.net_144478.txt": "ignored",
            }
        )

        files = self.mod.extract_archive_files(body)

        self.assertEqual(files, [{"filename": "Movie.srt", "path": "Movie.srt", "content": b"subtitle"}])

    def test_extract_archive_files_ignores_oversized_file_lists(self):
        body = _zip_body({f"readme-{index}.txt": "ignored" for index in range(self.mod.ARCHIVE_FILE_COUNT_LIMIT + 1)})

        self.assertEqual(self.mod.extract_archive_files(body), [])

    def test_extract_archive_files_rejects_zip_entries_over_memory_limit(self):
        self.mod.ARCHIVE_MEMORY_LIMIT = 4
        body = _zip_body({"Movie.srt": "subtitle"})

        self.assertEqual(self.mod.extract_archive_files(body), [])

    def test_collect_extracted_files_repairs_unreadable_archive_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Show.S01E01.en.srt"
            path.write_bytes(b"subtitle")
            path.chmod(0)
            try:
                files = self.mod._collect_extracted_subtitle_files(temp_dir)
            finally:
                path.chmod(0o600)

        self.assertEqual(files, [("Show.S01E01.en.srt", b"subtitle")])

    def test_collect_extracted_files_skips_symlinks(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink support is unavailable")
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir) / "outside.srt"
            outside.write_bytes(b"outside subtitle")
            link = Path(temp_dir) / "Show.S01E01.en.srt"
            os.symlink(outside, link)

            files = self.mod._collect_extracted_subtitle_files(temp_dir)

        self.assertEqual(files, [])


class SubsUnacsProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_movie_posts_expected_payload_and_returns_detail_entries(self):
        provider = self.mod.SubsUnacsProvider()
        calls = []

        def post_stub(url, data, timeout=10, referer=None):
            del timeout, referer
            calls.append((url, data))
            return SEARCH_DUNE_EN

        def get_stub(url, timeout=10, referer=None):
            del timeout, referer
            self.assertEqual(url, "https://subsunacs.net/subtitles/Dune-144478/!")
            return DETAIL_DUNE

        provider._http_post = post_stub
        provider._http_get = get_stub

        results = provider.search(
            {
                "kind": "movie",
                "title": "Dune",
                "year": 2021,
                "imdb_id": "tt1160419",
                "source": "WEBRip",
                "resolution": "1080p",
                "release_group": "SHITBOX",
                "fps": 23.976,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0][0], "https://subsunacs.net/search.php")
        self.assertEqual(calls[0][1]["m"], "Dune")
        self.assertEqual(calls[0][1]["l"], 1)
        self.assertEqual(calls[0][1]["y"], 2021)
        self.assertEqual(results[0]["language"]["alpha3"], "eng")
        self.assertEqual(results[0]["filename"], "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt")
        self.assertIn("title", results[0]["matches"])
        self.assertIn("release_group", results[0]["matches"])
        self.assertEqual(results[0]["provider_payload"]["entry_url"], "https://subsunacs.net/getentry.php?id=144478&ei=0")

    def test_search_episode_formats_query_and_filters_detail_entries_by_episode(self):
        provider = self.mod.SubsUnacsProvider()
        calls = []

        provider._http_post = lambda url, data, timeout=10, referer=None: calls.append(data) or SEARCH_GOT_BG
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_GOT

        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "source": "HDTV",
                "release_group": "fever",
                "fps": 23.976,
            },
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(calls[0]["m"], "Game of Thrones 01 01")
        self.assertEqual(calls[0]["l"], 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "game.of.thrones.s01e01.hdtv.xvid-fever.srt")
        self.assertIn("episode", results[0]["matches"])

    def test_search_applies_legacy_movie_title_aliases(self):
        provider = self.mod.SubsUnacsProvider()
        calls = []
        provider._http_post = lambda url, data, timeout=10, referer=None: calls.append(data) or b""
        provider._http_get = lambda url, timeout=10, referer=None: b""

        provider.search(
            {"kind": "movie", "title": "Bill & Ted Face the Music", "year": 2020},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(calls[0]["m"], "Bill Ted Face the Music")

    def test_search_movie_uses_rewritten_title_for_row_filter(self):
        provider = self.mod.SubsUnacsProvider()
        provider._http_post = lambda url, data, timeout=10, referer=None: SEARCH_BACK_TO_FUTURE_3_EN
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_SINGLE_FILE

        results = provider.search(
            {"kind": "movie", "title": "Back to the Future Part III", "year": 1990},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertIn("title", results[0]["matches"])

    def test_search_episode_rejects_rows_for_other_episodes(self):
        provider = self.mod.SubsUnacsProvider()
        provider._http_post = lambda url, data, timeout=10, referer=None: SEARCH_GOT_WRONG_EPISODE_BG
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_GENERIC_EPISODE

        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1},
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_preserves_requested_language_variants_when_deduping(self):
        provider = self.mod.SubsUnacsProvider()
        provider._http_post = lambda url, data, timeout=10, referer=None: SEARCH_DUNE_EN
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_DUNE

        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [
                {"alpha3": "eng", "alpha2": "en", "forced": False, "hi": False},
                {"alpha3": "eng", "alpha2": "en", "forced": True, "hi": False},
            ],
            {},
        )

        self.assertEqual(len(results), 2)
        self.assertEqual({item["language"]["forced"] for item in results}, {False, True})
        self.assertEqual(len({item["id"] for item in results}), 2)
        by_forced = {item["language"]["forced"]: item["filename"] for item in results}
        self.assertEqual(by_forced[False], "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt")
        self.assertEqual(by_forced[True], "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX_FORCED.srt")

    def test_download_fetches_direct_entry_and_normalizes_line_endings(self):
        provider = self.mod.SubsUnacsProvider()
        provider._http_get = lambda url, timeout=10, referer=None: b"1\r\nText\r\n"

        content = provider.download(
            {
                "entry_url": "https://subsunacs.net/getentry.php?id=144478&ei=0",
                "filename": "Dune.2021.1080p.WEBRip.DD5.1.x264-SHITBOX.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {},
        )
        data = base64.b64decode(content["content_b64"])

        self.assertEqual(data, b"1\nText\n")
        self.assertEqual(content["content_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(content["format"], "srt")

    def test_search_keeps_generic_resolution_file_for_other_episodes(self):
        # Without the fix, "720p" is parsed as S07E20 and the file is dropped for S01E05.
        provider = self.mod.SubsUnacsProvider()
        provider._http_post = lambda url, data, timeout=10, referer=None: SEARCH_SHOW_S01E05_BG
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_GENERIC_RESOLUTION_FILE

        results = provider.search(
            {"kind": "episode", "series": "Some Show", "season": 1, "episode": 5},
            [{"alpha3": "bul", "alpha2": "bg"}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Some.Show.720p.HDTV.x264.srt")

    def test_search_does_not_label_forced_file_for_normal_request(self):
        # Without the fix, a normal request returns the _FORCED file as forced: false.
        provider = self.mod.SubsUnacsProvider()
        provider._http_post = lambda url, data, timeout=10, referer=None: SEARCH_DUNE_EN
        provider._http_get = lambda url, timeout=10, referer=None: DETAIL_FORCED_ONLY

        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [{"alpha3": "eng", "alpha2": "en", "forced": False, "hi": False}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_keeps_archive_members_with_colliding_basenames(self):
        # Without the fix, both members collapse to the same basename key and one is dropped,
        # and a download cannot select the intended directory's file.
        provider = self.mod.SubsUnacsProvider()
        archive = _zip_body({"cd1/Movie.srt": "first", "cd2/Movie.srt": "second"})
        provider._http_post = lambda url, data, timeout=10, referer=None: SEARCH_DUNE_EN
        provider._http_get = lambda url, timeout=10, referer=None: archive

        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [{"alpha3": "eng", "alpha2": "en"}],
            {},
        )

        self.assertEqual(len(results), 2)
        paths = {item["provider_payload"]["path"] for item in results}
        self.assertEqual(paths, {"cd1/Movie.srt", "cd2/Movie.srt"})
        self.assertEqual(len({item["id"] for item in results}), 2)

        wanted = next(item for item in results if item["provider_payload"]["path"] == "cd2/Movie.srt")
        provider._http_get = lambda url, timeout=10, referer=None: archive
        content = provider.download(wanted["provider_payload"], {"alpha3": "eng", "alpha2": "en"}, {})
        self.assertEqual(base64.b64decode(content["content_b64"]), b"second")


if __name__ == "__main__":
    unittest.main()
