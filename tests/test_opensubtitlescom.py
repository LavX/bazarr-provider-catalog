import base64
import hashlib
import importlib.util
import io
import json
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "opensubtitlescom"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "opensubtitlescom_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _subtitle_item(
    subtitle_id="10139516",
    file_id=11047023,
    language="en",
    foreign_parts_only=False,
    hearing_impaired=False,
    ai_translated=False,
    machine_translated=False,
    moviehash_match=False,
    feature_type="Episode",
):
    return {
        "id": subtitle_id,
        "type": "subtitle",
        "attributes": {
            "subtitle_id": subtitle_id,
            "language": language,
            "download_count": 1250,
            "ratings": 8.5,
            "hearing_impaired": hearing_impaired,
            "fps": 23.976,
            "from_trusted": True,
            "foreign_parts_only": foreign_parts_only,
            "ai_translated": ai_translated,
            "machine_translated": machine_translated,
            "moviehash_match": moviehash_match,
            "upload_date": "2025-05-02T19:54:42Z",
            "release": "Breaking.Bad.S03E13.1080p.WEB-DL-GROUP",
            "uploader": {"name": "uploader"},
            "feature_details": {
                "feature_type": feature_type,
                "year": 2010,
                "movie_name": "Breaking Bad - S03E13 Full Measure",
                "imdb_id": 1628687,
                "season_number": 3,
                "episode_number": 13,
                "parent_imdb_id": 903747,
                "parent_title": "Breaking Bad",
            },
            "url": "https://www.opensubtitles.com/en/subtitles/10139516",
            "files": [
                {
                    "file_id": file_id,
                    "cd_number": 1,
                    "file_name": "Breaking.Bad.S03E13.1080p.WEB-DL-GROUP.srt",
                }
            ],
        },
    }


class OpenSubtitlesComHelperTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_sanitize_external_id_removes_tt_and_leading_zeroes(self):
        self.assertEqual(self.mod.sanitize_external_id("tt0903747"), 903747)
        self.assertEqual(self.mod.sanitize_external_id("000539911"), 539911)

    def test_api_language_code_preserves_legacy_custom_languages(self):
        self.assertEqual(self.mod.api_language_code({"alpha3": "por", "alpha2": "pt"}), "pt-PT")
        self.assertEqual(
            self.mod.api_language_code({"alpha3": "por", "alpha2": "pt", "country": "BR"}),
            "pt-BR",
        )
        self.assertEqual(self.mod.api_language_code({"alpha3": "zho", "alpha2": "zh"}), "zh-CN")
        self.assertEqual(
            self.mod.api_language_code({"alpha3": "spa", "alpha2": "es", "country": "MX"}),
            "ea",
        )
        self.assertEqual(
            self.mod.api_language_code({"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}),
            "pt-BR",
        )

    def test_api_language_code_accepts_catalog_regional_language_ids(self):
        self.assertEqual(self.mod.api_language_code({"alpha3": "por-BR"}), "pt-BR")
        self.assertEqual(self.mod.api_language_code({"alpha3": "spa-MX"}), "ea")
        self.assertEqual(self.mod.api_language_code({"alpha3": "srp-ME"}), "me")

    def test_api_language_code_matches_current_official_language_table(self):
        cases = {
            "abk": "ab",
            "amh": "am",
            "asm": "as",
            "aze": "az-az",
            "cym": "cy",
            "gle": "ga",
            "gla": "gd",
            "ibo": "ig",
            "ina": "ia",
            "kan": "kn",
            "kur": "ku",
            "mar": "mr",
            "nav": "nv",
            "nep": "ne",
            "ori": "or",
            "pus": "ps",
            "sat": "sx",
            "snd": "sd",
            "som": "so",
            "tat": "tt",
            "tet": "tm-td",
            "tuk": "tk",
        }
        for alpha3, api_code in cases.items():
            with self.subTest(alpha3=alpha3):
                self.assertEqual(self.mod.api_language_code({"alpha3": alpha3}), api_code)

    def test_language_payload_from_api_code_restores_custom_language(self):
        self.assertEqual(self.mod.language_payload_from_api_code("ea")["country"], "MX")
        self.assertEqual(self.mod.language_payload_from_api_code("pt-BR")["country"], "BR")
        self.assertEqual(self.mod.language_payload_from_api_code("me")["country"], "ME")
        self.assertEqual(self.mod.language_payload_from_api_code("en")["alpha3"], "eng")
        self.assertEqual(self.mod.language_payload_from_api_code("az-az")["alpha3"], "aze")
        self.assertEqual(self.mod.language_payload_from_api_code("tm-td")["alpha3"], "tet")

    def test_manifest_languages_are_limited_to_api_supported_codes(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["languages"], sorted(self.mod.ALPHA3_TO_API))

    def test_custom_api_codes_map_alpha2_to_base_language(self):
        mexican = self.mod.language_payload_from_api_code("ea")
        self.assertEqual(mexican["alpha3"], "spa")
        self.assertEqual(mexican["alpha2"], "es")

        montenegrin = self.mod.language_payload_from_api_code("me")
        self.assertEqual(montenegrin["alpha3"], "srp")
        self.assertEqual(montenegrin["alpha2"], "sr")

        bilingual_chinese = self.mod.language_payload_from_api_code("ze")
        self.assertEqual(bilingual_chinese["alpha3"], "zho")
        self.assertEqual(bilingual_chinese["alpha2"], "zh")

    def test_legacy_opensubtitles_hash_key_is_used(self):
        self.assertEqual(
            self.mod._moviehash({"hashes": {"opensubtitles": "8e245d9679d31e12"}}),
            "8e245d9679d31e12",
        )

    def test_year_match_requires_a_real_year(self):
        matches = self.mod.derive_matches(
            {"kind": "movie", "title": "Inception"},
            {},
            {"movie_name": "Inception"},
        )
        self.assertNotIn("year", matches)

        matched = self.mod.derive_matches(
            {"kind": "movie", "title": "Inception", "year": 2010},
            {},
            {"movie_name": "Inception", "year": 2010},
        )
        self.assertIn("year", matched)

    def test_episode_imdb_id_match_is_scored(self):
        video = {
            "kind": "episode",
            "series": "Breaking Bad",
            "season": 3,
            "episode": 13,
            "imdb_id": "tt1628687",
        }
        feature = {
            "feature_type": "Episode",
            "imdb_id": 1628687,
            "season_number": 3,
            "episode_number": 13,
        }
        matches = self.mod.derive_matches(video, {}, feature)
        self.assertIn("imdb_id", matches)
        self.assertEqual(
            self.mod.compute_score(matches),
            self.mod.compute_score([m for m in matches if m != "imdb_id"]) + 30,
        )


class OpenSubtitlesComSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_credentials(self):
        provider = self.mod.OpenSubtitlesComProvider()

        with self.assertRaisesRegex(ValueError, "username"):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "eng", "alpha2": "en"}],
                {"api_key": "api-key", "password": "pass"},
            )

    def test_login_uses_returned_base_url_and_vip_token_on_search(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []

        def post_json(path, payload, headers, timeout=30):
            del timeout
            calls.append(("POST", path, payload, headers))
            return {"token": "jwt-token", "base_url": "vip-api.opensubtitles.com", "status": 200}

        def get_json(path, params, headers, timeout=30):
            del timeout
            calls.append(("GET", path, params, headers))
            self.assertEqual(headers["Authorization"], "Bearer jwt-token")
            self.assertEqual(path, "https://vip-api.opensubtitles.com/api/v1/subtitles")
            return {"data": []}

        provider._http_post_json = post_json
        provider._http_get_json = get_json
        provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(calls[0][1], "https://api.opensubtitles.com/api/v1/login")
        self.assertEqual(calls[0][2], {"username": "user", "password": "pass"})

    def test_episode_search_uses_parent_imdb_id_hash_and_translation_filters(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []

        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            if any(key == "moviehash" for key, _value in params):
                return {"data": []}
            return {
                "data": [
                    _subtitle_item(moviehash_match=False),
                    _subtitle_item(subtitle_id="ai", file_id=2, ai_translated=True),
                    _subtitle_item(subtitle_id="machine", file_id=3, machine_translated=True),
                ]
            }

        provider._http_get_json = get_json
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "series_imdb_id": "tt0903747",
                "year": 2008,
                "hashes": {"opensubtitlescom": "8e245d9679d31e12"},
            },
            [{"alpha3": "eng", "alpha2": "en", "forced": False}],
            {
                "username": "user",
                "password": "pass",
                "api_key": "api-key",
                "use_hash": True,
                "include_ai_translated": False,
                "include_machine_translated": False,
            },
        )

        self.assertIn(("moviehash", "8e245d9679d31e12"), calls[0])
        self.assertNotIn(("moviehash", "8e245d9679d31e12"), calls[1])
        self.assertEqual(
            calls[1],
            [
                ("ai_translated", "exclude"),
                ("episode_number", 13),
                ("hearing_impaired", "exclude"),
                ("languages", "en"),
                ("parent_imdb_id", 903747),
                ("season_number", 3),
            ],
        )
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "opensubtitlescom")
        self.assertEqual(first["provider_payload"]["file_id"], 11047023)
        self.assertIn("series", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertIn("series_imdb_id", first["matches"])

    def test_search_retries_once_after_expired_token(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider.token = "old-token"
        provider.base_host = "vip-api.opensubtitles.com"
        provider.token_started = time.time()
        logins = []
        calls = []

        def post_json(path, payload, headers, timeout=30):
            del path, payload, headers, timeout
            logins.append("login")
            return {"token": "new-token", "base_url": "vip-api.opensubtitles.com", "status": 200}

        def get_json(path, params, headers, timeout=30):
            del path, params, timeout
            calls.append(headers["Authorization"])
            if len(calls) == 1:
                raise self.mod.AuthenticationRequired("expired token")
            return {"data": []}

        provider._http_post_json = post_json
        provider._http_get_json = get_json
        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(results, [])
        self.assertEqual(logins, ["login"])
        self.assertEqual(calls, ["Bearer old-token", "Bearer new-token"])

    def test_feature_lookup_retries_after_expired_token(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider.token = "old-token"
        provider.base_host = "vip-api.opensubtitles.com"
        provider.token_started = time.time()
        logins = []
        calls = []

        def post_json(path, payload, headers, timeout=30):
            del path, payload, headers, timeout
            logins.append("login")
            return {"token": "new-token", "base_url": "vip-api.opensubtitles.com", "status": 200}

        def get_json(path, params, headers, timeout=30):
            del params, timeout
            calls.append((path, headers["Authorization"]))
            if len(calls) == 1:
                raise self.mod.AuthenticationRequired("expired token")
            if path.endswith("/features"):
                return {
                    "data": [
                        {"id": "514811", "attributes": {"title": "inception", "year": "2010", "feature_type": "Movie"}}
                    ]
                }
            return {"data": []}

        provider._http_post_json = post_json
        provider._http_get_json = get_json
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(results, [])
        self.assertEqual(logins, ["login"])
        self.assertEqual(calls[0][1], "Bearer old-token")
        self.assertEqual(calls[1][1], "Bearer new-token")

    def test_hash_only_movie_search_reaches_subtitles_api(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "movie", "hashes": {"opensubtitlescom": "8e245d9679d31e12"}},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": True},
        )

        self.assertIn(("moviehash", "8e245d9679d31e12"), calls[0])
        self.assertNotIn("id", {key for key, _value in calls[0]})

    def test_movie_search_uses_title_feature_fallback_when_no_imdb_id_exists(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []

        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del headers, timeout
            calls.append((path, params))
            if path.endswith("/features"):
                return {
                    "data": [
                        {
                            "id": "514811",
                            "attributes": {
                                "title": "inception",
                                "year": "2010",
                            },
                        }
                    ]
                }
            return {"data": [_subtitle_item(language="pt-BR", feature_type="Movie")]}

        provider._http_get_json = get_json
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010},
            [{"alpha3": "por", "alpha2": "pt", "country": "BR"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(calls[0][0], "https://api.opensubtitles.com/api/v1/features")
        self.assertEqual(calls[0][1], [("query", "inception")])
        self.assertIn(("id", 514811), calls[1][1])
        self.assertIn(("languages", "pt-BR"), calls[1][1])
        self.assertEqual(results[0]["language"]["country"], "BR")

    def test_feature_fallback_filters_to_requested_media_type(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del headers, timeout
            calls.append((path, params))
            if path.endswith("/features"):
                return {
                    "data": [
                        {"id": "1", "attributes": {"title": "dark", "year": "2017", "feature_type": "Movie"}},
                        {"id": "2", "attributes": {"title": "dark", "year": "2017", "feature_type": "Tvshow"}},
                    ]
                }
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "episode", "series": "Dark", "year": 2017, "season": 1, "episode": 1},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertIn(("parent_feature_id", 2), calls[1][1])

    def test_episode_search_accepts_list_valued_episode(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "episode", "series_imdb_id": "tt0903747", "season": 3, "episode": [13, 14]},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertIn(("episode_number", 13), calls[0])

    def test_forced_request_returns_only_real_forced_subtitles(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }
        provider._http_get_json = lambda path, params, headers, timeout=30: {
            "data": [
                _subtitle_item(subtitle_id="regular", feature_type="Movie"),
                _subtitle_item(subtitle_id="forced", file_id=2, foreign_parts_only=True, feature_type="Movie"),
                _subtitle_item(
                    subtitle_id="hi",
                    file_id=3,
                    foreign_parts_only=True,
                    hearing_impaired=True,
                    feature_type="Movie",
                ),
            ]
        }

        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en", "forced": True}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["forced"])
        self.assertTrue(results[0]["language"]["forced"])

    def test_mixed_forced_request_preserves_per_language_preferences(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }
        provider._http_get_json = lambda path, params, headers, timeout=30: {
            "data": [
                _subtitle_item(subtitle_id="regular-en", language="en", feature_type="Movie"),
                _subtitle_item(
                    subtitle_id="forced-en", file_id=2, language="en", foreign_parts_only=True, feature_type="Movie"
                ),
                _subtitle_item(subtitle_id="regular-es", file_id=3, language="es", feature_type="Movie"),
                _subtitle_item(
                    subtitle_id="forced-es", file_id=4, language="es", foreign_parts_only=True, feature_type="Movie"
                ),
            ]
        }

        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [
                {"alpha3": "eng", "alpha2": "en", "forced": True},
                {"alpha3": "spa", "alpha2": "es", "forced": False},
            ],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(
            {item["provider_payload"]["subtitle_id"] for item in results},
            {"forced-en", "regular-es"},
        )

    def test_hash_only_miss_does_not_run_unscoped_fallback(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "movie", "hashes": {"opensubtitlescom": "8e245d9679d31e12"}},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": True},
        )

        self.assertEqual(len(calls), 1)
        self.assertIn(("moviehash", "8e245d9679d31e12"), calls[0])

    def test_hash_miss_with_imdb_id_still_runs_fallback(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {
                "kind": "movie",
                "imdb_id": "tt1375666",
                "hashes": {"opensubtitlescom": "8e245d9679d31e12"},
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": True},
        )

        self.assertEqual(len(calls), 2)
        self.assertIn(("moviehash", "8e245d9679d31e12"), calls[0])
        self.assertNotIn(("moviehash", "8e245d9679d31e12"), calls[1])
        self.assertIn(("imdb_id", 1375666), calls[1])

    def test_all_forced_request_pushes_foreign_parts_only_filter(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en", "forced": True}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertIn(("foreign_parts_only", "only"), calls[0])

    def test_regular_only_request_excludes_hi_upstream(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }

        def get_json(path, params, headers, timeout=30):
            del path, headers, timeout
            calls.append(params)
            return {"data": []}

        provider._http_get_json = get_json
        provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en", "hi": False}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertIn(("hearing_impaired", "exclude"), calls[0])

    def test_episode_search_drops_movie_typed_rows(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }
        provider._http_get_json = lambda path, params, headers, timeout=30: {
            "data": [
                _subtitle_item(subtitle_id="movie-row", file_id=2, feature_type="Movie"),
                _subtitle_item(subtitle_id="episode-row", file_id=3, feature_type="Episode"),
            ]
        }

        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "series_imdb_id": "tt0903747",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(
            [item["provider_payload"]["subtitle_id"] for item in results],
            ["episode-row"],
        )

    def test_hash_verifiable_flag_tracks_moviehash_match(self):
        provider = self.mod.OpenSubtitlesComProvider()
        hashed = provider._result(
            {"kind": "episode", "series_imdb_id": "tt0903747", "season": 3, "episode": 13},
            _subtitle_item(moviehash_match=True),
            _subtitle_item(moviehash_match=True)["attributes"],
            _subtitle_item(moviehash_match=True)["attributes"]["files"][0],
            forced=False,
        )
        plain = provider._result(
            {"kind": "episode", "series_imdb_id": "tt0903747", "season": 3, "episode": 13},
            _subtitle_item(moviehash_match=False),
            _subtitle_item(moviehash_match=False)["attributes"],
            _subtitle_item(moviehash_match=False)["attributes"]["files"][0],
            forced=False,
        )

        self.assertTrue(hashed["hash_verifiable"])
        self.assertFalse(plain["hash_verifiable"])

    def test_score_without_hash_excludes_hash_points(self):
        provider = self.mod.OpenSubtitlesComProvider()
        result = provider._result(
            {"kind": "episode", "series_imdb_id": "tt0903747", "season": 3, "episode": 13},
            _subtitle_item(moviehash_match=True),
            _subtitle_item(moviehash_match=True)["attributes"],
            _subtitle_item(moviehash_match=True)["attributes"]["files"][0],
            forced=False,
        )

        self.assertIn("hash", result["matches"])
        self.assertLess(result["score_without_hash"], result["score"])


class OpenSubtitlesComDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_posts_file_id_then_fetches_returned_link(self):
        provider = self.mod.OpenSubtitlesComProvider()
        calls = []
        content = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"

        def post_json(path, payload, headers, timeout=30):
            del timeout
            calls.append(("POST", path, payload, headers))
            if path.endswith("/login"):
                return {"token": "jwt-token", "base_url": "api.opensubtitles.com", "status": 200}
            self.assertEqual(payload, {"file_id": 11047023, "sub_format": "srt"})
            self.assertEqual(headers["Authorization"], "Bearer jwt-token")
            return {"link": "https://dl.opensubtitles.com/download/subtitle.srt"}

        def get_bytes(url, headers, timeout=30):
            del timeout
            calls.append(("GET", url, headers))
            return content

        provider._http_post_json = post_json
        provider._http_get_bytes = get_bytes
        result = provider.download(
            {
                "provider": "opensubtitlescom",
                "schema": 1,
                "subtitle_id": "10139516",
                "file_id": 11047023,
                "filename": "subtitle.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "password": "pass", "api_key": "api-key"},
        )

        body = base64.b64decode(result["content_b64"])
        self.assertEqual(body, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(calls[1][1], "https://api.opensubtitles.com/api/v1/download")
        self.assertEqual(calls[2][2]["User-Agent"], self.mod.USER_AGENT)

    def test_download_reports_srt_format_for_converted_ass_payload(self):
        provider = self.mod.OpenSubtitlesComProvider()
        content = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n"

        def post_json(path, payload, headers, timeout=30):
            del headers, timeout
            if path.endswith("/login"):
                return {"token": "jwt-token", "base_url": "api.opensubtitles.com", "status": 200}
            self.assertEqual(payload, {"file_id": 11047023, "sub_format": "srt"})
            return {"link": "https://dl.opensubtitles.com/download/subtitle.srt"}

        provider._http_post_json = post_json
        provider._http_get_bytes = lambda url, headers, timeout=30: content
        result = provider.download(
            {
                "provider": "opensubtitlescom",
                "schema": 1,
                "subtitle_id": "10139516",
                "file_id": 11047023,
                "filename": "Breaking.Bad.S03E13.ass",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "password": "pass", "api_key": "api-key"},
        )

        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")

    def _download_with_body(self, body, payload=None):
        provider = self.mod.OpenSubtitlesComProvider()

        def post_json(path, params, headers, timeout=30):
            del params, headers, timeout
            if path.endswith("/login"):
                return {"token": "jwt-token", "base_url": "api.opensubtitles.com", "status": 200}
            return {"link": "https://dl.opensubtitles.com/download/subtitle.srt"}

        provider._http_post_json = post_json
        provider._http_get_bytes = lambda url, headers, timeout=30: body
        return provider.download(
            payload
            or {
                "provider": "opensubtitlescom",
                "schema": 1,
                "subtitle_id": "10139516",
                "file_id": 11047023,
                "filename": "subtitle.srt",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "password": "pass", "api_key": "api-key"},
        )

    def test_download_returns_zip_archive_bytes_with_selected_member(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "ignore me")
            archive.writestr("Breaking.Bad.S03E13.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        body = buffer.getvalue()

        result = self._download_with_body(body)

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Breaking.Bad.S03E13.srt")
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_with_episode_for_host_selection(self):
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 64

        result = self._download_with_body(
            body,
            payload={
                "provider": "opensubtitlescom",
                "schema": 1,
                "file_id": 11047023,
                "filename": "pack.rar",
                "episode": 13,
            },
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 13)
        self.assertNotIn("member", result)
        self.assertNotIn("encoding", result)

    def test_download_returns_7z_archive_with_episode_for_host_selection(self):
        body = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64

        result = self._download_with_body(
            body,
            payload={"provider": "opensubtitlescom", "file_id": 11047023, "episode": 7},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["episode"], 7)
        self.assertNotIn("member", result)

    def test_download_rejects_empty_body(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            self._download_with_body(b"")

    def test_download_rejects_html_error_page(self):
        with self.assertRaisesRegex(ValueError, "HTML"):
            self._download_with_body(b"<!DOCTYPE html><html><body>error</body></html>")

    def test_download_does_not_pin_encoding_on_direct_content(self):
        result = self._download_with_body(b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHallo\r\n")

        self.assertIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_search_stores_episode_in_provider_payload(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }
        provider._http_get_json = lambda path, params, headers, timeout=30: {
            "data": [_subtitle_item(feature_type="Episode")]
        }

        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "series_imdb_id": "tt0903747",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual(results[0]["provider_payload"]["season"], 3)
        self.assertEqual(results[0]["provider_payload"]["episode"], 13)


if __name__ == "__main__":
    unittest.main()
