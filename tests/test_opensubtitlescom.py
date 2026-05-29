import base64
import hashlib
import importlib.util
import time
import unittest
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
                "feature_type": "Episode",
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
            return {"data": [_subtitle_item(language="pt-BR")]}

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

    def test_forced_request_returns_only_real_forced_subtitles(self):
        provider = self.mod.OpenSubtitlesComProvider()
        provider._http_post_json = lambda path, payload, headers, timeout=30: {
            "token": "jwt-token",
            "base_url": "api.opensubtitles.com",
            "status": 200,
        }
        provider._http_get_json = lambda path, params, headers, timeout=30: {
            "data": [
                _subtitle_item(subtitle_id="regular"),
                _subtitle_item(subtitle_id="forced", file_id=2, foreign_parts_only=True),
                _subtitle_item(subtitle_id="hi", file_id=3, foreign_parts_only=True, hearing_impaired=True),
            ]
        }

        results = provider.search(
            {"kind": "movie", "title": "Inception", "imdb_id": "tt1375666"},
            [{"alpha3": "eng", "alpha2": "en", "forced": True}],
            {"username": "user", "password": "pass", "api_key": "api-key", "use_hash": False},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["forced"])
        self.assertTrue(results[0]["language"]["forced"])


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


if __name__ == "__main__":
    unittest.main()
