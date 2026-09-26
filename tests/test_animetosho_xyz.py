import base64
import hashlib
import importlib.util
import io
import json
import lzma
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from urllib.response import addinfourl
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "animetosho_xyz"
FEED = "https://feed.animetosho.xyz"
DOWNLOAD = "https://animetosho.xyz/storage/attach/00000065/101.xz"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "animetosho_xyz_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(body):
    return json.dumps(body).encode("utf-8")


def _attachment(attachment_id, url=DOWNLOAD, language_code="eng", language="English", codec="srt", **values):
    return {
        "id": attachment_id,
        "type": "subtitle",
        "url": url,
        "size": 100,
        "info": {
            "language_code": language_code,
            "language": language,
            "name": values.pop("name", language),
            "codec": codec,
            "forced": values.pop("forced", 0),
            **values,
        },
    }


def _torrent(attachments, torrent_name="Solo.Leveling.S01E12.1080p.WEB-DL"):
    return {"torrent_name": torrent_name, "attachments": attachments}


def _http_response(url, code=200, body=b"", location=None):
    headers = Message()
    if location:
        headers["Location"] = location
    response = addinfourl(io.BytesIO(body), headers, url, code)
    response.msg = "Found" if code == 302 else "OK"
    return response


def _entry(entry_id, timestamp=100, title="Solo Leveling S01E12", episode_id=101):
    return {
        "id": entry_id,
        "anidb_eid": episode_id,
        "status": "complete",
        "timestamp": timestamp,
        "title": title,
    }


class AnimeToshoXYZProviderTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, responses):
        provider = self.mod.AnimeToshoXYZProvider()
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

    def test_search_uses_anidb_episode_id_and_returns_a_subtitle(self):
        video = {
            "kind": "episode",
            "series": "Solo Leveling",
            "season": 1,
            "episode": 12,
            "series_anidb_episode_id": [100, 101],
            "name": "Solo.Leveling.S01E12.mkv",
        }
        entry = _entry(616869, title="[ToonsHub] Solo Leveling S01E12")
        detail = _torrent([_attachment(101)], entry["title"])
        provider, calls = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([entry]),
                f"{FEED}/json?show=torrent&id=616869": _json(detail),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "alpha2": "en"}], {})

        self.assertEqual(
            calls,
            [f"{FEED}/feed/json?eid=101", f"{FEED}/json?show=torrent&id=616869"],
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["provider"], "animetosho_xyz")
        self.assertEqual(result["provider_payload"]["entry_id"], 616869)
        self.assertEqual(result["page_link"], DOWNLOAD)
        self.assertEqual(result["language"]["alpha3"], "eng")
        self.assertTrue({"series", "season", "episode"}.issubset(result["matches"]))

    def test_shared_attachment_keeps_distinct_release_ids_and_downloads(self):
        video = {
            "kind": "episode",
            "series": "Solo Leveling",
            "season": 1,
            "episode": 12,
            "series_anidb_episode_id": 101,
        }
        entries = [_entry(1, timestamp=2, title="Release One"), _entry(2, timestamp=1, title="Release Two")]
        detail = _torrent([_attachment(101)])
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json(entries),
                f"{FEED}/json?show=torrent&id=1": _json(detail),
                f"{FEED}/json?show=torrent&id=2": _json(detail),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "alpha2": "en"}], {})

        self.assertEqual(len(results), 2)
        self.assertEqual({result["provider_payload"]["entry_id"] for result in results}, {1, 2})
        self.assertEqual(len({result["id"] for result in results}), 2)
        self.assertEqual({result["page_link"] for result in results}, {DOWNLOAD})

    def test_forced_hearing_impaired_and_regional_languages_are_preserved(self):
        video = {
            "kind": "episode",
            "series": "Solo Leveling",
            "season": 1,
            "episode": 12,
            "series_anidb_episode_id": 101,
        }
        attachments = [
            _attachment(1, language_code="eng", name="Signs"),
            _attachment(2, language_code="eng", name="English (SDH)"),
            _attachment(3, language_code="eng", name="English"),
            _attachment(4, language_code="por", language="Portuguese (Brazil)"),
            _attachment(5, language_code="spa", language="Spanish (Latin America)"),
        ]
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json(
                    [_entry(1, timestamp=1, title="Solo Leveling")]
                ),
                f"{FEED}/json?show=torrent&id=1": _json(_torrent(attachments)),
            }
        )
        languages = [
            {"alpha3": "eng", "alpha2": "en", "forced": False, "hi": False},
            {"alpha3": "eng", "alpha2": "en", "forced": True, "hi": False},
            {"alpha3": "eng", "alpha2": "en", "forced": False, "hi": True},
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            {"alpha3": "spa", "alpha2": "es", "country_alpha2": "MX"},
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
            },
        )

    def test_missing_or_invalid_episode_ids_do_not_make_requests(self):
        provider, calls = self._provider({})

        self.assertEqual(
            provider.search({"kind": "episode", "series": "Solo Leveling"}, [{"alpha3": "eng"}], {}),
            [],
        )
        self.assertEqual(
            provider.search(
                {"kind": "episode", "series_anidb_episode_id": "../../101"},
                [{"alpha3": "eng"}],
                {},
            ),
            [],
        )
        self.assertEqual(calls, [])

    def test_malformed_feed_and_missing_feed_return_no_results(self):
        video = {"kind": "episode", "series_anidb_episode_id": 101}
        malformed, _ = self._provider({f"{FEED}/feed/json?eid=101": b"{"})
        self.assertEqual(malformed.search(video, [{"alpha3": "eng"}], {}), [])

        missing, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": urllib.error.HTTPError(
                    f"{FEED}/feed/json?eid=101", 404, "missing", {}, None
                )
            }
        )
        self.assertEqual(missing.search(video, [{"alpha3": "eng"}], {}), [])

    def test_missing_or_mismatched_feed_episode_ids_do_not_gain_credit(self):
        video = {"kind": "episode", "series_anidb_episode_id": 101}
        entries = [
            _entry(1, timestamp=3, title="Wrong episode", episode_id=102),
            _entry(2, timestamp=2, title="Missing episode", episode_id=None),
            _entry(3, timestamp=1, title="Exact episode", episode_id=101),
        ]
        provider, calls = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json(entries),
                f"{FEED}/json?show=torrent&id=1": _json(_torrent([_attachment(101)])),
                f"{FEED}/json?show=torrent&id=2": _json(_torrent([_attachment(102)])),
                f"{FEED}/json?show=torrent&id=3": _json(_torrent([_attachment(103)])),
            }
        )

        results = provider.search(video, [{"alpha3": "eng"}], {})

        self.assertEqual(calls, [f"{FEED}/feed/json?eid=101", f"{FEED}/json?show=torrent&id=3"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["entry_id"], 3)

    def test_mixed_episode_attachments_only_match_the_requested_episode(self):
        video = {
            "kind": "episode",
            "series": "Show",
            "season": 1,
            "episode": 1,
            "series_anidb_episode_id": 101,
        }
        episode_one = _attachment(201)
        episode_one["filename"] = "Show.S01E01.en.srt"
        episode_two = _attachment(
            202,
            url="https://animetosho.xyz/storage/attach/00000066/102.xz",
        )
        episode_two["filename"] = "Show.S01E02.en.srt"
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([_entry(1, title="Show S01E01")]),
                f"{FEED}/json?show=torrent&id=1": _json(
                    _torrent([episode_one, episode_two], "Show Season Pack")
                ),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual([row["provider_payload"]["subtitle_id"] for row in results], [201])

    def test_absolute_episode_number_only_matches_unseasoned_markers(self):
        video = {
            "kind": "episode",
            "series": "Show",
            "season": 2,
            "episode": 1,
            "series_anidb_episode_id": 101,
            "series_anidb_episode_no": 13,
        }
        attachments = []
        for attachment_id, filename in (
            (201, "Show.S02E01.en.srt"),
            (202, "Show.S02E13.en.srt"),
            (203, "Show.2x13.en.srt"),
            (204, "Show.E13.en.srt"),
        ):
            attachment = _attachment(
                attachment_id,
                url=f"https://animetosho.xyz/storage/attach/{attachment_id:08X}/{attachment_id}.xz",
            )
            attachment["filename"] = filename
            attachments.append(attachment)
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([_entry(1, title="Show S02E01")]),
                f"{FEED}/json?show=torrent&id=1": _json(_torrent(attachments, "Show Season Pack")),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual({row["provider_payload"]["subtitle_id"] for row in results}, {201, 204})

    def test_top_level_attachments_use_nested_file_associations(self):
        video = {
            "kind": "episode",
            "series": "Show",
            "season": 1,
            "episode": 1,
            "series_anidb_episode_id": 101,
        }
        episode_one = _attachment(201, url=DOWNLOAD)
        episode_one["filename"] = "English.srt"
        episode_two = _attachment(
            202,
            url="https://animetosho.xyz/storage/attach/00000066/102.xz",
        )
        episode_two["filename"] = "English.srt"
        detail = {
            "torrent_name": "Show Season Pack",
            "attachments": [episode_one, episode_two],
            "files": [
                {"filename": "Show.S01E01.mkv", "attachments": [{"id": 201}]},
                {"filename": "Show.S01E02.mkv", "attachments": [{"id": 202}]},
            ],
        }
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([_entry(1, title="Show S01E01")]),
                f"{FEED}/json?show=torrent&id=1": _json(detail),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual([row["provider_payload"]["subtitle_id"] for row in results], [201])

    def _batch_results(self, filenames, episode=1, absolute=None):
        video = {
            "kind": "episode",
            "series": "Show",
            "season": 1,
            "episode": episode,
            "series_anidb_episode_id": 101,
        }
        if absolute is not None:
            video["series_anidb_episode_no"] = absolute
        attachments = []
        files = []
        for index, filename in enumerate(filenames, start=201):
            attachment = _attachment(
                index,
                url=f"https://animetosho.xyz/storage/attach/{index:08X}/{index}.xz",
            )
            attachment["filename"] = "English.srt"
            attachments.append(attachment)
            files.append({"filename": filename, "attachments": [{"id": index}]})
        files.append({"filename": "Show.nfo", "attachments": []})
        detail = {"torrent_name": "Show Batch", "attachments": attachments, "files": files}
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([_entry(1, title="Show Batch")]),
                f"{FEED}/json?show=torrent&id=1": _json(detail),
            }
        )
        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})
        return [row["provider_payload"]["subtitle_id"] for row in results]

    def test_markerless_batch_files_are_matched_by_dash_number_or_dropped(self):
        filenames = [
            "[Group] Show - 01 [1080p].mkv",
            "[Group] Show - 02v2 [1080p].mkv",
            "[Group] Show [NCOP].mkv",
        ]

        self.assertEqual(self._batch_results(filenames), [201])
        self.assertEqual(self._batch_results(filenames, episode=2), [202])
        # A second-cour batch numbered 13 onward matches the AniDB absolute number.
        self.assertEqual(self._batch_results(["Show - 13.mkv", "Show - 14.mkv"], absolute=13), [201])

    def test_markerless_file_is_trusted_only_in_a_single_file_torrent(self):
        self.assertEqual(self._batch_results(["Show [1080p].mkv"]), [201])
        self.assertEqual(self._batch_results(["Show [1080p].mkv", "Show [NCED].mkv"]), [])

    def test_special_season_zero_rejects_regular_season_file(self):
        video = {
            "kind": "episode",
            "series": "Show",
            "season": 0,
            "episode": 5,
            "series_anidb_episode_id": 101,
        }
        special = _attachment(201)
        special["filename"] = "Show.S00E05.en.srt"
        regular = _attachment(
            202,
            url="https://animetosho.xyz/storage/attach/00000066/102.xz",
        )
        regular["filename"] = "Show.S01E05.en.srt"
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([_entry(1, title="Show S00E05")]),
                f"{FEED}/json?show=torrent&id=1": _json(_torrent([special, regular], "Show Specials Pack")),
            }
        )

        results = provider.search(video, [{"alpha3": "eng", "forced": False, "hi": False}], {})

        self.assertEqual([row["provider_payload"]["subtitle_id"] for row in results], [201])

    def test_untrusted_download_urls_are_rejected(self):
        video = {"kind": "episode", "series_anidb_episode_id": 101}
        entry = _entry(1, timestamp=1, title="Release")
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([entry]),
                f"{FEED}/json?show=torrent&id=1": _json(
                    _torrent([_attachment(1, url="https://example.invalid/subtitle.xz")])
                ),
            }
        )

        self.assertEqual(provider.search(video, [{"alpha3": "eng"}], {}), [])
        with self.assertRaisesRegex(ValueError, "URL"):
            provider.download(
                {"download_url": "https://example.invalid/subtitle.xz"},
                {"alpha3": "eng"},
                {},
            )

    def test_malformed_attachment_urls_are_skipped(self):
        video = {"kind": "episode", "series_anidb_episode_id": 101}
        entry = _entry(1, timestamp=1, title="Release")
        provider, _ = self._provider(
            {
                f"{FEED}/feed/json?eid=101": _json([entry]),
                f"{FEED}/json?show=torrent&id=1": _json(
                    _torrent([_attachment(1, url="https://animetosho.xyz:invalid/subtitle.xz")])
                ),
            }
        )

        self.assertEqual(provider.search(video, [{"alpha3": "eng"}], {}), [])

    def test_download_returns_verified_xz_content(self):
        provider, calls = self._provider({})
        content = b"1\n00:00:01,000 --> 00:00:02,000\nAnime\n"
        compressed = lzma.compress(content)
        provider._http_get = lambda url, timeout=15, max_bytes=None: compressed

        result = provider.download(
            {"provider": "animetosho_xyz", "schema": 1, "download_url": DOWNLOAD, "format": "srt"},
            {"alpha3": "eng"},
            {},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), content)
        self.assertEqual(result["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["format"], "srt")
        self.assertEqual(calls, [])

    def test_download_rejects_non_xz_and_bounded_expansion(self):
        provider, _ = self._provider({})
        payload = {"provider": "animetosho_xyz", "schema": 1, "download_url": DOWNLOAD}
        provider._http_get = lambda url, timeout=15, max_bytes=None: b"not xz"
        with self.assertRaisesRegex(ValueError, "XZ"):
            provider.download(payload, {"alpha3": "eng"}, {})

        expanded = lzma.compress(b"x" * (self.mod.MAX_SUBTITLE_BYTES + 1))
        provider._http_get = lambda url, timeout=15, max_bytes=None: expanded
        with self.assertRaisesRegex(ValueError, "size limit"):
            provider.download(payload, {"alpha3": "eng"}, {})

    def test_http_read_is_capped_and_uses_a_timeout(self):
        provider = self.mod.AnimeToshoXYZProvider()
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
                provider._http_get("https://feed.animetosho.xyz/feed/json", max_bytes=64)

        self.assertEqual(reads, [65])
        self.assertEqual(timeouts, [self.mod.HTTP_TIMEOUT_SECONDS])
        self.assertIsInstance(build_opener.call_args.args[0], self.mod._SameOriginRedirectHandler)

    def test_http_downgrade_and_cross_host_redirects_are_not_followed(self):
        provider = self.mod.AnimeToshoXYZProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        initial_url = f"{FEED}/feed/json"

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
            "https://feed.animetosho.xyz:444/private",
            "https://user@feed.animetosho.xyz/private",
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
        provider = self.mod.AnimeToshoXYZProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        initial_url = f"{FEED}/start"
        final_url = f"{FEED}/final"

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

    def test_same_origin_redirect_loop_is_bounded(self):
        provider = self.mod.AnimeToshoXYZProvider()
        original_build_opener = self.mod.urllib.request.build_opener
        loop_url = f"{FEED}/loop"

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

        for language in ("eng", "por-BR", "spa-MX"):
            with self.subTest(language=language):
                self.assertIn(language, manifest["languages"])


if __name__ == "__main__":
    unittest.main()
