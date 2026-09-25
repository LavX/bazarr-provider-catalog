import base64
import hashlib
import http.client
import importlib.util
import io
import json
import threading
import time
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest.mock import call, patch


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

    def test_new_subdl_language_codes_are_searchable(self):
        languages = [
            {"alpha3": code}
            for code in ("hye", "kaz", "kir", "khm", "kan", "mon", "eus", "glg", "gle", "jav", "sun")
        ]

        self.assertEqual(
            self.mod.language_codes(languages),
            ["EU", "GA", "GL", "HY", "JV", "KK", "KM", "KN", "KY", "MN", "SU"],
        )

    def test_traditional_chinese_country_and_script_aliases_use_big5_code(self):
        self.assertEqual(
            self.mod.language_codes(
                [
                    {"alpha3": "zho", "country_alpha2": "TW"},
                    {"alpha3": "zho", "script": "Hant"},
                ]
            ),
            ["ZH_BG"],
        )


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
        self.assertEqual(params["client"], "bazarr")
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

    def test_episode_search_accepts_packs_outside_anime_mode(self):
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

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["episode"], 5)
        self.assertTrue(results[0]["provider_payload"]["is_pack"])

    def test_episode_search_rejects_pack_from_another_season(self):
        provider = self.mod.SubDLProvider()
        pack_item = {
            "language": "EN",
            "name": "show.s02.pack.zip",
            "url": "/subtitle/pack.zip",
            "release_name": "Show S02 Pack",
            "season": 2,
            "episode_from": 1,
            "episode_end": 10,
            "hi": False,
        }
        provider._http_get_json = lambda params: _subdl_response(pack_item)

        results = provider.search(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 5},
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(results, [])

    def test_full_season_pack_requires_matching_child_when_unpack_files_are_listed(self):
        provider = self.mod.SubDLProvider()
        pack_item = {
            "language": "EN",
            "name": "show.s01.full.season.zip",
            "url": "/subtitle/season-pack.zip",
            "release_name": "Show S01 Full Season",
            "season": 1,
            "full_season": True,
            "unpack_files": [
                {
                    "file_n_id": "episode-2",
                    "name": "Show.S01E02.srt",
                    "season": 1,
                    "episode": 2,
                    "language": "EN",
                    "hi": False,
                    "url": "/subtitle/episode-2.srt",
                }
            ],
            "hi": False,
        }
        provider._http_get_json = lambda params: _subdl_response(pack_item)

        results = provider.search(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 5},
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(results, [])

    def test_pack_child_from_another_season_does_not_match_same_episode_number(self):
        provider = self.mod.SubDLProvider()
        pack_item = {
            "language": "EN",
            "name": "show.s02.full.season.zip",
            "url": "/subtitle/season-2-pack.zip",
            "release_name": "Show S02 Full Season",
            "season": 2,
            "full_season": True,
            "unpack_files": [
                {
                    "file_n_id": "season-2-episode-5",
                    "name": "Show.S02E05.srt",
                    "season": 2,
                    "episode": 5,
                    "language": "EN",
                    "hi": False,
                    "url": "/subtitle/season-2-episode-5.srt",
                }
            ],
            "hi": False,
        }
        provider._http_get_json = lambda params: _subdl_response(pack_item)

        results = provider.search(
            {"kind": "episode", "series": "Show", "season": 1, "episode": 5},
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(results, [])

    def test_stylized_sdh_marker_marks_result_hearing_impaired(self):
        item = {
            "language": "EN",
            "name": "movie-en.zip",
            "release_name": "Movie.2026.1080p.𝓢𝓓𝓗",
        }

        self.assertTrue(self.mod.is_hearing_impaired(item))

    def test_subdl_runtime_policy_bounds_pagination_and_controls_unpack(self):
        provider = self.mod.SubDLProvider()
        calls = []
        full_page = [
            {"name": f"unsupported-{index}", "language": "unsupported", "url": f"/subtitle/{index}.zip"}
            for index in range(30)
        ]

        def get_json(params):
            calls.append(dict(params))
            if params.get("page", 1) == 1:
                return {
                    "status": True,
                    "subtitles": full_page,
                    "bazarr_policy": {"max_pages": 99, "unpack_enabled": False},
                }
            return {"status": True, "subtitles": full_page}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["page"], 2)
        self.assertNotIn("unpack", calls[1])

    def test_subdl_runtime_policy_can_disable_the_provider(self):
        provider = self.mod.SubDLProvider()
        item = {"name": "movie-en.zip", "language": "EN", "url": "/movie.zip"}
        calls = []

        def get_json(params):
            calls.append(dict(params))
            return {
                "status": True,
                "subtitles": [item],
                "bazarr_policy": {"enabled": False},
            }

        provider._http_get_json = get_json
        results = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "eng"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(results, [])
        self.assertEqual(len(calls), 1)

    def test_subdl_runtime_policy_can_disable_season_and_title_fallbacks(self):
        provider = self.mod.SubDLProvider()
        calls = []

        def get_json(params):
            calls.append(dict(params))
            if len(calls) == 1:
                return {
                    "status": True,
                    "subtitles": [],
                    "bazarr_policy": {
                        "season_fallback_enabled": False,
                        "title_fallback_enabled": False,
                    },
                }
            return {"status": True, "subtitles": []}

        provider._http_get_json = get_json
        provider.search(
            {
                "kind": "episode",
                "series": "Show",
                "season": 2,
                "episode": 3,
                "absolute_episode": 40,
                "series_imdb_id": "tt1234567",
            },
            [{"alpha3": "eng"}],
            {"api_key": "test-key", "anime_mode": True},
        )

        self.assertEqual([call.get("episode_number") for call in calls], [3, 40])

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


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(code, body=b"", headers=None):
    import urllib.error

    return urllib.error.HTTPError(
        url="https://api.subdl.com/api/v1/subtitles",
        code=code,
        msg="error",
        hdrs=headers or {},
        fp=io.BytesIO(body),
    )


class SubDLTransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.sleeps = []
        sleep_patch = patch.object(self.mod.time, "sleep", self.sleeps.append)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def _patch_urlopen(self, sequence):
        calls = {"count": 0}

        def fake_urlopen(request, timeout=None):
            del request, timeout
            index = calls["count"]
            calls["count"] += 1
            outcome = sequence[index]
            if isinstance(outcome, Exception):
                raise outcome
            return _FakeResponse(outcome)

        urlopen_patch = patch.object(self.mod.urllib.request, "urlopen", fake_urlopen)
        urlopen_patch.start()
        self.addCleanup(urlopen_patch.stop)
        return calls

    def test_json_helper_retries_on_url_error_then_succeeds(self):
        import urllib.error

        body = json.dumps(_subdl_response()).encode("utf-8")
        calls = self._patch_urlopen(
            [urllib.error.URLError("connection reset"), body]
        )

        provider = self.mod.SubDLProvider()
        data = provider._http_get_json({"api_key": "test-key"})

        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.sleeps), 1)
        self.assertTrue(data.get("status"))

    def test_json_helper_retries_on_503_then_succeeds(self):
        body = json.dumps(_subdl_response()).encode("utf-8")
        calls = self._patch_urlopen(
            [_http_error(503), _http_error(503), body]
        )

        provider = self.mod.SubDLProvider()
        data = provider._http_get_json({"api_key": "test-key"})

        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(data.get("status"))

    def test_json_helper_honors_retry_after_on_429(self):
        body = json.dumps(_subdl_response()).encode("utf-8")
        self._patch_urlopen(
            [_http_error(429, headers={"Retry-After": "4"}), body]
        )

        provider = self.mod.SubDLProvider()
        data = provider._http_get_json({"api_key": "test-key"})

        self.assertEqual(self.sleeps, [4.0])
        self.assertTrue(data.get("status"))

    def test_json_helper_does_not_retry_404(self):
        calls = self._patch_urlopen([_http_error(404, body=b"missing")])

        provider = self.mod.SubDLProvider()
        with self.assertRaises(RuntimeError):
            provider._http_get_json({"api_key": "test-key"})

        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.sleeps, [])

    def test_json_helper_does_not_retry_403(self):
        calls = self._patch_urlopen([_http_error(403, body=b"forbidden")])

        provider = self.mod.SubDLProvider()
        with self.assertRaises(ValueError):
            provider._http_get_json({"api_key": "test-key"})

        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.sleeps, [])

    def test_json_helper_raises_after_exhausting_transient_retries(self):
        calls = self._patch_urlopen(
            [_http_error(503), _http_error(503), _http_error(503)]
        )

        provider = self.mod.SubDLProvider()
        with self.assertRaises(RuntimeError):
            provider._http_get_json({"api_key": "test-key"})

        self.assertEqual(calls["count"], 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_bytes_helper_retries_on_timeout_then_succeeds(self):
        calls = self._patch_urlopen([TimeoutError("read timed out"), b"OK"])

        provider = self.mod.SubDLProvider()
        body = provider._http_get_bytes("https://dl.subdl.com/file.srt")

        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(self.sleeps), 1)
        self.assertEqual(body, b"OK")

    def test_bytes_helper_does_not_retry_404(self):
        calls = self._patch_urlopen([_http_error(404)])

        provider = self.mod.SubDLProvider()
        with self.assertRaises(self.mod.urllib.error.HTTPError):
            provider._http_get_bytes("https://dl.subdl.com/missing.srt")

        self.assertEqual(calls["count"], 1)
        self.assertEqual(self.sleeps, [])


class SubDLSemanticHTTPErrorTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.provider = self.mod.SubDLProvider()

    def _invoke(self, helper):
        if helper == "json":
            return self.provider._http_get_json({"api_key": "synthetic-private-key"})
        return self.provider._http_get_bytes("https://dl.subdl.com/synthetic-private-url.srt")

    def _errors(self, codes, bodies, headers=None):
        errors = [_http_error(code, body, headers) for code, body in zip(codes, bodies)]
        for error in errors:
            self.addCleanup(error.close)
        return errors

    def _assert_failure(self, helper, code, body, expected):
        errors = self._errors([code] * 3, [body] * 3)
        with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors) as request:
            with patch.object(self.mod.time, "sleep") as sleep:
                with self.assertRaises(Exception) as raised:
                    self._invoke(helper)
        self.assertEqual(type(raised.exception).__name__, expected)
        self.assertIs(type(raised.exception), getattr(self.mod, expected))
        self.assertIs(raised.exception.__cause__, errors[-1])
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.5), call(1.0)])
        for private_text in ("synthetic-private", "https://", "api_key", "diagnostic-marker"):
            self.assertNotIn(private_text, str(raised.exception))

    def test_known_429_tokens_keep_distinct_semantics(self):
        for helper in ("json", "bytes"):
            for token, expected in (
                ("daily_limit", "DownloadLimitExceeded"),
                ("api_download_limit_exceeded", "DownloadLimitExceeded"),
                ("service_busy", "ServiceUnavailable"),
                ("rate_limit", "TooManyRequests"),
            ):
                with self.subTest(helper=helper, token=token):
                    body = json.dumps({"error": token, "message": "diagnostic-marker"}).encode()
                    self._assert_failure(helper, 429, body, expected)

    def test_unrecognized_429_bodies_remain_rate_limited(self):
        bodies = (
            b"", b"<html>daily_limit diagnostic-marker</html>", b"\xff",
            b"{", b"null", b"42", b'"daily_limit"', b"[]", b"{}",
            b'{"error":null}', b'{"error":["daily_limit"]}',
            b'{"error":{"code":"daily_limit"}}', b'{"code":"daily_limit"}',
            b'{"error":"unknown"}', b'{"error":"DAILY_LIMIT"}',
            b'{"error":" daily_limit "}',
            b'{"error":"daily_limit","padding":"' + b"x" * (64 * 1024) + b'"}',
        )
        for helper in ("json", "bytes"):
            for index, body in enumerate(bodies):
                with self.subTest(helper=helper, body_index=index):
                    self._assert_failure(helper, 429, body, "TooManyRequests")

    def test_all_server_errors_are_service_failures_even_with_quota_body(self):
        for helper in ("json", "bytes"):
            for code in (500, 502, 503, 599):
                for body in (b"<html>diagnostic-marker</html>", b'{"error":"daily_limit"}'):
                    with self.subTest(helper=helper, code=code, body=body):
                        self._assert_failure(helper, code, body, "ServiceUnavailable")

    def test_excessively_nested_429_json_remains_rate_limited(self):
        body = b"[" * 30000 + b"0" + b"]" * 30000
        for helper in ("json", "bytes"):
            with self.subTest(helper=helper):
                self._assert_failure(helper, 429, body, "TooManyRequests")

    def test_error_body_read_is_bounded_and_only_final_response_is_read(self):
        for helper in ("json", "bytes"):
            with self.subTest(helper=helper):
                errors = self._errors([429] * 3, [b"x" * (70 * 1024)] * 3)
                with patch.object(errors[0], "read", wraps=errors[0].read) as first_read:
                    with patch.object(errors[-1], "read", wraps=errors[-1].read) as final_read:
                        with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors):
                            with patch.object(self.mod.time, "sleep"):
                                with self.assertRaises(Exception) as raised:
                                    self._invoke(helper)
                self.assertEqual(type(raised.exception).__name__, "TooManyRequests")
                first_read.assert_not_called()
                final_read.assert_called_once_with(64 * 1024 + 1)

    def test_exhausted_semantic_error_closes_final_http_response(self):
        class FakeSocket:
            def __init__(self, code, body):
                self.stream = io.BytesIO(
                    f"HTTP/1.1 {code} Error\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
                )

            def makefile(self, *args):
                return self.stream

        for helper in ("json", "bytes"):
            for code, body in ((429, b'{"error":"daily_limit"}'), (429, b"x" * (70 * 1024)),
                               (500, b"service unavailable"), (503, b"x" * (70 * 1024))):
                with self.subTest(helper=helper, code=code, body_size=len(body)):
                    responses = []
                    errors = []
                    for _ in range(3):
                        response = http.client.HTTPResponse(FakeSocket(code, body))
                        response.begin()
                        error = self.mod.urllib.error.HTTPError(
                            "https://fixture.invalid", code, "Error", response.headers, response
                        )
                        responses.append(response)
                        errors.append(error)
                        self.addCleanup(error.close)
                    with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors):
                        with patch.object(self.mod.time, "sleep"):
                            with self.assertRaises(RuntimeError) as raised:
                                self._invoke(helper)
                    self.assertIs(raised.exception.__cause__, errors[-1])
                    self.assertTrue(responses[-1].isclosed())

    def test_failed_error_body_read_remains_rate_limited(self):
        for helper in ("json", "bytes"):
            for read_failure in (OSError("diagnostic-marker"), ValueError("closed response")):
                with self.subTest(helper=helper, read_failure=type(read_failure).__name__):
                    errors = self._errors([429] * 3, [b""] * 3)
                    with patch.object(errors[-1], "read", side_effect=read_failure):
                        with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors):
                            with patch.object(self.mod.time, "sleep"):
                                with self.assertRaises(Exception) as raised:
                                    self._invoke(helper)
                    self.assertEqual(type(raised.exception).__name__, "TooManyRequests")
                    self.assertNotIn("diagnostic-marker", str(raised.exception))

    def test_final_exhausted_response_determines_category(self):
        for helper in ("json", "bytes"):
            for bodies, expected in (
                ([b'{"error":"daily_limit"}', b"", b'{"error":"rate_limit"}'], "TooManyRequests"),
                ([b'{"error":"rate_limit"}', b"", b'{"error":"daily_limit"}'], "DownloadLimitExceeded"),
            ):
                with self.subTest(helper=helper, expected=expected):
                    errors = self._errors([429] * 3, bodies)
                    with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors):
                        with patch.object(self.mod.time, "sleep"):
                            with self.assertRaises(Exception) as raised:
                                self._invoke(helper)
                    self.assertEqual(type(raised.exception).__name__, expected)
                    self.assertIs(raised.exception.__cause__, errors[-1])

    def test_transient_failures_can_recover_before_exhaustion(self):
        for helper in ("json", "bytes"):
            with self.subTest(helper=helper):
                body = b'{"status":true,"subtitles":[]}' if helper == "json" else b"subtitle bytes"
                errors = self._errors([429, 500], [b'{"error":"daily_limit"}', b""])
                with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors + [_FakeResponse(body)]):
                    with patch.object(self.mod.time, "sleep") as sleep:
                        result = self._invoke(helper)
                self.assertEqual(result, json.loads(body) if helper == "json" else body)
                self.assertEqual(sleep.call_args_list, [call(0.5), call(1.0)])

    def test_retry_after_clamps_integer_and_preserves_invalid_backoff(self):
        for helper in ("json", "bytes"):
            for header, expected_delay in (("999", 8.0), ("-1", 0.5), ("later", 0.5),
                                           ("Wed, 21 Oct 2015 07:28:00 GMT", 0.5), ("0", None)):
                with self.subTest(helper=helper, header=header):
                    error = self._errors([429], [b""], {"Retry-After": header})[0]
                    body = b"{}" if helper == "json" else b"subtitle"
                    with patch.object(self.mod.urllib.request, "urlopen", side_effect=[error, _FakeResponse(body)]):
                        with patch.object(self.mod.time, "sleep") as sleep:
                            self._invoke(helper)
                    self.assertEqual(sleep.call_args_list, [] if expected_delay is None else [call(expected_delay)])

    def test_permanent_statuses_keep_existing_behavior_and_single_attempt(self):
        for helper in ("json", "bytes"):
            for code in (403, 404):
                with self.subTest(helper=helper, code=code):
                    error = self._errors([code], [b"missing"])[0]
                    expected = ValueError if code == 403 else (RuntimeError if helper == "json" else self.mod.urllib.error.HTTPError)
                    with patch.object(self.mod.urllib.request, "urlopen", side_effect=error) as request:
                        with patch.object(self.mod.time, "sleep") as sleep:
                            with self.assertRaises(Exception) as raised:
                                self._invoke(helper)
                    self.assertIs(type(raised.exception), expected)
                    self.assertEqual(request.call_count, 1)
                    sleep.assert_not_called()

    def test_quota_failure_does_not_start_movie_or_anime_fallback(self):
        videos = (
            {"kind": "movie", "title": "Example", "imdb_id": "tt1234567", "tmdb_id": 123},
            {"kind": "episode", "series": "Example", "season": 2, "episode": 1, "absolute_episode": 13},
        )
        for video in videos:
            with self.subTest(kind=video["kind"]):
                errors = self._errors([429] * 3, [b'{"error":"daily_limit"}'] * 3)
                with patch.object(self.mod.urllib.request, "urlopen", side_effect=errors) as request:
                    with patch.object(self.mod.time, "sleep"):
                        with self.assertRaises(Exception) as raised:
                            self.provider.search(video, [{"alpha3": "eng"}], {"api_key": "synthetic-key", "anime_mode": True})
                self.assertEqual(type(raised.exception).__name__, "DownloadLimitExceeded")
                self.assertEqual(request.call_count, 3)


class SubDLAITranslationSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_manifest_exposes_opt_in_settings_and_worker_timeout(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())
        schema = manifest["config_schema"]["properties"]

        self.assertEqual(manifest["version"], "0.2.0")
        self.assertIs(schema["ai_translate"]["default"], False)
        self.assertIn("SubDL publishes each translation as a regular subtitle", schema["ai_translate"]["title"])
        self.assertIs(schema["include_ai_translated"]["default"], False)
        timeout_schema = schema["ai_translate_timeout_seconds"]
        self.assertEqual(timeout_schema["default"], "240")
        self.assertEqual(timeout_schema["enum"], ["60", "120", "240", "360", "480", "600"])
        self.assertEqual(timeout_schema["title"], "AI translation wait in seconds")
        self.assertEqual(
            [self.mod._translation_timeout_seconds({"ai_translate_timeout_seconds": value})
             for value in (60, "60", -1, "-1", 100000, "100000", "abc", "", None)],
            [60, 60, 60, 60, 600, 600, 240, 240, 240],
        )

    def test_entitled_missing_language_returns_candidate_and_quota_event(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True,
            "missing_languages": ["FR"],
            "sources": [{"n_id": 771, "language": "EN", "hi": False,
                         "releases": ["Movie.2024.1080p.BluRay"]}],
            "quota_reset_at": "2026-10-01T00:00:00Z",
        }
        provider._http_get_json = lambda params: response

        results = provider.search(
            {"kind": "movie", "title": "Movie", "year": 2024, "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "secret-test-key", "ai_translate": True},
        )

        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["id"], "ai:771:FR:plain")
        self.assertEqual(candidate["language"], {"alpha3": "fra", "hi": False, "forced": False})
        self.assertEqual(candidate["display"]["uploader"], "SubDL AI translation from EN")
        self.assertTrue(candidate["ai_translated"])
        self.assertEqual(candidate["provider_payload"]["kind"], "ai_translation")
        self.assertEqual(candidate["provider_payload"]["target_language"], "FR")
        self.assertNotIn("secret-test-key", repr(candidate))
        self.assertEqual(provider.drain_events()[0], {"type": "translation_quota", "entitled": True,
            "exhausted": False, "remaining": None, "limit": None,
            "reset_at": "2026-10-01T00:00:00Z"})
        self.assertEqual(provider.drain_events(), [])

    def test_absent_translation_block_reports_cached_live_account_ineligibility(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["bazarr_policy"] = {"ai_translation_enabled": True}
        provider._http_get_json = lambda params: response
        account_calls = []

        def account_status(api_key):
            account_calls.append(api_key)
            return {
                "status": True,
                "pro": {
                    "isTranslationEligible": False,
                    "translationQuota": {
                        "limit": 0,
                        "remaining": 0,
                        "periodEnd": None,
                    },
                },
            }

        provider._http_get_account_json = account_status
        expected = {
            "type": "translation_quota",
            "entitled": False,
            "exhausted": True,
            "remaining": 0,
            "limit": 0,
            "reset_at": None,
        }

        with self.assertLogs("subdl", level="INFO") as logs:
            for title in ("Movie A", "Movie B"):
                results = provider.search(
                    {"kind": "movie", "title": title, "imdb_id": "tt1234567"},
                    [{"alpha3": "fra"}],
                    {"api_key": "secret-test-key", "ai_translate": True},
                )
                self.assertEqual(results, [])
                self.assertEqual(provider.drain_events(), [expected])

        self.assertEqual(account_calls, ["secret-test-key"])
        self.assertEqual(sum("requires Plus or Pro" in line for line in logs.output), 1)
        self.assertNotIn("secret-test-key", "\n".join(logs.output))

    def test_absent_translation_block_with_unknown_account_shape_stays_unknown(self):
        provider = self.mod.SubDLProvider()
        regular = {
            "language": "FR",
            "name": "Movie.fr.srt",
            "url": "/subtitle/movie.srt",
            "subtitlePage": "/en/subtitle/movie",
            "hi": False,
        }
        response = _subdl_response(regular)
        response["bazarr_policy"] = {"ai_translation_enabled": True}
        provider._http_get_json = lambda params: response
        provider._http_get_account_json = lambda api_key: {
            "pro": {
                "isTranslationEligible": "false",
                "translationQuota": {"remaining": "0", "limit": True},
            },
        }

        results = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "secret-test-key", "ai_translate": True},
        )

        self.assertEqual([item["id"] for item in results], ["Movie.fr.srt"])
        self.assertEqual(provider.drain_events(), [])

    def test_account_shape_reports_eligible_quota_without_inventing_candidates(self):
        status = self.mod._account_translation_status({
            "pro": {
                "isTranslationEligible": True,
                "translationQuota": {
                    "limit": 50,
                    "remaining": 37,
                    "periodEnd": "2026-10-01T00:00:00Z",
                },
            },
        })

        self.assertEqual(status, {
            "entitled": True,
            "exhausted": False,
            "remaining": 37,
            "limit": 50,
            "reset_at": "2026-10-01T00:00:00Z",
        })

    def test_account_status_never_caches_or_emits_an_echoed_api_key(self):
        provider = self.mod.SubDLProvider()
        sentinel = "SENTINEL-SUBDL-ACCOUNT-KEY"
        response = _subdl_response()
        response["bazarr_policy"] = {"ai_translation_enabled": True}
        provider._http_get_json = lambda params: response
        provider._http_get_account_json = lambda api_key: {
            "pro": {
                "isTranslationEligible": True,
                "translationQuota": {
                    "limit": 50,
                    "remaining": 37,
                    "periodEnd": sentinel,
                },
            },
        }

        provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": sentinel, "ai_translate": True},
        )

        self.assertNotIn(sentinel, repr(provider.__dict__))
        self.assertNotIn(sentinel, repr(provider.drain_events()))

    def test_account_status_body_uses_absolute_deadline_reader(self):
        provider = self.mod.SubDLProvider()
        now = [0.0]

        class TricklingResponse:
            def __enter__(inner_self):
                return inner_self

            def __exit__(inner_self, exc_type, exc, tb):
                return False

            def read(inner_self, amount=-1):
                raise AssertionError("production HTTP responses must use read1")

            def read1(inner_self, amount=-1):
                del amount
                now[0] += 4.0
                return b"x"

        calls = []

        def urlopen(request, timeout=None):
            calls.append((request, timeout))
            return TricklingResponse()

        with patch.object(self.mod, "ACCOUNT_STATUS_TIMEOUT_SECONDS", 10):
            with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
                with patch.object(self.mod.urllib.request, "urlopen", side_effect=urlopen):
                    with self.assertRaises(TimeoutError):
                        provider._http_get_account_json("secret-test-key")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 10)
        self.assertEqual(now[0], 12.0)

    def test_account_status_header_wait_cannot_block_the_worker_past_deadline(self):
        provider = self.mod.SubDLProvider()
        release = threading.Event()
        entered = threading.Event()

        class Response:
            def __enter__(inner_self):
                return inner_self

            def __exit__(inner_self, exc_type, exc, tb):
                return False

            def read1(inner_self, amount=-1):
                del amount
                return b'{"pro":{"isTranslationEligible":false}}'

        def slow_headers(request, timeout=None):
            del request, timeout
            entered.set()
            release.wait(0.3)
            return Response()

        started = time.perf_counter()
        try:
            with patch.object(self.mod, "ACCOUNT_STATUS_TIMEOUT_SECONDS", 0.05):
                with patch.object(self.mod.urllib.request, "urlopen", side_effect=slow_headers):
                    with self.assertRaises(TimeoutError):
                        provider._http_get_account_json("secret-test-key")
        finally:
            release.set()
            active = getattr(provider, "_account_request_thread", None)
            if active is not None:
                active.join(1)

        self.assertTrue(entered.is_set())
        self.assertLess(time.perf_counter() - started, 0.25)

    def test_private_translation_block_does_not_call_account_endpoint(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["bazarr_policy"] = {"ai_translation_enabled": True}
        response["translation"] = {
            "entitled": False,
            "missing_languages": [],
            "sources": [],
        }
        provider._http_get_json = lambda params: response
        provider._http_get_account_json = lambda api_key: self.fail("unexpected account request")

        provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "secret-test-key", "ai_translate": True},
        )

        self.assertEqual(provider.drain_events()[0]["entitled"], False)

    def test_hi_and_non_hi_candidates_from_same_source_have_unique_ids(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True,
            "missing_languages": ["FR"],
            "sources": [
                {"n_id": 882, "language": "EN", "hi": False, "releases": ["Movie BluRay"]},
                {"n_id": 882, "language": "EN", "hi": True, "releases": ["Movie SDH"]},
            ],
        }
        provider._http_get_json = lambda params: response

        results = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra", "hi": False}, {"alpha3": "fra", "hi": True}],
            {"api_key": "test-key", "ai_translate": True},
        )

        self.assertEqual({item["id"] for item in results},
                         {"ai:882:FR:plain", "ai:882:FR:hi"})
        self.assertEqual({item["language"]["hi"] for item in results}, {False, True})

    def test_episode_candidates_keep_variant_ids_and_context_in_payload(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": 882, "language": "EN", "hi": False}],
        }
        provider._http_get_json = lambda params: response
        candidates = []
        for episode in (3, 4):
            candidates.append(provider.search(
                {"kind": "episode", "series": "Show", "season": 2, "episode": episode,
                 "series_imdb_id": "tt0944947"},
                [{"alpha3": "fra"}],
                {"api_key": "test-key", "ai_translate": True},
            )[0])

        self.assertEqual([item["id"] for item in candidates],
                         ["ai:882:FR:plain", "ai:882:FR:plain"])
        self.assertEqual([item["provider_payload"]["episode"] for item in candidates], [3, 4])
        self.assertEqual([item["provider_payload"]["season"] for item in candidates], [2, 2])

    def test_string_source_id_is_interpolated_literally(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": "opaque:source", "language": "EN", "hi": False}],
        }
        provider._http_get_json = lambda params: response

        result = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {"api_key": "test-key", "ai_translate": True},
        )[0]

        self.assertEqual(result["id"], "ai:opaque:source:FR:plain")

    def test_s02e04_regular_row_without_episode_metadata_does_not_suppress_s02e03_ai(self):
        provider = self.mod.SubDLProvider()
        row = {
            "language": "FR",
            "name": "Show.S02E04.fr.srt",
            "url": "/subtitle/s02e04.srt",
            "subtitlePage": "/en/subtitle/s02e04",
            "hi": False,
        }
        response = _subdl_response(row)
        response["translation"] = {
            "entitled": True,
            "missing_languages": ["FR"],
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        provider._http_get_json = lambda params: response

        results = provider.search(
            {
                "kind": "episode",
                "series": "Show",
                "season": 2,
                "episode": 3,
                "series_imdb_id": "tt0944947",
            },
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )

        regular = next(item for item in results if item["id"] == row["name"])
        translated = next(item for item in results if item["id"] == "ai:771:FR:plain")
        self.assertNotIn("episode", regular["matches"])
        self.assertEqual(translated["provider_payload"]["episode"], 3)

    def test_ai_translation_is_opt_in_and_does_not_emit_quota_event_when_off(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True,
            "missing_languages": ["FR"],
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        provider._http_get_json = lambda params: response

        results = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "test-key"},
        )

        self.assertEqual(results, [])
        self.assertEqual(provider.drain_events(), [])

    def test_published_ai_rows_are_hidden_unless_include_switch_is_enabled(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response({
            "language": "FR",
            "name": "Movie.fr.srt",
            "url": "/subtitle/translated.srt",
            "subtitlePage": "/en/subtitle/translated",
            "author": "SubDL AI",
            "hi": False,
            "ai_translated": True,
        })
        provider._http_get_json = lambda params: response
        video = {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"}
        language = [{"alpha3": "fra"}]

        self.assertEqual(provider.search(video, language, {"api_key": "test-key"}), [])
        included = provider.search(
            video,
            language,
            {"api_key": "test-key", "include_ai_translated": True},
        )
        enabled = provider.search(video, language, {"api_key": "test-key", "ai_translate": True})
        self.assertEqual(len(enabled), 1)
        self.assertIs(enabled[0]["ai_translated"], True)

        self.assertEqual(len(included), 1)
        self.assertIs(included[0]["ai_translated"], True)
        self.assertIn("AI translated", included[0]["display"]["uploader"])

    def test_source_selection_ranks_release_overlap_then_preserves_server_ties(self):
        provider = self.mod.SubDLProvider()
        video = {
            "kind": "movie",
            "title": "Movie",
            "imdb_id": "tt1234567",
            "release_group": "GROUPA",
            "source": "BluRay",
        }
        sources = [
            {"n_id": 1, "language": "EN", "hi": False, "releases": ["Movie GROUPA"]},
            {"n_id": 2, "language": "EN", "hi": False, "releases": ["Movie BluRay GROUPA"]},
            {"n_id": 3, "language": "EN", "hi": False, "releases": ["Movie GROUPA BluRay"]},
        ]
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"], "sources": sources,
        }
        provider._http_get_json = lambda params: response
        translated = provider.search(
            video,
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )

        self.assertEqual(len(translated), 1)
        self.assertEqual(translated[0]["id"], "ai:2:FR:plain")
        self.assertEqual(translated[0]["release_info"], "Movie BluRay GROUPA")

        response["translation"]["sources"] = sources[1:]
        tied = provider.search(
            video,
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )
        self.assertEqual(tied[0]["id"], "ai:2:FR:plain")


    def test_unknown_quota_fields_stay_null_and_remote_policy_disables_candidates(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["bazarr_policy"] = {"ai_translation_enabled": False}
        response["translation"] = {
            "entitled": True,
            "missing_languages": ["FR"],
            "sources": [{"n_id": 12, "language": "EN", "hi": False}],
            "remaining": True,
            "limit": "100",
        }
        provider._http_get_json = lambda params: response

        results = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )

        self.assertEqual(results, [])
        self.assertEqual(provider.drain_events(), [{
            "type": "translation_quota", "entitled": True, "exhausted": False,
            "remaining": None, "limit": None, "reset_at": None,
        }])

    def test_missing_quota_counts_keep_counters_unknown(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": [], "sources": [],
        }
        provider._http_get_json = lambda params: response

        provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )
        self.assertEqual(provider.drain_events(), [{
            "type": "translation_quota", "entitled": True, "exhausted": True,
            "remaining": None, "limit": None, "reset_at": None,
        }])

    def test_forced_targets_are_skipped_and_regional_code_is_preserved(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["BR_PT"],
            "sources": [{"n_id": 55, "language": "EN", "hi": False}],
        }
        provider._http_get_json = lambda params: response
        video = {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"}
        forced = provider.search(
            video, [{"alpha3": "por", "country_alpha2": "BR", "forced": True}],
            {"api_key": "test-key", "ai_translate": True},
        )
        self.assertEqual(forced, [])
        regional = provider.search(
            video, [{"alpha3": "por", "country_alpha2": "BR"}],
            {"api_key": "test-key", "ai_translate": True},
        )
        self.assertEqual(regional[0]["id"], "ai:55:BR_PT:plain")
        self.assertEqual(regional[0]["language"]["country_alpha2"], "BR")

    def test_tmdb_fallback_translation_block_is_used_when_primary_has_none(self):
        provider = self.mod.SubDLProvider()
        fallback = _subdl_response()
        fallback["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": 77, "language": "EN", "hi": False}],
        }
        responses = [{"status": False, "error": "can't find movie or tv"}, fallback]
        calls = []

        def stub(params):
            calls.append(dict(params))
            return responses.pop(0)

        provider._http_get_json = stub
        candidates = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567", "tmdb_id": 27205},
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(candidates[0]["id"], "ai:77:FR:plain")


class SubDLAITranslationAmendedSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _search(self, provider, response):
        provider._http_get_json = lambda params: response
        return provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )

    def test_malformed_translation_blocks_are_rejected_and_warn_once(self):
        malformed = (
            [],
            {"missing_languages": ["FR"], "sources": [{"n_id": 1, "language": "EN", "hi": False}]},
            {"entitled": "true", "missing_languages": ["FR"], "sources": [{"n_id": 1, "language": "EN", "hi": False}]},
            {"entitled": True, "missing_languages": ["FR", 4], "sources": [{"n_id": 1, "language": "EN", "hi": False}]},
            {"entitled": True, "missing_languages": ["FR"], "sources": {}},
        )
        provider = self.mod.SubDLProvider()
        with self.assertLogs("subdl", level="WARNING") as logs:
            for block in malformed:
                response = _subdl_response({
                    "language": "FR", "name": "Movie.fr.srt", "url": "/movie.srt",
                    "subtitlePage": "/en/subtitle/1", "hi": False,
                })
                response["translation"] = block
                results = self._search(provider, response)
                self.assertEqual([item["id"] for item in results], ["Movie.fr.srt"])
                self.assertEqual(provider.drain_events(), [])
        self.assertEqual(len(logs.output), 1)

    def test_malformed_source_entries_are_skipped_and_bad_releases_are_ignored(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True,
            "missing_languages": ["FR"],
            "sources": [
                {"n_id": True, "language": "EN", "hi": False},
                {"n_id": 2, "language": "EN", "hi": "false"},
                {"n_id": 3, "language": "EN", "hi": False, "releases": "not-a-list"},
            ],
        }

        results = self._search(provider, response)

        self.assertEqual([item["id"] for item in results], ["ai:3:FR:plain"])
        self.assertEqual(results[0]["release_info"], "")

    def test_ai_candidate_exception_does_not_discard_regular_subtitles(self):
        provider = self.mod.SubDLProvider()
        response = _subdl_response({
            "language": "FR", "name": "Movie.fr.srt", "url": "/movie.srt",
            "subtitlePage": "/en/subtitle/1", "hi": False,
        })
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": 1, "language": "EN", "hi": False}],
        }
        with patch.object(self.mod, "_build_ai_candidates", side_effect=RuntimeError("private")):
            with self.assertLogs("subdl", level="WARNING") as logs:
                results = self._search(provider, response)

        self.assertEqual([item["id"] for item in results], ["Movie.fr.srt"])
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn("private", "\n".join(logs.output))


class SubDLAITranslationLoggingTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _search(self, provider, response, title):
        provider._http_get_json = lambda params: response
        return provider.search(
            {"kind": "movie", "title": title, "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {"api_key": "test-key", "ai_translate": True},
        )

    def test_not_entitled_log_is_once_per_instance_and_rearmed_by_entitled(self):
        provider = self.mod.SubDLProvider()
        not_entitled = _subdl_response()
        not_entitled["translation"] = {
            "entitled": False, "missing_languages": [], "sources": [],
            "upgrade_url": "https://subdl.com/pro?ref=bazarr",
        }
        entitled = _subdl_response()
        entitled["translation"] = {"entitled": True, "missing_languages": [], "sources": []}

        with self.assertLogs("subdl", level="INFO") as logs:
            self._search(provider, not_entitled, "Movie A")
            self._search(provider, not_entitled, "Movie B")
            self._search(provider, entitled, "Movie C")
            self._search(provider, not_entitled, "Movie D")

        self.assertEqual(sum("requires Plus or Pro" in line for line in logs.output), 2)

    def test_not_entitled_empty_sources_logs_safe_upgrade_only_once(self):
        provider = self.mod.SubDLProvider()
        upgrade_url = "https://www.subdl.com/pro/upgrade?from=account"
        response = _subdl_response()
        response["translation"] = {
            "entitled": False,
            "missing_languages": [],
            "sources": [],
            "upgrade_url": upgrade_url,
        }

        with self.assertLogs("subdl", level="INFO") as logs:
            self._search(provider, response, "Movie A")
            self._search(provider, response, "Movie B")

        self.assertEqual(len(logs.output), 1)
        self.assertIn("requires Plus or Pro", logs.output[0])
        self.assertIn(upgrade_url, logs.output[0])
        self.assertNotIn("quota is exhausted", "\n".join(logs.output))

    def test_empty_sources_log_once_for_each_distinct_reset_date(self):
        provider = self.mod.SubDLProvider()
        responses = []
        for reset in ("2030-01-01", "2030-01-01", "2030-02-01"):
            response = _subdl_response()
            response["translation"] = {
                "entitled": True, "missing_languages": ["FR"], "sources": [],
                "quota_reset_at": reset,
            }
            responses.append(response)

        with self.assertLogs("subdl", level="INFO") as logs:
            for index, response in enumerate(responses):
                self._search(provider, response, f"Movie {index}")

        reset_lines = [line for line in logs.output if "quota is exhausted" in line]
        self.assertEqual(len(reset_lines), 2)
        self.assertIn("2030-01-01", reset_lines[0])
        self.assertIn("2030-02-01", reset_lines[1])

class _FakeTranslationHTTPResponse:
    def __init__(self, body, status=200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    def read(self, amount=-1):
        return self._body if amount < 0 else self._body[:amount]

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SubDLAITranslationDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        self.provider = self.mod.SubDLProvider()
        self.api_key = "translation-test-secret"
        self.config = {"api_key": self.api_key, "ai_translate": True,
                       "ai_translate_timeout_seconds": 60}
        self.payload = {
            "provider": "subdl", "schema": 1, "kind": "ai_translation", "n_id": 771,
            "target_language": "FR", "source_language": "EN", "season": None, "episode": None,
            "absolute_episode": None,
        }

    def _response(self, body, status=200, headers=None):
        return _FakeTranslationHTTPResponse(body, status=status, headers=headers)

    def _patch_urlopen(self, outcomes):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        patcher = patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_post_202_already_ready_fetches_file_once_and_uses_filename_format(self):
        post = self._response(json.dumps({
            "request_id": "job-1", "job": {"status": "published", "download_ready": True},
        }).encode(), status=202)
        file_body = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
        file_response = self._response(
            file_body, headers={"Content-Disposition": 'attachment; filename="translated.vtt"'}
        )
        calls = self._patch_urlopen([post, file_response])

        result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertEqual(len(calls), 2)
        post_request, post_timeout = calls[0]
        self.assertEqual(post_request.get_method(), "POST")
        self.assertTrue(urllib.parse.urlparse(post_request.full_url).path.endswith("/pro/translate/subtitles"))
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlparse(post_request.full_url).query),
                         {"api_key": [self.api_key]})
        self.assertEqual(json.loads(post_request.data), {"n_id": 771, "target_language": "FR"})
        self.assertGreater(post_timeout, 0)
        self.assertLessEqual(post_timeout, 30)
        self.assertTrue(urllib.parse.urlparse(calls[1][0].full_url).path.endswith("/jobs/job-1/download"))
        self.assertEqual(result["format"], "vtt")
        self.assertEqual(base64.b64decode(result["content_b64"]), file_body)
        self.assertNotIn(self.api_key, repr(result))

    def test_episode_post_includes_season_and_episode_and_queued_job_polls_until_reused(self):
        self.payload.update({"season": 2, "episode": 3, "absolute_episode": 15})
        outcomes = [
            self._response(json.dumps({"request_id": "episode-job", "job": {
                "status": "queued", "download_ready": False, "estimated_duration_ms": 4000,
            }}).encode(), status=202),
            self._response(json.dumps({"job": {"status": "running", "download_ready": False}}).encode()),
            self._response(json.dumps({"job": {"status": "publishing", "download_ready": False}}).encode()),
            self._response(json.dumps({"job": {"status": "translated", "download_ready": False}}).encode()),
            self._response(json.dumps({"job": {"status": "reused", "download_ready": True}}).encode()),
            self._response(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"),
        ]
        calls = self._patch_urlopen(outcomes)
        now = [10.0]
        sleeps = []
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: (sleeps.append(delay), now.__setitem__(0, now[0] + delay))):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertEqual(len(calls), 6)
        self.assertEqual(json.loads(calls[0][0].data), {
            "n_id": 771, "target_language": "FR", "season": 2, "episode": 3,
        })
        self.assertEqual([urllib.parse.urlparse(item[0].full_url).path.rsplit("/", 1)[-1]
                          for item in calls[1:5]], ["episode-job"] * 4)
        self.assertEqual(sleeps, [4, 4, 4, 4])
        self.assertEqual(result["format"], "srt")

    def test_post_status_tokens_are_single_attempt_local_failures(self):
        import urllib.error

        cases = (
            (402, "translation_not_entitled", False, None),
            (429, "translation_quota_exhausted", None, True),
            (503, "provider_busy", None, None),
        )
        for status, token, entitled, exhausted in cases:
            with self.subTest(status=status):
                provider = self.mod.SubDLProvider()
                body = json.dumps({"error": token}).encode()
                error = urllib.error.HTTPError(
                    "https://api.subdl.com/api/v1/pro/translate/subtitles", status, "error", {}, io.BytesIO(body)
                )
                calls = []

                def fail_once(request, timeout=None):
                    calls.append((request, timeout))
                    raise error

                with patch.object(self.mod.urllib.request, "urlopen", side_effect=fail_once):
                    result = provider.download(self.payload, {"alpha3": "fra"}, self.config)

                self.assertIsNone(result)
                self.assertEqual(len(calls), 1)
                event = provider.drain_events()
                if entitled is not None or exhausted is not None:
                    self.assertEqual(len(event), 1)
                    self.assertIs(event[0]["entitled"], entitled)
                    self.assertIs(event[0]["exhausted"], exhausted)
                    self.assertNotIn(self.api_key, repr(event))
                else:
                    self.assertEqual(event, [])

    def test_trickling_quota_http_error_body_stays_within_deadline_and_blocks_locally(self):
        import urllib.error

        now = [0.0]
        body_parts = (b'{"error":', b'"translation_quota_exhausted"}')

        class TricklingQuotaHTTPError(urllib.error.HTTPError):
            def __init__(inner_self):
                super().__init__(
                    "https://api.subdl.com/api/v1/pro/translate/subtitles",
                    429,
                    "quota",
                    {},
                    io.BytesIO(),
                )
                inner_self.read1_calls = 0
                inner_self.unbounded_read_calls = 0

            def read1(inner_self, amount=-1):
                del amount
                inner_self.read1_calls += 1
                if inner_self.read1_calls <= len(body_parts):
                    now[0] += 29.99
                    return body_parts[inner_self.read1_calls - 1]
                return b""

            def read(inner_self, amount=-1):
                inner_self.unbounded_read_calls += 1
                now[0] = 61.0
                body = b'{"error":"translation_quota_exhausted"}'
                return body if amount < 0 else body[:amount]

        error = TricklingQuotaHTTPError()
        calls = []

        def submit_once(request, timeout=None):
            calls.append((request, timeout))
            raise error

        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.urllib.request, "urlopen", side_effect=submit_once):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

            self.assertIsNone(result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0].get_method(), "POST")
            self.assertLess(now[0], 60.0)
            self.assertEqual(error.read1_calls, 3)
            self.assertEqual(error.unbounded_read_calls, 0)
            self.assertTrue(error.fp.closed)
            self.assertEqual(self.provider._quota_blocked_until, now[0] + 900.0)
            event = self.provider.drain_events()
            self.assertEqual(len(event), 1)
            self.assertIs(event[0]["exhausted"], True)

            response = _subdl_response()
            response["translation"] = {
                "entitled": True,
                "missing_languages": ["FR"],
                "sources": [{"n_id": 771, "language": "EN", "hi": False}],
            }
            self.provider._http_get_json = lambda params: response
            results = self.provider.search(
                {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
                [{"alpha3": "fra"}],
                {"api_key": self.api_key, "ai_translate": True},
            )
            self.assertEqual(results, [])
            self.assertIs(self.provider.drain_events()[0]["exhausted"], True)

    def test_wrapped_http_error_tightens_socket_timeout_and_classifies_quota(self):
        import urllib.error

        now = [0.0]
        timeouts = []
        body_parts = (b'{"error":', b'"translation_quota_exhausted"}')

        class RecordingSocket:
            def settimeout(inner_self, timeout):
                timeouts.append(timeout)

        class RawReader:
            def __init__(inner_self):
                inner_self._sock = RecordingSocket()

        class BufferedReader:
            def __init__(inner_self):
                inner_self.raw = RawReader()

        class WrappedHTTPResponse:
            def __init__(inner_self):
                inner_self.fp = BufferedReader()
                inner_self.closed = False

            def close(inner_self):
                inner_self.closed = True

        class TricklingQuotaHTTPError(urllib.error.HTTPError):
            def __init__(inner_self):
                super().__init__(
                    "https://api.subdl.com/api/v1/pro/translate/subtitles",
                    429,
                    "quota",
                    {},
                    WrappedHTTPResponse(),
                )
                inner_self.read1_calls = 0
                inner_self.unbounded_read_calls = 0

            def read1(inner_self, amount=-1):
                del amount
                inner_self.read1_calls += 1
                if inner_self.read1_calls <= len(body_parts):
                    now[0] += 29.0
                    return body_parts[inner_self.read1_calls - 1]
                if timeouts and timeouts[-1] <= 2.0:
                    return b""
                now[0] += 29.0
                return b""

            def read(inner_self, amount=-1):
                inner_self.unbounded_read_calls += 1
                now[0] = 87.0
                body = b'{"error":"translation_quota_exhausted"}'
                return body if amount < 0 else body[:amount]

        error = TricklingQuotaHTTPError()
        calls = []

        def submit_once(request, timeout=None):
            calls.append((request, timeout))
            raise error

        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.urllib.request, "urlopen", side_effect=submit_once):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

            self.assertIsNone(result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0].get_method(), "POST")
            self.assertLess(now[0], 60.0)
            self.assertEqual(timeouts, [60.0, 31.0, 2.0])
            self.assertEqual(error.read1_calls, 3)
            self.assertEqual(error.unbounded_read_calls, 0)
            self.assertTrue(error.fp.closed)
            self.assertEqual(self.provider._quota_blocked_until, now[0] + 900.0)
            self.assertEqual(self.provider._translation_uncertainty, {})
            self.assertIs(self.provider.drain_events()[0]["exhausted"], True)

            response = _subdl_response()
            response["translation"] = {
                "entitled": True,
                "missing_languages": ["FR"],
                "sources": [{"n_id": 771, "language": "EN", "hi": False}],
            }
            self.provider._http_get_json = lambda params: response
            results = self.provider.search(
                {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
                [{"alpha3": "fra"}],
                {"api_key": self.api_key, "ai_translate": True},
            )
            self.assertEqual(results, [])
            self.assertIs(self.provider.drain_events()[0]["exhausted"], True)

    def test_real_httpresponse_wrapped_error_tightens_timeout_and_classifies_quota(self):
        import http.client
        import socket
        import urllib.error

        now = [0.0]
        timeouts = []
        body_parts = (b'{"error":', b'"translation_quota_exhausted"}')
        client_socket, peer_socket = socket.socketpair()
        self.addCleanup(client_socket.close)
        self.addCleanup(peer_socket.close)
        wrapped_response = http.client.HTTPResponse(client_socket)

        class TricklingQuotaHTTPError(urllib.error.HTTPError):
            def __init__(inner_self):
                super().__init__(
                    "https://api.subdl.com/api/v1/pro/translate/subtitles",
                    429,
                    "quota",
                    {},
                    wrapped_response,
                )
                inner_self.read1_calls = 0
                inner_self.unbounded_read_calls = 0

            def read1(inner_self, amount=-1):
                del amount
                inner_self.read1_calls += 1
                remaining_timeout = client_socket.gettimeout()
                timeouts.append(remaining_timeout)
                if remaining_timeout is not None and remaining_timeout <= 2.0:
                    return b""
                if inner_self.read1_calls <= len(body_parts):
                    now[0] += 29.0
                    return body_parts[inner_self.read1_calls - 1]
                now[0] += 29.0
                return b""

            def read(inner_self, amount=-1):
                inner_self.unbounded_read_calls += 1
                now[0] = 87.0
                body = b'{"error":"translation_quota_exhausted"}'
                return body if amount < 0 else body[:amount]

        error = TricklingQuotaHTTPError()
        calls = []

        def submit_once(request, timeout=None):
            calls.append((request, timeout))
            raise error

        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.urllib.request, "urlopen", side_effect=submit_once):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

            self.assertIsNone(result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0].get_method(), "POST")
            self.assertLess(now[0], 60.0)
            self.assertEqual(timeouts, [60.0, 31.0, 2.0])
            self.assertEqual(error.read1_calls, 3)
            self.assertEqual(error.unbounded_read_calls, 0)
            self.assertIsNone(wrapped_response.fp)
            self.assertEqual(self.provider._quota_blocked_until, now[0] + 900.0)
            self.assertEqual(self.provider._translation_uncertainty, {})
            self.assertIs(self.provider.drain_events()[0]["exhausted"], True)

            response = _subdl_response()
            response["translation"] = {
                "entitled": True,
                "missing_languages": ["FR"],
                "sources": [{"n_id": 771, "language": "EN", "hi": False}],
            }
            self.provider._http_get_json = lambda params: response
            results = self.provider.search(
                {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
                [{"alpha3": "fra"}],
                {"api_key": self.api_key, "ai_translate": True},
            )
            self.assertEqual(results, [])
            self.assertIs(self.provider.drain_events()[0]["exhausted"], True)

    def test_post_connection_error_is_not_retried_and_never_raises(self):
        import urllib.error

        calls = self._patch_urlopen([urllib.error.URLError("private connection detail")])
        with self.assertLogs("subdl", level="WARNING") as captured:
            result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(self.api_key, "\n".join(captured.output))
        self.assertNotIn("private connection detail", "\n".join(captured.output))

    def test_five_poll_failures_stop_and_failed_job_returns_none(self):
        import urllib.error

        queued = self._response(json.dumps({"request_id": "poll-job", "job": {
            "status": "queued", "download_ready": False, "estimated_seconds": 1,
        }}).encode(), status=202)
        failures = [urllib.error.URLError("hidden") for _ in range(5)]
        calls = self._patch_urlopen([queued, *failures])
        now = [0.0]
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 6)
        self.assertEqual(sum(request.get_method() == "POST" for request, _ in calls), 1)

        failed = self._response(json.dumps({"request_id": "failed-job", "job": {
            "status": "failed", "download_ready": False,
        }}).encode(), status=200)
        calls = self._patch_urlopen([failed])
        self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(len(calls), 1)

    def test_deadline_and_invalid_download_bodies_return_none_without_exceeding_budget(self):
        queued = self._response(json.dumps({"request_id": "slow-job", "job": {
            "status": "running", "download_ready": False, "estimated_seconds": 1000,
        }}).encode(), status=202)
        running = self._response(json.dumps({"job": {
            "status": "running", "download_ready": False,
        }}).encode())
        calls = self._patch_urlopen([queued, *([running] * 8)])
        now = [0.0]
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)
        self.assertIsNone(result)
        self.assertLessEqual(now[0], 60)
        self.assertEqual(len(calls), 8)

        for body in (b"", b"<!doctype html><html><body>error</body></html>"):
            with self.subTest(body=body[:8]):
                ready = self._response(json.dumps({"request_id": "bad-file", "job": {
                    "status": "published", "download_ready": True,
                }}).encode(), status=200)
                file_response = self._response(body)
                calls = self._patch_urlopen([ready, file_response])
                self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
                self.assertEqual(len(calls), 2)

    def test_ai_download_switch_off_returns_before_any_request(self):
        calls = self._patch_urlopen([])
        config = {**self.config, "ai_translate": False}

        self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, config))
        self.assertEqual(calls, [])

    def test_download_retries_idempotent_file_fetch_and_uses_content_type_fallback(self):
        import urllib.error

        ready = self._response(json.dumps({"request_id": "retry-file", "job": {
            "status": "published", "download_ready": True,
        }}).encode())
        retryable = urllib.error.HTTPError(
            "https://api.subdl.com/jobs/retry-file/download", 503, "busy", {}, io.BytesIO(b"busy"),
        )
        body = b"1\\n00:00:01,000 --> 00:00:02,000\\nHello\\n"
        file_response = self._response(body, headers={"Content-Type": "application/x-subrip; charset=utf-8"})
        calls = self._patch_urlopen([ready, retryable, file_response])
        with patch.object(self.mod.time, "sleep"):
            result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertEqual([request.get_method() for request, _ in calls], ["POST", "GET", "GET"])
        self.assertEqual(calls[1][0].full_url, calls[2][0].full_url)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(base64.b64decode(result["content_b64"]), body)

    def test_application_json_error_download_body_is_rejected(self):
        ready = self._response(json.dumps({"request_id": "json-error", "job": {
            "status": "published", "download_ready": True,
        }}).encode())
        error_file = self._response(
            b'{"error":"translation_quota_exhausted"}',
            headers={"Content-Type": "application/json"},
        )
        calls = self._patch_urlopen([ready, error_file])

        result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)

    def test_trickling_file_read_stops_at_operation_deadline(self):
        now = [0.0]

        class TricklingResponse(_FakeTranslationHTTPResponse):
            def read(inner_self, amount=-1):
                raise AssertionError("production HTTP responses must use read1")

            def read1(inner_self, amount=-1):
                del amount
                now[0] += 10.0
                return b"subtitle chunk"

        ready = self._response(json.dumps({"request_id": "slow-read", "job": {
            "status": "published", "download_ready": True,
        }}).encode())
        file_response = TricklingResponse(b"")
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            return ready if len(calls) == 1 else file_response

        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(now[0], 60.0)
        self.assertEqual(calls[-1][1], 30)

    def test_translation_file_larger_than_bound_is_rejected(self):
        ready = self._response(json.dumps({"request_id": "too-large", "job": {
            "status": "published", "download_ready": True,
        }}).encode())
        file_response = self._response(
            b"123456789",
            headers={"Content-Type": "application/x-subrip"},
        )
        calls = self._patch_urlopen([ready, file_response])

        with patch.object(self.mod, "TRANSLATION_FILE_MAX_BYTES", 8):
            result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNone(result)
        self.assertEqual(len(calls), 2)

    def test_job_ready_at_sixty_second_poll_boundary_downloads_with_full_file_timeout(self):
        queued = self._response(json.dumps({"request_id": "boundary-60", "job": {
            "status": "queued", "download_ready": False, "estimated_seconds": 1,
        }}).encode(), status=202)
        running = self._response(json.dumps({"job": {
            "status": "running", "download_ready": False,
        }}).encode())
        ready = self._response(json.dumps({"job": {
            "status": "published", "download_ready": True,
        }}).encode())
        file_response = self._response(b"1\n00:00:01,000 --> 00:00:02,000\nSubtitle\n")
        outcomes = [queued, *([running] * 6), ready, file_response]
        now = [0.0]
        poll_count = [0]
        calls = []

        def fake_urlopen(request, timeout=None):
            path = urllib.parse.urlparse(request.full_url).path
            calls.append((request, timeout, now[0]))
            outcome = outcomes.pop(0)
            if request.get_method() == "GET" and not path.endswith("/download"):
                poll_count[0] += 1
                if poll_count[0] == 7:
                    now[0] = 30.0
            return outcome

        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
                with patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen):
                    result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNotNone(result)
        polls = calls[1:-1]
        self.assertEqual(len(polls), 7)
        self.assertEqual(polls[-1][2], 25.0)
        self.assertEqual(polls[-1][1], 5)
        self.assertEqual(calls[-1][2], 30.0)
        self.assertEqual(calls[-1][1], 30)

    def test_poll_reserves_at_least_thirty_seconds_for_file_download(self):
        self.config["ai_translate_timeout_seconds"] = 120
        queued = self._response(json.dumps({"request_id": "boundary", "estimated_duration_ms": 999999,
            "job": {"status": "queued", "download_ready": False}}).encode(), status=202)
        running = self._response(json.dumps({"job": {"status": "running", "download_ready": False}}).encode())
        ready = self._response(json.dumps({"job": {"status": "published", "download_ready": True}}).encode())
        file_response = self._response(b"subtitle")
        calls = self._patch_urlopen([queued, *([running] * 21), ready, file_response])
        now = [0.0]
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNotNone(result)
        poll_calls = calls[1:-1]
        self.assertEqual(len(poll_calls), 22)
        self.assertEqual(poll_calls[-1][1], 5)
        self.assertEqual(calls[-1][1], 30)

class SubDLAITranslationLifecycleTests(unittest.TestCase):

    def test_translation_quota_failure_does_not_throttle_ordinary_subdl_search(self):
        import urllib.error

        error = urllib.error.HTTPError(
            "https://api.subdl.com/translation", 429, "quota", {},
            io.BytesIO(b'{"error":"translation_quota_exhausted"}'),
        )
        self._patch_urlopen([error])
        self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))

        item = {
            "language": "FR", "name": "Movie.fr.srt", "url": "/subtitle/movie.srt",
            "subtitlePage": "/en/subtitle/movie", "hi": False,
        }
        self.provider._http_get_json = lambda params: _subdl_response(item)
        results = self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {"api_key": self.key, "ai_translate": False},
        )

        self.assertEqual([item["id"] for item in results], ["Movie.fr.srt"])
    def setUp(self):
        self.mod = _load_provider_module()
        self.provider = self.mod.SubDLProvider()
        self.key = "lifecycle-secret-key"
        self.config = {"api_key": self.key, "ai_translate": True,
                       "ai_translate_timeout_seconds": "60"}
        self.payload = {
            "provider": "subdl", "schema": 1, "kind": "ai_translation", "n_id": 771,
            "target_language": "FR", "season": None, "episode": None,
        }

    def _response(self, data, status=200):
        return _FakeTranslationHTTPResponse(json.dumps(data).encode(), status=status)

    def _patch_urlopen(self, outcomes):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        patcher = patch.object(self.mod.urllib.request, "urlopen", side_effect=fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def _search(self):
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        self.provider._http_get_json = lambda params: response
        return self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}],
            {**self.config, "ai_translate": True},
        )

    def test_deadline_then_repeat_download_polls_remembered_job_without_post(self):
        queued = self._response({"request_id": "remember-me", "estimated_duration_ms": 120000,
                                 "job": {"status": "queued", "download_ready": False}}, status=202)
        running = self._response({"job": {"status": "running", "download_ready": False}})
        first_calls = self._patch_urlopen([queued, *([running] * 8)])
        now = [0.0]
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
                self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
                self.assertLessEqual(now[0], 60)
                self.assertEqual(sum(request.get_method() == "POST" for request, _ in first_calls), 1)

                second_calls = self._patch_urlopen([
                    self._response({"job": {"status": "published", "download_ready": True}}),
                    _FakeTranslationHTTPResponse(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"),
                ])
                result = self.provider.download(self.payload, {"alpha3": "fra"}, self.config)

        self.assertIsNotNone(result)
        self.assertTrue(all(request.get_method() == "GET" for request, _ in second_calls))
        self.assertTrue(all("remember-me" in request.full_url for request, _ in second_calls))

    def test_poll_404_forgets_job_and_next_download_may_submit(self):
        import urllib.error

        queued = self._response({"request_id": "gone", "job": {"status": "queued"}}, status=202)
        missing = urllib.error.HTTPError("https://api.subdl.com/job/gone", 404, "missing", {}, io.BytesIO(b""))
        first_calls = self._patch_urlopen([queued, missing])
        with patch.object(self.mod.time, "sleep"):
            self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(len(first_calls), 2)

        second_calls = self._patch_urlopen([
            self._response({"request_id": "new-job", "job": {"status": "published", "download_ready": True}}),
            _FakeTranslationHTTPResponse(b"subtitle"),
        ])
        self.assertIsNotNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(second_calls[0][0].get_method(), "POST")

    def test_uncertain_submit_suppresses_candidate_and_download_until_ttl(self):
        import urllib.error

        error = urllib.error.HTTPError(
            "https://api.subdl.com/translation", 503, "busy", {},
            io.BytesIO(b'{"error":"provider_busy"}'),
        )
        calls = self._patch_urlopen([error])
        now = [0.0]
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
            self.assertEqual(len(calls), 1)
            self.assertEqual(self._search(), [])
            later_calls = self._patch_urlopen([])
            self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
            self.assertEqual(later_calls, [])
            now[0] = 86401.0
            self.assertEqual(len(self._search()), 1)

    def test_refused_before_send_does_not_mark_job_uncertain(self):
        import urllib.error

        first_calls = self._patch_urlopen([urllib.error.URLError(ConnectionRefusedError("refused"))])
        self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(len(self._search()), 1)

        second_calls = self._patch_urlopen([
            self._response({"request_id": "accepted", "job": {"status": "published", "download_ready": True}}),
            _FakeTranslationHTTPResponse(b"subtitle"),
        ])
        self.assertIsNotNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(second_calls[0][0].get_method(), "POST")

    def test_successful_submit_decrements_known_quota_after_drain_and_switch_off_clears(self):
        search_response = _subdl_response()
        search_response["translation"] = {
            "entitled": True, "missing_languages": ["FR"], "remaining": 1, "limit": 2,
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        self.provider._http_get_json = lambda params: search_response
        self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {**self.config, "ai_translate": True},
        )
        self.assertEqual(self.provider.drain_events()[0]["remaining"], 1)

        calls = self._patch_urlopen([
            self._response({"request_id": "spent", "job": {"status": "published", "download_ready": True}}),
            _FakeTranslationHTTPResponse(b"subtitle"),
        ])
        self.assertIsNotNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(sum(request.get_method() == "POST" for request, _ in calls), 1)
        event = self.provider.drain_events()[0]
        self.assertEqual(event["remaining"], 0)
        self.assertIs(event["exhausted"], True)
        self.assertEqual(self.provider.drain_events(), [])

        self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {**self.config, "ai_translate": False},
        )
        self.assertIsNone(self.provider._quota_status)
        self.assertEqual(self.provider.drain_events(), [])

    def test_uncertainty_suppresses_same_episode_but_not_another_episode(self):
        import urllib.error

        self.payload.update({"season": 2, "episode": 3})
        error = urllib.error.HTTPError(
            "https://api.subdl.com/translation", 503, "busy", {}, io.BytesIO(b'{"error":"busy"}'),
        )
        self._patch_urlopen([error])
        self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))

        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        self.provider._http_get_json = lambda params: response

        def search_episode(episode):
            return self.provider.search(
                {"kind": "episode", "series": "Show", "season": 2, "episode": episode,
                 "series_imdb_id": "tt0944947"},
                [{"alpha3": "fra"}], {**self.config, "ai_translate": True},
            )

        self.assertEqual(search_episode(3), [])
        other_episode = search_episode(4)
        self.assertEqual(other_episode[0]["id"], "ai:771:FR:plain")
        self.assertEqual(other_episode[0]["provider_payload"]["episode"], 4)

    def test_uncertain_submit_clears_untrusted_quota_counts(self):
        import urllib.error

        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"], "remaining": 4, "limit": 8,
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        self.provider._http_get_json = lambda params: response
        self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {**self.config, "ai_translate": True},
        )
        self.provider.drain_events()

        error = urllib.error.HTTPError(
            "https://api.subdl.com/translation", 503, "busy", {}, io.BytesIO(b'{"error":"busy"}'),
        )
        self._patch_urlopen([error])
        self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(self.provider.drain_events(), [{
            "type": "translation_quota", "entitled": True, "exhausted": None,
            "remaining": None, "limit": 8, "reset_at": None,
        }])

    def test_unknown_count_reports_unknown_exhaustion_and_reused_emits_nothing(self):
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"],
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        self.provider._http_get_json = lambda params: response
        self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {**self.config, "ai_translate": True},
        )
        self.provider.drain_events()
        successful = self._response({"request_id": "unknown-count", "job": {
            "status": "published", "download_ready": True,
        }})
        self._patch_urlopen([successful, _FakeTranslationHTTPResponse(b"subtitle")])
        self.assertIsNotNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(self.provider.drain_events(), [{
            "type": "translation_quota", "entitled": True, "exhausted": None,
            "remaining": None, "limit": None, "reset_at": None,
        }])

        known = _subdl_response()
        known["translation"] = {
            "entitled": True, "missing_languages": ["FR"], "remaining": 3,
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        self.provider._http_get_json = lambda params: known
        self.provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {**self.config, "ai_translate": True},
        )
        self.provider.drain_events()
        reused = self._response({"request_id": "reused", "reused": True, "job": {
            "status": "reused", "download_ready": True,
        }})
        self._patch_urlopen([reused, _FakeTranslationHTTPResponse(b"subtitle")])
        self.assertIsNotNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
        self.assertEqual(self.provider._quota_status["remaining"], 3)
        self.assertEqual(self.provider.drain_events(), [])

    def test_secret_sentinel_is_absent_from_candidates_events_state_and_logs(self):
        import urllib.error

        sentinel = "SENTINEL-SUBDL-API-KEY-DO-NOT-LEAK"
        provider = self.mod.SubDLProvider()
        response = _subdl_response()
        response["translation"] = {
            "entitled": True, "missing_languages": ["FR"], "quota_reset_at": sentinel,
            "sources": [{"n_id": 771, "language": "EN", "hi": False}],
        }
        provider._http_get_json = lambda params: response
        candidates = provider.search(
            {"kind": "movie", "title": "Movie", "imdb_id": "tt1234567"},
            [{"alpha3": "fra"}], {"api_key": sentinel, "ai_translate": True},
        )
        events = provider.drain_events()
        error = urllib.error.HTTPError(
            "https://api.subdl.com/translation", 429, "error", {},
            io.BytesIO(json.dumps({"error": sentinel}).encode()),
        )
        with patch.object(self.mod.urllib.request, "urlopen", side_effect=error):
            with self.assertLogs("subdl", level="WARNING") as logs:
                provider.download(self.payload, {"alpha3": "fra"},
                                  {"api_key": sentinel, "ai_translate": True})
        for value in (candidates, events, provider.__dict__, logs.output):
            self.assertNotIn(sentinel, repr(value))

    def test_legacy_candidate_shape_retains_host_fields_and_exact_ai_flag(self):
        candidate = self._search()[0]
        for field in (
            "provider", "id", "language", "release_info", "filename", "matches", "score",
            "score_without_hash", "score_out_of", "hash_verifiable",
            "hearing_impaired_verifiable", "hearing_impaired", "display", "provider_payload",
        ):
            self.assertIn(field, candidate)
        self.assertIs(candidate["ai_translated"], True)
        self.assertNotIn("machine_translated", candidate)
    def test_timeout_unreadable_success_and_unknown_error_mark_uncertainty_without_retry(self):
        import urllib.error

        ambiguous = (
            self.mod.socket.timeout("submission timed out"),
            _FakeTranslationHTTPResponse(b"not-json", status=202),
            urllib.error.HTTPError(
                "https://api.subdl.com/translation", 418, "unknown", {},
                io.BytesIO(b'{"error":"new_error_token"}'),
            ),
        )
        for index, outcome in enumerate(ambiguous):
            with self.subTest(index=index):
                provider = self.mod.SubDLProvider()
                payload = dict(self.payload)
                calls = []

                def submit_once(request, timeout=None):
                    calls.append((request, timeout))
                    raise outcome if isinstance(outcome, Exception) else AssertionError("unexpected response")

                if not isinstance(outcome, Exception):
                    def submit_once(request, timeout=None):
                        calls.append((request, timeout))
                        return outcome

                with patch.object(self.mod.urllib.request, "urlopen", side_effect=submit_once):
                    result = provider.download(payload, {"alpha3": "fra"}, self.config)
                self.assertIsNone(result)
                self.assertEqual(len(calls), 1)

                retry_calls = []
                def unexpected_retry(request, timeout=None):
                    retry_calls.append((request, timeout))
                    raise AssertionError("uncertain submit must not be repeated")

                with patch.object(self.mod.urllib.request, "urlopen", side_effect=unexpected_retry):
                    self.assertIsNone(provider.download(payload, {"alpha3": "fra"}, self.config))
                self.assertEqual(retry_calls, [])

    def test_remembered_job_expires_after_24_hours_and_state_stays_bounded(self):
        queued = self._response({"request_id": "old-job", "job": {
            "status": "queued", "download_ready": False,
        }}, status=202)
        running = self._response({"job": {"status": "running", "download_ready": False}})
        first_calls = self._patch_urlopen([queued, *([running] * 8)])
        now = [0.0]
        with patch.object(self.mod.time, "monotonic", side_effect=lambda: now[0]):
            with patch.object(self.mod.time, "sleep", side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
                self.assertIsNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))
                self.assertEqual(first_calls[0][0].get_method(), "POST")
                now[0] = 86401.0
                second_calls = self._patch_urlopen([
                    self._response({"request_id": "new-job", "job": {
                        "status": "published", "download_ready": True,
                    }}),
                    _FakeTranslationHTTPResponse(b"subtitle"),
                ])
                self.assertIsNotNone(self.provider.download(self.payload, {"alpha3": "fra"}, self.config))

        self.assertEqual(second_calls[0][0].get_method(), "POST")
        self.assertEqual(len(self.provider._translation_jobs), 1)
        for index in range(300):
            self.provider._remember_translation_job((str(index), "FR", None, None), f"job-{index}", 90000)
        self.assertLessEqual(len(self.provider._translation_jobs), self.mod.TRANSLATION_STATE_MAX_ITEMS)
        self.assertNotIn(("0", "FR", None, None), self.provider._translation_jobs)
if __name__ == "__main__":
    unittest.main()
