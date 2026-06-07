import base64
import hashlib
import importlib.util
import io
import json
import urllib.error
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

    def _http_error(self, payload):
        body = json.dumps(payload).encode("utf-8")
        return urllib.error.HTTPError(
            url="https://api.betaseries.com/episodes/display",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    def test_search_handles_http_400_no_series_error(self):
        provider = self.mod.BetaSeriesProvider()

        def raise_400(url, timeout=10, config=None):
            del url, timeout, config
            raise self._http_error(NO_SERIES)

        provider._http_get_bytes = raise_400

        results = provider.search(
            {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1, "tvdb_id": 10101},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"token": "secret-key"},
        )

        self.assertEqual(results, [])

    def test_search_handles_http_400_invalid_token_error(self):
        provider = self.mod.BetaSeriesProvider()

        def raise_400(url, timeout=10, config=None):
            del url, timeout, config
            raise self._http_error(INVALID_TOKEN)

        provider._http_get_bytes = raise_400

        with self.assertRaisesRegex(ValueError, "Invalid token"):
            provider.search(
                {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1, "tvdb_id": 10101},
                [{"alpha3": "eng", "alpha2": "en"}],
                {"token": "secret-key"},
            )

    def test_search_reraises_non_api_http_errors(self):
        provider = self.mod.BetaSeriesProvider()

        def raise_500(url, timeout=10, config=None):
            del timeout, config
            raise urllib.error.HTTPError(
                url=url, code=500, msg="Server Error", hdrs=None,
                fp=io.BytesIO(b"<html>boom</html>"),
            )

        provider._http_get_bytes = raise_500

        with self.assertRaises(urllib.error.HTTPError):
            provider.search(
                {"kind": "episode", "series": "Blue Lights", "season": 1, "episode": 1, "tvdb_id": 10101},
                [{"alpha3": "eng", "alpha2": "en"}],
                {"token": "secret-key"},
            )

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

    def test_search_payload_carries_episode_and_season(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_json = lambda url, timeout=10, config=None: EPISODE_DISPLAY

        results = provider.search(
            {
                "kind": "episode",
                "series": "Blue Lights",
                "season": 1,
                "episode": 1,
                "tvdb_id": 10101,
            },
            [{"alpha3": "eng", "alpha2": "en"}],
            {"token": "secret-key"},
        )

        payload = results[0]["provider_payload"]
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 1)

    def test_download_zip_archive_returns_raw_archive_for_host(self):
        provider = self.mod.BetaSeriesProvider()
        body = _zip_body(
            {
                "Blue.Lights.S01E01.OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nWrong\n",
                "Blue.Lights.S01E01.ETHEL.srt": b"1\n00:00:01,000 --> 00:00:02,000\nRight\n",
            }
        )
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights-ethel.zip",
                "filename": "Blue.Lights.S01E01.1080p.WEB.h264-ETHEL.zip",
                "release_group": "ETHEL",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        # Archive mode: the worker hands the raw bytes back untouched, but pins the
        # member matching the scored release_group so the host extracts that release
        # rather than guessing by episode (both members share S01E01).
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["member"], "Blue.Lights.S01E01.ETHEL.srt")
        # No worker-side extraction or encoding guessing; episode is unused once pinned.
        self.assertNotIn("content_b64", result)
        self.assertNotIn("episode", result)
        self.assertNotIn("encoding", result)

    def test_download_zip_archive_without_release_group_falls_back_to_episode(self):
        provider = self.mod.BetaSeriesProvider()
        body = _zip_body(
            {
                "Blue.Lights.S01E01.OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nOne\n",
                "Blue.Lights.S01E01.ELSE.srt": b"1\n00:00:01,000 --> 00:00:02,000\nTwo\n",
            }
        )
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights.zip",
                "filename": "Blue.Lights.S01E01.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        # No release_group to disambiguate: hand the whole archive over and let the
        # host pick the member by episode.
        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["episode"], 1)
        self.assertNotIn("member", result)
        self.assertNotIn("content_b64", result)

    def test_download_zip_pins_requested_episode_member_in_season_pack(self):
        # A season pack carries the same release group across episodes; the member must be
        # narrowed to the requested episode, not the first release-group match (E01).
        provider = self.mod.BetaSeriesProvider()
        body = _zip_body(
            {
                "Blue.Lights.S01E01.ETHEL.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE1\n",
                "Blue.Lights.S01E02.ETHEL.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE2\n",
            }
        )
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights-s01.zip",
                "filename": "Blue.Lights.S01.1080p.WEB.h264-ETHEL.zip",
                "release_group": "ETHEL",
                "season": 1,
                "episode": 2,
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        self.assertEqual(result["member"], "Blue.Lights.S01E02.ETHEL.srt")
        self.assertNotIn("episode", result)

    def test_download_zip_defers_when_requested_episode_absent(self):
        # Episode markers present but not the requested one: pinning a wrong-episode member
        # would hard-fail the host download, so defer to host episode selection.
        provider = self.mod.BetaSeriesProvider()
        body = _zip_body(
            {
                "Blue.Lights.S01E01.ETHEL.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE1\n",
                "Blue.Lights.S01E03.ETHEL.srt": b"1\n00:00:01,000 --> 00:00:02,000\nE3\n",
            }
        )
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights-s01.zip",
                "filename": "Blue.Lights.S01.WEB-ETHEL.zip",
                "release_group": "ETHEL",
                "season": 1,
                "episode": 2,
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        self.assertNotIn("member", result)
        self.assertEqual(result["episode"], 2)

    def test_download_zip_matches_release_group_case_insensitively(self):
        # guessit may yield a differently-cased release_group than the filename carries.
        provider = self.mod.BetaSeriesProvider()
        body = _zip_body(
            {
                "Blue.Lights.S01E01.OTHER.srt": b"1\n00:00:01,000 --> 00:00:02,000\nWrong\n",
                "Blue.Lights.S01E01.ethel.srt": b"1\n00:00:01,000 --> 00:00:02,000\nRight\n",
            }
        )
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights.zip",
                "filename": "Blue.Lights.S01E01.WEB-ETHEL.zip",
                "release_group": "ETHEL",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        self.assertEqual(result["member"], "Blue.Lights.S01E01.ethel.srt")

    def test_download_rar_archive_returns_raw_archive_for_host(self):
        provider = self.mod.BetaSeriesProvider()
        # Minimal RAR4 signature; the host extracts, the worker only forwards bytes.
        body = b"Rar!\x1a\x07\x00" + b"\x00" * 32
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "101",
                "download_url": "https://betaseries.test/download/blue-lights.rar",
                "filename": "Blue.Lights.S01E07.rar",
                "season": 1,
                "episode": 7,
            },
            {"alpha3": "fra"},
            {"token": "secret-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(result["episode"], 7)
        self.assertNotIn("content_b64", result)
        self.assertNotIn("encoding", result)

    def test_download_archive_episode_is_none_for_movie(self):
        provider = self.mod.BetaSeriesProvider()
        body = _zip_body({"Some.Movie.2020.720p.srt": b"subtitle"})
        provider._http_get_bytes = lambda url, timeout=10, config=None: body

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "202",
                "download_url": "https://betaseries.test/download/movie.zip",
                "filename": "Some.Movie.2020.720p.zip",
                "season": None,
                "episode": None,
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        self.assertEqual(base64.b64decode(result["archive_b64"]), body)
        self.assertIsNone(result["episode"])

    def test_download_direct_subtitle_normalizes_line_endings(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_bytes = (
            lambda url, timeout=10, config=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nText\r\n"
        )

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "303",
                "download_url": "https://betaseries.test/download/blue-lights.srt",
                "filename": "Blue.Lights.S01E01.srt",
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        decoded = base64.b64decode(result["content_b64"])
        self.assertEqual(decoded, b"1\n00:00:01,000 --> 00:00:02,000\nText\n")
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())
        # Direct content path must not ship a worker-guessed encoding; the host normalizes.
        self.assertNotIn("encoding", result)
        self.assertNotIn("archive_b64", result)

    def test_download_rejects_html_error_page(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_bytes = lambda url, timeout=10, config=None: (
            b"<!DOCTYPE html>\n<html><head><title>404</title></head>"
            b"<body>Subtitle not found</body></html>"
        )

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "betaseries",
                    "schema": 1,
                    "subtitle_id": "404",
                    "download_url": "https://betaseries.test/download/missing.srt",
                    "filename": "missing.srt",
                },
                {"alpha3": "eng"},
                {"token": "secret-key"},
            )

    def test_download_rejects_empty_body(self):
        provider = self.mod.BetaSeriesProvider()
        provider._http_get_bytes = lambda url, timeout=10, config=None: b"   \r\n  "

        with self.assertRaises(ValueError):
            provider.download(
                {
                    "provider": "betaseries",
                    "schema": 1,
                    "subtitle_id": "405",
                    "download_url": "https://betaseries.test/download/empty.srt",
                    "filename": "empty.srt",
                },
                {"alpha3": "eng"},
                {"token": "secret-key"},
            )

    def test_download_404_returns_empty_content_payload(self):
        provider = self.mod.BetaSeriesProvider()

        def raise_404(url, timeout=10, config=None):
            del timeout, config
            error = urllib.error.HTTPError(
                url=url, code=404, msg="Not Found", hdrs=None, fp=io.BytesIO(b""),
            )
            self.addCleanup(error.close)
            raise error

        provider._http_get_bytes = raise_404

        result = provider.download(
            {
                "provider": "betaseries",
                "schema": 1,
                "subtitle_id": "406",
                "download_url": "https://betaseries.test/download/gone.srt",
                "filename": "gone.srt",
            },
            {"alpha3": "eng"},
            {"token": "secret-key"},
        )

        self.assertTrue(result["empty"])
        self.assertEqual(base64.b64decode(result["content_b64"]), b"")


if __name__ == "__main__":
    unittest.main()
