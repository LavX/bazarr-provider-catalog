"""Tests for the LegendasDivx Provider Hub plugin.

Three groups matter more than the rest:

* Parity. tests/fixtures/legendasdivx_builtin_parity.json records what the
  built-in provider (the reference implementation, verified against the live
  site by its author) returns for a set of inputs. The plugin has to agree.
* Double decode. cloudscraper hands back an already-decoded body while still
  reporting Content-Encoding. Decoding it a second time is the bug that was
  removed from the built-in, so a compressed-looking response has to survive.
* Wrong episode. An archive that does not carry the wanted episode must resolve
  to nothing, never to an arbitrary member.
"""

import base64
import hashlib
import importlib.util
import io
import json
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "legendasdivx"
FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location(
        "legendasdivx_provider", PROVIDER_DIR / "provider.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOGIN_HTML = (FIXTURE_DIR / "legendasdivx_login.html").read_bytes()
LOGIN_CAPTCHA_HTML = (FIXTURE_DIR / "legendasdivx_login_captcha.html").read_bytes()
SEARCH_DUNE_HTML = (FIXTURE_DIR / "legendasdivx_search_dune_por.html").read_bytes()
SEARCH_CHERNOBYL_HTML = (FIXTURE_DIR / "legendasdivx_search_chernobyl_pob.html").read_bytes()
SEARCH_LIMIT_HTML = (FIXTURE_DIR / "legendasdivx_search_limit.html").read_bytes()
VIDEO_DUNE = json.loads((FIXTURE_DIR / "legendasdivx_video_dune_2021.json").read_text("utf-8"))
VIDEO_CHERNOBYL = json.loads(
    (FIXTURE_DIR / "legendasdivx_video_chernobyl_s01e01.json").read_text("utf-8")
)
PARITY = json.loads((FIXTURE_DIR / "legendasdivx_builtin_parity.json").read_text("utf-8"))

MANIFEST = json.loads((PROVIDER_DIR / "provider.json").read_text("utf-8"))

POR = {"alpha3": "por", "alpha2": "pt", "hi": False, "forced": False}
POB = {"alpha3": "por", "alpha2": "pt", "country_alpha2": "BR", "hi": False, "forced": False}
CREDENTIALS = {"username": "user", "password": "secret", "request_delay_ms": 0}

AUTHENTICATED_COOKIES = {
    "phpbb3_2z8zs_u": "4242",
    "phpbb3_2z8zs_sid": "abcdef1234567890",
    "PHPSESSID": "sess4242",
}
ANONYMOUS_COOKIES = {"phpbb3_2z8zs_u": "1", "PHPSESSID": "sess0"}


class _Cookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.domain = ".legendasdivx.pt"
        self.path = "/"


class _CookieJar:
    """Enough of a requests cookie jar for the provider: iterate, set, clear."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    def __iter__(self):
        return iter([_Cookie(name, value) for name, value in list(self._values.items())])

    def set(self, name, value, **kwargs):
        del kwargs
        self._values[name] = value

    def clear(self, domain=None, path=None, name=None):
        del domain, path
        self._values.pop(name, None)


class _Response:
    def __init__(self, status_code, content, headers=None, url=""):
        self.status_code = status_code
        self.content = content
        self.headers = dict(headers or {})
        self.url = url


class FakeScraper:
    """Stands in for the ai-cloudscraper session.

    Routes are keyed by (method, url). Each entry is a _Response or a callable
    taking the scraper so a route can mutate the cookie jar the way a real login
    response would.
    """

    def __init__(self, routes, cookies=None):
        self.routes = routes
        self.cookies = _CookieJar(cookies)
        self.calls = []

    def request(self, method, url, data=None, headers=None, timeout=None, allow_redirects=True):
        del timeout, allow_redirects
        self.calls.append((method, url, data, dict(headers or {})))
        key = (method, url)
        if key not in self.routes:
            raise AssertionError(f"unexpected request: {method} {url}")
        route = self.routes[key]
        if callable(route):
            route = route(self)
        if isinstance(route, list):
            route = route.pop(0)
        return route


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _rar_body():
    # A rar the worker must not try to open: it only has to look like one.
    return b"Rar!\x1a\x07\x01\x00" + b"\x00" * 64


class LegendasDivxParityTests(unittest.TestCase):
    """The built-in provider is the oracle; the plugin must agree with it."""

    def setUp(self):
        self.mod = _load_provider_module()

    def test_clean_release_line_matches_the_builtin(self):
        for case in PARITY["clean_release_line"]:
            with self.subTest(text=case["text"]):
                self.assertEqual(self.mod.clean_release_line(case["text"]), case["expected"])

    def test_extract_release_info_matches_the_builtin(self):
        for case in PARITY["extract_release_info"]:
            with self.subTest(desc=case["desc"]):
                self.assertEqual(
                    self.mod.extract_release_info(case["title"], case["year"], case["desc"]),
                    case["expected"],
                )

    def test_member_episode_matching_matches_the_builtin(self):
        for case in PARITY["member_matches_episode"]:
            with self.subTest(name=case["name"], episode=case["episode"]):
                self.assertEqual(
                    self.mod.member_matches_episode(
                        case["name"],
                        case["season"],
                        case["episode"],
                        case["absolute_episode"],
                    ),
                    case["expected"],
                )


class LegendasDivxReleaseInfoTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_uploader_commentary_is_stripped(self):
        for prefix in (
            "Legendas anteriormente enviadas pelo fulano, ressincronizadas por mim para a(s) release(s):\n",
            "Sincronizadas para a versao: ",
            "Sincronizadas para a release: ",
            "ripadas por mim para a release: ",
            "ajustei a sincronia para a release: ",
        ):
            with self.subTest(prefix=prefix):
                desc = prefix + "The.Matrix.1999.1080p.BluRay.x264-SPARKS"
                self.assertEqual(
                    self.mod.extract_release_info("The Matrix", 1999, desc),
                    "The.Matrix.1999.1080p.BluRay.x264-SPARKS",
                )

    def test_a_specific_release_beats_conversational_text(self):
        desc = (
            "Enjoy! Feita a partir do DVD.\n"
            "The.Matrix.1999.1080p.BluRay.x264-SPARKS\n"
            "Avisem se houver erros."
        )
        self.assertEqual(
            self.mod.extract_release_info("The Matrix", 1999, desc),
            "The.Matrix.1999.1080p.BluRay.x264-SPARKS",
        )

    def test_no_usable_description_falls_back_to_title_and_year(self):
        for desc in ("Nao ha descricao disponivel", "n/a", "none", "   "):
            with self.subTest(desc=desc):
                self.assertEqual(
                    self.mod.extract_release_info("The Matrix", 1999, desc), "The Matrix (1999)"
                )

    def test_fallback_without_a_year_is_just_the_title(self):
        self.assertEqual(self.mod.extract_release_info("The Matrix", None, "n/a"), "The Matrix")


class LegendasDivxParserTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_parse_search_results_reads_the_result_header(self):
        rows = self.mod.parse_search_results(SEARCH_DUNE_HTML)
        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["lid"], "1101")
        self.assertEqual(first["title"], "Dune: Part One")
        self.assertEqual(first["year"], 2021)
        self.assertEqual(first["uploader"], "pt_uploader")
        self.assertEqual(first["hits"], 91)
        self.assertEqual(first["frame_rate"], "23.976")
        self.assertEqual(first["language"], "por")
        self.assertEqual(
            first["page_link"],
            "https://www.legendasdivx.pt/modules.php?name=Downloads&d_op=getit&lid=1101",
        )
        self.assertEqual(
            first["release_info"], "Dune.Part.One.2021.1080p.WEB-DL.DDP5.1.H.264-NTb"
        )

    def test_title_and_year_come_from_the_header_not_the_description(self):
        # The third Dune row has no usable description at all.
        row = self.mod.parse_search_results(SEARCH_DUNE_HTML)[2]
        self.assertEqual(row["title"], "Dune: Part One")
        self.assertEqual(row["year"], 2021)
        self.assertEqual(row["release_info"], "Dune: Part One (2021)")

    def test_language_is_read_from_the_flag_cell(self):
        rows = self.mod.parse_search_results(SEARCH_CHERNOBYL_HTML)
        self.assertEqual([row["language"] for row in rows], ["por-BR", "por"])

    def test_description_keeps_its_line_structure(self):
        row = self.mod.parse_search_results(SEARCH_CHERNOBYL_HTML)[0]
        self.assertIn("\n", row["description"])

    def test_empty_page_yields_no_rows(self):
        self.assertEqual(self.mod.parse_search_results(b"<html><body></body></html>"), [])

    def test_parse_login_inputs_collects_the_session_fields(self):
        data = self.mod.parse_login_inputs(LOGIN_HTML)
        self.assertEqual(data["sid"], "1890def6632a667396e33b8df3ac94ac")
        self.assertEqual(data["redirect"], "index.php")
        self.assertIn("username", data)

    def test_page_count_is_capped(self):
        self.assertEqual(self.mod._page_count(b'<div class="pager_bar">(12 encontradas)</div>'), 2)
        self.assertEqual(self.mod._page_count(b'<div class="pager_bar">(400 encontradas)</div>'), 6)
        self.assertEqual(self.mod._page_count(b"<html></html>"), 1)


class LegendasDivxQueryTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_movie_query_uses_the_imdb_id_and_language_filter(self):
        urls = self.mod.build_search_urls(VIDEO_DUNE, "por")
        self.assertEqual(len(urls), 1)
        self.assertIn("query=tt1160419", urls[0])
        self.assertIn("form_cat=28", urls[0])

    def test_brazilian_filter_id_differs(self):
        self.assertIn("form_cat=29", self.mod.build_search_urls(VIDEO_DUNE, "por-BR")[0])

    def test_episode_with_a_series_imdb_id_uses_the_episode_endpoint(self):
        urls = self.mod.build_search_urls(VIDEO_CHERNOBYL, "por-BR")
        self.assertEqual(len(urls), 1)
        self.assertIn("faz=pesquisa_episodio", urls[0])
        self.assertIn("imdb=7366338", urls[0])
        self.assertIn("idioma=29", urls[0])

    def test_episode_without_a_series_imdb_id_falls_back_to_the_season_pack_query(self):
        video = dict(VIDEO_CHERNOBYL)
        video.pop("series_imdb_id")
        urls = self.mod.build_search_urls(video, "por")
        self.assertEqual(len(urls), 2)
        self.assertIn("s01e01", urls[0])
        self.assertNotIn("s01e01", urls[1])

    def test_unknown_language_and_kind_produce_no_urls(self):
        self.assertEqual(self.mod.build_search_urls(VIDEO_DUNE, "eng"), [])
        self.assertEqual(self.mod.build_search_urls({"kind": "other"}, "por"), [])


class LegendasDivxLanguageTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_requested_languages_accepts_every_spelling_of_brazilian_portuguese(self):
        self.assertEqual(self.mod._requested_languages([POB]), ["por-BR"])
        self.assertEqual(self.mod._requested_languages([{"alpha3": "pob"}]), ["por-BR"])
        self.assertEqual(
            self.mod._requested_languages([{"alpha3": "por", "country": "BR"}]), ["por-BR"]
        )

    def test_requested_languages_ignores_other_languages(self):
        self.assertEqual(self.mod._requested_languages([{"alpha3": "eng"}]), [])

    def test_brazilian_portuguese_is_por_plus_a_country(self):
        payload = self.mod._language_payload("por-BR")
        self.assertEqual(payload["alpha3"], "por")
        self.assertEqual(payload["country_alpha2"], "BR")
        self.assertNotIn("country_alpha2", self.mod._language_payload("por"))


class LegendasDivxMatchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _row(self, html_bytes, index=0):
        return self.mod.parse_search_results(html_bytes)[index]

    def test_movie_matches_title_year_and_release_tags(self):
        matches = self.mod.derive_matches(VIDEO_DUNE, self._row(SEARCH_DUNE_HTML))
        self.assertIn("title", matches)
        self.assertIn("year", matches)
        self.assertIn("resolution", matches)
        self.assertIn("release_group", matches)

    def test_movie_imdb_id_needs_evidence_in_the_result(self):
        row = self._row(SEARCH_DUNE_HTML)
        self.assertNotIn("imdb_id", self.mod.derive_matches(VIDEO_DUNE, row))
        row["description"] = row["description"] + " imdb tt1160419"
        self.assertIn("imdb_id", self.mod.derive_matches(VIDEO_DUNE, row))

    def test_episode_with_a_series_imdb_id_claims_the_backend_guarantee(self):
        matches = self.mod.derive_matches(VIDEO_CHERNOBYL, self._row(SEARCH_CHERNOBYL_HTML))
        for key in ("series", "series_imdb_id", "season", "episode"):
            self.assertIn(key, matches)

    def test_episode_without_a_series_imdb_id_proves_season_and_episode(self):
        video = dict(VIDEO_CHERNOBYL)
        video.pop("series_imdb_id")
        matches = self.mod.derive_matches(video, self._row(SEARCH_CHERNOBYL_HTML))
        self.assertIn("series", matches)
        self.assertIn("season", matches)
        self.assertIn("episode", matches)

    def test_wrong_episode_in_the_description_is_not_an_episode_match(self):
        video = dict(VIDEO_CHERNOBYL)
        video.pop("series_imdb_id")
        video["episode"] = 4
        matches = self.mod.derive_matches(video, self._row(SEARCH_CHERNOBYL_HTML))
        self.assertNotIn("episode", matches)


class LegendasDivxArchiveMemberTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_season_pack_with_episode_only_member_names_resolves(self):
        members = [
            "101 - Pilot.srt",
            "102 - Cat's in the Bag....srt",
            "103 - ...And the Bag's in the River.srt",
        ]
        member, decision = self.mod.pick_archive_member(members, {"season": 1, "episode": 3})
        self.assertEqual(decision, "pin")
        self.assertEqual(member, "103 - ...And the Bag's in the River.srt")

    def test_multi_episode_member_names_resolve(self):
        members = ["Show.S01E01-E02.srt", "Show.S01E03.srt"]
        for episode in (1, 2):
            with self.subTest(episode=episode):
                member, decision = self.mod.pick_archive_member(
                    members, {"season": 1, "episode": episode}
                )
                self.assertEqual((member, decision), ("Show.S01E01-E02.srt", "pin"))

    def test_episode_in_the_directory_is_matched(self):
        members = ["Show.S01E01/subtitle.srt", "Show.S01E02/subtitle.srt"]
        member, decision = self.mod.pick_archive_member(members, {"season": 1, "episode": 2})
        self.assertEqual((member, decision), ("Show.S01E02/subtitle.srt", "pin"))

    def test_absolute_numbering_is_accepted(self):
        members = ["[HorribleSubs] One Piece - 310 [1080p].srt"]
        member, decision = self.mod.pick_archive_member(
            members, {"season": 10, "episode": 14, "absolute_episode": 310}
        )
        self.assertEqual((member, decision), (members[0], "pin"))

    def test_an_absent_episode_returns_nothing_rather_than_an_arbitrary_member(self):
        members = [f"1{index:02d} - Episode.srt" for index in range(1, 8)]
        self.assertEqual(
            self.mod.pick_archive_member(members, {"season": 1, "episode": 8}), (None, "reject")
        )

    def test_a_single_member_archive_is_still_checked(self):
        self.assertEqual(
            self.mod.pick_archive_member(["Breaking.Bad.S01E01.srt"], {"season": 1, "episode": 5}),
            (None, "reject"),
        )

    def test_a_movie_archive_defers_to_the_host_picker(self):
        member, decision = self.mod.pick_archive_member(["Dune.srt"], {"release_info": "Dune"})
        self.assertEqual((member, decision), (None, "defer"))

    def test_an_archive_without_subtitle_members_is_rejected(self):
        self.assertEqual(
            self.mod.pick_archive_member(["readme.nfo", "cover.jpg"], {"season": 1, "episode": 1}),
            (None, "reject"),
        )

    def test_release_info_breaks_a_tie_between_matching_members(self):
        members = [
            "Show.S01E02.720p.HDTV.x264-AAA.srt",
            "Show.S01E02.1080p.WEB.H264-BBB.srt",
        ]
        member, decision = self.mod.pick_archive_member(
            members,
            {"season": 1, "episode": 2, "release_info": "Show.S01E02.1080p.WEB.H264-BBB"},
        )
        self.assertEqual((member, decision), ("Show.S01E02.1080p.WEB.H264-BBB.srt", "pin"))

    def test_select_archive_member_returns_the_worker_contract_shape(self):
        provider = self.mod.LegendasDivxProvider()
        result = provider.select_archive_member(
            {"season": 1, "episode": 3}, POR, ["103 - Pilot.srt"], {}
        )
        self.assertEqual(result, {"member": "103 - Pilot.srt", "decision": "pin"})


class LegendasDivxDownloadTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def test_a_zip_is_handed_back_for_host_side_extraction(self):
        body = _zip_body({"Show.S01E02.srt": "1\n00:00:01,000 --> 00:00:02,000\nhi\n"})
        payload = self.mod._download_payload(body, {"season": 1, "episode": 2})
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)
        self.assertEqual(payload["archive_sha256"], hashlib.sha256(body).hexdigest())
        self.assertTrue(payload["select_member"])
        self.assertEqual(payload["episode"], 2)
        self.assertNotIn("content_b64", payload)

    def test_a_rar_is_handed_back_without_being_opened(self):
        body = _rar_body()
        payload = self.mod._download_payload(body, {"season": 1, "episode": 2})
        self.assertTrue(payload["select_member"])
        self.assertEqual(base64.b64decode(payload["archive_b64"]), body)

    def test_a_direct_subtitle_is_returned_as_content(self):
        body = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nOla\r\n"
        payload = self.mod._download_payload(body, {"filename": "legendasdivx.x.pt.srt"})
        self.assertEqual(payload["format"], "srt")
        self.assertEqual(payload["content_type"], "application/x-subrip")
        self.assertEqual(base64.b64decode(payload["content_b64"]), body.replace(b"\r\n", b"\n"))
        # The host detects the encoding; a worker guess only reintroduces mojibake.
        self.assertNotIn("encoding", payload)

    def test_an_html_error_page_is_not_a_subtitle(self):
        with self.assertRaises(ValueError):
            self.mod._download_payload(b"<!DOCTYPE html><html><body>erro</body></html>", {})

    def test_an_empty_body_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mod._download_payload(b"   ", {})


class LegendasDivxSessionTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, routes, cookies=None):
        provider = self.mod.LegendasDivxProvider()
        provider._scraper = FakeScraper(routes, cookies)
        provider._scraper_initialized = True
        return provider

    @staticmethod
    def _login_routes(cookies_after_login, login_body=LOGIN_HTML):
        def login_post(scraper):
            for name, value in cookies_after_login.items():
                scraper.cookies.set(name, value)
            return _Response(200, b"<html><body>Ligado</body></html>")

        return {
            ("GET", "https://www.legendasdivx.pt/forum/ucp.php?mode=login"): _Response(
                200, login_body
            ),
            ("POST", "https://www.legendasdivx.pt/forum/ucp.php?mode=login"): login_post,
        }

    def test_login_failure_is_detected_from_the_session_cookies(self):
        provider = self._provider(self._login_routes(ANONYMOUS_COOKIES))
        with self.assertRaises(self.mod.AuthenticationError):
            provider._ensure_authenticated(dict(CREDENTIALS))
        self.assertFalse(provider._authenticated)

    def test_login_without_a_session_id_is_a_failure(self):
        provider = self._provider(self._login_routes({"phpbb3_2z8zs_u": "4242"}))
        with self.assertRaises(self.mod.AuthenticationError):
            provider._ensure_authenticated(dict(CREDENTIALS))

    def test_login_succeeds_when_the_cookies_name_a_real_user(self):
        provider = self._provider(self._login_routes(AUTHENTICATED_COOKIES))
        provider._ensure_authenticated(dict(CREDENTIALS))
        self.assertTrue(provider._authenticated)

    def test_missing_credentials_raise_before_any_request(self):
        provider = self._provider({})
        with self.assertRaises(self.mod.AuthenticationError):
            provider._ensure_authenticated({})

    def test_a_captcha_gated_login_without_a_solver_says_so(self):
        provider = self._provider(
            self._login_routes(AUTHENTICATED_COOKIES, login_body=LOGIN_CAPTCHA_HTML)
        )
        with self.assertRaises(self.mod.AuthenticationError) as caught:
            provider._ensure_authenticated(dict(CREDENTIALS))
        self.assertIn("captcha_solver_url", str(caught.exception))

    def test_a_pre_solved_captcha_token_is_posted_with_the_credentials(self):
        provider = self._provider(
            self._login_routes(AUTHENTICATED_COOKIES, login_body=LOGIN_CAPTCHA_HTML)
        )
        config = dict(CREDENTIALS, captcha_response="token-123")
        provider._ensure_authenticated(config)
        post = [call for call in provider._scraper.calls if call[0] == "POST"][0]
        self.assertEqual(post[2]["g-recaptcha-response"], "token-123")

    def test_a_body_that_reports_an_encoding_is_not_decoded_twice(self):
        """cloudscraper reports Content-Encoding on a body it already decoded.

        Decompressing it again on the strength of that header is the failure that
        was removed from the built-in provider's session, so a response carrying
        the header with plain bytes has to come through untouched.
        """
        routes = self._login_routes(AUTHENTICATED_COOKIES)
        routes[("GET", "https://www.legendasdivx.pt/forum/ucp.php?mode=login")] = _Response(
            200, LOGIN_HTML, headers={"Content-Encoding": "br", "Content-Type": "text/html"}
        )
        provider = self._provider(routes)
        response = provider._http_get(
            "https://www.legendasdivx.pt/forum/ucp.php?mode=login", config={}
        )
        self.assertEqual(response.body, LOGIN_HTML)
        self.assertIn("sid", self.mod.parse_login_inputs(response.body))

    def test_a_cloudflare_block_without_flaresolverr_is_reported(self):
        routes = {
            ("GET", "https://www.legendasdivx.pt/x"): _Response(
                403, b"<html><title>Just a moment...</title></html>", headers={"Server": "cloudflare"}
            )
        }
        provider = self._provider(routes)
        with self.assertRaises(self.mod.CloudflareBlockedError):
            provider._http_get("https://www.legendasdivx.pt/x", config={})


class LegendasDivxSearchTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, routes, cookies=AUTHENTICATED_COOKIES):
        provider = self.mod.LegendasDivxProvider()
        provider._scraper = FakeScraper(routes, cookies)
        provider._scraper_initialized = True
        provider._authenticated = True
        return provider

    def _search_routes(self, video, language, body):
        urls = self.mod.build_search_urls(video, language)
        routes = {}
        for url in urls:
            routes[("GET", url)] = _Response(200, body)
            for page in range(2, self.mod.MAX_PAGES + 1):
                routes[("GET", f"{url}&page={page}")] = _Response(
                    200, b"<html><body></body></html>"
                )
        return routes

    def test_movie_search_returns_scored_candidates(self):
        provider = self._provider(self._search_routes(VIDEO_DUNE, "por", SEARCH_DUNE_HTML))
        results = provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))
        self.assertEqual(len(results), 3)
        best = results[0]
        self.assertEqual(best["provider"], "legendasdivx")
        self.assertEqual(best["language"], POR)
        self.assertEqual(best["release_info"], "Dune.Part.One.2021.1080p.WEB-DL.DDP5.1.H.264-NTb")
        self.assertEqual(best["provider_payload"]["lid"], "1101")
        self.assertGreaterEqual(best["score"], results[-1]["score"])

    def test_skip_wrong_fps_drops_mismatched_rows(self):
        provider = self._provider(self._search_routes(VIDEO_DUNE, "por", SEARCH_DUNE_HTML))
        results = provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS, skip_wrong_fps=True))
        self.assertEqual([row["provider_payload"]["lid"] for row in results], ["1101", "1103"])

    def test_episode_search_carries_the_episode_into_the_payload(self):
        provider = self._provider(
            self._search_routes(VIDEO_CHERNOBYL, "por-BR", SEARCH_CHERNOBYL_HTML)
        )
        results = provider.search(VIDEO_CHERNOBYL, [POB], dict(CREDENTIALS))
        self.assertEqual(len(results), 1)
        payload = results[0]["provider_payload"]
        self.assertEqual(payload["season"], 1)
        self.assertEqual(payload["episode"], 1)
        self.assertEqual(results[0]["language"], POB)

    def test_the_daily_search_limit_is_surfaced(self):
        provider = self._provider(self._search_routes(VIDEO_DUNE, "por", SEARCH_LIMIT_HTML))
        with self.assertRaises(self.mod.SearchLimitReached):
            provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))

    def test_a_blocked_ip_is_surfaced(self):
        body = b"<html><body>O seu IP foi bloqueado</body></html>"
        provider = self._provider(self._search_routes(VIDEO_DUNE, "por", body))
        with self.assertRaises(self.mod.IPAddressBlocked):
            provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))

    def test_an_unsupported_language_short_circuits(self):
        provider = self._provider({})
        self.assertEqual(provider.search(VIDEO_DUNE, [{"alpha3": "eng"}], dict(CREDENTIALS)), [])


class LegendasDivxDownloadFlowTests(unittest.TestCase):
    PAGE_LINK = "https://www.legendasdivx.pt/modules.php?name=Downloads&d_op=getit&lid=1101"

    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, routes):
        provider = self.mod.LegendasDivxProvider()
        provider._scraper = FakeScraper(routes, AUTHENTICATED_COOKIES)
        provider._scraper_initialized = True
        provider._authenticated = True
        return provider

    def test_download_returns_an_archive_payload(self):
        body = _zip_body({"Show.S01E01.srt": "1\n00:00:01,000 --> 00:00:02,000\nhi\n"})
        provider = self._provider({("GET", self.PAGE_LINK): _Response(200, body)})
        payload = provider.download(
            {"page_link": self.PAGE_LINK, "season": 1, "episode": 1}, POR, dict(CREDENTIALS)
        )
        self.assertTrue(payload["select_member"])

    def test_the_daily_download_limit_is_surfaced(self):
        body = b"<html><body>Limite de downloads diario atingido</body></html>"
        provider = self._provider({("GET", self.PAGE_LINK): _Response(200, body)})
        with self.assertRaises(self.mod.DownloadLimitExceeded):
            provider.download({"page_link": self.PAGE_LINK}, POR, dict(CREDENTIALS))

    def test_a_redirect_to_the_login_page_re_authenticates_once(self):
        srt = b"1\n00:00:01,000 --> 00:00:02,000\nOla\n"
        login_url = "https://www.legendasdivx.pt/forum/ucp.php?mode=login"

        def login_post(scraper):
            for name, value in AUTHENTICATED_COOKIES.items():
                scraper.cookies.set(name, value)
            return _Response(200, b"<html><body>Ligado</body></html>")

        routes = {
            ("GET", self.PAGE_LINK): [
                _Response(302, b"", headers={"Location": login_url}),
                _Response(200, srt),
            ],
            ("GET", login_url): _Response(200, LOGIN_HTML),
            ("POST", login_url): login_post,
        }
        provider = self._provider(routes)
        payload = provider.download(
            {"page_link": self.PAGE_LINK, "filename": "x.srt"}, POR, dict(CREDENTIALS)
        )
        self.assertEqual(base64.b64decode(payload["content_b64"]), srt)
        self.assertIn(("POST", login_url), [(call[0], call[1]) for call in provider._scraper.calls])

    def test_a_persistent_redirect_is_an_authentication_failure(self):
        login_url = "https://www.legendasdivx.pt/forum/ucp.php?mode=login"

        def login_post(scraper):
            for name, value in AUTHENTICATED_COOKIES.items():
                scraper.cookies.set(name, value)
            return _Response(200, b"<html><body>Ligado</body></html>")

        routes = {
            ("GET", self.PAGE_LINK): [
                _Response(302, b"", headers={"Location": login_url}),
                _Response(302, b"", headers={"Location": login_url}),
            ],
            ("GET", login_url): _Response(200, LOGIN_HTML),
            ("POST", login_url): login_post,
        }
        provider = self._provider(routes)
        with self.assertRaises(self.mod.AuthenticationError):
            provider.download({"page_link": self.PAGE_LINK}, POR, dict(CREDENTIALS))

    def test_download_without_a_page_link_is_a_value_error(self):
        provider = self._provider({})
        with self.assertRaises(ValueError):
            provider.download({}, POR, dict(CREDENTIALS))


class LegendasDivxManifestTests(unittest.TestCase):
    BANNED_PACKAGES = {
        # Archive backends: the host extracts, the worker never does.
        "py7zz", "py7zr", "rarfile", "patool",
        # Packages that leaked in from an authoring environment on the first pass.
        "typer", "rich", "click", "shellingham", "markdown-it-py", "mdurl",
        "pygments", "annotated-doc", "packaging",
    }

    def test_the_manifest_declares_the_anti_bot_capabilities(self):
        self.assertTrue(MANIFEST["anti_captcha"])
        self.assertTrue(MANIFEST["flaresolverr"])

    def test_cloudscraper_is_declared_with_hashes(self):
        requirements = {req["name"]: req for req in MANIFEST["dependencies"]["requirements"]}
        self.assertIn("ai-cloudscraper", requirements)
        for name, requirement in requirements.items():
            with self.subTest(package=name):
                self.assertTrue(requirement.get("version"))
                self.assertTrue(requirement.get("hashes"))

    def test_no_archive_backend_or_authoring_leftovers_are_declared(self):
        declared = {req["name"].lower() for req in MANIFEST["dependencies"]["requirements"]}
        self.assertEqual(declared & {name.lower() for name in self.BANNED_PACKAGES}, set())

    def test_the_config_schema_exposes_the_anti_bot_knobs(self):
        properties = MANIFEST["config_schema"]["properties"]
        for key in (
            "flaresolverr_url",
            "flaresolverr_timeout_ms",
            "captcha_solver_url",
            "captcha_solver_token",
            "captcha_solver_timeout_ms",
            "captcha_response",
        ):
            self.assertIn(key, properties)
        self.assertEqual(MANIFEST["config_schema"]["required"], ["username", "password"])

    def test_credentials_are_declared_secret(self):
        for key in ("username", "password", "captcha_solver_token", "captcha_response"):
            self.assertIn(key, MANIFEST["secret_fields"])

    def test_the_worker_imports_no_archive_or_subprocess_library(self):
        source = (PROVIDER_DIR / "provider.py").read_text("utf-8")
        for banned in ("import subprocess", "import shutil", "import tempfile", "py7zz", "rarfile"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
