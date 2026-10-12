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

    def test_parse_streams_derives_country_from_brazilian_title_markers(self):
        payload = {
            "streams": [
                {"index": 2, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Portuguese (Brazil)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 3, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "PT-BR Full"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 4, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Português (Brasil)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 5, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Português Brasileiro"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 6, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "pob"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 7, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "PTBR"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 8, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Portugais (Brésil)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(
            [stream["language"].get("country_alpha2") for stream in streams],
            ["BR", "BR", "BR", "BR", "BR", "BR", "BR"],
        )
        self.assertTrue(all(stream["language"]["alpha3"] == "por" for stream in streams))

    def test_parse_streams_derives_country_from_spanish_and_chinese_titles(self):
        payload = {
            "streams": [
                {"index": 2, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Español Latino"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 3, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Español (Latinoamérica)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 4, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Espagnol (Mexicain)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 5, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Argentinean Spanish"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 6, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Subtitulos Mexicanos"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 7, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Spanisch (Lateinamerika)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 8, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Spanish (LAT)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 9, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "Traditional Chinese"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 10, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "Chinese ZHHant"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 11, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "CHT"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 12, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "Chinese (Taiwan)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 13, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "Taiwanese Mandarin"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 14, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "Chinois (traditionnel)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 15, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "Chinese (TC)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(
            [(stream["language"]["alpha3"], stream["language"].get("country_alpha2"))
             for stream in streams],
            [
                ("spa", "MX"), ("spa", "MX"), ("spa", "MX"),
                ("spa", "MX"), ("spa", "MX"), ("spa", "MX"), ("spa", "MX"),
                ("zho", "TW"), ("zho", "TW"), ("zho", "TW"), ("zho", "TW"),
                ("zho", "TW"), ("zho", "TW"), ("zho", "TW"),
            ],
        )

    def test_parse_streams_title_marker_guards(self):
        payload = {
            "streams": [
                # An underscored subtag maps at the tag level.
                {"index": 2, "codec_name": "subrip",
                 "tags": {"language": "pt_BR", "title": "Portuguese"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # ... and stays explicit against a contradicting title.
                {"index": 3, "codec_name": "subrip",
                 "tags": {"language": "pt_PT", "title": "Brasil"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # An underscored marker folds to its hyphenated form.
                {"index": 4, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "es_LA"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # A marker behind an accent needs the fold to match.
                {"index": 5, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Español (México)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # A stem only matches at word start.
                {"index": 6, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Español Platino"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # A marker written as a plain subtag string in the title.
                {"index": 7, "codec_name": "subrip",
                 "tags": {"language": "spa", "title": "Spanish es-419"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # CJK markers match inside CJK titles.
                {"index": 8, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "繁體中文"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 9, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "中文（繁體）"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # A Simplified title stays plain.
                {"index": 10, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "简体中文"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # Mixed-script labels carry Traditional characters inside a
                # Simplified context and must not classify the track.
                {"index": 11, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "简繁中文"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 12, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "簡繁體中文"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 13, "codec_name": "subrip",
                 "tags": {"language": "zho", "title": "简繁体中文"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(
            [(stream["language"]["alpha3"], stream["language"].get("country_alpha2"))
             for stream in streams],
            [
                ("por", "BR"), ("por", None), ("spa", "MX"), ("spa", "MX"),
                ("spa", None), ("spa", "MX"), ("zho", "TW"), ("zho", "TW"),
                ("zho", None), ("zho", None), ("zho", None), ("zho", None),
            ],
        )

    def test_parse_streams_subtag_language_tags_win_over_titles(self):
        payload = {
            "streams": [
                # An explicit unmodeled region beats contradicting title markers.
                {"index": 2, "codec_name": "subrip",
                 "tags": {"language": "pt-PT", "title": "Brasil"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 3, "codec_name": "subrip",
                 "tags": {"language": "zh-Hans", "title": "Traditional Chinese"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # Modeled subtags map at the tag level.
                {"index": 4, "codec_name": "subrip",
                 "tags": {"language": "es-419", "title": "Spanish"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                {"index": 5, "codec_name": "subrip",
                 "tags": {"language": "zh-Hant", "title": "Chinese"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # es-AR joins es-419 as a Latin American subtag Bazarr itself
                # files under its Latino variant.
                {"index": 6, "codec_name": "subrip",
                 "tags": {"language": "es-AR", "title": "Español"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(
            [(stream["language"]["alpha3"], stream["language"].get("country_alpha2"))
             for stream in streams],
            [("por", None), ("zho", None), ("spa", "MX"), ("zho", "TW"),
             ("spa", "MX")],
        )

    def test_parse_streams_title_region_keeps_forced_and_hi_flags(self):
        payload = {
            "streams": [
                {"index": 2, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Brazilian Forced SDH"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(streams[0]["language"]["country_alpha2"], "BR")
        self.assertTrue(streams[0]["language"]["forced"])
        self.assertTrue(streams[0]["language"]["hi"])

    def test_parse_streams_title_markers_are_bounded_and_language_scoped(self):
        payload = {
            "streams": [
                # `pob` inside a longer word must not match.
                {"index": 2, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Pobre Diablo"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # A plain Portuguese title stays plain.
                {"index": 3, "codec_name": "subrip",
                 "tags": {"language": "por", "title": "Portuguese (Portugal)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # Markers never refine another language.
                {"index": 4, "codec_name": "subrip",
                 "tags": {"language": "eng", "title": "English (Brazil)"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
                # A regional language tag wins over contradicting title markers.
                {"index": 5, "codec_name": "subrip",
                 "tags": {"language": "pt-BR", "title": "Latin Spanish"},
                 "disposition": {"forced": 0, "hearing_impaired": 0}},
            ]
        }

        streams = self.mod.parse_probe_streams(payload, {})

        self.assertEqual(
            [(stream["language"]["alpha3"], stream["language"].get("country_alpha2"))
             for stream in streams],
            [("por", None), ("por", None), ("eng", None), ("por", "BR")],
        )

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

    def test_unknown_fallback_is_suppressed_only_for_same_language_and_disposition(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "und", "title": "Unknown generic"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 3,
                    "codec_name": "subrip",
                    "tags": {"language": "eng", "title": "English generic"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 4,
                    "codec_name": "subrip",
                    "tags": {"language": "und", "title": "Unknown forced"},
                    "disposition": {"forced": 1, "hearing_impaired": 0},
                },
                {
                    "index": 5,
                    "codec_name": "subrip",
                    "tags": {"language": "und", "title": "Unknown SDH"},
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
            [
                {"alpha3": "eng", "hi": False, "forced": False},
                {"alpha3": "eng", "hi": False, "forced": True},
                {"alpha3": "eng", "hi": True, "forced": False},
            ],
            {"unknown_as_fallback": True, "fallback_lang": "eng"},
        )

        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in results], [3, 4, 5]
        )

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

    def test_manifest_declares_the_modeled_regional_variants(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text())

        # The pool intersects the requested languages with the declared set as
        # whole Languages, so Language("por", "BR") is only ever passed to the
        # plugin when por-BR is declared alongside the base code.
        languages = set(manifest["languages"])
        for code in ("por", "por-BR", "spa", "spa-MX", "zho", "zho-TW"):
            self.assertIn(code, languages)


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

    def test_search_returns_brazilian_title_track_for_regional_request(self):
        # The reported case: a track remuxed with a legacy Matroska language
        # element probes as plain `por` while its title carries the region.
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "ass",
                    "tags": {"language": "por", "title": "Portuguese (Brazil)"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 3,
                    "codec_name": "subrip",
                    "tags": {"language": "por", "title": "Portuguese"},
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
        self.assertEqual(results[0]["language"]["alpha3"], "por")
        self.assertEqual(results[0]["language"]["country_alpha2"], "BR")

    def test_search_skips_brazilian_title_track_for_plain_request(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "ass",
                    "tags": {"language": "por", "title": "Portuguese (Brazil)"},
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

    def test_search_returns_traditional_title_track_for_regional_request(self):
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "zho", "title": "Traditional Chinese"},
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
            [{"alpha3": "zho", "alpha2": "zh", "country_alpha2": "TW"}],
            {},
        )

        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in results], [2]
        )
        self.assertEqual(results[0]["language"]["country_alpha2"], "TW")

    def test_search_unknown_fallback_survives_when_explicit_track_turns_regional(self):
        # The explicit track is Brazilian now, so a plain por request no longer
        # suppresses the unknown-language fallback: its key is the plain
        # language, and no explicit stream shares it anymore.
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "und", "title": "Brazilian"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 3,
                    "codec_name": "subrip",
                    "tags": {"language": "por", "title": "Portuguese (Brazil)"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
            ]
        }
        provider = self.mod.EmbeddedSubtitlesProvider(
            probe_runner=FakeProbeRunner(payload),
            path_exists=lambda path: True,
        )
        config = {"unknown_as_fallback": True, "fallback_lang": "por"}

        plain = provider.search(VIDEO, [{"alpha3": "por", "alpha2": "pt"}], config)
        regional = provider.search(
            VIDEO, [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}], config
        )

        # The fallback track carries no title-derived region: only a resolved
        # bare language tag is refined, so it answers the plain request and the
        # unknown-titled-Brazilian track never masquerades as regional.
        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in plain], [2]
        )
        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in regional], [3]
        )

    def test_search_hi_fallback_flips_regional_hi_track_for_regional_request(self):
        # The HI group is keyed by country, so the flip fires for the regional
        # group even when a plain-language non-HI track exists alongside it.
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "por", "title": "Portuguese (Brazil) SDH"},
                    "disposition": {"forced": 0, "hearing_impaired": 0},
                },
                {
                    "index": 3,
                    "codec_name": "subrip",
                    "tags": {"language": "por", "title": "Portuguese"},
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
            [{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR", "hi": False}],
            {"hi_fallback": True},
        )

        self.assertEqual(
            [item["provider_payload"]["stream_index"] for item in results], [2]
        )
        self.assertFalse(results[0]["language"]["hi"])

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
