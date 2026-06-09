import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subs4free"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subs4free_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_HTML = (FIXTURE_DIR / "subs4free_search_inception.html").read_bytes()
SUGGESTIONS_HTML = (FIXTURE_DIR / "subs4free_suggestions.html").read_bytes()
DOWNLOAD_HTML = (FIXTURE_DIR / "subs4free_download_page.html").read_bytes()


def _zip_body():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(".hidden.srt", "ignore")
        archive.writestr("Inception.2010.1080p.BluRay.RARBG.srt", "one\r\ntwo\r\n")
    return stream.getvalue()


def _zip_body_with(name, content):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, content)
    return stream.getvalue()


def _zip_body_members(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name in names:
            archive.writestr(name, "1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n")
    return stream.getvalue()


def _row(language, release, identifier):
    if language == "ell":
        language_name = "Greek"
        path_language = "greek"
        sprite = "elgif"
    else:
        language_name = "English"
        path_language = "english"
        sprite = "engif"
    slug = release.lower().replace(" ", "-")
    return f"""
    <div class="movie-details">
      <div class="movie-info">
        <a class="movie-heading"
           href="/{path_language}-subtitles/{identifier}/{slug}"
           title="{language_name} subtitles for {release}">
          <div class="sprite {sprite}"></div>
          <span>{release}</span>
        </a>
        <p>Uploaded by <a href="/subtitles-by-u1.html">Uploader</a></p>
      </div>
      <div class="movie-download"><p><b>1</b>DLs</p></div>
    </div>
    """.encode("utf-8")


def _html_with_rows(rows):
    return b"<!doctype html><html><body>" + b"".join(rows) + b"</body></html>"


class Subs4FreeParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_extracts_movie_rows(self):
        rows = self.mod.parse_search_results(SEARCH_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["language"], "ell")
        self.assertEqual(rows[0]["alpha2"], "el")
        self.assertEqual(rows[0]["release_info"], "Inception 2010 1080p BluRay H264 AAC-RARBG [SubRip]")
        self.assertEqual(rows[0]["uploader"], "Nerddork")
        self.assertEqual(rows[0]["downloads"], 1699)
        self.assertEqual(
            rows[0]["detail_url"],
            "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception-2010-1080p-bluray-h264-aac-rarbg-subrip",
        )
        self.assertEqual(rows[1]["language"], "eng")

    def test_parse_suggestions_keeps_option_links(self):
        rows = self.mod.parse_suggestions(SUGGESTIONS_HTML)

        self.assertEqual(rows[0]["title"], "Inception 2010")
        self.assertEqual(
            rows[0]["url"],
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010",
        )

    def test_parse_suggestions_normalizes_legacy_mov_sel_query_links(self):
        rows = self.mod.parse_suggestions(
            b"""
            <select name="Mov_sel">
              <option value="?p=movie-details/m640fe75810/inception-2010">Inception 2010</option>
            </select>
            """
        )

        self.assertEqual(
            rows[0]["url"],
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010",
        )

    def test_parse_download_form_extracts_post_id_and_click_bounds(self):
        form = self.mod.parse_download_form(DOWNLOAD_HTML)

        self.assertEqual(form["id"], "tkMTc4MDI0MDQ5MTE4NTk1MjUxMTg2MTg5NjQ2MTg5")
        self.assertEqual(form["width"], 200)
        self.assertEqual(form["height"], 58)

    def test_extract_download_zip_returns_raw_archive_for_host(self):
        body = _zip_body()
        payload = self.mod.extract_download(body, {"filename": "inception.el.zip", "episode": None})

        # Archive mode: the worker hands the raw archive bytes back untouched.
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertIsNone(payload["episode"])
        # No extraction, member selection, or encoding guessing happens worker-side.
        self.assertNotIn("content_b64", payload)
        self.assertNotIn("member", payload)
        self.assertNotIn("encoding", payload)

    def test_extract_download_rar_returns_raw_archive_for_host(self):
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        payload = self.mod.extract_download(body, {"filename": "inception.el.rar", "episode": 3})

        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(payload["episode"], 3)
        self.assertNotIn("content_b64", payload)
        self.assertNotIn("encoding", payload)

    def test_extract_download_movie_archive_carries_none_episode(self):
        body = _zip_body_with("Inception.2010.srt", "one\r\ntwo\r\n")
        payload = self.mod.extract_download(body, {"filename": "inception.el.zip"})

        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertIsNone(payload["episode"])

    def test_extract_download_direct_subtitle_normalizes_line_endings(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n"
        payload = self.mod.extract_download(body, {"filename": "inception.el.srt"})

        self.assertEqual(payload["format"], "srt")
        self.assertEqual(payload["empty"], False)
        data = base64.b64decode(payload["content_b64"])
        self.assertEqual(data, b"1\n00:00:01,000 --> 00:00:02,000\nText\n")
        self.assertEqual(payload["content_sha256"], hashlib.sha256(data).hexdigest())
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", payload)
        self.assertNotIn("archive_b64", payload)

    def test_extract_download_pins_release_member_in_multi_member_zip(self):
        # A multi-member zip with no episode signal: reproduce the old release-based pick by
        # pinning the member whose tokens overlap the scored release_info.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.RARBG.srt",
            "Inception.2010.720p.WEBRip.YIFY.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay H264 AAC-RARBG",
                "episode": None,
            },
        )

        self.assertEqual(payload["member"], "Inception.2010.1080p.BluRay.RARBG.srt")
        self.assertNotIn("episode", payload)
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertNotIn("content_b64", payload)

    def test_extract_download_defers_when_no_unique_release_winner(self):
        # Two members tie on release_info overlap: cannot disambiguate, so defer to the host
        # episode path rather than pin a coin-flip member.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.srt",
            "Inception.2010.1080p.BluRay.Director.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay",
                "episode": None,
            },
        )

        self.assertNotIn("member", payload)
        self.assertIsNone(payload["episode"])

    def test_extract_download_does_not_pin_wrong_release_member(self):
        # The release_info points at the BluRay cut; the WEBRip member must never be pinned.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.RARBG.srt",
            "Different.Movie.2011.720p.WEBRip.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertEqual(payload["member"], "Inception.2010.1080p.BluRay.RARBG.srt")

    def test_extract_download_skips_sidecars_and_directories(self):
        # __MACOSX/dotfile sidecars and directory entries must not count as members: only the
        # real subtitle remains, so a lone member defers to the host (no pin).
        body = _zip_body_members([
            "subs/",
            "__MACOSX/._Inception.2010.BluRay.srt",
            ".DS_Store",
            "subs/Inception.2010.1080p.BluRay.RARBG.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertNotIn("member", payload)
        self.assertIsNone(payload["episode"])

    def test_extract_download_does_not_pin_forced_over_main(self):
        # A forced sidecar shares release tokens with the main track; the forced penalty keeps
        # the main subtitle as the pinned member.
        body = _zip_body_members([
            "Inception.2010.1080p.BluRay.RARBG.srt",
            "Inception.2010.1080p.BluRay.RARBG.forced.srt",
        ])
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.zip",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertEqual(payload["member"], "Inception.2010.1080p.BluRay.RARBG.srt")

    def test_extract_download_rar_multi_member_defers_to_host(self):
        # RAR is not stdlib-listable: never pin, always hand the archive to the host.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 64
        payload = self.mod.extract_download(
            body,
            {
                "filename": "inception.el.rar",
                "release_info": "Inception 2010 1080p BluRay RARBG",
                "episode": None,
            },
        )

        self.assertNotIn("member", payload)
        self.assertIsNone(payload["episode"])
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)

    def test_extract_download_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            self.mod.extract_download(b"   \r\n  ", {"filename": "inception.el.zip"})

    def test_extract_download_rejects_html_error_page(self):
        body = (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )
        with self.assertRaises(ValueError):
            self.mod.extract_download(body, {"filename": "inception.el.zip"})


class Subs4FreeProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_requested_movie_languages(self):
        provider = self.mod.Subs4FreeProvider()
        calls = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if "search_report.php" in url:
                return SEARCH_HTML
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "source": "Blu-ray",
                "resolution": "1080p",
                "video_codec": "H.264",
                "audio_codec": "AAC",
                "release_group": "RARBG",
            },
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertTrue(any("Inception+2010" in url for url in calls))
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        first = results[0]
        self.assertEqual(first["provider"], "subs4free")
        self.assertEqual(first["score"], 100)
        self.assertIn("title", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertIn("resolution", first["matches"])
        self.assertEqual(first["provider_payload"]["detail_url"], results[0]["page_link"])
        # Host-side member selection needs episode (and season) in the payload; movies carry None.
        self.assertIn("episode", first["provider_payload"])
        self.assertIsNone(first["provider_payload"]["episode"])
        self.assertIsNone(first["provider_payload"]["season"])

    def test_search_uses_matching_suggestion_pages_when_present(self):
        provider = self.mod.Subs4FreeProvider()
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": SUGGESTIONS_HTML,
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010": SEARCH_HTML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "ell")

    def test_search_normalizes_legacy_mov_sel_suggestion_pages(self):
        provider = self.mod.Subs4FreeProvider()
        suggestions = b"""
        <select name="Mov_sel">
          <option value="?p=movie-details/m640fe75810/inception-2010">Inception 2010</option>
        </select>
        """
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": suggestions,
            "https://www.subs4free.info/movie-details/m640fe75810/inception-2010": SEARCH_HTML,
        }

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)

    def test_search_rejects_unrelated_direct_rows(self):
        provider = self.mod.Subs4FreeProvider()
        body = _html_with_rows([
            _row("eng", "Interstellar 2014 1080p BluRay", "s-interstellar"),
        ])

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if "search_report.php" in url:
                return body
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Dune", "year": 2021},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_search_applies_candidate_cap_after_language_filtering(self):
        provider = self.mod.Subs4FreeProvider()
        rows = [
            _row("ell", f"Inception 2010 Greek Release {index}", f"s-ell-{index}")
            for index in range(self.mod.MAX_CANDIDATES_PER_QUERY)
        ]
        rows.append(_row("eng", "Inception 2010 English Release", "s-eng"))
        body = _html_with_rows(rows)

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if "search_report.php" in url:
                return body
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["language"]["alpha3"], "eng")

    def test_search_continues_fallback_queries_until_requested_languages_are_covered(self):
        provider = self.mod.Subs4FreeProvider()
        responses = {
            "https://www.subs4free.info/search_report.php?search=Inception+2010&searchType=1": _html_with_rows([
                _row("ell", "Inception 2010 Greek Release", "s-ell"),
            ]),
            "https://www.subs4free.info/search_report.php?search=Inception&searchType=1": _html_with_rows([
                _row("eng", "Inception 2010 English Release", "s-eng"),
            ]),
        }
        calls = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            calls.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "ell", "alpha2": "el"}, {"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual({item["language"]["alpha3"] for item in results}, {"ell", "eng"})
        self.assertTrue(any("search=Inception+2010" in url for url in calls))
        self.assertTrue(any("search=Inception&" in url for url in calls))

    def test_derive_matches_accepts_list_valued_codec_metadata(self):
        matches = self.mod.derive_matches(
            {
                "title": "Inception",
                "year": 2010,
                "audio_codec": ["DTS-HD", "MA"],
            },
            {"release_info": "Inception 2010 1080p BluRay"},
        )

        self.assertIn("title", matches)
        self.assertIn("year", matches)
        self.assertNotIn("audio_codec", matches)

    def test_search_accepts_release_year_when_title_contains_year_token(self):
        provider = self.mod.Subs4FreeProvider()
        body = _html_with_rows([
            _row("eng", "1917 2019 1080p BluRay", "s-1917"),
        ])

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if "search_report.php" in url:
                return body
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        results = provider.search(
            {"kind": "movie", "title": "1917", "year": 2019},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["release_info"], "1917 2019 1080p BluRay")

    def test_download_posts_form_after_anti_block_requests(self):
        provider = self.mod.Subs4FreeProvider()
        seen_gets = []
        posts = []

        def get_stub(url, timeout=15, referer=None):
            del timeout
            seen_gets.append((url, referer))
            if url == "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception":
                return DOWNLOAD_HTML
            if "anti-block" in url or "favicon.ico" in url:
                return b"ok"
            raise AssertionError(f"unexpected GET: {url}")

        archive = _zip_body()

        def post_stub(url, data, timeout=15, referer=None):
            del timeout
            posts.append((url, data, referer))
            return archive

        provider._http_get = get_stub
        provider._http_post = post_stub
        result = provider.download(
            {
                "detail_url": "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception",
                "filename": "inception.el.zip",
                "episode": None,
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"request_delay_ms": 0},
        )

        self.assertEqual(posts[0][0], "https://www.subs4free.info/getSub.php")
        self.assertEqual(posts[0][1]["id"], "tkMTc4MDI0MDQ5MTE4NTk1MjUxMTg2MTg5NjQ2MTg5")
        self.assertEqual(posts[0][2], "https://www.subs4free.info/greek-subtitles/s3591aab93d/inception")
        self.assertTrue(any("favicon.ico" in item[0] for item in seen_gets))
        # Archive mode: download() forwards the raw archive bytes for host-side extraction.
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertIsNone(result["episode"])
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)


if __name__ == "__main__":
    unittest.main()
