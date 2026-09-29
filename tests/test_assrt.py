import base64
import hashlib
import importlib.util
import json
import time
import urllib.parse
import urllib.request
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "assrt"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "assrt_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUOTA = json.loads((FIXTURE_DIR / "assrt_quota.json").read_text())
INVALID_TOKEN = json.loads((FIXTURE_DIR / "assrt_invalid_token.json").read_text())
SEARCH_RICK = json.loads((FIXTURE_DIR / "assrt_search_rick_morty.json").read_text())
SEARCH_PACK = json.loads((FIXTURE_DIR / "assrt_search_season_pack.json").read_text())
DETAIL_PACK = json.loads((FIXTURE_DIR / "assrt_detail_season_pack.json").read_text())
DETAIL_SINGLE = json.loads((FIXTURE_DIR / "assrt_detail_single_file.json").read_text())
SEARCH_BILINGUAL = {
    "sub": {
        "subs": [
            {
                "id": 73001,
                "videoname": "Example.Movie.2024.1080p.WEB-DL",
                "lang": {"langlist": {"langdou": 1}},
            }
        ]
    }
}
SEARCH_BILINGUAL_NON_ENGLISH = {
    "sub": {
        "subs": [
            {
                "id": 73002,
                "videoname": "Korean.Movie.2024.1080p.WEB-DL",
                "lang": {"langlist": {"langdou": 1, "langkor": 1}},
            }
        ]
    }
}
SEARCH_BILINGUAL_WITH_ENGLISH = {
    "sub": {
        "subs": [
            {
                "id": 73003,
                "videoname": "Example.Movie.2024.1080p.WEB-DL",
                "lang": {"langlist": {"langdou": 1, "langeng": 1}},
            }
        ]
    }
}
DETAIL_ASS = {
    "sub": {
        "subs": [
            {
                "id": 71001,
                "filelist": [
                    {"f": "Rick.and.Morty.S07E10.chs.ass", "url": "https://file0.assrt.net/download/rick-s07e10-chs.ass"}
                ],
            }
        ]
    }
}
DETAIL_MULTI_SEASON_PACK = {
    "sub": {
        "subs": [
            {
                "id": 72002,
                "filelist": [
                    {
                        "f": "Rick.and.Morty.S01E02.1080p.BluRay.x264-STORiES.eng.srt",
                        "url": "https://file0.assrt.net/download/rick-s01e02-eng.srt",
                    },
                    {
                        "f": "Rick.and.Morty.S06E02.1080p.BluRay.x264-STORiES.eng.srt",
                        "url": "https://file0.assrt.net/download/rick-s06e02-eng.srt",
                    },
                ],
            }
        ]
    }
}
DETAIL_PENGUIN_LANGUAGE_PACK = {
    "sub": {
        "subs": [
            {
                "id": 72003,
                "filelist": [
                    {
                        "f": "The.Penguin.S01E01.chs.srt",
                        "url": "https://file0.assrt.net/download/penguin-s01e01-chs.srt",
                    },
                    {
                        "f": "The.Penguin.S01E01.eng.srt",
                        "url": "https://file0.assrt.net/download/penguin-s01e01-eng.srt",
                    },
                ],
            }
        ]
    }
}
DETAIL_PACK_MISSING_EPISODE = {
    "sub": {
        "subs": [
            {
                "id": 72004,
                "filelist": [
                    {
                        "f": "Rick.and.Morty.S06E01.eng.srt",
                        "url": "https://file0.assrt.net/download/rick-s06e01-eng.srt",
                    },
                    {
                        "f": "Rick.and.Morty.S06E03.eng.srt",
                        "url": "https://file0.assrt.net/download/rick-s06e03-eng.srt",
                    },
                ],
            }
        ]
    }
}


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))


class AssrtProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_token_is_required_and_not_returned_in_payload(self):
        provider = self.mod.AssrtProvider()

        with self.assertRaises(ValueError):
            provider.search({"kind": "movie", "title": "Dune"}, [{"alpha3": "eng"}], {})

    def test_api_status_error_is_raised(self):
        with self.assertRaisesRegex(ValueError, "invalid token"):
            self.mod.check_api_status(INVALID_TOKEN)

    def test_search_builds_episode_query_and_returns_requested_languages(self):
        provider = self.mod.AssrtProvider()
        calls = []

        def stub(url, timeout=15, config=None):
            del timeout, config
            calls.append(url)
            return QUOTA if "/user/quota" in url else SEARCH_RICK

        provider._http_get_json = stub
        provider._sleep = lambda seconds: None
        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho", "country": "CN"}, {"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        search_params = _query(calls[1])
        self.assertEqual(search_params["q"], "Rick and Morty S07E10")
        self.assertEqual(search_params["is_file"], "1")
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"zho", "eng"})
        self.assertNotIn("secret-token", json.dumps(results, sort_keys=True))

    def test_search_recognizes_bilingual_language_code(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_BILINGUAL
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "movie", "title": "Example Movie", "year": 2024},
            [{"alpha3": "zho", "country_alpha2": "CN"}, {"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        # "langdou" only reliably implies the Chinese half of a bilingual
        # subtitle, so an English request must not be satisfied by it.
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"zho"})

    def test_search_does_not_treat_bilingual_as_english(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_BILINGUAL_NON_ENGLISH
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "movie", "title": "Korean Movie", "year": 2024},
            [{"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        # A Chinese/Korean bilingual entry carries no English track, so an
        # English-only request must return nothing.
        self.assertEqual(results, [])

    def test_search_advertises_english_when_explicitly_present(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_BILINGUAL_WITH_ENGLISH
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "movie", "title": "Example Movie", "year": 2024},
            [{"alpha3": "zho", "country_alpha2": "CN"}, {"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        # An explicit "langeng" entry alongside "langdou" must still surface both
        # Chinese and English.
        self.assertEqual({item["language"]["alpha3"] for item in results}, {"zho", "eng"})

    def test_search_honors_country_alpha2_for_chinese_variants(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho", "country_alpha2": "TW"}],
            {"token": "secret-token"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["71002"])
        self.assertEqual(results[0]["language"]["country_alpha2"], "TW")

    def test_search_uses_native_name_when_videoname_is_meaningless(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho", "country": "TW"}],
            {"token": "secret-token"},
        )

        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], "71002")
        self.assertIn("720p.WEB", results[0]["release_info"])

    def test_search_marks_season_pack_as_episode_match(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_PACK
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 6, "episode": 2},
            [{"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        self.assertIn("episode", results[0]["matches"])

    def test_download_uses_single_file_detail_url(self):
        provider = self.mod.AssrtProvider()
        calls = []

        def json_stub(url, timeout=15, config=None):
            del timeout, config
            calls.append(url)
            return QUOTA if "/user/quota" in url else DETAIL_SINGLE

        provider._http_get_json = json_stub
        provider._http_get_bytes = lambda url, timeout=15, config=None: b"1\r\n00:00:01,000 --> 00:00:02,000\r\nLine\r\n"
        provider._sleep = lambda seconds: None
        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "71001",
                "language_code": "chs",
                "filename": "rick.srt",
            },
            {"alpha3": "zho", "country": "CN"},
            {"token": "secret-token"},
        )

        self.assertEqual(_query(calls[1])["id"], "71001")
        decoded = base64.b64decode(result["content_b64"])
        self.assertNotIn(b"\r\n", decoded)
        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_sha256"], hashlib.sha256(decoded).hexdigest())

    def test_download_uses_selected_detail_file_extension(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else DETAIL_ASS
        provider._http_get_bytes = lambda url, timeout=15, config=None: b"[Script Info]\r\nTitle: Rick\r\n"
        provider._sleep = lambda seconds: None

        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "71001",
                "language_code": "chs",
                "filename": "rick.srt",
            },
            {"alpha3": "zho", "country_alpha2": "CN"},
            {"token": "secret-token"},
        )

        self.assertEqual(result["format"], "ass")

    def test_download_selects_target_episode_file_from_season_pack(self):
        provider = self.mod.AssrtProvider()
        selected_urls = []

        def json_stub(url, timeout=15, config=None):
            del timeout, config
            return QUOTA if "/user/quota" in url else DETAIL_PACK

        def bytes_stub(url, timeout=15, config=None):
            del timeout, config
            selected_urls.append(url)
            return b"1\n00:00:01,000 --> 00:00:02,000\nEpisode two\n"

        provider._http_get_json = json_stub
        provider._http_get_bytes = bytes_stub
        provider._sleep = lambda seconds: None
        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "72001",
                "language_code": "eng",
                "season": 6,
                "episode": 2,
                "filename": "rick-pack.srt",
            },
            {"alpha3": "eng"},
            {"token": "secret-token"},
        )

        self.assertEqual(selected_urls[0], "https://file0.assrt.net/download/rick-s06e02-eng.srt")
        self.assertIn(b"Episode two", base64.b64decode(result["content_b64"]))

    def test_download_selects_pack_file_by_season_and_episode(self):
        provider = self.mod.AssrtProvider()
        selected_urls = []
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else DETAIL_MULTI_SEASON_PACK

        def bytes_stub(url, timeout=15, config=None):
            del timeout, config
            selected_urls.append(url)
            return b"1\n00:00:01,000 --> 00:00:02,000\nSeason six\n"

        provider._http_get_bytes = bytes_stub
        provider._sleep = lambda seconds: None
        provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "72002",
                "language_code": "eng",
                "season": 6,
                "episode": 2,
                "filename": "rick-pack.srt",
            },
            {"alpha3": "eng"},
            {"token": "secret-token"},
        )

        self.assertEqual(selected_urls[0], "https://file0.assrt.net/download/rick-s06e02-eng.srt")

    def test_download_rejects_pack_without_requested_episode(self):
        provider = self.mod.AssrtProvider()
        selected_urls = []
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else DETAIL_PACK_MISSING_EPISODE
        provider._http_get_bytes = lambda url, timeout=15, config=None: selected_urls.append(url) or b"wrong episode"
        provider._sleep = lambda seconds: None

        with self.assertRaisesRegex(ValueError, "download URL"):
            provider.download(
                {
                    "provider": "assrt",
                    "schema": 1,
                    "subtitle_id": "72004",
                    "language_code": "eng",
                    "season": 6,
                    "episode": 2,
                    "filename": "rick-pack.srt",
                },
                {"alpha3": "eng"},
                {"token": "secret-token"},
            )

        self.assertEqual(selected_urls, [])

    def test_download_matches_language_code_as_a_filename_token(self):
        provider = self.mod.AssrtProvider()
        selected_urls = []
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else DETAIL_PENGUIN_LANGUAGE_PACK
        provider._http_get_bytes = lambda url, timeout=15, config=None: selected_urls.append(url) or b"english"
        provider._sleep = lambda seconds: None

        provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "72003",
                "language_code": "eng",
                "season": 1,
                "episode": 1,
                "filename": "penguin-pack.srt",
            },
            {"alpha3": "eng"},
            {"token": "secret-token"},
        )

        self.assertEqual(selected_urls, ["https://file0.assrt.net/download/penguin-s01e01-eng.srt"])

    def test_download_rejects_standalone_wrong_episode_tags(self):
        for name in ("Show.E03.eng.srt", "Show.Episode3.eng.srt", "Show_Ep03_eng.srt", "Show.S02.E02.eng.srt"):
            with self.subTest(name=name):
                detail = {"sub": {"subs": [{"filelist": [{"f": name, "url": "https://file0.assrt.net/sub"}]}]}}
                self.assertIsNone(self.mod.select_download_file(detail, {"season": 6, "episode": 2, "language_code": "eng"}))
        detail = {"sub": {"subs": [{"filelist": [{"f": "Show.E02.eng.srt", "url": "https://file0.assrt.net/sub"}]}]}}
        self.assertEqual(self.mod.select_download_file(detail, {"episode": 2, "language_code": "eng"})["f"], "Show.E02.eng.srt")

    def test_download_rejects_explicit_other_language_and_allows_unlabelled(self):
        files = [{"f": "Show.S01E01.chs.srt", "url": "https://file0.assrt.net/chinese"}]
        detail = {"sub": {"subs": [{"filelist": files}]}}
        payload = {"season": 1, "episode": 1, "language_code": "eng"}
        self.assertIsNone(self.mod.select_download_file(detail, payload))
        files.append({"f": "Show.S01E01.srt", "url": "https://file0.assrt.net/unlabelled"})
        self.assertEqual(self.mod.select_download_file(detail, payload)["url"], "https://file0.assrt.net/unlabelled")

    def test_search_accepts_declared_chinese_variant_codes(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        # Provider Hub smoke tests can pass the exact manifest code "zho-TW".
        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho-TW"}],
            {"token": "secret-token"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["71002"])
        self.assertEqual(results[0]["language"]["country_alpha2"], "TW")

    def test_search_declared_variant_code_excludes_other_variant(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        # "zho-CN" must not accept the Traditional-only (langcht / 71002) result.
        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho-CN"}],
            {"token": "secret-token"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["71001"])
        self.assertEqual(results[0]["language"]["country_alpha2"], "CN")

    def test_download_payload_includes_content_type_without_encoding_guess(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else DETAIL_ASS
        provider._http_get_bytes = lambda url, timeout=15, config=None: "[Script Info]\nTitle: 你好\n".encode("utf-8")
        provider._sleep = lambda seconds: None

        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "71001",
                "language_code": "chs",
                "filename": "rick.srt",
            },
            {"alpha3": "zho", "country_alpha2": "CN"},
            {"token": "secret-token"},
        )

        self.assertEqual(result["format"], "ass")
        self.assertEqual(result["content_type"], "text/x-ssa")
        self.assertNotIn("encoding", result)

    def test_download_payload_does_not_guess_gbk_encoding(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else DETAIL_SINGLE
        provider._http_get_bytes = lambda url, timeout=15, config=None: "1\n00:00:01,000 --> 00:00:02,000\n你好世界\n".encode("gbk")
        provider._sleep = lambda seconds: None

        result = provider.download(
            {
                "provider": "assrt",
                "schema": 1,
                "subtitle_id": "71001",
                "language_code": "chs",
                "filename": "rick.srt",
            },
            {"alpha3": "zho", "country_alpha2": "CN"},
            {"token": "secret-token"},
        )

        self.assertEqual(result["format"], "srt")
        self.assertEqual(result["content_type"], "application/x-subrip")
        self.assertNotIn("encoding", result)

    def _download_with_detail_url(self, url):
        provider = self.mod.AssrtProvider()
        fetched = []
        detail = {"sub": {"subs": [{"id": 602333, "url": url, "filelist": []}]}}
        provider._http_get_json = lambda u, timeout=15, config=None: QUOTA if "/user/quota" in u else detail
        provider._http_get_bytes = lambda u, timeout=15, config=None: fetched.append(u) or b"1\n"
        provider._sleep = lambda seconds: None
        provider.download(
            {"subtitle_id": "602333", "language_code": "chs", "filename": "x.srt"},
            {"alpha3": "zho", "country_alpha2": "CN"},
            {"token": "secret-token"},
        )
        return fetched

    def test_download_rejects_empty_and_html_bodies(self):
        detail = {"sub": {"subs": [{"id": 602333, "url": "https://file0.assrt.net/x.srt", "filelist": []}]}}
        for body in (
            b"",
            b" \r\n\t",
            b"<!DOCTYPE html><html><body>Quota exceeded</body></html>",
            b"\xef\xbb\xbf\r\n<html><head><title>Login</title></head></html>",
            b"<body>File expired</body>",
        ):
            with self.subTest(body=body):
                provider = self.mod.AssrtProvider()
                provider._http_get_json = lambda u, timeout=15, config=None: QUOTA if "/user/quota" in u else detail
                provider._http_get_bytes = lambda u, timeout=15, config=None, body=body: body
                provider._sleep = lambda seconds: None
                with self.assertRaisesRegex(ValueError, "empty file|HTML page"):
                    provider.download(
                        {"subtitle_id": "602333", "language_code": "chs", "filename": "x.srt"},
                        {"alpha3": "zho", "country_alpha2": "CN"},
                        {"token": "secret-token"},
                    )

    def test_download_rejects_urls_outside_the_assrt_file_hosts(self):
        for url in ("http://127.0.0.1/admin", "https://example.com/x.srt", "https://assrt.net.example.com/x.srt"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "outside the Assrt file hosts"):
                    self._download_with_detail_url(url)

    def test_download_fetches_documented_http_file_url_over_https(self):
        # The Assrt API documents file0.assrt.net links as plain http.
        fetched = self._download_with_detail_url("http://file0.assrt.net/onthefly/602333/-/1/x.srt?api=1")

        self.assertEqual(fetched, ["https://file0.assrt.net/onthefly/602333/-/1/x.srt?api=1"])

    def test_redirects_are_followed_only_to_https_assrt_hosts(self):
        handler = self.mod._AssrtRedirectHandler()
        request = urllib.request.Request("https://file0.assrt.net/onthefly/602333/-/1/x.srt")

        for target in ("http://127.0.0.1/admin", "http://assrt.net/download/failed/x", "https://example.com/x.srt"):
            with self.subTest(target=target):
                self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, target))
        followed = handler.redirect_request(request, None, 302, "Found", {}, "https://assrt.net/download/failed/x")
        self.assertEqual(followed.full_url, "https://assrt.net/download/failed/x")

    def test_search_drops_rows_for_a_different_episode(self):
        search = {
            "sub": {
                "subs": [
                    {"id": 74001, "videoname": "Rick.and.Morty.S06E03.1080p.WEB.h264-ETHEL", "lang": {"langlist": {"langeng": 1}}},
                    {"id": 74002, "videoname": "Rick.and.Morty.S06E02.1080p.WEB.h264-ETHEL", "lang": {"langlist": {"langeng": 1}}},
                ]
            }
        }
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else search
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 6, "episode": 2},
            [{"alpha3": "eng"}],
            {"token": "secret-token"},
        )

        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], ["74002"])

    def _search_release_names(self, names, season, episode):
        search = {
            "sub": {
                "subs": [
                    {"id": 75000 + index, "videoname": name, "lang": {"langlist": {"langeng": 1}}}
                    for index, name in enumerate(names)
                ]
            }
        }
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else search
        provider._sleep = lambda seconds: None
        results = provider.search(
            {"kind": "episode", "series": "Show", "season": season, "episode": episode},
            [{"alpha3": "eng"}],
            {"token": "secret-token"},
        )
        return [item["release_info"] for item in results]

    def test_search_keeps_multi_episode_rows_only_for_the_episodes_they_give(self):
        cases = (
            ("Show.S01E01-E02.1080p.WEB", 2, True),
            ("Show.S01E01-02.1080p.WEB", 2, True),
            ("Show.S01E01.E02.1080p.WEB", 2, True),
            ("Show.S01E01E02.1080p.WEB", 1, True),
            ("Show.S01E01E02.1080p.WEB", 2, True),
            ("Show.S01E01E02.1080p.WEB", 3, False),
            ("Show.S01E01+E02.1080p.WEB", 2, True),
            # A range covers the episodes between its ends; a chained tag lists only its own.
            ("Show.S01E01-E03.1080p.WEB", 2, True),
            ("Show.S01E01E03.1080p.WEB", 2, False),
            ("Show.S01E01-E24.1080p.WEB", 12, True),
            ("Show.S01E01-E24.1080p.WEB", 25, False),
            # A resolution after a hyphen is not the end of a range.
            ("Show.S01E01-720p.WEB", 2, False),
            ("Show.S01E01-1080p.WEB", 2, False),
            ("Show.S01E02-720p.WEB", 2, True),
            ("Show.S02E01-E02.1080p.WEB", 2, False),
            # A spaced hyphen is a title separator unless an E follows it.
            ("Show.S01E05 - 10 Things I Hate", 5, True),
            ("Show.S01E05 - 10 Things I Hate", 7, False),
            ("Show.S01E01 - E03.1080p.WEB", 2, True),
            # Tags that touch a Chinese title, an underscore or a split season still count.
            ("剧集S01E01E02中英字幕", 2, True),
            ("剧集S01E01E02中英字幕", 3, False),
            ("Show_S01E03_1080p", 2, False),
            ("Show.S01.E03.1080p.WEB", 2, False),
            ("Show.S01.E02.1080p.WEB", 2, True),
            ("Show.S01-E03.1080p.WEB", 2, False),
            ("Show.S01 - E02 - Title", 2, True),
            # A tilde reads as a hyphen, in its ASCII, full-width and wave-dash
            # forms, and a comma chains like a dot.
            ("Show.S01E01~E03.1080p.WEB", 2, True),
            ("Show.S01E01~03.1080p.WEB", 2, True),
            ("Show.S01E01～E02.1080p.WEB", 2, True),
            ("剧集S01E01〜E03中字", 2, True),
            ("剧集S01E01〜E03中字", 4, False),
            ("Show.S01E05 ~ 10 Things I Hate", 7, False),
            ("Show.S01E01,E02.1080p.WEB", 2, True),
            ("Show.S01E01,E03.1080p.WEB", 2, False),
            # Chinese names join a range with "至" or "到" ("to"), and some use
            # an en or em dash.
            ("剧集S01E01至E05中字", 3, True),
            ("剧集S01E01至E05中字", 6, False),
            ("剧集S01E01到E05", 5, True),
            ("剧集S01E01至05中字", 4, True),
            ("Show.S01E01\N{EN DASH}E05.1080p.WEB", 3, True),
            ("Show.S01E01\N{EM DASH}E05.1080p.WEB", 3, True),
            ("Show.S01E01\N{EN DASH}E05.1080p.WEB", 6, False),
            ("Show.S01E05 \N{EN DASH} 10 Things I Hate", 7, False),
        )
        for name, episode, kept in cases:
            with self.subTest(name=name, episode=episode):
                self.assertEqual(self._search_release_names([name], 1, episode), [name] if kept else [])

    def test_search_reads_only_the_ends_of_a_very_long_range(self):
        name = "Show.S01E01-E150.WEB"

        self.assertEqual(self._search_release_names([name], 1, 150), [name])
        self.assertEqual(self._search_release_names([name], 1, 75), [])

    def test_long_chained_tags_from_uploaders_are_read_quickly(self):
        # Zero-padded continuations must each parse one way. When they did not,
        # every extra "E001" tripled the time, and names this long took most of
        # a minute, past the host's worker deadline.
        chain = "E001" * 14
        detail = {"sub": {"subs": [{"filelist": [{"f": f"Ep01{chain.lower()}x.srt", "url": "https://file0.assrt.net/x"}]}]}}

        started = time.perf_counter()
        self._search_release_names([f"Show.S01E01{chain}x"], 1, 2)
        self.mod.select_download_file(detail, {"season": 1, "episode": 2, "language_code": "eng"})

        self.assertLess(time.perf_counter() - started, 1.0)

    def test_derive_matches_reads_the_season_from_contiguous_tags(self):
        video = {"kind": "episode", "series": "Rick and Morty", "season": 6, "episode": 2}
        for name in (
            "Rick.and.Morty.S06E02.1080p.WEB",
            "Rick.and.Morty.S06E01E02.1080p.WEB",
            "Rick.and.Morty.S06E01-E02.1080p.WEB",
            "瑞克和莫蒂S06E02中英",
        ):
            with self.subTest(name=name):
                matches = self.mod.derive_matches(video, name)
                self.assertIn("season", matches)
                self.assertIn("episode", matches)
        self.assertNotIn("season", self.mod.derive_matches(video, "Rick.and.Morty.S16E02.1080p.WEB"))

    def test_derive_matches_gives_no_episode_to_a_tag_it_cannot_read(self):
        # These tags still name one episode, so they must not pass as a season
        # pack that fits every episode of the season.
        video = {"kind": "episode", "series": "Show", "season": 1, "episode": 5}
        for name in (
            "Show.S01E01v2.1080p",
            "Show.S01E01E02v2.1080p",
            "Show.S01E01a.1080p",
            "Show.S01E01HDTV",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.mod.derive_matches(video, name), ["series"])
        self.assertEqual(self.mod.derive_matches(video, "Show.S01.E01v2.1080p"), ["series", "season"])
        self.assertEqual(self.mod.derive_matches(video, "Show.S01-E01.1080p"), ["series", "season"])
        self.assertEqual(self.mod.derive_matches(video, "Show.S01-E05.1080p"), ["series", "season", "episode"])
        for name in ("Show.S01.1080p.WEB", "Show.Season.1.Complete", "Show.S01中英字幕"):
            with self.subTest(name=name):
                self.assertEqual(self.mod.derive_matches(video, name), ["series", "season", "episode"])

    def test_download_reads_a_pack_member_by_its_own_name_before_its_folder(self):
        for separator in ("/", "\\"):
            files = [
                {
                    "f": f"Show.S01E01-E10{separator}Show.S01E{episode:02d}.eng.srt",
                    "url": f"https://file0.assrt.net/e{episode:02d}",
                }
                for episode in range(1, 11)
            ]
            detail = {"sub": {"subs": [{"filelist": files}]}}
            for episode in (1, 5, 10):
                with self.subTest(separator=separator, episode=episode):
                    selected = self.mod.select_download_file(detail, {"season": 1, "episode": episode, "language_code": "eng"})
                    self.assertEqual(selected["url"], f"https://file0.assrt.net/e{episode:02d}")
        # A member whose own name gives no episode is read by its folder.
        files = [
            {"f": "Show.S01E03/Show.eng.srt", "url": "https://file0.assrt.net/folder-e03"},
            {"f": "Show.S01E04/Show.eng.srt", "url": "https://file0.assrt.net/folder-e04"},
            {"f": "Season 1/Show.E06.eng.srt", "url": "https://file0.assrt.net/season1-e06"},
            {"f": "Season 2/Show.E06.eng.srt", "url": "https://file0.assrt.net/season2-e06"},
        ]
        detail = {"sub": {"subs": [{"filelist": files}]}}
        expected = {(1, 4): "folder-e04", (1, 6): "season1-e06", (2, 6): "season2-e06", (1, 5): None}
        for (season, episode), url in expected.items():
            with self.subTest(season=season, episode=episode):
                selected = self.mod.select_download_file(detail, {"season": season, "episode": episode, "language_code": "eng"})
                if url is None:
                    self.assertIsNone(selected)
                else:
                    self.assertEqual(selected["url"], f"https://file0.assrt.net/{url}")
        # A member named only by its episode takes the season from the tag on
        # its folder, whether that tag names one episode or a range.
        files = [
            {"f": "Show.S02E05/Show.E05.eng.srt", "url": "https://file0.assrt.net/s02-e05"},
            {"f": "Show.S01E05/Show.E05.eng.srt", "url": "https://file0.assrt.net/s01-e05"},
            {"f": "Show.S04E01-E10/Show.E07.eng.srt", "url": "https://file0.assrt.net/s04-e07"},
            {"f": "Show.S03E01-E10/Show.E07.eng.srt", "url": "https://file0.assrt.net/s03-e07"},
        ]
        detail = {"sub": {"subs": [{"filelist": files}]}}
        expected = {(1, 5): "s01-e05", (2, 5): "s02-e05", (3, 7): "s03-e07", (4, 7): "s04-e07", (3, 5): None}
        for (season, episode), url in expected.items():
            with self.subTest(season=season, episode=episode):
                selected = self.mod.select_download_file(detail, {"season": season, "episode": episode, "language_code": "eng"})
                if url is None:
                    self.assertIsNone(selected)
                else:
                    self.assertEqual(selected["url"], f"https://file0.assrt.net/{url}")

    def test_download_picks_a_multi_episode_pack_member_for_each_episode_it_lists(self):
        files = [
            {"f": "Show.S01E01E02.eng.srt", "url": "https://file0.assrt.net/s01e01e02"},
            {"f": "Show.S01E03-E04.eng.srt", "url": "https://file0.assrt.net/s01e03-e04"},
            {"f": "Show.E05E06.eng.srt", "url": "https://file0.assrt.net/e05e06"},
            {"f": "Show_Ep07-08_eng.srt", "url": "https://file0.assrt.net/ep07-08"},
            # No member names E10, so the one whose range spans it answers, as
            # search already offered this subtitle for E10.
            {"f": "Show.S01E09-E11.eng.srt", "url": "https://file0.assrt.net/s01e09-e11"},
        ]
        detail = {"sub": {"subs": [{"filelist": files}]}}
        expected = {
            1: "s01e01e02",
            2: "s01e01e02",
            3: "s01e03-e04",
            4: "s01e03-e04",
            5: "e05e06",
            6: "e05e06",
            7: "ep07-08",
            8: "ep07-08",
            9: "s01e09-e11",
            10: "s01e09-e11",
            11: "s01e09-e11",
            12: None,
        }
        for episode, url in expected.items():
            with self.subTest(episode=episode):
                selected = self.mod.select_download_file(detail, {"season": 1, "episode": episode, "language_code": "eng"})
                if url is None:
                    self.assertIsNone(selected)
                else:
                    self.assertEqual(selected["url"], f"https://file0.assrt.net/{url}")
        self.assertIsNone(self.mod.select_download_file(detail, {"season": 2, "episode": 2, "language_code": "eng"}))

    def test_download_prefers_a_member_naming_the_episode_over_a_range_spanning_it(self):
        files = [
            {"f": "Show.S01E01-E03.eng.srt", "url": "https://file0.assrt.net/s01e01-e03"},
            {"f": "Show.S01E02.eng.srt", "url": "https://file0.assrt.net/s01e02"},
        ]
        detail = {"sub": {"subs": [{"filelist": files}]}}

        selected = self.mod.select_download_file(detail, {"season": 1, "episode": 2, "language_code": "eng"})

        self.assertEqual(selected["url"], "https://file0.assrt.net/s01e02")

    def test_download_prefers_a_member_made_for_the_episode_alone_over_a_combined_one(self):
        # A combined subtitle is timed for the joined video, so it only answers
        # when no member was made for the episode alone.
        packs = (
            (
                ["Show.S01E01E02.eng.srt", "Show.S01E01.eng.srt", "Show.S01E02.eng.srt"],
                {1: "Show.S01E01.eng.srt", 2: "Show.S01E02.eng.srt", 3: None},
            ),
            (
                ["Show.S01E01-E03.eng.srt", "Show.S01E03.eng.srt"],
                {1: "Show.S01E01-E03.eng.srt", 2: "Show.S01E01-E03.eng.srt", 3: "Show.S01E03.eng.srt"},
            ),
            (
                ["Show.S01E01-E03.eng.srt", "Show.S01E01E02.eng.srt", "Show.S01E03.eng.srt"],
                {1: "Show.S01E01-E03.eng.srt", 2: "Show.S01E01E02.eng.srt", 3: "Show.S01E03.eng.srt"},
            ),
        )
        for names, expected in packs:
            detail = {"sub": {"subs": [{"filelist": [{"f": name, "url": f"https://file0.assrt.net/{name}"} for name in names]}]}}
            for episode, name in expected.items():
                with self.subTest(names=names, episode=episode):
                    selected = self.mod.select_download_file(detail, {"season": 1, "episode": episode, "language_code": "eng"})
                    if name is None:
                        self.assertIsNone(selected)
                    else:
                        self.assertEqual(selected["f"], name)

    def test_download_falls_back_to_a_wider_member_in_the_requested_language(self):
        files = [
            {"f": "Show.S01E01-E03.eng.srt", "url": "https://file0.assrt.net/s01e01-e03-eng"},
            {"f": "Show.S01E01E02.cht.srt", "url": "https://file0.assrt.net/s01e01e02-cht"},
            {"f": "Show.S01E02.chs.srt", "url": "https://file0.assrt.net/s01e02-chs"},
        ]
        detail = {"sub": {"subs": [{"filelist": files}]}}
        expected = {"eng": "s01e01-e03-eng", "cht": "s01e01e02-cht", "chs": "s01e02-chs"}
        for language_code, url in expected.items():
            with self.subTest(language_code=language_code):
                selected = self.mod.select_download_file(detail, {"season": 1, "episode": 2, "language_code": language_code})
                self.assertEqual(selected["url"], f"https://file0.assrt.net/{url}")

    def test_download_fetches_a_lone_range_member_for_an_episode_inside_it(self):
        detail = {
            "sub": {
                "subs": [
                    {
                        "id": 75102,
                        "filelist": [
                            {"f": "Show.S01E09-E11.eng.srt", "url": "https://file0.assrt.net/download/s01e09-e11-eng.srt"},
                        ],
                    }
                ]
            }
        }
        provider = self.mod.AssrtProvider()
        fetched = []
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else detail
        provider._http_get_bytes = lambda url, timeout=15, config=None: fetched.append(url) or b"1\n00:00:01,000 --> 00:00:02,000\nTen\n"
        provider._sleep = lambda seconds: None

        provider.download(
            {"subtitle_id": "75102", "language_code": "eng", "season": 1, "episode": 10, "filename": "show.srt"},
            {"alpha3": "eng"},
            {"token": "secret-token"},
        )

        self.assertEqual(fetched, ["https://file0.assrt.net/download/s01e09-e11-eng.srt"])

    def test_download_fetches_the_two_episode_member_for_its_second_episode(self):
        detail = {
            "sub": {
                "subs": [
                    {
                        "id": 75101,
                        "filelist": [
                            {"f": "Show.S01E01E02.eng.srt", "url": "https://file0.assrt.net/download/s01e01e02-eng.srt"},
                            {"f": "Show.S01E03.eng.srt", "url": "https://file0.assrt.net/download/s01e03-eng.srt"},
                        ],
                    }
                ]
            }
        }
        provider = self.mod.AssrtProvider()
        fetched = []
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else detail
        provider._http_get_bytes = lambda url, timeout=15, config=None: fetched.append(url) or b"1\n00:00:01,000 --> 00:00:02,000\nTwo\n"
        provider._sleep = lambda seconds: None

        provider.download(
            {"subtitle_id": "75101", "language_code": "eng", "season": 1, "episode": 2, "filename": "show.srt"},
            {"alpha3": "eng"},
            {"token": "secret-token"},
        )

        self.assertEqual(fetched, ["https://file0.assrt.net/download/s01e01e02-eng.srt"])

    def test_search_skips_forced_only_and_hi_only_requests(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        # Assrt exposes no forced or HI metadata, so it cannot satisfy either variant.
        for flag in ("forced", "hi"):
            with self.subTest(flag=flag):
                results = provider.search(
                    {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
                    [{"alpha3": "eng", flag: True}],
                    {"token": "secret-token"},
                )
                self.assertEqual(results, [])

    def test_search_emits_country_alpha2_for_chinese_variants(self):
        provider = self.mod.AssrtProvider()
        provider._http_get_json = lambda url, timeout=15, config=None: QUOTA if "/user/quota" in url else SEARCH_RICK
        provider._sleep = lambda seconds: None

        results = provider.search(
            {"kind": "episode", "series": "Rick and Morty", "season": 7, "episode": 10},
            [{"alpha3": "zho", "country_alpha2": "CN"}, {"alpha3": "zho", "country_alpha2": "TW"}],
            {"token": "secret-token"},
        )

        # The host builds the result Language from country_alpha2 only.
        self.assertEqual(
            {item["provider_payload"]["subtitle_id"]: item["language"].get("country_alpha2") for item in results},
            {"71001": "CN", "71002": "TW"},
        )
        self.assertFalse(any("country" in item["language"] for item in results))


if __name__ == "__main__":
    unittest.main()
