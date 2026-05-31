import base64
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "whisperai"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "whisperai_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIDEO = json.loads((FIXTURE_DIR / "whisperai_video_japanese_audio.json").read_text())
CONFIG = {
    "endpoint": "http://whisper:9000",
    "response_timeout_seconds": 5,
    "transcription_timeout_seconds": 120,
    "ffmpeg_path": "ffmpeg",
    "pass_video_name": True,
}


class FakeAudioRunner:
    def __init__(self, body=b"pcm-audio"):
        self.body = body
        self.calls = []

    def __call__(self, path, ffmpeg_path, audio_stream_language=None, timeout_seconds=120):
        self.calls.append((path, ffmpeg_path, audio_stream_language, timeout_seconds))
        return self.body


class FakeHttpClient:
    def __init__(self, detect_payload=None, asr_body=b"1\n00:00:01,000 --> 00:00:02,000\nWhisper\n"):
        self.detect_payload = detect_payload or {"language_code": "ja", "detected_language": "Japanese"}
        self.asr_body = asr_body
        self.calls = []

    def post_multipart(self, endpoint, params, files, timeout):
        self.calls.append((endpoint, dict(params), dict(files), timeout))
        if endpoint == "/detect-language":
            return json.dumps(self.detect_payload).encode("utf-8"), "application/json"
        if endpoint == "/asr":
            return self.asr_body, "text/plain"
        raise AssertionError(f"unexpected endpoint: {endpoint}")


class WhisperAILanguageTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_language_mapping_supports_whisper_codes(self):
        self.assertEqual(self.mod.alpha3_to_alpha2("eng"), "en")
        self.assertEqual(self.mod.alpha2_to_alpha3("ja"), "jpn")
        self.assertEqual(self.mod.normalize_language("ger"), "deu")

    def test_plan_transcribes_matching_audio_language(self):
        plan = self.mod.plan_transcription(
            {"audio_languages": ["eng"], "original_path": "/media/a.mkv"},
            {"alpha3": "eng"},
        )

        self.assertEqual(plan["task"], "transcribe")
        self.assertEqual(plan["input_language"], "eng")
        self.assertEqual(plan["output_language"], "eng")

    def test_plan_translates_to_english_only(self):
        english = self.mod.plan_transcription(
            {"audio_languages": ["jpn"], "original_path": "/media/a.mkv"},
            {"alpha3": "eng"},
        )
        spanish = self.mod.plan_transcription(
            {"audio_languages": ["jpn"], "original_path": "/media/a.mkv"},
            {"alpha3": "spa"},
        )

        self.assertEqual(english["task"], "translate")
        self.assertIsNone(spanish)


class WhisperAIProviderSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_returns_translate_candidate_for_english_request(self):
        provider = self.mod.WhisperAIProvider(path_exists=lambda path: True)

        results = provider.search(VIDEO, [{"alpha3": "eng", "alpha2": "en"}], CONFIG)

        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["provider"], "whisperai")
        self.assertEqual(candidate["provider_payload"]["task"], "translate")
        self.assertEqual(candidate["provider_payload"]["input_language"], "jpn")
        self.assertEqual(candidate["provider_payload"]["output_language"], "eng")
        self.assertEqual(candidate["matches"], ["title"])

    def test_search_detects_language_when_video_has_no_audio_tags(self):
        audio = FakeAudioRunner()
        http = FakeHttpClient({"language_code": "en", "detected_language": "English"})
        provider = self.mod.WhisperAIProvider(
            audio_runner=audio,
            http_client_factory=lambda config: http,
            path_exists=lambda path: True,
        )
        video = {**VIDEO, "audio_languages": []}

        results = provider.search(video, [{"alpha3": "eng"}], CONFIG)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["task"], "transcribe")
        self.assertEqual(audio.calls[0][0], "/media/Example.Movie.2024.mkv")
        self.assertEqual(http.calls[0][0], "/detect-language")

    def test_search_requires_endpoint_and_existing_path(self):
        provider = self.mod.WhisperAIProvider(path_exists=lambda path: False)

        self.assertEqual(provider.search(VIDEO, [{"alpha3": "eng"}], CONFIG), [])
        with self.assertRaises(ValueError):
            provider.search(VIDEO, [{"alpha3": "eng"}], {"ffmpeg_path": "ffmpeg"})


class WhisperAIProviderDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_posts_audio_to_asr_endpoint(self):
        audio = FakeAudioRunner(b"audio-bytes")
        http = FakeHttpClient(asr_body=b"1\n00:00:01,000 --> 00:00:02,000\nWhisper\n")
        provider = self.mod.WhisperAIProvider(
            audio_runner=audio,
            http_client_factory=lambda config: http,
        )

        payload = {
            "provider": "whisperai",
            "schema": 1,
            "path": "/media/Example.Movie.2024.mkv",
            "task": "translate",
            "input_language": "jpn",
            "output_language": "eng",
            "audio_stream_language": "jpn",
        }
        result = provider.download(payload, {"alpha3": "eng"}, CONFIG)

        self.assertEqual(audio.calls, [("/media/Example.Movie.2024.mkv", "ffmpeg", "jpn", 120)])
        self.assertEqual(http.calls[0][0], "/asr")
        self.assertEqual(http.calls[0][1]["task"], "translate")
        self.assertEqual(http.calls[0][1]["language"], "ja")
        self.assertEqual(http.calls[0][1]["video_file"], "/media/Example.Movie.2024.mkv")
        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Whisper", decoded)
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_rejects_wrong_provider_payload(self):
        provider = self.mod.WhisperAIProvider(audio_runner=FakeAudioRunner())

        with self.assertRaises(ValueError):
            provider.download({"provider": "other"}, {"alpha3": "eng"}, CONFIG)


if __name__ == "__main__":
    unittest.main()
