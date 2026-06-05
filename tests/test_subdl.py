import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subdl"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subdl_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _subdl_response(*items):
    return {
        "status": True,
        "results": [
            {
                "imdb_id": "tt0944947",
                "tmdb_id": 1399,
                "type": "tv",
                "name": "Game of Thrones",
                "sd_id": 12345,
                "year": 2011,
            }
        ],
        "subtitles": list(items),
    }


def _zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()


class SubDLLanguageTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_language_codes_include_regional_and_script_variants(self):
        codes = self.mod.language_codes(
            [
                {"alpha3": "eng"},
                {"alpha3": "por", "country": "BR"},
                {"alpha3": "zho", "script": "Hant"},
            ]
        )

        self.assertEqual(codes, ["BR_PT", "EN", "ZH_BG"])

    def test_unsupported_language_is_ignored(self):
        codes = self.mod.language_codes([{"alpha3": "eng"}, {"alpha3": "xxx"}])

        self.assertEqual(codes, ["EN"])

    def test_regional_request_uses_country_alpha2(self):
        # Bazarr carries Brazilian Portuguese as alpha3 "por" + country_alpha2 "BR".
        # The mapper must read country_alpha2 so SubDL is queried with BR_PT, not generic PT.
        codes = self.mod.language_codes([{"alpha3": "por", "country_alpha2": "BR"}])

        self.assertEqual(codes, ["BR_PT"])


class SubDLQueryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_request_prefers_imdb_and_keeps_api_flags(self):
        requests = self.mod.build_search_requests(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 1,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "eng"}],
            "test-key",
            anime_mode=False,
        )

        self.assertEqual([item[0] for item in requests], ["primary"])
        params = requests[0][1]
        self.assertEqual(params["api_key"], "test-key")
        self.assertEqual(params["imdb_id"], "tt0944947")
        self.assertNotIn("film_name", params)
        self.assertEqual(params["season_number"], 1)
        self.assertEqual(params["episode_number"], 1)
        self.assertEqual(params["type"], "tv")
        self.assertEqual(params["languages"], "EN")
        self.assertEqual(params["subs_per_page"], 30)
        self.assertEqual(params["comment"], 1)
        self.assertEqual(params["releases"], 1)
        self.assertEqual(params["bazarr"], 1)
        self.assertEqual(params["unpack"], 1)

    def test_anime_mode_adds_absolute_episode_and_season_only_requests(self):
        requests = self.mod.build_search_requests(
            {
                "kind": "episode",
                "series": "One Piece",
                "season": 11,
                "episode": 1,
                "absolute_episode": 264,
            },
            [{"alpha3": "eng"}],
            "test-key",
            anime_mode=True,
        )

        labels = [item[0] for item in requests]
        self.assertEqual(labels, ["primary", "absolute", "season"])
        absolute = requests[1][1]
        self.assertEqual(absolute["episode_number"], 264)
        self.assertNotIn("season_number", absolute)
        season = requests[2][1]
        self.assertEqual(season["season_number"], 11)
        self.assertNotIn("episode_number", season)


class SubDLProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_api_key(self):
        provider = self.mod.SubDLProvider()

        with self.assertRaisesRegex(ValueError, "api_key"):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "eng"}],
                {},
            )

    def test_movie_search_retries_with_tmdb_when_primary_is_empty(self):
        provider = self.mod.SubDLProvider()
        calls = []
        movie_item = {
            "language": "EN",
            "name": "inception.2010.1080p.bluray.zip",
            "url": "/subtitle/3197651-3213944.zip",
            "subtitlePage": "/en/subtitle/sd123/inception",
            "release_name": "Inception 2010 1080p BluRay",
            "releases": ["Inception.2010.1080p.BluRay.x264"],
            "author": "subdl-user",
            "comment": "clean sync",
            "hi": False,
        }
        responses = [
            {"status": False, "error": "can't find movie or tv"},
            _subdl_response(movie_item),
        ]

        def stub(params):
            calls.append(dict(params))
            return responses.pop(0)

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "movie",
                "title": "Inception",
                "year": 2010,
                "imdb_id": "tt1375666",
                "tmdb_id": 27205,
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(calls[0]["imdb_id"], "tt1375666")
        self.assertNotIn("tmdb_id", calls[0])
        self.assertEqual(calls[1]["tmdb_id"], 27205)
        self.assertNotIn("imdb_id", calls[1])
        self.assertNotIn("film_name", calls[1])
        first = results[0]
        self.assertEqual(first["provider"], "subdl")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertEqual(first["provider_payload"]["download_url"], "/subtitle/3197651-3213944.zip")
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("title", first["matches"])

    def test_episode_search_skips_packs_outside_anime_mode(self):
        provider = self.mod.SubDLProvider()
        pack_item = {
            "language": "EN",
            "name": "game.of.thrones.s01.pack.zip",
            "url": "/subtitle/pack.zip",
            "subtitlePage": "/en/subtitle/sd999/game-of-thrones",
            "release_name": "Game of Thrones S01 Pack",
            "releases": ["Game.of.Thrones.S01.1080p.BluRay"],
            "season": 1,
            "episode": 1,
            "episode_from": 1,
            "episode_end": 10,
            "hi": False,
        }

        provider._http_get_json = lambda params: _subdl_response(pack_item)
        results = provider.search(
            {
                "kind": "episode",
                "series": "Game of Thrones",
                "season": 1,
                "episode": 5,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key", "anime_mode": False},
        )

        self.assertEqual(results, [])

    def test_anime_mode_accepts_matching_pack_unpack_file(self):
        provider = self.mod.SubDLProvider()
        calls = []
        pack_item = {
            "language": "EN",
            "name": "one.piece.e0264-0336.pack.zip",
            "url": "/subtitle/one-piece-pack.zip",
            "subtitlePage": "/en/subtitle/sd777/one-piece",
            "release_name": "One Piece EP0264-0336 Pack",
            "releases": ["One.Piece.EP0264-0336.1080p.WEB"],
            "season": 9,
            "episode": 1,
            "episode_from": 264,
            "episode_end": 336,
            "hi": False,
            "unpack_files": [
                {
                    "file_n_id": "file263",
                    "name": "One.Piece.E263.srt",
                    "release_name": "One Piece E263",
                    "season": 9,
                    "episode": 263,
                    "language": "EN",
                    "hi": False,
                    "format": "srt",
                    "url": "/subtitle/parent/file263",
                },
                {
                    "file_n_id": "file264",
                    "name": "One.Piece.E264.srt",
                    "release_name": "One Piece E264",
                    "season": 9,
                    "episode": 264,
                    "language": "EN",
                    "hi": False,
                    "format": "srt",
                    "url": "/subtitle/parent/file264",
                },
            ],
        }

        def stub(params):
            calls.append(dict(params))
            if params.get("episode_number") == 264:
                return _subdl_response(pack_item)
            return {"status": False, "error": "can't find movie or tv", "subtitles": []}

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "One Piece",
                "season": 11,
                "episode": 1,
                "absolute_episode": 264,
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key", "anime_mode": True},
        )

        self.assertEqual([call.get("episode_number") for call in calls[:2]], [1, 264])
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider_payload"]["download_url"], "/subtitle/parent/file264")
        self.assertEqual(first["provider_payload"]["archive_download_url"], "/subtitle/one-piece-pack.zip")
        self.assertTrue(first["provider_payload"]["is_pack"])
        self.assertEqual(first["provider_payload"]["absolute_episode"], 264)
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])

    def test_brazilian_portuguese_request_returns_regional_result(self):
        provider = self.mod.SubDLProvider()
        calls = []
        item = {
            "language": "BR_PT",
            "name": "filme.2020.1080p.zip",
            "url": "/subtitle/br-pt.zip",
            "subtitlePage": "/pt/subtitle/sd321/filme",
            "release_name": "Filme 2020 1080p",
            "releases": ["Filme.2020.1080p.WEB"],
            "author": "subdl-user",
            "comment": "sincronizado",
            "hi": False,
        }

        def stub(params):
            calls.append(dict(params))
            return _subdl_response(item)

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Filme", "year": 2020},
            [{"alpha3": "por", "country_alpha2": "BR"}],
            {"api_key": "test-key"},
        )

        # The query must ask SubDL for the regional code rather than generic PT.
        self.assertEqual(calls[0]["languages"], "BR_PT")
        self.assertEqual(len(results), 1)
        language = results[0]["language"]
        self.assertEqual(language["alpha3"], "por")
        self.assertEqual(language["country_alpha2"], "BR")

    def test_hi_and_forced_flags_are_detected_from_metadata(self):
        provider = self.mod.SubDLProvider()
        item = {
            "language": "EN",
            "name": "foreign.parts_hi_.zip",
            "url": "/subtitle/forced-hi.zip",
            "subtitlePage": "/en/subtitle/sd456/movie",
            "release_name": "Foreign Parts",
            "releases": ["Movie.2020.SDH"],
            "author": "subdl-user",
            "comment": "forced foreign SDH",
            "hi": False,
        }

        provider._http_get_json = lambda params: _subdl_response(item)
        results = provider.search(
            {"kind": "movie", "title": "Movie", "year": 2020},
            [{"alpha3": "eng", "hi": True, "forced": True}],
            {"api_key": "test-key"},
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["language"]["hi"])
        self.assertTrue(results[0]["language"]["forced"])
        self.assertTrue(results[0]["hearing_impaired"])


class SubDLProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_direct_unpacked_subtitle(self):
        provider = self.mod.SubDLProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nDirect file\n"
        called = []

        def stub(url, timeout=30):
            del timeout
            called.append(url)
            return body

        provider._http_get_bytes = stub
        result = provider.download(
            {
                "provider": "subdl",
                "schema": 1,
                "download_url": "/subtitle/parent/file264",
                "format": "srt",
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(called, ["https://dl.subdl.com/subtitle/parent/file264"])
        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertFalse(result["empty"])

    def test_download_preserves_explicit_direct_format_without_extension(self):
        provider = self.mod.SubDLProvider()
        body = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nDirect file\n"

        provider._http_get_bytes = lambda url, timeout=30: body
        result = provider.download(
            {
                "provider": "subdl",
                "schema": 1,
                "download_url": "/subtitle/parent/file-vtt",
                "format": "vtt",
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["format"], "vtt")
        self.assertEqual(result["content_type"], "text/vtt")

    def test_download_returns_archive_with_selected_pack_member(self):
        provider = self.mod.SubDLProvider()
        wanted = b"1\n00:00:01,000 --> 00:00:02,000\nEpisode three\n"
        archive = _zip_bytes(
            {
                "Show.S01E02.srt": b"episode two",
                "Show.S01E03.srt": wanted,
                "readme.txt": b"not a subtitle",
            }
        )

        provider._http_get_bytes = lambda url, timeout=30: archive
        result = provider.download(
            {
                "provider": "subdl",
                "schema": 1,
                "download_url": "/subtitle/show-pack.zip",
                "archive_download_url": "/subtitle/show-pack.zip",
                "format": "zip",
                "is_pack": True,
                "kind": "episode",
                "season": 1,
                "episode": 3,
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["member"], "Show.S01E03.srt")
        self.assertNotIn("episode", result)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_returns_first_member_for_non_pack_zip(self):
        provider = self.mod.SubDLProvider()
        archive = _zip_bytes(
            {
                "B.movie.srt": b"second alphabetically",
                "A.movie.srt": b"first alphabetically",
                "notes.txt": b"not a subtitle",
            }
        )

        provider._http_get_bytes = lambda url, timeout=30: archive
        result = provider.download(
            {
                "provider": "subdl",
                "schema": 1,
                "download_url": "/subtitle/movie.zip",
                "format": "zip",
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["member"], "A.movie.srt")
        self.assertNotIn("encoding", result)

    def test_download_lets_host_pick_when_pack_zip_has_no_requested_episode(self):
        provider = self.mod.SubDLProvider()
        archive = _zip_bytes({"Show.S01E02.srt": b"episode two"})

        provider._http_get_bytes = lambda url, timeout=30: archive
        result = provider.download(
            {
                "provider": "subdl",
                "schema": 1,
                "download_url": "/subtitle/show-pack.zip",
                "archive_download_url": "/subtitle/show-pack.zip",
                "format": "zip",
                "is_pack": True,
                "kind": "episode",
                "season": 1,
                "episode": 3,
            },
            {"alpha3": "eng"},
            {"api_key": "test-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 3)
        self.assertNotIn("member", result)

    def test_download_rejects_empty_body(self):
        provider = self.mod.SubDLProvider()
        provider._http_get_bytes = lambda url, timeout=30: b"   "

        with self.assertRaisesRegex(ValueError, "empty"):
            provider.download(
                {
                    "provider": "subdl",
                    "schema": 1,
                    "download_url": "/subtitle/empty.srt",
                    "format": "srt",
                },
                {"alpha3": "eng"},
                {"api_key": "test-key"},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.SubDLProvider()
        provider._http_get_bytes = lambda url, timeout=30: b"<!DOCTYPE html><html><body>Not found</body></html>"

        with self.assertRaisesRegex(ValueError, "HTML"):
            provider.download(
                {
                    "provider": "subdl",
                    "schema": 1,
                    "download_url": "/subtitle/broken.srt",
                    "format": "srt",
                },
                {"alpha3": "eng"},
                {"api_key": "test-key"},
            )


if __name__ == "__main__":
    unittest.main()
