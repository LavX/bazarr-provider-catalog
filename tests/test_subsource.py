import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subsource"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subsource_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()


def _contributors():
    return [
        {"id": 10, "displayname": "Other User"},
        {"id": 20, "displayname": "SubSource User"},
    ]


class SubSourceLanguageTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_language_names_include_regional_and_legacy_spellings(self):
        names = self.mod.language_names(
            [
                {"alpha3": "eng"},
                {"alpha3": "por", "country": "BR"},
                {"alpha3": "fas"},
            ]
        )

        self.assertEqual(names, ["Brazillian Portuguese", "English", "Farsi_persian"])

    def test_auth_headers_use_current_api_key_header(self):
        self.assertEqual(
            self.mod.auth_headers("test-key")["X-API-Key"],
            "test-key",
        )

    def test_persian_slash_display_name_is_normalized(self):
        # SubSource returns "Farsi/Persian" for some Persian subtitles; a fas
        # request must not be silently dropped.
        language = self.mod._language_dict("Farsi/Persian")
        self.assertIsNotNone(language)
        self.assertEqual(language["alpha3"], "fas")

    def test_brazilian_portuguese_result_carries_country_alpha2(self):
        # The country must survive into the result language so Bazarr keeps
        # pt-BR distinct from generic Portuguese.
        language = self.mod._language_dict("Brazillian Portuguese")
        self.assertEqual(language["alpha3"], "por")
        self.assertEqual(language.get("country_alpha2"), "BR")


class SubSourceSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_api_key(self):
        provider = self.mod.SubSourceProvider()

        with self.assertRaisesRegex(ValueError, "api_key"):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "eng"}],
                {},
            )

    def test_movie_search_falls_back_from_imdb_to_text_and_returns_subtitles(self):
        provider = self.mod.SubSourceProvider()
        calls = []
        subtitle_item = {
            "subtitleId": 501,
            "language": "English",
            "link": "/subtitles/inception/english/501",
            "releaseInfo": ["Inception.2010.1080p.BluRay.x264"],
            "commentary": "clean sync",
            "hearingImpaired": False,
            "foreignParts": False,
            "contributors": _contributors(),
            "uploaderId": 20,
        }

        def stub(path, params, config):
            del config
            calls.append((path, dict(params)))
            if path == "movies/search" and params["searchType"] == "imdb":
                return {"data": []}
            if path == "movies/search" and params["searchType"] == "text":
                return {
                    "data": [
                        {
                            "movieId": 1001,
                            "title": "Origami Dream",
                            "alternateTitle": "Inception",
                            "releaseYear": 2010,
                        }
                    ]
                }
            if path == "subtitles":
                self.assertEqual(params["movieId"], 1001)
                self.assertEqual(params["language"], "english")
                return {"success": True, "data": [subtitle_item]}
            raise AssertionError(f"unexpected call: {path} {params}")

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(
            calls[:2],
            [
                ("movies/search", {"searchType": "imdb", "imdb": "tt1375666"}),
                ("movies/search", {"searchType": "text", "q": "inception"}),
            ],
        )
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "subsource")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertEqual(first["release_info"], "Inception.2010.1080p.BluRay.x264")
        self.assertEqual(first["display"]["uploader"], "SubSource User")
        self.assertEqual(first["provider_payload"]["subtitle_id"], 501)
        self.assertIn("title", first["matches"])
        # IMDb lookup returned no result, so the text fallback selected this
        # title; it must not claim an unverified imdb_id match.
        self.assertNotIn("imdb_id", first["matches"])

    def test_episode_search_uses_season_and_episode_params(self):
        provider = self.mod.SubSourceProvider()
        calls = []
        subtitle_item = {
            "subtitleId": 601,
            "language": "English",
            "link": "/subtitles/game-of-thrones/english/601",
            "releaseInfo": ["Game.of.Thrones.S01E01.1080p.BluRay"],
            "commentary": "",
            "hearingImpaired": False,
            "foreignParts": False,
            "contributors": _contributors(),
            "uploaderId": 20,
        }

        def stub(path, params, config):
            del config
            calls.append((path, dict(params)))
            if path == "movies/search":
                self.assertEqual(params["searchType"], "imdb")
                self.assertEqual(params["season"], 1)
                return {
                    "data": [
                        {
                            "movieId": 2002,
                            "title": "Game of Thrones",
                            "releaseYear": 2011,
                        }
                    ]
                }
            if path == "subtitles":
                self.assertEqual(params["movieId"], 2002)
                self.assertEqual(params["seasonNumber"], 1)
                self.assertEqual(params["episodeNumber"], 1)
                return {"success": True, "data": [subtitle_item]}
            raise AssertionError(f"unexpected call: {path} {params}")

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "year": 2011,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(calls[1][0], "subtitles")
        self.assertEqual(len(results), 1)
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_filters_forced_and_hearing_impaired_variants(self):
        provider = self.mod.SubSourceProvider()
        forced_item = {
            "subtitleId": 701,
            "language": "English",
            "link": "/subtitles/movie/english/701",
            "releaseInfo": ["Movie.2020.1080p.WEB"],
            "commentary": "forced foreign parts",
            "hearingImpaired": False,
            "foreignParts": True,
            "contributors": _contributors(),
            "uploaderId": 20,
        }
        normal_item = dict(forced_item, subtitleId=702, foreignParts=False, commentary="")
        hi_item = dict(forced_item, subtitleId=703, foreignParts=False, commentary="SDH closed caption")

        provider._http_get_json = lambda path, params, config: (
            {
                "data": [
                    {"movieId": 3003, "title": "Movie", "releaseYear": 2020}
                ]
            }
            if path == "movies/search"
            else {"success": True, "data": [forced_item, normal_item, hi_item]}
        )

        normal_results = provider.search(
            {"kind": "movie", "title": "Movie", "year": 2020},
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )
        forced_results = provider.search(
            {"kind": "movie", "title": "Movie", "year": 2020},
            [{"alpha3": "eng", "forced": True}],
            {"api_key": "test-key"},
        )
        hi_results = provider.search(
            {"kind": "movie", "title": "Movie", "year": 2020},
            [{"alpha3": "eng", "hi": True}],
            {"api_key": "test-key"},
        )

        self.assertEqual([item["id"] for item in normal_results], [702, 703])
        self.assertEqual([item["id"] for item in forced_results], [701])
        self.assertEqual([item["id"] for item in hi_results], [703])

    def test_episode_pack_without_episode_is_accepted(self):
        provider = self.mod.SubSourceProvider()
        pack_item = {
            "subtitleId": 801,
            "language": "English",
            "link": "/subtitles/show/english/801",
            "releaseInfo": ["Show.S01.1080p.BluRay"],
            "commentary": "season pack",
            "hearingImpaired": False,
            "foreignParts": False,
            "contributors": _contributors(),
            "uploaderId": 20,
        }

        provider._http_get_json = lambda path, params, config: (
            {
                "data": [
                    {"movieId": 4004, "title": "Show", "releaseYear": 2020}
                ]
            }
            if path == "movies/search"
            else {"success": True, "data": [pack_item]}
        )
        results = provider.search(
            {
                "kind": "episode",
                "series": "Show",
                "season": 1,
                "episode": 3,
                "year": 2020,
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["provider_payload"]["is_pack"])
        self.assertIn("episode", results[0]["matches"])

    def test_episode_without_season_episode_token_is_accepted(self):
        # The subtitles request already filters by seasonNumber/episodeNumber,
        # so a release whose name lacks an SxxEyy token (miniseries/DVD) must
        # still reach Bazarr instead of being dropped as a mismatch.
        provider = self.mod.SubSourceProvider()
        item = {
            "subtitleId": 901,
            "language": "English",
            "link": "/subtitles/show/english/901",
            "releaseInfo": ["Show.DVDRip.XviD"],
            "commentary": "",
            "hearingImpaired": False,
            "foreignParts": False,
            "contributors": _contributors(),
            "uploaderId": 20,
        }

        provider._http_get_json = lambda path, params, config: (
            {"data": [{"movieId": 5005, "title": "Show", "releaseYear": 2020}]}
            if path == "movies/search"
            else {"success": True, "data": [item]}
        )
        results = provider.search(
            {
                "kind": "episode",
                "series": "Show",
                "season": 2,
                "episode": 4,
                "year": 2020,
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 901)
        self.assertTrue(results[0]["provider_payload"]["is_pack"])

    def test_episode_with_mismatching_season_token_is_rejected(self):
        # A present-but-wrong season token must still be dropped.
        provider = self.mod.SubSourceProvider()
        item = {
            "subtitleId": 902,
            "language": "English",
            "link": "/subtitles/show/english/902",
            "releaseInfo": ["Show.S03E01.1080p.WEB"],
            "commentary": "",
            "hearingImpaired": False,
            "foreignParts": False,
            "contributors": _contributors(),
            "uploaderId": 20,
        }

        provider._http_get_json = lambda path, params, config: (
            {"data": [{"movieId": 5006, "title": "Show", "releaseYear": 2020}]}
            if path == "movies/search"
            else {"success": True, "data": [item]}
        )
        results = provider.search(
            {
                "kind": "episode",
                "series": "Show",
                "season": 2,
                "episode": 1,
                "year": 2020,
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(results, [])

    def test_movie_imdb_match_only_claimed_when_result_selected_by_imdb(self):
        # When the IMDb search itself returns the selected title, imdb_id is a
        # real match; the text-fallback case is covered separately and must not
        # claim it.
        provider = self.mod.SubSourceProvider()
        subtitle_item = {
            "subtitleId": 1001,
            "language": "English",
            "link": "/subtitles/inception/english/1001",
            "releaseInfo": ["Inception.2010.1080p.BluRay.x264"],
            "commentary": "",
            "hearingImpaired": False,
            "foreignParts": False,
            "contributors": _contributors(),
            "uploaderId": 20,
        }

        def stub(path, params, config):
            del config
            if path == "movies/search" and params["searchType"] == "imdb":
                return {
                    "data": [
                        {"movieId": 7007, "title": "Inception", "releaseYear": 2010}
                    ]
                }
            if path == "subtitles":
                return {"success": True, "data": [subtitle_item]}
            raise AssertionError(f"unexpected call: {path} {params}")

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(len(results), 1)
        self.assertIn("imdb_id", results[0]["matches"])


class SubSourceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_archive_and_pins_selected_member(self):
        provider = self.mod.SubSourceProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"
        archive = _zip_bytes({"Movie.2020.srt": body})
        called = []

        def stub(path, config):
            called.append((path, dict(config)))
            return archive

        provider._http_get_bytes = stub
        result = provider.download(
            {
                "provider": "subsource",
                "schema": 1,
                "subtitle_id": 501,
                "format": "zip",
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(called[0][0], "subtitles/501/download")
        # Host-side extraction: hand back the raw archive bytes plus the selected member.
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["member"], "Movie.2020.srt")
        # No inline extraction, no encoding pinning.
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_pins_requested_episode_member_from_pack(self):
        provider = self.mod.SubSourceProvider()
        archive = _zip_bytes(
            {
                "Show.S01E02.srt": b"episode two",
                "Show.S01E03.srt": b"episode three",
            }
        )

        provider._http_get_bytes = lambda path, config: archive
        result = provider.download(
            {
                "provider": "subsource",
                "schema": 1,
                "subtitle_id": 801,
                "is_pack": True,
                "kind": "episode",
                "season": 1,
                "episode": 3,
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["member"], "Show.S01E03.srt")

    def test_download_pack_without_matching_member_lets_host_pick_by_episode(self):
        provider = self.mod.SubSourceProvider()
        archive = _zip_bytes(
            {
                "Show.S01E01.srt": b"episode one",
                "Show.S01E02.srt": b"episode two",
            }
        )

        provider._http_get_bytes = lambda path, config: archive
        result = provider.download(
            {
                "provider": "subsource",
                "schema": 1,
                "subtitle_id": 802,
                "is_pack": True,
                "kind": "episode",
                "season": 1,
                "episode": 5,
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        # No confident member match, so the host picks by episode via guessit.
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 5)

    def test_download_returns_rar_archive_for_host_picking(self):
        provider = self.mod.SubSourceProvider()
        body = b"Rar!\x1a\x07\x00rar-archive-bytes"

        provider._http_get_bytes = lambda path, config: body
        result = provider.download(
            {
                "provider": "subsource",
                "schema": 1,
                "subtitle_id": 901,
                "kind": "episode",
                "season": 2,
                "episode": 4,
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        # stdlib cannot list a rar, so the worker hands the bytes to the host with episode.
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 4)

    def test_download_rejects_empty_body(self):
        provider = self.mod.SubSourceProvider()
        provider._http_get_bytes = lambda path, config: b"   "

        with self.assertRaisesRegex(ValueError, "empty"):
            provider.download(
                {"provider": "subsource", "schema": 1, "subtitle_id": 501},
                {"alpha3": "eng"},
                {"api_key": "test-key"},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.SubSourceProvider()
        provider._http_get_bytes = lambda path, config: b"<!DOCTYPE html><html><body>error</body></html>"

        with self.assertRaisesRegex(ValueError, "HTML"):
            provider.download(
                {"provider": "subsource", "schema": 1, "subtitle_id": 501},
                {"alpha3": "eng"},
                {"api_key": "test-key"},
            )


if __name__ == "__main__":
    unittest.main()
