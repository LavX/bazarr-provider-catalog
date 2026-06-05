import base64
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "my_subs"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "my_subs_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEARCH_FIXTURE = (FIXTURE_DIR / "my_subs_search_chernobyl.html").read_bytes()
SHOW_FIXTURE = (FIXTURE_DIR / "my_subs_show_chernobyl.html").read_bytes()
DETAIL_FIXTURE = (
    FIXTURE_DIR / "my_subs_detail_chernobyl_s01e01.html"
).read_bytes()
MOVIE_FIXTURE = (FIXTURE_DIR / "my_subs_movie_avatar.html").read_bytes()
GATE_FIXTURE = (FIXTURE_DIR / "my_subs_download_gate_chernobyl.html").read_bytes()
SRT_FIXTURE = (FIXTURE_DIR / "my_subs_download_chernobyl_en.srt").read_bytes()


class BuildQueriesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_with_year_uses_precise_then_loose_query(self):
        queries = self.mod.build_queries(
            {"kind": "movie", "title": "Avatar", "year": 2009}
        )
        self.assertEqual(queries, ["Avatar 2009", "Avatar"])

    def test_episode_queries_series_first_then_episode_tag(self):
        queries = self.mod.build_queries(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1}
        )
        self.assertEqual(queries, ["Chernobyl", "Chernobyl S01E01"])

    def test_missing_required_fields_return_empty(self):
        self.assertEqual(self.mod.build_queries({"kind": "movie"}), [])
        self.assertEqual(
            self.mod.build_queries({"kind": "episode", "series": "Chernobyl"}), []
        )


class ParseSearchResultsTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_fixture_contains_show_and_movie_candidates(self):
        results = self.mod.parse_search_results(SEARCH_FIXTURE)
        show = next(item for item in results if item["media_type"] == "episode")
        movie = next(item for item in results if item["media_type"] == "movie")

        self.assertEqual(show["provider_id"], "2965")
        self.assertEqual(show["title"], "Chernobyl")
        self.assertEqual(
            show["detail_url"],
            "https://my-subs.co/showlistsubtitles-2965-chernobyl",
        )
        self.assertEqual(movie["title"], "Chernobyl: The Lost Tapes")
        self.assertEqual(movie["year"], 2022)
        self.assertTrue(
            movie["detail_url"].startswith("https://my-subs.co/film-versions-")
        )

    def test_search_parser_ignores_most_downloaded_episode_links(self):
        results = self.mod.parse_search_results(SEARCH_FIXTURE)
        self.assertTrue(results)
        self.assertFalse(any("/versions-" in item["detail_url"] for item in results))


class ParseEpisodeLinksTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_show_page_maps_requested_episode_to_detail_url(self):
        url = self.mod.find_episode_detail_url(
            SHOW_FIXTURE,
            season=1,
            episode=1,
        )
        self.assertEqual(
            url,
            "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles",
        )

    def test_show_page_returns_none_when_episode_missing(self):
        self.assertIsNone(
            self.mod.find_episode_detail_url(SHOW_FIXTURE, season=2, episode=1)
        )


class ParseSubtitleEntriesTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_detail_extracts_language_version_hi_and_download_url(self):
        entries = self.mod.parse_subtitle_entries(
            DETAIL_FIXTURE,
            page_url="https://my-subs.co/versions-2965-1-1-chernobyl-subtitles",
            media_title="Chernobyl Season 1 Episode 1",
        )
        english = [
            item
            for item in entries
            if item["language_alpha3"] == "eng"
            and item["release_info"] == "AMZN.WEBRip-NTb"
        ]
        self.assertGreaterEqual(len(english), 2)
        normal = next(item for item in english if not item["hearing_impaired"])
        hi = next(item for item in english if item["hearing_impaired"])

        self.assertEqual(normal["downloads"], 135334)
        self.assertFalse(normal["hearing_impaired"])
        self.assertTrue(hi["hearing_impaired"])
        self.assertTrue(normal["download_url"].startswith("https://my-subs.co/downloads/"))

    def test_movie_detail_extracts_english_rows(self):
        entries = self.mod.parse_subtitle_entries(
            MOVIE_FIXTURE,
            page_url="https://my-subs.co/film-versions-4068-avatar-subtitles",
            media_title="Avatar (2009)",
        )
        english = [item for item in entries if item["language_alpha3"] == "eng"]
        self.assertGreater(len(english), 20)
        self.assertEqual(english[0]["release_info"], "2009")
        self.assertEqual(english[0]["downloads"], 9530)

    def test_detail_maps_tagalog_ph_flag_to_filipino(self):
        body = b"""
        <a href="/downloads/tagalog-token" class="list-group-item">
          <span class="flag-icon flag-icon-ph" title="Tagalog"></span> <i>Tagalog</i>
          <strong>WEBRip</strong>
          <div class="pull-right"><b>42</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        """

        entries = self.mod.parse_subtitle_entries(
            body,
            page_url="https://my-subs.co/film-versions-1-test-subtitles",
            media_title="Test Movie",
        )

        self.assertEqual(entries[0]["language_alpha3"], "fil")
        self.assertEqual(entries[0]["language_alpha2"], "tl")


class BrazilianPortugueseTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_brazilian_flag_keeps_por_with_br_country(self):
        body = b"""
        <a href="/downloads/pt-br-token" class="list-group-item">
          <span class="flag-icon flag-icon-br" title="Portuguese (Brazilian)"></span>
          <strong>WEB-DL</strong>
          <div class="pull-right"><b>7</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        """
        entries = self.mod.parse_subtitle_entries(
            body,
            page_url="https://my-subs.co/film-versions-1-test-subtitles",
            media_title="Test Movie",
        )

        self.assertEqual(len(entries), 1)
        # Without the fix the row collapses to plain Portuguese with no country.
        self.assertEqual(entries[0]["language_alpha3"], "por")
        self.assertEqual(entries[0]["language_alpha2"], "pt")
        self.assertEqual(entries[0]["language_country"], "BR")

    def test_generic_portuguese_has_no_country(self):
        body = b"""
        <a href="/downloads/pt-token" class="list-group-item">
          <span class="flag-icon flag-icon-pt" title="Portuguese"></span>
          <strong>WEB-DL</strong>
          <div class="pull-right"><b>3</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        """
        entries = self.mod.parse_subtitle_entries(
            body,
            page_url="https://my-subs.co/film-versions-1-test-subtitles",
            media_title="Test Movie",
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["language_alpha3"], "por")
        self.assertIsNone(entries[0]["language_country"])

    def test_search_request_for_por_br_matches_only_brazilian_rows(self):
        detail_html = b"""
        <a href="/downloads/pt-br-token" class="list-group-item">
          <span class="flag-icon flag-icon-br" title="Portuguese (Brazilian)"></span>
          <strong>WEB-DL</strong>
          <div class="pull-right"><b>7</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        <a href="/downloads/pt-token" class="list-group-item">
          <span class="flag-icon flag-icon-pt" title="Portuguese"></span>
          <strong>HDTV</strong>
          <div class="pull-right"><b>3</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        """
        responses = {
            "https://my-subs.co/search.php?key=Avatar%202009": (
                b'<a href="/film-versions-4068-avatar-subtitles" title="Avatar">'
                b"Avatar (2009)</a>"
            ),
            "https://my-subs.co/film-versions-4068-avatar-subtitles": detail_html,
        }
        provider = self.mod.MySubsProvider()

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            video={"kind": "movie", "title": "Avatar", "year": 2009},
            languages=[
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}
            ],
            config={"request_delay_ms": 0},
        )

        # Without the fix both rows collapse to plain "pt" and both leak through.
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["language"]["alpha3"], "por")
        self.assertEqual(result["language"]["alpha2"], "pt")
        self.assertEqual(result["language"]["country_alpha2"], "BR")
        self.assertEqual(result["provider_payload"]["country_alpha2"], "BR")
        self.assertTrue(result["id"].endswith("-por-BR"))

    def test_search_request_for_plain_portuguese_excludes_brazilian_rows(self):
        detail_html = b"""
        <a href="/downloads/pt-br-token" class="list-group-item">
          <span class="flag-icon flag-icon-br" title="Portuguese (Brazilian)"></span>
          <strong>WEB-DL</strong>
          <div class="pull-right"><b>7</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        <a href="/downloads/pt-token" class="list-group-item">
          <span class="flag-icon flag-icon-pt" title="Portuguese"></span>
          <strong>HDTV</strong>
          <div class="pull-right"><b>3</b><span class="glyphicon glyphicon-download-alt"></span></div>
        </a>
        """
        responses = {
            "https://my-subs.co/search.php?key=Avatar%202009": (
                b'<a href="/film-versions-4068-avatar-subtitles" title="Avatar">'
                b"Avatar (2009)</a>"
            ),
            "https://my-subs.co/film-versions-4068-avatar-subtitles": detail_html,
        }
        provider = self.mod.MySubsProvider()

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            video={"kind": "movie", "title": "Avatar", "year": 2009},
            languages=[{"alpha3": "por", "alpha2": "pt"}],
            config={"request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertNotIn("country_alpha2", results[0]["language"])
        self.assertEqual(results[0]["provider_payload"]["download_url"], "https://my-subs.co/downloads/pt-token")


class DownloadGateTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_extracts_second_stage_download_url_from_gate_html(self):
        url = self.mod.extract_download_url(
            GATE_FIXTURE,
            page_url="https://my-subs.co/downloads/source-token",
        )
        self.assertEqual(url, "https://my-subs.co/download-277418")


class MySubsProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_search_skips_failed_show_candidate(self):
        search_html = b"""
        <a href="/showlistsubtitles-9999-broken-show" title="Broken Show">Broken Show</a>
        <a href="/showlistsubtitles-2965-chernobyl" title="Chernobyl">Chernobyl</a>
        """
        responses = {
            "https://my-subs.co/search.php?key=Chernobyl": search_html,
            "https://my-subs.co/showlistsubtitles-2965-chernobyl": SHOW_FIXTURE,
            "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles": DETAIL_FIXTURE,
        }
        provider = self.mod.MySubsProvider()

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url == "https://my-subs.co/showlistsubtitles-9999-broken-show":
                raise TimeoutError("simulated timeout")
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            video={"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            languages=[{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            config={"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)

    def test_candidate_cap_applies_after_media_type_filtering(self):
        movie_rows = "\n".join(
            f'<a href="/film-versions-{index}-other-{index}-subtitles" '
            f'title="Other {index}">Other {index} (200{index % 10})</a>'
            for index in range(self.mod.MAX_CANDIDATES_PER_QUERY + 2)
        ).encode("utf-8")
        search_html = (
            movie_rows
            + b'<a href="/showlistsubtitles-2965-chernobyl" title="Chernobyl">Chernobyl</a>'
        )
        responses = {
            "https://my-subs.co/search.php?key=Chernobyl": search_html,
            "https://my-subs.co/showlistsubtitles-2965-chernobyl": SHOW_FIXTURE,
            "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles": DETAIL_FIXTURE,
        }
        provider = self.mod.MySubsProvider()

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            video={"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            languages=[{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            config={"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)

    def test_media_title_uses_candidate_metadata_for_scoring(self):
        provider = self.mod.MySubsProvider()

        movie_title = provider._media_title(
            {"kind": "movie", "title": "Avatar", "year": 2009},
            {"media_type": "movie", "title": "Avatar: The Way of Water", "year": 2022},
        )
        episode_title = provider._media_title(
            {"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            {"media_type": "episode", "title": "Chernovik"},
        )

        self.assertEqual(movie_title, "Avatar: The Way of Water (2022)")
        self.assertEqual(episode_title, "Chernovik S01E01")

    def test_episode_search_uses_search_show_then_detail_page(self):
        responses = {
            "https://my-subs.co/search.php?key=Chernobyl": SEARCH_FIXTURE,
            "https://my-subs.co/showlistsubtitles-2965-chernobyl": SHOW_FIXTURE,
            "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles": DETAIL_FIXTURE,
        }
        provider = self.mod.MySubsProvider()
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout, referer
            called.append(url)
            if url not in responses:
                raise AssertionError(f"unexpected URL: {url}")
            return responses[url]

        provider._http_get = stub
        results = provider.search(
            video={"kind": "episode", "series": "Chernobyl", "season": 1, "episode": 1},
            languages=[{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            config={"request_delay_ms": 0},
        )

        self.assertGreater(len(results), 0)
        self.assertEqual(called[:3], list(responses))
        first = results[0]
        self.assertEqual(first["provider"], "my_subs")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("series", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertEqual(
            first["provider_payload"]["page_url"],
            "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles",
        )
        self.assertTrue(first["provider_payload"]["download_url"].startswith("https://my-subs.co/downloads/"))


class MySubsProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_follows_gate_and_returns_subtitle_bytes(self):
        provider = self.mod.MySubsProvider()
        called = []

        def stub(url, timeout=15, referer=None):
            del timeout
            called.append((url, referer))
            if url == "https://my-subs.co/downloads/source-token":
                return GATE_FIXTURE
            if url == "https://my-subs.co/download-277418":
                return SRT_FIXTURE
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get = stub
        result = provider.download(
            {
                "provider": "my_subs",
                "schema": 1,
                "download_url": "https://my-subs.co/downloads/source-token",
                "page_url": "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles",
            },
            language={"alpha3": "eng", "alpha2": "en"},
            config={},
        )

        self.assertEqual(
            called,
            [
                (
                    "https://my-subs.co/downloads/source-token",
                    "https://my-subs.co/versions-2965-1-1-chernobyl-subtitles",
                ),
                (
                    "https://my-subs.co/download-277418",
                    "https://my-subs.co/downloads/source-token",
                ),
            ],
        )
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertFalse(result["empty"])
        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_FIXTURE)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_FIXTURE).hexdigest())


if __name__ == "__main__":
    unittest.main()
