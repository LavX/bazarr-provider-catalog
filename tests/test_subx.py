import base64
import hashlib
import importlib.util
import io
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "subx"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "subx_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_body(filename, body):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, body)
    return stream.getvalue()


def _zip_multi(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return stream.getvalue()


class SubXHelpersTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_auth_headers_use_bearer_api_key(self):
        headers = self.mod.auth_headers("secret")
        self.assertEqual(headers["Authorization"], "Bearer secret")

    def test_build_episode_queries_keeps_exact_season_and_title_fallbacks(self):
        queries = self.mod.build_episode_queries(
            {"series": "Breaking.Bad", "season": 3, "episode": 13}
        )
        self.assertEqual(
            queries,
            [
                ("Breaking Bad S03E13", 3, 13),
                ("Breaking Bad S03", 3, None),
                ("Breaking Bad", 3, None),
            ],
        )

    def test_build_episode_queries_includes_alternative_series_titles(self):
        queries = self.mod.build_episode_queries(
            {
                "series": "La casa de papel",
                "alternative_series": ["Money Heist"],
                "season": 1,
                "episode": 2,
            }
        )

        self.assertEqual(
            queries,
            [
                ("La casa de papel S01E02", 1, 2),
                ("La casa de papel S01", 1, None),
                ("La casa de papel", 1, None),
                ("Money Heist S01E02", 1, 2),
                ("Money Heist S01", 1, None),
                ("Money Heist", 1, None),
            ],
        )

    def test_language_variant_detects_spain_spanish_keywords(self):
        spain = self.mod.language_payload_from_description("Subtitulos en castellano de Espana")
        latin = self.mod.language_payload_from_description("Subtitulos latinoamericanos")

        self.assertEqual(spain["country"], "ES")
        self.assertEqual(latin["country"], "MX")


class SubXProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_api_key(self):
        provider = self.mod.SubXProvider()

        with self.assertRaisesRegex(ValueError, "api_key"):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "spa", "alpha2": "es"}],
                {},
            )

    def test_movie_search_prefers_imdb_id_and_returns_result(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del timeout
            calls.append((path, params, api_key))
            return {
                "items": [
                    {
                        "id": "sub-1",
                        "video_type": "movie",
                        "title": "Inception",
                        "imdb_id": "tt1375666",
                        "description": "Inception 2010 BluRay latino",
                        "uploader_name": "tester",
                        "downloads": 42,
                    }
                ],
                "total": 1,
            }

        provider._http_get_json = stub
        results = provider.search(
            {"kind": "movie", "title": "Inception", "year": 2010, "imdb_id": "tt1375666"},
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(
            calls,
            [
                (
                    "/api/subtitles/search",
                    {
                        "limit": 200,
                        "video_type": "movie",
                        "imdb_id": "tt1375666",
                        "year": 2010,
                    },
                    "key-one",
                )
            ],
        )
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "subx")
        self.assertEqual(first["language"]["alpha3"], "spa")
        self.assertEqual(first["language"]["country"], "MX")
        self.assertIn("title", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], "sub-1")

    def test_episode_search_keeps_exact_episode_before_season_pack(self):
        provider = self.mod.SubXProvider()

        def stub(path, params, api_key, timeout=10):
            del path, api_key, timeout
            self.assertEqual(params["imdb_id"], "tt0903747")
            return {
                "items": [
                    {
                        "id": "wrong-episode",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": 12,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad S03E12 latino",
                    },
                    {
                        "id": "season-pack",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": None,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad temporada 3 castellano",
                    },
                    {
                        "id": "exact",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": 13,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad S03E13 castellano",
                    },
                ],
                "total": 3,
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "imdb_id": "tt0903747",
                "year": 2008,
            },
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["exact"])
        self.assertEqual(results[0]["language"]["country"], "ES")
        self.assertIn("series", results[0]["matches"])
        self.assertIn("season", results[0]["matches"])
        self.assertIn("episode", results[0]["matches"])

    def test_episode_search_uses_season_pack_when_no_exact_episode_exists(self):
        provider = self.mod.SubXProvider()

        def stub(path, params, api_key, timeout=10):
            del path, params, api_key, timeout
            return {
                "items": [
                    {
                        "id": "season-pack",
                        "video_type": "episode",
                        "title": "Breaking Bad",
                        "season": 3,
                        "episode": None,
                        "imdb_id": "tt0903747",
                        "description": "Breaking Bad temporada 3 castellano",
                    }
                ],
                "total": 1,
            }

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Breaking Bad",
                "season": 3,
                "episode": 13,
                "imdb_id": "tt0903747",
            },
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "season-pack")
        self.assertTrue(results[0]["provider_payload"]["season_pack"])
        self.assertIn("episode", results[0]["matches"])

    def test_search_retries_rate_limit_once(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del path, params, timeout
            calls.append(api_key)
            if len(calls) == 1:
                raise self.mod.RateLimited("retry later", retry_after=0)
            return {"items": [], "total": 0}

        provider._http_get_json = stub
        provider.search(
            {"kind": "movie", "title": "Inception"},
            [{"alpha3": "spa", "alpha2": "es"}],
            {"api_key": "key-one", "request_delay_ms": 0},
        )

        self.assertEqual(calls, ["key-one", "key-one"])

    def test_search_returns_empty_after_repeated_server_errors(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del path, params, timeout
            calls.append(api_key)
            raise RuntimeError("SubX server error 500")

        provider._http_get_json = stub

        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "spa", "alpha2": "es"}],
                {"api_key": "key-one", "request_delay_ms": 0},
            ),
            [],
        )
        self.assertEqual(calls, ["key-one", "key-one", "key-one"])

    def test_search_raises_after_repeated_rate_limits(self):
        provider = self.mod.SubXProvider()
        calls = []

        def stub(path, params, api_key, timeout=10):
            del path, params, timeout
            calls.append(api_key)
            raise self.mod.RateLimited("retry later", retry_after=0)

        provider._http_get_json = stub

        with self.assertRaises(self.mod.RateLimited):
            provider.search(
                {"kind": "movie", "title": "Inception"},
                [{"alpha3": "spa", "alpha2": "es"}],
                {"api_key": "key-one", "request_delay_ms": 0},
            )
        self.assertEqual(calls, ["key-one", "key-one", "key-one"])


class SubXProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_accepts_direct_subtitle_body(self):
        provider = self.mod.SubXProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHola\n"
        provider._http_get_bytes = lambda path, api_key, timeout=30: body

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "sub-1",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/sub-1/download",
                "filename": "subx.sub-1.es.srt",
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertNotIn("archive_b64", result)

    def test_download_does_not_guess_encoding_for_direct_body(self):
        provider = self.mod.SubXProvider()
        body = b"1\n00:00:01,000 --> 00:00:02,000\nHol\xe1\n"
        provider._http_get_bytes = lambda path, api_key, timeout=30: body

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "sub-1",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/sub-1/download",
                "filename": "subx.sub-1.es.srt",
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertNotIn("encoding", result)

    def test_download_returns_zip_archive_unextracted(self):
        provider = self.mod.SubXProvider()
        archive = _zip_body("Breaking.Bad.S03E13.es.srt", b"1\nHola\n")
        provider._http_get_bytes = lambda path, api_key, timeout=30: archive

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "exact",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/exact/download",
                "filename": "subx.exact.es.zip",
                "episode": 13,
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 13)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_unextracted(self):
        provider = self.mod.SubXProvider()
        archive = b"Rar!\x1a\x07\x00rar body bytes"
        provider._http_get_bytes = lambda path, api_key, timeout=30: archive

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "exact",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/exact/download",
                "filename": "subx.exact.es.rar",
                "episode": 13,
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())
        self.assertEqual(result["episode"], 13)
        self.assertNotIn("content_b64", result)

    def test_download_archive_carries_none_episode_for_movies(self):
        provider = self.mod.SubXProvider()
        archive = _zip_body("Inception.es.srt", b"1\nHola\n")
        provider._http_get_bytes = lambda path, api_key, timeout=30: archive

        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "movie-1",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/movie-1/download",
                "filename": "subx.movie-1.es.zip",
                "episode": None,
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)
        self.assertIsNone(result["episode"])

    def test_download_rejects_empty_body(self):
        provider = self.mod.SubXProvider()
        provider._http_get_bytes = lambda path, api_key, timeout=30: b""

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "subx",
                    "schema": 1,
                    "subtitle_id": "sub-1",
                    "download_url": "https://subx-api.duckdns.org/api/subtitles/sub-1/download",
                    "filename": "subx.sub-1.es.srt",
                },
                {"alpha3": "spa", "alpha2": "es"},
                {"api_key": "key-one"},
            )


class SubXSeasonPackMemberTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _download_pack(self, archive, season, episode):
        provider = self.mod.SubXProvider()
        provider._http_get_bytes = lambda path, api_key, timeout=30: archive
        return provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "pack",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/pack/download",
                "filename": "subx.pack.es.zip",
                "season": season,
                "episode": episode,
                "season_pack": True,
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )

    def test_pins_unique_member_for_requested_season_and_episode(self):
        archive = _zip_multi(
            {
                "Breaking.Bad.S03E12.es.srt": b"1\nDoce\n",
                "Breaking.Bad.S03E13.es.srt": b"1\nTrece\n",
                "Breaking.Bad.S03E14.es.srt": b"1\nCatorce\n",
            }
        )
        result = self._download_pack(archive, season=3, episode=13)
        self.assertEqual(result["member"], "Breaking.Bad.S03E13.es.srt")
        self.assertNotIn("episode", result)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(archive).hexdigest())

    def test_pins_member_with_separated_season_episode_token(self):
        archive = _zip_multi(
            {
                "Show.S02.E04.castellano.srt": b"1\nCuatro\n",
                "Show.S02.E05.castellano.srt": b"1\nCinco\n",
            }
        )
        result = self._download_pack(archive, season=2, episode=5)
        self.assertEqual(result["member"], "Show.S02.E05.castellano.srt")
        self.assertNotIn("episode", result)

    def test_pins_member_with_nxnn_token(self):
        archive = _zip_multi(
            {
                "Serie 1x02 latino.srt": b"1\nDos\n",
                "Serie 1x03 latino.srt": b"1\nTres\n",
            }
        )
        result = self._download_pack(archive, season=1, episode=2)
        self.assertEqual(result["member"], "Serie 1x02 latino.srt")

    def test_defers_when_requested_episode_absent_from_pack(self):
        archive = _zip_multi(
            {
                "Breaking.Bad.S03E12.es.srt": b"1\nDoce\n",
                "Breaking.Bad.S03E14.es.srt": b"1\nCatorce\n",
            }
        )
        result = self._download_pack(archive, season=3, episode=13)
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 13)
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)

    def test_does_not_pin_wrong_season_with_same_episode_number(self):
        archive = _zip_multi(
            {
                "Show.S01E05.es.srt": b"1\nUno\n",
                "Show.S02E05.es.srt": b"1\nDos\n",
            }
        )
        result = self._download_pack(archive, season=2, episode=5)
        self.assertEqual(result["member"], "Show.S02E05.es.srt")

    def test_excludes_macosx_sidecar_members(self):
        archive = _zip_multi(
            {
                "__MACOSX/._Show.S01E02.es.srt": b"junk",
                "Show.S01E02.es.srt": b"1\nDos\n",
            }
        )
        result = self._download_pack(archive, season=1, episode=2)
        self.assertEqual(result["member"], "Show.S01E02.es.srt")

    def test_three_digit_episode_code_does_not_match_resolution(self):
        # S07E20 -> bare token "720" must not match the "720p" resolution tag.
        archive = _zip_multi(
            {
                "Show.720p.WEB.S07E20.es.srt": b"1\nVeinte\n",
                "Show.720p.WEB.S07E21.es.srt": b"1\nVeintiuno\n",
            }
        )
        result = self._download_pack(archive, season=7, episode=20)
        self.assertEqual(result["member"], "Show.720p.WEB.S07E20.es.srt")

    def test_defers_when_season_missing_from_payload(self):
        archive = _zip_multi(
            {
                "Show.S01E02.es.srt": b"1\nDos\n",
                "Show.S01E03.es.srt": b"1\nTres\n",
            }
        )
        provider = self.mod.SubXProvider()
        provider._http_get_bytes = lambda path, api_key, timeout=30: archive
        result = provider.download(
            {
                "provider": "subx",
                "schema": 1,
                "subtitle_id": "pack",
                "download_url": "https://subx-api.duckdns.org/api/subtitles/pack/download",
                "filename": "subx.pack.es.zip",
                "episode": 2,
            },
            {"alpha3": "spa", "alpha2": "es"},
            {"api_key": "key-one"},
        )
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 2)

    def test_rar_pack_defers_to_host_episode_selection(self):
        archive = b"Rar!\x1a\x07\x00rar season pack bytes"
        result = self._download_pack(archive, season=3, episode=13)
        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 13)
        self.assertEqual(base64.b64decode(result["archive_b64"]), archive)


class SubXMemberHasEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_matches_whole_token_season_episode_form(self):
        # "101" stands for S01E01 only when delimited.
        self.assertTrue(self.mod._member_has_episode("Show 101 latino.srt", 1, 1))
        self.assertFalse(self.mod._member_has_episode("Show 1010 latino.srt", 1, 1))

    def test_bare_token_does_not_match_resolution(self):
        # S07E20 -> "720" must not match the "720p" resolution tag.
        self.assertFalse(self.mod._member_has_episode("Show.720p.es.srt", 7, 20))

    def test_bare_token_does_not_match_codec(self):
        # S02E64 -> "264" must not match the "x264" codec tag.
        self.assertFalse(self.mod._member_has_episode("Show.x264.es.srt", 2, 64))

    def test_sxxeyy_requires_matching_season(self):
        self.assertTrue(self.mod._member_has_episode("Show.S02E05.es.srt", 2, 5))
        self.assertFalse(self.mod._member_has_episode("Show.S01E05.es.srt", 2, 5))

    def test_episode_digits_not_a_substring(self):
        # E20 must not be satisfied by E200 or 1080p style numbers.
        self.assertFalse(self.mod._member_has_episode("Show.S01E200.es.srt", 1, 20))


if __name__ == "__main__":
    unittest.main()
