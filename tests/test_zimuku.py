import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "zimuku"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("zimuku_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHALLENGE_HTML = (FIXTURE_DIR / "zimuku_challenge.html").read_bytes()
SEARCH_HTML = (FIXTURE_DIR / "zimuku_search_game_of_thrones.html").read_bytes()
EPISODE_HTML = (FIXTURE_DIR / "zimuku_episode_game_of_thrones.html").read_bytes()
DETAIL_HTML = (FIXTURE_DIR / "zimuku_detail_page.html").read_bytes()
DOWNLOAD_HTML = (FIXTURE_DIR / "zimuku_download_page.html").read_bytes()
CURRENT_SEARCH_HTML = """
<div class="item prel clearfix">
  <div class="litpic hidden-xs"><a href="//zimuku.org/subs/59295.html"><img alt="沙丘 Dune 字幕下载"/></a></div>
  <div class="title"><p class="tt clearfix"><a href="//zimuku.org/subs/59295.html"><b>沙丘 Dune (2021)</b></a></p>
  <div class="sublist"><table><tbody>
    <tr class="odd"><td class="first">
      <img alt="&nbsp;简体中文字幕&nbsp;繁體中文字幕&nbsp;English字幕" title="&nbsp;简体中文字幕&nbsp;繁體中文字幕&nbsp;English字幕" src="/images/v2/flag/jollyroger.gif">
      <a href="//zimuku.org/detail/210615.html" title="沙丘.Dune.2021.官方简繁英粵.7z"><b>沙丘.Dune.2021.官方简繁英粵.7z</b></a>
      <span class="label label-info">SRT</span>
    </td></tr>
  </tbody></table></div></div>
</div>
<div class="item prel clearfix">
  <div class="title"><p class="tt clearfix"><a href="//zimuku.org/subs/59400.html"><b>沙丘行星 Planet Dune (2021)</b></a></p>
  <div class="sublist"><table><tbody>
    <tr class="odd"><td class="first">
      <img alt="&nbsp;简体中文字幕" title="&nbsp;简体中文字幕" src="/images/v2/flag/china.gif">
      <a href="//zimuku.org/detail/161440.html" title="沙丘行星.Planet.Dune.2021.1080p.WEB-DL.srt"><b>沙丘行星.Planet.Dune.2021.1080p.WEB-DL.srt</b></a>
      <span class="label label-info">SRT</span>
    </td></tr>
  </tbody></table></div></div>
</div>
""".encode("utf-8")
YUNSUO_CAPTCHA_42168_ZLIB_B64 = (
    "eNrtWd1LU2EYt5uu1JomzCZ+EISBSFAmJkKkQ8TMizALSyO66cabvHFIUxTJsx3P2ZfbWrqGn2VMIVEpneLHprJNneWFinrl39Ev35DDHPXsiHRzDjvwOw/P7zzvec77e5/nvLtftZOVcHzcwZmL8xVODc4LCepj+0DW8QU7/xx7e3uRSOTo6EgB/wRKBuhAyQAdKBlQNPhfNDi/MD/0ZWjv+KDcFs6TM5PeCW8UoNBPswCIz7K7uytlrays0FMBZ2nQzcimPA3WWeoSnAkj3hFiaDjH/FHoMYmUoD+3f+psOimr9X1rMBikBD04OICzlNv5sRM3lKHBR4ZHoI9PjhPfb7FQDP8rpiulQuk9470SroQBCp0553bn4g43xBuMTgkaDAelQQFwybk5StAOZwec08xpLDoALsVBkaJBwweD87PT3G9m4K7xLrg6lw4WSuiFxQWk6yZ/c3R8VN7KIAwKiNjuaKezWmwtKqtK59AxC8Al8yXinIQbnPV9emYBuGy5HFMIdBERQwNMTE0UCUUlxpKxr2My0oUsIVaXu4vOgn++IV9qwcuiCx/OUsst4RaMFA3CLcWcUmuobbA01Al11/hrsFTwFbDQn/eT91OBUFDOlyNv8aYLWUJEzC46i/68pwHc4Cy1IO0xJ8Zp7mPDY7VZbRu0MUutUAvitG863unhHnHnC/nVfPWMb+a8NQh/jVnjGHYwC0C6kE4UwlPhKZzdXjezAFwVr9YYaiga9Af8iKIVtPLqoBTY++3Xxes13TV+v/+8NYgfYjVaG18LrwFw+dz4nF4XIJ83zjegMx0tLi9SNIhGpUFsyOjOcI26ZNTBKKDltKDj3Z23BjEZKrnKk6X1hfXFxuYGhR4IBOAsXZafGJ7s7OwQe9HIVgRPhyydRYMM9H3u0wgael2QrUGsObPzs+iy0B2hX/LN+Yj0UDhU1lWWJ+aBBToAltml5SV6L+rxenKEHNEtPrM8k63BeEvSWTSoMquQJRkjfCA+yBQze4d7mQUgy5SFWkbvRWd9s8gS9HtGDcZVkmRr8J3n3e81ltPKGOFZ6uAJeNvzNseUk25Kj0uDVVyV1NLU05RkSSJqEC33S/6lDA1+m/nGem+9Xc8s9cb6rR9bxDmJETa7mpkFINmaTOxFpQXxIfeQLXd0DV7suXibu41etJAvBEi1pIKOTwDiEp1kTcrms+PV4Mn3oNqkZtETrYlIIIWObxMmYTZmAFzG/Ej5+92gQZYrugY9Y56obp8f4Le3t4l7FExN8WoQAJsDjWKjjKChUAjO0gG32dsODw/j3ZNBQZz6PoVPlf39feLIw+Hw3OIcYzGwtrZGV9Pq6ipjxaxEfwfrG+vygsJZOmaIV9kXVfZFlf8mlP8mFA0qGlRAFPgFroID+A=="
)


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(".hidden.srt", "ignore")
        archive.writestr("Game.of.Thrones.S01E01.HDTV.XviD-FEVER.cht.ass", "traditional")
        archive.writestr("Game.of.Thrones.S01E01.HDTV.XviD-FEVER.chs&eng.srt", "simplified bilingual")
    return stream.getvalue()


def _rar_body():
    return b"Rar!\x1a\x07\x00" + b"\x00" * 64


def _7z_body():
    return b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64


class ZimukuParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_yunsuo_challenge_extracts_verify_path_and_image(self):
        challenge = self.mod.parse_yunsuo_challenge(CHALLENGE_HTML)

        self.assertEqual(challenge["verify_prefix"], "/search?q=Dune 2021&security_verify_img=")
        self.assertEqual(challenge["image_b64"], "Qk1GQUtFQk1Q")
        self.assertEqual(self.mod.string_to_hex("1234"), "31323334")

    def test_solves_live_yunsuo_bitmap_captcha(self):
        image = zlib.decompress(base64.b64decode(YUNSUO_CAPTCHA_42168_ZLIB_B64))

        self.assertEqual(self.mod.solve_yunsuo_captcha_image(image), "42168")

    def test_yunsuo_templates_are_bundled_separately(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))

        self.assertTrue((PROVIDER_DIR / "yunsuo_templates.py").is_file())
        self.assertIn("yunsuo_templates.py", manifest["files"])

    def test_parse_search_results_filters_requested_episode_season(self):
        rows = self.mod.parse_search_results(SEARCH_HTML, {"kind": "episode", "series": "Game of Thrones", "season": 1, "year": 2011})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "权力的游戏 Game of Thrones 第一季 (2011)")
        self.assertEqual(rows[0]["year"], 2011)
        self.assertEqual(rows[0]["url"], "https://srtku.com/detail/game-of-thrones-season-1.html")

    def test_parse_episode_page_expands_language_rows(self):
        rows = self.mod.parse_episode_page(EPISODE_HTML, 2011)

        self.assertEqual([row["language"] for row in rows], ["zho-CN", "zho-TW", "eng"])
        self.assertEqual(rows[0]["release_info"], "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.CHS.CHT")
        self.assertEqual(rows[0]["year"], 2011)
        self.assertEqual(rows[2]["detail_url"], "https://srtku.com/subtitle/game-of-thrones-s01e01-eng.html")

    def test_parse_current_search_page_subtitle_rows(self):
        rows = self.mod.parse_search_subtitle_rows(CURRENT_SEARCH_HTML, {"kind": "movie", "title": "Dune", "year": 2021})

        self.assertEqual([row["language"] for row in rows], ["zho-TW", "zho-CN", "eng"])
        self.assertEqual(rows[0]["detail_url"], "https://zimuku.org/detail/210615.html")
        self.assertEqual(rows[0]["release_info"], "Dune.2021")
        self.assertEqual(rows[0]["year"], 2021)

    def test_extract_download_returns_zip_archive_mode(self):
        body = _zip_body()

        result = self.mod.extract_download(body, {"filename": "archive.zip", "episode": 1})

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_extract_download_returns_rar_archive_mode(self):
        body = _rar_body()

        result = self.mod.extract_download(body, {"filename": "archive.rar", "episode": 3})

        self.assertTrue(self.mod._is_rar_archive(body))
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 3)
        self.assertNotIn("content_b64", result)

    def test_extract_download_returns_7z_archive_mode(self):
        body = _7z_body()

        result = self.mod.extract_download(body, {"filename": "archive.7z", "episode": 5})

        self.assertTrue(self.mod._is_7z_archive(body))
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 5)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_extract_download_carries_no_episode_for_movies(self):
        body = _zip_body()

        result = self.mod.extract_download(body, {"filename": "archive.zip"})

        self.assertIn("archive_b64", result)
        self.assertIsNone(result["episode"])

    def test_extract_download_direct_subtitle_body_is_content_mode(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nhello\n"

        result = self.mod.extract_download(body, {"filename": "dune.srt"})

        self.assertNotIn("archive_b64", result)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertNotIn("encoding", result)

    def test_extract_download_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"", {"filename": "dune.zip"})
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"   \n", {"filename": "dune.zip"})

    def test_extract_download_rejects_html_error_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"<!DOCTYPE html><html><body>error</body></html>", {"filename": "dune.zip"})

    def test_languages_from_row_merges_flag_and_bilingual_text(self):
        # Finding 3: a Chinese flag plus a *.CHS.ENG release must still expose
        # the English/bilingual half.
        row = (
            '<td class="tac lang"><img src="/static/images/china.gif" alt="简体"></td>'
            '<td><a href="/subtitle/x.html">Show.S01E01.CHS.ENG.srt</a></td>'
        )
        languages = self.mod._languages_from_row(row, "Show.S01E01.CHS.ENG.srt")

        self.assertIn("eng", languages)
        self.assertIn("zho-CN", languages)

    def test_languages_from_row_emits_zho_cn_for_simplified(self):
        # Finding 4: simplified rows must be returned for zho-CN requests.
        row = (
            '<td class="tac lang"><img src="/static/images/china.gif" alt="简体"></td>'
            '<td><a href="/subtitle/x.html">Show.S01E01.CHS.srt</a></td>'
        )
        languages = self.mod._languages_from_row(row, "Show.S01E01.CHS.srt")

        self.assertEqual(languages, ["zho-CN"])
        self.assertTrue(self.mod._requested("zho-CN", {"zho-CN"}))
        # A generic zho request must still accept the zho-CN row.
        self.assertTrue(self.mod._requested("zho-CN", {"zho"}))
        # A zho-CN request must not pull in traditional rows.
        self.assertFalse(self.mod._requested("zho-TW", {"zho-CN"}))


class ZimukuProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_solves_yunsuo_challenge_and_returns_requested_language(self):
        provider = self.mod.ZimukuProvider()
        calls = []
        responses = {
            "https://srtku.com/search?q=Game+of+Thrones.S01": [
                self.mod.HttpResponse(404, CHALLENGE_HTML, {}, "https://srtku.com/search?q=Game+of+Thrones.S01"),
                self.mod.HttpResponse(200, SEARCH_HTML, {}, "https://srtku.com/search?q=Game+of+Thrones.S01"),
            ],
            "https://srtku.com/search?q=Dune%202021&security_verify_img=31323334": [
                self.mod.HttpResponse(302, b"", {"Location": "/search?q=Game+of+Thrones.S01"}, "https://srtku.com/search?q=Dune%202021&security_verify_img=31323334"),
            ],
            "https://srtku.com/detail/game-of-thrones-season-1.html": [
                self.mod.HttpResponse(200, EPISODE_HTML, {}, "https://srtku.com/detail/game-of-thrones-season-1.html"),
            ],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            calls.append(url)
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        provider._solve_yunsuo_image = lambda challenge, config: "1234"
        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1, "year": 2011, "release_group": "FEVER"},
            [{"alpha3": "zho", "alpha2": "zh"}],
            {"request_delay_ms": 0},
        )

        self.assertIn("https://srtku.com/search?q=Dune%202021&security_verify_img=31323334", calls)
        cookies = {cookie.name: cookie.value for cookie in provider._cookie_jar}
        self.assertEqual(cookies["srcurl"], self.mod.string_to_hex("https://srtku.com/search?q=Dune 2021"))
        self.assertEqual({item["provider_payload"]["language"] for item in results}, {"zho-CN", "zho-TW"})
        self.assertTrue(all(item["provider"] == "zimuku" for item in results))
        # Episode searches must carry the episode (and season) into the payload so
        # download() can pass episode for host-side member selection.
        self.assertTrue(all(item["provider_payload"]["episode"] == 1 for item in results))
        self.assertTrue(all(item["provider_payload"]["season"] == 1 for item in results))

    def test_search_uses_current_inline_search_rows(self):
        provider = self.mod.ZimukuProvider()
        calls = []
        responses = {
            "https://srtku.com/search?q=Dune+2021": [
                self.mod.HttpResponse(200, CURRENT_SEARCH_HTML, {}, "https://srtku.com/search?q=Dune+2021"),
            ],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            calls.append(url)
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [{"alpha3": "zho", "alpha2": "zh"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls, ["https://srtku.com/search?q=Dune+2021"])
        self.assertEqual({item["provider_payload"]["language"] for item in results}, {"zho-CN", "zho-TW"})
        self.assertEqual({item["provider_payload"]["detail_url"] for item in results}, {"https://zimuku.org/detail/210615.html"})
        # Movies carry no episode.
        self.assertTrue(all(item["provider_payload"]["episode"] is None for item in results))

    def test_yunsuo_bypass_allows_multiple_challenge_retries(self):
        provider = self.mod.ZimukuProvider()
        search_url = "https://srtku.com/search?q=Dune+2021"
        verify_url = "https://srtku.com/search?q=Dune%202021&security_verify_img=31323334"
        responses = {
            search_url: [self.mod.HttpResponse(404, CHALLENGE_HTML, {}, search_url) for _index in range(5)]
            + [self.mod.HttpResponse(200, CURRENT_SEARCH_HTML, {}, search_url)],
            verify_url: [self.mod.HttpResponse(302, b"", {}, verify_url) for _index in range(5)],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        provider._solve_yunsuo_image = lambda challenge, config: "1234"

        response = provider._bypass_get(search_url, {"request_delay_ms": 0})

        self.assertEqual(response.status, 200)

    def test_yunsuo_bypass_solves_challenge_body_without_404_status(self):
        provider = self.mod.ZimukuProvider()
        search_url = "https://srtku.com/search?q=Dune+2021"
        verify_url = "https://srtku.com/search?q=Dune%202021&security_verify_img=31323334"
        responses = {
            search_url: [
                self.mod.HttpResponse(200, CHALLENGE_HTML, {}, search_url),
                self.mod.HttpResponse(200, CURRENT_SEARCH_HTML, {}, search_url),
            ],
            verify_url: [self.mod.HttpResponse(302, b"", {}, verify_url)],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        provider._solve_yunsuo_image = lambda challenge, config: "1234"

        response = provider._bypass_get(search_url, {"request_delay_ms": 0})

        self.assertEqual(response.content, CURRENT_SEARCH_HTML)

    def test_yunsuo_verify_url_uses_challenge_response_host(self):
        provider = self.mod.ZimukuProvider()
        detail_url = "https://zimuku.org/detail/210615.html"
        verify_url = "https://zimuku.org/search?q=Dune%202021&security_verify_img=31323334"
        calls = []
        responses = {
            detail_url: [
                self.mod.HttpResponse(404, CHALLENGE_HTML, {}, detail_url),
                self.mod.HttpResponse(200, CURRENT_SEARCH_HTML, {}, detail_url),
            ],
            verify_url: [self.mod.HttpResponse(302, b"", {}, verify_url)],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            calls.append(url)
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        provider._solve_yunsuo_image = lambda challenge, config: "1234"

        response = provider._bypass_get(detail_url, {"request_delay_ms": 0})

        self.assertEqual(response.status, 200)
        self.assertIn(verify_url, calls)
        cookies = {cookie.name: cookie.value for cookie in provider._cookie_jar}
        self.assertEqual(cookies["srcurl"], self.mod.string_to_hex("https://zimuku.org/search?q=Dune 2021"))

    def test_yunsuo_bypass_generates_coordinate_response_without_image(self):
        provider = self.mod.ZimukuProvider()
        search_url = "https://srtku.com/search?q=Dune+2021"
        challenge_without_image = CHALLENGE_HTML.replace(
            b'    <img class="verifyimg" alt="verify_img" src="data:image/bmp;base64,Qk1GQUtFQk1Q" />\n',
            b"",
        )
        decoded_codes = []
        responses = {
            search_url: [
                self.mod.HttpResponse(404, challenge_without_image, {}, search_url),
                self.mod.HttpResponse(200, CURRENT_SEARCH_HTML, {}, search_url),
            ],
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            if url.startswith("https://srtku.com/search?q=Dune%202021&security_verify_img="):
                hex_code = url.rsplit("=", 1)[1]
                decoded_codes.append(bytes.fromhex(hex_code).decode("ascii"))
                return self.mod.HttpResponse(302, b"", {}, url)
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub

        response = provider._bypass_get(search_url, {"request_delay_ms": 0})

        self.assertEqual(response.status, 200)
        self.assertEqual(len(decoded_codes), 1)
        self.assertRegex(decoded_codes[0], r"^\d{3,4},\d{3,4}$")

    def test_download_follows_detail_and_download_pages(self):
        provider = self.mod.ZimukuProvider()
        zip_body = _zip_body()
        calls = []
        responses = {
            "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html": self.mod.HttpResponse(200, DETAIL_HTML, {}, "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html"),
            "https://srtku.com/download/dune-2021.html": self.mod.HttpResponse(200, DOWNLOAD_HTML, {}, "https://srtku.com/download/dune-2021.html"),
            "https://srtku.com/download/file/dune-2021.zip": self.mod.HttpResponse(200, zip_body, {"Content-Disposition": "attachment; filename=dune.zip"}, "https://srtku.com/download/file/dune-2021.zip"),
        }

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, allow_redirects
            calls.append((url, referer))
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get_response = response_stub
        result = provider.download(
            {
                "detail_url": "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html",
                "language": "zho-TW",
                "filename": "Game.of.Thrones.S01E01.zip",
                "episode": 1,
            },
            {"alpha3": "zho", "alpha2": "zh", "country": "TW"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(calls[1][1], "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html")
        self.assertEqual(calls[2][1], "https://srtku.com/subtitle/game-of-thrones-s01e01-chs-cht.html")
        # Host-side archive mode: the raw zip bytes are handed back, episode carried through.
        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(zip_body).hexdigest())
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("content_b64", result)

    def test_search_filters_wrong_episode_rows(self):
        # Finding 5: a season result listing sibling episodes must not offer the
        # wrong-episode subtitle for a single-episode request.
        provider = self.mod.ZimukuProvider()
        search_html = (
            '<div class="item prel clearfix">'
            '<div class="title"><p class="tt clearfix">'
            '<a href="//srtku.com/subs/1.html"><b>Game of Thrones 第一季 (2011)</b></a></p>'
            '<div class="sublist"><table><tbody>'
            '<tr class="odd"><td class="first">'
            '<img alt="简体" src="/images/v2/flag/china.gif">'
            '<a href="//srtku.com/detail/e01.html" title="Game.of.Thrones.S01E01.HDTV.chs.srt">'
            '<b>Game.of.Thrones.S01E01.HDTV.chs.srt</b></a></td></tr>'
            '<tr class="even"><td class="first">'
            '<img alt="简体" src="/images/v2/flag/china.gif">'
            '<a href="//srtku.com/detail/e02.html" title="Game.of.Thrones.S01E02.HDTV.chs.srt">'
            '<b>Game.of.Thrones.S01E02.HDTV.chs.srt</b></a></td></tr>'
            "</tbody></table></div></div></div>"
        ).encode("utf-8")
        search_url = "https://srtku.com/search?q=Game+of+Thrones.S01"
        responses = {search_url: [self.mod.HttpResponse(200, search_html, {}, search_url)]}

        def response_stub(url, timeout=30, referer=None, allow_redirects=True):
            del timeout, referer, allow_redirects
            if url not in responses or not responses[url]:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url].pop(0)

        provider._http_get_response = response_stub
        results = provider.search(
            {"kind": "episode", "series": "Game of Thrones", "season": 1, "episode": 1, "year": 2011},
            [{"alpha3": "zho", "alpha2": "zh", "country": "CN"}],
            {"request_delay_ms": 0},
        )

        detail_urls = {item["provider_payload"]["detail_url"] for item in results}
        self.assertEqual(detail_urls, {"https://srtku.com/detail/e01.html"})

    def test_http_get_response_honors_disabled_redirects(self):
        # Finding 6: the Yunsuo verification request must not follow its 302.
        provider = self.mod.ZimukuProvider()
        used = []

        class FakeResponse:
            status = 302
            headers = {"Location": "/the-subtitle-file"}

            def read(self):
                return b""

            def geturl(self):
                return "https://srtku.com/verify"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class FakeOpener:
            def __init__(self, name):
                self.name = name

            def open(self, request, timeout=None):
                used.append(self.name)
                return FakeResponse()

        provider._opener = FakeOpener("follow")
        provider._no_redirect_opener = FakeOpener("no-redirect")

        response = provider._http_get_response("https://srtku.com/verify", allow_redirects=False)

        self.assertEqual(used, ["no-redirect"])
        self.assertEqual(response.status, 302)


if __name__ == "__main__":
    unittest.main()
