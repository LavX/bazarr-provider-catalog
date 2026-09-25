import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

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

        # 502 is an HDBits "br" row, returned as Brazilian Portuguese.
        self.assertEqual([row["subtitle_id"] for row in rows], [501, 502])
        self.assertEqual([row["language"] for row in rows], ["eng", "por"])
        self.assertEqual([row["country_alpha2"] for row in rows], [None, "BR"])
        self.assertTrue(all("commentary" not in row["release_info"].lower() for row in rows))

    def test_parse_subtitles_accepts_sub_files(self):
        rows = self.mod.parse_subtitles(
            [
                {
                    "filename": "Dune.2021.1080p.BluRay.x264-GROUP.en.sub",
                    "id": 504,
                    "language": "uk",
                    "title": "Dune.2021.1080p.BluRay.x264-GROUP",
                }
            ],
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual([row["subtitle_id"] for row in rows], [504])

    def test_sub_files_are_selected_from_archives_and_keep_their_format(self):
        member = self.mod.select_subtitle_file(["readme.txt", "Dune.2021.en.sub"], {"language": {"alpha3": "eng"}})
        payload = self.mod.download_payload(b"{1}{25}Hello\n", {"filename": "Dune.2021.en.sub"})

        self.assertEqual(member, "Dune.2021.en.sub")
        self.assertEqual(payload["format"], "sub")

    def test_parse_subtitles_returns_brazilian_rows_for_plain_portuguese(self):
        rows = self.mod.parse_subtitles(
            MOVIE_SUBS_1001["data"] + MOVIE_SUBS_1002["data"],
            requested_alpha3=[
                {"alpha3": "eng", "alpha2": "en"},
                {"alpha3": "por", "alpha2": "pt"},
            ],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        # The manifest declares only "por", so the host sends plain Portuguese.
        # The "br" row (502) answers it and keeps its Brazilian region.
        self.assertEqual([row["subtitle_id"] for row in rows], [501, 502])
        self.assertEqual([row["country_alpha2"] for row in rows], [None, "BR"])

    def test_parse_subtitles_keeps_plain_portuguese_rows_out_of_brazilian_requests(self):
        rows = self.mod.parse_subtitles(
            [{"filename": "Dune.2021.pt.srt", "id": 511, "language": "pt", "title": "Dune.2021"}],
            requested_alpha3=[{"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"}],
            video=MOVIE_VIDEO,
            base_matches=["imdb_id", "title", "year"],
        )

        self.assertEqual(rows, [])

    def test_every_advertised_language_is_reachable_from_an_hdbits_code(self):
        manifest = json.loads((PROVIDER_DIR / "provider.json").read_text(encoding="utf-8"))
        codes = set(self.mod.ALPHA2_TO_ALPHA3) | set(self.mod.SPECIAL_HDBITS_LANGUAGE)
        reachable = {self.mod.hdbits_language(code)[0] for code in codes}

        self.assertEqual(sorted(manifest["languages"]), self.mod.HDBITS_LANGUAGES)
        self.assertEqual(set(manifest["languages"]) - reachable, set())
        # "uk" is English on HDBits, so no row can be Ukrainian.
        self.assertNotIn("ukr", manifest["languages"])

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

    def test_parse_subtitles_rejects_same_episode_from_another_season(self):
        rows = [
            {"filename": "Chernobyl.S02E01.en.srt", "id": 731, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.en.srt", "id": 732, "language": "uk", "title": "Chernobyl.S02E01.1080p"},
            {"filename": "Chernobyl.S01E01.en.srt", "id": 733, "language": "uk", "title": "Chernobyl"},
            {"filename": "Chernobyl.E01.en.srt", "id": 734, "language": "uk", "title": "Chernobyl"},
        ]

        parsed = self.mod.parse_subtitles(
            rows,
            requested_alpha3=[{"alpha3": "eng", "alpha2": "en"}],
            video=EPISODE_VIDEO,
            base_matches=["tvdb_id", "imdb_id", "series", "title", "season", "episode"],
            episode=1,
        )

        # S02E01 is not S01E01. A bare E01 carries no season and still counts.
        self.assertEqual([row["subtitle_id"] for row in parsed], [733, 734])


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

    def test_episode_search_requires_row_or_torrent_to_name_the_episode(self):
        provider = self.mod.HDBitsProvider()
        torrents = {
            "data": [
                {"id": 3001, "name": "Chernobyl S01E01 1080p WEB-DL-GROUP"},
                {"id": 3002, "name": "Chernobyl S01E02 1080p WEB-DL-GROUP"},
                {"id": 3003, "name": "Chernobyl S01 1080p WEB-DL-GROUP"},
            ]
        }
        subtitles = {
            3001: {"data": [{"filename": "Chernobyl.en.srt", "id": 901, "language": "uk", "title": "Chernobyl"}]},
            3002: {"data": [{"filename": "Chernobyl.en.srt", "id": 902, "language": "uk", "title": "Chernobyl"}]},
            3003: {"data": [{"filename": "Chernobyl.en.srt", "id": 903, "language": "uk", "title": "Chernobyl"}]},
        }

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            return subtitles[payload["torrent_id"]]

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        # Only the S01E01 torrent verifies an unnumbered direct subtitle.
        self.assertEqual([item["provider_payload"]["subtitle_id"] for item in results], [901])

    def test_episode_search_skips_torrent_for_another_season(self):
        provider = self.mod.HDBitsProvider()
        torrents = {"data": [{"id": 3101, "name": "Chernobyl S02E01 1080p WEB-DL-GROUP"}]}

        def post_stub(url, payload, timeout=15):
            del timeout
            if url == self.mod.TORRENTS_URL:
                return torrents
            raise AssertionError("a torrent for another season must not be scanned")

        provider._post_json = post_stub
        results = provider.search(
            EPISODE_VIDEO,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        self.assertEqual(results, [])

    def test_episode_scores_leave_room_for_release_matches(self):
        provider = self.mod.HDBitsProvider()
        torrents = {"data": [{"id": 3201, "name": "Chernobyl S01E01 1080p WEB-DL-GROUP"}]}
        subtitles = {
            "data": [
                {"filename": "Chernobyl.S01E01.en.srt", "id": 941, "language": "uk", "title": "Chernobyl.S01E01.HDTV"},
                {"filename": "Chernobyl.S01E01.en.srt", "id": 942, "language": "uk", "title": "Chernobyl.S01E01.1080p.WEB-DL-GROUP"},
            ]
        }
        provider._post_json = lambda url, payload, timeout=15: torrents if url == self.mod.TORRENTS_URL else subtitles
        video = {**EPISODE_VIDEO, "resolution": "1080p", "release_group": "GROUP"}

        results = provider.search(
            video,
            [{"alpha3": "eng", "alpha2": "en"}],
            {"username": "user", "passkey": "secret", "request_delay_ms": 0},
        )

        scores = {item["provider_payload"]["subtitle_id"]: item["score"] for item in results}
        # Six identity matches alone used to reach 100 and tie every row.
        self.assertEqual(scores[941], 70)
        self.assertGreater(scores[942], scores[941])
        self.assertLess(scores[942], 100)


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

        self.assertEqual(base64.b64decode(result["archive_b64"]), zip_body)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(zip_body).hexdigest())
        self.assertEqual(result["season"], 1)
        self.assertEqual(result["episode"], 1)
        self.assertTrue(result["select_member"])
        self.assertNotIn("content_b64", result)

    def test_archive_selector_pins_member_by_requested_language(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1},
            {"alpha3": "ell", "alpha2": "el"},
            ["Chernobyl.S01E01.en.srt", "Chernobyl.S01E01.gr.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Chernobyl.S01E01.gr.srt"})

    def test_archive_selector_prefers_explicit_portuguese_region_independent_of_member_order(self):
        provider = self.mod.HDBitsProvider()
        portuguese = {"alpha3": "por", "alpha2": "pt"}
        brazilian_portuguese = {
            "alpha3": "por",
            "alpha2": "pt",
            "country_alpha2": "BR",
        }
        members = ["Show.S01E01.pt.srt", "Show.S01E01.br.srt"]

        for language, expected in (
            (portuguese, "Show.S01E01.pt.srt"),
            (brazilian_portuguese, "Show.S01E01.br.srt"),
        ):
            for ordered_members in (members, list(reversed(members))):
                with self.subTest(language=language, members=ordered_members):
                    result = provider.select_archive_member(
                        {"season": 1, "episode": 1}, language, ordered_members, {}
                    )
                    self.assertEqual(result, {"decision": "pin", "member": expected})

    def test_archive_selector_rejects_explicit_brazilian_member_for_generic_portuguese(self):
        provider = self.mod.HDBitsProvider()
        for member in ("Show.S01E01.br.srt", "Show.S01E01.pt-BR.srt"):
            with self.subTest(member=member):
                result = provider.select_archive_member(
                    {"season": 1, "episode": 1},
                    {"alpha3": "por", "alpha2": "pt"},
                    [member],
                    {},
                )
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_uses_provider_country_when_host_language_has_no_region(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1, "country_alpha2": "BR"},
            {"alpha3": "por", "alpha2": "pt"},
            ["Show.S01E01.pt.srt", "Show.S01E01.br.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.br.srt"})

    def test_archive_selector_host_brazilian_language_overrides_stale_provider_country(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {
                "season": 1,
                "episode": 1,
                "language": "por",
                "country_alpha2": "PT",
            },
            {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
            ["Show.S01E01.pt.srt", "Show.S01E01.br.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.br.srt"})

    def test_archive_selector_accepts_explicit_portuguese_region_for_plain_portuguese(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {},
            {"alpha3": "por", "alpha2": "pt"},
            ["Film.pt-PT.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Film.pt-PT.srt"})

    def test_archive_selector_rejects_explicit_other_portuguese_region(self):
        provider = self.mod.HDBitsProvider()
        cases = (
            (
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR"},
                ["Film.pt-PT.srt"],
            ),
            (
                {"alpha3": "por", "alpha2": "pt", "country_alpha2": "PT"},
                ["Film.pt-BR.srt"],
            ),
        )

        for language, members in cases:
            with self.subTest(language=language, members=members):
                result = provider.select_archive_member({}, language, members, {})
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_preserves_provider_language_for_empty_host_language(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"language": "por", "country_alpha2": "BR"},
            {},
            ["Film.pt-PT.srt", "Film.pt-BR.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "pin", "member": "Film.pt-BR.srt"})

    def test_archive_selector_rejects_archive_missing_requested_episode(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1},
            {"alpha3": "eng", "alpha2": "en"},
            ["Chernobyl.S01E02.en.srt"],
            {},
        )

        self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_rejects_only_explicit_other_languages(self):
        provider = self.mod.HDBitsProvider()
        for payload, members in (
            ({"season": 1, "episode": 1}, ["Show.S01E01.en.srt", "Show.S01E01.gr.srt"]),
            ({}, ["Movie.en.srt", "Movie.gr.srt"]),
        ):
            with self.subTest(payload=payload):
                result = provider.select_archive_member(payload, {"alpha3": "fra"}, members, {})
                self.assertEqual(result, {"decision": "reject"})

    def test_archive_selector_allows_unlabelled_without_pinning_other_language(self):
        provider = self.mod.HDBitsProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 1}, {"alpha3": "fra"},
            ["Show.S01E01.en.srt", "Show.S01E01.srt"], {},
        )
        self.assertEqual(result, {"decision": "pin", "member": "Show.S01E01.srt"})

    def test_archive_selector_does_not_treat_title_words_as_language_tags(self):
        provider = self.mod.HDBitsProvider()
        name = "How.to.Train.Your.Dragon.S01E01.srt"
        result = provider.select_archive_member(
            {"season": 1, "episode": 1}, {"alpha3": "fra"}, [name], {},
        )
        self.assertEqual(result, {"decision": "pin", "member": name})

    def test_archive_selector_distinguishes_hindi_from_english_hi_suffix(self):
        provider = self.mod.HDBitsProvider()
        payload = {"season": 1, "episode": 1}
        for name in ("Show.S01E01.en.HI.srt", "Show.S01E01.en.SDH.HI.srt"):
            with self.subTest(name=name):
                self.assertEqual(provider.select_archive_member(payload, {"alpha3": "hin"}, [name], {}), {"decision": "reject"})
        name = "Show.S01E01.hi.srt"
        self.assertEqual(provider.select_archive_member(payload, {"alpha3": "hin"}, [name], {}), {"decision": "pin", "member": name})

    def test_download_rejects_empty_response(self):
        provider = self.mod.HDBitsProvider()
        provider._http_get = lambda url, timeout=15: b""

        with self.assertRaisesRegex(RuntimeError, "empty"):
            provider.download(
                {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": "movie.en.srt"},
                {"alpha3": "eng", "alpha2": "en"},
                {"username": "user", "passkey": "secret"},
            )

    def test_download_rejects_html_error_page(self):
        provider = self.mod.HDBitsProvider()
        page = b"<!DOCTYPE html>\n<html><body>Invalid passkey</body></html>"
        provider._http_get = lambda url, timeout=15: page

        for filename in ("Chernobyl.S01.en.zip", "Chernobyl.S01.gr.rar", "movie.en.srt"):
            with self.subTest(filename=filename), self.assertRaisesRegex(RuntimeError, "HTML"):
                provider.download(
                    {"provider": "hdbits", "schema": 1, "subtitle_id": 501, "filename": filename},
                    {"alpha3": "eng", "alpha2": "en"},
                    {"username": "user", "passkey": "secret"},
                )

    def test_content_payload_omits_guessed_encoding(self):
        body = "Zażółć gęślą jaźń".encode("cp1250")

        result = self.mod._content_payload(body, "srt")

        self.assertEqual(base64.b64decode(result["content_b64"]), body)
        self.assertNotIn("encoding", result)

    def test_download_returns_rar_archive_for_host_extraction(self):
        provider = self.mod.HDBitsProvider()
        raw_archive = b"rar bytes"
        provider._http_get = lambda url, timeout=15: raw_archive

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

        self.assertEqual(base64.b64decode(result["archive_b64"]), raw_archive)
