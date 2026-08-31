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
from http.cookiejar import CookieJar
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

    def test_the_plugin_diverges_from_the_builtin_only_where_recorded(self):
        """Every deliberate divergence is pinned, so neither side drifts unnoticed."""
        for case in PARITY["member_matches_episode_intentional_divergences"]:
            with self.subTest(name=case["name"], episode=case["episode"]):
                self.assertNotEqual(case["builtin"], case["plugin"])
                self.assertTrue(case["reason"].strip())
                self.assertEqual(
                    self.mod.member_matches_episode(
                        case["name"], case["season"], case["episode"], case["absolute_episode"]
                    ),
                    case["plugin"],
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



class LegendasDivxReviewRegressionTests(unittest.TestCase):
    """Regressions from the review of the first port commit.

    Each of these reproduced a real defect before the fix: a persistent login
    redirect read as an empty result page, a season-only path segment dropped
    when a member name falls through to bare numbers, an ordinary origin error
    proxied by Cloudflare classified as a challenge, and a challenged login POST
    replayed as a GET with the credentials thrown away.
    """

    PAGE_LINK = "https://www.legendasdivx.pt/modules.php?name=Downloads&d_op=getit&lid=1101"
    LOGIN_URL = "https://www.legendasdivx.pt/forum/ucp.php?mode=login"

    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, routes, cookies=AUTHENTICATED_COOKIES, authenticated=True):
        provider = self.mod.LegendasDivxProvider()
        provider._scraper = FakeScraper(routes, cookies)
        provider._scraper_initialized = True
        provider._authenticated = authenticated
        return provider

    def _login_routes(self, cookies_after_login=AUTHENTICATED_COOKIES):
        def login_post(scraper):
            for name, value in cookies_after_login.items():
                scraper.cookies.set(name, value)
            return _Response(200, b"<html><body>Ligado</body></html>")

        return {
            ("GET", self.LOGIN_URL): _Response(200, LOGIN_HTML),
            ("POST", self.LOGIN_URL): login_post,
        }

    # --- a persistent login redirect is an authentication failure, not "no results"

    def test_a_search_still_redirected_after_relogin_is_an_authentication_failure(self):
        routes = self._login_routes()
        redirect = _Response(302, b"", headers={"Location": self.LOGIN_URL})
        for url in self.mod.build_search_urls(VIDEO_DUNE, "por"):
            routes[("GET", url)] = [redirect, redirect]
        provider = self._provider(routes)
        with self.assertRaises(self.mod.AuthenticationError):
            provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))

    def test_a_pagination_redirect_that_survives_relogin_is_an_authentication_failure(self):
        routes = self._login_routes()
        redirect = _Response(302, b"", headers={"Location": self.LOGIN_URL})
        for url in self.mod.build_search_urls(VIDEO_DUNE, "por"):
            routes[("GET", url)] = _Response(200, SEARCH_DUNE_HTML)
            routes[("GET", f"{url}&page=2")] = [redirect, redirect]
        provider = self._provider(routes)
        with self.assertRaises(self.mod.AuthenticationError):
            provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))

    # --- a season stated by a path segment still binds a bare episode number

    def test_a_season_only_path_segment_binds_the_bare_episode_number(self):
        for name in ("Season 1/02.srt", "Show.S01/02.srt", "Temporada 1/02.srt"):
            with self.subTest(name=name):
                self.assertTrue(self.mod.member_matches_episode(name, 1, 2))
                # Season two episode two is not season one episode two.
                self.assertFalse(self.mod.member_matches_episode(name, 2, 2))

    def test_a_season_number_is_not_also_read_as_an_episode(self):
        self.assertFalse(self.mod.member_matches_episode("Season 1/02.srt", 1, 1))

    def test_a_season_only_pack_does_not_pin_a_member_from_another_season(self):
        members = ["Season 1/01.srt", "Season 1/02.srt"]
        self.assertEqual(
            self.mod.pick_archive_member(members, {"season": 2, "episode": 2}), (None, "reject")
        )

    # --- an ordinary origin error proxied by Cloudflare is not a challenge

    def test_a_proxied_origin_error_is_not_a_cloudflare_challenge(self):
        for status, body in (
            (403, b"<html><body>O seu IP foi bloqueado</body></html>"),
            (429, b"<html><body>Demasiados pedidos</body></html>"),
            (503, b"<html><body>Manutencao</body></html>"),
        ):
            with self.subTest(status=status):
                self.assertFalse(
                    self.mod._is_cloudflare_challenge(status, {"Server": "cloudflare"}, body)
                )

    def test_a_real_challenge_is_still_detected(self):
        self.assertTrue(
            self.mod._is_cloudflare_challenge(
                403, {"Server": "cloudflare"}, b"<html><title>Just a moment...</title></html>"
            )
        )
        self.assertTrue(
            self.mod._is_cloudflare_challenge(403, {"cf-mitigated": "challenge"}, b"")
        )

    def test_a_proxied_ip_block_reaches_the_ip_block_error(self):
        body = b"<html><body>O seu IP foi bloqueado</body></html>"
        routes = {("GET", self.PAGE_LINK): _Response(403, body, headers={"Server": "cloudflare"})}
        provider = self._provider(routes)
        with self.assertRaises(self.mod.IPAddressBlocked):
            provider.download({"page_link": self.PAGE_LINK}, POR, dict(CREDENTIALS))

    # --- a challenged POST keeps its method and its form data

    def test_a_challenged_login_post_is_replayed_as_a_post_through_flaresolverr(self):
        challenge = _Response(
            503,
            b"<html><title>Just a moment...</title><body>cf-challenge</body></html>",
            headers={"Server": "cloudflare"},
        )
        routes = {
            ("GET", self.LOGIN_URL): _Response(200, LOGIN_HTML),
            ("POST", self.LOGIN_URL): challenge,
        }
        provider = self._provider(routes, cookies={}, authenticated=False)
        sent = []

        class _Solved:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            del timeout
            sent.append(json.loads(request.data.decode("utf-8")))
            return _Solved(
                {
                    "status": "ok",
                    "solution": {
                        "status": 200,
                        "url": self.LOGIN_URL,
                        "userAgent": "FlareSolverr/UA",
                        "response": "<html><body>Ligado</body></html>",
                        "cookies": [
                            {"name": name, "value": value}
                            for name, value in AUTHENTICATED_COOKIES.items()
                        ],
                    },
                }
            )

        original = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            provider._ensure_authenticated(
                dict(CREDENTIALS, flaresolverr_url="http://127.0.0.1:8191/v1")
            )
        finally:
            self.mod.urllib.request.urlopen = original

        self.assertTrue(provider._authenticated)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["cmd"], "request.post")
        self.assertIn("username=user", sent[0]["postData"])
        self.assertIn("sid=1890def6632a667396e33b8df3ac94ac", sent[0]["postData"])

    def test_a_challenged_get_still_uses_request_get(self):
        challenge = _Response(
            503,
            b"<html><title>Just a moment...</title></html>",
            headers={"Server": "cloudflare"},
        )
        provider = self._provider({("GET", self.PAGE_LINK): challenge})
        sent = []

        class _Solved:
            def read(self):
                return json.dumps(
                    {
                        "status": "ok",
                        "solution": {"status": 200, "response": "1\n00:00:01,000 --> 00:00:02,000\nOla\n"},
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            del timeout
            sent.append(json.loads(request.data.decode("utf-8")))
            return _Solved()

        original = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            payload = provider.download(
                {"page_link": self.PAGE_LINK, "filename": "x.srt"},
                POR,
                dict(CREDENTIALS, flaresolverr_url="http://127.0.0.1:8191/v1"),
            )
        finally:
            self.mod.urllib.request.urlopen = original

        self.assertEqual(sent[0]["cmd"], "request.get")
        self.assertNotIn("postData", sent[0])
        self.assertIn("content_b64", payload)




class LegendasDivxSecondReviewRegressionTests(unittest.TestCase):
    """Regressions from the second review round."""

    PAGE_LINK = "https://www.legendasdivx.pt/modules.php?name=Downloads&d_op=getit&lid=1101"
    LOGIN_URL = "https://www.legendasdivx.pt/forum/ucp.php?mode=login"
    FLARESOLVERR = "http://127.0.0.1:8191/v1"

    def setUp(self):
        self.mod = _load_provider_module()

    def _provider(self, routes, cookies=AUTHENTICATED_COOKIES, authenticated=True):
        provider = self.mod.LegendasDivxProvider()
        provider._scraper = FakeScraper(routes, cookies)
        provider._scraper_initialized = True
        provider._authenticated = authenticated
        return provider

    def _stub_flaresolverr(self, solution, sent=None):
        class _Solved:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            del timeout
            if sent is not None:
                sent.append(json.loads(request.data.decode("utf-8")))
            return _Solved({"status": "ok", "solution": solution})

        return fake_urlopen

    # --- a login page returned by FlareSolverr is still a login redirect

    def test_flaresolverr_returning_the_login_page_is_treated_as_a_redirect(self):
        challenge = _Response(
            503, b"<html><title>Just a moment...</title></html>", headers={"Server": "cloudflare"}
        )
        provider = self._provider({("GET", self.PAGE_LINK): challenge})
        original = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = self._stub_flaresolverr(
            {
                "status": 200,
                "url": self.LOGIN_URL,
                "response": LOGIN_HTML.decode("utf-8"),
            }
        )
        try:
            response = provider._flaresolverr_request(
                "GET", self.PAGE_LINK, None, {"flaresolverr_url": self.FLARESOLVERR}
            )
        finally:
            self.mod.urllib.request.urlopen = original
        self.assertIn(response.status, self.mod.REDIRECT_STATUS_CODES)

    def test_a_search_solved_into_the_login_page_is_an_authentication_failure(self):
        challenge = _Response(
            503, b"<html><title>Just a moment...</title></html>", headers={"Server": "cloudflare"}
        )
        routes = {("GET", self.LOGIN_URL): _Response(200, LOGIN_HTML)}

        def login_post(scraper):
            for name, value in AUTHENTICATED_COOKIES.items():
                scraper.cookies.set(name, value)
            return _Response(200, b"<html><body>Ligado</body></html>")

        routes[("POST", self.LOGIN_URL)] = login_post
        for url in self.mod.build_search_urls(VIDEO_DUNE, "por"):
            routes[("GET", url)] = challenge
        provider = self._provider(routes)
        original = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = self._stub_flaresolverr(
            {"status": 200, "url": self.LOGIN_URL, "response": LOGIN_HTML.decode("utf-8")}
        )
        try:
            with self.assertRaises(self.mod.AuthenticationError):
                provider.search(
                    VIDEO_DUNE, [POR], dict(CREDENTIALS, flaresolverr_url=self.FLARESOLVERR)
                )
        finally:
            self.mod.urllib.request.urlopen = original

    # --- the stdlib fallback has to send the cookies FlareSolverr solved

    def test_solved_cookies_reach_the_stdlib_cookie_jar(self):
        provider = self.mod.LegendasDivxProvider()
        provider._scraper = None
        provider._scraper_initialized = True
        provider._store_flaresolverr_solution(
            {
                "userAgent": "FlareSolverr/UA",
                "cookies": [
                    {"name": name, "value": value}
                    for name, value in AUTHENTICATED_COOKIES.items()
                ],
            }
        )
        self.assertIsNotNone(provider._cookie_jar)
        jar = {cookie.name: cookie.value for cookie in provider._cookie_jar}
        self.assertEqual(jar.get("PHPSESSID"), "sess4242")
        self.assertEqual(jar.get("phpbb3_2z8zs_u"), "4242")

    # --- pagination must not request a page past the end of the results

    def test_page_count_does_not_add_an_empty_final_page(self):
        for count, expected in ((10, 1), (20, 2), (21, 3), (12, 2), (1, 1), (0, 1)):
            with self.subTest(count=count):
                body = f'<div class="pager_bar">({count} encontradas)</div>'.encode("utf-8")
                self.assertEqual(self.mod._page_count(body), expected)

    # --- a direct download keeps its real format

    def test_a_direct_non_srt_download_keeps_its_format(self):
        payload = {"filename": "legendasdivx.some-release.pt.zip"}
        ass = self.mod._download_payload(b"[Script Info]\nTitle: x\n", payload)
        self.assertEqual(ass["format"], "ass")
        self.assertEqual(ass["content_type"], "text/x-ssa")
        vtt = self.mod._download_payload(b"WEBVTT\n\n00:01.000 --> 00:02.000\nOla\n", payload)
        self.assertEqual(vtt["format"], "vtt")

    def test_a_content_disposition_filename_decides_the_format(self):
        payload = {"filename": "legendasdivx.some-release.pt.zip"}
        result = self.mod._download_payload(
            b"1\n00:00:01,000 --> 00:00:02,000\nOla\n",
            payload,
            headers={"Content-Disposition": 'attachment; filename="Show.S01E01.vtt"'},
        )
        self.assertEqual(result["format"], "vtt")

    def test_a_plain_srt_is_still_srt(self):
        result = self.mod._download_payload(
            b"1\n00:00:01,000 --> 00:00:02,000\nOla\n",
            {"filename": "legendasdivx.some-release.pt.zip"},
        )
        self.assertEqual(result["format"], "srt")

    # --- compact multi-episode ranges

    def test_a_compact_episode_range_covers_its_whole_span(self):
        for episode in (1, 2, 3):
            with self.subTest(episode=episode):
                self.assertTrue(
                    self.mod.member_matches_episode("Show.S01E01-03.srt", 1, episode)
                )
        self.assertFalse(self.mod.member_matches_episode("Show.S01E01-03.srt", 1, 4))

    def test_a_resolution_tag_is_not_read_as_a_second_episode(self):
        self.assertFalse(self.mod.member_matches_episode("Show.S01E01.2160p.srt", 1, 2))
        self.assertFalse(self.mod.member_matches_episode("Show.S01E01.1080p.x264.srt", 1, 80))

    # --- a row without a description is still a row

    def test_a_result_without_a_description_is_kept(self):
        chunk = (
            '<html><body><div class="sub_box">'
            '<div class="sub_header"><b>The Matrix</b> (1999) - '
            '<a href="profile.php?u=1">someone</a></div>'
            "<table><tr><th>Idioma:</th>"
            '<td><img src="images/flags/portugal.png" /></td>'
            "<th>Hits:</th><td>7</td><th>Frame Rate:</th><td>23.976</td></tr></table>"
            '<div class="sub_footer">'
            '<a class="sub_download" href="?name=Downloads&amp;d_op=getit&amp;lid=9001">D</a>'
            "</div></div></body></html>"
        ).encode("utf-8")
        rows = self.mod.parse_search_results(chunk)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lid"], "9001")
        self.assertEqual(rows[0]["release_info"], "The Matrix (1999)")

    def test_a_result_without_a_download_link_is_still_dropped(self):
        chunk = (
            '<html><body><div class="sub_box">'
            '<div class="sub_header"><b>The Matrix</b> (1999)</div>'
            "<table><tr><th>Idioma:</th>"
            '<td><img src="images/flags/portugal.png" /></td></tr></table>'
            '<td class="td_desc brd_up">The.Matrix.1999.1080p.BluRay.x264-SPARKS</td>'
            "</div></body></html>"
        ).encode("utf-8")
        self.assertEqual(self.mod.parse_search_results(chunk), [])

    # --- hcaptcha uses hcaptcha's site key and field

    def test_an_hcaptcha_login_uses_the_hcaptcha_field_and_site_key(self):
        page = (
            b"<html><body><form method='post'>"
            b"<input type='hidden' name='sid' value='abc' />"
            b"<div class=\"h-captcha\" data-sitekey=\"HCAP-SITE-KEY\"></div>"
            b"</form></body></html>"
        )

        def login_post(scraper):
            for name, value in AUTHENTICATED_COOKIES.items():
                scraper.cookies.set(name, value)
            return _Response(200, b"<html><body>Ligado</body></html>")

        provider = self._provider(
            {
                ("GET", self.LOGIN_URL): _Response(200, page),
                ("POST", self.LOGIN_URL): login_post,
            },
            cookies={},
            authenticated=False,
        )
        seen = {}

        def fake_solver(site_key, page_url, config):
            seen["site_key"] = site_key
            return "hcaptcha-token"

        provider._captcha_response = fake_solver
        provider._ensure_authenticated(dict(CREDENTIALS, captcha_solver_url="http://solver"))
        self.assertEqual(seen["site_key"], "HCAP-SITE-KEY")
        post = [call for call in provider._scraper.calls if call[0] == "POST"][0]
        self.assertEqual(post[2]["h-captcha-response"], "hcaptcha-token")
        self.assertNotIn("g-recaptcha-response", post[2])

    # --- an authenticated session needs a phpBB user cookie

    def test_a_session_without_a_phpbb_user_cookie_is_not_authenticated(self):
        def login_post(scraper):
            scraper.cookies.set("PHPSESSID", "sess0")
            return _Response(200, b"<html><body>ok</body></html>")

        provider = self._provider(
            {
                ("GET", self.LOGIN_URL): _Response(200, LOGIN_HTML),
                ("POST", self.LOGIN_URL): login_post,
            },
            cookies={},
            authenticated=False,
        )
        with self.assertRaises(self.mod.AuthenticationError):
            provider._ensure_authenticated(dict(CREDENTIALS))

    def test_a_different_board_prefix_still_authenticates(self):
        def login_post(scraper):
            scraper.cookies.set("phpbb3_9xyz1_u", "77")
            scraper.cookies.set("phpbb3_9xyz1_sid", "deadbeef")
            return _Response(200, b"<html><body>ok</body></html>")

        provider = self._provider(
            {
                ("GET", self.LOGIN_URL): _Response(200, LOGIN_HTML),
                ("POST", self.LOGIN_URL): login_post,
            },
            cookies={},
            authenticated=False,
        )
        provider._ensure_authenticated(dict(CREDENTIALS))
        self.assertTrue(provider._authenticated)

    # --- "bloqueado" in a description is not an IP block

    def test_the_word_blocked_in_a_description_does_not_abort_the_search(self):
        body = SEARCH_DUNE_HTML.replace(
            b"Sincronizadas para a release:",
            b"O uploader foi bloqueado no forum. Sincronizadas para a release:",
        )
        routes = {}
        for url in self.mod.build_search_urls(VIDEO_DUNE, "por"):
            routes[("GET", url)] = _Response(200, body)
            for page in range(2, self.mod.MAX_PAGES + 1):
                routes[("GET", f"{url}&page={page}")] = _Response(200, b"<html></html>")
        provider = self._provider(routes)
        results = provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))
        self.assertEqual(len(results), 3)

    def test_a_real_ip_block_notice_still_aborts_the_search(self):
        body = b"<html><body>O seu IP foi bloqueado neste servidor.</body></html>"
        routes = {}
        for url in self.mod.build_search_urls(VIDEO_DUNE, "por"):
            routes[("GET", url)] = _Response(200, body)
        provider = self._provider(routes)
        with self.assertRaises(self.mod.IPAddressBlocked):
            provider.search(VIDEO_DUNE, [POR], dict(CREDENTIALS))




class LegendasDivxOracleSweepRegressionTests(unittest.TestCase):
    """Divergences from the built-in found by sweeping realistic member names.

    These were not reported by review; they came out of running both
    implementations over the same names and comparing. Each one is a member the
    plugin resolved differently from the implementation that was verified against
    the live site.
    """

    def setUp(self):
        self.mod = _load_provider_module()

    def test_only_a_leading_number_reads_as_season_plus_episode(self):
        # This site's packs open with the number: 105 is season one episode five.
        self.assertTrue(self.mod.member_matches_episode("105 - Pilot.srt", 1, 5))
        # An anime member's number is absolute, and reading it compactly as well
        # would let it answer for season one episode five too.
        self.assertFalse(
            self.mod.member_matches_episode("[Group] Anime - 105 [720p].srt", 1, 5)
        )
        self.assertTrue(
            self.mod.member_matches_episode("[Group] Anime - 105 [720p].srt", 1, 105, 105)
        )

    def test_a_compact_number_does_not_also_stand_for_itself_as_an_episode(self):
        # "105 - Pilot.srt" is episode five, so it must not answer for episode 105.
        self.assertFalse(self.mod.member_matches_episode("105 - Pilot.srt", 1, 105))
        self.assertTrue(self.mod.member_matches_episode("105 - Pilot.srt", 1, 105, 105))

    def test_a_numbered_part_or_disc_is_not_an_episode(self):
        for name in ("Show.Part.2.srt", "Show.Parte.2.srt", "Show.CD2.srt", "Show.Vol.2.srt"):
            with self.subTest(name=name):
                # States nothing about numbering, so it contradicts nothing.
                self.assertTrue(self.mod.member_matches_episode(name, 1, 1))
                self.assertTrue(self.mod.member_matches_episode(name, 2, 7))

    def test_a_folder_that_contradicts_the_file_name_answers_for_neither(self):
        name = "Pack S02/Show.S01E07.srt"
        self.assertFalse(self.mod.member_matches_episode(name, 1, 7))
        self.assertFalse(self.mod.member_matches_episode(name, 2, 7))

    def test_a_folder_that_agrees_with_the_file_name_still_matches(self):
        self.assertTrue(self.mod.member_matches_episode("Pack S01/Show.S01E07.srt", 1, 7))
        self.assertTrue(
            self.mod.member_matches_episode("Breaking Bad S01/Breaking.Bad.S01E03.srt", 1, 3)
        )



if __name__ == "__main__":
    unittest.main()


class ReviewFindingsOnTheMergedHead(unittest.TestCase):
    """The four findings recorded when the port was promoted with review open.

    Each test states the exact scenario from the finding, so a regression
    reproduces the original report rather than a synthetic approximation.
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_provider_module()

    # Finding 1: a compact season+episode reading must not contradict the
    # season the folder already claims.
    def test_compact_reading_yields_to_the_folder_season(self):
        self.assertFalse(self.mod.member_matches_episode("Season 2/105 - Pilot.srt", 1, 5))

    def test_compact_reading_consistent_with_the_folder_season_matches(self):
        self.assertTrue(self.mod.member_matches_episode("Season 1/105 - Pilot.srt", 1, 5))

    def test_conflicting_compact_number_still_counts_under_its_own_folder(self):
        # The number itself is not a season claim; under its stated folder it
        # can still be episode 105 (or an absolute number), just never S01E05.
        self.assertTrue(self.mod.member_matches_episode("Season 2/105 - Pilot.srt", 2, 105))

    # Finding 2: DTS-HD MA channel counts are release noise, not episodes.
    def test_dts_hd_ma_channels_are_not_episode_numbers(self):
        self.assertTrue(self.mod.member_matches_episode("Show.1080p.BluRay.DTS-HD.MA.5.1.srt", 1, 2))

    def test_plain_dts_hd_channels_stay_stripped(self):
        self.assertTrue(self.mod.member_matches_episode("Show.1080p.BluRay.DTS-HD.5.1.srt", 1, 2))

    # Finding 3: the live session jar outranks the FlareSolverr cookie cache,
    # or a solved anonymous login page keeps vetoing a later successful login.
    def test_live_jar_cookies_beat_the_flaresolverr_cache(self):
        provider = self.mod.LegendasDivxProvider()
        provider._flaresolverr_cookies = {"phpbb3_2z8zs_u": "1", "PHPSESSID": "stale"}
        provider._cookie_jar = CookieJar()
        for name, value in AUTHENTICATED_COOKIES.items():
            provider._cookie_jar.set_cookie(self.mod._jar_cookie(name, value))
        cookies = provider.session_cookies()
        self.assertEqual(cookies["phpbb3_2z8zs_u"], "4242")
        self.assertEqual(cookies["PHPSESSID"], "sess4242")

    def test_flaresolverr_cookies_still_serve_when_the_jars_lack_them(self):
        provider = self.mod.LegendasDivxProvider()
        provider._flaresolverr_cookies = {"cf_clearance": "solved"}
        self.assertEqual(provider.session_cookies()["cf_clearance"], "solved")

    # Finding 4: every explicit episode spelling the matcher accepts must also
    # earn the explicit-member ranking bonus.
    def test_hyphen_separated_explicit_member_outranks_release_overlap(self):
        member, decision = self.mod.pick_archive_member(
            ["Show.Release.srt", "Show.S01-E02.srt"],
            {"season": 1, "episode": 2, "release_info": "Show.Release"},
        )
        self.assertEqual(decision, "pin")
        self.assertEqual(member, "Show.S01-E02.srt")

    def test_a_generic_cloudflare_error_page_is_not_a_challenge(self):
        # Cloudflare's ordinary error template carries cf-error-details too; a
        # plain 403 must keep its meaning instead of triggering a solve.
        self.assertFalse(self.mod._is_cloudflare_challenge(
            403,
            {"Server": "cloudflare"},
            b"<html>cf-error-details: access denied</html>",
        ))

    def test_the_challenge_page_body_is_still_recognized(self):
        self.assertTrue(self.mod._is_cloudflare_challenge(
            403, {"Server": "cloudflare"}, b"<html><title>Just a moment...</title></html>"
        ))

    def test_cross_form_explicit_member_outranks_release_overlap(self):
        member, decision = self.mod.pick_archive_member(
            ["Show.Release.srt", "Show.1x02.srt"],
            {"season": 1, "episode": 2, "release_info": "Show.Release"},
        )
        self.assertEqual(decision, "pin")
        self.assertEqual(member, "Show.1x02.srt")
