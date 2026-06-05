import base64
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "embeddedsubtitles"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "embeddedsubtitles_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = json.loads((FIXTURE_DIR / "embeddedsubtitles_video.json").read_text())

PROBE_PAYLOAD = {
    "streams": [
        {
            "index": 2,
            "codec_name": "subrip",
            "tags": {"language": "eng", "title": "English"},
            "disposition": {"forced": 0, "hearing_impaired": 0, "default": 1},
        },
        {
            "index": 3,
            "codec_name": "ass",
            "tags": {"language": "spa", "title": "Spanish Forced"},
            "disposition": {"forced": 1, "hearing_impaired": 0, "default": 0},
        },
        {
            "index": 4,
            "codec_name": "subrip",
            "tags": {"language": "eng", "title": "English SDH"},
            "disposition": {"forced": 0, "hearing_impaired": 1, "default": 0},
        },
        {
            "index": 5,
            "codec_name": "dvd_subtitle",
            "tags": {"language": "eng", "title": "Bitmap"},
            "disposition": {"forced": 0, "hearing_impaired": 0, "default": 0},
        },
    ]
}


class FakeProbeRunner:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, path, config):
        self.calls.append((path, dict(config or {})))
        return self.payload


class FakeExtractRunner:
    def __init__(self, content=b"1\n00:00:01,000 --> 00:00:02,000\nEmbedded\n"):
        self.content = content
        self.calls = []

    def __call__(self, path, stream_index, fmt, config):
        self.calls.append((path, stream_index, fmt, dict(config or {})))
        return self.content


class EmbeddedSubtitlesParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_streams_filters_allowed_codecs(self):
        streams = self.mod.parse_probe_streams(PROBE_PAYLOAD, {"included_codecs": "subrip,ass"})

        self.assertEqual([stream["index"] for stream in streams], [2, 3, 4])
        self.assertEqual(streams[0]["language"]["alpha3"], "eng")
        self.assertEqual(streams[1]["language"]["alpha3"], "spa")
        self.assertTrue(streams[1]["language"]["forced"])
        self.assertTrue(streams[2]["language"]["hi"])

    def test_parse_streams_derives_forced_and_hi_flags_from_titles(self):
        payload = {
            "streams": [
                {
                    "index": 8,
                    "codec_name": "subrip",
                    "tags": {"language": "eng", "title": "English Forced"},
                    "disposition": {"forced": 0, "hearing_impaired": 0, "default": 0},
                },
                {
                    "index": 9,
                    "codec_name": "subrip",
                    "tags": {"language": "eng", "title": "English SDH"},
                    "disposition": {"forced": 0, "hearing_impaired": 0, "default": 0},
                },
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertTrue(streams[0]["language"]["forced"])
        self.assertFalse(streams[0]["language"]["hi"])
        self.assertFalse(streams[1]["language"]["forced"])
        self.assertTrue(streams[1]["language"]["hi"])

    def test_parse_streams_preserves_country_on_regional_tags(self):
        payload = {
            "streams": [
                {
                    "index": 10,
                    "codec_name": "subrip",
                    "tags": {"language": "pt-BR", "title": "Brazilian"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 11,
                    "codec_name": "subrip",
                    "tags": {"language": "spa-MX", "title": "Latin Spanish"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 12,
                    "codec_name": "subrip",
                    "tags": {"language": "zh-TW", "title": "Traditional"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 13,
                    "codec_name": "subrip",
                    "tags": {"language": "pt-PT", "title": "European Portuguese"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(streams[0]["language"]["alpha3"], "por")
        self.assertEqual(streams[0]["language"]["country_alpha2"], "BR")
        self.assertEqual(streams[1]["language"]["alpha3"], "spa")
        self.assertEqual(streams[1]["language"]["country_alpha2"], "MX")
        self.assertEqual(streams[2]["language"]["alpha3"], "zho")
        self.assertEqual(streams[2]["language"]["country_alpha2"], "TW")
        # An unmodeled region (pt-PT) collapses to the plain language.
        self.assertEqual(streams[3]["language"]["alpha3"], "por")
        self.assertNotIn("country_alpha2", streams[3]["language"])

    def test_unknown_language_can_use_fallback(self):
        payload = {
            "streams": [
                {
                    "index": 7,
                    "codec_name": "subrip",
                    "tags": {"language": "und", "title": "Unknown"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                }
            ]
        }

        streams = self.mod.parse_probe_streams(
            payload,
            {"unknown_as_fallback": True, "fallback_lang": "eng"},
        )

        self.assertEqual(streams[0]["language"]["alpha3"], "eng")
        self.assertEqual(streams[0]["display_language"], "und -> eng")

    def test_probe_media_wraps_missing_ffprobe_as_embedded_error(self):
        with self.assertRaises(self.mod.EmbeddedSubtitleError):
            self.mod.probe_media(
                "/media/Example.Show.S01E02.mkv",
                {"ffprobe_path": "/definitely/missing/ffprobe"},
            )

    def test_probe_media_wraps_timeout_as_embedded_error(self):
        original_run = self.mod.subprocess.run

        def raise_timeout(command, **kwargs):
            del kwargs
            raise self.mod.subprocess.TimeoutExpired(command, timeout=30)

        self.mod.subprocess.run = raise_timeout
        try:
            with self.assertRaises(self.mod.EmbeddedSubtitleError):
                self.mod.probe_media("/media/Example.Show.S01E02.mkv", {})
        finally:
            self.mod.subprocess.run = original_run

    def test_probe_media_wraps_process_start_os_errors_as_embedded_error(self):
        original_run = self.mod.subprocess.run

        def raise_permission(command, **kwargs):
            del command, kwargs
            raise PermissionError("not executable")

        self.mod.subprocess.run = raise_permission
        try:
            with self.assertRaises(self.mod.EmbeddedSubtitleError):
                self.mod.probe_media("/media/Example.Show.S01E02.mkv", {})
        finally:
            self.mod.subprocess.run = original_run

    def test_manifest_exposes_hi_fallback_setting(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())

        properties = manifest["config_schema"]["properties"]
        self.assertIn("hi_fallback", properties)
        self.assertEqual(properties["hi_fallback"]["type"], "boolean")
        self.assertFalse(properties["hi_fallback"]["default"])

    def test_manifest_reuses_builtin_provider_id(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())

        # The plugin replaces the built-in by reusing its id, so no legacy alias
        # is needed and Bazarr's same-id registry overwrite takes effect.
        self.assertEqual(manifest["provider_id"], "embeddedsubtitles")
        self.assertNotIn("legacy_provider_id", manifest)


class EmbeddedSubtitlesProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_matching_generic_and_hi_streams(self):
        probe = FakeProbeRunner(PROBE_PAYLOAD)
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=probe,
            path_exists=lambda path: True,
        )

        results = provider.search(
            VIDEO,
            [
                {"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False},
                {"alpha3": "eng", "alpha2": "en", "hi": True, "forced": False},
            ],
            {},
        )

        self.assertEqual([item["provider_payload"]["stream_index"] for item in results], [2, 4])
        self.assertEqual(results[0]["matches"], ["series", "season", "episode", "hash"])
        self.assertEqual(results[1]["language"]["hi"], True)
        self.assertEqual(probe.calls[0][0], "/media/Example.Show.S01E02.mkv")

    def test_search_does_not_return_forced_stream_for_generic_request(self):
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(PROBE_PAYLOAD),
            path_exists=lambda path: True,
        )

        results = provider.search(
            VIDEO,
            [{"alpha3": "spa", "alpha2": "es", "hi": False, "forced": False}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_returns_forced_stream_when_requested(self):
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(PROBE_PAYLOAD),
            path_exists=lambda path: True,
        )

        results = provider.search(
            VIDEO,
            [{"alpha3": "spa", "alpha2": "es", "hi": False, "forced": True}],
            {},
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["language"]["forced"])
        self.assertEqual(results[0]["provider_payload"]["format"], "ass")

    def test_search_matches_request_country_for_brazilian_portuguese(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "pt-BR", "title": "Brazilian"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 3,
                    "codec_name": "subrip",
                    "tags": {"language": "pt", "title": "Portuguese"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
            ]
        }
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(payload),
            path_exists=lambda path: True,
        )

        results = provider.search(
            VIDEO,
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            {},
        )

        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in results], [2]
        )
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")

    def test_search_skips_regional_stream_for_plain_language_request(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "pt-BR", "title": "Brazilian"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                }
            ]
        }
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(payload),
            path_exists=lambda path: True,
        )

        results = provider.search(
            VIDEO,
            [{"alpha3": "por", "alpha2": "pt"}],
            {},
        )

        self.assertEqual(results, [])

    def test_search_hi_fallback_satisfies_non_hi_request(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "eng", "title": "English SDH"},
                    "disposition": {"forced": 0, "hearing_impaired": 1},
                }
            ]
        }
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(payload),
            path_exists=lambda path: True,
        )
        request = [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}]

        # Without the fallback the HI-only track does not satisfy a normal request.
        self.assertEqual(provider.search(VIDEO, request, {}), [])

        results = provider.search(VIDEO, request, {"hi_fallback": True})

        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in results], [2]
        )
        self.assertFalse(results[0]["language"]["hi"])
        self.assertFalse(results[0]["hearing_impaired"])

    def test_search_hi_fallback_keeps_hi_when_non_hi_track_present(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "eng", "title": "English"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 3,
                    "codec_name": "subrip",
                    "tags": {"language": "eng", "title": "English SDH"},
                    "disposition": {"forced": 0, "hearing_impaired": 1},
                },
            ]
        }
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(payload),
            path_exists=lambda path: True,
        )

        results = provider.search(
            VIDEO,
            [{"alpha3": "eng", "alpha2": "en", "hi": False, "forced": False}],
            {"hi_fallback": True},
        )

        # The genuine non-HI track still wins; the HI track is left untouched.
        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in results], [2]
        )

    def test_search_returns_empty_when_path_is_missing(self):
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(PROBE_PAYLOAD),
            path_exists=lambda path: False,
        )

        self.assertEqual(provider.search(VIDEO, [{"alpha3": "eng"}], {}), [])


class EmbeddedSubtitlesProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_extracts_requested_stream(self):
        body = b"1\n00:00:01,000 --> 00:00:02,000\nEmbedded\n"
        extractor = FakeExtractRunner(body)
        provider = self.mod.EmbeddedSubtitlesProvider(extract_runner=extractor)

        result = provider.download(
            {
                "provider": "embeddedsubtitles",
                "schema": 1,
                "path": "/media/Example.Show.S01E02.mkv",
                "stream_index": 2,
                "format": "srt",
            },
            {"alpha3": "eng"},
            {},
        )

        self.assertEqual(extractor.calls, [("/media/Example.Show.S01E02.mkv", 2, "srt", {})])
        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["content_sha256"], hashlib.sha256(body).hexdigest())
        self.assertFalse(result["empty"])

    def test_extract_stream_uses_webvtt_muxer_for_vtt_format(self):
        calls = []
        original_run = self.mod.subprocess.run

        class CompletedProcess:
            returncode = 0
            stdout = b"WEBVTT\n\n"
            stderr = b""

        def fake_run(command, **kwargs):
            del kwargs
            calls.append(command)
            return CompletedProcess()

        self.mod.subprocess.run = fake_run
        try:
            result = self.mod.extract_subtitle_stream(
                "/media/Example.Show.S01E02.mkv",
                6,
                "vtt",
                {},
            )
        finally:
            self.mod.subprocess.run = original_run

        self.assertEqual(result, b"WEBVTT\n\n")
        self.assertEqual(calls[0][calls[0].index("-f") + 1], "webvtt")

    def test_extract_stream_wraps_process_start_and_timeout_errors(self):
        original_run = self.mod.subprocess.run

        cases = [
            FileNotFoundError("missing"),
            PermissionError("not executable"),
            self.mod.subprocess.TimeoutExpired(["ffmpeg"], timeout=600),
        ]

        try:
            for error in cases:
                with self.subTest(error=type(error).__name__):
                    def raise_error(command, **kwargs):
                        del command, kwargs
                        raise error

                    self.mod.subprocess.run = raise_error
                    with self.assertRaises(self.mod.EmbeddedSubtitleError):
                        self.mod.extract_subtitle_stream(
                            "/media/Example.Show.S01E02.mkv",
                            2,
                            "srt",
                            {},
                        )
        finally:
            self.mod.subprocess.run = original_run

    def test_download_rejects_wrong_provider_payload(self):
        provider = self.mod.EmbeddedSubtitlesProvider(extract_runner=FakeExtractRunner())

        with self.assertRaises(ValueError):
            provider.download({"provider": "other"}, {"alpha3": "eng"}, {})


if __name__ == "__main__":
    unittest.main()
