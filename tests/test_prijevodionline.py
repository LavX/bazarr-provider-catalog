import ast
import base64
import hashlib
import importlib.util
import io
import json
import logging
import re
import http.server
import socket
import threading
import unittest
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "providers" / "prijevodionline"
FIXTURE_DIR = ROOT / "tests" / "fixtures"
API = "/api/v1"


def _load_provider_module():
    spec = importlib.util.spec_from_file_location("prijevodionline_provider", PROVIDER_DIR / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(name):
    return json.loads((FIXTURE_DIR / f"prijevodionline_{name}.json").read_text("utf-8"))


def _zip_body(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


DEFAULT_HEADERS = dict(_fixture("api_headers")["headers"])
ANON_ME = _fixture("api_auth_me_anonymous")
SRT = b"1\n00:00:01,000 --> 00:00:02,000\nZdravo\n"
GOT_ZIP = _zip_body({"Game of Thrones - 01x02 - The Kingsroad 720p.BluRay HR.srt": SRT})
HRV = {"alpha3": "hrv", "alpha2": "hr", "script": None, "hi": False, "forced": False}
SRP = {"alpha3": "srp", "alpha2": "sr", "script": None, "hi": False, "forced": False}
GOT_S01E02 = {
    "kind": "episode",
    "series": "Game of Thrones",
    "season": 1,
    "episode": 2,
    "year": 2011,
    "title": "The Kingsroad",
    "resolution": "720p",
    "source": "Blu-ray",
}
INCEPTION = _fixture("video_inception_2010")
MAPPED_HOST_NAMES = {
    "DownloadLimitExceeded",
    "TooManyRequests",
    "RateLimited",
    "ServiceUnavailable",
    "APIThrottled",
    "AuthenticationError",
    "AuthenticationRequired",
    "ConfigurationError",
}

USER = "USERSENTINEL"
PASSWORD = "PASSSENTINEL"
COOKIE_VALUE = "COOKIESENTINEL"
LOGIN_COOKIE = "LOGINCOOKIESENTINEL"
COOKIE_HEADER = f"Cookie: SMFCookie123={COOKIE_VALUE}; PHPSESSID=PHPSENTINEL; cf_clearance=CFSENTINEL; _ga=GA1.2.3"


def ok(data, headers=None):
    return 200, headers or {}, data


def api_error(status, code, message="", headers=None, **extra):
    error = {"code": code, "message": message}
    error.update(extra)
    return status, headers or {}, {"error": error}


def zip_answer(body=GOT_ZIP):
    return 200, {"content-type": "application/zip"}, body


# Synthetic shapes below come from the site's client bundle, not a capture.


def member_me(balance=3, series_free=False, extra=("movies.translations.download", "account.tokens.read")):
    """Member /auth/me. shape from the site's client bundle, not a capture."""
    data = json.loads(json.dumps(ANON_ME))
    user = data["user"]
    user.update({"id": 4242, "name": "member", "displayName": "Member", "isAnonymous": False, "tokenBalance": balance})
    permissions = [item for item in user["permissions"] if item != "auth.login"]
    if not series_free:
        permissions = [item for item in permissions if item != "series.translations.downloadFree"]
    user["permissions"] = permissions + list(extra)
    return data


def login_ok(cookie_value=LOGIN_COOKIE):
    """Login success. shape from the site's client bundle, not a capture."""
    return (
        200,
        [("Set-Cookie", f"PHPSESSID={cookie_value}; Path=/; Secure; HttpOnly")],
        {"auth": {"id": 4242, "name": "member", "displayName": "Member"}},
    )


def quote_download(translation_id, kind="series"):
    """Quote answering download. shape from the site's client bundle, not a capture."""
    return ok({"intent": {"action": "download", "translationId": translation_id, "translationType": kind}})


def quote_purchase(translation_id, cost=1, balance=3, can_afford=True, token="QUOTETOKEN", kind="series", headers=None):
    """Quote answering purchase. shape from the site's client bundle, not a capture."""
    return ok(
        {
            "intent": {
                "action": "purchase",
                "translationId": translation_id,
                "translationType": kind,
                "tokenCost": cost,
                "balance": balance,
                "canAfford": can_afford,
                "token": token,
            }
        },
        headers,
    )


def confirm_ok(translation_id, cost=1):
    """Confirm success. shape from the site's client bundle, not a capture."""
    return ok(
        {
            "purchase": {
                "id": 777,
                "uuid": "00000000-0000-0000-0000-000000000777",
                "memberId": 4242,
                "translationId": translation_id,
                "translationType": "series",
                "tokenCost": cost,
                "packId": None,
                "revokedAt": None,
                "revokedBy": None,
                "createdAt": "2026-09-26T10:00:00.000Z",
            }
        }
    )


def confirm_insufficient(status=400):
    """Confirm refused for balance. shape from the site's client bundle, not a capture."""
    return api_error(status, "Tokens/InsufficientBalance", "Insufficient token balance")


def season_items(**changes):
    data = season_capture()
    for item in data["translations"]["items"]:
        item.update(changes)
    return data


def with_variant(data, container, index, **changes):
    """A price or ownership variant derived from a captured item."""
    data = json.loads(json.dumps(data))
    data[container]["items"][index].update(changes)
    return data


def movie_items(extra_free=True):
    data = _fixture("api_translations_movies")
    if extra_free:
        free = dict(data["movieTranslations"]["items"][0])
        free.update({"id": 580, "fileId": 904, "price": None, "title": "Inception.2010.1080p.BluRay.x264-FREE"})
        data["movieTranslations"]["items"].append(free)
        data["movieTranslations"]["total"] += 1
    return data


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


class FakeSite:
    """Routes (method, path) to scripted answers and records every request."""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def on(self, method, path, *answers):
        self.routes[(method, path)] = list(answers)

    def send(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        call = {
            "method": method,
            "url": url,
            "path": parts.path,
            "query": dict(urllib.parse.parse_qsl(parts.query)),
            "headers": dict(headers),
            "body": body,
            "timeout": timeout,
        }
        self.calls.append(call)
        answers = self.routes.get((method, parts.path))
        if not answers:
            raise AssertionError(f"unexpected request {method} {parts.path}")
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if callable(answer):
            answer = answer(call)
        if isinstance(answer, BaseException):
            raise answer
        status, extra, data = answer
        pairs = list(DEFAULT_HEADERS.items())
        extra_pairs = list(extra.items()) if isinstance(extra, dict) else list(extra)
        extra_names = {name.lower() for name, _ in extra_pairs}
        pairs = [(name, value) for name, value in pairs if name.lower() not in extra_names] + extra_pairs
        raw = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        return status, pairs, raw

    def count(self, method, path):
        return sum(1 for call in self.calls if call["method"] == method and call["path"] == path)

    def paths(self):
        return [(call["method"], call["path"]) for call in self.calls]

    def reset(self):
        self.calls.clear()


def season_capture():
    """The season capture, trimmed to 3 of its 153 items, with total to match."""
    data = _fixture("api_translations_series_by_season")
    data["translations"]["total"] = len(data["translations"]["items"])
    return data


def anonymous_site(translations=None, movies=None, series_results=None, movie_results=None):
    site = FakeSite()
    site.on("GET", API + "/auth/me", ok(ANON_ME))

    def search(call):
        if call["query"].get("type") == "series":
            return ok(series_results or _fixture("api_search_results_series"))
        return ok(movie_results or _fixture("api_search_results_movies"))

    site.on("GET", API + "/search/results", search)
    site.on("GET", API + "/series/935/seasons", ok(_fixture("api_series_seasons")))
    site.on("GET", API + "/translations/series", ok(translations or season_capture()))
    site.on("GET", API + "/movies/by-slug/inception-2010", ok(_fixture("api_movie_by_slug")))
    site.on("GET", API + "/translations/movies", ok(movies or movie_items()))
    site.on("GET", API + "/translations/series/120299/download", zip_answer())
    site.on("GET", API + "/translations/series/165016/download", zip_answer())
    return site


def cookie_member_site(member=None, translations=None, cookie_value=COOKIE_VALUE):
    """A site where the pasted session cookie is signed in."""
    site = anonymous_site(translations=translations)
    member = member or member_me()
    site.on(
        "GET",
        API + "/auth/me",
        lambda call: ok(member) if cookie_value in call["headers"].get("Cookie", "") else ok(ANON_ME),
    )
    return site


class PasswordSite:
    """Login issues a fresh session cookie each time; sessions can be expired."""

    def __init__(self, site, member=None, login=None):
        self.site = site
        self.member = member or member_me()
        self.valid = set()
        self.logins = 0
        self.login_answer = login
        site.on("POST", API + "/auth/login", self._login)
        site.on("GET", API + "/auth/me", self._me)

    def _login(self, call):
        self.logins += 1
        if self.login_answer is not None:
            return self.login_answer(call) if callable(self.login_answer) else self.login_answer
        value = f"{LOGIN_COOKIE}{self.logins}"
        self.valid.add(value)
        return login_ok(value)

    def _me(self, call):
        cookie = call["headers"].get("Cookie", "")
        if any(f"PHPSESSID={value}" in cookie for value in self.valid):
            return ok(self.member)
        return ok(ANON_ME)

    def expire(self):
        self.valid.clear()


class ProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = _load_provider_module()
        # The worker relays plugin warnings through Python's last-resort
        # handler; keep that out of the test output.
        quiet = logging.NullHandler()
        plugin_logger = logging.getLogger("prijevodionline")
        plugin_logger.addHandler(quiet)
        self.addCleanup(plugin_logger.removeHandler, quiet)

    def provider(self, site, clock=None):
        provider = self.mod.PrijevodiOnlineProvider()
        clock = clock or FakeClock()
        provider._monotonic = clock.monotonic
        provider._sleep = clock.sleep
        provider._send = site.send
        provider._flaresolverr_transport = lambda payload: (_ for _ in ()).throw(
            AssertionError("FlareSolverr must not be called")
        )
        self.clock = clock
        return provider

    def payload(self, translation_id=120299, kind="series", access="granted", list_price=1, **changes):
        payload = {
            "v": 1,
            "kind": kind,
            "translation_id": translation_id,
            "season": 1 if kind == "series" else None,
            "episode": 2 if kind == "series" else None,
            "language": "hrv",
            "script": None,
            "list_price": list_price,
            "access": access,
            "mode": "anonymous",
            "file_name": "Game of Thrones - 01x02 - The Kingsroad 720p.BluRay HR.zip",
            "releases": ["720p", "bluray"],
        }
        payload.update(changes)
        return payload


# A. Pure functions


class PureFunctionTests(ProviderTestCase):
    def test_match_series_exact_title_with_year_tiebreak(self):
        items = _fixture("api_search_results_series")["results"]["items"]
        self.assertEqual(self.mod.match_series(items, "Game of Thrones", None)["id"], 935)
        self.assertEqual(self.mod.match_series(items, "game of thrones", 2011)["id"], 935)
        self.assertIsNone(self.mod.match_series(items, "Game of Thrones: Conquest", None))
        twins = [
            {"type": "series", "id": 1, "title": "Shameless", "originalTitle": "Shameless", "premiereYear": 2004},
            {"type": "series", "id": 2, "title": "Shameless", "originalTitle": "Shameless", "premiereYear": 2011},
        ]
        self.assertEqual(self.mod.match_series(twins, "Shameless", 2011)["id"], 2)
        self.assertEqual(self.mod.match_series(twins, "Shameless (2011)", None)["id"], 2)
        self.assertIsNone(self.mod.match_series(twins, "Shameless", None))
        self.assertIsNone(self.mod.match_series(twins, "Shameless", 1999))
        dropped_apostrophe = [{"type": "series", "id": 77, "title": "Da Vincis Demons", "originalTitle": None}]
        self.assertEqual(self.mod.match_series(dropped_apostrophe, "Da Vinci's Demons", None)["id"], 77)
        self.assertEqual(self.mod.normalize_title("Law & Order: Special Victims Unit"), "law and order special victims unit")
        self.assertEqual(self.mod.normalize_title("Élite"), "elite")
        self.assertEqual(self.mod.strip_year_suffix("Doctor Who (2005)"), ("Doctor Who", 2005))

    def test_localized_or_unknown_title_returns_no_series(self):
        empty = _fixture("api_search_results_empty")
        self.assertIsNone(self.mod.match_series(empty["results"]["items"], "igra prijestolja", None))
        site = anonymous_site(series_results=empty)
        provider = self.provider(site)
        video = dict(GOT_S01E02, series="Igra prijestolja")
        self.assertEqual(provider.search(video, [HRV], {}), [])
        self.assertEqual(site.count("GET", API + "/series/935/seasons"), 0)
        # The miss is cached, so a second search asks nothing but the cache.
        site.reset()
        self.assertEqual(provider.search(video, [HRV], {}), [])
        self.assertEqual(site.calls, [])

    def test_season_map_and_specials_bucket(self):
        seasons = _fixture("api_series_seasons")["seasons"]["items"]
        self.assertEqual(self.mod.season_id_for(seasons, 1), 3391)
        self.assertEqual(self.mod.season_id_for(seasons, "1"), 3391)
        self.assertEqual(self.mod.season_id_for(seasons, 0), 3399)
        self.assertIsNone(self.mod.season_id_for(seasons, 10))
        self.assertIsNone(self.mod.season_id_for(seasons, None))

    def test_language_mapping_by_code(self):
        mod = self.mod
        self.assertEqual(mod.requested_languages([HRV, SRP]), {"hrv", "srp"})
        self.assertEqual(mod.requested_languages([{"alpha3": "srp", "alpha2": "sr", "script": "Cyrl"}]), {"srp-Cyrl"})
        self.assertEqual(mod.requested_languages([{"alpha2": "bs"}, {"alpha3": "mkd"}]), {"bos", "mkd"})
        self.assertEqual(mod.requested_languages([{"alpha3": "cnr", "alpha2": "me"}]), {"cnr"})
        self.assertEqual(mod.requested_languages([{"alpha3": "hbs", "alpha2": "sh"}]), {"hbs"})
        self.assertEqual(mod.requested_languages([{"alpha3": "eng"}, {"alpha3": "slv"}]), set())
        self.assertEqual(mod.language_for_row("hr", {"hrv"}), "hrv")
        self.assertEqual(mod.language_for_row("sr", {"srp"}), "srp")
        self.assertIsNone(mod.language_for_row("sr-cyr", {"srp"}))
        self.assertEqual(mod.language_for_row("sr-cyr", {"srp-Cyrl"}), "srp-Cyrl")
        self.assertIsNone(mod.language_for_row("sr", {"srp-Cyrl"}))
        self.assertEqual(mod.language_for_row("bs", {"bos"}), "bos")
        self.assertEqual(mod.language_for_row("cnr", {"cnr"}), "cnr")
        self.assertEqual(mod.language_for_row("mk", {"mkd"}), "mkd")
        # hbs takes hr, sr and cnr rows that were not asked for under their own code.
        self.assertEqual(mod.language_for_row("hr", {"hbs"}), "hbs")
        self.assertEqual(mod.language_for_row("cnr", {"hbs"}), "hbs")
        self.assertEqual(mod.language_for_row("hr", {"hbs", "hrv"}), "hrv")
        self.assertIsNone(mod.language_for_row("bs", {"hbs"}))
        self.assertIsNone(mod.language_for_row("sr-cyr", {"hbs"}))
        everything = set(mod.LANGUAGES)
        for code in ("mix", "??", "en", "sl"):
            self.assertIsNone(mod.language_for_row(code, everything))
        for item in _fixture("api_languages")["languages"]["items"]:
            handled = mod.language_for_row(item["code"], everything) is not None
            self.assertTrue(handled or item["code"] in ("mix", "??"), item["code"])
        self.assertEqual(
            mod.language_payload("srp-Cyrl"),
            {"alpha3": "srp", "alpha2": "sr", "hi": False, "forced": False, "script": "Cyrl"},
        )
        self.assertEqual(mod.language_payload("cnr")["alpha2"], "me")
        self.assertEqual(mod.language_payload("hbs")["alpha2"], "sh")

    def test_classify_access_matrix(self):
        mod = self.mod
        anonymous = {"member": False, "permissions": frozenset(ANON_ME["user"]["permissions"])}
        member_free = {"member": True, "permissions": frozenset(member_me(series_free=True)["user"]["permissions"])}
        member = {"member": True, "permissions": frozenset(member_me()["user"]["permissions"])}
        series = season_capture()["translations"]["items"][0]
        movie = _fixture("api_translations_movies")["movieTranslations"]["items"][0]

        def variant(item, **changes):
            copy = dict(item)
            copy.update(changes)
            return copy

        classify = mod.classify_access
        self.assertEqual(classify(series, "series", anonymous, ()), "granted")
        self.assertEqual(classify(variant(series, price=None), "series", anonymous, ()), "free")
        self.assertEqual(classify(variant(series, price=0), "series", anonymous, ()), "free")
        self.assertEqual(classify(variant(series, price=5), "series", anonymous, ()), "granted")
        self.assertEqual(classify(series, "series", anonymous, {"series"}), "account_required")
        self.assertEqual(classify(variant(series, isPurchased=True), "series", anonymous, ()), "granted")
        self.assertEqual(classify(movie, "movies", anonymous, ()), "account_required")
        self.assertEqual(classify(variant(movie, price=None), "movies", anonymous, ()), "account_required")
        self.assertEqual(classify(series, "series", member_free, ()), "granted")
        self.assertEqual(classify(series, "series", member_free, {"series"}), "priced")
        self.assertEqual(classify(series, "series", member, ()), "priced")
        self.assertEqual(classify(variant(series, price=None), "series", member, ()), "free")
        self.assertEqual(classify(variant(series, isPurchased=True), "series", member, ()), "owned")
        self.assertEqual(classify(variant(series, isPurchased=True, isRevoked=True), "series", member, ()), "priced")
        self.assertEqual(classify(movie, "movies", member, ()), "priced")
        for broken in ({"fileId": None}, {"isPublished": False}, {"status": "pending"}, {"price": "abc"}, {"price": -1}):
            self.assertEqual(classify(variant(series, **broken), "series", member, ()), "skip", broken)
        # A row without a price key is unreadable, never free.
        priceless = {key: value for key, value in series.items() if key != "price"}
        for snapshot in (anonymous, member, member_free):
            self.assertEqual(classify(priceless, "series", snapshot, ()), "skip")
        self.assertEqual(self.mod._item_price(priceless), (False, None))
        for item in _fixture("api_translations_series_price_tiers")["translations"]["items"]:
            self.assertEqual(classify(item, "series", anonymous, ()), "granted")
            self.assertEqual(classify(item, "series", member, ()), "priced")

    def test_parse_cap_is_defensive(self):
        for value, expected in (("1", 1), (5, 5), ("10", 10), (" 2 ", 2), ("3", 3)):
            self.assertEqual(self.mod.parse_cap(value), expected)
        for value in ("0", "-1", "abc", None, "99", True, False, 4, "2.0", 2.0, ""):
            self.assertIsNone(self.mod.parse_cap(value), value)

    def test_parse_session_cookie(self):
        parse = self.mod.parse_session_cookie
        self.assertEqual(
            parse(
                "Cookie: SMFCookie123=abc%3D; PHPSESSID=xyz; cf_clearance=CF; __cf_bm=BM; _cfuvid=U; "
                "_ga=GA1.1; _ga_ABC=1; _gid=G; _gat=1; __utma=1; _fbp=f; po_oauth_state=S; custom=1"
            ),
            [("SMFCookie123", "abc%3D"), ("PHPSESSID", "xyz"), ("custom", "1")],
        )
        self.assertEqual(parse('  quoted="a1b2"  '), [("quoted", '"a1b2"')])
        self.assertEqual(parse("bad name=1; ok=2; spaced=a b; comma=a,b"), [("ok", "2")])
        for rejected in ("a=b\r\nc=d", "a=b\nc=d", "a=b\x00", "x=" + "y" * 5000, "", "   ", "novalue", "cf_clearance=x"):
            self.assertIsNone(parse(rejected), repr(rejected)[:40])
        self.assertIsNone(parse(None))

    def test_classify_login_failure(self):
        classify = self.mod.classify_login_failure

        def envelope(code, message="", **extra):
            error = {"code": code, "message": message}
            error.update(extra)
            return json.dumps({"error": error}).encode("utf-8")

        captcha_validation = envelope(
            "VALIDATION_ERROR", "Validation failed: body", validation=[{"path": ["captchaToken"], "message": "Required"}]
        )
        self.assertEqual(classify(400, captcha_validation), "captcha_required")
        self.assertEqual(classify(403, envelope("Auth/Forbidden", "Turnstile verification failed")), "captcha_required")
        self.assertEqual(classify(400, envelope("Auth/CaptchaRequired", "x")), "captcha_required")
        self.assertEqual(classify(401, envelope("Auth/InvalidCredentials", "Wrong password")), "invalid_credentials")
        self.assertEqual(classify(400, envelope("Auth/InvalidCredentials", "Wrong password")), "invalid_credentials")
        self.assertEqual(classify(403, b""), "invalid_credentials")
        self.assertEqual(classify(400, envelope("Bad/Request", "x"), True), "captcha_required")
        self.assertEqual(classify(422, envelope("Bad/Request", "x"), False), "unknown")
        self.assertEqual(classify(400, envelope("Bad/Request", "x")), "unknown")
        self.assertEqual(classify(429, envelope("RATE_LIMITED")), "rate_limited")
        self.assertEqual(classify(502, b""), "unavailable")
        self.assertEqual(classify(301, b""), "unavailable")
        self.assertEqual(classify(200, b"<!DOCTYPE html><html></html>"), "unavailable")
        self.assertEqual(classify(200, json.dumps({"auth": {"id": 0}}).encode()), "no_session")
        self.assertEqual(classify(418, b"{}"), "unknown")

    def test_classify_confirm_outcome(self):
        classify = self.mod.classify_confirm_outcome
        body = lambda data: json.dumps(data).encode("utf-8")  # noqa: E731
        bought = confirm_ok(120299, cost=2)[2]
        self.assertEqual(classify(200, body(bought)), ("bought", {"id": 777, "tokenCost": 2}))
        for unreadable in (body({"purchase": {"id": "x", "tokenCost": 1}}), b"not json", body({"ok": True}), b""):
            self.assertEqual(classify(200, unreadable)[0], "uncertain")
        insufficient = body(confirm_insufficient()[2])
        self.assertEqual(classify(400, insufficient), ("insufficient", "Tokens/InsufficientBalance"))
        self.assertEqual(classify(402, insufficient), ("insufficient", "Tokens/InsufficientBalance"))
        self.assertEqual(classify(403, body({"error": {"code": "Auth/Forbidden", "message": "x"}}))[0], "auth")
        self.assertEqual(classify(401, body({"error": {"code": "Auth/Unauthorized", "message": ""}}))[0], "auth")
        self.assertEqual(classify(409, body({"error": {"code": "Purchase/Conflict", "message": ""}})), ("refused", "Purchase/Conflict"))
        self.assertEqual(classify(408, body({"error": {"code": "Timeout", "message": ""}}))[0], "uncertain")
        self.assertEqual(classify(429, body({"error": {"code": "RATE_LIMITED", "message": ""}}))[0], "throttled")
        self.assertEqual(classify(500, body({"error": {"code": "INTERNAL", "message": ""}}))[0], "uncertain")
        self.assertEqual(classify(502, b"")[0], "uncertain")
        self.assertEqual(classify(404, b"<!DOCTYPE html><html></html>")[0], "uncertain")
        self.assertEqual(classify(None, None, socket.timeout("timed out"))[0], "uncertain")
        self.assertEqual(classify(None, None, urllib.error.URLError(ConnectionResetError()))[0], "uncertain")
        self.assertEqual(classify(None, None, urllib.error.URLError(socket.gaierror()))[0], "not_sent")
        self.assertEqual(classify(None, None, urllib.error.URLError(ConnectionRefusedError()))[0], "not_sent")
        challenge = b"<html><title>Just a moment...</title></html>"
        self.assertEqual(classify(403, challenge, None, {"cf-mitigated": "challenge"})[0], "challenge")

    def test_rate_headers_parse(self):
        parse = self.mod.parse_rate_headers
        self.assertEqual(parse(DEFAULT_HEADERS), {"limit": 200, "remaining": 199, "reset": 54.0, "retry_after": None})
        self.assertEqual(parse({"Retry-After": "7"})["retry_after"], 7.0)
        self.assertEqual(parse([("X-RateLimit-Remaining", "5"), ("X-RateLimit-Reset", "12")])["remaining"], 5)
        self.assertIsNone(parse({"x-ratelimit-remaining": "abc"})["remaining"])
        self.assertIsNone(parse({"x-ratelimit-reset": "-4"})["reset"])
        self.assertEqual(parse(None)["limit"], None)


# B. Anonymous search


class AnonymousSearchTests(ProviderTestCase):
    def test_anonymous_search_returns_series_candidates(self):
        site = anonymous_site()
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV, SRP], {})

        by_id = {item["id"]: item for item in results}
        self.assertEqual(
            set(by_id),
            {"prijevodionline-s120299-hrv", "prijevodionline-s165016-srp", "prijevodionline-s37045-srp"},
        )
        hr = by_id["prijevodionline-s120299-hrv"]
        self.assertEqual(hr["language"], {"alpha3": "hrv", "alpha2": "hr", "hi": False, "forced": False})
        for key in ("series", "season", "episode", "year", "resolution", "source"):
            self.assertIn(key, hr["matches"])
        self.assertEqual(hr["score"], 95)
        self.assertNotIn("token", hr["release_info"])
        self.assertEqual(hr["provider_payload"]["access"], "granted")
        self.assertEqual(hr["provider_payload"]["list_price"], 1)
        self.assertEqual(hr["provider_payload"]["releases"], ["720p", "bluray"])
        self.assertEqual(hr["filename"], "Game of Thrones - 01x02 - The Kingsroad 720p.BluRay HR.zip")
        self.assertEqual(hr["display"], {"source": "prijevodi-online-api", "uploader": "translator_a"})
        self.assertEqual(
            site.paths(),
            [
                ("GET", API + "/auth/me"),
                ("GET", API + "/search/results"),
                ("GET", API + "/series/935/seasons"),
                ("GET", API + "/translations/series"),
            ],
        )
        self.assertEqual(site.calls[1]["query"], {"q": "Game of Thrones", "type": "series", "perPage": "20"})
        self.assertEqual(site.calls[3]["query"], {"seasonId": "3391", "perPage": "1000"})
        self.assertTrue(all("Cookie" not in call["headers"] for call in site.calls))
        self.assertTrue(all(call["url"].startswith("https://www.prijevodi-online.org/api/v1/") for call in site.calls))

    def test_hbs_request_and_cyrillic_rows(self):
        cyrillic = with_variant(season_capture(), "translations", 1, languageCode="sr-cyr")
        site = anonymous_site(translations=cyrillic)
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [{"alpha3": "hbs", "alpha2": "sh"}], {})
        self.assertEqual(sorted(item["id"] for item in results), ["prijevodionline-s120299-hbs", "prijevodionline-s37045-hbs"])
        results = provider.search(GOT_S01E02, [{"alpha3": "srp", "alpha2": "sr", "script": "Cyrl"}], {})
        self.assertEqual([item["id"] for item in results], ["prijevodionline-s165016-srp-cyrl"])
        self.assertEqual(results[0]["language"]["script"], "Cyrl")
        self.assertEqual(results[0]["provider_payload"]["script"], "Cyrl")

    def test_anonymous_search_hides_priced_movies_and_lists_free_movie_needing_account(self):
        site = anonymous_site()
        provider = self.provider(site)
        with self.assertLogs("prijevodionline", "INFO") as logs:
            results = provider.search(INCEPTION, [HRV, SRP], {})
        self.assertEqual([item["id"] for item in results], ["prijevodionline-m580-hrv"])
        free = results[0]
        self.assertEqual(free["provider_payload"]["access"], "account_required")
        self.assertEqual(free["provider_payload"]["kind"], "movie")
        self.assertTrue(free["release_info"].endswith("needs an account"), free["release_info"])
        self.assertEqual(set(free["matches"]) & {"title", "year", "imdb_id"}, {"title", "year", "imdb_id"})
        self.assertIn("needs_account=2", "\n".join(logs.output))

    def test_search_caches_lookups_and_season_translations(self):
        site = anonymous_site()
        provider = self.provider(site)
        provider.search(GOT_S01E02, [HRV], {})
        self.assertEqual(len(site.calls), 4)
        site.reset()
        provider.search(dict(GOT_S01E02, episode=1, title="Winter Is Coming"), [HRV], {})
        self.assertEqual(site.calls, [])
        self.clock.advance(11 * 60)
        provider.search(GOT_S01E02, [HRV], {})
        self.assertEqual(site.paths(), [("GET", API + "/translations/series")])

    def test_season_translations_page_until_total_and_stop_on_repeats(self):
        full = _fixture("api_translations_series_by_season")
        items = full["translations"]["items"]

        def page(call):
            number = int(call["query"].get("page", "1"))
            data = json.loads(json.dumps(full))
            data["translations"]["items"] = [items[number - 1]] if number <= 3 else []
            data["translations"]["total"] = 3
            return ok(data)

        site = anonymous_site()
        site.on("GET", API + "/translations/series", page)
        results = self.provider(site).search(GOT_S01E02, [HRV, SRP], {})
        self.assertEqual(len(results), 3)
        pages = [call["query"].get("page") for call in site.calls if call["path"] == API + "/translations/series"]
        self.assertEqual(pages, [None, "2", "3"])
        # The trimmed capture still says 153: a repeated page ends the paging.
        site = anonymous_site(translations=full)
        self.assertEqual(len(self.provider(site).search(GOT_S01E02, [HRV, SRP], {})), 3)
        self.assertEqual(site.count("GET", API + "/translations/series"), 2)

    def test_movie_search_verifies_imdb_and_drops_mismatch(self):
        site = anonymous_site()
        provider = self.provider(site)
        results = provider.search(INCEPTION, [HRV], {})
        self.assertIn("imdb_id", results[0]["matches"])
        self.assertEqual(results[0]["score"], 90)
        self.assertEqual(site.count("GET", API + "/movies/by-slug/inception-2010"), 1)

        site = anonymous_site()
        provider = self.provider(site)
        self.assertEqual(provider.search(dict(INCEPTION, imdb_id="tt0000001"), [HRV], {}), [])
        self.assertEqual(site.count("GET", API + "/translations/movies"), 0)

        site = anonymous_site()
        site.on("GET", API + "/movies/by-slug/inception-2010", (500, {}, {"error": {"code": "INTERNAL", "message": ""}}))
        provider = self.provider(site)
        results = provider.search(INCEPTION, [HRV], {})
        self.assertEqual([item["id"] for item in results], ["prijevodionline-m580-hrv"])
        self.assertNotIn("imdb_id", results[0]["matches"])

    def test_movie_tmdb_fallback_only_after_title_miss(self):
        site = anonymous_site()
        site.on("GET", API + "/movies", ok(_fixture("api_movies_by_tmdb_id_empty")))
        provider = self.provider(site)
        provider.search(dict(INCEPTION, tmdb_id=27205), [HRV], {})
        self.assertEqual(site.count("GET", API + "/movies"), 0)

        site = anonymous_site(movie_results=_fixture("api_search_results_empty"))
        site.on("GET", API + "/movies", ok(_fixture("api_movies_by_tmdb_id_empty")))
        provider = self.provider(site)
        self.assertEqual(provider.search(dict(INCEPTION, tmdb_id=27205), [HRV], {}), [])
        self.assertEqual(site.count("GET", API + "/movies"), 1)
        self.assertEqual(site.calls[-1]["query"], {"tmdbId": "27205", "perPage": "5"})
        self.assertEqual(site.count("GET", API + "/translations/movies"), 0)

        site = anonymous_site(movie_results=_fixture("api_search_results_empty"))
        provider = self.provider(site)
        self.assertEqual(provider.search(INCEPTION, [HRV], {}), [])
        self.assertEqual(site.count("GET", API + "/movies"), 0)

    def test_rows_without_price_are_skipped_and_a_priceless_list_is_an_api_change(self):
        data = season_capture()
        del data["translations"]["items"][0]["price"]
        site = cookie_member_site(member=member_me(series_free=True), translations=data)
        provider = self.provider(site)
        config = {"session_cookie": COOKIE_HEADER}
        results = provider.search(GOT_S01E02, [HRV, SRP], config)
        self.assertEqual(sorted(item["id"] for item in results), ["prijevodionline-s165016-srp", "prijevodionline-s37045-srp"])
        self.assertFalse([call for call in site.calls if call["path"].endswith("/download")])

        for rows in ("translations", "movieTranslations"):
            data = season_capture() if rows == "translations" else movie_items()
            for item in data[rows]["items"]:
                del item["price"]
            site = anonymous_site(translations=data) if rows == "translations" else anonymous_site(movies=data)
            video = GOT_S01E02 if rows == "translations" else INCEPTION
            route = "series" if rows == "translations" else "movies"
            with self.assertRaisesRegex(self.mod.ServiceUnavailable, f"API changed \\(/translations/{route}\\)"):
                self.provider(site).search(video, [HRV, SRP], {})

    def test_movie_multi_cd_rows_are_skipped(self):
        data = movie_items()
        data["movieTranslations"]["items"][-1]["cdCount"] = 2
        self.assertEqual(self.provider(anonymous_site(movies=data)).search(INCEPTION, [HRV], {}), [])
        data["movieTranslations"]["items"][-1]["cdCount"] = 1
        self.assertEqual(len(self.provider(anonymous_site(movies=data)).search(INCEPTION, [HRV], {})), 1)

    def test_specials_need_the_episode_title(self):
        data = season_capture()
        items = data["translations"]["items"]
        items[0].update(seasonNumber=99, episodeNumber=2, episodeName="Behind the Scenes")
        items[1].update(seasonNumber=99, episodeNumber=2, episodeName="Another Special")
        site = anonymous_site(translations=data)
        provider = self.provider(site)
        special = dict(GOT_S01E02, season=0, episode=2, title="Behind the Scenes")
        results = provider.search(special, [HRV, SRP], {})
        self.assertEqual([item["id"] for item in results], ["prijevodionline-s120299-hrv"])
        self.assertEqual(site.calls[-1]["query"]["seasonId"], "3399")
        self.assertEqual(provider.search(dict(special, title=""), [HRV, SRP], {}), [])

    def test_removed_series_finds_nothing_instead_of_failing(self):
        site = anonymous_site()
        provider = self.provider(site)
        self.assertEqual(len(provider.search(GOT_S01E02, [HRV], {})), 1)
        self.clock.advance(6 * 60 * 60 + 1)
        site.on("GET", API + "/series/935/seasons", api_error(404, "Series/NotFound", "Series not found"))
        site.reset()
        self.assertEqual(provider.search(GOT_S01E02, [HRV], {}), [])
        self.assertEqual(site.count("GET", API + "/series/935/seasons"), 1)
        site.reset()
        self.assertEqual(provider.search(GOT_S01E02, [HRV], {}), [])
        # The lookup was dropped and asked again; the missing seasons were not.
        self.assertEqual(site.paths(), [("GET", API + "/search/results")])
        # Any other error answer still fails the search.
        site = anonymous_site()
        site.on("GET", API + "/series/935/seasons", api_error(500, "INTERNAL", ""))
        with self.assertRaises(self.mod.ServiceUnavailable):
            self.provider(site).search(GOT_S01E02, [HRV], {})
        site = anonymous_site()
        site.on("GET", API + "/series/935/seasons", api_error(410, "Series/Gone", ""))
        with self.assertRaises(self.mod.ApiError):
            self.provider(site).search(GOT_S01E02, [HRV], {})

    def test_visitor_never_keeps_or_sends_site_cookies(self):
        site = anonymous_site()
        site.on("GET", API + "/auth/me", (200, [("Set-Cookie", "PHPSESSID=ANONSET; Path=/; Secure")], ANON_ME))
        provider = self.provider(site)
        provider.search(GOT_S01E02, [HRV], {})
        provider.search(dict(GOT_S01E02, episode=1), [HRV], {})
        self.assertTrue(all("Cookie" not in call["headers"] for call in site.calls))
        self.assertEqual(list(provider._anonymous.jar), [])

    def test_candidates_satisfy_contract(self):
        site = anonymous_site()
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV, SRP], {}) + provider.search(INCEPTION, [HRV], {})
        documented = (
            "provider", "id", "language", "release_info", "filename", "matches", "score",
            "score_without_hash", "score_out_of", "hash_verifiable", "hearing_impaired_verifiable",
            "hearing_impaired", "display", "provider_payload",
        )
        self.assertTrue(results)
        for candidate in results:
            for field in documented:
                self.assertIn(field, candidate)
            self.assertEqual(candidate["provider"], "prijevodionline")
            self.assertEqual(candidate["score_out_of"], 100)
            self.assertIs(candidate["hash_verifiable"], False)
            self.assertLessEqual(len(candidate["release_info"]), 300)
            payload = candidate["provider_payload"]
            encoded = json.dumps(payload)
            self.assertLess(len(encoded), 1024)
            self.assertEqual(
                set(payload),
                {"v", "kind", "translation_id", "season", "episode", "language", "script",
                 "list_price", "access", "mode", "file_name", "releases"},
            )
            self.assertNotIn("cookie", encoded.lower())
        # The same source check as tests.test_catalog.CandidateContractTests:
        # the one dict literal with a provider_payload key carries every field
        # the guide documents.
        guide = (ROOT / "docs" / "writing-a-scraper-provider.md").read_text("utf-8")
        line = next(item for item in guide.splitlines() if item.startswith("- `search()` returns"))
        required = re.findall(r"`([a-z_]+)`", line.split("each dict has:", 1)[1])
        self.assertEqual(set(required), set(documented))
        tree = ast.parse((PROVIDER_DIR / "provider.py").read_text("utf-8"))
        literals = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            and any(isinstance(key, ast.Constant) and key.value == "provider_payload" for key in node.keys)
        ]
        self.assertEqual(len(literals), 1)
        keys = {key.value for key in literals[0].keys if isinstance(key, ast.Constant)}
        self.assertEqual([field for field in required if field not in keys], [])

    def test_translation_detail_route_is_never_called(self):
        site = anonymous_site()
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV], {})
        provider.download(results[0]["provider_payload"], HRV, {})
        provider.search(INCEPTION, [HRV], {})
        detail = re.compile(r"^/api/v1/translations/(series|movies)/\d+$")
        self.assertFalse([call["path"] for call in site.calls if detail.match(call["path"])])


# C. Anonymous downloads


class AnonymousDownloadTests(ProviderTestCase):
    def test_anonymous_free_download_returns_archive(self):
        site = anonymous_site(translations=season_items(price=None))
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV], {})
        self.assertEqual(results[0]["provider_payload"]["access"], "free")
        site.reset()
        result = provider.download(results[0]["provider_payload"], HRV, {})
        self.assertEqual(base64.b64decode(result["archive_b64"]), GOT_ZIP)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(GOT_ZIP).hexdigest())
        self.assertEqual(result["episode"], 2)
        self.assertEqual(site.paths(), [("GET", API + "/translations/series/120299/download")])
        self.assertNotIn("Cookie", site.calls[0]["headers"])
        self.assertEqual(site.calls[0]["headers"]["Accept"], "*/*")

    def test_anonymous_granted_series_download_is_direct_and_never_quotes(self):
        site = anonymous_site()
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV], {})
        site.reset()
        result = provider.download(results[0]["provider_payload"], HRV, {})
        self.assertIn("archive_b64", result)
        self.assertEqual(site.paths(), [("GET", API + "/translations/series/120299/download")])
        self.assertFalse([call for call in site.calls if "/purchases" in call["path"]])

    def test_anonymous_download_refused_raises_account_required_and_hides_until_ttl(self):
        site = anonymous_site()
        site.on(
            "GET", API + "/translations/series/120299/download",
            api_error(403, "Auth/Forbidden", "Missing permission: series.translations.download"),
        )
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV, SRP], {})
        with self.assertRaises(self.mod.AccountRequired) as caught:
            provider.download(results[0]["provider_payload"], HRV, {})
        self.assertIn("does not let visitors", str(caught.exception))
        with self.assertLogs("prijevodionline", "INFO") as logs:
            self.assertEqual(provider.search(GOT_S01E02, [HRV, SRP], {}), [])
        self.assertIn("needs_account=3", "\n".join(logs.output))
        self.clock.advance(6 * 60 * 60 + 1)
        self.assertEqual(len(provider.search(GOT_S01E02, [HRV, SRP], {})), 3)

    def test_visitor_learns_only_from_real_refusals(self):
        route = API + "/translations/series/120299/download"
        answers = (
            (api_error(404, "Something/Else", "x"), ValueError, "refused the download \\(Something/Else\\)"),
            ((500, {}, b"oops"), self.mod.ServiceUnavailable, "did not answer"),
            ((200, {"content-type": "text/html"}, b"<!doctype html><html></html>"), self.mod.ServiceUnavailable, "API changed"),
        )
        for answer, error, text in answers:
            site = anonymous_site()
            site.on("GET", route, answer)
            provider = self.provider(site)
            results = provider.search(GOT_S01E02, [HRV, SRP], {})
            with self.assertRaisesRegex(error, text):
                provider.download(results[0]["provider_payload"], HRV, {})
            self.assertEqual(len(provider.search(GOT_S01E02, [HRV, SRP], {})), 3, answer[0])
        for answer in (api_error(401, "Auth/Unauthorized", ""), (402, {}, b""), api_error(400, "Tokens/Required", "")):
            site = anonymous_site()
            site.on("GET", route, answer)
            provider = self.provider(site)
            results = provider.search(GOT_S01E02, [HRV, SRP], {})
            with self.assertRaises(self.mod.AccountRequired):
                provider.download(results[0]["provider_payload"], HRV, {})
            self.assertEqual(provider.search(GOT_S01E02, [HRV, SRP], {}), [], answer[0])

    def test_free_movie_without_account_raises_account_required_without_request(self):
        site = anonymous_site()
        provider = self.provider(site)
        payload = self.payload(580, kind="movie", access="account_required", list_price=None)
        with self.assertRaises(self.mod.AccountRequired) as caught:
            provider.download(payload, HRV, {})
        self.assertIn("needs an account", str(caught.exception))
        self.assertEqual(site.calls, [])

    def test_download_body_validation(self):
        route = API + "/translations/series/120299/download"
        cases = (
            (b"   \n", ValueError, "empty"),
            ((404, {}, _fixture("api_error_series_download_not_found")), ValueError, "removed"),
            ((200, {"content-type": "application/json"}, {"download": {"url": "x"}}), ValueError, "JSON"),
            (b"PK\x03\x04broken", ValueError, "damaged"),
        )
        for answer, error, text in cases:
            site = anonymous_site()
            site.on("GET", route, answer if isinstance(answer, tuple) else (200, {}, answer))
            provider = self.provider(site)
            with self.assertRaisesRegex(error, text):
                provider.download(self.payload(access="free", list_price=None), HRV, {})
        # The app shell is a changed route for a visitor, never a learned
        # refusal, and a broken answer for a member.
        shell = b"<!doctype html><html><head><title>Prijevodi Online</title></head><body></body></html>"
        site = anonymous_site()
        site.on("GET", route, (200, {"content-type": "text/html"}, shell))
        provider = self.provider(site)
        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "API changed"):
            provider.download(self.payload(), HRV, {})
        self.assertEqual(len(provider.search(GOT_S01E02, [HRV, SRP], {})), 3)
        site = cookie_member_site()
        site.on("GET", route, (200, {"content-type": "text/html"}, shell))
        with self.assertRaisesRegex(ValueError, "web page"):
            self.provider(site).download(self.payload(access="free", list_price=None), HRV, {"session_cookie": COOKIE_HEADER})
        # A bare subtitle goes back in content mode, a RAR in archive mode.
        for body, key in ((SRT, "content_b64"), (b"Rar!\x1a\x07\x00rar-bytes", "archive_b64")):
            site = anonymous_site()
            site.on("GET", route, (200, {}, body))
            result = self.provider(site).download(self.payload(access="free", list_price=None, file_name="x.srt"), HRV, {})
            self.assertEqual(base64.b64decode(result[key]), body)
            self.assertNotIn("encoding", result)
        self.assertEqual(result["episode"], 2)

    def test_copyright_notice_raises_download_blocked(self):
        site = anonymous_site()
        site.on(
            "GET", API + "/translations/series/120299/download",
            api_error(451, "Copyright/InfringementNotice", "Blocked", data={"title": "Game of Thrones"}),
        )
        with self.assertRaisesRegex(self.mod.DownloadBlocked, "copyright notice \\(Game of Thrones\\)"):
            self.provider(site).download(self.payload(), HRV, {})

    def test_old_payloads_and_bad_payloads_are_rejected(self):
        provider = self.provider(anonymous_site())
        old = {"provider": "prijevodionline", "schema": 1, "subtitle_id": "18050", "url": "https://x"}
        for payload in (old, None, self.payload(v=True), self.payload(translation_id=0), self.payload(kind="x"), self.payload(access="x")):
            with self.assertRaises(ValueError):
                provider.download(payload, HRV, {})


class ArchiveMemberTests(ProviderTestCase):
    """The 0.2.1 member selection is kept as is."""

    def result(self, files, **payload):
        body = _zip_body(files)
        base = {"kind": "series", "season": 1, "episode": 1, "releases": []}
        base.update(payload)
        return self.mod._download_payload(body, base)

    def test_pins_release_member_for_requested_episode(self):
        result = self.result(
            {
                "Game.of.Thrones.S01E02.HDTV.srt": "wrong episode",
                "Game.of.Thrones.S01E01.HDTV.XviD-FEVER.srt": "wrong release",
                "Game.of.Thrones.S01E01.720p.HDTV.CTU.srt": "right subtitle",
            },
            releases=["720p", "hdtv", "ctu"],
        )
        self.assertEqual(result["member"], "Game.of.Thrones.S01E01.720p.HDTV.CTU.srt")
        self.assertNotIn("episode", result)

    def test_defers_on_ties_and_never_pins_the_wrong_season(self):
        tie = self.result({"Show.S01E01.HDTV.part1.srt": "a", "Show.S01E01.HDTV.part2.srt": "b"}, releases=["hdtv"])
        self.assertNotIn("member", tie)
        self.assertEqual(tie["episode"], 1)
        wrong = self.result(
            {"Show.S01E05.720p.WEB.CTU.srt": "1", "Show.S03E05.720p.WEB.CTU.srt": "3"},
            season=2, episode=5, releases=["720p"],
        )
        self.assertNotIn("member", wrong)
        lone = self.result(
            {"Show.S01E05.720p.srt": "1", "Show.S02E05.720p.srt": "2"}, season=2, episode=5
        )
        self.assertEqual(lone["member"], "Show.S02E05.720p.srt")

    def test_boundary_and_sidecar_guards(self):
        guarded = self.result(
            {"Show.Extras1E02.WEB-DL-CTU.srt": "bonus", "Show.S01E02.HDTV-FEVER.srt": "real"},
            episode=2, releases=["web", "dl", "ctu"],
        )
        self.assertEqual(guarded["member"], "Show.S01E02.HDTV-FEVER.srt")
        sidecars = self.result(
            {
                "__MACOSX/._Show.S07E20.srt": "mac",
                ".hidden.srt": "dot",
                "Show.720p.WEB-DL.srt": "decoy",
                "Show.S07.E20.HDTV-FEVER.srt": "wrong release",
                "Show.S07.E20.WEB-DL-CTU.srt": "right",
            },
            season=7, episode=20, releases=["web", "dl", "ctu"],
        )
        self.assertEqual(sidecars["member"], "Show.S07.E20.WEB-DL-CTU.srt")

    def test_movie_archive_pins_only_a_single_subtitle(self):
        single = self.mod._download_payload(_zip_body({"Inception.2010.srt": "x", "readme.txt": "y"}), {"kind": "movie"})
        self.assertEqual(single["member"], "Inception.2010.srt")
        several = self.mod._download_payload(_zip_body({"a.srt": "x", "b.srt": "y"}), {"kind": "movie"})
        self.assertNotIn("member", several)
        self.assertIsNone(several["episode"])


# D. Paid paths (mocked)


class PaidDownloadTests(ProviderTestCase):
    CONFIG = {"session_cookie": COOKIE_HEADER, "allow_paid_downloads": True, "max_tokens_per_download": "2"}

    def member_provider(self, config=None, member=None, translations=None):
        site = cookie_member_site(member=member, translations=translations)
        provider = self.provider(site)
        return site, provider, dict(self.CONFIG, **(config or {}))

    def purchases(self, site):
        return [call for call in site.calls if call["path"].startswith(API + "/purchases")]

    def test_paid_refused_without_opt_in(self):
        site, provider, config = self.member_provider({"allow_paid_downloads": False})
        with self.assertLogs("prijevodionline", "INFO") as logs:
            self.assertEqual(provider.search(GOT_S01E02, [HRV, SRP], config), [])
        self.assertIn("spending_off=3", "\n".join(logs.output))
        with self.assertRaises(self.mod.PaidDownloadRefused) as caught:
            provider.download(self.payload(access="priced", mode="cookie"), HRV, config)
        self.assertIn("nothing was spent", str(caught.exception))
        self.assertEqual(self.purchases(site), [])

    def test_purchase_quote_for_a_granted_row_never_spends_with_spending_off(self):
        # The most common member path: a role holding downloadFree sees price-1
        # series rows as granted, shown with spending off. Only the gate after
        # the quote stops a purchase here.
        site = cookie_member_site(member=member_me(series_free=True))
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
        provider = self.provider(site)
        config = {"session_cookie": COOKIE_HEADER}
        results = provider.search(GOT_S01E02, [HRV], config)
        self.assertEqual([item["provider_payload"]["access"] for item in results], ["granted"])
        with self.assertRaises(self.mod.PaidDownloadRefused) as caught:
            provider.download(results[0]["provider_payload"], HRV, config)
        self.assertIn("nothing was spent", str(caught.exception))
        self.assertIn("Allow token-priced downloads", str(caught.exception))
        self.assertEqual(site.count("POST", API + "/purchases/intent"), 1)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)
        self.assertEqual(site.count("GET", API + "/translations/series/120299/download"), 0)
        # The grant proved unreliable, so the row is now priced and hidden.
        with self.assertLogs("prijevodionline", "INFO") as logs:
            self.assertEqual(provider.search(GOT_S01E02, [HRV], config), [])
        self.assertIn("spending_off=1", "\n".join(logs.output))

    def test_purchase_quote_for_an_owned_or_promoted_row_never_spends_with_spending_off(self):
        config = {"session_cookie": COOKIE_HEADER}
        for payload in (
            self.payload(access="owned", list_price=1),
            self.payload(access="account_required", list_price=1),
        ):
            site = cookie_member_site(member=member_me(series_free=True))
            site.on("POST", API + "/purchases/intent", quote_purchase(120299))
            site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
            provider = self.provider(site)
            with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "nothing was spent"):
                provider.download(payload, HRV, config)
            self.assertEqual(site.count("POST", API + "/purchases/intent"), 1, payload["access"])
            self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0, payload["access"])
        # Without downloadFree the promoted row is priced and refused before any quote.
        site = cookie_member_site()
        provider = self.provider(site)
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "token-priced downloads are off"):
            provider.download(self.payload(access="account_required", list_price=1), HRV, config)
        self.assertEqual(self.purchases(site), [])

    def test_purchase_quote_with_an_invalid_cap_never_spends(self):
        for cap in ("abc", "0", None, "4"):
            site = cookie_member_site(member=member_me(series_free=True))
            site.on("POST", API + "/purchases/intent", quote_purchase(120299))
            site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
            provider = self.provider(site)
            config = {"session_cookie": COOKIE_HEADER, "allow_paid_downloads": True, "max_tokens_per_download": cap}
            with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "not valid"):
                provider.download(self.payload(access="granted"), HRV, config)
            self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0, cap)

    def test_owned_subtitle_is_never_bought_again(self):
        # A search lists the row as owned; a later worker (a restart, another
        # pool, or the owned cache expired) gets a purchase quote for it.
        owned_list = with_variant(season_capture(), "translations", 0, isPurchased=True, isRevoked=False)
        site = cookie_member_site(translations=owned_list)
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=1))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
        config = dict(self.CONFIG, max_tokens_per_download="1")
        clock = FakeClock()
        searched = self.provider(site, clock)
        owned = [item for item in searched.search(GOT_S01E02, [HRV], config) if item["provider_payload"]["access"] == "owned"]
        self.assertEqual(len(owned), 1)
        self.assertTrue(owned[0]["release_info"].endswith("owned"))
        self.assertEqual(owned[0]["provider_payload"]["list_price"], 1)
        fresh = self.provider(site)
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "already bought.*nothing was spent"):
            fresh.download(owned[0]["provider_payload"], HRV, config)
        clock.advance(31 * 60)
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "already bought"):
            searched.download(owned[0]["provider_payload"], HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)
        # Seen as owned by this worker's own search, it downloads without a quote.
        site.reset()
        searched.search(GOT_S01E02, [HRV], config)
        site.reset()
        searched.download(owned[0]["provider_payload"], HRV, config)
        self.assertEqual(site.paths(), [("GET", API + "/translations/series/120299/download")])

    def test_quote_guards_each_refuse_on_their_own(self):
        cases = (
            (dict(can_afford=False, balance=None, token="T"), self.mod.InsufficientTokens, "cannot buy this subtitle right now"),
            (dict(can_afford="true", balance=3, token="T"), self.mod.InsufficientTokens, "cannot buy this subtitle right now"),
            (dict(can_afford=True, balance=0, token="T"), self.mod.InsufficientTokens, "needs 1 token, the account has 0"),
            (dict(can_afford=True, balance=3, token=None), self.mod.PaidDownloadRefused, "no purchase token"),
            (dict(can_afford=True, balance=3, token="  "), self.mod.PaidDownloadRefused, "no purchase token"),
        )
        for quote, error, text in cases:
            site, provider, config = self.member_provider()
            site.on("POST", API + "/purchases/intent", quote_purchase(120299, **quote))
            site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
            with self.assertRaisesRegex(error, text):
                provider.download(self.payload(access="priced"), HRV, config)
            self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0, quote)

    def test_limits_the_user_controls_are_named_before_the_balance(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=5, balance=0, can_afford=False))
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "limit of 2"):
            provider.download(self.payload(access="priced"), HRV, config)
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=2, balance=0, can_afford=False))
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "above the 1 shown at search time"):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)

    def test_quote_for_a_different_subtitle_is_refused(self):
        for answer in (
            quote_purchase(999999),
            quote_purchase(120299, kind="movie"),
            quote_download(999999),
            ok({"intent": {"action": "download", "translationId": "999999", "translationType": "series"}}),
        ):
            site, provider, config = self.member_provider()
            site.on("POST", API + "/purchases/intent", answer)
            site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
            with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "different subtitle; nothing was spent"):
                provider.download(self.payload(access="priced"), HRV, config)
            self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)
            self.assertEqual(site.count("GET", API + "/translations/series/120299/download"), 0)
        # A quote that echoes nothing, the same subtitle as a string id, or a
        # type spelled some other way, is accepted.
        for intent in (
            {"action": "download"},
            {"action": "download", "translationId": "120299"},
            {"action": "download", "translationId": 120299, "translationType": "seriesTranslation"},
        ):
            site, provider, config = self.member_provider()
            site.on("POST", API + "/purchases/intent", ok({"intent": intent}))
            self.assertIn("archive_b64", provider.download(self.payload(access="priced"), HRV, config))

    def test_low_balance_hides_priced_rows(self):
        site, provider, config = self.member_provider(member=member_me(balance=0))
        with self.assertLogs("prijevodionline", "INFO") as logs:
            self.assertEqual(provider.search(GOT_S01E02, [HRV, SRP], config), [])
        self.assertIn("low_balance=3", "\n".join(logs.output))

    def test_granted_rows_carry_a_may_cost_tag_only_when_spending_is_on(self):
        site = cookie_member_site(member=member_me(series_free=True))
        provider = self.provider(site)
        on = provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual(on[0]["provider_payload"]["access"], "granted")
        self.assertTrue(on[0]["release_info"].endswith("may cost 1 token"), on[0]["release_info"])
        off = provider.search(GOT_S01E02, [HRV], {"session_cookie": COOKIE_HEADER})
        self.assertNotIn("token", off[0]["release_info"])
        anonymous = self.provider(anonymous_site()).search(GOT_S01E02, [HRV], {"allow_paid_downloads": True})
        self.assertNotIn("token", anonymous[0]["release_info"])

    def test_402_after_a_purchase_does_not_claim_nothing_was_spent(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299), quote_download(120299))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
        site.on(
            "GET", API + "/translations/series/120299/download",
            api_error(402, "Tokens/PurchaseRequired", "Purchase required"), zip_answer(),
        )
        with self.assertRaises(ValueError) as caught:
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertIn("purchase went through", str(caught.exception))
        self.assertNotIn("nothing was spent", str(caught.exception))
        # The retry quotes again, gets "download" and never confirms twice.
        self.assertIn("archive_b64", provider.download(self.payload(access="priced"), HRV, config))
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)

    def test_overcharge_stops_purchases_for_a_day(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=1), quote_purchase(165016, cost=1))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299, cost=2), confirm_ok(165016))
        with self.assertLogs("prijevodionline", "ERROR"):
            provider.download(self.payload(access="priced"), HRV, config)
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "charged more than its quote.*nothing was spent"):
            provider.download(self.payload(165016, access="priced"), SRP, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)
        self.clock.advance(24 * 60 * 60 + 1)
        provider.download(self.payload(165016, access="priced"), SRP, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 2)

    def test_uncertain_purchase_survives_a_fresh_cookie_for_the_same_account(self):
        site = cookie_member_site()
        member = member_me()
        site.on(
            "GET", API + "/auth/me",
            lambda call: ok(member) if "SENTINEL" in call["headers"].get("Cookie", "") else ok(ANON_ME),
        )
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", socket.timeout("timed out"), confirm_ok(120299))
        provider = self.provider(site)
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, self.CONFIG)
        fresh = dict(self.CONFIG, session_cookie="Cookie: SMFCookie123=FRESHSENTINEL")
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, fresh)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)

    def test_paid_refused_over_cap(self):
        priced_two = with_variant(season_capture(), "translations", 0, price=2)
        site, provider, config = self.member_provider({"max_tokens_per_download": "1"}, translations=priced_two)
        with self.assertLogs("prijevodionline", "INFO") as logs:
            results = provider.search(GOT_S01E02, [HRV], config)
        self.assertEqual(results, [])
        self.assertIn("over_cap=1", "\n".join(logs.output))
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "limit of 1; nothing was spent"):
            provider.download(self.payload(access="priced", list_price=2), HRV, config)
        self.assertEqual(self.purchases(site), [])

        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=3))
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "limit of 2"):
            provider.download(self.payload(access="priced", list_price=2), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/intent"), 1)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)

    def test_paid_refused_when_quote_exceeds_search_price(self):
        site, provider, config = self.member_provider({"max_tokens_per_download": "5"})
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=2))
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "above the 1 shown at search time"):
            provider.download(self.payload(access="priced", list_price=1), HRV, config)
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "free at search time"):
            provider.download(self.payload(access="granted", list_price=None), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)

    def test_paid_allowed_with_opt_in_spends_exactly_once(self):
        site, provider, config = self.member_provider()
        results = provider.search(GOT_S01E02, [HRV], config)
        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["provider_payload"]["access"], "priced")
        self.assertTrue(candidate["release_info"].endswith("costs 1 token"), candidate["release_info"])
        self.assertEqual(candidate["display"]["uploader"], "translator_a (1 token)")
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=1))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299, cost=1))
        site.reset()
        with self.assertLogs("prijevodionline", "INFO") as logs:
            result = provider.download(candidate["provider_payload"], HRV, config)
        self.assertEqual(base64.b64decode(result["archive_b64"]), GOT_ZIP)
        self.assertEqual(
            site.paths(),
            [
                ("POST", API + "/purchases/intent"),
                ("POST", API + "/purchases/confirm"),
                ("GET", API + "/translations/series/120299/download"),
            ],
        )
        self.assertEqual(json.loads(site.calls[0]["body"]), {"translationId": 120299, "translationType": "series"})
        self.assertEqual(json.loads(site.calls[1]["body"]), {"token": "QUOTETOKEN"})
        self.assertIn("spent 1 token on series subtitle 120299", "\n".join(logs.output))
        # The snapshot and the season list are fetched fresh after a purchase.
        site.reset()
        provider.search(GOT_S01E02, [HRV], config)
        self.assertEqual(site.paths(), [("GET", API + "/auth/me"), ("GET", API + "/translations/series")])

    def test_charge_above_the_quote_is_logged_as_an_error(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=1))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299, cost=2))
        with self.assertLogs("prijevodionline", "ERROR") as logs:
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertIn("more than the 1 quoted", "\n".join(logs.output))

    def test_quote_download_action_skips_confirm(self):
        site, provider, config = self.member_provider({"allow_paid_downloads": False})
        site.on("POST", API + "/purchases/intent", quote_download(120299))
        result = provider.download(self.payload(access="granted"), HRV, config)
        self.assertIn("archive_b64", result)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)
        self.assertEqual(site.count("GET", API + "/translations/series/120299/download"), 1)

    def test_insufficient_balance_from_quote(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299, cost=1, balance=0, can_afford=False, token=None))
        with self.assertRaisesRegex(self.mod.InsufficientTokens, "needs 1 token, the account has 0; nothing was spent"):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)

    def test_insufficient_balance_from_confirm(self):
        for status in (400, 402):
            site, provider, config = self.member_provider()
            site.on("POST", API + "/purchases/intent", quote_purchase(120299))
            site.on("POST", API + "/purchases/confirm", confirm_insufficient(status), confirm_ok(120299))
            with self.assertRaisesRegex(self.mod.InsufficientTokens, "nothing was spent"):
                provider.download(self.payload(access="priced"), HRV, config)
            self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)
            # The ledger was cleared: a later attempt may buy.
            provider.download(self.payload(access="priced"), HRV, config)
            self.assertEqual(site.count("POST", API + "/purchases/confirm"), 2)

    def test_ambiguous_confirm_is_never_retried_and_blocks_rebuy(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", socket.timeout("timed out"))
        with self.assertRaisesRegex(self.mod.PurchaseUncertain, "check Purchases"):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)
        site.on("POST", API + "/purchases/intent", quote_download(120299))
        result = provider.download(self.payload(access="priced"), HRV, config)
        self.assertIn("archive_b64", result)
        # The download answer cleared the uncertain entry.
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
        provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 2)

    def test_confirmed_purchase_is_never_paid_again(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
        provider.download(self.payload(access="priced"), HRV, config)
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)

    def test_confirm_bypasses_retry_loop_and_flaresolverr(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", (502, {}, b"bad gateway"), confirm_ok(120299))
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)
        self.assertEqual(self.clock.sleeps, [])

        site, provider, config = self.member_provider({"flaresolverr_url": "http://fs:8191/v1"})
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on(
            "POST", API + "/purchases/confirm",
            (403, {"cf-mitigated": "challenge", "content-type": "text/html"}, b"<html>Just a moment...</html>"),
        )
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "Cloudflare challenge; nothing was spent"):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 1)

    def test_throttled_confirm_is_uncertain_and_throttles(self):
        site, provider, config = self.member_provider()
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", (429, {"x-ratelimit-reset": "40"}, {"error": {"code": "RATE_LIMITED", "message": ""}}))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(caught.exception.retry_after, 40)
        self.assertIn("check Purchases", str(caught.exception))
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, config)

    def test_spend_ledger_and_learned_state_scoped_per_account(self):
        other_cookie = "Cookie: SMFCookie123=OTHERSENTINEL"
        site = cookie_member_site(member=member_me(series_free=True))
        member_a = member_me(series_free=True)
        member_b = member_me(series_free=True)
        member_b["user"]["id"] = 4343

        def me(call):
            cookie = call["headers"].get("Cookie", "")
            if "OTHERSENTINEL" in cookie:
                return ok(member_b)
            return ok(member_a) if "SENTINEL" in cookie else ok(ANON_ME)

        site.on("GET", API + "/auth/me", me)
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        site.on("POST", API + "/purchases/confirm", socket.timeout("timed out"))
        provider = self.provider(site)
        account_a = dict(self.CONFIG)
        account_b = dict(self.CONFIG, session_cookie=other_cookie)
        # Account A: a granted item whose quote says "purchase" teaches A to
        # treat grants as priced, and the timed-out confirm blocks a rebuy.
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="granted"), HRV, account_a)
        self.assertEqual(provider.search(GOT_S01E02, [HRV], account_a)[0]["provider_payload"]["access"], "priced")
        # Account B is unaffected by either.
        self.assertEqual(provider.search(GOT_S01E02, [HRV], account_b)[0]["provider_payload"]["access"], "granted")
        site.on("POST", API + "/purchases/confirm", confirm_ok(120299))
        provider.download(self.payload(access="priced"), HRV, account_b)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 2)
        # Back on A, the uncertain purchase still blocks.
        site.on("POST", API + "/purchases/intent", quote_purchase(120299))
        with self.assertRaises(self.mod.PurchaseUncertain):
            provider.download(self.payload(access="priced"), HRV, account_a)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 2)

    def test_short_deadline_or_rate_budget_refuses_before_confirm(self):
        site, provider, config = self.member_provider()
        site.on(
            "POST", API + "/purchases/intent",
            quote_purchase(120299, headers={"x-ratelimit-remaining": "24", "x-ratelimit-reset": "50"}),
        )
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "request budget"):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)

        site, provider, config = self.member_provider()

        def slow_quote(call):
            self.clock.advance(20)
            return quote_purchase(120299)

        site.on("POST", API + "/purchases/intent", slow_quote)
        with self.assertRaisesRegex(self.mod.PaidDownloadRefused, "not enough time"):
            provider.download(self.payload(access="priced"), HRV, config)
        self.assertEqual(site.count("POST", API + "/purchases/confirm"), 0)

    def test_refusal_classes_never_use_host_throttling_names(self):
        refusals = (
            "AccountRequired", "AccountLoginFailed", "PaidDownloadRefused", "InsufficientTokens",
            "PurchaseUncertain", "DownloadBlocked", "CloudflareBlockedError",
        )
        for name in refusals:
            self.assertNotIn(name, MAPPED_HOST_NAMES)
            self.assertTrue(issubclass(getattr(self.mod, name), RuntimeError))
        self.assertNotIn("ApiError", MAPPED_HOST_NAMES)
        for name in ("AuthenticationError", "AuthenticationRequired", "ConfigurationError", "DownloadLimitExceeded", "TooManyRequests", "RateLimited"):
            self.assertFalse(hasattr(self.mod, name), name)
        self.assertEqual(self.mod.APIThrottled("x", retry_after=12).retry_after, 12)
        self.assertIn("ServiceUnavailable", MAPPED_HOST_NAMES)


# E. Authentication (mocked)


class AuthenticationTests(ProviderTestCase):
    CONFIG = {"username": USER, "password": PASSWORD}

    def password_site(self, **kwargs):
        site = anonymous_site()
        return site, PasswordSite(site, **kwargs)

    def test_password_login_caches_session(self):
        site, account = self.password_site(member=member_me(series_free=True))
        site.on("POST", API + "/purchases/intent", quote_download(120299))
        provider = self.provider(site)
        results = []
        for _ in range(3):
            results = provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual(results[0]["provider_payload"]["mode"], "password")
        provider.download(results[0]["provider_payload"], HRV, self.CONFIG)
        self.assertEqual(account.logins, 1)
        login = next(call for call in site.calls if call["path"] == API + "/auth/login")
        body = json.loads(login["body"])
        self.assertEqual(set(body), {"username", "password", "rememberMe"})
        self.assertIs(body["rememberMe"], True)
        self.assertEqual(body["username"], USER)
        self.assertEqual(login["headers"]["Origin"], "https://www.prijevodi-online.org")
        self.assertEqual(login["headers"]["Referer"], "https://www.prijevodi-online.org/")
        self.assertEqual(login["headers"]["Content-Type"], "application/json")
        later = [call for call in site.calls if call["path"] != API + "/auth/login"][1:]
        self.assertTrue(all(f"PHPSESSID={LOGIN_COOKIE}1" in call["headers"].get("Cookie", "") for call in later))

    def test_expired_session_relogs_once_and_retries_once(self):
        site, account = self.password_site(member=member_me(series_free=True))
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV], self.CONFIG)
        account.expire()

        def refuse_expired(call):
            if any(f"PHPSESSID={value}" in call["headers"].get("Cookie", "") for value in account.valid):
                return quote_download(120299)
            return api_error(403, "Auth/Forbidden", "Missing permission: series.translations.download")

        site.on("POST", API + "/purchases/intent", refuse_expired)
        result = provider.download(results[0]["provider_payload"], HRV, self.CONFIG)
        self.assertIn("archive_b64", result)
        self.assertEqual(account.logins, 2)

        site.on("POST", API + "/purchases/intent", api_error(403, "Auth/Forbidden", "Missing permission: x.y"))
        account.expire()
        with self.assertRaises(self.mod.AccountLoginFailed):
            provider.download(results[0]["provider_payload"], HRV, self.CONFIG)
        self.assertEqual(account.logins, 3)

    def test_permission_403_with_live_session_does_not_relogin(self):
        site, account = self.password_site(member=member_me(series_free=True))
        site.on(
            "POST", API + "/purchases/intent",
            api_error(403, "Auth/Forbidden", "Missing permission: series.translations.download or series.translations.downloadFree"),
        )
        provider = self.provider(site)
        with self.assertRaises(self.mod.AccountRequired) as caught:
            provider.download(self.payload(access="granted"), HRV, self.CONFIG)
        self.assertIn(
            "Missing permission: series.translations.download or series.translations.downloadFree",
            str(caught.exception),
        )
        self.assertEqual(account.logins, 1)

    def test_invalid_login_degrades_to_anonymous_and_is_suppressed(self):
        site, account = self.password_site(
            login=api_error(401, "Auth/InvalidCredentials", f"Invalid password for {USER}")
        )
        provider = self.provider(site)
        with self.assertLogs("prijevodionline", "WARNING") as logs:
            results = provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["provider_payload"]["mode"], "anonymous")
        self.assertIn("invalid_credentials", "\n".join(logs.output))
        self.assertNotIn(USER, "\n".join(logs.output))
        self.clock.advance(30 * 60)
        provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual(account.logins, 1)
        provider.search(GOT_S01E02, [HRV], dict(self.CONFIG, password="NEWPASSSENTINEL"))
        self.assertEqual(account.logins, 2)
        self.clock.advance(61 * 60)
        provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual(account.logins, 3)

    def test_captcha_login_failure_points_to_session_cookie(self):
        site, account = self.password_site(
            login=api_error(
                400, "VALIDATION_ERROR", "Validation failed: body",
                validation=[{"path": ["captchaToken"], "message": "Required"}],
            )
        )
        provider = self.provider(site)
        self.assertEqual(len(provider.search(GOT_S01E02, [HRV], self.CONFIG)), 1)
        with self.assertRaises(self.mod.AccountLoginFailed) as caught:
            provider.download(self.payload(access="priced"), HRV, self.CONFIG)
        self.assertIn("captcha", str(caught.exception))
        self.assertIn("session cookie", str(caught.exception))
        self.assertEqual(account.logins, 1)

        site, account = self.password_site(login=api_error(400, "Bad/Request", "Please try again"))
        site.on("GET", API + "/auth/captcha-config", ok(_fixture("api_auth_captcha_config")))
        provider = self.provider(site)
        provider.search(GOT_S01E02, [HRV], self.CONFIG)
        with self.assertRaisesRegex(self.mod.AccountLoginFailed, "captcha"):
            provider.download(self.payload(access="owned"), HRV, self.CONFIG)
        self.assertEqual(site.count("GET", API + "/auth/captcha-config"), 1)

    def test_session_cookie_mode_validates_and_skips_login(self):
        site = cookie_member_site(member=member_me(series_free=True))
        site.on("POST", API + "/auth/login", AssertionError("no login in cookie mode"))
        site.on("POST", API + "/purchases/intent", quote_download(120299))
        provider = self.provider(site)
        config = {
            "session_cookie": COOKIE_HEADER,
            "session_user_agent": "Mozilla/5.0 TestBrowser",
            "username": USER,
            "password": PASSWORD,
        }
        results = provider.search(GOT_S01E02, [HRV], config)
        self.assertEqual(results[0]["provider_payload"]["mode"], "cookie")
        provider.download(results[0]["provider_payload"], HRV, config)
        self.assertEqual(site.count("POST", API + "/auth/login"), 0)
        for call in site.calls:
            self.assertTrue(call["url"].startswith("https://www.prijevodi-online.org/api/v1/"))
            pairs = {part.strip() for part in call["headers"]["Cookie"].split(";")}
            self.assertEqual(pairs, {f"SMFCookie123={COOKIE_VALUE}", "PHPSESSID=PHPSENTINEL"})
            self.assertEqual(call["headers"]["User-Agent"], "Mozilla/5.0 TestBrowser")
        self.assertEqual(site.count("GET", API + "/auth/me"), 1)

    def test_expired_session_cookie_falls_back_to_password_then_anonymous(self):
        site, account = self.password_site()
        provider = self.provider(site)
        config = dict(self.CONFIG, session_cookie="SMFCookie123=STALESENTINEL")
        with self.assertLogs("prijevodionline", "WARNING") as logs:
            results = provider.search(GOT_S01E02, [HRV], config)
        self.assertIn("session cookie is not signed in", "\n".join(logs.output))
        self.assertEqual(account.logins, 1)
        self.assertEqual(provider.search(GOT_S01E02, [HRV], config), results)
        self.assertEqual(account.logins, 1)

        site = anonymous_site()
        provider = self.provider(site)
        cookie_only = {"session_cookie": "SMFCookie123=STALESENTINEL"}
        self.assertEqual(len(provider.search(GOT_S01E02, [HRV], cookie_only)), 1)
        with self.assertRaisesRegex(self.mod.AccountLoginFailed, "session cookie is no longer signed in"):
            provider.download(self.payload(access="priced"), HRV, cookie_only)

    def test_credential_change_drops_old_session_and_state(self):
        site, account = self.password_site()
        provider = self.provider(site)
        provider.search(GOT_S01E02, [HRV], self.CONFIG)
        old_sessions = list(provider._sessions.values())
        self.assertEqual(len(old_sessions), 1)
        self.assertTrue(list(old_sessions[0].jar))
        site.reset()
        provider.search(GOT_S01E02, [HRV], dict(self.CONFIG, password="SECONDPASSSENTINEL"))
        self.assertEqual(account.logins, 2)
        self.assertEqual(list(old_sessions[0].jar), [])
        self.assertEqual(len(provider._sessions), 1)
        self.assertIn(("GET", API + "/translations/series"), site.paths())

    def test_login_transport_failure_degrades_to_anonymous(self):
        site, account = self.password_site(login=urllib.error.URLError(ConnectionResetError()))
        provider = self.provider(site)
        with self.assertLogs("prijevodionline", "WARNING") as logs:
            results = provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual([item["provider_payload"]["mode"] for item in results], ["anonymous"])
        self.assertIn("sign-in failed (unavailable)", "\n".join(logs.output))
        provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual(account.logins, 1)
        with self.assertRaisesRegex(self.mod.AccountLoginFailed, "did not answer"):
            provider.download(self.payload(access="priced"), HRV, self.CONFIG)

    def test_throttled_captcha_check_does_not_fail_the_search(self):
        site, account = self.password_site(login=api_error(400, "Bad/Request", "Please try again"))
        site.on("GET", API + "/auth/captcha-config", (429, {"retry-after": "40"}, {"error": {"code": "RATE_LIMITED", "message": ""}}))
        provider = self.provider(site)
        results = provider.search(GOT_S01E02, [HRV], self.CONFIG)
        self.assertEqual([item["provider_payload"]["mode"] for item in results], ["anonymous"])
        self.assertEqual(site.count("GET", API + "/auth/captcha-config"), 1)

    def test_partial_credentials_warn_and_search_anonymously(self):
        site = anonymous_site()
        provider = self.provider(site)
        with self.assertLogs("prijevodionline", "WARNING") as logs:
            self.assertEqual(len(provider.search(GOT_S01E02, [HRV], {"username": USER})), 1)
        self.assertIn("both a username and a password", "\n".join(logs.output))
        with self.assertRaisesRegex(self.mod.AccountLoginFailed, "both a username and a password"):
            provider.download(self.payload(access="priced"), HRV, {"username": USER})


# F. Site down, rate limits, Cloudflare


class TransportTests(ProviderTestCase):
    def test_site_down_raises_service_unavailable(self):
        site = anonymous_site()
        site.on("GET", API + "/auth/me", urllib.error.URLError(ConnectionRefusedError()))
        provider = self.provider(site)
        with self.assertRaises(self.mod.ServiceUnavailable):
            provider.search(GOT_S01E02, [HRV], {})
        self.assertEqual(site.count("GET", API + "/auth/me"), 3)
        self.assertEqual(self.clock.sleeps, [0.5, 1.0])

        site = anonymous_site()

        def slow_failure(call):
            self.clock.advance(15)
            return 503, {}, b"down"

        site.on("GET", API + "/auth/me", slow_failure)
        provider = self.provider(site)
        with self.assertRaises(self.mod.ServiceUnavailable):
            provider.search(GOT_S01E02, [HRV], {})
        self.assertEqual(site.count("GET", API + "/auth/me"), 2)

    def test_app_shell_or_redirect_from_api_raises_instead_of_empty_results(self):
        shell = b"<!doctype html><html><head><title>Prijevodi Online</title></head><body><div id=app></div></body></html>"
        answers = (
            (200, {"content-type": "text/html"}, shell),
            (301, {"location": "/series?letter=g"}, b""),
            (200, {}, {"unexpected": True}),
        )
        for answer in answers:
            site = anonymous_site()
            site.on("GET", API + "/search/results", answer)
            with self.assertRaisesRegex(self.mod.ServiceUnavailable, "API changed"):
                self.provider(site).search(GOT_S01E02, [HRV], {})

    def test_route_not_found_raises_service_unavailable(self):
        site = anonymous_site()
        site.on("GET", API + "/series/935/seasons", api_error(404, "NOT_FOUND", "Route not found"))
        with self.assertRaisesRegex(self.mod.ServiceUnavailable, "API changed \\(/series/seasons\\)"):
            self.provider(site).search(GOT_S01E02, [HRV], {})

    def test_validation_error_is_a_plugin_bug(self):
        site = anonymous_site()
        site.on("GET", API + "/translations/series", (400, {}, _fixture("api_error_validation_querystring")))
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.provider(site).search(GOT_S01E02, [HRV], {})

    def test_429_raises_api_throttled_with_reset(self):
        site = anonymous_site()
        site.on("GET", API + "/search/results", (429, {"x-ratelimit-reset": "40", "x-ratelimit-remaining": "0"}, {}))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.provider(site).search(GOT_S01E02, [HRV], {})
        self.assertEqual(caught.exception.retry_after, 40)

        site = anonymous_site()
        site.on("GET", API + "/search/results", (429, {"retry-after": "2"}, {}), ok(_fixture("api_search_results_series")))
        results = self.provider(site).search(GOT_S01E02, [HRV], {})
        self.assertEqual(len(results), 1)
        self.assertEqual(self.clock.sleeps, [2.0])

    def test_low_ratelimit_remaining_waits_or_throttles(self):
        site = anonymous_site()
        site.on("GET", API + "/auth/me", ok(ANON_ME, {"x-ratelimit-remaining": "15", "x-ratelimit-reset": "3"}))
        results = self.provider(site).search(GOT_S01E02, [HRV], {})
        self.assertEqual(len(results), 1)
        self.assertEqual(self.clock.sleeps, [3.0])

        site = anonymous_site()
        site.on("GET", API + "/auth/me", ok(ANON_ME, {"x-ratelimit-remaining": "15", "x-ratelimit-reset": "30"}))
        with self.assertRaises(self.mod.APIThrottled) as caught:
            self.provider(site).search(GOT_S01E02, [HRV], {})
        self.assertEqual(caught.exception.retry_after, 30)
        self.assertEqual(len(site.calls), 1)

    def test_request_delay_ms_spacing(self):
        site = anonymous_site()
        self.provider(site).search(GOT_S01E02, [HRV], {"request_delay_ms": 500})
        self.assertEqual(self.clock.sleeps, [0.5, 0.5, 0.5])
        site = anonymous_site()
        self.provider(site).search(GOT_S01E02, [HRV], {"request_delay_ms": "90000"})
        self.assertEqual(self.clock.sleeps, [5.0, 5.0, 5.0])
        site = anonymous_site()
        self.provider(site).search(GOT_S01E02, [HRV], {})
        self.assertEqual(self.clock.sleeps, [])

    def test_json_403_is_not_a_cloudflare_challenge(self):
        body = json.dumps(_fixture("api_error_movie_download_forbidden")).encode()
        self.assertFalse(self.mod._is_cloudflare_challenge(403, {"server": "cloudflare"}, body))
        self.assertFalse(self.mod._is_cloudflare_challenge(403, {"cf-mitigated": "challenge"}, body))
        self.assertTrue(self.mod._is_cloudflare_challenge(403, {"cf-mitigated": "challenge"}, b"<html></html>"))
        self.assertTrue(self.mod._is_cloudflare_challenge(503, {}, b"<title>Just a moment...</title>"))
        self.assertFalse(self.mod._is_cloudflare_challenge(403, {}, b"<title>Attention Required! | Cloudflare</title>"))
        site = anonymous_site()
        site.on("GET", API + "/translations/series/120299/download", (403, {"server": "cloudflare"}, body))
        with self.assertRaises(self.mod.AccountRequired):
            self.provider(site).download(self.payload(), HRV, {"flaresolverr_url": "http://fs:8191/v1"})

    def test_flaresolverr_visits_only_auth_me_and_never_gets_session_cookies(self):
        challenge = (403, {"cf-mitigated": "challenge", "content-type": "text/html"}, b"<html><title>Just a moment...</title></html>")
        site = cookie_member_site()
        site.on("GET", API + "/series/935/seasons", challenge, ok(_fixture("api_series_seasons")))
        provider = self.provider(site)
        payloads = []

        def solver(payload):
            payloads.append(payload)
            return {
                "status": "ok",
                "solution": {
                    "status": 200,
                    "response": "{}",
                    "userAgent": "FS UA",
                    "cookies": [
                        {"name": "cf_clearance", "value": "CFNEW", "domain": ".prijevodi-online.org", "path": "/"},
                        {"name": "SMFCookie123", "value": "EVIL", "domain": ".prijevodi-online.org", "path": "/"},
                        {"name": "__cf_bm", "value": "bad value;x=1", "domain": ".prijevodi-online.org", "path": "/"},
                    ],
                },
            }

        provider._flaresolverr_transport = solver
        config = {"session_cookie": COOKIE_HEADER, "flaresolverr_url": "http://fs:8191/v1"}
        provider.search(GOT_S01E02, [HRV], config)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["url"], "https://www.prijevodi-online.org/api/v1/auth/me")
        self.assertEqual(payloads[0]["cmd"], "request.get")
        self.assertNotIn("cookies", payloads[0])
        self.assertNotIn("postData", payloads[0])
        self.assertNotIn(COOKIE_VALUE, json.dumps(payloads))
        replay = [call for call in site.calls if call["path"] == API + "/series/935/seasons"][-1]
        self.assertEqual(replay["headers"]["User-Agent"], "FS UA")
        cookie = replay["headers"]["Cookie"]
        self.assertIn("cf_clearance=CFNEW", cookie)
        self.assertIn(f"SMFCookie123={COOKIE_VALUE}", cookie)
        self.assertNotIn("EVIL", cookie)
        self.assertNotIn("__cf_bm", cookie)

        site = anonymous_site()
        site.on("GET", API + "/auth/me", challenge)
        provider = self.provider(site)
        provider._flaresolverr_transport = solver
        with self.assertRaisesRegex(self.mod.CloudflareBlockedError, "still challenged"):
            provider.search(GOT_S01E02, [HRV], {"flaresolverr_url": "http://fs:8191/v1"})
        self.assertEqual(len(payloads), 2)

        site = anonymous_site()
        site.on("GET", API + "/auth/me", challenge)
        with self.assertRaisesRegex(self.mod.CloudflareBlockedError, "FlareSolverr URL"):
            self.provider(site).search(GOT_S01E02, [HRV], {})
        self.assertEqual(len(site.calls), 1)

    def test_deadline_crosses_as_service_unavailable(self):
        site = anonymous_site()

        def slow_me(call):
            self.clock.advance(29)
            return ok(ANON_ME)

        site.on("GET", API + "/auth/me", slow_me)
        with self.assertRaises(self.mod.ServiceUnavailable) as caught:
            self.provider(site).search(GOT_S01E02, [HRV], {})
        # The host maps exceptions by class name only.
        self.assertEqual(type(caught.exception).__name__, "ServiceUnavailable")
        self.assertIn("time budget", str(caught.exception))
        self.assertEqual(len(site.calls), 1)

    def test_real_opener_refuses_redirects_and_keeps_every_set_cookie(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802, http.server API
                seen.append(self.path)
                self.send_response(301)
                self.send_header("Location", "/elsewhere")
                self.send_header("Set-Cookie", "a=1; Path=/")
                self.send_header("Set-Cookie", "b=2; Path=/")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = self.mod.PrijevodiOnlineProvider()
        url = f"http://127.0.0.1:{server.server_address[1]}/api/v1/series"
        status, headers, body = provider._send("GET", url, {"Accept": "application/json"}, None, 5)
        self.assertEqual(status, 301)
        self.assertEqual(seen, ["/api/v1/series"])
        cookies = [value for name, value in self.mod._header_pairs(headers) if name.lower() == "set-cookie"]
        self.assertEqual(len(cookies), 2)
        self.assertEqual(body, b"")

    def test_flaresolverr_timeout_is_clamped(self):
        self.assertEqual(self.mod._flaresolverr_timeout_ms({"flaresolverr_timeout_ms": 30000}), 25000)
        self.assertEqual(self.mod._flaresolverr_timeout_ms({"flaresolverr_timeout_ms": "100"}), 5000)
        self.assertEqual(self.mod._flaresolverr_timeout_ms({}), 25000)


# G. Secrets


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class SecretTests(ProviderTestCase):
    def test_no_credential_in_candidates_payloads_results_errors_urls_logs_or_state(self):
        sentinels = (USER, PASSWORD, COOKIE_VALUE, LOGIN_COOKIE, "PHPSENTINEL")
        collector = _Collector()
        root = logging.getLogger()
        plugin = logging.getLogger("prijevodionline")
        old_root_level, old_plugin_level = root.level, plugin.level
        root.addHandler(collector)
        plugin.addHandler(collector)
        root.setLevel(logging.DEBUG)
        plugin.setLevel(logging.DEBUG)
        outputs, errors, providers, solver_payloads, calls = [], [], [], [], []
        try:
            # Degraded search: the login fails and the site echoes the username.
            site, account = anonymous_site(), None
            PasswordSite(site, login=api_error(401, "Auth/InvalidCredentials", f"Unknown user {USER} {PASSWORD}"))
            provider = self.provider(site)
            outputs.append(provider.search(GOT_S01E02, [HRV], {"username": USER, "password": PASSWORD}))
            try:
                provider.download(self.payload(access="priced"), HRV, {"username": USER, "password": PASSWORD})
            except Exception as error:  # noqa: BLE001, collected for inspection
                errors.append(error)
            providers.append(provider)
            calls.extend(site.calls)

            # Account search, a paid download and a failed confirm.
            site = anonymous_site()
            account = PasswordSite(site)
            site.on("POST", API + "/purchases/intent", quote_purchase(120299))
            site.on("POST", API + "/purchases/confirm", confirm_ok(120299), (500, {}, f"error for {USER}".encode()))
            site.on(
                "GET", API + "/translations/series/165016/download",
                api_error(451, "Copyright/InfringementNotice", "x", data={"title": f"{USER} {LOGIN_COOKIE}1"}),
            )
            provider = self.provider(site)
            config = {"username": USER, "password": PASSWORD, "allow_paid_downloads": True}
            results = provider.search(GOT_S01E02, [HRV, SRP], config)
            outputs.append(results)
            outputs.append(provider.download(self.payload(access="priced"), HRV, config))
            for payload in (self.payload(165016, access="priced"), self.payload(165016, access="free", list_price=None)):
                try:
                    provider.download(payload, SRP, config)
                except Exception as error:  # noqa: BLE001, collected for inspection
                    errors.append(error)
            providers.append(provider)
            calls.extend(site.calls)

            # Cookie mode through a Cloudflare solve.
            site = cookie_member_site()
            site.on(
                "GET", API + "/series/935/seasons",
                (403, {"cf-mitigated": "challenge"}, b"<html>Just a moment...</html>"),
                ok(_fixture("api_series_seasons")),
            )
            provider = self.provider(site)

            def solver(payload):
                solver_payloads.append(payload)
                return {"status": "ok", "solution": {"status": 200, "response": "{}", "userAgent": "FS UA", "cookies": []}}

            provider._flaresolverr_transport = solver
            cookie_config = {"session_cookie": COOKIE_HEADER, "flaresolverr_url": "http://fs:8191/v1"}
            outputs.append(provider.search(GOT_S01E02, [HRV], cookie_config))
            providers.append(provider)
            calls.extend(site.calls)
        finally:
            root.removeHandler(collector)
            plugin.removeHandler(collector)
            root.setLevel(old_root_level)
            plugin.setLevel(old_plugin_level)

        self.assertEqual(len(errors), 3)
        self.assertIn("***", str(errors[-1]))
        haystacks = {
            "outputs": json.dumps(outputs),
            "errors": "\n".join(f"{type(error).__name__}: {error}" for error in errors),
            "urls": "\n".join(call["url"] for call in calls),
            "flaresolverr": json.dumps(solver_payloads),
            "logs": "\n".join(collector.lines),
            "state": "\n".join(repr(provider.__dict__) for provider in providers),
        }
        self.assertTrue(collector.lines)
        for name, text in haystacks.items():
            for sentinel in sentinels:
                self.assertNotIn(sentinel, text, f"{sentinel} leaked through {name}")


class ScrubbingTests(ProviderTestCase):
    def test_chained_exceptions_are_scrubbed(self):
        provider = self.provider(anonymous_site())
        config = {"username": USER, "password": PASSWORD}
        with self.assertRaises(ValueError) as caught:
            with provider._call_scope(config):
                try:
                    raise RuntimeError(f"inner {PASSWORD}")
                except RuntimeError as inner:
                    raise ValueError(f"outer {USER}") from inner
        chain = [caught.exception, caught.exception.__cause__, caught.exception.__context__]
        for error in chain:
            self.assertNotIn(PASSWORD, str(error))
            self.assertNotIn(USER, str(error))

    def test_digest_is_keyed(self):
        plain = hashlib.sha256(f"prijevodionline\0v1\0password\0{USER}\0{PASSWORD}".encode()).hexdigest()
        keyed = self.mod._digest("password", f"{USER}\0{PASSWORD}")
        self.assertNotEqual(keyed, plain)
        self.assertEqual(keyed, self.mod._digest("password", f"{USER}\0{PASSWORD}"))
        self.assertNotEqual(keyed, _load_provider_module()._digest("password", f"{USER}\0{PASSWORD}"))

    def test_short_cookie_values_stay_in_release_names(self):
        data = with_variant(
            season_capture(), "translations", 0,
            name="Game of Thrones - 01x02 - The Kingsroad 720p.BluRay HR dark",
        )
        site = cookie_member_site(member=member_me(series_free=True), translations=data)
        provider = self.provider(site)
        config = {"session_cookie": f"theme=dark; SMFCookie123={COOKIE_VALUE}"}
        results = provider.search(GOT_S01E02, [HRV], config)
        self.assertIn("HR dark", results[0]["release_info"])
        self.assertEqual(provider._scrub(f"x {COOKIE_VALUE} y"), "x *** y")


# H. Manifest and fixtures


class ManifestTests(ProviderTestCase):
    def manifest(self):
        return json.loads((PROVIDER_DIR / "provider.json").read_text("utf-8"))

    def test_manifest_advertises_accepted_codes(self):
        manifest = self.manifest()
        self.assertIn("cnr", manifest["languages"])
        self.assertNotIn("mne", manifest["languages"])
        self.assertEqual(manifest["languages"], ["bos", "cnr", "hbs", "hrv", "mkd", "sr-Cyrl", "srp"])
        for code in manifest["languages"]:
            if code == "sr-Cyrl":
                request, expected = {"alpha3": "srp", "alpha2": "sr", "script": "Cyrl"}, "srp-Cyrl"
            else:
                request, expected = {"alpha3": code}, code
            self.assertEqual(
                self.mod.requested_languages([request]), {expected},
                msg=f"manifest advertises {code} but the provider rejects it",
            )

    def test_manifest_declares_the_flaresolverr_capability(self):
        manifest = self.manifest()
        self.assertIs(manifest.get("flaresolverr"), True)
        properties = manifest["config_schema"]["properties"]
        self.assertIn("flaresolverr_url", properties)
        self.assertIn("flaresolverr_timeout_ms", properties)

    def test_manifest_account_and_spend_settings(self):
        manifest = self.manifest()
        schema = manifest["config_schema"]
        properties = schema["properties"]
        self.assertNotIn("required", schema)
        self.assertEqual(manifest["secret_fields"], ["password", "session_cookie", "username"])
        for key in manifest["secret_fields"]:
            self.assertIs(properties[key].get("secret"), True, key)
            self.assertEqual(properties[key]["type"], "string")
            self.assertEqual(properties[key]["default"], "")
        self.assertEqual(properties["allow_paid_downloads"]["type"], "boolean")
        self.assertIs(properties["allow_paid_downloads"]["default"], False)
        cap = properties["max_tokens_per_download"]
        self.assertEqual(cap["type"], "string")
        self.assertEqual(cap["default"], "1")
        self.assertEqual(cap["enum"], ["1", "2", "3", "5", "10"])
        self.assertEqual([self.mod.parse_cap(value) for value in cap["enum"]], list(self.mod.CAP_CHOICES))
        self.assertNotIn("secret", properties["session_user_agent"])
        for key in properties:
            self.assertFalse(key in ("timeout", "timeout_seconds", "worker_timeout") or key.endswith("_timeout_seconds"), key)
        self.assertEqual(manifest["supported_media"], ["episode", "movie"])
        self.assertEqual(manifest["version"], "0.3.0")

    def test_manifest_text_is_current(self):
        text = (PROVIDER_DIR / "provider.json").read_text("utf-8")
        self.assertNotIn("offline since", text.lower())
        manifest = json.loads(text)
        # The host renders the settings in key order, so help text must not
        # point at fields "above" or "below".
        descriptions = [manifest["description"]] + [
            value.get("description", "") for value in manifest["config_schema"]["properties"].values()
        ]
        for description in descriptions:
            self.assertIsNone(re.search(r"\b(above|below)\b", description, re.I), description)
        movie_note = "the site does not let visitors download movies"
        self.assertIn(movie_note, manifest["description"])
        self.assertIn(movie_note, manifest["config_schema"]["properties"]["username"]["description"])
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2014", (PROVIDER_DIR / "provider.py").read_text("utf-8"))

    def test_api_fixtures_carry_no_account_data(self):
        paths = sorted(FIXTURE_DIR.glob("prijevodionline_api_*.json"))
        self.assertEqual(len(paths), 17)
        forbidden = re.compile(r"set-cookie|smfcookie|phpsessid|cf_clearance", re.I)

        def walk(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "username":
                        self.assertEqual(item, "translator_a", path)
                    if key == "memberId":
                        self.assertEqual(item, 1001, path)
                    if key in ("reviewerName", "reviewedBy"):
                        self.assertIsNone(item, path)
                    if key == "email":
                        self.assertEqual(item, "", path)
                    if key == "siteKey":
                        self.assertIn("REDACTED", item, path)
                    walk(item, f"{path}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")

        for path in paths:
            text = path.read_text("utf-8")
            self.assertIsNone(forbidden.search(text), path.name)
            walk(json.loads(text), path.name)


if __name__ == "__main__":
    unittest.main()
