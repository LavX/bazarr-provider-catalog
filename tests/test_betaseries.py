import base64
import hashlib
import importlib.util
import io
import json
import urllib.parse
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "betaseries"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "betaseries_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EPISODE_DISPLAY = json.loads((FIXTURE_DIR / "betaseries_episode_display.json").read_text())
SHOWS_EPISODES = json.loads((FIXTURE_DIR / "betaseries_shows_episodes.json").read_text())
NO_SERIES = json.loads((FIXTURE_DIR / "betaseries_no_series.json").read_text())
INVALID_TOKEN = json.loads((FIXTURE_DIR / "betaseries_invalid_token.json").read_text())


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class BetaSeriesProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_token_is_required(self):
        provider = self.mod.BetaSeriesProvider()

        with self.assertRaises(ValueError):
            provider.search({"kind": "episode", "series": "Blue Lights"}, [{"alpha3": "eng"}], {})

    def test_api_error_handling(self):
        self.assertEqual(self.mod.handle_api_errors(NO_SERIES), "empty")
        with self.assertRaisesRegex(ValueError, "Invalid token"):
            self.mod.handle_api_errors(INVALID_TOKEN)

    def test_search_uses_episode_display_when_episode_tvdb_id_exists(self):
        provider = self.mod.BetaSeriesProvider()
        calls = []

        def stub(url, timeout=10, config=None):
            del timeout, config
            calls.append(url)
            return EPISODE_DISPLAY

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Blue Lights",
                "season": 1,
                "episode": 1,
                "tvdb_id": 10101,
                "release_group": "ETHEL",
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"token": "secret-key"},
        )

        self.assertEqual(calls[0].split("?")[0], "https://api.betaseries.com/episodes/display")
        params = _query(calls[0])
        self.assertEqual(params["key"], "secret-key")
        self.assertEqual(params["thetvdb_id"], "10101")
        self.assertEqual(params["subtitles"], "1")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "101")
        self.assertIn("tvdb_id", results[0]["matches"])
        self.assertNotIn("secret-key", json.dumps(results, sort_keys=True))

    def test_search_falls_back_to_show_episode_endpoint(self):
        provider = self.mod.BetaSeriesProvider()
        calls = []

        def stub(url, timeout=10, config=None):
            del timeout, config
            calls.append(url)
            return SHOWS_EPISODES

        provider._http_get_json = stub
        results = provider.search(
            {
                "kind": "episode",
                "series": "Blue Lights",
                "season": 1,
                "episode": 1,
                "series_tvdb_id": 425089,
                "release_group": "ETHEL",
            },
            [{"alpha3": "fra", "alpha2": "fr"}],
            {"token": "secret-key"},
        )

        self.assertEqual(calls[0].split("?")[0], "https://api.betaseries.com/shows/episodes")
        params = _query(calls[0])
        self.assertEqual(params["thetvdb_id"], "425089")
        self.assertEqual(params["season"], "1")
        self.assertEqual(params["episode"], "1")
        self.assertEqual(results[0]["language"]["alpha3"], "fra")

    def test_search_returns_empty_for_movie_or_missing_tvdb(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_json = lambda url, timeout=10, config=None: EPISODE_DISPLAY

        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Dune"},
                [{"alpha3": "eng"}],
                {"token": "secret-key"},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1},
                [{"alpha3": "eng"}],
                {"token": "secret-key"},
            ),
            [],
        )

    def test_search_skips_seriessub_source(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_json = lambda url, timeout=10, config=None: EPISODE_DISPLAY

        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1, "tvdb_id": 10101},
            [{"alpha3": "fra", "alpha2": "fr"}],
            {"token": "secret-key"},
        )

        self.assertEqual(results, [])

    def test_download_extracts_release_group_match_from_zip(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_bytes = lambda url, timeout=10, config=None: _zip_body(
            {
                "Blue.Lights.S01E01.OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nWrong\n",
                "Blue.Lights.S01E01.ETHEL.srt": b"1\n00:00:01,000 --> 00:00:02,000\nRight\n",
            }
        )

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights-ethel.zip",
                "filename": "Blue.Lights.S01E01.1080p.WEB.h264-ETHEL.zip",
                "release_group": "ETHEL",
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertIn(b"Right", decoded)
        self.assertNotIn(b"Wrong", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())


if __name__ == "__main__":
    unittest.main()
