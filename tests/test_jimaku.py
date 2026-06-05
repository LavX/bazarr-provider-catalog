import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "jimaku"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "jimaku_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_json(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


ENTRY_ANILIST = _fixture_json("jimaku_entries_anilist_154587.json")
ENTRY_MOVIE = _fixture_json("jimaku_entries_movie_query.json")
EMPTY = _fixture_json("jimaku_entries_empty.json")
FILES_EPISODE_05 = _fixture_json("jimaku_files_episode_05.json")
FILES_EPISODE_17 = _fixture_json("jimaku_files_episode_17.json")
FILES_ARCHIVES_ONLY = _fixture_json("jimaku_files_archives_only.json")
VIDEO_EPISODE = _fixture_json("jimaku_video_frieren_s01e05.json")
VIDEO_OFFSET = _fixture_json("jimaku_video_frieren_s02e05_offset.json")
VIDEO_MOVIE = _fixture_json("jimaku_video_godzilla_minus_one.json")
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nJimaku fixture.\n"


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class JimakuSearchParamTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_prefers_anilist_id(self):
        params = self.mod.build_entry_search_params(
            VIDEO_EPISODE,
            enable_name_search_fallback=False,
        )

        self.assertEqual(params, {"anilist_id": 154587})

    def test_movie_uses_tmdb_id_when_available(self):
        params = self.mod.build_entry_search_params(
            VIDEO_MOVIE,
            enable_name_search_fallback=False,
        )

        self.assertEqual(params, {"tmdb_id": "movie:940721"})

    def test_name_fallback_appends_season_number_after_season_one(self):
        params = self.mod.build_entry_search_params(
            {"kind": "episode", "series": "Show", "season": 2},
            enable_name_search_fallback=True,
        )

        self.assertEqual(params, {"query": "show 2"})

    def test_episode_without_ids_and_disabled_fallback_returns_none(self):
        params = self.mod.build_entry_search_params(
            {"kind": "episode", "series": "Show", "season": 1},
            enable_name_search_fallback=False,
        )

        self.assertIsNone(params)


class JimakuFilterTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_language_detection_rejects_likely_multilingual_subtitles(self):
        self.assertEqual(self.mod.detect_subtitle_languages("Episode.JA.srt"), ["jpn"])
        self.assertEqual(
            self.mod.detect_subtitle_languages("Episode.JPSC.ass"),
            ["jpn", "srd"],
        )
        self.assertEqual(
            self.mod.detect_subtitle_languages("Episode.1080p.ass"),
            ["jpn"],
        )

    def test_filter_files_respects_ai_archive_corrupt_and_multilingual_rules(self):
        rows = self.mod.filter_file_entries(
            FILES_EPISODE_05,
            enable_archives_download=False,
            enable_ai_subs=False,
            only_archives=False,
        )

        self.assertEqual([row["name"] for row in rows], ["Sousou.no.Frieren.S01E05.JA.srt"])

    def test_filter_files_keeps_archive_when_archives_are_only_option(self):
        rows = self.mod.filter_file_entries(
            FILES_ARCHIVES_ONLY,
            enable_archives_download=False,
            enable_ai_subs=False,
            only_archives=False,
        )

        self.assertEqual([row["name"] for row in rows], ["Sousou.no.Frieren.S01.JP.zip"])

    def test_single_non_japanese_language_is_rejected(self):
        # An English-only file must not be exposed as a Japanese result.
        self.assertEqual(
            self.mod.detect_subtitle_languages("Episode.EN.srt"),
            ["eng"],
        )
        rows = self.mod.filter_file_entries(
            [
                {
                    "name": "Sousou.no.Frieren.S01E05.EN.srt",
                    "size": 12345,
                    "url": "https://jimaku.cc/files/frieren-s01e05-en.srt",
                }
            ],
            enable_archives_download=False,
            enable_ai_subs=False,
            only_archives=False,
        )

        self.assertEqual(rows, [])

    def test_plain_whisper_filename_is_filtered_as_ai_subtitle(self):
        rows = self.mod.filter_file_entries(
            [
                {
                    "name": "Show.S01E05.Whisper.JA.srt",
                    "size": 12345,
                    "url": "https://jimaku.cc/files/show-s01e05-whisper.srt",
                }
            ],
            enable_archives_download=False,
            enable_ai_subs=False,
            only_archives=False,
        )

        self.assertEqual(rows, [])

    def test_unsupported_companion_does_not_suppress_archive_only_entry(self):
        rows = self.mod.filter_file_entries(
            [
                {
                    "name": "Sousou.no.Frieren.S01.JP.zip",
                    "size": 50000,
                    "url": "https://jimaku.cc/files/frieren-pack.zip",
                },
                {
                    "name": "readme.txt",
                    "size": 1024,
                    "url": "https://jimaku.cc/files/readme.txt",
                },
            ],
            enable_archives_download=False,
            enable_ai_subs=False,
            only_archives=False,
        )

        self.assertEqual([row["name"] for row in rows], ["Sousou.no.Frieren.S01.JP.zip"])


class JimakuProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_api_key(self):
        provider = self.mod.JimakuProvider()

        with self.assertRaisesRegex(ValueError, "api_key"):
            provider.search(VIDEO_EPISODE, [{"alpha3": "jpn"}], {})

    def test_search_uses_anilist_entry_and_episode_files(self):
        provider = self.mod.JimakuProvider()
        calls = []
        responses = {
            "entries/search?anilist_id=154587": ENTRY_ANILIST,
            "entries/123/files?episode=5": FILES_EPISODE_05,
        }

        def get_json(path, params=None, config=None):
            key = self.mod.api_path(path, params)
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected API call: {key}")
            return responses[key]

        provider._get_json = get_json
        results = provider.search(
            VIDEO_EPISODE,
            [{"alpha3": "jpn", "alpha2": "ja"}],
            {
                "api_key": "secret",
                "enable_name_search_fallback": False,
                "enable_archives_download": False,
                "enable_ai_subs": False,
            },
        )

        self.assertEqual(calls, ["entries/search?anilist_id=154587", "entries/123/files?episode=5"])
        self.assertEqual(len(results), 1)
        first = results[0]
        self.assertEqual(first["provider"], "jimaku")
        self.assertEqual(first["language"]["alpha3"], "jpn")
        self.assertEqual(first["provider_payload"]["url"], "https://jimaku.cc/files/frieren-s01e05.srt")
        self.assertEqual(first["provider_payload"]["episode"], 5)
        self.assertNotIn("api_key", first["provider_payload"])
        self.assertIn("episode", first["matches"])
        self.assertIn("audio_codec", first["matches"])

    def test_name_search_retries_live_action_without_anime_filter(self):
        provider = self.mod.JimakuProvider()
        calls = []
        responses = {
            "entries/search?anime=true&query=godzilla+minus+one": EMPTY,
            "entries/search?anime=false&query=godzilla+minus+one": ENTRY_MOVIE,
            "entries/456/files": [
                {
                    "last_modified": "2026-05-20T12:00:00Z",
                    "name": "Godzilla.Minus.One.2023.JA.srt",
                    "size": 12345,
                    "url": "https://jimaku.cc/files/godzilla-minus-one.srt",
                }
            ],
        }

        def get_json(path, params=None, config=None):
            key = self.mod.api_path(path, params)
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected API call: {key}")
            return responses[key]

        provider._get_json = get_json
        results = provider.search(
            {"kind": "movie", "title": "Godzilla Minus One", "year": 2023},
            [{"alpha3": "jpn", "alpha2": "ja"}],
            {"api_key": "secret", "enable_name_search_fallback": True},
        )

        self.assertEqual(
            calls,
            [
                "entries/search?anime=true&query=godzilla+minus+one",
                "entries/search?anime=false&query=godzilla+minus+one",
                "entries/456/files",
            ],
        )
        self.assertEqual(len(results), 1)
        self.assertIn("title", results[0]["matches"])

    def test_episode_offset_tries_adjusted_episode_first(self):
        provider = self.mod.JimakuProvider()
        calls = []
        responses = {
            "entries/search?anilist_id=154587": ENTRY_ANILIST,
            "entries/123/files?episode=17": FILES_EPISODE_17,
        }

        def get_json(path, params=None, config=None):
            key = self.mod.api_path(path, params)
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected API call: {key}")
            return responses[key]

        provider._get_json = get_json
        results = provider.search(
            VIDEO_OFFSET,
            [{"alpha3": "jpn", "alpha2": "ja"}],
            {"api_key": "secret", "enable_name_search_fallback": False},
        )

        self.assertEqual(calls, ["entries/search?anilist_id=154587", "entries/123/files?episode=17"])
        self.assertEqual(results[0]["provider_payload"]["episode"], 17)

    def test_episode_file_retry_without_episode_only_returns_archives(self):
        provider = self.mod.JimakuProvider()
        calls = []
        responses = {
            "entries/search?anilist_id=154587": ENTRY_ANILIST,
            "entries/123/files?episode=5": EMPTY,
            "entries/123/files": FILES_ARCHIVES_ONLY,
        }

        def get_json(path, params=None, config=None):
            key = self.mod.api_path(path, params)
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected API call: {key}")
            return responses[key]

        provider._get_json = get_json
        results = provider.search(
            VIDEO_EPISODE,
            [{"alpha3": "jpn", "alpha2": "ja"}],
            {
                "api_key": "secret",
                "enable_name_search_fallback": False,
                "enable_archives_download": False,
            },
        )

        self.assertEqual(calls, ["entries/search?anilist_id=154587", "entries/123/files?episode=5", "entries/123/files"])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["provider_payload"]["is_archive"])


class JimakuDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_returns_direct_subtitle_payload(self):
        provider = self.mod.JimakuProvider()
        provider._http_get = lambda url, config=None: SRT_BODY

        result = provider.download(
            {
                "provider": "jimaku",
                "schema": 1,
                "url": "https://jimaku.cc/files/frieren-s01e05.srt",
                "filename": "Sousou.no.Frieren.S01E05.JA.srt",
                "is_archive": False,
            },
            {"alpha3": "jpn", "alpha2": "ja"},
            {"api_key": "secret"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_matching_episode_from_zip(self):
        provider = self.mod.JimakuProvider()
        archive = _zip_body(
            {
                "Sousou.no.Frieren.S01E04.JA.srt": b"wrong",
                "Sousou.no.Frieren.S01E05.JA.srt": SRT_BODY,
            }
        )
        provider._http_get = lambda url, config=None: archive

        result = provider.download(
            {
                "provider": "jimaku",
                "schema": 1,
                "url": "https://jimaku.cc/files/frieren-pack.zip",
                "filename": "Sousou.no.Frieren.S01.JP.zip",
                "is_archive": True,
                "video": {"kind": "episode", "season": 1, "episode": 5},
            },
            {"alpha3": "jpn", "alpha2": "ja"},
            {"api_key": "secret"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)

    def test_download_extracts_matching_episode_from_rar(self):
        provider = self.mod.JimakuProvider()
        provider._http_get = lambda url, config=None: b"rar bytes"

        with mock.patch.object(
            self.mod,
            "_extract_rar_files",
            return_value=[
                ("Sousou.no.Frieren.S01E04.JA.srt", b"wrong"),
                ("Sousou.no.Frieren.S01E05.JA.srt", SRT_BODY),
            ],
        ) as extractor:
            result = provider.download(
                {
                    "provider": "jimaku",
                    "schema": 1,
                    "url": "https://jimaku.cc/files/frieren-pack.rar",
                    "filename": "Sousou.no.Frieren.S01.JP.rar",
                    "is_archive": True,
                    "video": {"kind": "episode", "season": 1, "episode": 5},
                },
                {"alpha3": "jpn", "alpha2": "ja"},
                {"api_key": "secret"},
            )

        extractor.assert_called_once_with(b"rar bytes")
        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)

    def test_download_rejects_archive_missing_requested_episode(self):
        provider = self.mod.JimakuProvider()
        archive = _zip_body(
            {
                "Sousou.no.Frieren.S01E04.JA.srt": b"wrong",
                "Sousou.no.Frieren.S01E06.JA.srt": b"also wrong",
            }
        )
        provider._http_get = lambda url, config=None: archive

        with self.assertRaisesRegex(ValueError, "episode 5"):
            provider.download(
                {
                    "provider": "jimaku",
                    "schema": 1,
                    "url": "https://jimaku.cc/files/frieren-pack.zip",
                    "filename": "Sousou.no.Frieren.S01.JP.zip",
                    "is_archive": True,
                    "video": {"kind": "episode", "season": 1, "episode": 5},
                },
                {"alpha3": "jpn", "alpha2": "ja"},
                {"api_key": "secret"},
            )
