import base64
import io
import hashlib
import importlib.util
import json
import lzma
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from urllib.response import addinfourl
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "tsukihime"
API = "https://api.tsukihime.org/v1"
STORE = "https://storage.tsukihime.org"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "tsukihime_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(body):
    return json.dumps(body).encode("utf-8")


def _attachment(attachment_id, language, codec="srt", name="English", **values):
    return {
        "id": attachment_id,
        "type": 1,
        "info": {
            "cached": values.pop("cached", 1),
            "codec": codec,
            "lang": language,
            "name": name,
            "forced": values.pop("forced", 0),
            **values,
        },
    }


def _http_response(url, code=200, body=b"", location=None):
    headers = Message()
    if location:
        headers["Location"] = location
    response = addinfourl(io.BytesIO(body), headers, url, code)
    response.msg = "Found" if code == 302 else "OK"
    return response


class TsukiHimeProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, responses):
        provider = self.mod.TsukiHimeProvider()
        calls = []

        def get(url, timeout=15, max_bytes=None):
            del timeout, max_bytes
            calls.append(url)
            value = responses[url]
            if isinstance(value, BaseException):
                raise value
            return value

        provider._http_get = get
        return provider, calls

    def test_episode_search_uses_anidb_series_and_episode_ids(self):
        video = {
            "kind": "episode",
            "series": "One Piece",
            "season": 1,
            "episode": 1171,
            "year": 1999,
            "series_anidb_id": [68, 69],
            "series_anidb_episode_id": [1170, 1171],
            "series_anidb_episode_no": 1171,
            "name": "One.Piece.S01E1171.1080p.WEB-DL.mkv",
        }
        responses = {
            f"{API}/animes/anidb/69": _json({"id": 2086, "release_year": 1999}),
            f"{API}/animes/2086/episodes/1171": _json(
                {
                    "results": [
                        {
                            "id": 301,
                            "name": "One.Piece.S01E1171.1080p.WEB-DL",
                            "state": "completed",
                            "sublangs": ["en"],
                            "source_date": 1,
                        }
                    ]
                }
            ),
            f"{API}/torrents/301": _json(
                {
                    "files": [
                        {
                            "filename": "One.Piece.S01E1171.1080p.WEB-DL.mkv",
                            "attachments": [_attachment(133226, "en")],
                        }
                    ]
                }
            ),
        }
        provider, calls = self._provider(responses)

        results = provider.search(
            video,
            [{"alpha3": "eng", "alpha2": "en", "forced": False, "hi": False}],
            {},
        )

        self.assertEqual(
            calls,
            [
                f"{API}/animes/anidb/69",
                f"{API}/animes/2086/episodes/1171",
                f"{API}/torrents/301",
            ],
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["provider"], "tsukihime")
        self.assertEqual(result["language"]["alpha3"], "eng")
        self.assertEqual(
            result["page_link"], f"{STORE}/attach/0002086A/133226.xz"
        )
        self.assertTrue({"series", "season", "episode", "year"}.issubset(result["matches"]))

    def test_movie_search_uses_anilist_id_and_selects_the_best_file(self):
        video = {
            "kind": "movie",
            "title": "Summer Pockets Season 1 Omnibus",
            "year": 2025,
            "anilist_id": "195230",
            "name": "Summer.Pockets.Season.1.Omnibus.1080p.BluRay.mkv",
        }
        responses = {
            f"{API}/animes/anilist/195230": _json({"id": 400, "release_year": 2025}),
            f"{API}/animes/400": _json(
                {
                    "results": [
                        {
                            "id": 401,
                            "name": "Summer.Pockets.Season.1.Omnibus.1080p.BluRay",
                            "state": "completed",
                            "sublangs": ["en"],
                            "source_date": 1,
                            "animetosho": True,
                        }
                    ]
                }
            ),
            f"{API}/torrents/401": _json(
                {
                    "files": [
                        {
                            "filename": "Summer.Pockets.Season.1.Omnibus.mkv",
                            "attachments": [_attachment(65537, "en", "ass")],
                        },
                        {
                            "filename": "Random.Extra.Track.mkv",
                            "attachments": [_attachment(65538, "en")],
                        },
                    ]
                }
            ),
        }
        provider, calls = self._provider(responses)

        results = provider.search(video, [{"alpha3": "eng", "alpha2": "en"}], {})

        self.assertEqual(calls[0], f"{API}/animes/anilist/195230")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["attachment_id"], 65537)
        self.assertEqual(results[0]["format"], "ass")
        self.assertEqual(
            results[0]["page_link"], f"{STORE}/tosho/attach/00010001/65537.xz"
        )
        self.assertTrue({"title", "year"}.issubset(results[0]["matches"]))

    def test_shared_attachment_keeps_each_release_association(self):
        video = {
            "kind": "movie",
            "title": "Summer Pockets",
            "anilist_id": 195230,
            "name": "Summer.Pockets.mkv",
        }
        listing = {
            "results": [
                {"id": 1, "name": "Summer Pockets First", "state": "completed", "sublangs": ["en"]},
                {"id": 2, "name": "Summer Pockets Second", "state": "completed", "sublangs": ["en"]},
            ]
        }
        detail = {
            "files": [
                {"filename": "Summer.Pockets.mkv", "attachments": [_attachment(10, "en")]}
            ]
        }
        provider, _ = self._provider(
            {
                f"{API}/animes/anilist/195230": _json({"id": 400, "release_year": 2025}),
                f"{API}/animes/400": _json(listing),
                f"{API}/torrents/1": _json(detail),
                f"{API}/torrents/2": _json(detail),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "alpha2": "en"}], {})

        self.assertEqual(len(results), 2)
        self.assertEqual({result["provider_payload"]["release_id"] for result in results}, {1, 2})
        self.assertEqual(len({result["id"] for result in results}), 2)
        self.assertEqual(len({result["page_link"] for result in results}), 1)

    def test_episode_file_matching_checks_season_and_keeps_absolute_episode_numbers(self):
        video = {
            "kind": "episode",
            "series": "One Piece",
            "season": 1,
            "episode": 5,
            "series_anidb_id": 69,
            "series_anidb_episode_id": 9001,
            "series_anidb_episode_no": 1171,
            "name": "One.Piece.S01E05.mkv",
        }
        responses = {
            f"{API}/animes/anidb/69": _json({"id": 2086, "release_year": 1999}),
            f"{API}/animes/2086/episodes/9001": _json(
                {"results": [{"id": 301, "state": "completed", "sublangs": ["en"]}]}
            ),
            f"{API}/torrents/301": _json(
                {
                    "files": [
                        {"filename": "One.Piece.S01E05.en.srt", "attachments": [_attachment(1, "en")]},
                        {"filename": "One.Piece.S02E05.en.srt", "attachments": [_attachment(2, "en")]},
                        {"filename": "One.Piece.E1171.en.srt", "attachments": [_attachment(3, "en")]},
                    ]
                }
            ),
        }
        provider, _ = self._provider(responses)

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual({row["provider_payload"]["attachment_id"] for row in results}, {1, 3})

    def test_single_file_with_wrong_season_is_rejected(self):
        video = {
            "kind": "episode",
            "series": "One Piece",
            "season": 1,
            "episode": 5,
            "series_anidb_id": 69,
            "series_anidb_episode_id": 9001,
            "series_anidb_episode_no": 1171,
            "name": "One.Piece.S01E05.mkv",
        }
        provider, _ = self._provider(
            {
                f"{API}/animes/anidb/69": _json({"id": 2086, "release_year": 1999}),
                f"{API}/animes/2086/episodes/9001": _json(
                    {"results": [{"id": 301, "state": "completed", "sublangs": ["en"]}]}
                ),
                f"{API}/torrents/301": _json(
                    {
                        "files": [
                            {
                                "filename": "One.Piece.S02E05.en.srt",
                                "attachments": [_attachment(2, "en")],
                            }
                        ]
                    }
                ),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual(results, [])

    def test_special_season_zero_rejects_regular_season_file(self):
        video = {
            "kind": "episode",
            "series": "One Piece",
            "season": 0,
            "episode": 5,
            "series_anidb_id": 69,
            "series_anidb_episode_id": 9001,
            "name": "One.Piece.S00E05.mkv",
        }
        provider, _ = self._provider(
            {
                f"{API}/animes/anidb/69": _json({"id": 2086, "release_year": 1999}),
                f"{API}/animes/2086/episodes/9001": _json(
                    {"results": [{"id": 301, "state": "completed", "sublangs": ["en"]}]}
                ),
                f"{API}/torrents/301": _json(
                    {
                        "files": [
                            {"filename": "One.Piece.S00E05.en.srt", "attachments": [_attachment(5, "en")]},
                            {"filename": "One.Piece.S01E05.en.srt", "attachments": [_attachment(15, "en")]},
                        ]
                    }
                ),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual([row["provider_payload"]["attachment_id"] for row in results], [5])

    def test_regional_forced_and_hearing_impaired_languages_are_preserved(self):
        video = {
            "kind": "episode",
            "series": "One Piece",
            "season": 1,
            "episode": 1,
            "series_anidb_id": 69,
            "series_anidb_episode_id": 1171,
            "name": "One.Piece.S01E01.mkv",
        }
        responses = {
            f"{API}/animes/anidb/69": _json({"id": 2086, "release_year": 1999}),
            f"{API}/animes/2086/episodes/1171": _json(
                {"results": [{"id": 301, "state": "completed", "sublangs": ["en", "pt-BR", "es-419", "zh-Hant"]}]}
            ),
            f"{API}/torrents/301": _json(
                {
                    "files": [
                        {
                            "filename": "One.Piece.S01E01.mkv",
                            "attachments": [
                                _attachment(1, "en", name="Signs"),
                                _attachment(2, "en", name="English (CC)"),
                                _attachment(3, "en", name="English"),
                                _attachment(4, "pt-BR", name="Portuguese (Brazil)"),
                                _attachment(5, "es-419", name="Spanish (Latin America)"),
                                _attachment(6, "zh-Hant", name="Chinese Traditional"),
                            ],
                        }
                    ]
                }
            ),
        }
        provider, _ = self._provider(responses)
        languages = [
            {"alpha3": "eng", "alpha2": "en", "forced": False, "hi": False},
            {"alpha3": "eng", "alpha2": "en", "forced": True, "hi": False},
            {"alpha3": "eng", "alpha2": "en", "forced": False, "hi": True},
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            {"alpha3": "spa", "alpha2": "es", "country_alpha2": "MX"},
            {"alpha3": "zho", "alpha2": "zh", "country_alpha2": "TW"},
        ]

        results = provider.search(video, languages, {})

        observed = {
            (
                result["language"]["alpha3"],
                result["language"].get("country_alpha2"),
                result["language"]["forced"],
                result["language"]["hi"],
            )
            for result in results
        }
        self.assertEqual(
            observed,
            {
                ("eng", None, False, False),
                ("eng", None, True, False),
                ("eng", None, False, True),
                ("por", "BR", False, False),
                ("spa", "MX", False, False),
                ("zho", "TW", False, False),
            },
        )

    def test_missing_or_invalid_metadata_ids_do_not_make_requests(self):
        provider, calls = self._provider({})

        self.assertEqual(
            provider.search({"kind": "movie", "title": "Example"}, [{"alpha3": "eng"}], {}),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "movie", "title": "Example", "anilist_id": "../400"},
                [{"alpha3": "eng"}],
                {},
            ),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "episode", "series_anidb_id": 69, "season": 1, "episode": 1},
                [{"alpha3": "eng"}],
                {},
            ),
            [],
        )
        self.assertEqual(calls, [])

    def test_missing_anime_returns_empty_and_malformed_json_is_rejected(self):
        video = {"kind": "movie", "title": "Example", "anilist_id": 400}
        missing, _ = self._provider(
            {
                f"{API}/animes/anilist/400": urllib.error.HTTPError(
                    f"{API}/animes/anilist/400", 404, "missing", {}, None
                )
            }
        )
        self.assertEqual(missing.search(video, [{"alpha3": "eng"}], {}), [])

        malformed, _ = self._provider({f"{API}/animes/anilist/400": b"{"})
        with self.assertRaisesRegex(ValueError, "JSON"):
            malformed.search(video, [{"alpha3": "eng"}], {})

    def test_download_returns_verified_content_and_rejects_bad_ids(self):
        provider, calls = self._provider({})
        content = b"1\n00:00:01,000 --> 00:00:02,000\nExample\n"
        compressed = lzma.compress(content)
        provider._http_get = lambda url, timeout=15, max_bytes=None: compressed

        result = provider.download(
            {
                "provider": "tsukihime",
                "schema": 1,
                "attachment_id": 1,
                "release_id": 301,
                "animetosho": False,
                "format": "srt",
            },
            {"alpha3": "eng"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), content)
        self.assertEqual(result["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(ValueError, "attachment"):
            provider.download({"attachment_id": 0}, {"alpha3": "eng"}, {})

    def test_download_rejects_non_xz_truncated_and_bounded_expansion(self):
        provider, _ = self._provider({})
        payload = {
            "provider": "tsukihime",
            "schema": 1,
            "attachment_id": 1,
            "animetosho": False,
            "format": "srt",
        }
        provider._http_get = lambda url, timeout=15, max_bytes=None: b"<html>"
        with self.assertRaisesRegex(ValueError, "XZ"):
            provider.download(payload, {"alpha3": "eng"}, {})

        compressed = lzma.compress(b"subtitle")[:-5]
        provider._http_get = lambda url, timeout=15, max_bytes=None: compressed
        with self.assertRaisesRegex(ValueError, "truncated"):
            provider.download(payload, {"alpha3": "eng"}, {})

        expanded = lzma.compress(b"x" * (self.mod.MAX_SUBTITLE_BYTES + 1))
        provider._http_get = lambda url, timeout=15, max_bytes=None: expanded
        with self.assertRaisesRegex(ValueError, "size limit"):
            provider.download(payload, {"alpha3": "eng"}, {})

    def test_http_read_is_capped_and_uses_a_timeout(self):
        provider = self.mod.TsukiHimeProvider()
        reads = []
        timeouts = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self, size):
                reads.append(size)
                return b"x" * size

        class Opener:
            def open(self, request, timeout):
                del request
                timeouts.append(timeout)
                return Response()

        with patch.object(self.mod.urllib.request, "build_opener", return_value=Opener()) as build_opener:
            with self.assertRaisesRegex(ValueError, "size limit"):
                provider._http_get("https://api.tsukihime.org/v1/test", max_bytes=64)

        self.assertEqual(reads, [65])
        self.assertEqual(timeouts, [self.mod.HTTP_TIMEOUT_SECONDS])
        self.assertIsInstance(build_opener.call_args.args[0], self.mod._SameOriginRedirectHandler)

    def test_http_downgrade_and_cross_host_redirects_are_not_followed(self):
        provider = self.mod.TsukiHimeProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        initial_url = "https://api.tsukihime.org/v1/test"

        class RedirectServer(self.mod.urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self, target):
                self.opened = []
                self.target = target

            def https_open(self, request):
                self.opened.append(request.full_url)
                return _http_response(request.full_url, code=302, location=self.target)

            def http_open(self, request):
                self.opened.append(request.full_url)
                return _http_response(request.full_url, body=b"must not be read")

        for target in (
            "http://127.0.0.1/private",
            "https://example.invalid/private",
            "https://api.tsukihime.org:444/private",
            "https://user@api.tsukihime.org/private",
        ):
            with self.subTest(target=target):
                server = RedirectServer(target)
                with patch.object(
                    self.mod.urllib.request,
                    "build_opener",
                    side_effect=lambda *handlers: original_build_opener(server, *handlers),
                ):
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        provider._http_get(initial_url)
                    raised.exception.close()
                self.assertEqual(server.opened, [initial_url])

    def test_same_origin_redirect_is_followed_but_response_read_stays_bounded(self):
        provider = self.mod.TsukiHimeProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        initial_url = "https://api.tsukihime.org/v1/start"
        final_url = "https://api.tsukihime.org/v1/final"

        class RedirectServer(self.mod.urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self):
                self.opened = []

            def https_open(self, request):
                self.opened.append(request.full_url)
                if request.full_url == initial_url:
                    return _http_response(request.full_url, code=302, location=final_url)
                return _http_response(request.full_url, body=b"x" * 80)

        server = RedirectServer()
        with patch.object(
            self.mod.urllib.request,
            "build_opener",
            side_effect=lambda *handlers: original_build_opener(server, *handlers),
        ):
            with self.assertRaisesRegex(ValueError, "size limit"):
                provider._http_get(initial_url, max_bytes=64)

        self.assertEqual(server.opened, [initial_url, final_url])

    def test_storage_mirror_redirect_is_followed_with_bounded_response_read(self):
        provider = self.mod.TsukiHimeProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        initial_url = "https://storage.tsukihime.org/attach/00000001/1.xz"
        mirror_url = "https://storage.animetosho.org/attach/00000001/1.xz"

        class RedirectServer(self.mod.urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self):
                self.opened = []

            def https_open(self, request):
                self.opened.append(request.full_url)
                if request.full_url == initial_url:
                    return _http_response(request.full_url, code=302, location=mirror_url)
                return _http_response(request.full_url, body=b"x" * 80)

        server = RedirectServer()
        with patch.object(
            self.mod.urllib.request,
            "build_opener",
            side_effect=lambda *handlers: original_build_opener(server, *handlers),
        ):
            with self.assertRaisesRegex(ValueError, "size limit"):
                provider._http_get(initial_url, max_bytes=64)

        self.assertEqual(server.opened, [initial_url, mirror_url])

    def test_same_origin_redirect_loop_is_bounded(self):
        provider = self.mod.TsukiHimeProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        loop_url = "https://api.tsukihime.org/v1/loop"

        class RedirectLoop(self.mod.urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self):
                self.opened = []

            def https_open(self, request):
                self.opened.append(request.full_url)
                return _http_response(request.full_url, code=302, location=loop_url)

        loop = RedirectLoop()
        with patch.object(
            self.mod.urllib.request,
            "build_opener",
            side_effect=lambda *handlers: original_build_opener(loop, *handlers),
        ):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                provider._http_get(loop_url)
            raised.exception.close()

        self.assertGreater(len(loop.opened), 1)
        self.assertLessEqual(len(loop.opened), 10)

    def test_manifest_declares_regional_variants(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))

        for language in ("eng", "por-BR", "spa-MX", "zho-CN", "zho-TW"):
            with self.subTest(language=language):
                self.assertIn(language, manifest["languages"])


if __name__ == "__main__":
    unittest.main()
