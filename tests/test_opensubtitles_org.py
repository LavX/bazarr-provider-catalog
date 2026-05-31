import base64
import hashlib
import importlib.util
import json
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "opensubtitles_org"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "opensubtitles_org_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRAPER_SEARCH_TV = json.loads(
    (FIXTURE_DIR / "opensubtitles_scraper_search_tv.json").read_text(encoding="utf-8")
)
SCRAPER_SUBTITLES_TV = json.loads(
    (FIXTURE_DIR / "opensubtitles_scraper_subtitles_tv.json").read_text(encoding="utf-8")
)
SCRAPER_DOWNLOAD = json.loads(
    (FIXTURE_DIR / "opensubtitles_scraper_download.json").read_text(encoding="utf-8")
)
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nWinter is coming.\n"


EPISODE_VIDEO = {
    "kind": "episode",
    "series": "Game of Thrones",
    "title": "Winter Is Coming",
    "year": 2011,
    "season": 1,
    "episode": 1,
    "series_imdb_id": "tt0944947",
    "imdb_id": "tt1480055",
    "fps": "23.976",
    "size": 234567890,
    "hashes": {"opensubtitles": "9f8e7d6c5b4a3210"},
    "original_name": "Game.of.Thrones.S01E01.1080p.WEB-DL",
}


MOVIE_VIDEO = {
    "kind": "movie",
    "title": "Dune",
    "year": 2021,
    "imdb_id": "tt1160419",
    "fps": "23.976",
    "size": 345678901,
    "hashes": {"opensubtitles": "0011223344556677"},
    "original_name": "Dune.2021.1080p.BluRay",
}


class ApiCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_episode_api_criteria_preserve_hash_tag_imdb_and_languages(self):
        context = self.mod.build_search_context(
            EPISODE_VIDEO, {"use_tag_search": True}
        )

        criteria = self.mod.build_xmlrpc_criteria(
            context,
            [{"alpha3": "hun", "alpha2": "hu"}, {"alpha3": "eng", "alpha2": "en"}],
        )

        self.assertEqual(
            criteria,
            [
                {
                    "moviehash": "9f8e7d6c5b4a3210",
                    "moviebytesize": "234567890",
                    "sublanguageid": "eng,hun",
                },
                {
                    "tag": "Game.of.Thrones.S01E01.1080p.WEB-DL",
                    "sublanguageid": "eng,hun",
                },
                {
                    "imdbid": "0944947",
                    "season": 1,
                    "episode": 1,
                    "sublanguageid": "eng,hun",
                },
            ],
        )

    def test_movie_api_criteria_use_movie_imdb_without_episode_fields(self):
        context = self.mod.build_search_context(MOVIE_VIDEO, {"use_tag_search": False})

        criteria = self.mod.build_xmlrpc_criteria(context, [{"alpha3": "eng"}])

        self.assertEqual(
            criteria,
            [
                {
                    "moviehash": "0011223344556677",
                    "moviebytesize": "345678901",
                    "sublanguageid": "eng",
                },
                {"imdbid": "1160419", "sublanguageid": "eng"},
            ],
        )


class ScraperModeTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_uses_health_tv_result_selection_and_language_mapping(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def get_json(url, timeout=15):
            calls.append(("GET", url, timeout))
            self.assertEqual(url, "http://helper:8000/health")
            return {"ok": True}

        def post_json(url, payload, timeout=120):
            calls.append(("POST", url, payload, timeout))
            if url == "http://helper:8000/api/v1/search/tv":
                return SCRAPER_SEARCH_TV
            if url == "http://helper:8000/api/v1/subtitles":
                return SCRAPER_SUBTITLES_TV
            raise AssertionError(f"unexpected URL: {url}")

        provider._http_get_json = get_json
        provider._http_post_json = post_json

        results = provider.search(
            EPISODE_VIDEO,
            [
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "hun", "alpha2": "hu", "hi": True},
            ],
            {
                "use_web_scraper": True,
                "scraper_service_url": "helper:8000",
                "skip_wrong_fps": True,
            },
        )

        self.assertEqual(calls[0], ("GET", "http://helper:8000/health", 15))
        self.assertEqual(
            calls[1],
            (
                "POST",
                "http://helper:8000/api/v1/search/tv",
                {
                    "query": "Game of Thrones",
                    "imdb_id": "tt0944947",
                    "year": 2011,
                    "kind": "episode",
                },
                120,
            ),
        )
        self.assertEqual(
            calls[2],
            (
                "POST",
                "http://helper:8000/api/v1/subtitles",
                {
                    "movie_url": "https://www.opensubtitles.org/en/search/sublanguageid-all/idmovie-121361",
                    "languages": ["en", "hu"],
                    "season": 1,
                    "episode": 1,
                },
                120,
            ),
        )
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["provider"], "opensubtitles_org")
        self.assertEqual(first["provider_payload"]["legacy_provider_id"], "opensubtitles")
        self.assertEqual(first["provider_payload"]["mode"], "scraper")
        self.assertEqual(first["provider_payload"]["subtitle_id"], "1952619105")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("series", first["matches"])
        self.assertIn("season", first["matches"])
        self.assertIn("episode", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertEqual(first["display"]["download_count"], 4312)

    def test_only_foreign_keeps_forced_subtitles_and_marks_language(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        provider._http_get_json = lambda url, timeout=15: {"ok": True}
        provider._http_post_json = lambda url, payload, timeout=120: (
            SCRAPER_SEARCH_TV
            if url.endswith("/api/v1/search/tv")
            else SCRAPER_SUBTITLES_TV
        )

        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en", "forced": True}],
            {
                "use_web_scraper": True,
                "scraper_service_url": "http://helper:8000",
                "only_foreign": True,
            },
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "1952619106")
        self.assertTrue(results[0]["language"]["forced"])

    def test_scraper_download_decodes_base64_payload(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        calls = []

        def post_json(url, payload, timeout=120):
            calls.append((url, payload, timeout))
            self.assertEqual(url, "http://helper:8000/api/v1/download/subtitle")
            return SCRAPER_DOWNLOAD

        provider._http_post_json = post_json

        result = provider.download(
            {
                "provider": "opensubtitles_org",
                "schema": 1,
                "mode": "scraper",
                "subtitle_id": "1952619105",
                "download_url": "https://www.opensubtitles.org/en/subtitles/1952619105",
            },
            {"alpha3": "eng", "alpha2": "en"},
            {
                "use_web_scraper": True,
                "scraper_service_url": "helper:8000",
            },
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(calls[0][1]["subtitle_id"], "1952619105")
        self.assertEqual(data, SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())


class XmlRpcModeTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_api_search_parses_forced_hi_and_filters_unrequested_languages(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        provider._api_token = "token"
        provider._api_server = object()
        api_items = [
            {
                "SubLanguageID": "eng",
                "SubHearingImpaired": "0",
                "SubtitlesLink": "https://www.opensubtitles.org/en/subtitles/111",
                "IDSubtitleFile": "111",
                "MatchedBy": "tag",
                "MovieKind": "episode",
                "MovieHash": "9f8e7d6c5b4a3210",
                "MovieName": "\"Game of Thrones\" Winter Is Coming",
                "MovieReleaseName": "Game.of.Thrones.S01E01.1080p.WEB-DL",
                "MovieYear": "2011",
                "MovieFPS": "23.976",
                "SeriesIMDBParent": "0944947",
                "IDMovieImdb": "1480055",
                "SeriesSeason": "1",
                "SeriesEpisode": "1",
                "SubFileName": "Game.of.Thrones.S01E01.srt",
                "SubEncoding": "utf-8",
                "SubForeignPartsOnly": "0",
                "UserNickName": "syncmaster",
                "SubDownloadsCnt": "1234",
            },
            {
                "SubLanguageID": "eng",
                "SubHearingImpaired": "0",
                "SubtitlesLink": "https://www.opensubtitles.org/en/subtitles/112",
                "IDSubtitleFile": "112",
                "MatchedBy": "imdbid",
                "MovieKind": "episode",
                "MovieHash": "",
                "MovieName": "\"Game of Thrones\" Winter Is Coming",
                "MovieReleaseName": "Game.of.Thrones.S01E01.Forced",
                "MovieYear": "2011",
                "MovieFPS": "23.976",
                "SeriesIMDBParent": "0944947",
                "IDMovieImdb": "1480055",
                "SeriesSeason": "1",
                "SeriesEpisode": "1",
                "SubFileName": "Game.of.Thrones.S01E01.Forced.srt",
                "SubEncoding": "",
                "SubForeignPartsOnly": "1",
                "UserNickName": "",
                "SubDownloadsCnt": "55",
            },
        ]

        candidates = provider.parse_api_subtitles(
            api_items,
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            self.mod.build_search_context(EPISODE_VIDEO, {"use_tag_search": True}),
            {"also_foreign": False, "skip_wrong_fps": True},
        )

        self.assertEqual(len(candidates), 1)
        first = candidates[0]
        self.assertEqual(first["provider_payload"]["mode"], "api")
        self.assertEqual(first["provider_payload"]["subtitle_id"], "111")
        self.assertEqual(first["uploader"], "syncmaster")
        self.assertIn("hash", first["matches"])
        self.assertIn("imdb_id", first["matches"])
        self.assertEqual(first["display"]["download_count"], 1234)

    def test_api_download_uses_xmlrpc_and_decompresses_response(self):
        provider = self.mod.OpenSubtitlesOrgProvider()
        provider._api_token = "token"

        class FakeServer:
            def __init__(self):
                self.calls = []

            def DownloadSubtitles(self, token, subtitle_ids):
                self.calls.append((token, subtitle_ids))
                compressed = zlib.compress(SRT_BODY)
                return {
                    "status": "200 OK",
                    "data": [
                        {
                            "data": base64.b64encode(compressed).decode("ascii"),
                        }
                    ],
                }

        fake_server = FakeServer()
        provider._api_server = fake_server

        result = provider.download(
            {
                "provider": "opensubtitles_org",
                "schema": 1,
                "mode": "api",
                "subtitle_id": "111",
            },
            {"alpha3": "eng"},
            {"use_web_scraper": False},
        )

        data = base64.b64decode(result["content_b64"].encode("ascii"), validate=True)
        self.assertEqual(fake_server.calls, [("token", ["111"])])
        self.assertEqual(data, SRT_BODY)


if __name__ == "__main__":
    unittest.main()
