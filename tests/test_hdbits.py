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
PROVIDER_DIR = ROOT / "providers" / "hdbits"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "hdbits_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_json(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


MOVIE_VIDEO = _fixture_json("hdbits_video_dune_2021.json")
EPISODE_VIDEO = _fixture_json("hdbits_video_chernobyl_s01e01.json")
MOVIE_TORRENTS = _fixture_json("hdbits_torrents_dune.json")
EPISODE_TORRENTS = _fixture_json("hdbits_torrents_chernobyl.json")
MOVIE_SUBS_1001 = _fixture_json("hdbits_subtitles_dune_1001.json")
MOVIE_SUBS_1002 = _fixture_json("hdbits_subtitles_dune_1002.json")
EPISODE_SUBS_2001 = _fixture_json("hdbits_subtitles_chernobyl_2001.json")
SRT_BODY = b"1\n00:00:01,000 --> 00:00:02,000\nHDBits fixture.\n"


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return stream.getvalue()


class HDBitsLookupTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_lookup_uses_imdb_id_without_tt_prefix(self):
        lookup, matches, episode = self.mod.build_lookup(MOVIE_VIDEO)

        self.assertEqual(lookup, {"imdb": {"id": "1160419"}})
        self.assertIsNone(episode)
        self.assertEqual(matches, ["imdb_id", "title", "year"])

    def test_episode_lookup_uses_tvdb_id_and_season(self):
        lookup, matches, episode = self.mod.build_lookup(EPISODE_VIDEO)

        self.assertEqual(lookup, {"tvdb": {"id": 360893, "season": 1}})
        self.assertEqual(episode, 1)
        self.assertEqual(
            matches,
            ["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
        )

    def test_movie_lookup_empty_without_imdb_id(self):
        video = {**MOVIE_VIDEO, "imdb_id": "", "imdb": ""}

        lookup, matches, episode = self.mod.build_lookup(video)

        self.assertEqual(lookup, {})
        self.assertEqual(matches, [])
        self.assertIsNone(episode)

    def test_episode_lookup_empty_without_tvdb_id(self):
        video = {**EPISODE_VIDEO}
        video.pop("series_tvdb_id", None)
        video.pop("tvdb_id", None)
        video.pop("tvdb", None)

        lookup, matches, episode = self.mod.build_lookup(video)

        self.assertEqual(lookup, {})
        self.assertEqual(matches, [])
        self.assertIsNone(episode)

    def test_search_skips_api_when_required_id_missing(self):
        provider = self.mod.HDBitsProvider()

        def fail(*_args, **_kwargs):
            raise AssertionError("search must not call the HDBits API without an id")

        provider._post_json = fail
        results = provider.search(
            {**MOVIE_VIDEO, "imdb_id": "", "imdb": ""},
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(results, [])


class HDBitsLanguageAndFilterTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_hdbits_special_language_codes_match_legacy_behavior(self):
        self.assertEqual(self.mod.hdbits_language_to_alpha3("uk"), "eng")
        self.assertEqual(self.mod.hdbits_language_to_alpha3("br"), "por")
        self.assertEqual(self.mod.hdbits_language_to_alpha3("gr"), "ell")
        self.assertEqual(self.mod.hdbits_language_to_alpha3("es"), "spa")

    def test_parse_subtitles_filters_extension_language_and_blocked_keywords(self):
        rows = self.mod.parse_subtitles(
            MOVIE_SUBS_1001["data"] + MOVIE_SUBS_1002["data"],
            requested_alpha3=[
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            ],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        # 502 is an HDBits "br" row, so it only matches a Brazilian Portuguese request.
        self.assertEqual([row["subtitle_id"] for row in rows], [501, 502])
        self.assertEqual([row["language"] for row in rows], ["eng", "por"])
        self.assertEqual([row["country_alpha2"] for row in rows], [None, "BR"])
        self.assertTrue(all("commentary" not in row["release_info"].lower() for row in rows))

    def test_parse_subtitles_keeps_brazilian_rows_out_of_plain_portuguese(self):
        rows = self.mod.parse_subtitles(
            MOVIE_SUBS_1001["data"] + MOVIE_SUBS_1002["data"],
            requested_alpha3=[
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt"},
            ],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        # Plain Portuguese must not pick up the Brazilian-only "br" row (502).
        self.assertEqual([row["subtitle_id"] for row in rows], [501])

    def test_parse_subtitles_allows_extraction_titles(self):
        rows = self.mod.parse_subtitles(
            [
                {
                    "filename": "Extraction.2020.1080p.en.srt",
                    "id": 801,
                    "language": "uk",
                    "title": "Extraction.2020.1080p",
                }
            ],
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in rows], [801])

    def test_episode_subtitles_skip_explicit_different_episode(self):
        rows = self.mod.parse_subtitles(
            EPISODE_SUBS_2001["data"],
            requested_alpha3={"eng", "ell"},
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        self.assertEqual([row["subtitle_id"] for row in rows], [601, 603])
        self.assertEqual({row["language"] for row in rows}, {"eng", "ell"})

    def test_parse_subtitles_preserves_forced_and_hi_variants(self):
        rows = [
            {
                "filename": "Dune.2021.Forced.en.srt",
                "id": 701,
                "language": "uk",
                "title": "Dune.2021.Forced",
            },
            {
                "filename": "Dune.2021.SDH.en.srt",
                "id": 702,
                "language": "uk",
                "title": "Dune.2021.SDH",
            },
            {
                "filename": "Dune.2021.en.srt",
                "id": 703,
                "language": "uk",
                "title": "Dune.2021",
            },
        ]

        normal = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )
        forced = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "forced": True}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )
        hi = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "hi": True}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in normal], [703])
        self.assertEqual([row["subtitle_id"] for row in forced], [701])
        self.assertTrue(forced[0]["forced"])
        self.assertEqual([row["subtitle_id"] for row in hi], [702])
        self.assertTrue(hi[0]["hearing_impaired"])

    def test_parse_subtitles_does_not_flag_hindi_as_hearing_impaired(self):
        rows = [
            {
                "filename": "Movie.2021.hi.srt",
                "id": 711,
                "language": "hi",
                "title": "Movie.2021",
            }
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "hin", "alpha2": "hi"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in parsed], [711])
        self.assertEqual(parsed[0]["language"], "hin")
        self.assertFalse(parsed[0]["hearing_impaired"])

    def test_parse_subtitles_rejects_episode_from_filename_tag(self):
        rows = [
            {
                "filename": "Chernobyl.S01E02.en.srt",
                "id": 721,
                "language": "uk",
                "title": "Chernobyl.S01.1080p.WEB-DL-GROUP",
            }
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        # Season-pack title hides the episode, but the filename tags S01E02 so an
        # S01E01 request must drop the row.
        self.assertEqual(parsed, [])


class HDBitsSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_search_requires_username_and_passkey(self):
        provider = self.mod.HDBitsProvider()

        with self.assertRaisesRegex(ValueError, "username"):
            provider.search(MOVIE_VIDEO, [{"alpha3": "eng"}], {"passkey": "secret"})
        with self.assertRaisesRegex(ValueError, "passkey"):
            provider.search(MOVIE_VIDEO, [{"alpha3": "eng"}], {"username": "user"})

    def test_movie_search_posts_authenticated_lookup_and_returns_results(self):
        provider = self.mod.HDBitsProvider()
        calls = []
        responses = {
            ("https://hdbits.org/api/torrents", json.dumps({"imdb": {"id": "1160419"}, "passkey": "secret", "username": "user"}, sort_keys=True)): MOVIE_TORRENTS,
            ("https://hdbits.org/api/subtitles", json.dumps({"passkey": "secret", "torrent_id": 1001, "username": "user"}, sort_keys=True)): MOVIE_SUBS_1001,
            ("https://hdbits.org/api/subtitles", json.dumps({"passkey": "secret", "torrent_id": 1002, "username": "user"}, sort_keys=True)): MOVIE_SUBS_1002,
        }

        def post_stub(url, payload, timeout=15):
            del timeout
            key = (url, json.dumps(payload, sort_keys=True))
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected POST: {key}")
            return responses[key]

        provider._post_json = post_stub
        results = provider.search(
            MOVIE_VIDEO,
            [
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            ],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 2)
        self.assertEqual([call[0] for call in calls], ["https://hdbits.org/api/torrents", "https://hdbits.org/api/subtitles", "https://hdbits.org/api/subtitles"])
        first = results[0]
        self.assertEqual(first["provider"], "hdbits")
        self.assertEqual(first["language"]["alpha3"], "eng")
        self.assertIn("imdb_id", first["matches"])
        self.assertIn("title", first["matches"])
        self.assertIn("year", first["matches"])
        self.assertIn("source", first["matches"])
        self.assertEqual(first["provider_payload"]["subtitle_id"], 501)
        self.assertNotIn("passkey", first["provider_payload"])
        brazilian = next(item for item in results if item["provider_payload"]["subtitle_id"] == 502)
        self.assertEqual(brazilian["language"]["alpha3"], "por")
        self.assertEqual(brazilian["language"]["country_alpha2"], "BR")

    def test_search_surfaces_hdbits_api_errors(self):
        provider = self.mod.HDBitsProvider()
        provider._post_json = lambda url, payload, timeout=15: {"status": 5, "message": "bad passkey"}

        with self.assertRaisesRegex(ValueError, "bad passkey"):
            provider.search(
                MOVIE_VIDEO,
                [{"alpha3": "eng", "alpha2": "en"}],
                {"username": "user", "passkey": "secret", "request_delay_ms": 0},
            )

    def test_episode_search_filters_by_episode_and_tvdb_lookup(self):
        provider = self.mod.HDBitsProvider()
        calls = []
        responses = {
            ("https://hdbits.org/api/torrents", json.dumps({"passkey": "secret", "tvdb": {"id": 360893, "season": 1}, "username": "user"}, sort_keys=True)): EPISODE_TORRENTS,
            ("https://hdbits.org/api/subtitles", json.dumps({"passkey": "secret", "torrent_id": 2001, "username": "user"}, sort_keys=True)): EPISODE_SUBS_2001,
        }

        def post_stub(url, payload, timeout=15):
            del timeout
            key = (url, json.dumps(payload, sort_keys=True))
            calls.append(key)
            if key not in responses:
                raise AssertionError(f"unexpected POST: {key}")
            return responses[key]

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(calls[0][0], "https://hdbits.org/api/torrents")
        self.assertEqual(results[0]["provider_payload"]["subtitle_id"], 601)
        self.assertEqual(results[0]["provider_payload"]["episode"], 1)


class HDBitsDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_download_fetches_direct_subtitle_with_passkey_from_config(self):
        provider = self.mod.HDBitsProvider()

        def get_stub(url, timeout=15):
            del timeout
            self.assertEqual(url, "https://hdbits.org/getdox.php?id=501&passkey=secret")
            return SRT_BODY

        provider._http_get = get_stub
        result = provider.download(
            {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": "movie.en.srt"},
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)
        self.assertEqual(result["content_sha256"], hashlib.sha256(SRT_BODY).hexdigest())
        self.assertEqual(result["format"], "srt")

    def test_download_extracts_matching_episode_from_zip(self):
        provider = self.mod.HDBitsProvider()
        zip_body = _zip_body(
            {
                "Chernobyl.S01E02.en.srt": b"wrong",
                "Chernobyl.S01E01.en.srt": SRT_BODY,
            }
        )
        provider._http_get = lambda url, timeout=15: zip_body

        result = provider.download(
            {
                "provider": "hdbits",
                "schema": 1,
                "subtitle_id": 601,
                "filename": "Chernobyl.S01E01.en.zip",
                "season": 1,
                "episode": 1,
            },
            {"alpha3": "eng", "alpha2": "en"},
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)

    def test_download_selects_archive_member_by_requested_language(self):
        provider = self.mod.HDBitsProvider()
        zip_body = _zip_body(
            {
                "Chernobyl.S01E01.en.srt": b"english",
                "Chernobyl.S01E01.gr.srt": b"greek",
            }
        )
        provider._http_get = lambda url, timeout=15: zip_body

        result = provider.download(
            {
                "provider": "hdbits",
                "schema": 1,
                "subtitle_id": 603,
                "filename": "Chernobyl.S01E01.zip",
                "season": 1,
                "episode": 1,
                "language": "ell",
            },
            {"alpha3": "ell", "alpha2": "el"},
            {"username": "user", "passkey": "secret"},
        )

        self.assertEqual(base64.b64decode(result["content_b64"]), b"greek")

    def test_download_rejects_archive_missing_requested_episode(self):
        provider = self.mod.HDBitsProvider()
        zip_body = _zip_body({"Chernobyl.S01E02.en.srt": SRT_BODY})
        provider._http_get = lambda url, timeout=15: zip_body

        with self.assertRaisesRegex(ValueError, "episode"):
            provider.download(
                {
                    "provider": "hdbits",
                    "schema": 1,
                    "subtitle_id": 601,
                    "filename": "Chernobyl.S01.en.zip",
                    "season": 1,
                    "episode": 1,
                },
                {"alpha3": "eng", "alpha2": "en"},
                {"username": "user", "passkey": "secret"},
            )

    def test_download_rejects_empty_response(self):
        provider = self.mod.HDBitsProvider()
        provider._http_get = lambda url, timeout=15: b""

        with self.assertRaisesRegex(RuntimeError, "empty"):
            provider.download(
                {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": "movie.en.srt"},
                {"alpha3": "eng", "alpha2": "en"},
                {"username": "user", "passkey": "secret"},
            )

    def test_content_payload_reports_cp1250_encoding(self):
        body = "Zażółć gęślą jaźń".encode("cp1250")

        result = self.mod._content_payload(body, "srt")

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertEqual(result["encoding"], "cp1250")

    def test_download_extracts_matching_episode_from_rar(self):
        provider = self.mod.HDBitsProvider()
        provider._http_get = lambda url, timeout=15: b"rar bytes"

        with mock.patch.object(
            self.mod,
            "_extract_rar_files",
            return_value=[
                ("Chernobyl.S01E02.en.srt", b"wrong"),
                ("Chernobyl.S01E01.en.srt", SRT_BODY),
            ],
        ) as extractor:
            result = provider.download(
                {
                    "provider": "hdbits",
                    "schema": 1,
                    "subtitle_id": 603,
                    "filename": "Chernobyl.S01.rar",
                    "season": 1,
                    "episode": 1,
                },
                {"alpha3": "ell", "alpha2": "el"},
                {"username": "user", "passkey": "secret"},
            )

        extractor.assert_called_once_with(b"rar bytes")
        self.assertEqual(base64.b64decode(result["content_b64"]), SRT_BODY)
